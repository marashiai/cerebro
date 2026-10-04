# cerebro lib: commands/acp
# subcommands under `cerebro acp`:
#   (default)        -- start the proxy (external, invoked by the editor)
#   restart          -- signal a running proxy to exit so the editor respawns
#                       it and the new session picks up fresh config
#   mint             -- INTERNAL: called by lib/python/acp_server.py per
#                       session/new to mint a cerebro session
#   set-foreign      -- INTERNAL: called by the python server to record the
#                       upstream child's session id in cerebro metadata
# Sourced by bin/cerebro; not meant to be executed directly.

# `cerebro acp` is the ACP (Agent Client Protocol) front-end for editors like
# Zed. It is a THIN PROXY (lib/python/acp_server.py, on the official
# agent-client-protocol Python SDK): per ACP session it mints a cerebro session,
# spawns a per-session upstream claude-agent-acp child, injects
# CEREBRO_SESSION_ID, pins the Cerebro
# supervisor agent, and relays JSON-RPC unchanged with sessionId
# remap. The upstream child owns the entire ACP capability surface (images,
# @-mentions, thinking, elicitation, terminals, MCP, permissions, edit review,
# model/mode/effort pickers, session load/resume/fork/list, usage); cerebro owns
# session minting + env injection + agent pinning + sessionId remap + metadata.
# See docs/USAGE.md (ACP) and the plan for the pin mechanism details.

# ACP is editor-driven and non-interactive: stdin/stdout are the JSON-RPC pipe,
# not a terminal. So cmd_acp deliberately does NOT call require_interactive.

# acp_require_python_deps -- locate a Python >=3.10 for the ACP SDK
# (agent-client-protocol needs >=3.10,<3.15), preferring Homebrew python3
# (/opt/homebrew/bin/python3) since macOS system python3 is 3.9, and ensure the
# SDK is importable (auto-installing to the user site if missing). For the
# claude backend, also require Node >=22 + npx (claude-agent-acp runs on the
# Claude Agent SDK via `npx -y`). Sets CEREBRO_ACP_PYTHON (exported).
acp_require_python_deps() {
  local py="" v major minor
  for cand in /opt/homebrew/bin/python3 python3; do
    command -v "$cand" >/dev/null 2>&1 || continue
    v="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || continue
    if [[ "$v" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
      major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
      if (( major == 3 && minor >= 10 )) || (( major > 3 )); then
        py="$cand"; break
      fi
    fi
  done
  [[ -n "$py" ]] || die "cerebro acp needs Python >=3.10 (for the agent-client-protocol SDK). None found on PATH. Install with: brew install python"
  export CEREBRO_ACP_PYTHON="$py"
  if ! "$py" -c 'import acp' >/dev/null 2>&1; then
    warn "cerebro acp: installing agent-client-protocol (Python SDK) for $py ..."
    if ! "$py" -m pip install --user --break-system-packages agent-client-protocol >/dev/null 2>&1; then
      die "cerebro acp: failed to install agent-client-protocol. Install manually:
    $py -m pip install --user --break-system-packages agent-client-protocol"
    fi
  fi
  if [[ "$(current_backend)" == "claude" ]]; then
    command -v npx >/dev/null 2>&1 \
      || die "cerebro acp (claude backend) needs Node/npx on PATH (for @agentclientprotocol/claude-agent-acp). Install Node >=22 (e.g. brew install node)."
    local node_major
    node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)" \
      || die "cerebro acp (claude backend): cannot determine Node version (needs >=22)."
    (( node_major >= 22 )) \
      || die "cerebro acp (claude backend) needs Node >=22 (found $node_major). Upgrade Node (e.g. brew upgrade node)."
  fi
}

# cmd_acp -- start the cerebro ACP proxy server over stdio. Materialise the home
# tree once (so cerebro subcommands the orchestrator spawns find their agents),
# build + export the upstream child spec (JSON: argv + pin + env, backend-fixed),
# then exec the python server. The server reads CEREBRO_ACP_CHILD_SPEC and
# shells out to `cerebro acp mint` / `cerebro acp set-foreign` per session.
cmd_acp() {
  case "$(current_backend)" in
    claude) ;;
    pi|codex) backend_acp_child_spec; return $? ;;
    *) die "unsupported ACP backend: $(current_backend)" ;;
  esac
  case "${1:-}" in
    restart)     shift; cmd_acp_restart     "$@" ; return $? ;;
    # internal: called by lib/python/acp_server.py over `cerebro acp <name>`
    mint)        shift; cmd_acp_mint        "$@" ; return $? ;;
    set-foreign) shift; cmd_acp_set_foreign "$@" ; return $? ;;
    "")      ;;
    *)       die "cerebro acp: unknown subcommand: $1 (try: cerebro acp restart)" ;;
  esac
  # Reap any orphan upstream children left behind by a previous proxy that
  # exited without going through a clean per-session teardown (e.g. SIGTERM
  # with no signal handler, or an editor crash). Without this, the new
  # proxy's session/load may pick up a foreign session id that the orphan
  # is still bound to, and the editor's prompt errors out with
  # "ACP connection closed". Done before the python server starts so we
  # never see our own freshly-spawned children as orphans (their PPID is
  # this process, not 1).
  acp_reap_orphans
  require_deps
  acp_require_python_deps
  materialise_home
  export CEREBRO_ACP_CHILD_SPEC="$(backend_acp_child_spec)"
  # The proxy and its native parents must preserve separate child model defaults.
  export CEREBRO_HOME \
         CEREBRO_BACKEND \
         CEREBRO_MODEL CEREBRO_SUPERVISOR_MODEL CEREBRO_REVIEW_MODEL \
         CEREBRO_SUPERVISOR_EFFORT CEREBRO_IMPLEMENTOR_EFFORT CEREBRO_REVIEW_EFFORT \
         CEREBRO_CLAUDE_BASE_URL CEREBRO_CLAUDE_AUTH_TOKEN
  export CEREBRO_INPUT_OWNER=external
  exec "$CEREBRO_ACP_PYTHON" "$CEREBRO_LIB_DIR/python/acp_server.py"
}

