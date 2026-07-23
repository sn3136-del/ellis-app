#!/usr/bin/env bash
# Report Ellis process + provider status without exposing any secret value.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPSUP="$HOME/Library/Application Support/Ellis"
RUN="$APPSUP/run"; LOGS="$APPSUP/logs"
PORT="${ELLIS_PORT:-8000}"; HOST="127.0.0.1"
PY="$ROOT/backend/.venv/bin/python"

echo "Ellis status"
echo "  data dir : $APPSUP"

proc() {
  local n="$1" pf="$RUN/$1.pid" pid
  if [ -f "$pf" ] && pid="$(cat "$pf" 2>/dev/null)" && [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    printf '  %-9s: running (pid %s)\n' "$n" "$pid"
  else
    printf '  %-9s: not running\n' "$n"
  fi
}
proc backend
proc worker
proc frontend

# Health + provider preflight (booleans only — never secret values).
CAPS="$(curl -fsS -m 4 -H 'Authorization: Bearer dev-token' -H 'X-Org-Id: local' -H 'X-User-Id: local' \
  "http://$HOST:$PORT/capabilities" 2>/dev/null || true)"
if [ -n "$CAPS" ] && [ -x "$PY" ]; then
  echo "  backend  : healthy on http://$HOST:$PORT"
  "$PY" - "$CAPS" <<'PYEOF' || true
import json, sys
d = json.loads(sys.argv[1])
def yn(v): return "configured" if v else "NOT configured (fail-closed)"
print("  runtime  :", d.get("runtime_mode"), "(real-only)" if d.get("runtime_mode") in
      ("local_real_services","tripcom_evaluation","staging","production") else "")
print("  providers:")
print("    kimi        :", yn(d.get("kimi")))
print("    document_ai :", yn(d.get("ocr")))
print("    browserbase :", yn(d.get("browserbase")))
print("    workflow    :", d.get("workflow_engine"))
print("    auth        :", d.get("auth"))
print("    database    :", d.get("database"))
PYEOF
else
  echo "  backend  : not reachable on http://$HOST:$PORT"
fi
echo "  logs     : $LOGS/{backend,worker,frontend}.log"
