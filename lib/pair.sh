# Shared event-driven steering and managed checkout helpers.

# pair_label <role> <repo> [branch] -- a stable, human-readable session name
# for the paired child (shown in cerebro's connect banner).
pair_label() {
  local role="$1" repo="$2" branch="${3:-}"
  printf 'cerebro:%s:%s%s' "$role" "$(basename "$repo")" "${branch:+:$branch}"
}

# pair_banner <role> <sid> <label> <fifo> -- tell the developer how to attach to
# the live child. Goes to stderr so it never pollutes the stdout child-log path
# contract.
pair_banner() {
  local role="$1" sid="$2" label="$3" fifo="$4"
  {
    printf 'cerebro: PAIR MODE -- watch this %s session live and steer it.\n' "$role"
    printf '  session : %s (id %s)\n' "$label" "$sid"
    printf '  steer   : cerebro steer "<message>"   (inject one instruction; returns at once)\n'
    printf '            if several paired sessions run at once: cerebro steer %s "<message>"\n' "$fifo"
    printf '  steer while the child is running; the steering channel closes when it finishes.\n'
    printf '  your steering goes straight into the child; it never enters the orchestrator chat.\n'
  } >&2
}

# Watched jobs use the native steering transport without a post-turn delay.
watch_prepare() {
  case "$CEREBRO_JEV_ENABLED" in 0|1) ;; *) die "jev_enabled must be 0 or 1" ;; esac
  export CEREBRO_JEV_ENABLED
  (( CEREBRO_JEV_ENABLED )) || return 0
  export CEREBRO_JEV_ENABLED CEREBRO_JEV_API_KEY CEREBRO_JEV_MODEL \
         CEREBRO_JEV_ENDPOINT CEREBRO_JEV_CONFIDENCE CEREBRO_TASK_FILE
  python3 "$CEREBRO_LIB_DIR/python/scope_watch.py" || return $?
  (( pair )) || { pair=1; CEREBRO_PAIR_IDLE=0; }
}

# pair_restart_marker <child_log> -- path of the pump's restart sidecar (holds
# the diagnosis text the orchestrator uses to correct the relaunch prompt).
pair_restart_marker() { printf '%s' "${1%.jsonl}.restart"; }

# pair_restarted <child_log> -- true iff the pump flagged this child for restart.
pair_restarted() { [[ -e "$(pair_restart_marker "$1")" ]]; }

# pair_restart_read <child_log> -- emit the restart diagnosis text.
pair_restart_read() { cat "$(pair_restart_marker "$1")" 2>/dev/null; }

# pair_restart_clear <child_log> -- consume (remove) the restart marker.
pair_restart_clear() { rm -f "$(pair_restart_marker "$1")"; }

# pair_resolve_live_fifo <pipe> [verb] -- resolve the steering fifo of a live
# paired child into the global PAIR_RESOLVED_FIFO. With an explicit <pipe> it
# validates that pipe is live; with none it globs every session's children
# steer fifos, keeps the live ones, and picks the single match -- or prints
# several-sessions / no-session guidance to stderr and fails. Shared by
# `cerebro steer` and `cerebro restart` so their discovery UX is identical; the
# verb ($2, default `steer`) only tailors the hint. It assigns to a global
# rather than echoing so a no-match `die`/`exit` terminates the caller (a
# command-substitution capture would trap it in a subshell instead).
PAIR_RESOLVED_FIFO=""
pair_resolve_live_fifo() {
  local fifo="${1:-}" verb="${2:-steer}"
  PAIR_RESOLVED_FIFO=""
  if [[ -n "$fifo" ]]; then
    [[ -p "$fifo" ]] || die "$verb: no live paired session at $fifo (the child may have finished)"
    PAIR_RESOLVED_FIFO="$fifo"
    return 0
  fi
  local candidates=() f
  shopt -s nullglob
  for f in "$CEREBRO_HOME"/sessions/*/children/*.steer.fifo; do
    steer_fifo_live "$f" && candidates+=("$f")
  done
  shopt -u nullglob
  if (( ${#candidates[@]} == 0 )); then
    die "$verb: no live paired session found. Start a task with 'cerebro execute' first, then run 'cerebro $verb \"<message>\"'."
  elif (( ${#candidates[@]} > 1 )); then
    { printf 'cerebro: %s: several live paired sessions -- pass the pipe of the one you mean:\n' "$verb"
      for f in "${candidates[@]}"; do printf '  cerebro %s %s "<message>"\n' "$verb" "$f"; done
    } >&2
    exit 1
  fi
  PAIR_RESOLVED_FIFO="${candidates[0]}"
}

# Workspace selection lives in python/task_workspace.py; these helpers inspect
# and remove explicitly managed worktrees.

# execute_worktree_branch <wt> -- the branch the child produced in the worktree
# (empty or "HEAD" when it is still detached, i.e. no branch was created).
execute_worktree_branch() {
  git -C "$1" rev-parse --abbrev-ref HEAD 2>/dev/null || true
}

# Removal must succeed through Git; never recursively delete an unregistered path.
execute_worktree_remove() {
  local repo="$1" wt="$2"
  git -C "$repo" worktree remove --force "$wt" \
    || die "worktrees: Git refused cleanup; retained $wt"
}
