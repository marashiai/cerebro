# cerebro lib: commands/models
# subcommand: models (list the user's model catalog)
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- subcommand: cerebro models [--json] ----------------------------------
# Prints the model catalog the user maintains at
# $CEREBRO_HOME/models-config.json so the orchestrator (and the user) can
# discover which models are available, their capability tags, and a free-text
# description of their fit -- then pick a model per task via the subcommands'
# --model flag. The catalog is a JSON object:
#   { "models": [ { "id": "<provider/model>",
#                    "capabilities": ["vision","tools","thinking","audio"],
#                    "contextTokens": <integer>,
#                    "description": "<free text>" }, ... ] }
# `id` is the exact provider/model string the opencode/claude `--model` flag
# expects (same shape as CEREBRO_MODEL). `capabilities` is an open set of
# present tags; "vision" (multimodal image input) is the one that matters for
# screenshot/browser verification -- a model without it cannot interpret
# Playwright screenshots, so the orchestrator routes visual verification to a
# vision-capable model from this catalog. `description` is free text for
# judgement the orchestrator reasons about (context window, reasoning depth,
# cost, etc.). `contextTokens` is the optional integer token count of the
# model's context window; when the claude backend runs behind a custom
# endpoint (CEREBRO_CLAUDE_BASE_URL) Claude Code can't infer the window for an
# unrecognized id and falls back to 200k, so cerebro exports it as
# CLAUDE_CODE_AUTO_COMPACT_WINDOW for that model (see backend_claude_endpoint_env
# and `cerebro model-env`).
#
# A missing/unreadable catalog is NOT a failure: it prints a one-line note (or
# an empty JSON array with --json) so the orchestrator knows there is nothing
# to choose from and the subcommands fall back to their env-var defaults
# (CEREBRO_MODEL / CEREBRO_REVIEW_MODEL). This command does not require a
# session -- it is a plain catalog lookup usable from a shell too.

# require_model_for_backend <id> <backend> [subcmd] -- reject a --model whose
# format does not match the backend the child runs under, up front with a
# clear message instead of a confusing downstream failure. opencode requires
# a 'provider/model' id (with a '/'); a claude-backend id (no '/', e.g. an
# Ollama "model:tag") can't be resolved by opencode and is silently dropped
# by the opencode serve pump on resume -- so reject it here. claude passes
# the id verbatim and tolerates either shape, so it is left unchecked.
require_model_for_backend() {
  local id="$1" backend="$2" subcmd="${3:-}"
  [[ "$backend" == "opencode" && "$id" != */* ]] || return 0
  die "${subcmd:+$subcmd: }--model '$id' is not valid for the opencode backend, which needs a 'provider/model' id (with a '/'). List models with: cerebro models"
}

cmd_models() {
  local json=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json) json=1; shift ;;
      *) die "models: unknown arg: $1" ;;
    esac
  done
  local cfg="$CEREBRO_HOME/models-config.json"
  if [[ ! -r "$cfg" || ! -s "$cfg" ]]; then
    if (( json )); then
      printf '{"models":[]}\n'
    else
      say "no model catalog found at $cfg"
      echo "Create one to let the orchestrator pick models per task. Schema:"
      echo '  { "models": [ { "id": "<provider/model>", "capabilities": ["vision","tools","thinking","audio"], "contextTokens": <integer>, "description": "<free text>" } ] }'
      echo "The subcommands' --model flag takes the catalog entry's id verbatim."
    fi
    return 0
  fi
  if ! jq -e '.models' "$cfg" >/dev/null 2>&1; then
    warn "models: $cfg is not a valid catalog (missing top-level .models array)"
    (( json )) && printf '{"models":[]}\n'
    return 1
  fi
  if (( json )); then
    jq -c '.models' "$cfg"
    return 0
  fi
  say "model catalog ($cfg)"
  jq -r '
    .models[]
    | "## " + .id
      + "\ncapabilities: " + ((.capabilities // []) | join(", ") | if . == "" then "(none)" else . end)
      + "\n" + (.description // "(no description)")
      + "\n"
  ' "$cfg"
}

# models_context_tokens <id> -- look up a model's context-window token count in
# the user's catalog ($CEREBRO_HOME/models-config.json) by id, printing the
# integer to stdout. The value backs the CLAUDE_CODE_AUTO_COMPACT_WINDOW cerebro
# exports for a custom claude endpoint (CEREBRO_CLAUDE_BASE_URL) so Claude Code
# doesn't fall back to its 200k default for an unrecognized model id (see
# backend_claude_endpoint_env and `cerebro model-env`). Non-failure on a
# missing/unreadable catalog, an unknown id, or a missing/non-numeric
# contextTokens: prints nothing and returns 0, so the caller just skips the
# override and keeps Claude Code's default window.
models_context_tokens() {
  local cfg="$CEREBRO_HOME/models-config.json"
  [[ -r "$cfg" && -s "$cfg" ]] || return 0
  local toks
  toks="$(jq -r --arg id "$1" \
      '.models[] | select(.id==$id) | (.contextTokens | numbers)' \
      "$cfg" 2>/dev/null)" || return 0
  [[ -n "$toks" ]] || return 0
  toks="${toks%%$'\n'*}"   # first match only; ids are unique in practice
  [[ "$toks" =~ ^[0-9]+$ && "$toks" -gt 0 ]] || return 0
  printf '%s\n' "$toks"
}