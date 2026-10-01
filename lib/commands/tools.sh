# Restricted command MCP surface for native supervisors and observers.
cmd_tools() {
  require_session
  local role="${1:-}"
  case "$role" in supervisor|observer|reviewer) ;; *) die "usage: cerebro tools <supervisor|observer|reviewer>" ;; esac
  export CEREBRO_HOME CEREBRO_ROLE="$role"
  exec python3 "$CEREBRO_LIB_DIR/python/command_server.py" "$role" "$CEREBRO_LIB_DIR/../bin/cerebro"
}

cmd_guide() {
  [[ $# -eq 1 && "$1" =~ ^cerebro-[a-z-]+$ ]] || die "usage: cerebro guide <skill-name>"
  local path="$(cerebro_skills_dir)/$1/SKILL.md"
  [[ -f "$path" ]] || die "unknown Cerebro skill: $1"
  cerebro_skill_body "$path"
}
