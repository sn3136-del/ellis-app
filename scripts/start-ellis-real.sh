#!/usr/bin/env bash
# Run Ellis from source in REAL-SERVICES-ONLY mode — no .app, no packaging.
#
# Starts the FastAPI backend (127.0.0.1 only) + the durable background worker,
# runs migrations, waits for backend health, then launches the Electron/Vite
# frontend. Ctrl+C shuts every child down cleanly. Real-services-only:
# ELLIS_RUNTIME_MODE=local_real_services — never MockPortal/SyntheticPortal,
# never a simulated fee/appointment/submission/confirmation. Missing providers
# or unreleased routes fail closed with an honest error.
#
#   Main command:  npm run ellis:real
#   Backend/worker only (smoke/CI):  ELLIS_LAUNCHER_NO_FRONTEND=1 scripts/start-ellis-real.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPSUP="$HOME/Library/Application Support/Ellis"
RUN="$APPSUP/run"; LOGS="$APPSUP/logs"
PORT="${ELLIS_PORT:-8000}"; HOST="127.0.0.1"
PY="$ROOT/backend/.venv/bin/python"
ALEMBIC="$ROOT/backend/.venv/bin/alembic"
ENV_FILE="$ROOT/backend/.env"
DB="$APPSUP/ellis.db"

mkdir -p "$RUN" "$LOGS"

# Rotate logs at each launch so stale errors are never mistaken for current ones.
for f in backend worker frontend electron-main; do : > "$LOGS/$f.log" 2>/dev/null || true; done

log() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "backend venv not found at $PY (create it: cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)"

# ---- prevent duplicate instances ------------------------------------------
running() { local pf="$1"; [ -f "$pf" ] && kill -0 "$(cat "$pf" 2>/dev/null)" 2>/dev/null; }
if running "$RUN/backend.pid"; then
  die "Ellis backend already running (pid $(cat "$RUN/backend.pid")). Run: npm run ellis:stop"
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "port $PORT is already in use — stop the other process, or: npm run ellis:stop"
fi

# ---- environment ----------------------------------------------------------
# Load gitignored backend/.env safely (KEY=VALUE only; never echo values, never
# execute — `export "$line"` treats the value literally, no command substitution).
if [ -f "$ENV_FILE" ]; then
  log "loading backend/.env"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue;; esac
    [ "${line#*=}" != "$line" ] && export "$line" 2>/dev/null || true
  done < "$ENV_FILE"
else
  log "backend/.env not found — providers unconfigured; real actions will fail closed (honest)"
fi

export ELLIS_RUNTIME_MODE="local_real_services"   # REAL-ONLY: no mock/synthetic
export DATABASE_URL="sqlite:///$DB"
export ELLIS_DATA_DIR="$ROOT/data"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

echo "Starting Ellis (real services, from source)"
log "runtime mode : local_real_services"
log "backend      : http://$HOST:$PORT"
log "data dir     : $APPSUP"

# ---- migrations + full schema BEFORE starting backend/worker --------------
# Alembic tracks the migration-managed tables; create_all() then ensures the
# remaining core tables (visa_applications, applicants, …) exist too. Both run
# BEFORE the worker starts, so the worker never races a half-built schema.
log "running database migrations…"
( cd "$ROOT/backend" && "$ALEMBIC" upgrade head >>"$LOGS/backend.log" 2>&1 ) \
  || ( cd "$ROOT/backend" && "$ALEMBIC" stamp head >>"$LOGS/backend.log" 2>&1 ) \
  || log "migrations: non-fatal alembic issue; ensuring schema directly (see logs/backend.log)"
log "ensuring complete schema…"
( cd "$ROOT/backend" && "$PY" -c "from app.db import create_all; create_all()" >>"$LOGS/backend.log" 2>&1 ) \
  || die "could not initialize the database schema — see $LOGS/backend.log"

# ---- start backend + worker (127.0.0.1 only) ------------------------------
cd "$ROOT/backend"
"$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level warning \
  >>"$LOGS/backend.log" 2>&1 &
echo $! > "$RUN/backend.pid"
log "backend pid  : $(cat "$RUN/backend.pid")"

"$PY" -m app.worker >>"$LOGS/worker.log" 2>&1 &
echo $! > "$RUN/worker.pid"
log "worker pid   : $(cat "$RUN/worker.pid")"
cd "$ROOT"

# ---- clean shutdown of the whole tree -------------------------------------
_cleaned=0
kill_tree() {
  local pid="$1"; [ -n "$pid" ] || return 0
  local c
  for c in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$c"; done
  kill "$pid" 2>/dev/null || true
}
cleanup() {
  [ "$_cleaned" = "1" ] && return 0; _cleaned=1
  echo ""; echo "Shutting down Ellis…"
  local n pf pid
  for n in frontend worker backend; do
    pf="$RUN/$n.pid"
    if [ -f "$pf" ]; then
      pid="$(cat "$pf" 2>/dev/null)"
      kill_tree "$pid"
      rm -f "$pf"
      log "stopped $n"
    fi
  done
  # belt-and-suspenders: nothing left holding the port
  pkill -f "uvicorn app.main:app.*--port $PORT" 2>/dev/null || true
}
trap 'cleanup; exit 0' INT TERM
trap cleanup EXIT

