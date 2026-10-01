# Backend adapters own native launch, resume, stream and steering transport.
# All roles in a Cerebro session use its recorded backend.
current_backend() { printf '%s' "${CEREBRO_RESUME_BACKEND:-$CEREBRO_BACKEND}"; }
backend_is() { [[ "$(current_backend)" == "$1" ]]; }

backend_child_agent_name() {
  case "$1" in
    execute|apply-review|doc-write|verify|review|audit|improve) printf '%s\n' "$1" ;;
    *) die "unknown child role: $1" ;;
  esac
}
backend_child_provider() { current_backend; }
backend_answerable_pattern() { printf '%s:%s\n' "$(current_backend)" "$1"; }
backend_materialise_extras() { "backend_$(current_backend)_materialise_extras"; }
backend_launch_orchestrator() { "backend_$(current_backend)_launch_orchestrator" "$@"; }
backend_launch_observer() { "backend_$(current_backend)_launch_observer" "$@"; }
backend_resume_orchestrator() { "backend_$(current_backend)_resume_orchestrator" "$@"; }
backend_acp_child_spec() { "backend_$(current_backend)_acp_child_spec" "$@"; }
pair_begin() { "backend_$(current_backend)_pair_begin" "$@"; }
pair_run() { "backend_$(current_backend)_pair_run" "$@"; }
pair_cleanup() { "backend_$(current_backend)_pair_cleanup" "$@"; }
child_run() { "backend_$(current_backend)_child_run" "$@"; }

# The MCP server runs outside the parent's read-only sandbox. Its argv-only
# command surface retains the CLI's worktree, review and path guards.
backend_supervisor_config() {
  local role="$1"
  export CEREBRO_ROLE="$role"
  local key
  for key in $(compgen -v CEREBRO_); do export "$key"; done
  local native_env=(CODEX_HOME OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT OPENCODE_CONFIG_DIR
    OPENCODE_TEST_HOME CLAUDE_CONFIG_DIR XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME)
  local env
  env="$(python3 -c 'import json,os,sys; print(json.dumps({k:v for k,v in os.environ.items() if (k.startswith("CEREBRO_") and not k.startswith("CEREBRO_CFG_")) or k in sys.argv[1:]}))' "${native_env[@]}")" || return $?
  local cfg="$CEREBRO_SESSION_DIR/tools-$role.json"
  (umask 077; jq -n --arg cmd "$CEREBRO_LIB_DIR/../bin/cerebro" --arg role "$role" --argjson env "$env" \
    '{mcpServers:{cerebro:{command:$cmd,args:["tools",$role],env:$env}}}' > "$cfg") || return $?
  chmod 600 "$cfg"
  printf '%s\n' "$cfg"
}
