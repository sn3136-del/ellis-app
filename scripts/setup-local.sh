#!/usr/bin/env bash
# One-time local setup for Ellis. Safe to re-run. Never prints secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
echo "== Ellis local setup =="
command -v docker >/dev/null || { echo "ERROR: Docker is required"; exit 1; }
command -v node   >/dev/null || { echo "ERROR: Node 20+ is required"; exit 1; }
command -v python3>/dev/null || { echo "ERROR: Python 3.12+ is required"; exit 1; }
echo "-- node deps"; npm install --silent
echo "-- python venv"; (cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -q -r requirements.txt)
if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; echo "-- created backend/.env from template (fill in MOONSHOT_API_KEY etc.)"; else echo "-- backend/.env already present (left untouched)"; fi
echo "== setup complete. Next: scripts/start-local.sh =="
