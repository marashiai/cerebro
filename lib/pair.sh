# cerebro lib: pair
# pair-programming mode: watch + steer a live child session
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- pair-programming mode -----------------------------------------------
# `--pair` on a child (execute / apply-review / doc-write) lets the developer
# WATCH the live session and STEER it. The transport is backend-specific:
#   pi        -- the child runs in native RPC mode over JSONL stdin/stdout
#   codex     -- the child runs under the native app-server JSON-RPC transport
#   claude    -- the child runs with `--input-format stream-json` stdin; the
#               pump writes JSON user messages to stdin
# After each turn cerebro waits a short window for steering injected
# over a named pipe; each steering message is forwarded as the child's next
# turn and recorded. `cerebro steer "<message>"` injects one instruction.
# The child runs to completion; a quiet window finishes it.
# Steering is recorded as it is injected and folded back so the orchestrator can
# reconcile it against the spec.
#
# pair_begin / pair_run / pair_cleanup are dispatched to the active backend
# (lib/backend.sh -> lib/backend-{pi,codex,claude}.sh). This file holds only
# the shared pair helpers + the per-task worktree management.

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
    printf '  the child runs to completion; it waits a short window after each turn for\n'
    printf '  steering, so steer within that window to keep it open and redirect it.\n'
    printf '  your steering goes straight into the child; it never enters the orchestrator chat.\n'
  } >&2
}

# pair_stall_marker <child_log> -- path of the pump's authoritative stall sidecar.
pair_stall_marker() { printf '%s' "${1%.jsonl}.stalled"; }

# pair_stalled <child_log> -- true iff the pump flagged this child as stalled.
pair_stalled() { [[ -e "$(pair_stall_marker "$1")" ]]; }

# Watched jobs use the native steering transport without a post-turn delay.
watch_prepare() {
  case "$CEREBRO_JEV_ENABLED" in 0|1) ;; *) die "jev_enabled must be 0 or 1" ;; esac
  export CEREBRO_JEV_ENABLED
  (( CEREBRO_JEV_ENABLED )) || return 0
  export CEREBRO_JEV_ENABLED CEREBRO_JEV_API_KEY CEREBRO_JEV_MODEL \
         CEREBRO_JEV_ENDPOINT CEREBRO_JEV_CONFIDENCE CEREBRO_WATCH_PLAN
  python3 "$CEREBRO_LIB_DIR/python/scope_watch.py" || return $?
  (( pair )) || { pair=1; CEREBRO_PAIR_IDLE=0; }
}

# pair_stall_clear <child_log> -- consume (remove) the stall marker.
pair_stall_clear() { rm -f "$(pair_stall_marker "$1")"; }

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
    die "$verb: no live paired session found. Start one with --pair (e.g. 'cerebro execute <repo> ... --pair'), then run 'cerebro $verb \"<message>\"'."
  elif (( ${#candidates[@]} > 1 )); then
    { printf 'cerebro: %s: several live paired sessions -- pass the pipe of the one you mean:\n' "$verb"
      for f in "${candidates[@]}"; do printf '  cerebro %s %s "<message>"\n' "$verb" "$f"; done
    } >&2
    exit 1
  fi
  PAIR_RESOLVED_FIFO="${candidates[0]}"
}

# pair_stall_backoff <attempt> -- wait CEREBRO_PAIR_STALL_BACKOFF * 2^(attempt-1)
# seconds before a resume restart. Default base is 5s.
pair_stall_backoff() {
  local n="$1"
  local base="${CEREBRO_PAIR_STALL_BACKOFF:-5}"
  local d=$(( base * (1 << (n - 1)) ))
  log_event "pair_stall_restart" "attempt=$n backoff=${d}s"
  if (( d > 0 )); then sleep "$d"; fi
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

# pair_report <pair> <child_log> -- after a paired child exits, fold the live
# steering it received (recorded to the .steering.md beside its log as each
# message was injected) back onto stdout as a compact bullet block for the
# orchestrator to reconcile against the spec. A no-op when unpaired or when no
# steering was sent.
pair_report() {
  local pair="$1" child_log="$2"
  (( pair )) || return 0
  local steer_path="${child_log%.jsonl}.steering.md" n=0
  [[ -s "$steer_path" ]] && n="$(grep -c '^- ' "$steer_path" 2>/dev/null || printf 0)"
  if [[ "${n:-0}" -gt 0 ]]; then
    log_event "pair_steering" "n=$n path=$steer_path"
    printf '=== PAIR STEERING (%s message(s), applied live) ===\n' "$n"
    cat "$steer_path"
    printf '=== END PAIR STEERING (file: %s) ===\n' "$steer_path"
    say "cerebro: folded $n live steering message(s) from the paired session -> $steer_path"
  else
    say "cerebro: pair mode -- no steering was sent during the paired session"
  fi
}