# cmd_acp_mint (internal) -- mint a cerebro session for one ACP session/new and
# prepare its Cerebro-owned ACP project dir, then print only the sid. Native
# launch happens after the proxy injects the session binding. Claude discovers
# its supervisor wrapper here. The user's repo is an additional directory.
cmd_acp_mint() {
  local sid sess_dir ts proj
  sid="$(mint_uuid)"
  sess_dir="$CEREBRO_HOME/sessions/$sid"
  proj="$CEREBRO_HOME/acp/$sid"
  mkdir -p "$sess_dir/tasks" "$sess_dir/children" \
           "$proj/.claude/agents" \
    || die "acp-mint: cannot create session dirs under $CEREBRO_HOME"
  : > "$sess_dir/transcript.jsonl"
  ts="$(ts_iso)"
  write_metadata_new "$sess_dir" "$sid" "$ts"
  CEREBRO_SESSION_ID="$sid" CEREBRO_SESSION_DIR="$sess_dir" \
    backend_supervisor_config supervisor > /dev/null
  cp "$sess_dir/tools-supervisor.json" "$proj/.mcp.json"
  write_if_changed "$proj/.claude/agents/cerebro-orchestrator.md" "$(claude_orchestrator_agent_file)"
  printf '%s\n' "$sid"
}

# cmd_acp_set_foreign <sid> <foreign-id> (internal) -- record the upstream
# child's native session id into the
# cerebro session metadata, so ACP session/load + session/resume can reopen the
# same upstream conversation. Called by the python server after new_session /
# load_session / resume_session.
cmd_acp_set_foreign() {
  local sid="$1" foreign="$2" sess_dir
  sess_dir="$CEREBRO_HOME/sessions/$sid"
  [[ -d "$sess_dir" ]] || die "acp-set-foreign: no such session: $sid"
  set_metadata_foreign "$sess_dir" "$foreign"
}

