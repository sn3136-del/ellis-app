#!/usr/bin/env bash
# Start the full local stack (Postgres, Temporal+UI, MinIO, Mailpit, API, worker,
# mock portal) and wait until healthy. Never prints secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT/backend"
echo "== starting Ellis stack =="
docker compose up -d --build
echo "-- waiting for health (up to ~90s)"
for i in $(seq 1 30); do
  ok=$(docker compose ps --format '{{.Name}} {{.Status}}' | grep -c "healthy" || true)
  echo "   healthy services: $ok/6"
  [ "$ok" -ge 6 ] && break
  sleep 3
done
"$ROOT/scripts/health-check.sh" || true
echo "== stack up. API http://localhost:8000  Temporal UI http://localhost:8080  Mailpit http://localhost:8025 =="
echo "== run the app: npm run dev  (login admin/1234, open 'Visa Platform') =="
