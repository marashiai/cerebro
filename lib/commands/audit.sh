# Independent read-only plan audit on the session backend.

cmd_audit() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local plan_path="${1:-}"; shift || true
  local context="" out_name="" model=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --context) shift; context="${1:-}"; shift || true ;;
      --out) shift; out_name="${1:-}"; shift || true ;;
      --model) shift; model="${1:-}"; shift || true ;;
      *) die "audit: unknown arg: $1" ;;
    esac
  done
  [[ -n "$model" ]] && require_model_for_backend "$model" "$(current_backend)" audit
  [[ -n "$repo" && -n "$plan_path" ]] \
    || die "usage: cerebro audit <repo-abs-path> <plan-path> [--context \"<crucial context>\"] [--out <name>] [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "audit: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "audit: repo not a directory: $repo"
  [[ -s "$plan_path" ]] || die "audit: plan file missing or empty: $plan_path"

  local audits_dir="$CEREBRO_SESSION_DIR/audits"
  mkdir -p "$audits_dir"

  # Default findings name follows the plan, so a re-audit after a revision
  # overwrites the stale findings instead of piling up files.
  local plan_name; plan_name="$(basename "${plan_path%.md}")"
  [[ -z "$out_name" ]] && out_name="$plan_name-audit"
  out_name="${out_name%.md}"
  [[ "$out_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || die "audit: invalid output name: $out_name"
  local out_path="$audits_dir/$out_name.md"
  out_path="$(resolve_in_repo "$CEREBRO_SESSION_DIR" "$out_path")" || return $?
  : > "$out_path" || die "audit: cannot write findings: $out_path"
  local child_log="${out_path%.md}.log"

  # Child-session continuity is only for interrupted/incomplete audits. A
  # cleanly finished audit gets marked done; re-auditing starts fresh.
  local store_file; store_file="$(child_sessions_file)"
  local ckey prior=""
  ckey="$(child_key "$repo" audit "$out_name")"
  if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
    :
  else
    prior=""
  fi

  say "cerebro: auditing $plan_path against $repo -> $out_path"
  log_event "audit_started" "plan=$plan_path out=$out_path resume=${prior:-none}"

  local audit_prompt
  audit_prompt="$(cerebro_audit_prompt)

<plan>
$(cat "$plan_path")
</plan>"

  local spec_path="$CEREBRO_SESSION_DIR/spec.md"
  if [[ -s "$spec_path" ]]; then
    audit_prompt+="

The session specification records what the user actually asked for; judge the plan's scope against it.

<spec>
$(cat "$spec_path")
</spec>"
  fi

  if [[ -n "$context" ]]; then
    audit_prompt+="

Crucial context from the orchestrator (source paths, decisions already made, constraints):

<context>
$context
</context>"
  fi

  # Use this session backend with the selected review model and capture the final findings.
  local agent; agent="$(backend_child_agent_name audit)"
  local rc id_capture out_capture; id_capture="$(mktemp)"; out_capture="$(mktemp)"

  child_store_begin "$ckey" "$(current_backend)" audit "$repo" "$out_name" "$child_log" "${prior:+preserve-id}"
  child_run 0 "$repo" "$audit_prompt" "$agent" "$prior" \
    "$child_log" "$out_capture" "$id_capture" "$store_file" "$ckey" "${model:-$CEREBRO_REVIEW_MODEL}"
  rc=$?

  # The findings are the run's closing message; write them to out_path.
  if (( rc == 0 )) && [[ -s "$out_capture" ]]; then
    cp "$out_capture" "$out_path" || rc=$?
  fi
  local _cap_id; _cap_id="$(cat "$id_capture" 2>/dev/null || true)"
  rm -f "$id_capture"

  # A failed run must not supply a findings/report path; retain a native ID for resume.
  if (( rc != 0 )) || [[ ! -s "$out_capture" || ! -s "$out_path" ]]; then
    rm -f "$out_capture"
    # Retain incomplete work when this attempt or the prior run has a native ID.
    [[ -z "$_cap_id" && -z "$prior" ]] && child_store_done "$ckey"
    log_event "audit_failed" "rc=$rc log=$child_log out=$out_path"
    warn "audit: review run failed (rc=$rc)"
    [[ -s "$child_log" ]] && warn "see event log: $child_log"
    child_fail_stderr "$child_log"
    die "audit: review run failed; not echoing a findings path"
  fi

  child_store_done "$ckey"
  rm -f "$out_capture"

  log_event "audit_written" "$out_path"
  echo "$out_path"
}
