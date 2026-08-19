#!/usr/bin/env bash
# Run Ellis from source as a LOCAL WEB APP in the default browser — no Electron.
#
# Starts the FastAPI backend (127.0.0.1) + the durable worker + migrations, waits
# for backend health AND worker liveness, then serves the renderer with Vite on
# 127.0.0.1 and opens the browser. Real-services-only
# (ELLIS_RUNTIME_MODE=local_real_services): never MockPortal/SyntheticPortal,
# never a simulated fee/appointment/submission/confirmation; missing providers or
# unreleased routes fail closed with an honest error. Electron is never imported,
# spawned, signed, or executed. Ctrl+C shuts everything down cleanly.
#
#   Command:  npm run ellis:web
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPSUP="$HOME/Library/Application Support/Ellis"
RUN="$APPSUP/run"; LOGS="$APPSUP/logs"
PORT="${ELLIS_PORT:-8000}"; HOST="127.0.0.1"
WEB_PORT="${ELLIS_WEB_PORT:-5199}"
PY="$ROOT/backend/.venv/bin/python"
ALEMBIC="$ROOT/backend/.venv/bin/alembic"
VITE="$ROOT/node_modules/.bin/vite"
ENV_FILE="$ROOT/backend/.env"
DB="$APPSUP/ellis.db"

mkdir -p "$RUN" "$LOGS"

# The provider keys travel ENCRYPTED (backend/secrets.enc, AES-256). The
# passphrase comes from the owner, not the repo — enter it once here (or
# set ELLIS_UNLOCK) and the plaintext lives only on this machine.
if [ ! -f "$ENV_FILE" ] && [ -f "$ROOT/backend/secrets.enc" ]; then
  if [ -n "$ELLIS_UNLOCK" ]; then PASS="$ELLIS_UNLOCK"; else
    printf 'Enter the unlock passphrase (from the owner): '
    read -rs PASS; printf '\n'
  fi
  if openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "pass:$PASS" \
       -in "$ROOT/backend/secrets.enc" | tar -C "$ROOT/backend" -xf -; then
    printf '  %s\n' "provider keys unlocked"
  else
    printf 'error: wrong passphrase — ask the owner for the unlock phrase\n' >&2
    exit 1
  fi
fi

# Fresh clone: build what is missing, so download -> run is one command.
if [ ! -x "$PY" ]; then
  printf '  %s\n' "first run: creating the backend environment"
  python3 -m venv "$ROOT/backend/.venv" \
    && "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt" \
    || { printf 'error: could not build the backend environment\n' >&2; exit 1; }
fi
if [ ! -x "$VITE" ]; then
  printf '  %s\n' "first run: installing frontend dependencies"
  (cd "$ROOT" && npm install --silent) \
    || { printf 'error: npm install failed\n' >&2; exit 1; }
fi
for f in backend worker web; do : > "$LOGS/$f.log" 2>/dev/null || true; done

log() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "backend venv not found at $PY"
[ -x "$VITE" ] || die "vite not found at $VITE (run: npm install)"

# ---- prevent duplicate instances ------------------------------------------
running() { local pf="$1"; [ -f "$pf" ] && kill -0 "$(cat "$pf" 2>/dev/null)" 2>/dev/null; }
if running "$RUN/backend.pid"; then die "Ellis backend already running (pid $(cat "$RUN/backend.pid")). Run: npm run ellis:stop"; fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then die "port $PORT already in use — stop it, or: npm run ellis:stop"; fi
if lsof -nP -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then die "web port $WEB_PORT already in use — stop it, or: npm run ellis:stop"; fi

# ---- environment ----------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  log "loading backend/.env"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue;; esac
    [ "${line#*=}" != "$line" ] && export "$line" 2>/dev/null || true
  done < "$ENV_FILE"
else
  log "backend/.env not found — providers unconfigured; real actions fail closed (honest)"
fi
# Demo portability (2026-08-04): the Google credential ships in the repo so
# the Trip.com laptop needs no cloud setup — point at it wherever the repo
# lives (the .env's absolute path only exists on the authoring machine).
if [ -f "$ROOT/backend/google_adc.json" ]; then
  export GOOGLE_APPLICATION_CREDENTIALS="$ROOT/backend/google_adc.json"
fi
export ELLIS_RUNTIME_MODE="local_real_services"
export DATABASE_URL="sqlite:///$DB"
export ELLIS_DATA_DIR="$ROOT/data"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

echo "Starting Ellis (real services, web mode, from source — no Electron)"
log "runtime mode : local_real_services"
log "backend      : http://$HOST:$PORT"
log "web app      : http://$HOST:$WEB_PORT"

