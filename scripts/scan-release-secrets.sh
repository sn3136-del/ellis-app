#!/usr/bin/env bash
# Scan built Electron artifacts for leaked secrets / private data before release.
# Extracts every app.asar and scans it plus the whole release tree. Prints ONLY
# a redacted fingerprint for any hit (never the secret value). Exits non-zero if
# anything is found — safe to run in CI as a release gate.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_DIR="${1:-$ROOT/release}"
FINDINGS=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Forbidden file basenames (credentials / private data)
FORBIDDEN_NAMES='(^|/)(kimi\.key|.*\.key|.*\.pem|.*\.p12|.*\.pfx|id_rsa.*|\.env|\.env\..*|application_default_credentials\.json|service-account.*\.json|.*\.passport|fixture_(us|sg|cn|pdf).*)$'
# Secret VALUE patterns (high-signal)
VALUE_PATTERNS='sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY|"private_key"[[:space:]]*:[[:space:]]*"-----BEGIN|MOONSHOT_API_KEY=[^[:space:]]|BROWSERBASE_API_KEY=[^[:space:]]|browserbase\.com/v1/sessions/[A-Za-z0-9-]+/(live|debug)'

report() { # path category
  FINDINGS=$((FINDINGS+1))
  local fp
  fp="$(printf '%s|%s' "$1" "$2" | shasum -a 256 | cut -c1-12)"
  echo "  FINDING: category=$2 path=${1#$ROOT/} fingerprint=$fp"
}

if [ ! -d "$RELEASE_DIR" ]; then
  echo "No release dir at $RELEASE_DIR — nothing to scan (ok)."; exit 0
fi

echo "Scanning $RELEASE_DIR ..."

# 1) Extract each app.asar so we can scan bundled contents.
SCAN_ROOTS=("$RELEASE_DIR")
i=0
while IFS= read -r asar; do
  dest="$TMP/asar_$i"; i=$((i+1))
  if npx --no-install asar extract "$asar" "$dest" >/dev/null 2>&1; then
    SCAN_ROOTS+=("$dest")
  else
    # Fallback: raw byte scan for the forbidden 'kimi.key' entry name.
    grep -aqE 'kimi\.key' "$asar" && report "$asar" "asar-bundles-kimi.key"
  fi
done < <(find "$RELEASE_DIR" -name 'app.asar' 2>/dev/null)

# 2) Forbidden filenames across release + extracted asars (skip node_modules).
for r in "${SCAN_ROOTS[@]}"; do
  while IFS= read -r f; do
    base="$(basename "$f")"
    case "$base" in
      *credentials.ts|*credentials.js|*credentials.mjs|*credentials.d.ts) continue;; # SDK source, not a secret
    esac
    if echo "$f" | grep -qE "$FORBIDDEN_NAMES"; then report "$f" "forbidden-file"; fi
  done < <(find "$r" -type f -not -path '*/node_modules/*' 2>/dev/null)
done

# 3) Secret VALUE patterns in text files (skip node_modules + binaries).
for r in "${SCAN_ROOTS[@]}"; do
  while IFS= read -r f; do
    grep -aIlEm1 "$VALUE_PATTERNS" "$f" >/dev/null 2>&1 && report "$f" "secret-value-pattern"
  done < <(find "$r" -type f -not -path '*/node_modules/*' \
             \( -name '*.js' -o -name '*.mjs' -o -name '*.json' -o -name '*.map' -o -name '*.html' -o -name '*.txt' -o -name '*.env*' \) 2>/dev/null)
done

echo ""
if [ "$FINDINGS" -eq 0 ]; then
  echo "CLEAN: no secrets or private data found in $RELEASE_DIR"
  exit 0
else
  echo "FAILED: $FINDINGS finding(s) — remediate before release (values not printed)."
  exit 1
fi
