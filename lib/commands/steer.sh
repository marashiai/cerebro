# cerebro lib: commands/steer
# subcommand: steer (one-shot inject into a paired child)
# Sourced by bin/cerebro; not meant to be executed directly.


# steer_fifo_live <fifo> -- true if the pipe exists AND the child's pump is
# still reading it (a writer-open succeeds), i.e. it is a live paired session
# rather than a stale pipe left by a crashed child.
steer_fifo_live() {
  [[ -p "$1" ]] || return 1
  python3 "$CEREBRO_LIB_DIR/python/fifo_live.py" "$1" 2>/dev/null
}

# ----- subcommand: cerebro steer [<pipe>] "<message>" ----------------------
# One-shot steering: inject a single instruction into a live `--pair` child and
# return at once (no attach, no lock). With ONE argument that argument is the
# message and the live paired session is found automatically (the common case);
# with TWO, the first is the <pipe> path from the child's PAIR MODE banner (to
# pick one when several run at once) and the second is the message. The message
# becomes the child's next user turn. Runs from any directory.
cmd_steer() {
  local fifo="" msg=""
  if (( $# == 1 )); then
    msg="$1"
  elif (( $# >= 2 )); then
    fifo="$1"; msg="$2"
  else
    die "steer: usage: cerebro steer [<pipe>] \"<message>\""
  fi
  [[ -n "$msg" ]] || die "steer: empty steering message"
  case "${CEREBRO_ROLE:-user}" in
    observer) msg="[observer] Enforce the approved spec and plan only; this is not a new user requirement. $msg" ;;
    supervisor) msg="[supervisor] Enforce the approved spec and plan only; this is not a new user requirement. $msg" ;;
    *) msg="[user] $msg" ;;
  esac
  pair_resolve_live_fifo "$fifo" steer
  fifo="$PAIR_RESOLVED_FIFO"
  python3 "$CEREBRO_LIB_DIR/python/steer_send.py" "$fifo" "$msg" || die "steer: could not deliver (the child may have finished)"
  say "cerebro: steered $(basename "${fifo%.steer.fifo}")"
}
