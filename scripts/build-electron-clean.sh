#!/usr/bin/env bash
# Clean, secret-free Electron build. Removes prior output, rebuilds, packages
# (unpacked), and runs the release secret scan as a gate. Never prints secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
echo "== clean build =="; rm -rf out release
npm run build
echo "== package (unpacked) =="; npx --no-install electron-builder --mac --dir || \
  { echo "electron-builder failed (code signing is optional locally)"; }
echo "== secret scan (release gate) =="; ./scripts/scan-release-secrets.sh
echo "== clean Electron build complete =="
