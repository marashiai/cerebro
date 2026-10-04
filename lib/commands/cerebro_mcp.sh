# cerebro lib: commands/cerebro-mcp
# `cerebro cerebro-mcp` starts the generic PTY MCP server (lib/python/cerebro_mcp_server.py)
# over stdio on the official `mcp` Python SDK. The server owns long-lived
# pseudo-terminals in its own process and exposes them as MCP tools so a
# controller (another agent, an editor, a test) can spawn an interactive TTY
# program, send input, and wait on idle/match/exit events without polling.
# cerebro is one client; the surface drives any interactive program.
#
# Sourced by bin/cerebro; not meant to be executed directly.

# Like `cerebro acp`, this is editor/client-driven and non-interactive: stdin and
# stdout are the MCP JSON-RPC pipe, not a terminal. So cmd_cerebro_mcp deliberately
# does NOT call require_interactive.

# There is intentionally NO `cerebro-mcp restart` subcommand (unlike `cerebro acp
# restart`). ACP restart exists to (a) kill its proxy so the editor respawns it
# with fresh config.json/backend env, and (b) reap orphan upstream children that
# outlive a dead proxy. Neither applies here: the PTY server reads no server-wide
# config (it's a generic stateless PTY holder), the MCP client -- not an auto-
# respawning editor -- owns the process, killing it would destroy the live PTY
# sessions that are the whole point, and PTY children self-clean via SIGHUP on
# master close + atexit SIGTERM. If the server misbehaves, reconnect via the MCP
# client (session restart), not a cerebro subcommand. See lib/commands/acp.sh
# for the contrast.

# cerebro_mcp_require_python_deps -- locate a Python >=3.10 for the `mcp` SDK
# (the official mcp Python SDK needs >=3.10), preferring Homebrew python3
# (/opt/homebrew/bin/python3) since macOS system python3 is 3.9, and ensure the
# SDK is importable (auto-installing to the user site if missing). Mirrors
# acp_require_python_deps (lib/commands/acp.sh) with `import mcp` substituted.
# Sets CEREBRO_MCP_PYTHON (exported).
cerebro_mcp_require_python_deps() {
  local py="" v major minor
  for cand in /opt/homebrew/bin/python3 python3 python3.13 python3.12 python3.11 python3.10; do
    command -v "$cand" >/dev/null 2>&1 || continue
    v="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || continue
    if [[ "$v" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
      major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
      if (( major == 3 && minor >= 10 )) || (( major > 3 )); then
        py="$cand"; break
      fi
    fi
  done
  [[ -n "$py" ]] || die "cerebro cerebro-mcp needs Python >=3.10 (for the mcp SDK). None found on PATH. Install with: brew install python"
  export CEREBRO_MCP_PYTHON="$py"
  if ! "$py" -c 'from mcp.server.mcpserver import MCPServer' >/dev/null 2>&1; then
    warn "cerebro cerebro-mcp: installing mcp (Python SDK) for $py ..."
    if ! "$py" -m pip install --user --break-system-packages 'mcp>=2,<3' >/dev/null 2>&1; then
      die "cerebro cerebro-mcp: failed to install mcp. Install manually:
    $py -m pip install --user --break-system-packages 'mcp>=2,<3'"
    fi
  fi
}

# cmd_cerebro_mcp -- start the cerebro PTY MCP server over stdio. Probe the python
# dep, export the cerebro env so programs spawned later via cerebro_spawn inherit
# the user's cerebro home / backend / model defaults (harmless for non-cerebro
# programs, helpful when the spawned program IS cerebro), then exec the server.
# The server inherits stdio directly: stdin/stdout = MCP JSON-RPC pipe to the
# client, stderr = diagnostics.
cmd_cerebro_mcp() {
  cerebro_mcp_require_python_deps
  # config.sh sets the CEREBRO_* vars as plain shell variables (only
  # CEREBRO_HOME defaults there); export them so the python server's env carries
  # them and cerebro_spawn'd children inherit them (os.environ.copy() in the server).
  export CEREBRO_HOME \
         CEREBRO_BACKEND \
         CEREBRO_MODEL CEREBRO_SUPERVISOR_MODEL CEREBRO_REVIEW_MODEL \
         CEREBRO_CLAUDE_BASE_URL CEREBRO_CLAUDE_AUTH_TOKEN
  exec "$CEREBRO_MCP_PYTHON" "$CEREBRO_LIB_DIR/python/cerebro_mcp_server.py"
}
