# OpenCode V2: native sessions, shared skills and the served paired transport.
backend_opencode_detect_version() {
  [[ -n "${CEREBRO_OPENCODE_CHECKED:-}" ]] && return 0
  local version
  version="$("$CEREBRO_OPENCODE_CMD" --version)" || die "cannot determine OpenCode version"
  [[ "$version" =~ (^|[[:space:]])v?2\.[0-9]+\.[0-9]+ ]] \
    || die "OpenCode V2 is required (found $version)"
  if [[ "$version" =~ (^|[[:space:]])v?2\.0\.([0-9]+) ]] && (( BASH_REMATCH[2] < 19 )); then
    die "OpenCode 2.0.19 or newer is required for native session permissions"
  fi
  CEREBRO_OPENCODE_CHECKED=1
}

backend_opencode_prepare() {
  backend_opencode_detect_version
  materialise_home
  case "${1:-}" in
    supervisor|observer) backend_supervisor_config "$1" >/dev/null ;;
    review|audit|improve) backend_supervisor_config reviewer >/dev/null ;;
  esac
  local overlay
  overlay="$(python3 "$CEREBRO_LIB_DIR/python/opencode_config.py" \
    "$CEREBRO_HOME" "${1:-execute}" "${2:-}")" || die "cannot build OpenCode configuration"
  export OPENCODE_CONFIG_CONTENT="$overlay"
  case "${1:-}" in
    supervisor|observer)
      "$CEREBRO_OPENCODE_CMD" api --standalone POST /api/rpc/cerebro/ready --data '{"input":{}}' \
        | jq -e '.output == true' >/dev/null \
        || die "OpenCode did not activate the required Cerebro role gate" ;;
  esac
}

backend_opencode_materialise_extras() {
  mkdir -p "$CEREBRO_HOME/opencode-plugin"
  write_if_changed "$CEREBRO_HOME/opencode-plugin/index.js" "$(cerebro_plugin_js)"
  write_if_changed "$CEREBRO_HOME/opencode-plugin/package.json" \
    "$(cat "$(cerebro_payloads_dir)/plugin/package.json")"
}

backend_opencode_child_run() {
  local pair="$1" cwd="$2" prompt="$3" role="$4" resume="$5" \
    child_log="$6" msg_capture="$7" id_capture="$8" store_file="$9" ckey="${10}"
  local model="${11:-$CEREBRO_MODEL}"
  playwright_isolate_child
  if (( ! pair )); then
    local PAIR_ENABLED=0
    backend_opencode_pair_begin "$role" "$cwd" "" "$child_log" "$resume" "$model"
  fi
  backend_opencode_pair_run "$cwd" "$prompt" "$role" "$resume" \
    "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
  local rc=$?
  (( pair )) || backend_opencode_pair_cleanup 1
  return "$rc"
}

backend_opencode_launch_orchestrator() {
  backend_opencode_prepare supervisor "$CEREBRO_MODEL"
  exec "$CEREBRO_OPENCODE_CMD" --standalone
}

backend_opencode_launch_observer() {
  local target="$3" prompt_opt=()
  backend_opencode_prepare observer "$CEREBRO_MODEL"
  [[ -z "$target" ]] || prompt_opt=(--prompt "Observe session $target. Follow the cerebro-observer skill.")
  exec "$CEREBRO_OPENCODE_CMD" --standalone "${prompt_opt[@]}"
}

backend_opencode_resume_orchestrator() {
  local id="$2" session_opt=() role
  role="$(jq -r '.role // "supervisor"' "$1/metadata.json")"
  backend_opencode_prepare "$role" "${6:-$CEREBRO_MODEL}"
  [[ -z "$id" ]] || session_opt=(--session "$id")
  exec "$CEREBRO_OPENCODE_CMD" --standalone "${session_opt[@]}"
}

