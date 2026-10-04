# Preserve native defaults before a supervisor or worker role overrides them.
backend_claude_capture_model_env() {
  if [[ -z "${CEREBRO_CLAUDE_NATIVE_MODEL_ENV+x}" ]]; then
    CEREBRO_CLAUDE_NATIVE_MODEL_ENV="$(python3 -c 'import json,os; print(json.dumps({k:os.environ[k] for k in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_AUTO_COMPACT_WINDOW") if k in os.environ}))' )"
    export CEREBRO_CLAUDE_NATIVE_MODEL_ENV
  fi
}

# Custom gateways receive the selected model for both task and housekeeping calls.
backend_claude_endpoint_env() {
  [[ -n "$CEREBRO_CLAUDE_BASE_URL" ]] || return 0
  local model="${1-$CEREBRO_MODEL}" previous_model="${ANTHROPIC_MODEL:-}" key
  backend_claude_capture_model_env
  for key in ANTHROPIC_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL CLAUDE_CODE_AUTO_COMPACT_WINDOW; do
    if jq -e --arg key "$key" 'has($key)' <<<"$CEREBRO_CLAUDE_NATIVE_MODEL_ENV" >/dev/null; then
      export "$key=$(jq -r --arg key "$key" '.[$key]' <<<"$CEREBRO_CLAUDE_NATIVE_MODEL_ENV")"
    else
      unset "$key"
    fi
  done
  export ANTHROPIC_BASE_URL="$CEREBRO_CLAUDE_BASE_URL"
  export ANTHROPIC_AUTH_TOKEN="${CEREBRO_CLAUDE_AUTH_TOKEN:-ollama}"
  unset ANTHROPIC_API_KEY
  [[ -n "$model" ]] || return 0
  export ANTHROPIC_MODEL="$model"
  export ANTHROPIC_DEFAULT_HAIKU_MODEL="$model"
  # Use the catalog context window for custom gateway models when available.
  local ctx; ctx="$(models_context_tokens "$model")"
  if [[ -n "$ctx" ]]; then
    export CLAUDE_CODE_AUTO_COMPACT_WINDOW="$ctx"
  elif [[ -n "$previous_model" && "$previous_model" != "$model" ]]; then
    unset CLAUDE_CODE_AUTO_COMPACT_WINDOW
  fi
}

backend_claude_child_run_opts() {
  local role="$1" resume="${2:-}" model="${3:-}"
  CHILD_RUN_OPTS=(-p --output-format stream-json --verbose
    --append-system-prompt "$(child_sys_prompt "$role")")
  CHILD_RUN_OPTS+=(--permission-mode bypassPermissions)
  [[ -z "${CEREBRO_CHILD_EFFORT:-}" ]] || CHILD_RUN_OPTS+=(--effort "$CEREBRO_CHILD_EFFORT")
  [[ -n "$model" ]] && CHILD_RUN_OPTS+=(--model "$model")
  [[ -n "$resume" ]] && CHILD_RUN_OPTS+=(--resume "$resume")
}

# Capture native stream-json and errors; pair mode additionally accepts input turns.
backend_claude_child_run() {
  local pair="$1" cwd="$2" prompt="$3" role="$4" resume="$5" \
        child_log="$6" msg_capture="$7" id_capture="$8" store_file="$9" ckey="${10}"
  local model="${11-$CEREBRO_MODEL}"

  playwright_isolate_child

  backend_claude_endpoint_env "$model"

  if (( pair )); then
    backend_claude_pair_run "$cwd" "$prompt" "$role" "$resume" \
      "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
    return $?
  fi

  backend_claude_child_run_opts "$role" "$resume" "$model"
  local err_log="${child_log%.*}.err.log"
  ( cd "$cwd" && printf '%s' "$prompt" \
      | env -u CEREBRO_SESSION_ID -u CEREBRO_SESSION_DIR -u CEREBRO_ROLE \
        -u CEREBRO_JEV_API_KEY -u CEREBRO_CFG_JEV_API_KEY -u CEREBRO_JOB_STATUS \
        "${TIMEOUT_CMD[@]}" "$CEREBRO_CLAUDE_CMD" "${CHILD_RUN_OPTS[@]}" 2>"$err_log" \
      | tee "$child_log" \
      | python3 "$CEREBRO_LIB_DIR/python/parse_stream.py" \
          "$msg_capture" "$id_capture" "$store_file" "$ckey" claude )
  return $?
}

