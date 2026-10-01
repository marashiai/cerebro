# cerebro lib: config
# global config: set -uo pipefail and CEREBRO_* env defaults
# Sourced by bin/cerebro; not meant to be executed directly.
#
# Option precedence is env > $CEREBRO_HOME/config.json > hardcoded default:
# every CEREBRO_* option below reads ${CEREBRO_VAR:-${CEREBRO_CFG_VAR:-default}},
# where CEREBRO_CFG_VAR is populated from the optional config.json (see
# _cerebro_options_load). CEREBRO_HOME itself is env-only -- the config file
# lives under it, so it can't bootstrap itself from the file.

set -uo pipefail

CEREBRO_HOME="${CEREBRO_HOME:-$HOME/.cerebro}"

# ----- options config file ($CEREBRO_HOME/config.json) ----------------------
# Optional JSON file alongside models-config.json. Top-level keys are lower-
# case option names (e.g. "backend", "model", "pair_idle") and supply the
# default for the matching CEREBRO_<KEY> env var. Env vars always win; the file
# wins over the hardcoded defaults below. Unknown keys are silently ignored,
# and a missing/invalid file is not an error -- it just leaves the CEREBRO_CFG_*
# scratch vars empty so the ${...:-default} fallbacks apply. Values are read as
# strings (jq tostring); numeric options are coerced by their arithmetic
# context at use, exactly as an env var would be.
CEREBRO_CFG_BACKEND=""
CEREBRO_CFG_MODEL=""
CEREBRO_CFG_REVIEW_MODEL=""
CEREBRO_CFG_TIMEOUT=""
CEREBRO_CFG_CHILD_IDLE_TIMEOUT=""
CEREBRO_CFG_OPENCODE_CMD=""
CEREBRO_CFG_CLAUDE_CMD=""
CEREBRO_CFG_CODEX_CMD=""
CEREBRO_CFG_DEBUG=""
CEREBRO_CFG_CLAUDE_BASE_URL=""
CEREBRO_CFG_CLAUDE_AUTH_TOKEN=""
CEREBRO_CFG_OVERLAY_CAP=""
CEREBRO_CFG_META_HORIZON=""
CEREBRO_CFG_CHILD_SESSION_TTL=""
CEREBRO_CFG_PAIR_IDLE=""
CEREBRO_CFG_PAIR_STALL=""
CEREBRO_CFG_PAIR_STALL_BUSY=""
CEREBRO_CFG_PAIR_STALL_RETRIES=""
CEREBRO_CFG_PAIR_STALL_BACKOFF=""
_cerebro_options_load() {
  local cfg="$CEREBRO_HOME/config.json"
  [[ -r "$cfg" && -s "$cfg" ]] || return 0
  local k v
  while IFS=$'\t' read -r k v; do
    [[ -n "$k" ]] || continue
    case "$k" in
      backend)             CEREBRO_CFG_BACKEND="$v" ;;
      model)              CEREBRO_CFG_MODEL="$v" ;;
      review_model)       CEREBRO_CFG_REVIEW_MODEL="$v" ;;
      timeout)            CEREBRO_CFG_TIMEOUT="$v" ;;
      child_idle_timeout) CEREBRO_CFG_CHILD_IDLE_TIMEOUT="$v" ;;
      opencode_cmd)       CEREBRO_CFG_OPENCODE_CMD="$v" ;;
      codex_cmd)          CEREBRO_CFG_CODEX_CMD="$v" ;;
      claude_cmd)         CEREBRO_CFG_CLAUDE_CMD="$v" ;;
      debug)              CEREBRO_CFG_DEBUG="$v" ;;
      claude_base_url)    CEREBRO_CFG_CLAUDE_BASE_URL="$v" ;;
      claude_auth_token)  CEREBRO_CFG_CLAUDE_AUTH_TOKEN="$v" ;;
      overlay_cap)        CEREBRO_CFG_OVERLAY_CAP="$v" ;;
      meta_horizon)       CEREBRO_CFG_META_HORIZON="$v" ;;
      child_session_ttl)  CEREBRO_CFG_CHILD_SESSION_TTL="$v" ;;
      pair_idle)          CEREBRO_CFG_PAIR_IDLE="$v" ;;
      pair_stall)         CEREBRO_CFG_PAIR_STALL="$v" ;;
      pair_stall_busy)    CEREBRO_CFG_PAIR_STALL_BUSY="$v" ;;
      pair_stall_retries) CEREBRO_CFG_PAIR_STALL_RETRIES="$v" ;;
      pair_stall_backoff) CEREBRO_CFG_PAIR_STALL_BACKOFF="$v" ;;
      # unknown keys are ignored
    esac
  done < <(jq -r 'to_entries[] | "\(.key)\t\(.value|tostring)"' "$cfg" 2>/dev/null)
}
_cerebro_options_load

