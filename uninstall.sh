#!/usr/bin/env bash
# uninstall.sh - Remove the aminmarashi/cerebro install: drop the `cerebro`
# symlinks and clean the PATH block the installer added.
#
# Usage:
#   cerebro-uninstall            # remove symlinks + PATH block (keep clone + state)
#   cerebro-uninstall --purge    # also delete the cloned source
#
# Session state under $CEREBRO_HOME (default ~/.cerebro/) is NEVER touched.
#
# Env:
#   CEREBRO_SRC       clone location           (default: $HOME/.local/share/cerebro)
#   CEREBRO_BINDIR    dir with the symlinks     (default: $HOME/bin)
#   CEREBRO_PURGE     if 1, also delete the clone (same as --purge)

set -uo pipefail

SRC="${CEREBRO_SRC:-$HOME/.local/share/cerebro}"
BINDIR="${CEREBRO_BINDIR:-$HOME/bin}"
PURGE="${CEREBRO_PURGE:-0}"
[[ "${1:-}" == "--purge" ]] && PURGE=1

say() { printf '==> %s\n' "$*"; }

for link in "$BINDIR/cerebro" "$BINDIR/cerebro-uninstall"; do
  if [[ -L "$link" || -f "$link" ]]; then
    rm -f "$link" && say "removed $link"
  fi
done

MARK_OPEN="# >>> cerebro (aminmarashi) >>>"
MARK_CLOSE="# <<< cerebro (aminmarashi) <<<"

for rc in \
  "$HOME/.zshrc" \
  "$HOME/.bashrc" \
  "$HOME/.bash_profile" \
  "$HOME/.profile" \
  "$HOME/.config/fish/config.fish"
do
  [[ -f "$rc" ]] || continue
  if grep -qF "$MARK_OPEN" "$rc"; then
    tmp="$(mktemp)"
    awk -v open="$MARK_OPEN" -v close="$MARK_CLOSE" '
      index($0, open)   { skip=1; next }
      index($0, close)  { skip=0; next }
      !skip
    ' "$rc" > "$tmp" && mv "$tmp" "$rc"
    say "cleaned PATH block from $rc"
  fi
done

if [[ "$PURGE" == "1" ]]; then
  if [[ -d "$SRC/.git" ]]; then
    rm -rf "$SRC" && say "removed clone at $SRC"
  fi
else
  [[ -d "$SRC/.git" ]] && say "kept clone at $SRC (pass --purge to delete)"
fi

say "Done. Session state under \${CEREBRO_HOME:-~/.cerebro} was left untouched."
