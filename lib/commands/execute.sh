# Implement authorized work in the selected checkout.

cmd_execute() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local plan_path=""
  local prompt_text=""
  local base_branch=""
  local requested_branch=""
  local pair=0 isolated=0
  local CEREBRO_JEV_ENABLED="$CEREBRO_JEV_ENABLED" CEREBRO_PAIR_IDLE="$CEREBRO_PAIR_IDLE"
  local CEREBRO_WATCH_PLAN=""
  local model=""
  # Second positional, if present and not a flag, is the plan path.
  if [[ $# -gt 0 && "${1:-}" != --* ]]; then
    plan_path="$1"; shift
  fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prompt) shift; prompt_text="${1:-}"; shift || true ;;
      --base)   shift; base_branch="${1:-}"; shift || true ;;
      --branch) shift; requested_branch="${1:-}";  shift || true ;;
      --worktree) isolated=1; shift ;;
      --model)  shift; model="${1:-}";       shift || true ;;
      --pair)   pair=1; shift ;;
      --watch)  CEREBRO_JEV_ENABLED=1; shift ;;
      --no-watch) CEREBRO_JEV_ENABLED=0; shift ;;
      *) die "execute: unknown arg: $1" ;;
    esac
  done
  [[ -n "$repo" ]] \
    || die "usage: cerebro execute <repo-abs-path> (<plan-path> | --prompt \"<text>\") [--worktree] [--base <ref>] [--branch <name>] [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "execute: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "execute: repo not a directory: $repo"
  if [[ -n "$plan_path" && -n "$prompt_text" ]]; then
    die "execute: pass either <plan-path> or --prompt, not both"
  fi
  if [[ -z "$plan_path" && -z "$prompt_text" ]]; then
    die "execute: requires <plan-path> or --prompt \"<text>\""
  fi
  if [[ -n "$plan_path" ]]; then
    [[ -r "$plan_path" ]] || die "execute: cannot read plan: $plan_path"
  fi
  repo="$(canonical_worktree_root "$repo")" || return $?

  local plan_body source_desc
  if [[ -n "$plan_path" ]]; then
    plan_body="$(cat "$plan_path")"
    source_desc="plan=$plan_path"
  else
    plan_body="$prompt_text"
    source_desc="prompt=inline"
  fi

  CEREBRO_WATCH_PLAN="$plan_path"
  watch_prepare || return $?

  local child_log; child_log="$(child_log_path execute)"

  local provider; provider="$(backend_child_provider execute)"
  local agent; agent="$(backend_child_agent_name execute)"

  if [[ -n "$plan_path" ]]; then
    say "cerebro: executing $plan_path in $repo${base_branch:+ (base=$base_branch)}${requested_branch:+ (branch=$requested_branch)}"
  else
    say "cerebro: executing inline prompt in $repo${base_branch:+ (base=$base_branch)}${requested_branch:+ (branch=$requested_branch)}"
  fi

  # Child-session continuity is only for incomplete work. A completed execute
  # must not bleed provider context into the next sub-agent, even when a trunk
  # based suite runs multiple plans on the same branch. The key always includes
  # the plan/prompt; --branch narrows it but never replaces it.
  local store_file; store_file="$(child_sessions_file)"
  local key_disc; key_disc="$(execute_child_disc "$requested_branch" "$plan_path" "$prompt_text")"
  (( isolated )) && key_disc+='|workspace:isolated'
  local ckey prior=""
  ckey="$(child_key "$repo" execute "$key_disc")"
  if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
    :
  else
    prior=""
  fi
  local wt workspace task_branch start_head directory="" resume=0
  (( isolated )) && directory="$CEREBRO_HOME/worktrees/$CEREBRO_SESSION_ID-$ckey"
  [[ -n "$prior" ]] && resume=1
  workspace="$(python3 "$CEREBRO_LIB_DIR/python/task_workspace.py" prepare \
    "$repo" "$directory" "$requested_branch" "$base_branch" "$store_file" "$ckey" "$resume")" || return $?
  wt="$(jq -r '.path' <<<"$workspace")"
  task_branch="$(jq -r '.branch' <<<"$workspace")"
  start_head="$(jq -r '.start_head' <<<"$workspace")"
  log_event "execute_started" "$source_desc repo=$repo workspace=$wt branch=$task_branch resume=${prior:-none}"

  local child_prompt
  child_prompt="$(
    cat "$(cerebro_payloads_dir)/prompts/execute.md"
    printf '\n\n'
    printf 'Selected checkout: %s\nSelected branch: %s\nStarting commit: %s\n\n' "$wt" "$task_branch" "$start_head"
    [[ -n "$base_branch" ]] && printf 'Requested starting reference: %s (used only for a new branch or detached checkout). PR targeting comes from the task delivery instructions.\n\n' "$base_branch"
    printf '<task>\n%s\n</task>\n' "$plan_body"
  )"

  local rc id_capture msg_capture
  id_capture="$(mktemp)"
  msg_capture="$(mktemp)"
  local PAIR_SID="" PAIR_FIFO="" PAIR_STEER="" PAIR_IDLE="" PAIR_STALL="" PAIR_STALL_BUSY=""
  local PAIR_PORT="" PAIR_SERVE_PID="" PAIR_BASE_URL="" PAIR_OPTS=() PAIR_PGID="" PAIR_LAUNCH=()
  (( pair )) && pair_begin execute "$wt" "$task_branch" "$child_log" "$prior"

  local stall_n=0
  while :; do
    # Mark the child in-flight (preserving any prior id we are resuming) BEFORE
    # it launches, so an interrupt now leaves a resumable record.
    child_store_begin "$ckey" "$provider" execute "$wt" "$task_branch" "$child_log" "${prior:+preserve-id}" \
      || die "execute: cannot persist the child workspace"
    child_run "$pair" "$wt" "$child_prompt" "$agent" "$prior" \
      "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
    rc=$?
    pair_cleanup "$pair"

    if (( pair )) && pair_stalled "$child_log"; then
      if (( stall_n < ${CEREBRO_PAIR_STALL_RETRIES:-2} )); then
        stall_n=$((stall_n + 1))
        pair_stall_backoff "$stall_n"
        pair_stall_clear "$child_log"
        pair_begin execute "$wt" "$task_branch" "$child_log" "$PAIR_SID"
        prior="$PAIR_SID"
        continue
      fi
      pair_stall_clear "$child_log"
      log_event "pair_stall_giveup" "after=$stall_n stalls log=$child_log resume=$PAIR_SID"
      python3 "$CEREBRO_LIB_DIR/python/task_workspace.py" refresh "$store_file" "$ckey" >/dev/null || return $?
      rm -f "$id_capture" "$msg_capture"
      die "execute: paired child stalled $stall_n time(s) and was not restarted further; it remains resumable (id $PAIR_SID) -- see $child_log"
    fi
    break
  done

  # Restart replaces the conversation; workspace cleanup is a separate action.
  if (( pair )) && pair_restarted "$child_log"; then
    local diag; diag="$(pair_restart_read "$child_log")"
    local branch; branch="$(execute_worktree_branch "$wt")"
    child_store retire "$ckey" "$(ts_iso)" || die "restart: cannot retire the child conversation"
    python3 "$CEREBRO_LIB_DIR/python/task_workspace.py" refresh "$store_file" "$ckey" >/dev/null || return $?
    pair_cleanup "$pair"
    pair_restart_clear "$child_log"
    log_event "execute_restarted" "log=$child_log workspace=$wt branch=${branch:-none} retained=1"
    rm -f "$id_capture" "$msg_capture"
    printf '=== RESTART REQUESTED ===\n'
    printf '%s\n' "$diag"
    printf '(workspace=%s branch=%s -- files, branch and PR are retained; the native conversation was retired)\n' "$wt" "${branch:-none}"
    printf '=== END RESTART REQUESTED ===\n'
    say "cerebro: inspect the retained work, then run the corrected task with 'cerebro execute $wt' without --worktree. Cleanup requires separate authority."
    return 0
  fi

  python3 "$CEREBRO_LIB_DIR/python/task_workspace.py" refresh "$store_file" "$ckey" >/dev/null || return $?

  if (( rc != 0 )); then
    # Keep failed work resumable when a native ID exists; do not silently start fresh.
    local _cap_id; _cap_id="$(cat "$id_capture" 2>/dev/null || true)"
    rm -f "$id_capture" "$msg_capture"
    if [[ -z "$_cap_id" && -z "$prior" ]]; then
      child_store_done "$ckey"
    fi
    log_event "execute_failed" "rc=$rc log=$child_log"
    warn "execute: child failed (rc=$rc); see $child_log"
    child_fail_stderr "$child_log"
    die "execute: child failed (rc=$rc); see $child_log"
  fi

  # The child's provider id was already persisted at startup (see
  # parse_stream.py); just mark this line of work cleanly finished so it no
  # longer shows up as interrupted in `cerebro status`.
  child_store_done "$ckey"
  local child_id; child_id="$(cat "$id_capture" 2>/dev/null || true)"
  rm -f "$id_capture"
  log_event "execute_finished" "$child_log"
  pair_report "$pair" "$child_log"
  surface_child_reply "$msg_capture" execute "$child_id"
  rm -f "$msg_capture"

  local done_branch; done_branch="$(execute_worktree_branch "$wt")"
  printf '=== TASK WORKTREE: %s (branch %s) ===\n' "$wt" "${done_branch:-detached}"
  say "cerebro: continue this task in $wt -- use that checkout for subsequent execute / review / apply-review / doc-write calls."
  echo "$child_log"
}