# A session and all its children use one native backend. An empty model lets
# that backend select its configured default; --model still overrides per call.
CEREBRO_BACKEND="${CEREBRO_BACKEND:-${CEREBRO_CFG_BACKEND:-opencode}}"
CEREBRO_MODEL="${CEREBRO_MODEL:-${CEREBRO_CFG_MODEL:-}}"
CEREBRO_REVIEW_MODEL="${CEREBRO_REVIEW_MODEL:-${CEREBRO_CFG_REVIEW_MODEL:-$CEREBRO_MODEL}}"
CEREBRO_TIMEOUT="${CEREBRO_TIMEOUT:-${CEREBRO_CFG_TIMEOUT:-0}}"   # 0/empty/none/unlimited = no cap
# Native transports own completion and busy-tool timeouts. This optional
# parser inactivity cap defaults to disabled so quiet tools can finish.
CEREBRO_CHILD_IDLE_TIMEOUT="${CEREBRO_CHILD_IDLE_TIMEOUT:-${CEREBRO_CFG_CHILD_IDLE_TIMEOUT:-0}}"
CEREBRO_OPENCODE_CMD="${CEREBRO_OPENCODE_CMD:-${CEREBRO_CFG_OPENCODE_CMD:-opencode}}"
CEREBRO_CLAUDE_CMD="${CEREBRO_CLAUDE_CMD:-${CEREBRO_CFG_CLAUDE_CMD:-claude}}"
CEREBRO_CODEX_CMD="${CEREBRO_CODEX_CMD:-${CEREBRO_CFG_CODEX_CMD:-codex}}"
CEREBRO_DEBUG="${CEREBRO_DEBUG:-${CEREBRO_CFG_DEBUG:-0}}"
# Optional custom Anthropic-compatible endpoint for the claude backend (e.g. a
# local Ollama server exposing /v1/messages, or any proxy/gateway). Empty (the
# default) = use the claude.ai subscription `claude` is logged into -- the
# existing subscription path, unchanged. When CEREBRO_CLAUDE_BASE_URL is set,
# backend_claude_endpoint_env (lib/backend-claude.sh) exports ANTHROPIC_BASE_URL
# and ANTHROPIC_AUTH_TOKEN into every spawned `claude` process, unsets
# ANTHROPIC_API_KEY (so a logged-in subscription can't hijack the run), and pins
# ANTHROPIC_MODEL/ANTHROPIC_DEFAULT_HAIKU_MODEL to CEREBRO_MODEL -- including
# for the orchestrator, which has no --model flag of its own. CEREBRO_MODEL must
# then name a model the endpoint actually serves. CEREBRO_CLAUDE_AUTH_TOKEN
# defaults to a non-empty placeholder the endpoint ignores (local no-auth
# servers); set it to the real key for an authenticated gateway.
CEREBRO_CLAUDE_BASE_URL="${CEREBRO_CLAUDE_BASE_URL:-${CEREBRO_CFG_CLAUDE_BASE_URL:-}}"
CEREBRO_CLAUDE_AUTH_TOKEN="${CEREBRO_CLAUDE_AUTH_TOKEN:-${CEREBRO_CFG_CLAUDE_AUTH_TOKEN:-}}"
# Max chars in a single harness overlay file. Larger than learnings' cap since
# overlays aren't all carried in one system message, but still bounded.
CEREBRO_OVERLAY_CAP="${CEREBRO_OVERLAY_CAP:-${CEREBRO_CFG_OVERLAY_CAP:-4000}}"

# ----- two-timescale self-improvement ---------------------------------------
# The fast loop (cerebro improve) evolves task-level overlays; the slow loop
# (cerebro improve --meta) evolves the meta-skill (the improvement procedure
# itself) every H successful fast-loop runs.
CEREBRO_META_HORIZON="${CEREBRO_META_HORIZON:-${CEREBRO_CFG_META_HORIZON:-2}}"

# ----- pair / child-session options (also configurable via config.json) ----
# These were historically read inline at their use sites with their own
# hardcoded fallbacks. Setting them here through the same env > config.json >
# default precedence makes $CEREBRO_HOME/config.json effective for them too.
# The inline ${...:-default} reads at the use sites remain as a redundant
# safety net and stay in sync with these defaults.
CEREBRO_CHILD_SESSION_TTL="${CEREBRO_CHILD_SESSION_TTL:-${CEREBRO_CFG_CHILD_SESSION_TTL:-86400}}"
CEREBRO_PAIR_IDLE="${CEREBRO_PAIR_IDLE:-${CEREBRO_CFG_PAIR_IDLE:-60}}"
CEREBRO_PAIR_STALL="${CEREBRO_PAIR_STALL:-${CEREBRO_CFG_PAIR_STALL:-180}}"
CEREBRO_PAIR_STALL_BUSY="${CEREBRO_PAIR_STALL_BUSY:-${CEREBRO_CFG_PAIR_STALL_BUSY:-450}}"
CEREBRO_PAIR_STALL_RETRIES="${CEREBRO_PAIR_STALL_RETRIES:-${CEREBRO_CFG_PAIR_STALL_RETRIES:-2}}"
CEREBRO_PAIR_STALL_BACKOFF="${CEREBRO_PAIR_STALL_BACKOFF:-${CEREBRO_CFG_PAIR_STALL_BACKOFF:-5}}"