# ---- wait for backend health ----------------------------------------------
log "waiting for backend health…"
healthy=0
for i in $(seq 1 40); do
  if curl -fsS -m 2 "http://$HOST:$PORT/healthz" >/dev/null 2>&1; then healthy=1; break; fi
  if ! kill -0 "$(cat "$RUN/backend.pid" 2>/dev/null)" 2>/dev/null; then
    die "backend process exited during startup — see $LOGS/backend.log"
  fi
  sleep 0.5
done
[ "$healthy" = "1" ] || die "backend did not become healthy in time — see $LOGS/backend.log"
log "backend healthy ✓"

# Confirm the worker survived its first tick (schema was ready before it ran).
if kill -0 "$(cat "$RUN/worker.pid" 2>/dev/null)" 2>/dev/null; then
  log "worker healthy ✓"
else
  echo "  worker exited early — see $LOGS/worker.log:" >&2
  tail -3 "$LOGS/worker.log" 2>/dev/null | sed 's/^/    /' >&2
  cleanup; exit 1
fi

# ---- provider preflight (booleans only; no secret values) -----------------
CAPS="$(curl -fsS -m 4 -H 'Authorization: Bearer dev-token' -H 'X-Org-Id: local' -H 'X-User-Id: local' \
  "http://$HOST:$PORT/capabilities" 2>/dev/null || true)"
if [ -n "$CAPS" ]; then
  "$PY" - "$CAPS" <<'PYEOF' || true
import json, sys
d = json.loads(sys.argv[1])
def yn(v): return "configured" if v else "NOT configured (fail-closed)"
print("  provider preflight:")
print("    runtime mode :", d.get("runtime_mode"))
print("    kimi         :", yn(d.get("kimi")))
print("    document_ai  :", yn(d.get("ocr")))
print("    browserbase  :", yn(d.get("browserbase")))
print("    workflow     :", d.get("workflow_engine"))
PYEOF
fi

if [ "${ELLIS_LAUNCHER_NO_FRONTEND:-0}" = "1" ]; then
  log "backend + worker are up; frontend skipped (ELLIS_LAUNCHER_NO_FRONTEND=1)."
  log "check: npm run ellis:status   stop: npm run ellis:stop"
  trap - EXIT   # leave services running for the caller
  exit 0
fi

# ---- frontend (Electron/Vite) — Ctrl+C here tears everything down ----------
# Pre-check: the Electron binary must exist and be executable. On this machine
# macOS security has been removing unsigned Electron binaries; if it's gone we
# report the real blocker and keep the backend + worker running (still usable
# via the API / status) instead of failing opaquely.
ELECTRON_BIN="$ROOT/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
if [ ! -x "$ELECTRON_BIN" ]; then
  echo ""
  echo "Frontend cannot start: the Electron binary is missing or not executable:"
  echo "    $ELECTRON_BIN"
  echo "  This machine's macOS security (Gatekeeper/XProtect, macOS $(sw_vers -productVersion)) removes"
  echo "  UNSIGNED Electron binaries. Repair the dev install with:"
  echo "    rm -rf node_modules/electron && npm install"
  echo "  and, if it is removed again, that is the same signing/notarization blocker"
  echo "  documented in artifacts/production_packaging_report.md — a Developer ID"
  echo "  signature is required for Electron to persist on this machine."
  echo ""
  echo "  The backend + worker are running and usable:"
  echo "    status: npm run ellis:status   stop: npm run ellis:stop"
  trap - EXIT   # keep backend/worker up for the user
  exit 0
fi

echo "Launching the Ellis desktop UI (Ctrl+C to stop everything)…"
: > "$LOGS/frontend.log"   # fresh frontend log per launch (no stale errors)
ELLIS_RUNTIME_MODE="local_real_services" npm --prefix "$ROOT" run dev >>"$LOGS/frontend.log" 2>&1 &
FRONT_PID=$!
echo "$FRONT_PID" > "$RUN/frontend.pid"
log "frontend pid : $FRONT_PID"

SECONDS=0
wait "$FRONT_PID" 2>/dev/null || true
elapsed=$SECONDS

# Distinguish an intentional quit (user closed the window) from an unexpected
# early crash — and NEVER shut down silently on a crash.
if [ "$_cleaned" != "1" ] && [ "$elapsed" -lt 20 ]; then
  echo ""
  echo "Frontend exited unexpectedly after ${elapsed}s. Actual logs:"
  echo "  --- frontend.log (tail) ---"; tail -12 "$LOGS/frontend.log" 2>/dev/null | sed 's/^/    /'
  echo "  --- electron-main.log (tail) ---"; tail -12 "$LOGS/electron-main.log" 2>/dev/null | sed 's/^/    /'
  if [ ! -x "$ELECTRON_BIN" ]; then
    echo "  DIAGNOSIS: the Electron binary was removed during launch — macOS security"
    echo "  is stripping the unsigned Electron executable (same root cause as the .app"
    echo "  packaging block). Fix requires a Developer ID signature; see artifacts/."
  fi
fi
cleanup
