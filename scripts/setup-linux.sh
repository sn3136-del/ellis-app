#!/usr/bin/env bash
# One command from nothing to a running Ellis on Ubuntu/Debian — including
# Windows via WSL (Ubuntu). Installs Node and Python if missing, then hands
# over to the normal launcher. Everything already present is left alone.
#
#   ELLIS_UNLOCK=<passphrase> bash scripts/setup-linux.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
if ! command -v node >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 \
   || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  command -v apt-get >/dev/null 2>&1 || die "this setup script is for Ubuntu/Debian (or WSL Ubuntu). Install Node 18+ and Python 3.11+, then run: npm run ellis:web"
  say "installing Node, Python and build tools (asks for your password)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq nodejs npm python3 python3-venv python3-pip curl lsof >/dev/null \
    || die "apt install failed. Install Node 18+ and Python 3.11+ yourself, then run: npm run ellis:web"
fi
say "Node $(node --version) | Python $(python3 --version 2>&1 | cut -d' ' -f2)"
exec bash "$ROOT/scripts/start-ellis-web.sh"
