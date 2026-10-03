# cerebro lib: main
# dispatch: usage routing for top-level subcommands
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- dispatch ------------------------------------------------------------

main() {
  if [[ $# -eq 0 ]]; then
    cmd_launch
    return
  fi
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --resume) shift; cmd_resume "${1:-}" ;;
    list) shift; cmd_list "$@" ;;
    _task-stage) shift; cmd_task_stage "$@" ;;
    execute) shift; cmd_execute "$@" ;;
    detach) shift; cmd_detach "$@" ;;
    wait) shift; cmd_wait "$@" ;;
    jobs) shift; cmd_jobs "$@" ;;
    cancel) shift; cmd_cancel "$@" ;;
    # ACP (Agent Client Protocol) front-end for editors (Zed, ...). `acp` is the
    # external entry point; it dispatches to `mint` and `set-foreign` itself
    # (called by the python ACP server over `cerebro acp <name>`).
    acp) shift; cmd_acp "$@" ;;
    # cerebro MCP server: a generic terminal MCP (lib/python/cerebro_mcp_server.py) that
    # holds long-lived PTYs and exposes them as MCP tools so a controller can
    # drive an interactive TTY program (cerebro or any other) event-driven.
    tools) shift; cmd_tools "$@" ;;
    cerebro-mcp) shift; cmd_cerebro_mcp "$@" ;;
    answer) shift; cmd_answer "$@" ;;
    models)  shift; cmd_models "$@" ;;
    model-env) shift; cmd_model_env "$@" ;;
    steer) shift; cmd_steer "$@" ;;
    restart) shift; cmd_restart "$@" ;;
    worktrees) shift; cmd_worktrees "$@" ;;
    recall) shift; cmd_recall "$@" ;;
    status) shift; cmd_status "$@" ;;
    *) die "unknown subcommand: $1 (try --help)" ;;
  esac
}
