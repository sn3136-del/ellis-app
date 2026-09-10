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
Use the latest dated release audit and its immutable snapshot SHA for current
counts. Recompute documented completeness across all 25 contractual fields;
retain operational applicable 20 and literal 25 non-null diagnostics separately.
A previous release's percentage must not be presented as current. Completion,
source-link presence and publication availability do not certify policy accuracy.

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

## Source corrections and issue dispositions

Reviewed releases preserve raw source history, issue records and prior review dates.
Changed fields receive their actual scoped verification date; unrelated dates
are not renewed.
Before installing a source correction, compare the captured raw cache, merged
answer, route row, provenance, ordered seed overlays and operator overrides.
Rebuild the exact approved candidate from those six layers. Pause writers and
recheck those preconditions at installation; changed input requires a new review.
A narrowly scoped correction must not renew unrelated fields or release products
whose independent evidence or disputes are unresolved.

GET /api/database/issues returns revision_sha256 for each current issue.
Clients that bind their review to that revision pass expected_issue_sha256 with
the disposition request. The API also checks its own read-to-write revision for
all callers. A changed issue returns HTTP 409. The issue update and AuditEvent
commit in one transaction. Older clients that omit the expected revision do not
receive protection for changes between their earlier screen review and the API
request. Separate route/provenance prechecks are not an atomic route-row lock.
After an uncertain POST result, inspect current state before deciding what to do;
do not blindly resubmit. Retain the actual actor, review note and source evidence.

## Review scheduling priorities

After eligibility and continuation filters, review the four reported critical
routes first, then US/Hong Kong routes to the 18 station markets plus mainland
China, then other station routes to those markets, then the remaining inventory.
These are user and project priorities, not measured traffic statistics. Every
eighth dispatch takes the eligible route with the oldest recorded source-check
attempt; missing or invalid attempt dates sort first. This prevents indefinite
starvation without using policy expiry as the priority clock.
Continuation retains the dispatch position and original cycle deadline. A release
must not reset the five-hour budget or count unattempted work as verified.

## Acceptance evidence

Keep the supplied requirements ledger with its original document hashes and
clause identities. A release report must distinguish implemented behavior,
measured checks and remaining external requirements. A scheduled report is not
proof that the required human/provider review occurred; a short database restore
does not establish full service recovery, and a short monitoring interval cannot
prove a monthly 99.99% availability target. Retain actual dated evidence and
record any remaining acceptance item without inventing sign-off.