backend_claude_materialise_extras() {
  mkdir -p "$CEREBRO_HOME/.claude"
  write_if_changed "$CEREBRO_HOME/.claude/settings.local.json" \
    "$(cerebro_settings_json "$CEREBRO_LIB_DIR/payloads/hook.sh")"
}

backend_claude_parent_opts() {
  local config
  config="$(backend_supervisor_config supervisor)"
  PARENT_OPTS=(--settings "$CEREBRO_HOME/.claude/settings.local.json" --mcp-config "$config"
    --append-system-prompt "$(cerebro_system_prompt)")
  [[ -z "$CEREBRO_SUPERVISOR_EFFORT" ]] || PARENT_OPTS+=(--effort "$CEREBRO_SUPERVISOR_EFFORT")
  [[ -z "$CEREBRO_SUPERVISOR_MODEL" ]] || PARENT_OPTS+=(--model "$CEREBRO_SUPERVISOR_MODEL")
}
backend_claude_launch_orchestrator() {
  backend_claude_endpoint_env "$CEREBRO_SUPERVISOR_MODEL"
  backend_claude_parent_opts
  exec "$CEREBRO_CLAUDE_CMD" --session-id "$(basename "$1")" "${PARENT_OPTS[@]}"
}
backend_claude_resume_orchestrator() {
  backend_claude_endpoint_env "$CEREBRO_SUPERVISOR_MODEL"
  backend_claude_parent_opts
  exec "$CEREBRO_CLAUDE_CMD" --resume "$2" "${PARENT_OPTS[@]}"
}

# ----- pair mode (claude stream-json stdin) --------------------------------

# Pair mode adds a steering FIFO to the native stream-json child session.
backend_claude_pair_begin() {
  case "$1" in execute|review) ;; *) die "pair: unsupported role: $1" ;; esac
  PAIR_SID="${5:-$(mint_uuid)}"
  PAIR_FIFO="${4%.jsonl}.steer.fifo"
  PAIR_STEER="${4%.jsonl}.steering.md"
  : > "$PAIR_STEER"
  rm -f "$PAIR_FIFO"
  mkfifo "$PAIR_FIFO" || die "pair: cannot create steering pipe"
  pair_banner "$1" "$PAIR_SID" "$(pair_label "$1" "$2" "$3")" "$PAIR_FIFO"
}
backend_claude_pair_run() {
  local cwd="$1" prompt="$2" role="$3" resume="$4" child_log="$5" \
    msg_capture="$6" id_capture="$7" store_file="$8" ckey="$9" model="${10-$CEREBRO_MODEL}"
  backend_claude_child_run_opts "$role" "$resume" "$model"
  CHILD_RUN_OPTS+=(--input-format stream-json --replay-user-messages)
  [[ -n "$resume" ]] || CHILD_RUN_OPTS+=(--session-id "$PAIR_SID")
  printf '%s' "$prompt" | CEREBRO_CHILD_ROLE="$role" CEREBRO_PAIR_IDLE="$CEREBRO_PAIR_IDLE" \
    "${TIMEOUT_CMD[@]}" python3 "$CEREBRO_LIB_DIR/python/pair_process.py" claude "$cwd" "$resume" "$model" \
      "$PAIR_FIFO" "$PAIR_STEER" "$child_log" "$CEREBRO_CLAUDE_CMD" "${CHILD_RUN_OPTS[@]}" \
      2>"${child_log%.*}.err.log" | tee "$child_log" \
    | python3 "$CEREBRO_LIB_DIR/python/parse_stream.py" "$msg_capture" "$id_capture" "$store_file" "$ckey" claude
  local codes=("${PIPESTATUS[@]}")
  (( codes[1] == 0 )) || return "${codes[1]}"
  return "${codes[3]}"
}
backend_claude_pair_cleanup() {
  (( $1 )) || return 0
  [[ -z "${PAIR_FIFO:-}" ]] || rm -f "$PAIR_FIFO"
}

# Claude ACP pins a small native wrapper around the shared supervisor skill.

