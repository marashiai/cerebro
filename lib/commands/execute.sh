# One task packet drives implementation and an independent review.
cmd_execute() {
  require_session
  local key
  for key in $(compgen -v CEREBRO_); do export "$key"; done
  exec python3 "$CEREBRO_LIB_DIR/python/task_lifecycle.py" "$@"
}

# The controller owns stage selection and durable output paths. Native IDs
# remain in child-sessions.json so interrupted turns resume the same child.
cmd_task_stage() {
  require_session
  build_timeout_cmd
  local task_dir="$1" role="$2" prompt_file="$3" prior="$4" model="$5" effort="$6" requested_log="$7"
  local repo branch ckey child_log msg_capture id_capture pair=1
  repo="$(jq -r '.workspace.path' "$task_dir/task.json")"
  branch="$(jq -r '.workspace.branch' "$task_dir/task.json")"
  ckey="$(basename "$task_dir")-$role"
  child_log="$requested_log"
  msg_capture="${child_log%.jsonl}.reply"
  id_capture="${child_log%.jsonl}.native-id"
  local CEREBRO_CHILD_EFFORT="$effort" CEREBRO_PAIR_IDLE=0 CEREBRO_PAIR_STALL=0 CEREBRO_PAIR_STALL_BUSY=0
  local CEREBRO_TASK_FILE="$task_dir/task.json"
  export CEREBRO_CHILD_EFFORT CEREBRO_TASK_FILE CEREBRO_PAIR_STALL CEREBRO_PAIR_STALL_BUSY
  watch_prepare || return $?
  local PAIR_SID="" PAIR_FIFO="" PAIR_STEER=""
  pair_begin "$role" "$repo" "$branch" "$child_log" "$prior"
  child_store_begin "$ckey" "$(current_backend)" "$role" "$repo" "$branch" "$child_log" "${prior:+preserve-id}" || return $?
  child_run 1 "$repo" "$(cat "$prompt_file")" "$role" "$prior" \
    "$child_log" "$msg_capture" "$id_capture" "$(child_sessions_file)" "$ckey" "$model"
  local rc=$?
  pair_cleanup 1
  if pair_restarted "$child_log"; then
    child_store retire "$ckey" "$(ts_iso)" || return $?
    printf 'Restart requested: %s\n' "$(pair_restart_read "$child_log")" >&2
    rc=3
  fi
  (( rc == 0 )) || child_fail_stderr "$child_log"
  printf '%s\n' "$rc" > "${child_log%.jsonl}.exit.tmp"
  mv "${child_log%.jsonl}.exit.tmp" "${child_log%.jsonl}.exit" || return $?
  log_event "task_stage_finished" "task=$(basename "$task_dir") role=$role rc=$rc log=$child_log"
  return "$rc"
}