backend_opencode_pair_begin() {
  local role="$1" repo="$2" branch="$3" child_log="$4" resume="${5:-}"
  backend_opencode_prepare "$role" "${6:-$CEREBRO_MODEL}"
  playwright_isolate_child
  PAIR_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  PAIR_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
  PAIR_BASE_URL="http://127.0.0.1:$PAIR_PORT"
  ( cd "$repo" && exec env -u CEREBRO_SESSION_ID -u CEREBRO_SESSION_DIR -u CEREBRO_ROLE \
      OPENCODE_PASSWORD="$PAIR_PASSWORD" "$CEREBRO_OPENCODE_CMD" serve \
      --port "$PAIR_PORT" --hostname 127.0.0.1 ) >/dev/null 2>&1 &
  PAIR_SERVE_PID=$!
  if ! OPENCODE_PASSWORD="$PAIR_PASSWORD" python3 "$CEREBRO_LIB_DIR/python/serve_ctl.py" health "$PAIR_BASE_URL"; then
    backend_opencode_pair_cleanup 1
    die "pair: OpenCode serve failed to start"
  fi
  if ! OPENCODE_PASSWORD="$PAIR_PASSWORD" python3 "$CEREBRO_LIB_DIR/python/serve_ctl.py" gate "$PAIR_BASE_URL"; then
    backend_opencode_pair_cleanup 1
    die "OpenCode did not activate the required Cerebro role gate"
  fi
  PAIR_SID="$resume"
  if [[ -z "$PAIR_SID" ]]; then
    PAIR_SID="$(OPENCODE_PASSWORD="$PAIR_PASSWORD" python3 "$CEREBRO_LIB_DIR/python/serve_ctl.py" \
      create "$PAIR_BASE_URL" "$(pair_label "$role" "$repo" "$branch")" "$repo")" || {
        backend_opencode_pair_cleanup 1; die "pair: could not create OpenCode session";
      }
  fi
  PAIR_STEER="${child_log%.jsonl}.steering.md"
  PAIR_FIFO="${child_log%.jsonl}.steer.fifo"
  PAIR_IDLE="$CEREBRO_PAIR_IDLE"
  PAIR_STALL="$CEREBRO_PAIR_STALL"
  PAIR_STALL_BUSY="$CEREBRO_PAIR_STALL_BUSY"
  if (( ${PAIR_ENABLED:-1} )); then
    : > "$PAIR_STEER"
    rm -f "$PAIR_FIFO"
    mkfifo "$PAIR_FIFO" || die "pair: cannot create steering pipe"
    pair_banner "$role" "$PAIR_SID" "$(pair_label "$role" "$repo" "$branch")" "$PAIR_FIFO"
  else
    PAIR_FIFO=""
    PAIR_IDLE=0
  fi
}

backend_opencode_pair_run() {
  local cwd="$1" prompt="$2" role="$3" resume="$4" child_log="$5" \
    msg_capture="$6" id_capture="$7" store_file="$8" ckey="$9" model="${10:-$CEREBRO_MODEL}"
  printf '%s' "$prompt" | OPENCODE_PASSWORD="$PAIR_PASSWORD" \
    "${TIMEOUT_CMD[@]}" python3 "$CEREBRO_LIB_DIR/python/pair_pump_opencode.py" \
      "$PAIR_BASE_URL" "$PAIR_SID" general "$model" "$PAIR_FIFO" "$PAIR_STEER" "$child_log" \
      "$PAIR_IDLE" "$PAIR_STALL" "$PAIR_STALL_BUSY" 2>"${child_log%.*}.err.log" \
    | tee "$child_log" | python3 "$CEREBRO_LIB_DIR/python/parse_stream.py" \
      "$msg_capture" "$id_capture" "$store_file" "$ckey" opencode
  local codes=("${PIPESTATUS[@]}")
  (( codes[1] == 0 )) || return "${codes[1]}"
  return "${codes[3]}"
}

backend_opencode_pair_cleanup() {
  (( $1 )) || return 0
  if [[ -n "${PAIR_SERVE_PID:-}" ]]; then
    kill "$PAIR_SERVE_PID" 2>/dev/null || true
    wait "$PAIR_SERVE_PID" 2>/dev/null || true
  fi
  [[ -z "${PAIR_FIFO:-}" ]] || rm -f "$PAIR_FIFO"
  PAIR_SERVE_PID=""
}

backend_opencode_acp_child_spec() {
  # The proxy has not minted a session yet. Its child launcher receives the
  # binding before constructing the session-specific MCP environment.
  jq -n --arg cmd "$CEREBRO_LIB_DIR/../bin/cerebro" \
    '{argv:[$cmd,"acp","opencode-child"],pin:{config_id:"mode",value:"build"},env:{}}'
}

backend_opencode_launch_acp() {
  require_session
  backend_opencode_prepare supervisor "$CEREBRO_MODEL"
  exec "$CEREBRO_OPENCODE_CMD" acp
}