# cmd_acp_restart -- signal the running `cerebro acp` proxy (for this
# CEREBRO_HOME) to exit, so the editor respawns it and the new session
# picks up the fresh $CEREBRO_HOME/config.json and CEREBRO_* env. The proxy
# is a long-lived process spawned by the editor; SIGTERM closes the JSON-RPC
# pipe and the editor (Zed, ...) respawns `cerebro acp` automatically. After
# the proxy exits, also reap any orphan upstream children (the per-session
# `claude-agent-acp` processes the old proxy spawned); they
# were attached to the dead proxy, so leaving them alive means the new
# proxy's `session/load` for those session ids will conflict with the orphan
# and the editor sees "ACP connection closed" on its next prompt. The new
# proxy itself also reaps on startup (see cmd_acp), so a manual kill of the
# proxy via Activity Monitor is also handled on the next editor reconnect.
# Idempotent: no proxy running -> exit 0; the orphan reaping still runs.
cmd_acp_restart() {
  local pids=() pid home
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    home="$(acp_proxy_env_home "$pid" 2>/dev/null || true)"
    [[ "$home" == "$CEREBRO_HOME" ]] && pids+=("$pid")
  done < <(acp_running_proxy_pids)
  case "${#pids[@]}" in
    0)
      say "cerebro acp: no running proxy for CEREBRO_HOME=$CEREBRO_HOME (already stopped, or never started)"
      ;;
    1)
      say "cerebro acp: restarting proxy pid=${pids[0]} (CEREBRO_HOME=$CEREBRO_HOME)"
      kill -TERM "${pids[0]}" 2>/dev/null || true
      local i
      for i in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "${pids[0]}" 2>/dev/null || break
        sleep 0.2
      done
      if kill -0 "${pids[0]}" 2>/dev/null; then
        warn "cerebro acp: proxy pid=${pids[0]} did not exit on SIGTERM; sending SIGKILL"
        kill -KILL "${pids[0]}" 2>/dev/null || true
      fi
      say "cerebro acp: proxy ${pids[0]} stopped"
      ;;
    *)
      die "cerebro acp: multiple proxies found for CEREBRO_HOME=$CEREBRO_HOME: ${pids[*]} (kill the wrong ones manually with: kill -TERM <pid>)"
      ;;
  esac
  acp_reap_orphans
  say "cerebro acp: restart done -- the editor will respawn it and new sessions will pick up the fresh config"
  return 0
}

# acp_running_proxy_pids -- PIDs of processes whose command line mentions
# acp_server.py (the exec'd target of `cerebro acp`), excluding the calling
# process. Echoes one PID per line. `ps -o key=` (headerless) is portable
# to BSD ps (macOS) and GNU ps (linux).
acp_running_proxy_pids() {
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    [[ "$pid" == "$$" ]] && continue
    printf '%s\n' "$pid"
  done < <(ps -A -o pid=,command= 2>/dev/null \
            | awk 'index($0, "acp_server.py") { print $1 }')
}

# acp_proxy_env_home <pid> -- echo the value of CEREBRO_HOME in <pid>'s env,
# or empty if unreadable. macOS exposes the env via `ps eww -p <pid>`; linux
# exposes it via /proc/<pid>/environ (NUL-separated). The macOS form dumps
# `KEY=VALUE` tokens after the command line, so we split on whitespace and
# newlines; on linux we split on NUL. We extract only the CEREBRO_HOME key
# (and only its value) to avoid being fooled by other env values that
# happen to contain `CEREBRO_HOME=` (e.g. CEREBRO_ACP_CHILD_SPEC is JSON
# and may contain anything).
acp_proxy_env_home() {
  local pid="$1"
  local blob=""
  if [[ -r "/proc/$pid/environ" ]]; then
    blob="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null || true)"
  else
    # macOS: ps eww prints `KEY=VAL` tokens separated by spaces after the
    # command line. Newlines and `=` inside values (e.g. the JSON spec) are
    # escaped as `\n` and `\=`. We split on raw whitespace; the
    # awk-by-key filter below picks out the CEREBRO_HOME token.
    blob="$(ps eww -p "$pid" 2>/dev/null | tail -n +2 | tr ' \t' '\n' || true)"
  fi
  printf '%s\n' "$blob" \
    | awk -F= 'index($0, "CEREBRO_HOME=")==1 { sub(/^CEREBRO_HOME=/,""); print; exit }'
}

