"""The automatic 48-hour cycle: re-verify every served answer against its
official page at least every two days.

Run by the ellis-freshness systemd timer every six hours. Each run takes the
oldest slice of the backlog, so the whole database is re-read well inside 48
hours while no single run hammers government sites: rows are spaced a few
seconds apart and the run stops at its own time budget. Corrections apply
automatically with quotes (the same recheck the console's refresh button
runs); anything the page disputes against a human-verified value lands in
the correction queue for a person, exactly like the rest of the loop.
"""
import logging
import sys
import time

sys.path.insert(0, "/opt/ellis/backend")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("freshness-sweep")

MAX_ROWS = 400            # 4 runs a day x 400 rows covers the fleet twice over
MAX_SECONDS = 50 * 60     # never collide with the next timer firing
SPACING_SECONDS = 3.0     # politeness between page reads


def main() -> int:
    from app.db import SessionLocal
    from app.visa_snapshot import freshness

    started = time.monotonic()
    checked = corrected = disputed = unreadable = 0
    db = SessionLocal()
    try:
        rows = freshness.due_rows(db, older_than_hours=48, limit=MAX_ROWS)
        log.info("48-hour sweep: %d rows due", len(rows))
        for row in rows:
            if time.monotonic() - started > MAX_SECONDS:
                log.info("time budget reached, the next run continues")
                break
            try:
                report = freshness.recheck_row(db, row) or {}
            except Exception as e:  # noqa: BLE001 - one bad row never ends the sweep
                log.warning("recheck failed for %s: %s", row.cache_key, e)
                unreadable += 1
                continue
            checked += 1
            if report.get("changed"):
                corrected += 1
                log.info("corrected %s: %s (%s)", row.cache_key,
                         report["changed"], report.get("source_url"))
            if report.get("disputed"):
                disputed += 1
            if report.get("outcome") in ("page_unreachable", "page_not_relevant"):
                unreadable += 1
            time.sleep(SPACING_SECONDS)
    finally:
        db.close()
    log.info("sweep done: %d checked, %d corrected, %d disputed, %d unreadable",
             checked, corrected, disputed, unreadable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
