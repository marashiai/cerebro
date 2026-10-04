# cerebro lib: helpers
# small shared helpers: say/warn/die, error codes, path + repo resolution, usage
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- helpers --------------------------------------------------------------

say()  { printf '==> %s\n' "$*" >&2; }
warn() { printf 'cerebro: warning: %s\n' "$*" >&2; }
die()  { printf 'cerebro: error: %s\n' "$*" >&2; exit 1; }
dbg()  { [[ "$CEREBRO_DEBUG" == "1" ]] && printf 'cerebro: debug: %s\n' "$*" >&2; return 0; }

# Surface native child stderr from the sibling .err.log file on failure.
child_fail_stderr() {
  local child_log="$1"
  local err_log="${child_log%.*}.err.log"
  [[ -s "$err_log" ]] || return 0
  local tail_out
  tail_out="$(tail -n 15 "$err_log" 2>/dev/null)"
  [[ -n "$tail_out" ]] || return 0
  warn "child stderr (tail of $err_log):"
  printf '%s\n' "$tail_out" | sed 's/^/    /' >&2
}

resolve_in_repo() {
  python3 "$CEREBRO_LIB_DIR/python/resolve_in_repo.py" "$1" "$2"
}

usage() {
  cat <<'EOF'
usage:
  cerebro                         start a native supervisor
  cerebro --resume [<session-id>]  reopen its native conversation
  cerebro execute [--packet file] read one JSON task packet (stdin by default)
  cerebro execute --resume <task-id> [--answer "text"]
  cerebro answer <task-id> "text"  continue a terminal question
  cerebro steer [--interrupt] [<pipe>] "text"    steer a running stage
  cerebro restart [<pipe>] "text"  retire its conversation, retaining work
  cerebro jobs | status | list     inspect durable session state
  cerebro wait <job-id> [--after N --disposition continue|correct|stop --note "text" [--interrupt]]
  cerebro cancel <job-id>          stop that job and its descendants
  cerebro models | model-env       inspect native model settings
  cerebro worktrees | recall       inspect retained work and session history
  cerebro acp [restart]            Claude editor frontend

A task packet contains goal, task, acceptance, repo (absolute), and base (Git
reference). Optional models.implementor/reviewer override per-role model/effort.
Omitted models and efforts use native backend defaults. Implementation and tests
run first; independent review starts automatically after a complete structured implementation handoff.
Questions, blocked work, unfinished handoffs and failures stop for adjudication.
Native tools, authentication and configuration remain available in every role.
EOF
}

# Top-level chats require a real TTY; session-bound commands may use stdio MCP.
require_interactive() {
  [[ -n "${CEREBRO_SESSION_ID:-}" ]] && return 0
  if [[ ! -t 0 || ! -t 1 ]]; then
    die "cerebro is interactive-only; stdin and stdout must be terminals"
  fi
}

require_deps() {
  local cmd backend; backend="$(current_backend)"
  case "$backend" in
    pi) cmd="$CEREBRO_PI_CMD" ;;
    claude) cmd="$CEREBRO_CLAUDE_CMD" ;;
    codex) cmd="$CEREBRO_CODEX_CMD" ;;
    *) die "unsupported backend: $backend (choose pi, claude or codex)" ;;
  esac
  local dep
  for dep in jq python3 "$cmd"; do
    command -v "$dep" >/dev/null 2>&1 || die "missing required command on PATH: $dep"
  done
  [[ "$backend" != pi ]] || backend_pi_detect_version
}

# Bind commands to the session environment and restore its recorded backend.
require_session() {
  [[ -n "${CEREBRO_SESSION_ID:-}" ]] || {
    # The Claude prompt hook records the active session for external commands.
    if [[ -L "$CEREBRO_HOME/current-session" ]]; then
      local target
      target="$(readlink "$CEREBRO_HOME/current-session")"
      target="${target##*/}"
      [[ -n "$target" ]] && export CEREBRO_SESSION_ID="$target"
    fi
  }
  [[ -n "${CEREBRO_SESSION_ID:-}" ]] || die "no current cerebro session (CEREBRO_SESSION_ID unset and no current-session symlink). Did you launch this from a \`cerebro\` shell?"
  CEREBRO_SESSION_DIR="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID"
  [[ -d "$CEREBRO_SESSION_DIR" ]] || die "session dir missing: $CEREBRO_SESSION_DIR"
  export CEREBRO_SESSION_DIR
  # The recorded session backend governs every delegated child.
  CEREBRO_RESUME_BACKEND="$(session_backend "$CEREBRO_SESSION_DIR")" || return $?
  export CEREBRO_RESUME_BACKEND
}

mint_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr 'A-Z' 'a-z'
  else
    python3 -c 'import uuid; print(uuid.uuid4())'
  fi
}

ts_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
ts_compact() { date -u +%Y%m%dT%H%M%SZ; }

# Append a structured event to the active session's transcript.
log_event() {
  local what="$1"; shift || true
  local extra="${1:-}"
  [[ -z "${CEREBRO_SESSION_DIR:-}" ]] && return 0
  local file="$CEREBRO_SESSION_DIR/transcript.jsonl"
  jq -nc --arg ts "$(ts_iso)" --arg what "$what" --arg extra "$extra" \
    '{kind:"event", ts:$ts, what:$what} + (if $extra == "" then {} else {detail:$extra} end)' \
    >> "$file" 2>/dev/null || true
}

# Separate browser profiles prevent concurrent native children sharing a lock.
playwright_isolate_child() {
  [[ "${CEREBRO_PLAYWRIGHT_ISOLATED:-1}" != "0" ]] && export PLAYWRIGHT_MCP_ISOLATED=1
}

# A configured wall-clock limit wraps child work; zero leaves it uncapped.
build_timeout_cmd() {
  case "${CEREBRO_TIMEOUT:-0}" in
    ''|0|none|unlimited|NONE|UNLIMITED)
      TIMEOUT_CMD=(env)
      return 0
      ;;
  esac
  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD=(timeout "$CEREBRO_TIMEOUT")
  elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD=(gtimeout "$CEREBRO_TIMEOUT")
  elif command -v perl >/dev/null 2>&1; then
    TIMEOUT_CMD=(perl -e 'alarm shift; exec @ARGV' "$CEREBRO_TIMEOUT")
  else
    TIMEOUT_CMD=(env)
  fi
}

# Materialize the supervisor context without loading development ceremonies.
materialise_home() {
  mkdir -p "$CEREBRO_HOME/sessions" "$CEREBRO_HOME/.claude" || die "cannot create $CEREBRO_HOME"
  python3 "$CEREBRO_LIB_DIR/python/retire_skills.py" "$CEREBRO_HOME" || return $?
  write_if_changed "$CEREBRO_HOME/system-prompt.md" "$(cerebro_system_prompt)"
  backend_materialise_extras
}

write_if_changed() {
  local path="$1" content="$2"
  if [[ -f "$path" ]] && [[ "$(cat "$path")" == "$content" ]]; then
    return 0
  fi
  printf '%s' "$content" > "$path"
}
