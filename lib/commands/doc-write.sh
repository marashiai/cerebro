# Delegate documentation updates on the task branch.

cmd_doc_write() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local plan=""
  local prompt_text=""
  local notes=""
  local pair=0
  local CEREBRO_JEV_ENABLED="$CEREBRO_JEV_ENABLED" CEREBRO_PAIR_IDLE="$CEREBRO_PAIR_IDLE"
  local CEREBRO_WATCH_PLAN=""
  local model=""
  if [[ $# -gt 0 && "${1:-}" != --* ]]; then
    plan="$1"; shift
  fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prompt) shift; prompt_text="${1:-}"; shift || true ;;
      --notes)  shift; notes="${1:-}";       shift || true ;;
      --model)  shift; model="${1:-}";       shift || true ;;
      --pair)   pair=1; shift ;;
      --watch)  CEREBRO_JEV_ENABLED=1; shift ;;
      --no-watch) CEREBRO_JEV_ENABLED=0; shift ;;
      *) die "doc-write: unknown arg: $1" ;;
    esac
  done
  [[ -n "$repo" ]] \
    || die "usage: cerebro doc-write <repo-abs-path> (<plan-path> [--notes \"...\"] | --prompt \"<text>\") [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "doc-write: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "doc-write: repo not a directory: $repo"
  if [[ -n "$plan" && -n "$prompt_text" ]]; then
    die "doc-write: pass either <plan-path> or --prompt, not both"
  fi
  if [[ -z "$plan" && -z "$prompt_text" ]]; then
    die "doc-write: requires <plan-path> or --prompt \"<text>\""
  fi
  if [[ -n "$prompt_text" && -n "$notes" ]]; then
    die "doc-write: --notes is only meaningful with a plan file; bake the context into --prompt instead"
  fi
  if [[ -n "$plan" ]]; then
    [[ -r "$plan" ]] || die "doc-write: cannot read plan: $plan"
  fi

  local plan_body source_desc
  if [[ -n "$plan" ]]; then
    plan_body="$(cat "$plan")"
    source_desc="plan=$plan"
  else
    plan_body="$prompt_text"
    source_desc="prompt=inline"
  fi

  CEREBRO_WATCH_PLAN="$plan"
  watch_prepare || return $?

  local child_log; child_log="$(child_log_path doc-write)"

  local provider; provider="$(backend_child_provider doc-write)"
  local agent; agent="$(backend_child_agent_name doc-write)"

  # Child-session continuity is only for interrupted/incomplete doc writes. A
  # completed doc child must not be reused as the context for a later one.
  local store_file; store_file="$(child_sessions_file)"
  local dw_branch; dw_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  local ckey prior=""
  ckey="$(child_key "$repo" doc-write "${dw_branch:-default}")"
  if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
    :
  else
    prior=""
  fi

  say "cerebro: updating docs in $repo"
  log_event "doc_write_started" "$source_desc resume=${prior:-none}"

  local PAIR_SID="" PAIR_FIFO="" PAIR_STEER="" PAIR_IDLE="" PAIR_STALL="" PAIR_STALL_BUSY=""
  local PAIR_PORT="" PAIR_SERVE_PID="" PAIR_BASE_URL="" PAIR_OPTS=() PAIR_PGID="" PAIR_LAUNCH=()
  (( pair )) && pair_begin doc-write "$repo" "$dw_branch" "$child_log" "$prior"

  local child_prompt
  child_prompt="$(printf 'Update documentation for the delegated task and actual changes on this branch within the supplied delivery permissions.\n\n<orchestrator-notes>\n%s\n</orchestrator-notes>\n\n<plan>\n%s\n</plan>\n' "$notes" "$plan_body")"

  local rc id_capture msg_capture; id_capture="$(mktemp)"; msg_capture="$(mktemp)"
  local stall_n=0
  while :; do
    child_store_begin "$ckey" "$provider" doc-write "$repo" "${dw_branch:-default}" "$child_log" "${prior:+preserve-id}"
    child_run "$pair" "$repo" "$child_prompt" "$agent" "$prior" \
      "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
    rc=$?
    pair_cleanup "$pair"

    if (( pair )) && pair_stalled "$child_log"; then
      if (( stall_n < ${CEREBRO_PAIR_STALL_RETRIES:-2} )); then
        stall_n=$((stall_n + 1))
        pair_stall_backoff "$stall_n"
        pair_stall_clear "$child_log"
        pair_begin doc-write "$repo" "$dw_branch" "$child_log" "$PAIR_SID"
        prior="$PAIR_SID"
        continue
      fi
      pair_stall_clear "$child_log"
      log_event "pair_stall_giveup" "after=$stall_n stalls log=$child_log resume=$PAIR_SID"
      rm -f "$id_capture" "$msg_capture"
      die "doc-write: paired child stalled $stall_n time(s) and was not restarted further; it remains resumable (id $PAIR_SID) -- see $child_log"
    fi
    break
  done

  if (( rc != 0 )); then
    local _cap_id; _cap_id="$(cat "$id_capture" 2>/dev/null || true)"
    rm -f "$id_capture" "$msg_capture"
    # Retain incomplete work when this attempt or the prior run has a native ID.
    [[ -z "$_cap_id" && -z "$prior" ]] && child_store_done "$ckey"
    log_event "doc_write_failed" "rc=$rc log=$child_log"
    warn "doc-write: child failed (rc=$rc); see $child_log"
    child_fail_stderr "$child_log"
    die "doc-write: child failed (rc=$rc); see $child_log"
  fi
  child_store_done "$ckey"
  local child_id; child_id="$(cat "$id_capture" 2>/dev/null || true)"
  rm -f "$id_capture"
  log_event "doc_write_finished" "$child_log"
  pair_report "$pair" "$child_log"
  surface_child_reply "$msg_capture" doc-write "$child_id"
  rm -f "$msg_capture"
  echo "$child_log"
}
