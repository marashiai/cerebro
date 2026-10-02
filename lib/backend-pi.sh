# Pi children use the native RPC transport, with optional live steering.
backend_pi_materialise_extras() { :; }

backend_pi_detect_version() {
  local version
  version="$("$CEREBRO_PI_CMD" --version 2>/dev/null)" || die "cannot determine Pi version"
  [[ "$version" =~ ([0-9]+)\.([0-9]+)\.([0-9]+) ]] || die "cannot parse Pi version: $version"
  (( BASH_REMATCH[1] > 0 || BASH_REMATCH[2] > 99 || (BASH_REMATCH[2] == 99 && BASH_REMATCH[3] >= 2) )) \
    || die "Pi requires version 0.99.2 or newer (found $version)"
}

backend_pi_launch_orchestrator() {
  backend_supervisor_config supervisor >/dev/null
  exec python3 "$CEREBRO_LIB_DIR/python/pi_launch.py" \
    "$CEREBRO_PI_CMD" supervisor "$CEREBRO_HOME" "$1" "" "$CEREBRO_SUPERVISOR_MODEL"
}
backend_pi_resume_orchestrator() {
  [[ -n "$2" ]] || die "resume: Pi session has no recorded native session file"
  backend_supervisor_config supervisor >/dev/null
  exec python3 "$CEREBRO_LIB_DIR/python/pi_launch.py" \
    "$CEREBRO_PI_CMD" supervisor "$CEREBRO_HOME" "$1" "$2" "$CEREBRO_SUPERVISOR_MODEL"
}

backend_pi_child_run() {
  local pair="$1" cwd="$2" prompt="$3" role="$4" resume="$5" \
    child_log="$6" msg_capture="$7" id_capture="$8" store_file="$9" ckey="${10}" model="${11:-$CEREBRO_MODEL}"
  playwright_isolate_child
  if (( ! pair )); then
    local PAIR_FIFO="" PAIR_STEER="" CEREBRO_PAIR_IDLE=0 CEREBRO_JEV_ENABLED=0
  fi
  backend_pi_pair_run "$cwd" "$prompt" "$role" "$resume" \
    "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
}

backend_pi_pair_begin() {
  case "$1" in execute|apply-review|doc-write) ;; *) die "pair: unsupported role: $1" ;; esac
  PAIR_SID="${5:-}"
  PAIR_FIFO="${4%.jsonl}.steer.fifo"
  PAIR_STEER="${4%.jsonl}.steering.md"
  : > "$PAIR_STEER"
  rm -f "$PAIR_FIFO"
  mkfifo "$PAIR_FIFO" || die "pair: cannot create steering pipe"
  pair_banner "$1" "${PAIR_SID:-pending}" "$(pair_label "$1" "$2" "$3")" "$PAIR_FIFO"
}
backend_pi_pair_run() {
  local cwd="$1" prompt="$2" role="$3" resume="$4" child_log="$5" \
    msg_capture="$6" id_capture="$7" store_file="$8" ckey="$9" model="${10:-$CEREBRO_MODEL}"
  case "$role" in review|audit|improve) backend_supervisor_config reviewer >/dev/null ;; esac
  local instructions; instructions="$(child_sys_prompt "$role")"
  printf '%s' "$prompt" | CEREBRO_CHILD_ROLE="$role" CEREBRO_CHILD_INSTRUCTIONS="$instructions" \
    CEREBRO_PAIR_IDLE="$CEREBRO_PAIR_IDLE" "${TIMEOUT_CMD[@]}" python3 "$CEREBRO_LIB_DIR/python/pair_process.py" \
      pi "$cwd" "$resume" "$model" "$PAIR_FIFO" "$PAIR_STEER" "$child_log" "$CEREBRO_PI_CMD" \
      2>"${child_log%.*}.err.log" | tee "$child_log" \
    | python3 "$CEREBRO_LIB_DIR/python/parse_stream.py" "$msg_capture" "$id_capture" "$store_file" "$ckey" pi
  local codes=("${PIPESTATUS[@]}")
  [[ ! -s "$id_capture" ]] || PAIR_SID="$(cat "$id_capture")"
  (( codes[1] == 0 )) || return "${codes[1]}"
  return "${codes[3]}"
}
backend_pi_pair_cleanup() {
  (( $1 )) || return 0
  [[ -z "${PAIR_FIFO:-}" ]] || rm -f "$PAIR_FIFO"
}
backend_pi_acp_child_spec() {
  die "Pi has no native ACP endpoint; use the native Cerebro terminal session"
}
