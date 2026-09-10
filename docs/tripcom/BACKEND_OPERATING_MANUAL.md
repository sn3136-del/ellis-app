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
Use literal25 acceptance metrics alongside the operational fillable metric.

Historical bilingual HTML/PDF operation/caliber manuals need release-matched
regeneration before delivery. Old PASS labels, sample availability percentages,
or unmeasured recovery claims are not current acceptance evidence.

## Security posture
UFW allows 22/80/443 only. Backend binds loopback; only Caddy is exposed.
Secrets ship encrypted (backend/secrets.enc, AES-256-CBC) and are decrypted
on the machine at deploy time; backend/.env never enters git.
