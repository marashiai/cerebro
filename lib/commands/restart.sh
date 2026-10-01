# cerebro lib: commands/restart
# subcommand: restart (abandon a strayed paired child for a clean-slate re-run)
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- subcommand: cerebro restart [<pipe>] "<diagnosis>" ------------------
# Heavier sibling of `cerebro steer`. Where steer is a small in-flight nudge,
# restart ABANDONS a paired `--pair` execute child that started from wrong
# assumptions or has drifted from the spec, so the orchestrator can relaunch it
# FRESH with a corrected prompt. It writes one restart line to the child's pipe;
# the pump reaps the child and marks it, and `cerebro execute` then reverts the
# strayed work to a clean slate and surfaces the diagnosis. With ONE argument
# that argument is the diagnosis and the live paired session is found
# automatically (the common case); with TWO, the first is the <pipe> path (from
# the child's PAIR MODE banner, to pick one when several run) and the second is
# the diagnosis. The diagnosis is REQUIRED -- it is what the orchestrator uses to
# correct the relaunch prompt. Runs from any directory.
cmd_restart() {
  local fifo="" diag=""
  if (( $# == 1 )); then
    diag="$1"
  elif (( $# >= 2 )); then
    fifo="$1"; diag="$2"
  else
    die "restart: usage: cerebro restart [<pipe>] \"<diagnosis>\""
  fi
  [[ -n "$diag" ]] || die "restart: empty diagnosis (it is what the orchestrator needs to correct the prompt)"
  diag="[${CEREBRO_ROLE:-user}] $diag"
  pair_resolve_live_fifo "$fifo" restart
  fifo="$PAIR_RESOLVED_FIFO"
  python3 "$CEREBRO_LIB_DIR/python/steer_send.py" "$fifo" "$diag" R \
    || die "restart: could not deliver (the child may have finished)"
  say "cerebro: restart signalled to $(basename "${fifo%.steer.fifo}") -- the child is being abandoned and the orchestrator will relaunch it fresh with a corrected prompt"
  say "cerebro: restart is for replacing a strayed agent; for a small in-flight nudge use 'cerebro steer' instead"
}
