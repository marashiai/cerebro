# Delegate runtime acceptance verification and capture its final report.

cmd_verify() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local plan_path="" prompt_text="" context="" model=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --plan) shift; plan_path="${1:-}"; shift || true ;;
      --prompt) shift; prompt_text="${1:-}"; shift || true ;;
      --context) shift; context="${1:-}"; shift || true ;;
      --model) shift; model="${1:-}"; shift || true ;;
      *) die "verify: unknown arg: $1" ;;
    esac
  done
  [[ -n "$repo" ]] \
    || die "usage: cerebro verify <repo-abs-path> (--plan <path> | --prompt \"<text>\") [--context \"<text>\"] [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "verify: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "verify: repo not a directory: $repo"
  # Exactly one of --plan / --prompt is required.
  if [[ -z "$plan_path" && -z "$prompt_text" ]]; then
    die "verify: requires --plan <path> or --prompt \"<text>\""
  fi
  if [[ -n "$plan_path" && -n "$prompt_text" ]]; then
    die "verify: pass either --plan or --prompt, not both"
  fi
  local plan_block=""
  if [[ -n "$plan_path" ]]; then
    [[ "$plan_path" = /* ]] || die "verify: plan path must be absolute: $plan_path"
    [[ -r "$plan_path" && -s "$plan_path" ]] \
      || die "verify: cannot read plan (or it is empty): $plan_path"
    plan_block="$(cat "$plan_path")"
  fi

  # Canonical worktree root keys the per-repo state file so re-verifies on the
  # same branch resume continuity instead of colliding with other repos.
  local canonical_repo
  canonical_repo="$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" \
    || die "verify: not a git worktree: $repo"
  local repo_key
  repo_key="$(repo_state_key "$repo")" \
    || die "verify: not a git worktree: $repo"
  local state_dir="$CEREBRO_SESSION_DIR/review-state"
  local state_file="$state_dir/$repo_key.json"
  mkdir -p "$state_dir"

  local store_file; store_file="$(child_sessions_file)"
  local ckey="" prior="" verify_branch
  verify_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [[ -n "$verify_branch" ]]; then
    ckey="$(child_key "$canonical_repo" verify "$verify_branch")"
    if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
      :
    else
      prior=""
    fi
  fi

  # Uniquify the report filename (same convention as review).
  local out_path="$CEREBRO_SESSION_DIR/children/verify-$(ts_compact)-$(basename "$repo")-$$-${RANDOM}.md"
  local child_log="${out_path%.md}.log"

  say "cerebro: verifying $repo (${verify_branch:-unknown branch})"
  log_event "verify_started" "repo=$repo branch=${verify_branch:-none} resume=${prior:-none}"

  local verify_prompt
  verify_prompt="$(cat "$(cerebro_payloads_dir)/prompts/verify.md")"

  if [[ -n "$plan_block" ]]; then
    verify_prompt+=$'\n\n<requirements>\n'"$plan_block"$'\n</requirements>'
  else
    verify_prompt+=$'\n\nThe ad-hoc verification request: '"$prompt_text"
  fi

  if [[ -n "$context" ]]; then
    verify_prompt+=$'\n\n<parent-context>\n'"$context"$'\n</parent-context>'
  fi

  # Verification retains runtime/browser tools, unlike read-only review.
  local agent; agent="$(backend_child_agent_name verify)"
  local rc id_capture out_capture; id_capture="$(mktemp)"; out_capture="$(mktemp)"

  child_store_begin "$ckey" "$(current_backend)" verify "$repo" "${verify_branch:-auto}" "$child_log" "${prior:+preserve-id}"
  child_run 0 "$repo" "$verify_prompt" "$agent" "$prior" \
    "$child_log" "$out_capture" "$id_capture" "$store_file" "$ckey" "${model:-$CEREBRO_REVIEW_MODEL}"
  rc=$?

  # The report is the run's closing message; write it to out_path.
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
    log_event "verify_failed" "rc=$rc log=$child_log out=$out_path"
    warn "verify: verification run failed (rc=$rc)"
    [[ -s "$child_log" ]] && warn "see event log: $child_log"
    child_fail_stderr "$child_log"
    die "verify: verification run failed; not echoing a report path"
  fi

  child_store_done "$ckey"
  rm -f "$out_capture"

  # Record the result under last_verify so `cerebro status` can show it.
  local current_sha
  current_sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
  if [[ -n "$current_sha" ]]; then
    mkdir -p "$state_dir"
    jq -n --arg sha "$current_sha" --arg branch "${verify_branch:-}" \
          --arg verdict "$(tail -n 1 "$out_path" 2>/dev/null)" \
      '{last_verify_sha:$sha, branch:$branch, last_verify:$verdict}' \
      > "$state_file" 2>/dev/null || true
  fi

  log_event "verify_written" "$out_path"
  echo "$out_path"
}
