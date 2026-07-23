#!/usr/bin/env bash
# Stop the Ellis backend, worker, and (if running) the frontend started by
# start-ellis-real.sh. Kills each recorded process tree cleanly.
set -uo pipefail

APPSUP="$HOME/Library/Application Support/Ellis"
RUN="$APPSUP/run"
PORT="${ELLIS_PORT:-8000}"

kill_tree() {
  local pid="$1"; [ -n "$pid" ] || return 0
  local c
  for c in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$c"; done
  kill "$pid" 2>/dev/null || true
}

stopped=0
for n in frontend worker backend; do
  pf="$RUN/$n.pid"
  if [ -f "$pf" ]; then
    pid="$(cat "$pf" 2>/dev/null)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill_tree "$pid"
      echo "stopped $n (pid $pid)"
      stopped=1
    fi
    rm -f "$pf"
  fi
done

# Final sweep: ensure nothing Ellis-owned still holds the loopback port.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  pkill -f "uvicorn app.main:app.*--port $PORT" 2>/dev/null && { echo "stopped stray backend on :$PORT"; stopped=1; } || true
fi

[ "$stopped" = "1" ] && echo "Ellis stopped." || echo "Ellis was not running (no live PID files)."
