#!/usr/bin/env bash
# Build the SLIM Python runtime that the self-contained Electron app bundles.
#
# The packaged Ellis app embeds this runtime + the backend source + reference
# data (see package.json build.extraResources) and spawns uvicorn on launch
# (src/main/backendService.js). We derive the slim runtime from the working
# dev venv (backend/.venv) so it reuses the exact wheels the test suite passes
# against, then prune the deps not needed at runtime in local_mock_demo mode:
#   playwright  (live browser; local mode uses MockPortal)
#   temporalio  (durable workers; local mode uses the DB runner)
#   psycopg     (PostgreSQL; local mode uses SQLite)
#   pip, pytest (build/test only)
#
# The interpreter symlink is dereferenced into a real binary so electron-builder
# copies it cleanly. NOTE: the runtime still relies on the base CPython 3.14
# framework (/Library/Frameworks/Python.framework/Versions/3.14) for its stdlib,
# so the bundle is self-contained on a machine that has that framework. A fully
# portable (framework-free) build would use PyInstaller — a further step.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/backend/.venv"
DST="$ROOT/backend/.runtime-venv"
PYVER="python3.14"
FRAMEWORK="/Library/Frameworks/Python.framework/Versions/3.14/bin/$PYVER"

[ -d "$SRC" ] || { echo "error: dev venv not found at $SRC (create it first)"; exit 1; }

echo "Staging slim runtime venv from $SRC ..."
rm -rf "$DST"
cp -R "$SRC" "$DST"

SP="$DST/lib/$PYVER/site-packages"
( cd "$SP" && rm -rf \
    playwright pyee playwright-*.dist-info \
    temporalio temporalio-*.dist-info \
    psycopg psycopg_binary psycopg-*.dist-info psycopg_binary-*.dist-info \
    pip pip-*.dist-info \
    _pytest pytest pytest-*.dist-info pytest_asyncio pytest_asyncio-*.dist-info \
    2>/dev/null || true )
find "$DST" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
rm -f "$DST"/bin/pip* "$DST"/bin/playwright "$DST"/bin/pytest 2>/dev/null || true

# Dereference the interpreter symlink so packaging copies a real binary.
if [ -L "$DST/bin/$PYVER" ]; then
  rm "$DST/bin/$PYVER"
  cp "$FRAMEWORK" "$DST/bin/$PYVER"
  chmod +x "$DST/bin/$PYVER"
fi

# Smoke test: the slim runtime must import the FastAPI app (cwd = backend/ so
# the `app` package is importable, matching how the app spawns uvicorn).
( cd "$ROOT/backend" && ELLIS_DATA_DIR="$ROOT/data" "$DST/bin/$PYVER" \
    -c "import fastapi, uvicorn, sqlalchemy, app.main" ) \
  && echo "OK: slim runtime imports the backend ($(du -sh "$DST" | cut -f1))" \
  || { echo "error: slim runtime failed to import the backend"; exit 1; }
