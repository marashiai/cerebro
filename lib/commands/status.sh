# Summarize durable tasks and native child records without polling.
cmd_status() {
  require_session
  printf 'session: %s\ndir: %s\n' "$CEREBRO_SESSION_ID" "$CEREBRO_SESSION_DIR"
  local task
  shopt -s nullglob
  for task in "$CEREBRO_SESSION_DIR"/tasks/*/task.json; do
    jq -r --arg id "$(basename "$(dirname "$task")")" \
      '"task: \($id) stage=\(.stage) status=\(.status) workspace=\(.workspace.path)\n  resume: cerebro execute --resume \($id)"' "$task"
  done
  shopt -u nullglob
  python3 "$CEREBRO_LIB_DIR/python/detached_jobs.py" list "$CEREBRO_SESSION_DIR/detached-jobs"
}
