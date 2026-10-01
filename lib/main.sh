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
    --observe) shift; cmd_launch_observer "${1:-}" ;;
    list) shift; cmd_list "$@" ;;
    plan) shift; cmd_plan "$@" ;;
    plans) shift; cmd_plans "$@" ;;
    audit) shift; cmd_audit "$@" ;;
    improve) shift; cmd_improve "$@" ;;
    execute) shift; cmd_execute "$@" ;;
    review) shift; cmd_review "$@" ;;
    apply-review) shift; cmd_apply_review "$@" ;;
    verify) shift; cmd_verify "$@" ;;
    doc-write) shift; cmd_doc_write "$@" ;;
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
    guide) shift; cmd_guide "$@" ;;
    cerebro-mcp) shift; cmd_cerebro_mcp "$@" ;;
    answer) shift; cmd_answer "$@" ;;
    models)  shift; cmd_models "$@" ;;
    model-env) shift; cmd_model_env "$@" ;;
    steer) shift; cmd_steer "$@" ;;
    restart) shift; cmd_restart "$@" ;;
    observe) shift; cmd_observe "$@" ;;
    worktrees) shift; cmd_worktrees "$@" ;;
    recall) shift; cmd_recall "$@" ;;
    status) shift; cmd_status "$@" ;;
    spec) shift; cmd_spec "$@" ;;
    learnings)  shift; cmd_learnings "$@" ;;
    learn-note) shift; cmd_learn_note "$@" ;;
    learn-set)  shift; cmd_learn_set "$@" ;;
    overlay)    shift; cmd_overlay "$@" ;;
    git)    shift; cmd_git "$@" ;;
    gh)     shift; cmd_gh "$@" ;;
    read)   shift; cmd_read "$@" ;;
    grep)   shift; cmd_grep "$@" ;;
    ls)     shift; cmd_ls "$@" ;;
    *) die "unknown subcommand: $1 (try --help)" ;;
  esac
}
