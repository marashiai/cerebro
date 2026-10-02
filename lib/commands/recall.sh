# cerebro lib: commands/recall
# subcommand: recall
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- subcommand: cerebro recall <query> ----------------------------------

cmd_recall() {
  require_session
  local query="${*:-}"
  [[ -n "$query" ]] || die "usage: cerebro recall <query>"

  local -a files=()
  local file
  while IFS= read -r -d '' file; do files+=("$file"); done \
    < <(find "$CEREBRO_HOME/sessions" -type f -name '*.jsonl' -print0 2>/dev/null)
  if (( ${#files[@]} == 0 )); then
    echo "cerebro: nothing to recall (no session logs yet)"
    return 0
  fi

  local have_rg=0
  command -v rg >/dev/null 2>&1 && have_rg=1

  # Pass 1: literal match of the whole query (the precise hit).
  local out
  if (( have_rg )); then
    out=$(rg --no-heading --line-number --color never --fixed-strings -- "$query" "${files[@]}" 2>/dev/null || true)
  else
    out=$(grep -RnF --color=never -- "$query" "${files[@]}" 2>/dev/null || true)
  fi
  if [[ -n "$out" ]]; then
    printf '%s\n' "$out"
    return 0
  fi

  # Pass 2: broaden. The whole-phrase match found nothing, but a
  # multi-word query treated literally rarely appears verbatim in a
  # transcript. Re-search for ANY single term (case-insensitive) so a
  # query like "repo-x orchestrator game designer" still
  # surfaces the session that only ever said "repo-x".
  local -a terms
  read -ra terms <<<"$query"
  (( ${#terms[@]} <= 1 )) && return 0

  local -a args
  if (( have_rg )); then
    args=(rg --no-heading --line-number --color never --ignore-case --fixed-strings)
  else
    args=(grep -RnFi --color=never)
  fi
  local t
  for t in "${terms[@]}"; do args+=(-e "$t"); done

  out=$("${args[@]}" -- "${files[@]}" 2>/dev/null | head -n 100 || true)
  [[ -z "$out" ]] && return 0

  echo "cerebro recall: no verbatim match for \"$query\";" \
       "broadened to ANY of these terms (first 100 hits): ${terms[*]}"
  echo "---"
  printf '%s\n' "$out"
}
