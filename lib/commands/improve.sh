# Read-only trace analysis and periodic analysis of improvement history.

improve_history_file() { printf '%s\n' "$CEREBRO_HOME/improvement-history.json"; }

# A zero-match grep must emit one numeric count, despite its nonzero exit code.
improve_count_findings() {
  local f="$1" n
  [[ -s "$f" ]] || { printf '0'; return 0; }
  n="$(grep -cE '^[[:space:]]*(#{1,6}[[:space:]]+)?[0-9]+\.' "$f" 2>/dev/null)" || n=0
  printf '%s' "$n"
}

improve_valid_verdict() {
  local f="$1" label="$2" verdict
  [[ -s "$f" ]] || return 1
  verdict="$(tail -n 1 "$f" 2>/dev/null || true)"
  case "$label:$verdict" in
    improve:HILL\ CLIMB:\ ISSUES\ FOUND|improve:HILL\ CLIMB:\ NO\ CHANGES\ RECOMMENDED) return 0 ;;
    meta-improve:META\ CLIMB:\ ISSUES\ FOUND|meta-improve:META\ CLIMB:\ NO\ CHANGES\ RECOMMENDED) return 0 ;;
    *) return 1 ;;
  esac
}

# Fast and slow analysis share native resume, final-report capture and error handling.
improve_run_reviewer() {
  local repo="$1" prompt="$2" agent="$3" prior="$4"
  local child_log="$5" out_path="$6" label="$7" model="${8:-}"
  local store_file; store_file="$(child_sessions_file)"
  local ckey; ckey="$(child_key "$repo" "$label" "$label")"

  # Resume only an incomplete retained conversation.
  if [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
    :
  else
    prior=""
  fi

  # Never permit a previous successful report to satisfy this invocation.
  : > "$out_path"
  local rc id_capture out_capture; id_capture="$(mktemp)"; out_capture="$(mktemp)"

  child_store_begin "$ckey" "$(current_backend)" "$label" "$repo" "$label" "$child_log" "${prior:+preserve-id}"
  child_run 0 "$repo" "$prompt" "$agent" "$prior" \
    "$child_log" "$out_capture" "$id_capture" "$store_file" "$ckey" "${model:-$CEREBRO_REVIEW_MODEL}"
  rc=$?

  local _cap_id; _cap_id="$(cat "$id_capture" 2>/dev/null || true)"

  if (( rc == 0 )) && improve_valid_verdict "$out_capture" "$label"; then
    cp "$out_capture" "$out_path"
  fi
  rm -f "$id_capture"

  # A failed run must not supply a findings/report path; retain a native ID for resume.
  if (( rc != 0 )) || [[ ! -s "$out_path" ]]; then
    rm -f "$out_capture"
    [[ -z "$_cap_id" && -z "$prior" ]] && child_store_done "$ckey"
    log_event "${label}_failed" "rc=$rc log=$child_log out=$out_path"
    warn "${label}: review run failed or returned an invalid verdict (rc=$rc)"
    [[ -s "$child_log" ]] && warn "see event log: $child_log"
    child_fail_stderr "$child_log"
    return 1
  fi

  child_store_done "$ckey"
  rm -f "$out_capture"
  return 0
}

# ----- main command ---------------------------------------------------------

cmd_improve() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local context=""
  local force_meta=0
  local model=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --context) shift; context="${1:-}"; shift || true ;;
      --meta) force_meta=1; shift ;;
      --model) shift; model="${1:-}"; shift || true ;;
      *) die "improve: unknown arg: $1" ;;
    esac
  done
  [[ -n "$model" ]] && require_model_for_backend "$model" "$(current_backend)" improve
  [[ -n "$repo" ]] \
    || die "usage: cerebro improve <cerebro-repo-abs-path> [--context \"<focus>\"] [--meta] [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "improve: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "improve: repo not a directory: $repo"

  local imp_dir="$CEREBRO_SESSION_DIR/improvements"
  mkdir -p "$imp_dir"
  local out_path="$imp_dir/improve.md"
  local child_log="${out_path%.md}.log"
  local meta_out_path="$imp_dir/meta-improve.md"
  local meta_child_log="${meta_out_path%.md}.log"

# --- utility estimation --------------------------------------------------
  # Score sessions created since the last recorded improvement run. The
  # history operation returns an ISO timestamp, never a run id.
  local history_file; history_file="$(improve_history_file)"
  local since_ts
  since_ts="$(python3 "$CEREBRO_LIB_DIR/python/improve_history.py" \
    "$history_file" last-timestamp)" \
    || die "improve: could not read improvement history: $history_file"
  since_ts="${since_ts%$'\n'}"
  local util_now
  util_now="$(python3 "$CEREBRO_LIB_DIR/python/improve_utility.py" "$CEREBRO_HOME" "$since_ts" 2>/dev/null || printf '0.5')"

  local agent; agent="$(backend_child_agent_name improve)"
  local results=()

  # --- FAST LOOP: task-skill improvement -----------------------------------
  say "cerebro: mining traces under $CEREBRO_HOME against $repo -> $out_path"
  log_event "improve_started" "repo=$repo out=$out_path util=$util_now"

  local improve_prompt
  improve_prompt="$(cerebro_improve_prompt)

