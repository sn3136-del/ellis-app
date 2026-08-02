#!/usr/bin/env bash
# Publish every RELEASED route from the working database into the one the
# running app serves.
#
# Ellis keeps two databases: backend/ellis.db, where the orchestrator builds
# and releases adapters, and ~/Library/Application Support/Ellis/ellis.db,
# which the launcher actually serves. A route released by a build is invisible
# to the app until it is published here — the app showed only Vietnam while
# eight more were released and working.
#
#   npm run routes:sync
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"
APPDB="$HOME/Library/Application Support/Ellis/ellis.db"

[ -x "$PY" ] || { echo "error: backend venv missing at $PY" >&2; exit 1; }
[ -f "$APPDB" ] || { echo "error: app database not found at $APPDB (start Ellis once first)" >&2; exit 1; }

cd "$ROOT/backend"
echo "  exporting released routes from the working database…"
"$PY" route_bundle.py export >/dev/null || { echo "error: export failed" >&2; exit 1; }

BAK="$APPDB.bak-$(date +%Y%m%d-%H%M%S)"
cp "$APPDB" "$BAK"
echo "  backed up the app database -> $(basename "$BAK")"

n=0
for f in route_bundles/*.json; do
  [ -f "$f" ] || continue
  if DATABASE_URL="sqlite:///$APPDB" "$PY" route_bundle.py import "$f" --overwrite >/dev/null 2>&1; then
    echo "    published $(basename "$f" .json)"
    n=$((n+1))
  else
    echo "    FAILED    $(basename "$f" .json)" >&2
  fi
done

DATABASE_URL="sqlite:///$APPDB" "$PY" - <<'PYEOF'
from app.db import SessionLocal
from sqlalchemy import text
db = SessionLocal()
rel = db.execute(text("SELECT COUNT(*) FROM portal_family_adapters WHERE released=1")).fetchone()[0]
act = db.execute(text("SELECT COUNT(*) FROM adapter_runtime_bindings WHERE active=1")).fetchone()[0]
print(f"\n  the app now serves {rel} released route(s), {act} active binding(s)")
for (f,) in db.execute(text("SELECT family_id FROM portal_family_adapters WHERE released=1 ORDER BY family_id")).fetchall():
    print(f"    {f}")
db.close()
PYEOF
echo "  restart Ellis to pick them up:  npm run ellis:stop && npm run ellis:web"
