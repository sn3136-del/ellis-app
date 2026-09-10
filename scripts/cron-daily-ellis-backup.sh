#!/bin/sh
set -eu
umask 077
exec /opt/ellis/backend/.venv/bin/python /opt/ellis/backend/scripts/backup_database.py \
  /var/lib/ellis/ellis.db "/var/backups/ellis/ellis-$(date +%u).db"
