# Answer a terminal question in the same native task stage.
cmd_answer() {
  require_session
  [[ $# -eq 2 && -n "$2" ]] || die 'usage: cerebro answer <task-id> "<answer>"'
  cmd_execute --resume "$1" --answer "$2"
}
