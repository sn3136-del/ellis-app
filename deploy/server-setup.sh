#!/usr/bin/env bash
# One command from a fresh Ubuntu server to a running, password-gated Ellis.
#
#   bash deploy/server-setup.sh
#
# Asks for the two secrets (the ELLIS_UNLOCK passphrase and the site password
# for Trip.com), installs Docker if missing, builds, and starts. Run it again
# any time to redeploy after a git pull.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "installing Docker"
  curl -fsSL https://get.docker.com | sh
fi

read -rsp "ELLIS_UNLOCK passphrase: " ELLIS_UNLOCK; echo
read -rsp "Site password to give Trip.com: " SITE_PASSWORD; echo
read -rp  "Domain (leave empty to serve on the plain IP): " SITE_DOMAIN

HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$SITE_PASSWORD")
SITE="${SITE_DOMAIN:-:80}"

cat > deploy/.env <<ENV
ELLIS_UNLOCK=$ELLIS_UNLOCK
ELLIS_PASSWORD_HASH=$HASH
ELLIS_SITE=$SITE
ENV
chmod 600 deploy/.env

docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
echo
echo "Ellis is deploying. In ~1 minute it serves at:"
if [ -n "$SITE_DOMAIN" ]; then echo "  https://$SITE_DOMAIN  (user: tripcom)"; else
  echo "  http://$(curl -s ifconfig.me || echo YOUR_SERVER_IP)/  (user: tripcom)"; fi
