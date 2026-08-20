#!/usr/bin/env bash
# The deployed backend's own startup: unlock the sealed keys, migrate, seed the
# warm cache and released routes, then serve the API. Data lives on a mounted
# volume at /data so it survives restarts and redeploys.
set -uo pipefail
cd /app
DB="/data/ellis.db"
export DATABASE_URL="sqlite:///$DB"
export ELLIS_DATA_DIR="/app/data"
mkdir -p /data

# 1) Unlock the encrypted provider keys (passphrase from the environment).
if [ ! -f backend/.env ] && [ -f backend/secrets.enc ]; then
  [ -n "${ELLIS_UNLOCK:-}" ] || { echo "FATAL: set ELLIS_UNLOCK"; exit 1; }
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "pass:$ELLIS_UNLOCK" \
    -in backend/secrets.enc | tar -C backend -xf - \
    || { echo "FATAL: wrong ELLIS_UNLOCK passphrase"; exit 1; }
  echo "provider keys unlocked"
fi
set -a; [ -f backend/.env ] && . backend/.env; set +a
[ -f backend/google_adc.json ] && export GOOGLE_APPLICATION_CREDENTIALS="/app/backend/google_adc.json"

# 2) Schema + migrations.
( cd backend && alembic upgrade head ) 2>/dev/null \
  || ( cd backend && python -c "from app.db import create_all; create_all()" )

# 3) First-boot seeding (idempotent): released routes + the warm Database cache.
ROUTES=$(python -c "from app.db import SessionLocal; from sqlalchemy import text; d=SessionLocal(); print(d.execute(text('SELECT COUNT(*) FROM portal_family_adapters WHERE released=1')).fetchone()[0]); d.close()" 2>/dev/null || echo 0)
if [ "${ROUTES:-0}" = "0" ]; then
  for f in backend/route_bundles/*.json; do
    [ -f "$f" ] && python backend/route_bundle.py import "$f" --overwrite >/dev/null 2>&1 || true
  done
fi
( cd backend && python scripts/warm_database.py import ) >/dev/null 2>&1 || true

# 4) The worker (background) + the API (foreground).
( cd backend && python -m app.worker ) &
exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
