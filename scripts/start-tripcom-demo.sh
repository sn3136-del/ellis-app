#!/usr/bin/env bash
# Trip.com demo launcher — ONE command from a fresh clone to a running Ellis.
#
#   npm run demo        (or: ./scripts/start-tripcom-demo.sh)
#
# Self-bootstrapping and safe to re-run: installs node deps and the Python
# venv when missing, seeds the app database from the bundled demo seed on
# first run (never overwrites an existing database), then starts Ellis the
# normal way (scripts/start-ellis-web.sh: backend + worker + web app).
# Credentials ship in this repo (backend/.env + backend/google_adc.json) —
# the demo runs on the owner's provider accounts; nothing to configure.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPSUP="$HOME/Library/Application Support/Ellis"
DB="$APPSUP/ellis.db"
SEED="$ROOT/data/demo_seed/ellis-demo.db"

log() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

echo "== Ellis — Trip.com demo =="

# ---- prerequisites ---------------------------------------------------------
command -v node >/dev/null || die "Node.js 20+ is required — install from https://nodejs.org and re-run"
command -v python3 >/dev/null || die "Python 3.12+ is required — install from https://python.org and re-run"
node -e 'process.exit(parseInt(process.versions.node) >= 20 ? 0 : 1)' \
  || die "Node.js 20+ is required (found $(node --version))"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || die "Python 3.12+ is required (found $(python3 --version 2>&1))"

# ---- node dependencies -----------------------------------------------------
if [ ! -x "$ROOT/node_modules/.bin/vite" ]; then
  log "installing node dependencies (first run, a few minutes)…"
  (cd "$ROOT" && npm install --no-audit --no-fund) || die "npm install failed"
else
  log "node dependencies present"
fi

# ---- python backend venv ---------------------------------------------------
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  log "creating the backend Python environment (first run, a few minutes)…"
  (cd "$ROOT/backend" && python3 -m venv .venv \
     && ./.venv/bin/pip install -q --upgrade pip \
     && ./.venv/bin/pip install -q -r requirements.txt) \
    || die "backend environment setup failed"
else
  log "backend Python environment present"
fi

# ---- credentials sanity (they ship in the repo; just verify) ---------------
[ -f "$ROOT/backend/.env" ] || die "backend/.env missing — re-download the repo"
[ -f "$ROOT/backend/google_adc.json" ] || die "backend/google_adc.json missing — re-download the repo"

# ---- app database: seed on first run only ----------------------------------
if [ ! -f "$DB" ]; then
  mkdir -p "$APPSUP"
  cp "$SEED" "$DB"
  log "seeded the app database (released routes: Germany, Vietnam, Singapore)"
else
  log "app database already present (left untouched)"
fi

# ---- run -------------------------------------------------------------------
# Through bash: a ZIP download may have dropped the script's executable bit.
exec bash "$ROOT/scripts/start-ellis-web.sh"
