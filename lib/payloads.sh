# Shared task-local instructions and native session bindings.
cerebro_payloads_dir() { printf '%s\n' "$CEREBRO_LIB_DIR/payloads"; }
cerebro_skills_dir() { printf '%s\n' "$CEREBRO_LIB_DIR/payloads/skills"; }
cerebro_skill_body() {
  awk 'BEGIN{f=0} /^---$/{if(!f){f=1;next};if(f==1){f=2;next}} f==2||f==0{print}' "$1"
}
cerebro_settings_json() {
  local command; command="bash $(printf '%q' "$1")"
  jq --arg command "$command" '.hooks.UserPromptSubmit[0].hooks[0].command = $command' \
    "$(cerebro_payloads_dir)/settings.json"
}
cerebro_system_prompt() { cerebro_skill_body "$(cerebro_skills_dir)/cerebro-supervisor/SKILL.md"; }
child_sys_prompt() {
  case "$1" in
    execute) printf '%s\n' 'You implement the delegated task. Follow its goal and repository instructions; return the requested structured handoff.' ;;
    review) printf '%s\n' 'You independently review the delegated task. Follow repository instructions, inspect the actual work, and return structured findings without changing implementation.' ;;
    *) die "unknown child role: $1" ;;
  esac
}
claude_orchestrator_agent_file() {
  printf '%s\n' '---' 'name: cerebro-orchestrator' 'description: Cerebro supervisor' '---'
  cerebro_system_prompt
}
