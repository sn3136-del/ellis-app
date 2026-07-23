#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"; echo "== stopping Ellis stack (volumes kept) =="; docker compose stop
