# Canonical shared skills, role prompts, native configuration and templates.
cerebro_payloads_dir() { printf '%s\n' "$CEREBRO_LIB_DIR/payloads"; }

cerebro_skills_dir() { printf '%s\n' "$CEREBRO_LIB_DIR/payloads/skills"; }

# Strip optional YAML frontmatter when a native backend needs a prompt body.
cerebro_skill_body() {
  awk 'BEGIN{f=0} /^---$/{if(!f){f=1;next};if(f==1){f=2;next}} f==2||f==0{print}' "$1"
}

# OpenCode V2 plugin: enforce role permissions and bind native parent sessions.
cerebro_plugin_js() {
  cat "$(cerebro_payloads_dir)/plugin/cerebro.js"
}


# Bind Claude prompts to the Cerebro transcript and active-session link.
cerebro_hook_script() { cat "$(cerebro_payloads_dir)/hook.sh"; }

# Substitute the literal hook path in Claude settings without shell evaluation.
cerebro_settings_json() {
  local hook_path="$1" tpl
  tpl="$(cat "$(cerebro_payloads_dir)/settings.json")"
  printf '%s\n' "${tpl//__CEREBRO_HOOK_PATH__/$hook_path}"
}

cerebro_system_prompt() { cerebro_skill_body "$(cerebro_skills_dir)/cerebro-supervisor/SKILL.md"; }

# The shared observer skill adds scope comparison and authorized steering.
cerebro_observe_mode_prompt() { cerebro_skill_body "$(cerebro_skills_dir)/cerebro-observer/SKILL.md"; }

# Read-only constraints shared by audit, review and improvement analysis.
cerebro_reviewer_note() {
  cat "$(cerebro_payloads_dir)/prompts/reviewer-note.md"
}

# Audit task plus shared read-only constraints and the user-owned grader overlay.
cerebro_audit_prompt() {
  local out; out="$(printf '%s\n\n%s' \
    "$(cerebro_reviewer_note)" \
    "$(cat "$(cerebro_payloads_dir)/prompts/audit.md")")"
  local ov; ov="$(overlay_body grader)"
  [[ -n "$ov" ]] && out="$(printf '%s\n\n# Local grader overlay\n%s' "$out" "$ov")"
  printf '%s\n' "$out"
}

# Compose improvement components in order, each with its optional local overlay.
CEREBRO_META_COMPONENTS="analyzer retriever allocator proposer evolver"

# Fast-loop analysis composes shared components and local meta-overlays.
cerebro_improve_prompt() {
  local out comp ov
  out="$(printf '%s\n\n%s' \
    "$(cerebro_reviewer_note)" \
    "$(cat "$(cerebro_payloads_dir)/prompts/meta/intro.md")")"
  for comp in $CEREBRO_META_COMPONENTS; do
    out="$(printf '%s\n\n%s' "$out" \
      "$(cat "$(cerebro_payloads_dir)/prompts/meta/$comp.md")")"
    ov="$(overlay_body "meta-$comp")"
    [[ -n "$ov" ]] && out="$(printf '%s\n\n# Local meta-%s overlay\n%s' "$out" "$comp" "$ov")"
  done
  printf '%s\n' "$out"
}

# Slow-loop references are diagnostic input, not conflicting output instructions.
cerebro_meta_improve_prompt() {
  local out; out="$(printf '%s\n\n%s' \
    "$(cerebro_reviewer_note)" \
    "$(cat "$(cerebro_payloads_dir)/prompts/meta-improve.md")")"
  local comp ov
  out="$(printf '%s\n\n## Current meta-skill components (for reference -- diagnose them, do not follow their output instructions)\n' "$out")"
  for comp in $CEREBRO_META_COMPONENTS; do
    out="$(printf '%s\n\n### %s\n%s' "$out" "$comp" \
      "$(cat "$(cerebro_payloads_dir)/prompts/meta/$comp.md")")"
    ov="$(overlay_body "meta-$comp")"
    [[ -n "$ov" ]] && out="$(printf '%s\n\n# Local meta-%s overlay\n%s' "$out" "$comp" "$ov")"
  done
  printf '%s\n' "$out"
}


# Common lifecycle rules: terminal questions, joined background work and cleanup.
child_noninteractive_note() {
  cat "$(cerebro_payloads_dir)/prompts/noninteractive-note.md"
}

child_sys_prompt() {
  local role="$1"
  case "$role" in
    execute|apply-review|doc-write|verify)
      local f="$(cerebro_skills_dir)/cerebro-$role/SKILL.md"
      printf '%s\n\n%s' "$(cerebro_skill_body "$f")" "$(child_noninteractive_note)" ;;
    review|audit|improve)
      printf '%s\n' "$(cerebro_reviewer_note)" ;;
    *) die "child_sys_prompt: unknown role: $role" ;;
  esac
}

# ----- default templates ----------------------------------------------------

# Bootstrap defaults are user-editable and never overwrite existing repo instructions.
cerebro_default_agents_md() { cat "$(cerebro_payloads_dir)/templates/AGENTS.md"; }

cerebro_default_claude_md() { cat "$(cerebro_payloads_dir)/templates/CLAUDE.md"; }

# Claude's ACP frontend pins its native tool surface through an agent selector.
# The behavior remains the same shared supervisor skill used by terminal mode.
claude_orchestrator_agent_file() {
  printf '%s\n' '---' 'name: cerebro-orchestrator' 'description: Cerebro supervisor' 'tools: mcp__cerebro__command' '---'
  cerebro_system_prompt
}
