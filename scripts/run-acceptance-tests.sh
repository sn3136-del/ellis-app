#!/usr/bin/env bash
# Run the automated acceptance suite. Assumes the stack is up (start-local.sh).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"; rc=0
echo "== 1/4 frontend + build-security + visa-logic (npm test) =="; npm test || rc=1
echo "== 2/4 backend hermetic suite =="
(cd backend && . .venv/bin/activate && python -m pytest tests/test_unit.py tests/test_e2e.py tests/test_signature.py tests/test_discovery.py tests/test_tripcom.py tests/test_temporal.py tests/test_privacy.py tests/test_adapters_admin.py -q) || rc=1
echo "== 3/4 frontend->backend lifecycle (live) =="; node --test tests/visa/frontend_backend.test.mjs || rc=1
echo "== 4/4 release secret scan (if a build exists) =="; ./scripts/scan-release-secrets.sh || rc=1
[ "$rc" -eq 0 ] && echo "== ACCEPTANCE: PASS ==" || echo "== ACCEPTANCE: FAILURES (see above) =="
exit $rc