# acp_pid_env_get <pid> <key> -- echo the value of env <key> in <pid>'s
# environment, or empty if unset. macOS exposes the env via `ps eww -p
# <pid>`; linux exposes it via /proc/<pid>/environ (NUL-separated). On
# macOS the values may contain newlines/=/spaces that would break a naive
# `KEY=VAL` tokenisation -- so we read the WHOLE blob, then awk-pick the
# line that starts with "<key>=" and emit everything after it. This is
# the same trick `acp_proxy_env_home` uses, kept here as a separate
# function so the orphan reaper can pull any env key it likes.
acp_pid_env_get() {
  local pid="$1" key="$2"
  [[ -n "$pid" && -n "$key" ]] || return 0
  local blob=""
  if [[ -r "/proc/$pid/environ" ]]; then
    blob="$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null || true)"
  else
    blob="$(ps eww -p "$pid" 2>/dev/null | tail -n +2 | tr ' \t' '\n' || true)"
  fi
  printf '%s\n' "$blob" \
    | awk -v k="$key" -F= 'index($0, k "=")==1 { sub("^" k "=", ""); print; exit }'
}

# acp_reap_orphans -- for every per-session project dir under
# $CEREBRO_HOME/acp/, kill any orphan upstream child bound to that session.
# Called from both cmd_acp_restart (after the proxy dies) and cmd_acp
# startup (so a manually-killed proxy is also cleaned up on next editor
# reconnect). Quiet: no output when there are no orphans to reap.
#
# Performance: doing one pass per session (scan ps, then per-candidate
# `ps eww` to read its env) is O(sessions * processes) and can take
# seconds on a busy system. We do ONE pass instead: enumerate all
# PPID==1 processes, read each one's env, extract the
# CEREBRO_SESSION_ID, group by sid, then kill the ones whose sid is
# under $CEREBRO_HOME/acp/. The env reads are still per-orphan, but the
# ps scan is a single pass and the total is O(processes), not
# O(sessions * processes).
acp_reap_orphans() {
  local acp_dir="$CEREBRO_HOME/acp"
  [[ -d "$acp_dir" ]] || return 0
  # Collect known session ids into a bash-3-friendly space-separated list.
  # `local -A` (associative arrays) is bash 4+ and the macOS system bash
  # is 3.2, so we use a flat string and `case` instead. The sids are
  # uuid-like (alnum+hyphen) so a leading/trailing space around each
  # makes the `case "$list" in *" $sid "*` test unambiguous.
  local known_sids=" "
  local sid
  for sid in "$acp_dir"/*/; do
    [[ -d "$sid" ]] || continue
    sid="${sid%/}"; sid="${sid##*/}"
    known_sids+="$sid "
  done
  [[ "$known_sids" != " " ]] || return 0
  # Single ps pass: PPID==1 processes whose command line looks like an
  # upstream ACP child (claude-agent-acp or the npx
  # shim that wraps claude-agent-acp). We pre-filter on a command
  # substring before paying the `ps eww` cost for the env read -- on a
  # busy system there can be ~600 PPID==1 processes (every macOS system
  # service is reparented to launchd), and we only care about the few
  # that are ours. The cmdline filter is generous on purpose: any
  # process that doesn't even mention `acp` / `claude`
  # couldn't be a child of the cerebro acp proxy.
  local -a to_reap
  local pid ppid cmdline env_sid
  while IFS= read -r line; do
    pid="${line%% *}"; line="${line#"$pid" }"
    ppid="${line%% *}"; line="${line#"$ppid" }"
    cmdline="$line"
    [[ "$ppid" == "1" ]] || continue
    case "$cmdline" in
      *acp*|*claude*|*npx*) ;;  # could be one of ours
      *) continue ;;
    esac
    env_sid="$(acp_pid_env_get "$pid" "CEREBRO_SESSION_ID")"
    [[ -n "$env_sid" ]] || continue
    case "$known_sids" in
      *" $env_sid "*) ;;  # known: queue for reaping
      *) continue ;;
    esac
    to_reap+=("$pid:$env_sid")
  done < <(ps -A -o pid=,ppid=,command= 2>/dev/null | awk '{$1=$1; print}')
  # Reap. Quiet unless something was found.
  if (( ${#to_reap[@]} == 0 )); then return 0; fi
  local reaped=0 entry
  for entry in "${to_reap[@]}"; do
    pid="${entry%%:*}"; sid="${entry#*:}"
    say "cerebro acp: reaping orphan upstream child pid=$pid (session $sid)"
    kill -TERM "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    reaped=$((reaped + 1))
  done
  if (( reaped > 0 )); then
    say "cerebro acp: reaped $reaped orphan upstream child(ren)"
  fi
}