# ---- migrations + complete schema BEFORE backend/worker -------------------
log "running database migrations…"
( cd "$ROOT/backend" && "$ALEMBIC" upgrade head >>"$LOGS/backend.log" 2>&1 ) \
  || ( cd "$ROOT/backend" && "$ALEMBIC" stamp head >>"$LOGS/backend.log" 2>&1 ) \
  || log "migrations: non-fatal alembic issue; ensuring schema directly"
log "ensuring complete schema…"
( cd "$ROOT/backend" && "$PY" -c "from app.db import create_all; create_all()" >>"$LOGS/backend.log" 2>&1 ) \
  || die "could not initialize the database schema — see $LOGS/backend.log"

# ---- backend + worker (127.0.0.1 only) ------------------------------------
cd "$ROOT/backend"
"$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level warning >>"$LOGS/backend.log" 2>&1 &
echo $! > "$RUN/backend.pid"; log "backend pid  : $(cat "$RUN/backend.pid")"
"$PY" -m app.worker >>"$LOGS/worker.log" 2>&1 &
echo $! > "$RUN/worker.pid"; log "worker pid   : $(cat "$RUN/worker.pid")"
cd "$ROOT"

# ---- clean shutdown of the whole tree -------------------------------------
_cleaned=0
kill_tree() { local pid="$1"; [ -n "$pid" ] || return 0; local c
  for c in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$c"; done; kill "$pid" 2>/dev/null || true; }
cleanup() {
  [ "$_cleaned" = "1" ] && return 0; _cleaned=1
  echo ""; echo "Shutting down Ellis…"
  local n pf pid
  for n in web worker backend; do
    pf="$RUN/$n.pid"
    if [ -f "$pf" ]; then pid="$(cat "$pf" 2>/dev/null)"; kill_tree "$pid"; rm -f "$pf"; log "stopped $n"; fi
  done
  pkill -f "uvicorn app.main:app.*--port $PORT" 2>/dev/null || true
  pkill -f "vite --config $ROOT/vite.web.config.mjs" 2>/dev/null || true
}
trap 'cleanup; exit 0' INT TERM
trap cleanup EXIT

# ---- wait for backend health + worker liveness ----------------------------
log "waiting for backend health…"
healthy=0
for i in $(seq 1 40); do
  curl -fsS -m 2 "http://$HOST:$PORT/healthz" >/dev/null 2>&1 && { healthy=1; break; }
  kill -0 "$(cat "$RUN/backend.pid" 2>/dev/null)" 2>/dev/null || die "backend exited during startup — see $LOGS/backend.log"
  sleep 0.5
done
[ "$healthy" = "1" ] || die "backend did not become healthy — see $LOGS/backend.log"
log "backend healthy ✓"
kill -0 "$(cat "$RUN/worker.pid" 2>/dev/null)" 2>/dev/null \
  && log "worker healthy ✓" \
  || { echo "  worker exited early — see $LOGS/worker.log:" >&2; tail -3 "$LOGS/worker.log" | sed 's/^/    /' >&2; cleanup; exit 1; }

# ---- provider preflight (booleans only; no secret values) -----------------
CAPS="$(curl -fsS -m 4 -H 'Authorization: Bearer dev-token' -H 'X-Org-Id: local' -H 'X-User-Id: local' "http://$HOST:$PORT/capabilities" 2>/dev/null || true)"
[ -n "$CAPS" ] && "$PY" - "$CAPS" <<'PYEOF' || true
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

# ---- Vite web server (127.0.0.1) — NO Electron ----------------------------
echo "Starting the Ellis web app (Vite, 127.0.0.1 only)…"
"$VITE" --config "$ROOT/vite.web.config.mjs" >>"$LOGS/web.log" 2>&1 &
WEB_PID=$!; echo "$WEB_PID" > "$RUN/web.pid"; log "web pid      : $WEB_PID"
echo "$WEB_PID" > "$RUN/frontend.pid"   # so ellis:stop/status also cover it

log "waiting for the web server…"
web_ok=0
for i in $(seq 1 40); do
  curl -fsS -m 2 "http://$HOST:$WEB_PORT/" >/dev/null 2>&1 && { web_ok=1; break; }
  kill -0 "$WEB_PID" 2>/dev/null || { echo "  web server exited — see $LOGS/web.log:" >&2; tail -8 "$LOGS/web.log" | sed 's/^/    /' >&2; cleanup; exit 1; }
  sleep 0.5
done
[ "$web_ok" = "1" ] || { echo "web server did not become ready — see $LOGS/web.log" >&2; cleanup; exit 1; }
log "web server ready ✓"

URL="http://$HOST:$WEB_PORT/"
echo ""
echo "Ellis is running in your browser:  $URL"
echo "  (Ctrl+C to stop backend + worker + web server)"
command -v open >/dev/null 2>&1 && open "$URL" 2>/dev/null || log "open your browser to $URL"

wait "$WEB_PID" 2>/dev/null || true
cleanup
