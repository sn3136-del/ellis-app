#!/usr/bin/env bash
# One command from nothing to a running Ellis, on a Mac.
#
#   bash scripts/setup-mac.sh
#
# Installs what is missing (Homebrew, Node, a modern Python), builds the
# backend environment with a Python new enough for the codebase, and hands
# over to the normal launcher. Everything already present is left alone.
#
# Honest about the one thing it cannot do silently: installing Homebrew asks
# for the Mac's password, because it writes outside the home folder. If the
# tester would rather not, the script says exactly what to install by hand.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this setup script is for macOS; on Linux install node 18+ and python 3.11+, then run: npm run ellis:web"

# --- Node ------------------------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
  say "Node is not installed — installing it"
  if ! command -v brew >/dev/null 2>&1; then
    say "installing Homebrew first (it will ask for your Mac password)"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      || die "Homebrew install did not finish. Install Node yourself from https://nodejs.org (LTS), then run: npm run ellis:web"
    # Apple silicon puts brew outside the default PATH for this shell.
    [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
  fi
  brew install node || die "could not install Node. Install it from https://nodejs.org (LTS), then run: npm run ellis:web"
fi
say "Node $(node --version)"

# --- Python (3.11+; the codebase uses modern syntax) -----------------------
pick_python() {
  for c in python3.14 python3.13 python3.12 python3.11 python3; do
    p="$(command -v $c 2>/dev/null)" || continue
    v="$("$p" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null)" || continue
    [ "${v:-0}" -ge 311 ] && { echo "$p"; return 0; }
  done
  return 1
}
PYBIN="$(pick_python)" || {
  say "no Python 3.11+ found — installing one"
  command -v brew >/dev/null 2>&1 || die "Homebrew is needed to install Python. Install Python 3.12 from https://python.org, then run: npm run ellis:web"
  brew install python@3.12 || die "could not install Python. Install 3.12 from https://python.org, then run: npm run ellis:web"
  PYBIN="$(pick_python)" || die "Python 3.11+ still not found after install"
}
say "Python $("$PYBIN" --version 2>&1 | awk '{print $2}')"

# --- Backend environment ---------------------------------------------------
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  say "building the backend environment (a minute or two)"
  "$PYBIN" -m venv "$ROOT/backend/.venv" || die "could not create the backend environment"
  "$ROOT/backend/.venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt" \
    || die "could not install backend dependencies — see the output above"
fi

# --- Frontend dependencies -------------------------------------------------
if [ ! -x "$ROOT/node_modules/.bin/vite" ]; then
  say "installing frontend dependencies"
  (cd "$ROOT" && npm install --silent) || die "npm install failed"
fi

say "starting Ellis"
exec bash "$ROOT/scripts/start-ellis-web.sh"
