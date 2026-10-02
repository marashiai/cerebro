# Implement authorized work in an isolated task worktree.

cmd_execute() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local plan_path=""
  local prompt_text=""
  local base_branch=""
  local new_branch=""
  local pair=0
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
      --branch) shift; new_branch="${1:-}";  shift || true ;;
      --model)  shift; model="${1:-}";       shift || true ;;
      --pair)   pair=1; shift ;;
      --watch)  CEREBRO_JEV_ENABLED=1; shift ;;
      --no-watch) CEREBRO_JEV_ENABLED=0; shift ;;
      *) die "execute: unknown arg: $1" ;;
    esac
  done
  [[ -n "$repo" ]] \
    || die "usage: cerebro execute <repo-abs-path> (<plan-path> | --prompt \"<text>\") [--base <branch>] [--branch <name>] [--model <provider/model>]"
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
  # Identical --base and --branch is the old existing-branch invocation, which
  # is gone. The child always cuts a FRESH branch in its worktree, so asking it
  # to create branch X from origin/X and open a PR back to X is impossible.
  if [[ -n "$base_branch" && "$base_branch" == "$new_branch" ]]; then
    die "execute: --base and --branch must differ ('$base_branch'); existing-branch mode was removed -- to do follow-up work on a branch, target that task's worktree path (passed back by its execute) or use 'cerebro apply-review'"
  fi

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

  # Stacked-branch support. --base pins the branch this PR forks from and
  # targets (so a suite of plans can stack: plan 1 off main, plan 2 off
  # plan 1's branch, ...); --branch pins the new branch's name so the
  # orchestrator deterministically knows it for the next plan's --base. When
  # neither is given the child keeps its default behaviour (fetch the repo's
  # base branch, branch from it, open the PR against the default base). The
  # child always works inside an isolated worktree and always creates a fresh
  # branch -- there is no "commit onto an existing branch" mode.
  local branch_instr=""
  if [[ -n "$base_branch" || -n "$new_branch" ]]; then
    branch_instr='Task branch constraints:'
    if [[ -n "$base_branch" ]]; then
      branch_instr+=$'\n'"  * Fetch '$base_branch' from origin (git fetch origin $base_branch) and create your new branch from origin/$base_branch -- NOT from origin/main or any other default base."
    fi
    if [[ -n "$new_branch" ]]; then
      branch_instr+=$'\n'"  * Name the new branch EXACTLY '$new_branch' (it already follows the repo's conventions; do not invent a different name)."
    fi
    if [[ -n "$base_branch" ]]; then
      branch_instr+=$'\n'"  * If a pull request is authorized, set its base to '$base_branch' (gh pr create --base $base_branch) so this PR stacks on top of it."
    fi
  fi

  if [[ -n "$plan_path" ]]; then
    say "cerebro: executing $plan_path in $repo${base_branch:+ (base=$base_branch)}${new_branch:+ (branch=$new_branch)}"
  else
    say "cerebro: executing inline prompt in $repo${base_branch:+ (base=$base_branch)}${new_branch:+ (branch=$new_branch)}"
  fi

  # Child-session continuity is only for incomplete work. A completed execute
  # must not bleed provider context into the next sub-agent, even when a trunk
  # based suite runs multiple plans on the same branch. The key always includes
  # the plan/prompt; --branch narrows it but never replaces it.
  local store_file; store_file="$(child_sessions_file)"
  local key_disc; key_disc="$(execute_child_disc "$new_branch" "$plan_path" "$prompt_text")"
  local ckey prior=""
  ckey="$(child_key "$repo" execute "$key_disc")"
  if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
    :
  else
    prior=""
  fi
  log_event "execute_started" "$source_desc repo=$repo base=${base_branch:-default} branch=${new_branch:-auto} resume=${prior:-none}"

  # Isolated worktree: the child runs in a private worktree under
  # $CEREBRO_HOME/worktrees/<ckey>, never the user's live checkout. Its base
  # start point is --base if given, else origin/HEAD's default branch, else
  # main; the child re-fetches and branches inside it. The worktree persists
  # between runs (follow-ups reuse it) and is removed only by a restart or
  # `cerebro worktrees cleanup`.
  local wt base_ref
  wt="$(execute_worktree_path "$ckey")"
  base_ref="$base_branch"
  if [[ -z "$base_ref" ]]; then
    base_ref="$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null \
                | sed 's#^origin/##')"
  fi
  [[ -n "$base_ref" ]] || base_ref="main"
  execute_worktree_create "$repo" "$wt" "$base_ref"

  local child_prompt
  child_prompt="$(
    cat "$(cerebro_payloads_dir)/prompts/execute.md"
    printf '\n\n'
    [[ -n "$branch_instr" ]] && printf '%s\n\n' "$branch_instr"
    printf '<task>\n%s\n</task>\n' "$plan_body"
  )"

  local rc id_capture msg_capture
  id_capture="$(mktemp)"
  msg_capture="$(mktemp)"
  local PAIR_SID="" PAIR_FIFO="" PAIR_STEER="" PAIR_IDLE="" PAIR_STALL="" PAIR_STALL_BUSY=""
  local PAIR_PORT="" PAIR_SERVE_PID="" PAIR_BASE_URL="" PAIR_OPTS=() PAIR_PGID="" PAIR_LAUNCH=()
  (( pair )) && pair_begin execute "$wt" "$new_branch" "$child_log" "$prior"

  local stall_n=0
  while :; do
    # Mark the child in-flight (preserving any prior id we are resuming) BEFORE
    # it launches, so an interrupt now leaves a resumable record.
    child_store_begin "$ckey" "$provider" execute "$repo" "${new_branch:-auto}" "$child_log" "${prior:+preserve-id}"
    child_run "$pair" "$wt" "$child_prompt" "$agent" "$prior" \
      "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
    rc=$?
    pair_cleanup "$pair"

    if (( pair )) && pair_stalled "$child_log"; then
      if (( stall_n < ${CEREBRO_PAIR_STALL_RETRIES:-2} )); then
        stall_n=$((stall_n + 1))
        pair_stall_backoff "$stall_n"
        pair_stall_clear "$child_log"
        pair_begin execute "$wt" "$new_branch" "$child_log" "$PAIR_SID"
        prior="$PAIR_SID"
        continue
      fi
      pair_stall_clear "$child_log"
      log_event "pair_stall_giveup" "after=$stall_n stalls log=$child_log resume=$PAIR_SID"
      rm -f "$id_capture" "$msg_capture"
      die "execute: paired child stalled $stall_n time(s) and was not restarted further; it remains resumable (id $PAIR_SID) -- see $child_log"
    fi
    break
  done

  # Authorized restart abandons this task and tears down its isolated branch/PR/worktree.
  # Retire the old native conversation before handing the diagnosis back for relaunch.
  if (( pair )) && pair_restarted "$child_log"; then
    local diag; diag="$(pair_restart_read "$child_log")"
    local branch; branch="$(execute_worktree_branch "$wt")"
    local real_branch=0
    [[ -n "$branch" && "$branch" != "HEAD" && "$branch" != "$base_ref" ]] && real_branch=1
    if (( real_branch )); then
      [[ -z "$new_branch" || "$branch" == "$new_branch" ]] \
        || die "restart: child switched to '$branch' instead of '$new_branch'; branch and worktree retained"
      local initial_branches branch_check
      initial_branches="$(git -C "$wt" rev-parse --git-path cerebro-initial-branches)" \
        || die "restart: cannot locate task branch metadata; branch and worktree retained"
      [[ -r "$initial_branches" ]] \
        || die "restart: initial branch metadata is missing; branch and worktree retained"
      grep -Fxq -e "refs/heads/$branch" -e "refs/remotes/origin/$branch" \
        -e remote-unknown "$initial_branches"; branch_check=$?
      [[ "$branch_check" == 1 ]] \
        || die "restart: cannot prove '$branch' was created for this task; branch and worktree retained"
    fi
    # Tear down the PR + remote branch FIRST (while the worktree still exists, so
    # `gh` runs inside it), then remove the worktree (un-checking-out the branch),
    # then delete the now-unused local branch.
    if (( real_branch )); then
      if ! ( cd "$wt" && gh pr close "$branch" --delete-branch >/dev/null 2>&1 ); then
        git -C "$repo" push origin --delete "$branch" >/dev/null 2>&1 || true
      fi
    fi
    execute_worktree_remove "$repo" "$wt"
    (( real_branch )) && git -C "$repo" branch -D "$branch" >/dev/null 2>&1 || true
    child_store_done "$ckey"
    pair_cleanup "$pair"
    pair_restart_clear "$child_log"
    log_event "execute_restarted" "log=$child_log base=${base_branch:-default} branch=${branch:-none} torn_down=1"
    rm -f "$id_capture" "$msg_capture"
    printf '=== RESTART REQUESTED ===\n'
    printf '%s\n' "$diag"
    printf '(repo=%s branch=%s -- the strayed work was a fresh branch and has been fully torn down: branch, PR, and worktree are all gone, so the relaunch starts from a clean slate)\n' \
      "$repo" "${branch:-none}"
    printf '=== END RESTART REQUESTED ===\n'
    say "cerebro: paired child was RESTARTED -- fold the diagnosis above into a corrected plan/prompt (make the prior mistake explicit at the START), then re-run 'cerebro execute' FRESH."
    return 0
  fi

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

  # Announce the persistent worktree so the orchestrator can address THIS task
  # (its review / apply-review / doc-write / restart) by the worktree path. The
  # worktree is NOT removed on success -- follow-ups reuse it.
  local done_branch; done_branch="$(execute_worktree_branch "$wt")"
  printf '=== TASK WORKTREE: %s (branch %s) ===\n' "$wt" "${done_branch:-detached}"
  say "cerebro: this task's work lives in the worktree $wt -- pass that path as <repo> for this task's review / apply-review / doc-write / restart (NOT the main checkout)."
  echo "$child_log"
}
