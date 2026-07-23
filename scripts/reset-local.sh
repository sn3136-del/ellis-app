#!/usr/bin/env bash
# Tear down the stack AND delete local data (volumes + dev sqlite). Destructive.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT/backend"
echo "== resetting Ellis local data (containers + volumes + dev db) =="
docker compose down -v
rm -f ellis.db
echo "== reset complete =="
