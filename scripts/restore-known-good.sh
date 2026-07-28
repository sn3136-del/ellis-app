#!/usr/bin/env bash
# Bring Ellis back to a verified savepoint: the code AND the released portal
# route that makes it able to file. See RESTORE.md.
#
#   ./scripts/restore-known-good.sh [tag]      (default: known-good-2026-07-28b)
#
# Refuses to discard uncommitted work without --force, tells you honestly what
# it cannot restore (provider keys, applicant cases), and never touches the
# applicant database.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-known-good-2026-07-28b}"
[ "${1:-}" = "--force" ] && TAG="known-good-2026-07-28b"
FORCE=0
for a in "$@"; do [ "$a" = "--force" ] && FORCE=1; done

cd "$ROOT"
say() { printf '  %s\n' "$*"; }

if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "no such savepoint: $TAG"
  echo "available:"; git tag -l 'known-good-*' | sed 's/^/  /'
  exit 1
fi

DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
if [ "$DIRTY" != "0" ] && [ "$FORCE" != "1" ]; then
  echo "You have $DIRTY uncommitted change(s). Restoring would discard them."
  echo "Commit or stash first, or re-run with --force."
  exit 1
fi

echo "Restoring Ellis to $TAG"
if [ "$DIRTY" != "0" ]; then
  STASH="pre-restore-$(git rev-parse --short HEAD)"
  git stash push -u -q -m "$STASH" && say "stashed your changes as '$STASH'"
fi
git checkout -q "$TAG" 2>/dev/null || { echo "checkout failed"; exit 1; }
say "code at $(git rev-parse --short HEAD)"

# The portal route lives in the database, not in the checkout — put it back.
BUNDLES="$ROOT/backend/route_bundles"
PY="$ROOT/backend/.venv/bin/python"
if [ -d "$BUNDLES" ] && [ -x "$PY" ]; then
  export DATABASE_URL="${DATABASE_URL:-sqlite:///$HOME/Library/Application Support/Ellis/ellis.db}"
  for f in "$BUNDLES"/*.json; do
    [ -e "$f" ] || continue
    say "restoring route $(basename "$f")"
    ( cd "$ROOT/backend" && "$PY" route_bundle.py import "$f" 2>&1 | sed 's/^/    /' )
  done
else
  say "no route bundles found (or backend venv missing) — skipped"
fi

echo
echo "Restored. Two things this could NOT bring back:"
[ -f "$ROOT/backend/.env" ] \
  && say "backend/.env is present (good — it is never committed)" \
  || say "backend/.env is MISSING — recreate it from backend/.env.example with your
    Kimi / Browserbase / Google keys, or Ellis cannot reach any provider."
say "applicant cases live in ~/Library/Application Support/Ellis and are untouched."
echo
echo "Start it with:  npm run ellis:web"
