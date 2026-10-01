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

# Bridge exit codes and diagnostics are documented in cerebro-commands.
err_usage()  { printf 'cerebro: error: %s\n' "$*" >&2; exit 2; }
err_path()   { printf 'cerebro: error: %s\n' "$*" >&2; exit 3; }
err_subcmd() { printf 'cerebro: error: %s\n' "$*" >&2; exit 4; }
err_flag()   { printf 'cerebro: error: %s\n' "$*" >&2; exit 5; }
err_escape() { printf 'cerebro: error: %s\n' "$*" >&2; exit 6; }

# Exploration misses are successful empty results unless --strict-missing is set.
# $1 strict (0|1), $2 stdout marker, $3 strict-mode diagnostic.
missing_target() {
  local strict="$1" marker="$2" msg="$3"
  if [[ "$strict" == "1" ]]; then
    printf 'cerebro: error: %s\n' "$msg" >&2
    exit 3
  fi
  printf '%s\n' "$marker"
  exit 0
}

# True if $1 is among the remaining args.
contains() {
  local needle="$1"; shift
  local hay
  for hay in "$@"; do [[ "$hay" == "$needle" ]] && return 0; done
  return 1
}

# Resolve path $2 relative to repo $1, requiring the result to stay inside
# the repo. Echoes the absolute resolved path on stdout. Exits 6 if the path
# escapes. Uses python3 for cross-platform realpath (macOS lacks GNU realpath).
resolve_in_repo() {
  python3 "$CEREBRO_LIB_DIR/python/resolve_in_repo.py" "$1" "$2"
}

