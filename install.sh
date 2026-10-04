#!/usr/bin/env bash
# install.sh - Install aminmarashi/cerebro by cloning the repo and symlinking
# the `cerebro` entry point onto your PATH.
#
# cerebro is a Bash CLI split across a library (lib/*.sh), so it is not a
# single copyable file. The clone stays put and `cerebro` is symlinked into a
# bin dir on your PATH; the entry point resolves the symlink back to its libs.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/aminmarashi/cerebro/main/install.sh | bash
#
# Env:
#   CEREBRO_REF       git ref to install                 (default: main)
#   CEREBRO_SRC       where to clone the source           (default: $HOME/.local/share/cerebro)
#   CEREBRO_BINDIR    dir for the `cerebro` symlink        (default: $HOME/bin)
#   CEREBRO_REPO_URL  clone URL                            (default: https://github.com/aminmarashi/cerebro.git)

set -euo pipefail

REPO_URL="${CEREBRO_REPO_URL:-https://github.com/aminmarashi/cerebro.git}"
REF="${CEREBRO_REF:-main}"
SRC="${CEREBRO_SRC:-$HOME/.local/share/cerebro}"
BINDIR="${CEREBRO_BINDIR:-$HOME/bin}"

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required"

# Clone fresh, or update an existing clone in place.
if [[ -d "$SRC/.git" ]]; then
  say "Updating existing clone at $SRC"
  git -C "$SRC" fetch --quiet origin "$REF"
  git -C "$SRC" checkout --quiet "$REF"
  git -C "$SRC" reset --hard --quiet "origin/$REF" 2>/dev/null \
    || git -C "$SRC" reset --hard --quiet "$REF"
else
  say "Cloning $REPO_URL -> $SRC"
  mkdir -p "$(dirname "$SRC")"
  git clone --quiet --branch "$REF" "$REPO_URL" "$SRC" 2>/dev/null \
    || git clone --quiet "$REPO_URL" "$SRC"
fi

chmod +x "$SRC/bin/cerebro"

mkdir -p "$BINDIR"
ln -sf "$SRC/bin/cerebro" "$BINDIR/cerebro"
say "Symlinked $BINDIR/cerebro -> $SRC/bin/cerebro"
ln -sf "$SRC/uninstall.sh" "$BINDIR/cerebro-uninstall"
say "Symlinked $BINDIR/cerebro-uninstall -> $SRC/uninstall.sh"

# Ensure BINDIR is on PATH by appending a guarded block to the user's shell rc.
detect_rc() {
  local sh="${SHELL##*/}"
  case "$sh" in
    zsh)  echo "$HOME/.zshrc" ;;
    bash)
      if [[ -f "$HOME/.bashrc" ]]; then echo "$HOME/.bashrc"
      else echo "$HOME/.bash_profile"; fi ;;
    fish) echo "$HOME/.config/fish/config.fish" ;;
    *)    echo "$HOME/.profile" ;;
  esac
}

RC="$(detect_rc)"
MARK_OPEN="# >>> cerebro (aminmarashi) >>>"
MARK_CLOSE="# <<< cerebro (aminmarashi) <<<"

case ":${PATH:-}:" in
  *":$BINDIR:"*) say "$BINDIR already on PATH" ;;
  *)
    if [[ -f "$RC" ]] && grep -qF "$MARK_OPEN" "$RC"; then
      say "PATH block already present in $RC"
    else
      mkdir -p "$(dirname "$RC")"
      {
        printf '\n%s\n' "$MARK_OPEN"
        if [[ "$RC" == *config/fish/config.fish ]]; then
          printf 'set -gx PATH %s $PATH\n' "$BINDIR"
        else
          printf 'export PATH="%s:$PATH"\n' "$BINDIR"
        fi
        printf '%s\n' "$MARK_CLOSE"
      } >> "$RC"
      say "Added $BINDIR to PATH in $RC"
      say "Open a new shell, or run: source \"$RC\""
    fi ;;
esac

say "Done. Run 'cerebro' to start (needs claude, codex, jq, python3 on PATH)."
say "Uninstall any time with: cerebro-uninstall"
