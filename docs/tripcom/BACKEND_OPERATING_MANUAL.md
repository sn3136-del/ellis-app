# Backend Operating Manual — ellis-visa.com

## Topology
Hetzner CX23 (Falkenstein). Caddy terminates TLS for ellis-visa.com and
serves the static app from /opt/ellis/src/renderer/dist; /api/* is proxied
to the FastAPI backend (systemd unit ellis-backend) on 127.0.0.1:8000.
Data: SQLite at /var/lib/ellis/ellis.db.

## Routine operations
- Status: systemctl status ellis-backend caddy
- Logs: journalctl -u ellis-backend -f (backend), -u caddy (edge)
- Deploy an update: cd /opt/ellis && git fetch origin h1b-edition &&
  git reset --hard origin/h1b-edition && npx vite build --config
  vite.web.config.mjs && systemctl restart ellis-backend
- Health: GET https://ellis-visa.com/api/healthz -> {"ok": true}

## Data protection
Daily full database backup (cron.daily/ellis-backup) to
/var/backups/ellis/ellis-<weekday>.db, seven-day rotation. Restore:
stop ellis-backend, copy the chosen backup over /var/lib/ellis/ellis.db,
start ellis-backend. RTO: minutes (systemd restarts automatically on
failure; Restart=always, RestartSec=3).

## Quality-control backend
https://ellis-visa.com/#ops - records with combined spot-check filters
(names, codes and Chinese accepted), per-record 25-field checklists,
confidence and source-substantiation chips, one-click error flagging into
the tracked correction queue, the change log, freshness, and the two-sheet
Excel export.

## Security posture
UFW allows 22/80/443 only. Backend binds loopback; only Caddy is exposed.
Secrets ship encrypted (backend/secrets.enc, AES-256-CBC) and are decrypted
on the machine at deploy time; backend/.env never enters git.