# Validate that $1 is an absolute path to a git repo. Exits 3 on any failure.
require_git_repo() {
  local repo="$1"
  [[ "$repo" = /* ]] || err_path "repo path must be absolute: $repo"
  [[ -d "$repo" ]]   || err_path "repo not a directory: $repo"
  git --no-optional-locks -C "$repo" rev-parse --git-dir >/dev/null 2>&1 \
    || err_path "not a git repo: $repo"
}

# Canonicalize $1 to its git worktree root and echo it on stdout. Exits 3
# if $1 is not an absolute directory inside a git worktree. Bridges that
# read files from the user's repo (read/grep/ls) call this to anchor
# subsequent path resolution to the worktree boundary instead of trusting
# an arbitrary absolute directory.
canonical_worktree_root() {
  local repo="$1"
  [[ "$repo" = /* ]] || err_path "repo path must be absolute: $repo"
  [[ -d "$repo" ]]   || err_path "repo not a directory: $repo"
  local root
  root="$(git --no-optional-locks -C "$repo" rev-parse --show-toplevel 2>/dev/null)" \
    || err_path "not a git worktree: $repo"
  [[ -n "$root" ]] || err_path "not a git worktree: $repo"
  printf '%s\n' "$root"
}

# Echo the per-repo state key (sha1 of canonical worktree root, 16 hex).
# Returns non-zero (and prints nothing) when $1 is not a git worktree, so
# callers can treat a non-repo argument as "no key".
repo_state_key() {
  local canonical
  canonical="$(git --no-optional-locks -C "$1" rev-parse --show-toplevel 2>/dev/null)" || return 1
  [[ -n "$canonical" ]] || return 1
  python3 -c 'import hashlib,sys; print(hashlib.sha1(sys.argv[1].encode()).hexdigest()[:16])' "$canonical"
}

# Walk upward from an absolute path looking for an enclosing git worktree
# (a directory containing a `.git` file or directory). Echoes the worktree
# root on stdout if found; returns non-zero (and prints nothing) otherwise.
# Bounded to 12 levels so we don't traverse the entire filesystem.
find_enclosing_worktree() {
  local p="$1"
  [[ "$p" = /* ]] || return 1
  python3 "$CEREBRO_LIB_DIR/python/find_enclosing_worktree.py" "$p" 2>/dev/null
}

# Resolve $1 (an absolute path) for a bare-abs read/grep/ls invocation.
# Echoes the realpathed result on stdout. Exit codes: 3 if not absolute
# (internal misuse only); 6 if it resolves under /dev /proc /sys (special
# filesystems / blocking devices) -- a security refusal callers MUST keep
# hard; 7 for a benign missing path or wrong type (no regular file or
# directory: FIFO, socket, char/block device) which callers may translate
# into a successful empty result. The in-repo escape guard in
# resolve_in_repo() does NOT apply -- this branch deliberately reads
# outside any repo.
resolve_bare_abs() {
  python3 "$CEREBRO_LIB_DIR/python/resolve_bare_abs.py" "$1"
}

# Map common short rg --type aliases to the canonical rg type name. Unknown
# inputs are passed through verbatim so rg emits its own diagnostic.
canonicalise_rg_type() {
  case "$1" in
    rs)  printf 'rust\n' ;;
    tsx) printf 'ts\n' ;;
    jsx) printf 'js\n' ;;
    yml) printf 'yaml\n' ;;
    rb)  printf 'ruby\n' ;;
    kt)  printf 'kotlin\n' ;;
    *)   printf '%s\n' "$1" ;;
  esac
}

usage() {
  cat <<'EOF'
usage:
  cerebro                       # start a native supervisor session
  cerebro --resume [<id>]        # resume an ID, or the most recent session
  cerebro --observe [<id>]       # observe another session's live paired children
  cerebro list                   # list sessions
  cerebro jobs                   # rediscover durable child jobs
  cerebro wait <job-id>          # block on a job completion notification
  cerebro cancel <job-id>        # stop an authorized job and its descendants
  cerebro detach --output <path> -- <child-subcommand> [...]
  cerebro acp [restart]          # editor frontend for OpenCode or Claude
  cerebro --help

CEREBRO_BACKEND selects opencode (default), codex or claude. OpenCode requires
V2, minimum 2.0.19; V1 is unsupported. A session and all its children, including
reviews, use one backend. Resume restores the recorded backend.

The parent supervises requirements, plans, delegation and delivery gates.
Repository development happens in native children, with execute work isolated
in a task worktree. Guarded Cerebro MCP commands accept literal argv and stdin;
the parent has no unrestricted mutation tools. Review uses fresh read-only
context on the same backend, optionally with another CEREBRO_REVIEW_MODEL.
Empty model settings use the backend's native default; --model overrides one
child call. The optional models-config.json catalog helps select models.

Long child commands detach automatically through the command tool and survive
parent disconnects. Their monitors notify waiters on completion; no child-state
or log polling is required to wait. A blocked child ends with a question;
answer <child-id> <answer> resumes that same conversation. A failed resume is
reported instead of silently creating a fresh child.

Pair execute/apply-review/doc-write for live observation and steering. A separate
observer reads log batches and compares them with the approved spec and plan.
Preauthorized autosteering may correct drift. Restart additionally needs
permission to abandon and remove the task's isolated branch, PR and worktree.
Observer/supervisor steering cannot add user requirements. Paired children have
a short post-turn steering window and bounded native inactivity handling.

Interactive chats require a real TTY on stdin/stdout; controllers using a real
PTY work. Session-bound commands are non-interactive. Cerebro does not serialize
competing mutations against the same repository; sequence them.

Requirements: jq, python3 and the selected native CLI on PATH. Development/PR
work additionally needs git and gh; browser verification needs native browser
capability. ACP is unavailable for Codex; its terminal frontend is supported.

Options use env > $CEREBRO_HOME/config.json > default. CEREBRO_HOME is env-only.
Supported options include CEREBRO_BACKEND, CEREBRO_MODEL, CEREBRO_REVIEW_MODEL,
CEREBRO_TIMEOUT, CEREBRO_CHILD_IDLE_TIMEOUT (default 0), CEREBRO_CHILD_SESSION_TTL,
CEREBRO_OPENCODE_CMD, CEREBRO_CODEX_CMD, CEREBRO_CLAUDE_CMD,
CEREBRO_CLAUDE_BASE_URL, CEREBRO_CLAUDE_AUTH_TOKEN, CEREBRO_OVERLAY_CAP,
CEREBRO_META_HORIZON, CEREBRO_PAIR_IDLE, CEREBRO_PAIR_STALL,
CEREBRO_PAIR_STALL_BUSY, CEREBRO_PAIR_STALL_RETRIES, CEREBRO_PAIR_STALL_BACKOFF,
CEREBRO_DEBUG. See docs/USAGE.md for the full table and workflows.
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
    opencode) cmd="$CEREBRO_OPENCODE_CMD" ;;
    claude) cmd="$CEREBRO_CLAUDE_CMD" ;;
    codex) cmd="$CEREBRO_CODEX_CMD" ;;
    *) die "unsupported backend: $backend (choose opencode, claude or codex)" ;;
  esac
  local dep
  for dep in jq python3 "$cmd"; do
    command -v "$dep" >/dev/null 2>&1 || die "missing required command on PATH: $dep"
  done
  [[ "$backend" != opencode ]] || backend_opencode_detect_version
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
  CEREBRO_RESUME_BACKEND="$(session_backend "$CEREBRO_SESSION_DIR")"
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

# Build a collision-resistant child-log path under the session's children/
# dir. We allow concurrent mutating runs (no per-repo lock), so two
# same-session invocations can start within the same second; a bare
# <subcmd>-<ts> name would let them share one file and produce truncated or
# interleaved logs. Keep the human-readable <subcmd>-<ts> prefix but append
# the PID plus a random token so each invocation gets a distinct file.
child_log_path() {
  local subcmd="$1"
  printf '%s\n' "$CEREBRO_SESSION_DIR/children/${subcmd}-$(ts_compact)-$$-${RANDOM}.jsonl"
}

# Surface a child's final message to the orchestrator on stdout. A child runs
# non-interactively, so when it pauses on a genuine blocker it ends with its
# QUESTION as its closing message rather than completing the work. The mutating
# children otherwise discard that text (they push commits, not output), so we
# capture it and print it under a clear marker. The orchestrator reads this to
# decide whether the child finished or is waiting on an answer (see the
# "child stops to ask a question" rule in its system prompt).
#   $1 = file holding the child's final result text   $2 = role label
#   $3 = optional child provider session id for `cerebro answer`
surface_child_reply() {
  local f="$1" role="$2" child_id="${3:-}"
  [[ -s "$f" ]] || return 0
  printf -- '----- %s child closing message (read it: a question here means the child PAUSED for an answer) -----\n' "$role"
  if [[ -n "$child_id" ]]; then
    printf 'child session: %s\n' "$child_id"
    printf 'answer with: cerebro answer %s "<answer>"\n\n' "$child_id"
  fi
  cat "$f"
  printf -- '\n----- end %s child closing message -----\n' "$role"
}

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

# Materialize shared skills and native backend extras without overwriting user overlays.
materialise_home() {
  mkdir -p "$CEREBRO_HOME/.agents/skills" "$CEREBRO_HOME/.claude/skills" \
    "$CEREBRO_HOME/sessions" "$CEREBRO_HOME/templates" "$CEREBRO_HOME/overlays" \
    || die "cannot create $CEREBRO_HOME"
  write_if_changed "$CEREBRO_HOME/system-prompt.md" "$(cerebro_system_prompt)"
  local src topic
  for src in "$(cerebro_skills_dir)"/*/SKILL.md; do
    topic="$(basename "$(dirname "$src")")"
    mkdir -p "$CEREBRO_HOME/.agents/skills/$topic"
    write_if_changed "$CEREBRO_HOME/.agents/skills/$topic/SKILL.md" "$(cat "$src")"
    # Claude and Codex/OpenCode discover the same source through their native roots.
    if [[ -d "$CEREBRO_HOME/.claude/skills/$topic" && ! -L "$CEREBRO_HOME/.claude/skills/$topic" ]]; then
      rm -rf "$CEREBRO_HOME/.claude/skills/$topic"
    fi
    ln -sfn "../../.agents/skills/$topic" "$CEREBRO_HOME/.claude/skills/$topic"
  done
  local role
  for role in orchestrator observer execute apply-review doc-write verify reviewer; do
    rm -f "$CEREBRO_HOME/.opencode/agent/cerebro-$role.md"
  done
  rm -f "$CEREBRO_HOME/.opencode/plugin/cerebro.js" "$CEREBRO_HOME/.opencode/opencode.json"
  write_if_missing "$CEREBRO_HOME/templates/AGENTS.md" "$(cerebro_default_agents_md)"
  backend_materialise_extras
}

write_if_changed() {
  local path="$1" content="$2"
  if [[ -f "$path" ]] && [[ "$(cat "$path")" == "$content" ]]; then
    return 0
  fi
  printf '%s' "$content" > "$path"
}

write_if_missing() {
  local path="$1" content="$2"
  [[ -f "$path" ]] && return 0
  printf '%s' "$content" > "$path"
}
