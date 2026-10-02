# cerebro lib: commands/answer
# subcommand: answer (resume a paused child with an answer to its question)
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- subcommand: cerebro answer <child-session-id> "<answer>" -------------
# A child (execute / apply-review / doc-write) runs non-interactively, so
# when it hits a genuine blocker it ends with its QUESTION as its closing
# message instead of finishing the work. `cerebro answer` resumes that exact
# child session and delivers the orchestrator's answer as the child's next
# turn, so it continues where it paused instead of redoing work.
cmd_answer() {
  require_session
  build_timeout_cmd

  local child_id="${1:-}"; shift || true
  local answer=""
  local model=""
  # Second positional, if present and not a flag, is the answer text.
  if [[ $# -gt 0 && "${1:-}" != --* ]]; then
    answer="$1"; shift
  fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model) shift; model="${1:-}"; shift || true ;;
      *) die "answer: unknown arg: $1" ;;
    esac
  done

  [[ -n "$child_id" ]] || die "usage: cerebro answer <child-session-id> \"<answer>\" [--model <provider/model>]"
  [[ -n "$answer" ]] || die "answer: empty answer (pass the answer text as the second argument)"

  local rows n
  rows="$(child_session_find_id "$child_id")"
  n="$(printf '%s' "$rows" | grep -c .)"
  if (( n == 0 )); then
    die "answer: no fresh child session $child_id in this cerebro session"
  elif (( n > 1 )); then
    die "answer: child session $child_id matches several child records in this cerebro session; cannot resume safely"
  fi
  local ckey prior provider role repo label status updated log
  IFS=$'\t' read -r ckey prior provider role repo label status updated log <<<"$rows"
  case "$provider:$role" in
    "$(backend_answerable_pattern execute)"|\
    "$(backend_answerable_pattern apply-review)"|\
    "$(backend_answerable_pattern doc-write)") ;;
    *) die "answer: child session $child_id is not an answerable $(current_backend) child (provider=$provider role=$role)" ;;
  esac
  [[ -d "$repo" ]] || die "answer: stored child repo is missing: $repo"

  local agent; agent="$(backend_child_agent_name "$role")"
  local child_log; child_log="$(child_log_path "answer-$role")"
  local store_file; store_file="$(child_sessions_file)"

  local child_prompt
  child_prompt="$(printf 'The cerebro orchestrator is answering the question you raised when you paused. Use this answer and CONTINUE the task from where you stopped -- do not restart work you have already completed. If you hit another genuine blocker, pause again the same way (end with a single clear question).\n\n<answer>\n%s\n</answer>\n' "$answer")"

  say "cerebro: answering $role child session $prior in $repo"
  log_event "answer_started" "role=$role repo=$repo child=$ckey resume=$prior"

  # The roles push commits rather than producing a file, so we capture only
  # the child's closing message to surface (it may complete, or pause again
  # with a further question).
  local rc msg_capture id_capture
  msg_capture="$(mktemp)"; id_capture="$(mktemp)"

  child_store_begin "$ckey" "$provider" "$role" "$repo" "$label" "$child_log" preserve-id
  child_run 0 "$repo" "$child_prompt" "$agent" "$prior" \
    "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
  rc=$?
  rm -f "$id_capture"

  if (( rc != 0 )); then
    rm -f "$msg_capture"
    log_event "answer_failed" "rc=$rc role=$role resume=$prior log=$child_log"
    die "answer: resuming the $role child failed (rc=$rc); see $child_log"
  fi

  child_store_done "$ckey"
  log_event "answer_finished" "role=$role log=$child_log"
  surface_child_reply "$msg_capture" "$role" "$prior"
  rm -f "$msg_capture"
  echo "$child_log"
}