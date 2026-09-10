# Availability, quality review and recovery evidence

Revision: 2026-09-10. Runtime source: `482e2fdd65fd5827c5f322ef1c4a1a689e597d14`. This runbook states the current implementation and the limits of the inspected evidence. It does not certify ≥99.99% monthly availability or ≤1 hour full-service recovery.

## Availability measurement

The public health path is `https://ellis-visa.com/api/health`; the report is `https://ellis-visa.com/api/health/uptime?format=json`. The backend route is `/health/uptime`, exposed through the edge's `/api` prefix.

`backend/app/main.py::_uptime_data` reads monthly CSVs under `ELLIS_UPTIME_DIR` (default `/var/lib/ellis/uptime`). It reports recorded probes, HTTP-200 probes, their ratio, median successful-probe latency and the incident-log line count. The intended host probe interval is one minute with a retry. The reader does not verify the live cron, infer downtime from absent minutes, or independently identify planned maintenance. Confirm the installed probe configuration and raw timestamps when preparing an acceptance period.

A same-host probe cannot observe host loss or its own uplink failure while it is unable to run. Formal monthly availability evidence needs the whole time interval, treatment of missing probes, incident records, and an independent observation point. Only maintenance actually reported to the client under the contract may be excluded; a retrospective documentation entry is not notification evidence. No complete current monthly period or external-monitor delivery record was certified in this release review.

Process restart settings and watchdog scripts are operational configuration to verify on the host. A successful restart or health response does not establish disaster recovery time. No measured process-restart duration or service-level availability percentage is asserted here.

## Verified daily database backup

`scripts/cron-daily-ellis-backup.sh` invokes `backend/scripts/backup_database.py` against `/var/lib/ellis/ellis.db`, writing weekday-rotated `/var/backups/ellis/ellis-1.db` through `ellis-7.db`. The updated production daily job was verified on 2026-09-10.

The script uses SQLite's online backup API, including committed WAL content. It checks the destination's integrity, creates a private temporary file, and publishes it atomically with mode 0600. Failure preserves an existing good destination. It does not fall back to copying a live database file, which could omit WAL transactions.

The live database-only drill completed at `2026-09-10T02:25:22.007650+00:00`:

| Evidence | Result |
|---|---|
| Backup bytes / tables | 23,834,624 bytes / 99 tables |
| SHA-256 | `267599b91f800a3cb7ca89358b0cc31f63f7ec5d16ec2bbbf3777c59a4253cde` |
| Restored integrity | `ok` |
| Restored route-table rows | 949 |
| Database backup plus restore elapsed | 0.708 seconds |
| Full-service RTO certified | No |

This was an actual database backup and restoration check. It was not a replacement-host recovery drill and did not validate application installation, configuration, secrets, DNS, edge setup or external dependencies. Database table counts are not canonical API coverage counts. Seven weekday files on the same host do not survive host loss; durable off-host backup, retention and retrieval need separate verification.

## Full-service recovery runbook — contractual target ≤1 hour, proof pending

1. Record incident start, recovery owner, intended release commit and latest independently recoverable backup. Verify their checksums and documented dependencies before restoration.
2. Provision the replacement host and restore the checksum-pinned application release with its locked dependencies. Provision secrets through the owner's approved secret-management process; do not assume an encrypted archive exists or that source control contains every fact.
3. Verify the selected database backup and its integrity in isolation. Stop application writes before installing the restored database with the intended ownership and permissions. Preserve the previous artifacts for rollback; avoid pairing it with an unrelated WAL or shared-memory file.
4. Restore the matching service/edge configuration, required external-service settings and static frontend bundle. Start the service, then verify local and public `/api/health`, representative canonical route reads, quality-console access and protected operator functions.
5. Restore external routing when needed. Confirm monitoring, freshness scheduling and daily backups resume; record recovery completion and any data-loss interval.
6. Archive the full transcript, checksums, timestamps and observed public behavior. Claim the ≤1-hour full-service RTO only after an actual timed drill or incident demonstrates the complete scope.

There is no guaranteed replay of every verified fact from git. Restore planning must include the live database and independently protected application/configuration artifacts.

## Periodic quality review

`deploy/systemd/ellis-freshness.timer` schedules the canonical sweep at 00:20, 06:20, 12:20 and 18:20 UTC, with up to ten minutes of random delay and persistent scheduling. `ellis-freshness.service` invokes `backend/scripts/freshness_sweep.py`. Current defaults use four workers, up to 5,000 rows, a five-hour cycle budget and a 75-second route budget. A fifteen-minute duplicate-attempt window avoids immediately repeating very recent reads. Inspect the installed timer and status rather than inferring them from repository files alone.

This is a bounded attempt schedule, not a promise that each policy is verified each cycle. The inspected 2026-09-10 snapshot showed an active scheduler but the preceding sweep was interrupted: 894 attempts, 778 reads, 20 verified results and outstanding work. The inventory's last-48-hour counts were 939 attempts, 846 reads and 30 verified checks across 946 canonical routes, with 916 never-verified routes. A fetched page or scheduled job must not be counted as full monthly source verification.

The monthly quality reporter is `backend/scripts/monthly_quality_report.py`. Its operational completeness uses the configured 20 required fields and supported applicability states. Formal acceptance must additionally retain both literal 25 diagnostics: filled cells ÷ (25 × all records), and records with all 25 populated ÷ all records. Keep pending/disputed cells visible, include full schema/count validation, and archive the snapshot timestamp/hash. The separate `backend/scripts/audit_acceptance_snapshot.py` provides these strict diagnostics and rejects incomplete input. The user has approved counting source-validated Not applicable and Not published states as complete in a separate documented-state 25 metric. Unknown, unsupported and pending-review cells remain incomplete. Retain both literal 25 non-null diagnostics without changing their denominator; they remain comparable with the written acceptance clauses. The hardened auditor recomputes these metrics from the record values and public pending/unpublished metadata rather than trusting a supplied summary. The 2026-09-10 02:29 UTC snapshot has 29,093 / 35,500 documented-complete cells (81.9521%), including 3,225 disposition cells; 0 of 1,420 records are complete across all 25. Operational applicable 20 completeness is separately 87.32%. These are recorded-state measurements, not new official-source verifications, and do not achieve the 100% target.

Confidence currently emits binary High/Low, with equal evidence rules for AI and human review. Grade, source-link presence, requirement support, all-field correctness and publication hold are separate dimensions. The written High/Medium/Low contract must be reconciled explicitly; do not relabel old Medium counts as current performance.

Monthly full verification and quarterly bidirectional sampling require actual completed evidence across the agreed inventory and client sampling records. Policy update (48 hours ordinary / 24 hours urgent), issue closure (48 hours) and alert-delivery obligations require timestamped workflow and receipt evidence. Schedule configuration, tests and an empty overdue count do not prove those obligations.

## Acceptance evidence still required

- Full-period availability telemetry with gap and maintenance accounting, plus independent monitoring and tested alert delivery.
- Durable off-host backup/retrieval and a complete timed service-recovery drill within the one-hour target.
- Completed monthly whole-inventory source review and quarterly joint bidirectional sampling.
- Real change/feedback workflow evidence and client acceptance records.

See `DATA_CALIBER_MANUAL.md`, `BACKEND_OPERATING_MANUAL.md` and `requirement-ledger.html` for the related release semantics and current measurement gaps.
