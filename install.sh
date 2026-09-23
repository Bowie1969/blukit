#!/usr/bin/env bash
# blukit installer.
#
# Installs the CLI with uv or pipx when one is available, otherwise into a
# private virtual environment under the user's data directory, then drops the
# colour theme into the Claude Code themes directory. Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${BLUKIT_CLAUDE_HOME:-$HOME/.claude}"
THEMES_DIR="$CLAUDE_HOME/themes"
VENV_DIR="${BLUKIT_VENV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/blukit/venv}"
USER_BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"

info() { printf 'blukit: %s\n' "$*"; }
fail() { printf 'blukit: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "python3 is required"

if [ "$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')" != "True" ]; then
  fail "Python 3.11 or newer is required (found $(python3 -V 2>&1))"
fi

install_with_venv() {
  info "installing into a private virtual environment at $VENV_DIR"
  python3 -m venv --clear "$VENV_DIR"
  if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    fail "the new virtual environment has no pip; install the 'python3-venv' package and re-run"
  fi
  "$VENV_DIR/bin/python" -m pip install --quiet --force-reinstall "$REPO_DIR"
  mkdir -p "$USER_BIN"
  ln -sf "$VENV_DIR/bin/blukit" "$USER_BIN/blukit"
  info "linked $USER_BIN/blukit"
}

install_cli() {
  if command -v uv >/dev/null 2>&1; then
    info "installing with uv"
    uv tool install --force "$REPO_DIR"
  elif command -v pipx >/dev/null 2>&1; then
    info "installing with pipx"
    pipx install --force "$REPO_DIR"
  elif python3 -m venv --help >/dev/null 2>&1; then
    # A venv sidesteps PEP 668, where a plain 'pip install --user' is refused
    # on externally-managed Pythons (Debian, Ubuntu, Parrot, and friends).
    install_with_venv
  else
    info "installing with pip --user"
    python3 -m pip install --user --force-reinstall --quiet "$REPO_DIR"
  fi
}

install_cli

if [ -d "$CLAUDE_HOME" ]; then
  mkdir -p "$THEMES_DIR"
  cp "$REPO_DIR/blukit/data/blukit.json" "$THEMES_DIR/blukit.json"
  info "theme installed to $THEMES_DIR/blukit.json"
  info "set  theme = \"blukit\"  in $CLAUDE_HOME/tui.toml to activate it"
else
  info "no $CLAUDE_HOME directory found — skipping the theme"
  info "install the Claude Code CLI first, then re-run this script"
fi

if command -v blukit >/dev/null 2>&1; then
  info "done — run 'blukit doctor' to verify"
else
  info "installed, but 'blukit' is not on your PATH"
  info "add your user bin directory (usually ~/.local/bin) to PATH and re-run 'blukit doctor'"
fi
