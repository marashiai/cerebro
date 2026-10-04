# cerebro lib: commands/restart
# subcommand: restart (retire an execute conversation and retain its work)
# Sourced by bin/cerebro; not meant to be executed directly.

# Replace an execute child's native conversation while retaining its checkout.
# One argument resolves the live pair; two select its steering pipe explicitly.
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
  say "cerebro: restart signalled to $(basename "${fifo%.steer.fifo}") -- the conversation is being retired; the checkout, branch and PR are retained"
  say "cerebro: restart is for replacing a strayed agent; for a small in-flight nudge use 'cerebro steer' instead"
}
