# cerebro lib: commands/model-env
# subcommand: model-env (print Claude Code env exports for a catalog model)
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- subcommand: cerebro model-env <id> [--no-compact] ---------------------
# Prints shell `export` lines that tell Claude Code the selected model's real
# context window, for use before a direct `claude --model <id>` launch against
# a custom endpoint (Ollama / any ANTHROPIC_BASE_URL). Claude Code can't infer
# the window for an unrecognized model id and falls back to 200k; these exports
# fix that. The token count is read from the model's `contextTokens` field in
# $CEREBRO_HOME/models-config.json (see `cerebro models` and
# models_context_tokens).
#
# Default (keep auto-compaction): export CLAUDE_CODE_AUTO_COMPACT_WINDOW=<tokens>
# -- raises the threshold at which auto-compaction fires, so a 1M model is not
# compacted at the 200k default. NOTE: Claude Code caps this value at the
# model's "actual context window", which for an unrecognized id may still be the
# 200k fallback, so the status line can still read 200k and compaction may still
# fire there. If you hit that, use --no-compact.
#
# --no-compact: export CLAUDE_CODE_MAX_CONTEXT_TOKENS=<tokens> and DISABLE_COMPACT=1
# -- the documented override that makes Claude Code honor the true window (the
# status line and /context reflect it) at the cost of disabling auto-compaction
# entirely (sessions hit the hard limit instead of summarizing).
#
# A model with no `contextTokens` (or an unknown id, or no catalog) prints a
# one-line note on stderr and no exports on stdout, so `eval "$(cerebro
# model-env <id>)"` is a safe no-op. This command does not require a session.
cmd_model_env() {
  local no_compact=0 id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-compact) no_compact=1; shift ;;
      -*) die "model-env: unknown arg: $1" ;;
      *) [[ -z "$id" ]] || die "model-env: unexpected extra arg: $1"
         id="$1"; shift ;;
    esac
  done
  [[ -n "$id" ]] || die "model-env: missing model id (try `cerebro models`)"
  local toks; toks="$(models_context_tokens "$id")"
  if [[ -z "$toks" ]]; then
    say "model-env: no contextTokens declared for '$id'; nothing to export"
    return 0
  fi
  if (( no_compact )); then
    printf 'export CLAUDE_CODE_MAX_CONTEXT_TOKENS=%s\n' "$toks"
    printf 'export DISABLE_COMPACT=1\n'
  else
    printf 'export CLAUDE_CODE_AUTO_COMPACT_WINDOW=%s\n' "$toks"
  fi
}