# Register up to four catalog IDs with Claude ACP; the proxy owns picker labels.
# HAIKU stays reserved for the configured housekeeping model.
claude_acp_catalog_env() {
  local cfg="$CEREBRO_HOME/models-config.json"
  [[ -r "$cfg" && -s "$cfg" ]] || { printf '%s\n' '{}'; return 0; }
  jq -e '.models' "$cfg" >/dev/null 2>&1 || { printf '%s\n' '{}'; return 0; }
  # Keep housekeeping on the configured task model.
  local -a slots=(OPUS SONNET FABLE)
  local slot_idx=0 entry id env_obj slot
  env_obj='{}'
  while IFS= read -r entry; do
    id="$(jq -r '.id // empty' <<<"$entry")"
    [[ -n "$id" ]] || continue
    if (( slot_idx < 3 )); then
      slot="${slots[$slot_idx]}"
      env_obj="$(jq -c --arg slot "$slot" --arg id "$id" \
          '. + {("ANTHROPIC_DEFAULT_" + $slot + "_MODEL"): $id}' \
        <<<"$env_obj")"
    elif (( slot_idx == 3 )); then
      env_obj="$(jq -c --arg id "$id" \
          '. + {"ANTHROPIC_CUSTOM_MODEL_OPTION": $id}' \
        <<<"$env_obj")"
    else
      break
    fi
    slot_idx=$((slot_idx + 1))
  done < <(jq -c '.models[]' "$cfg")
  printf '%s\n' "$env_obj"
}

# Pin Claude ACP to the shared supervisor wrapper and native endpoint configuration.
backend_claude_acp_child_spec() {
  local argv_bin
  if command -v claude-agent-acp >/dev/null 2>&1; then
    argv_bin='["claude-agent-acp"]'
  else
    argv_bin='["npx","-y","@agentclientprotocol/claude-agent-acp"]'
  fi
  backend_claude_capture_model_env
  local acp_model="$CEREBRO_SUPERVISOR_MODEL" env_json
  env_json="$(jq -n --arg model "$acp_model" \
    'if $model == "" then {} else {ANTHROPIC_MODEL:$model} end')"
  env_json="$(jq -c --arg native "$CEREBRO_CLAUDE_NATIVE_MODEL_ENV" '. + {CEREBRO_CLAUDE_NATIVE_MODEL_ENV:$native}' <<<"$env_json")"
  if [[ -n "$CEREBRO_CLAUDE_BASE_URL" ]]; then
    env_json="$(jq -n --argjson env "$env_json" --arg base "$CEREBRO_CLAUDE_BASE_URL" \
        --arg tok "${CEREBRO_CLAUDE_AUTH_TOKEN:-ollama}" \
        --arg model "$acp_model" \
        '$env + {ANTHROPIC_BASE_URL:$base, ANTHROPIC_AUTH_TOKEN:$tok, ANTHROPIC_API_KEY:null}
          + (if $model == "" then {} else {ANTHROPIC_DEFAULT_HAIKU_MODEL:$model} end)')"
    # Register model IDs; editor-facing labels remain owned by the proxy.
    env_json="$(jq -c --argjson cat "$(claude_acp_catalog_env)" \
        '. + $cat' <<<"$env_json")"
    # Propagate a declared custom-model context window to Claude ACP.
    local ctx; ctx="$(models_context_tokens "$acp_model")"
    if [[ -n "$ctx" ]]; then
      env_json="$(jq -c --arg ctx "$ctx" \
        '. + {CLAUDE_CODE_AUTO_COMPACT_WINDOW:$ctx}' <<<"$env_json")"
    elif [[ -n "$acp_model" && -n "${ANTHROPIC_MODEL:-}" && "$ANTHROPIC_MODEL" != "$acp_model" ]]; then
      env_json="$(jq -c '. + {CLAUDE_CODE_AUTO_COMPACT_WINDOW:null}' <<<"$env_json")"
    fi
  fi
  jq -n --argjson argv "$argv_bin" --arg cfg "agent" --arg val "cerebro-orchestrator" \
        --arg ccd "$CEREBRO_HOME/.claude" --argjson env "$env_json" \
    '{argv:$argv, pin:{config_id:$cfg, value:$val},
      env:($env + {CLAUDE_CONFIG_DIR:$ccd})}'
}
