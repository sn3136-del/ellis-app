#!/usr/bin/env bash
# Verify every local service answers. Exits non-zero if any is down.
set -uo pipefail
fail=0
chk() { printf "  %-14s " "$1:"; if curl -fsS -o /dev/null --max-time 5 "$2"; then echo "OK"; else echo "DOWN"; fail=1; fi; }
echo "== Ellis health =="
chk "api"         "http://localhost:8000/healthz"
chk "api-ready"   "http://localhost:8000/readyz"
chk "mock-portal" "http://localhost:8090/healthz"
chk "temporal-ui" "http://localhost:8080/"
chk "mailpit"     "http://localhost:8025/"
chk "minio"       "http://localhost:9000/minio/health/live"
[ "$fail" -eq 0 ] && echo "== all healthy ==" || { echo "== some services down =="; exit 1; }
