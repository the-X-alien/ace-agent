#!/bin/sh
# Ace installer for macOS and Linux. Installs a pinned version, is safe to run twice, and never asks for keys.
# Usage: ACE_REF=v0.1.1 sh install.sh        (ACE_SOURCE overrides the install source, used for testing)
set -eu
REF="${ACE_REF:-v0.1.1}"
SOURCE="${ACE_SOURCE:-git+https://github.com/the-X-alien/ace-agent@${REF}}"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "Python 3.9 or newer is required. Install it from https://www.python.org/downloads/ and run this again."

if command -v pipx >/dev/null 2>&1; then
  say "Installing ace-agent with pipx from ${SOURCE}"
  pipx install --force "$SOURCE"
else
  say "pipx not found, using a private virtual environment at ${ACE_HOME:-$HOME/.ace-agent}"
  HOME_DIR="${ACE_HOME:-$HOME/.ace-agent}"
  "$PY" -m venv "$HOME_DIR/venv" || fail "could not create a virtual environment (on Debian or Ubuntu: sudo apt install python3-venv)"
  "$HOME_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
  "$HOME_DIR/venv/bin/python" -m pip install --quiet --force-reinstall "$SOURCE"
  BIN="${ACE_BIN:-$HOME/.local/bin}"
  mkdir -p "$BIN"
  ln -sf "$HOME_DIR/venv/bin/ace" "$BIN/ace"
  case ":$PATH:" in *":$BIN:"*) ;; *) say "Add this to your shell profile so 'ace' is found: export PATH=\"$BIN:\$PATH\"" ;; esac
fi
say "Done. Check it with: ace --version  then: ace doctor"
