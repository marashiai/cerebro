# Controller orchestration MCP; only the supervisor receives it.
cmd_tools() {
  require_session
  [[ "${1:-}" == supervisor ]] || die 'usage: cerebro tools supervisor'
  export CEREBRO_HOME CEREBRO_ROLE=supervisor
  exec python3 "$CEREBRO_LIB_DIR/python/command_server.py" supervisor "$CEREBRO_LIB_DIR/../bin/cerebro"
}
