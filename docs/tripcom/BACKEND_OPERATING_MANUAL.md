# Backend Operating Manual — ellis-visa.com

## Topology
Hetzner CX23 (Falkenstein). Caddy terminates TLS for ellis-visa.com and
serves the static app from /opt/ellis/src/renderer/dist; /api/* is proxied
to the FastAPI backend (systemd unit ellis-backend) on 127.0.0.1:8000.
Data: SQLite at /var/lib/ellis/ellis.db.

## Routine operations
- Status: systemctl status ellis-backend caddy
- Logs: journalctl -u ellis-backend -f (backend), -u caddy (edge)
- Deploy a tested release pinned to its commit and artifact checksum. Verify
  the current database backup and retain the previous release before updating.
  Record the release identifier, migration steps and rollback procedure; smoke
  test the backend, public application and quality console after restarting.
- Health: GET https://ellis-visa.com/api/health

## Data protection
Daily full database backup is required. `backend/scripts/backup_database.py`
uses SQLite's online backup mechanism to include committed WAL content,
validates the destination database and atomically publishes a private
(mode 0600) file only after a successful integrity check. The daily wrapper
retains the existing `/var/backups/ellis/ellis-<weekday>.db` paths. Never fall back to copying a live
database file when SQLite backup fails. Record successful backups and failures;
verify the deployed daily wrapper and its actual output, rather than inferring
success from a schedule's existence. Keep a verified off-host copy for host-loss
recovery. This is a database backup; release artifacts, configuration and
necessary external storage require their own recovery arrangements.

For restore, stop the service, preserve the current state, restore the verified
backup using the release-specific runbook, and check database integrity and
public/quality behavior before reopening service. The contractual recovery
target is at most one hour; a dated timed full recovery exercise is required to
prove it. systemd process restart is useful but is not a disaster-recovery test.
The 2026-09-10 live database-only drill restored a 23,834,624-byte backup,
99 tables and 949 route-table rows with integrity `ok`; database backup and
restore took 0.708 seconds. The daily wrapper was updated. Full-service recovery,
including host/app/configuration/secrets/DNS and off-host durability, is not
certified by that database check. Details are in `AVAILABILITY.md`.

## Quality-control backend
https://ellis-visa.com/#ops - records with combined spot-check filters
(names, codes and Chinese accepted), per-record 25-field checklists,
confidence and source-substantiation chips, one-click error flagging into
the tracked correction queue, the change log, freshness, and the two-sheet
Excel export.

The current confidence display is binary High/Low, with AI/human authorship
kept in source provenance and publication controlled separately by the existing
evidence/conflict gates. See `DATA_CALIBER_MANUAL.md` for precise definitions.
`info_validity` is the published policy end date only; unknown dates remain
blank. `freshness_valid_until` is separate internal review-deadline metadata.
The user-approved completeness target includes documented Not applicable/Not
published across all 25 fields; unknown/unsupported/pending states stay incomplete.
The recorded-state metric is 81.9521% on the 2026-09-10 02:29 UTC snapshot;
operational applicable 20 completeness is separately 87.32%. Retain literal 25
non-null diagnostics (72.9718% cells) as well. These are completion measurements,
not certification of policy accuracy.

The current bilingual HTML/PDF manuals have been regenerated for these semantics. Old PASS labels, sample availability percentages,
or unmeasured recovery claims are not current acceptance evidence.

## Security posture
UFW allows 22/80/443 only. Backend binds loopback; only Caddy is exposed.
Secrets ship encrypted (backend/secrets.enc, AES-256-CBC) and are decrypted
on the machine at deploy time; backend/.env never enters git.

## Route and product publication

QC keeps every product available for inspection. Product held and route_held
are distinct: a partial route can publish a supported exemption or required e-visa
while separate optional products remain withheld. The public page labels this state and
excludes unsupported alternative claims from its answer and chat. Fees, filing deadlines and required conditions
cannot be discarded to make a route publishable. Report default availability,
fully released routes and held products separately; partial publication is not
100% product coverage.
