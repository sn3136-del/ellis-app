# Availability and Recovery (acceptance metrics 6.1: availability, RTO)

## How availability is measured

A cron on the production server probes the full public path once per minute:
DNS, TLS, the Caddy edge, and the backend behind it, via
`https://ellis-visa.com/api/health`. A failed probe retries once after two
seconds before it counts as downtime, so a single dropped packet does not
register as an outage. Every probe is appended to a monthly CSV under
`/var/lib/ellis/uptime/` with its timestamp, HTTP code and latency.

The record is visible live, to both parties, at:

    https://ellis-visa.com/api/health/uptime

which reports per month: probes taken, probes OK, availability percent, and
median latency, plus the count of logged incidents. On the server,
`ellis-uptime-report` prints the same summary plus the incident log.

Honest limitation: the prober runs on the production host, so it exercises
the whole serving stack but cannot observe a failure of the host's own
network uplink from outside. An external vantage point requires a
third-party monitoring account, which the owner can add at any time; the
in-host record remains the primary evidence either way.

## How the service self-heals

Two layers, both automatic:

1. systemd: `Restart=always` with `RestartSec=3`. A crashed backend process
   is back in seconds; measured restart-to-healthy is under 10 seconds.
2. The minute probe: when the public path fails but the incident is the
   backend (a hung process rather than a crashed one), the probe restarts
   `ellis-backend` itself and logs the action to
   `/var/lib/ellis/uptime/incidents.log`. Nothing is restarted while the
   backend answers locally: an edge or network incident is logged, not
   "fixed" by restarting a healthy process.

## Recovery runbook (RTO target: 1 hour)

Worst case, a destroyed server:

1. Provision a replacement host, point the `ellis-visa.com` A record at it
   (Cloudflare, TTL is minutes).
2. `git clone` the repository into `/opt/ellis`; decrypt `secrets.enc` into
   `backend/.env` with the owner-held passphrase.
3. Restore the newest database backup from `/var/backups/ellis/` (a daily
   cron; copy the latest off-host after every acceptance milestone) to
   `/var/lib/ellis/ellis.db`.
4. Install the systemd unit and Caddyfile from this repository's deploy
   notes, `systemctl enable --now ellis-backend caddy`.
5. `npx vite build --config vite.web.config.mjs` for the web bundle.

Measured on the current host, steps 2 to 5 complete in well under 30
minutes; DNS propagation dominates. Data loss window is bounded by the daily
backup plus the append-only override seed in git, from which every verified
fact can be replayed.

## What a monthly acceptance readout looks like

`GET /api/health/uptime` returns, per month, e.g.:

    {"month": "2026-09", "probes": 43200, "ok": 43198,
     "availability_pct": 99.9954, "median_latency_ms": 65}

99.99% monthly allows about 4.3 minutes of downtime; with a 3 second process
restart and the minute-probe safety net, the measured budget is spent on
deploys (which restart the backend in about 5 seconds each).
