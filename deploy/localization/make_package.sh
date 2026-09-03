#!/usr/bin/env bash
# Assemble the Trip.com local installation zip.
#
#   deploy/localization/make_package.sh <snapshot-dir> [out-dir]
#
# <snapshot-dir> holds ellis.db (a sanitized export of the four database
# tables) and operator_overrides.json from the live server. The frontend must
# already be built with `npx vite build --config vite.web.config.mjs`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SNAP="${1:?snapshot dir with ellis.db and operator_overrides.json}"
OUT="${2:-$ROOT/.package}"
NAME="ellis-local-$(date +%F)"
STAGE="$OUT/$NAME"

[ -f "$ROOT/src/renderer/dist/index.html" ] || { echo "build the frontend first"; exit 1; }
[ -f "$SNAP/ellis.db" ] || { echo "no ellis.db in $SNAP"; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE/backend" "$STAGE/snapshot"
cp "$ROOT"/deploy/localization/package/{Dockerfile,docker-compose.yml,Caddyfile,README.md} "$STAGE/"
rsync -a --exclude '__pycache__' --exclude '.DS_Store' "$ROOT/backend/app" "$STAGE/backend/"
cp "$ROOT/backend/requirements.txt" "$STAGE/backend/"
rsync -a --exclude '.DS_Store' "$ROOT/data" "$STAGE/"
rsync -a "$ROOT/src/renderer/dist/" "$STAGE/dist/"
cp "$SNAP/ellis.db" "$STAGE/snapshot/ellis.db"
cp "$SNAP/operator_overrides.json" "$STAGE/snapshot/operator_overrides.json"
printf 'Snapshot of ellis-visa.com taken %s UTC\n' "$(date -u +'%Y-%m-%d %H:%M')" > "$STAGE/snapshot/SNAPSHOT.txt"

# Never ship a secret: the package must contain no .env and no key-shaped strings.
if find "$STAGE" -name '.env' | grep -q . ; then echo "refusing: .env inside package"; exit 1; fi
if grep -rIlE 'sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}' "$STAGE" >/dev/null; then
  echo "refusing: key-shaped string inside package"; exit 1
fi

( cd "$OUT" && rm -f "$NAME.zip" && zip -qr "$NAME.zip" "$NAME" )
echo "wrote $OUT/$NAME.zip"
du -sh "$OUT/$NAME.zip"