The cerebro trace corpus to analyse lives under: $CEREBRO_HOME
  sessions/*/children/*.jsonl   - agent trajectories (model + tool calls)
  sessions/*/transcript.jsonl   - user prompts + milestones
  sessions/*/audits/*.md, sessions/*/children/review-*.md - grader feedback
  pending-learnings.md, learnings.md, overlays/*.md - applied prefs/overlays"

  if [[ -n "$context" ]]; then
    improve_prompt+="

Focus from the orchestrator (where to concentrate the analysis):

<context>
$context
</context>"
  fi

  # Resume handling: check for a prior in-flight improve session.
  local fast_ckey; fast_ckey="$(child_key "$repo" improve improve)"
  local fast_prior=""
  if fast_prior="$(child_session_get "$fast_ckey")" && [[ -n "$fast_prior" ]] && child_session_running_fresh "$fast_ckey"; then
    :
  else
    fast_prior=""
  fi

  if improve_run_reviewer "$repo" "$improve_prompt" "$agent" "$fast_prior" \
      "$child_log" "$out_path" improve "$model"; then
    log_event "improve_written" "$out_path"
    results+=("$out_path")

    # Append this successful fast run before checking the horizon, so H=1
    # fires on the first invocation and H=2 on the second.
    local findings_count verdict
    findings_count="$(improve_count_findings "$out_path")"
    verdict="$(tail -1 "$out_path" 2>/dev/null || true)"
    local ts
    ts="$(ts_iso)"
    local fast_run_id
    fast_run_id="$(python3 "$CEREBRO_LIB_DIR/python/improve_history.py" \
      "$history_file" add-run fast "$ts" "$findings_count" "$verdict" "$util_now")" \
      || die "improve: could not append improvement history: $history_file"
    fast_run_id="${fast_run_id%$'\n'}"
    log_event "improve_history_run" "run=$fast_run_id findings=$findings_count util=$util_now"
  else
    die "improve: review run failed; not echoing a findings path"
  fi

  local fast_since_meta
  fast_since_meta="$(python3 "$CEREBRO_LIB_DIR/python/improve_history.py" "$history_file" fast-since-meta)" \
    || die "improve: could not read improvement history: $history_file"
  fast_since_meta="${fast_since_meta%$'\n'}"
  local run_meta=0
  if (( force_meta == 1 )) || (( fast_since_meta >= CEREBRO_META_HORIZON )); then
    run_meta=1
  fi

  # --- SLOW LOOP: meta-skill improvement -----------------------------------
  if (( run_meta == 1 )); then
    say "cerebro: running meta-loop (evolving the improvement procedure) -> $meta_out_path"
    log_event "meta_improve_started" "out=$meta_out_path fast_since_meta=$fast_since_meta"

    # Build the meta-improve prompt: the slow-loop base prompt, then the
    # current meta-skill component files (presented as reference for
    # diagnosis, not as active output instructions), then the chronological
    # improvement history.
    local meta_prompt
    meta_prompt="$(cerebro_meta_improve_prompt)

The cerebro trace corpus to analyse lives under: $CEREBRO_HOME
  sessions/*/children/*.jsonl   - agent trajectories (model + tool calls)
  sessions/*/transcript.jsonl   - user prompts + milestones
  sessions/*/improvements/improve.md - past fast-loop findings
  pending-learnings.md, learnings.md, overlays/*.md - applied prefs/overlays

The chronological improvement history (from $history_file):"

    local history_summary
    history_summary="$(python3 "$CEREBRO_LIB_DIR/python/improve_history.py" "$history_file" list)" \
      || die "improve: could not read improvement history: $history_file"
    if [[ -n "$history_summary" ]]; then
      meta_prompt+="

<improvement_history>
$(printf '%s' "$history_summary")
</improvement_history>"
    else
      meta_prompt+="

<improvement_history>
(first meta-loop run -- no prior improvement history yet)
</improvement_history>"
    fi

    if [[ -n "$context" ]]; then
      meta_prompt+="

Focus from the orchestrator:

<context>
$context
</context>"
    fi

    # The meta loop uses a distinct child-session key so it does not
    # collide with the fast loop's session.
    local meta_ckey; meta_ckey="$(child_key "$repo" meta-improve meta-improve)"
    local meta_prior=""
    if meta_prior="$(child_session_get "$meta_ckey")" && [[ -n "$meta_prior" ]] && child_session_running_fresh "$meta_ckey"; then
      :
    else
      meta_prior=""
    fi

    if improve_run_reviewer "$repo" "$meta_prompt" "$agent" "$meta_prior" \
        "$meta_child_log" "$meta_out_path" meta-improve "$model"; then
      log_event "meta_improve_written" "$meta_out_path"
      results+=("$meta_out_path")

      # Append the successful meta run; this resets the horizon count.
      local meta_findings_count meta_verdict meta_ts
      meta_findings_count="$(improve_count_findings "$meta_out_path")"
      meta_verdict="$(tail -1 "$meta_out_path" 2>/dev/null || true)"
      meta_ts="$(ts_iso)"
      local meta_run_id
      meta_run_id="$(python3 "$CEREBRO_LIB_DIR/python/improve_history.py" \
        "$history_file" add-run meta "$meta_ts" "$meta_findings_count" "$meta_verdict" "$util_now")" \
        || die "improve: could not append improvement history: $history_file"
      meta_run_id="${meta_run_id%$'\n'}"
      log_event "meta_improve_history_run" "run=$meta_run_id findings=$meta_findings_count"
    else
      warn "improve: meta-loop review run failed; fast-loop findings still available"
    fi
  fi

  # Echo all findings paths (fast first, then meta if it ran).
  local r
  for r in "${results[@]}"; do
    printf '%s\n' "$r"
  done
}
