# Hunk sidecar notes are the reviewer's only permitted write surface.
cmd_hunk() {
  require_session
  python3 "$CEREBRO_LIB_DIR/python/hunk_bridge.py" "$@"
}
