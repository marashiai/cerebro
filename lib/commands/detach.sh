# cerebro lib: commands/detach
# subcommand: detach (launch a long-running cerebro child independently)
# Sourced by bin/cerebro; not meant to be executed directly.

# Resolve either allowed output root before a monitor or lost-job waiter writes.
detached_output_path() {
  [[ "$1" == /* ]] || die "detached output path must be absolute"
  local scratch="/tmp/cerebro-$CEREBRO_SESSION_ID" resolved
  resolved="$(resolve_in_repo "$CEREBRO_SESSION_DIR" "$1" 2>/dev/null)" \
    || resolved="$(resolve_in_repo "$scratch" "$1" 2>/dev/null)" \
    || die "detached output must be under $scratch or $CEREBRO_SESSION_DIR"
  printf '%s\n' "$resolved"
}

# ----- subcommand: cerebro detach --output <path> -- <subcommand> [...] -----
# Launch a long-running cerebro subcommand outside the calling agent harness's
# process group. Agent-tool background jobs have finite lifetimes; without this
# boundary, their cleanup can kill an otherwise healthy paired child.
cmd_detach() {
  require_session
  command -v python3 >/dev/null 2>&1 || die "detach: missing required command on PATH: python3"

  [[ "${1:-}" == "--output" && -n "${2:-}" ]] \
    || die "usage: cerebro detach --output <absolute-path> -- <subcommand> [...]"
  local output="$2"
  shift 2
  [[ "${1:-}" == "--" ]] || die "detach: expected -- before the subcommand"
  shift
  [[ $# -gt 0 ]] || die "detach: missing subcommand"
  [[ "$output" == /* ]] || die "detach: output path must be absolute"

  case "$1" in
    execute|answer) ;;
    *) die "detach: '$1' is not a long-running child subcommand" ;;
  esac

  local status pid_path
  output="$(detached_output_path "$output")" || return $?
  status="$(detached_output_path "$output.status")" || return $?
  pid_path="$(detached_output_path "$output.pid")" || return $?
  detached_output_path "$output.status.updates.json" >/dev/null || return $?

  if [[ -r "$output.status" && "$(cat "$output.status" 2>/dev/null)" == "running" \
        && -r "$output.pid" ]]; then
    local active_pid
    active_pid="$(cat "$output.pid" 2>/dev/null)"
    if [[ "$active_pid" =~ ^[0-9]+$ ]] && kill -0 "$active_pid" 2>/dev/null; then
      die "detach: output already belongs to running pid $active_pid: $output"
    fi
  fi

  local jobs_dir="$CEREBRO_SESSION_DIR/detached-jobs" job_id job_file
  jobs_dir="$(resolve_in_repo "$CEREBRO_SESSION_DIR" "$jobs_dir")" || return $?
  mkdir -p "$jobs_dir"
  job_id="$(mint_uuid)"
  job_file="$jobs_dir/$job_id.json"

  python3 "$CEREBRO_LIB_DIR/python/detach_process.py" \
    "$output" "$status" "$pid_path" "$job_file" "$job_id" "$1" \
    "$CEREBRO_LIB_DIR/../bin/cerebro" "$@"
}


# ----- subcommand: cerebro wait <detached-status-path> ----------------------
# Block until a detached monitor records a scope notice or its final exit code.
# --after acknowledges a handled notice before waiting for the next update.
# This command is
# safe to put in an agent harness's managed background mode: it owns no child,
# so harness cleanup can only kill the disposable waiter.
cmd_wait() {
  require_session
  command -v python3 >/dev/null 2>&1 || die "wait: missing required command on PATH: python3"
  [[ $# -ge 1 ]] || die "usage: cerebro wait <job-id|absolute-output.status> [--after <sequence>]"
  local target="$1" after=0 note="" disposition="" interrupt=""; shift
  while [[ $# -gt 0 ]]; do
    [[ "$1" == --interrupt ]] && { interrupt=1; shift; continue; }
    [[ $# -ge 2 && -n "$2" ]] || die "wait: missing value for $1"
    case "$1" in
      --after) after="${2:-}"; shift 2 ;;
      --note) note="${2:-}"; shift 2 ;;
      --disposition) disposition="${2:-}"; shift 2 ;;
      *) die "wait: unknown argument: $1" ;;
    esac
  done
  [[ "$after" =~ ^[0-9]+$ ]] || die "wait: --after must be a nonnegative sequence"
  [[ -z "$interrupt" || "$disposition" == correct ]] || die "wait: --interrupt applies only to --disposition correct"

  local status job_file=""
  if [[ "$target" == /* ]]; then
    status="$target"
  else
    [[ "$target" =~ ^[0-9a-fA-F-]+$ ]] || die "wait: invalid job id: $target"
    job_file="$CEREBRO_SESSION_DIR/detached-jobs/$target.json"
    job_file="$(resolve_in_repo "$CEREBRO_SESSION_DIR" "$job_file")" || return $?
    [[ -r "$job_file" ]] || die "wait: no such detached job: $target"
    status="$(jq -r '.status // empty' "$job_file")"
    [[ -n "$status" ]] || die "wait: malformed detached job: $target"
  fi
  [[ "$status" == *.status ]] || die "wait: path must end in .status"
  status="$(detached_output_path "$status")" || return $?
  local updates_path
  updates_path="$(detached_output_path "$status.updates.json")" || return $?
  detached_output_path "$updates_path.lock" >/dev/null || return $?
  local args=("$status" --after "$after") field value
  if [[ -n "$job_file" ]]; then
    for field in output result; do
      value="$(jq -r --arg field "$field" '.[$field] // empty' "$job_file")"
      [[ -n "$value" ]] || die "wait: malformed job $field"
      detached_output_path "$value" >/dev/null || return $?
    done
    args+=(--job-file "$job_file")
  fi

  [[ -z "$note$disposition" ]] || args+=(--note "$note" --disposition "$disposition")
  # A correct decision is delivered to the cited child before it is recorded,
  # so everything the record needs is validated first.
  if [[ -n "$note$disposition" ]]; then
    [[ -n "$note" && -n "$disposition" && -n "$job_file" ]] && (( after > 0 )) \
      || die "wait: a concern decision requires a job ID, --after, --disposition and --note"
  fi
  if [[ "$disposition" == correct ]]; then
    local pipe
    pipe="$(jq -r --argjson sequence "$after" \
      '.notices[] | select(.sequence == $sequence) | .notice.steering_pipe // empty' "$updates_path")"
    [[ -n "$pipe" ]] || die "wait: notice $after has no live child to correct; decide continue or stop and use a correction packet"
    cmd_steer ${interrupt:+--interrupt} "$pipe" "$note"
  fi
  python3 "$CEREBRO_LIB_DIR/python/wait_detached.py" \
    "${args[@]}"
}


# ----- subcommand: cerebro jobs --------------------------------------------
# List every detached job registered to this parent session, including jobs
# that completed while the interactive parent was not running.
cmd_jobs() {
  require_session
  local jobs_dir="$CEREBRO_SESSION_DIR/detached-jobs"
  python3 "$CEREBRO_LIB_DIR/python/detached_jobs.py" list "$jobs_dir"
}


# ----- subcommand: cerebro cancel <job-id> ---------------------------------
# Stop a detached job and its full descendant tree. Descendant discovery is
# PID-based rather than process-group-only because paired Claude children create
# their own process group for targeted stall cleanup.
cmd_cancel() {
  require_session
  [[ $# -eq 1 && "$1" =~ ^[0-9a-fA-F-]+$ ]] \
    || die "usage: cerebro cancel <detached-job-id>"
  local job_file="$CEREBRO_SESSION_DIR/detached-jobs/$1.json"
  job_file="$(resolve_in_repo "$CEREBRO_SESSION_DIR" "$job_file")" || return $?
  [[ -r "$job_file" ]] || die "cancel: no such detached job: $1"
  python3 "$CEREBRO_LIB_DIR/python/detached_jobs.py" cancel "$job_file"
}
