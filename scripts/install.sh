#!/bin/sh
# Ace installer for macOS and Linux. Installs a pinned version, is safe to run twice, never asks for keys or sudo.
# One line:  curl -fsSL https://raw.githubusercontent.com/the-X-alien/ace-agent/v0.1.4/scripts/install.sh | sh
# Remove:    ACE_UNINSTALL=1 sh install.sh
set -eu
REF="${ACE_REF:-v0.1.4}"
SOURCE="${ACE_SOURCE:-https://github.com/the-X-alien/ace-agent/archive/refs/tags/${REF}.zip}"
HOME_DIR="${ACE_HOME:-$HOME/.ace-agent}"
BIN="${ACE_BIN:-$HOME/.local/bin}"
MARK="# added by the Ace installer"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

if [ -n "${ACE_UNINSTALL:-}" ]; then
  rm -rf "$HOME_DIR"; rm -f "$BIN/ace-agent"; [ "$(readlink "$BIN/ace" 2>/dev/null)" = "$HOME_DIR/venv/bin/ace" ] && rm -f "$BIN/ace"
  for f in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
    [ -f "$f" ] && grep -q "$MARK" "$f" && { grep -v "$MARK" "$f" > "$f.ace-tmp" && cat "$f.ace-tmp" > "$f"; rm -f "$f.ace-tmp"; }
  done
  say "Ace removed. Open a new terminal for PATH to refresh."; exit 0
fi

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "Python 3.9 or newer is needed. macOS: install from https://www.python.org/downloads/ or run 'xcode-select --install'. Linux: install python3 with your package manager. Then run this command again."

say "Installing Ace ${REF} into ${HOME_DIR}"
"$PY" -m venv "$HOME_DIR/venv" || fail "could not create a virtual environment (on Debian or Ubuntu: sudo apt install python3-venv)"
"$HOME_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$HOME_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check --force-reinstall "$SOURCE" || fail "pip could not install Ace from $SOURCE. Check your internet connection and try again."
mkdir -p "$BIN"
ln -sf "$HOME_DIR/venv/bin/ace-agent" "$BIN/ace-agent"
OTHER="$(command -v ace 2>/dev/null || true)"
if [ -z "$OTHER" ] || [ "$OTHER" = "$BIN/ace" ]; then ln -sf "$HOME_DIR/venv/bin/ace" "$BIN/ace"; else say "Note: a different program named ace already exists at $OTHER. Ace did not change it; use ace-agent to run this Ace."; fi

case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    # Add to the profile of the shell(s) you use, once.
    for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
      case "$f" in
        */.zshrc) [ -f "$f" ] || [ "${SHELL##*/}" = "zsh" ] || continue ;;
        */.bashrc) [ -f "$f" ] || [ "${SHELL##*/}" = "bash" ] || continue ;;
      esac
      grep -q "$MARK" "$f" 2>/dev/null || printf 'export PATH="%s:$PATH" %s\n' "$BIN" "$MARK" >> "$f"
    done
    [ -f "$HOME/.profile" ] && { grep -q "$MARK" "$HOME/.profile" || printf 'export PATH="%s:$PATH" %s\n' "$BIN" "$MARK" >> "$HOME/.profile"; }
    NEWPATH=1 ;;
esac

PATH="$BIN:$PATH" ace-agent --version
if [ "${NEWPATH:-}" = 1 ]; then
  say "Done. Open a new terminal (or run: export PATH=\"$BIN:\$PATH\") and type: ace-agent doctor"
else
  say "Done. Type: ace-agent doctor"
fi
