#!/usr/bin/env python3
"""Normalize the two reviewed legacy unknown objects; dry-run by default.

This is a representation repair, never a passport-policy correction. No
provider requests, source reads, new provenance or operator release are made.
Applying requires a new backup path. The entire SQLite repair and its audit
entries commit together; existing source disputes and change history survive.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.visa_snapshot import kimi_primary as kp, verified_overrides as overrides

TARGETS = (
    "USA|USA|ERI|tourism|default|unknown|v6",
    "HKG|HKG|NRU|tourism|default|unknown|v6",
)
FIELD = "passport_validity_requirement"
EMPTY = {"kind": None, "months": None}
FAILURE = "passport_validity_requirement has an unknown kind"
NOTE = "Deterministic integrity check: " + FAILURE
ORIGIN = "legacy-unknown-normalization"


def _json(value, default):
    return json.loads(value) if value is not None else default


def _replace_cache(db, row, guidance, contradictions, stamp):
    """The writer refuses a stale whole-row snapshot, including review stamps."""
    result = db.execute("""UPDATE kimi_route_guidance_cache
        SET guidance=?, contradictions=?, updated_at=?
        WHERE id=? AND guidance IS ? AND contradictions IS ?
        AND verification IS ? AND route IS ?""",
        (json.dumps(guidance, ensure_ascii=False), json.dumps(contradictions), stamp,
         row["id"], row["guidance"], row["contradictions"], row["verification"], row["route"]))
    if result.rowcount != 1:
        raise RuntimeError("Canonical row changed during the repair; transaction rolled back")


def normalize(database, *, apply=False, backup=None, now=None):
    database = Path(database).resolve()
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).replace(tzinfo=None).isoformat(" ", timespec="microseconds")
    if apply:
        if not backup:
            raise ValueError("--apply requires an explicit new backup file")
        backup = Path(backup).resolve()
        if backup.exists() or backup == database or not backup.parent.is_dir():
            raise ValueError("backup must be a new separate file in an existing directory")
    db = sqlite3.connect(database.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        if apply:
            # Exclude a concurrent API/worker write between inspection and
            # repair. CAS below also refuses changed review metadata.
            db.execute("BEGIN IMMEDIATE")
        plan, prepared = [], []
        for key in TARGETS:
            row = db.execute("SELECT * FROM kimi_route_guidance_cache WHERE cache_key=?", (key,)).fetchone()
            if row is None:
                plan.append({"cache_key": key, "action": "missing"})
                continue
            raw = _json(row["guidance"], {})
            # Exact reviewed shape only: no broad catch-all for unknown kinds.
            if not isinstance(raw, dict) or raw.get(FIELD) != EMPTY:
                plan.append({"cache_key": key, "action": "unchanged_not_exact_legacy_null_object"})
                continue
            guidance = dict(raw, **{FIELD: None})
            route = _json(row["route"], {})
            merged, _ = overrides.apply(guidance, route)
            failures = kp.serve_time_invariants(merged)
            existing = _json(row["contradictions"], [])
            if not isinstance(existing, list):
                raise ValueError("Stored contradiction history has an unsupported shape")
            contradictions = [p for p in existing if p != FAILURE] if not failures else existing
            issues = []
            if not failures:
                for issue in db.execute("""SELECT * FROM database_issue_reports
                        WHERE cache_key=? AND field='integrity' AND reported_by='freshness_monitor'
                        AND status IN ('open','acknowledged') AND note=?""", (key, NOTE)):
                    proposal = _json(issue["proposal"], {})
                    if (isinstance(proposal, dict) and proposal.get("outcome") == "integrity_failed"
                            and proposal.get("contradictions") == [FAILURE]):
                        issues.append((issue, proposal))
            plan.append({"cache_key": key, "action": "normalize_unknown", "full_guidance_passes": not failures,
                         "remaining_invariants": failures, "owned_integrity_issues_to_correct": len(issues),
                         "other_disputes_and_verification_unchanged": True})
            prepared.append((row, guidance, contradictions, issues))
        report = {"applied": False, "backup": None, "normalized": 0, "issues_corrected": 0,
                  "plan": plan, "policy_facts_invented": False, "automatic_operator_release": False,
                  "unsupported_nonempty_kinds_targeted": False}
        if not apply or not prepared:
            return report
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(backup) as target:
                source.backup(target)
        for row, guidance, contradictions, issues in prepared:
            _replace_cache(db, row, guidance, contradictions, stamp)
            closed = []
            for issue, proposal in issues:
                proposal = dict(proposal, resolution_check={"outcome": "integrity_passed",
                    "checked_at": now.isoformat(), "contradictions": [], "origin": ORIGIN})
                result = db.execute("""UPDATE database_issue_reports
                    SET status='corrected', resolved_by=?, resolved_at=?, resolution=?, proposal=?, updated_at=?
                    WHERE id=? AND status IS ? AND proposal IS ? AND note IS ?""",
                    (ORIGIN, stamp, "Legacy empty passport-rule object normalized to unknown. Full merged guidance passes integrity checks; source accuracy and other disputes retain their separate status.",
                     json.dumps(proposal), stamp, issue["id"], issue["status"], issue["proposal"], issue["note"]))
                if result.rowcount != 1:
                    raise RuntimeError("Integrity issue changed during repair; transaction rolled back")
                closed.append(issue["id"])
            changes = {FIELD: {"before": EMPTY, "after": None},
                       "corrected_integrity_issue_ids": closed}
            note = "Representation-only repair: empty legacy passport contract is unknown; no policy, source verification, freshness or release was added."
            db.execute("""INSERT INTO database_change_log
                (id,cache_key,route,action,origin,changes,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, row["cache_key"], row["route"], "correct", ORIGIN,
                 json.dumps(changes), note, stamp, stamp))
            db.execute("""INSERT INTO audit_events
                (id,seq,at,org_id,application_id,actor,action,detail) VALUES (?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, (db.execute("SELECT max(seq) FROM audit_events").fetchone()[0] or 0) + 1,
                 stamp, "platform", "", "system", ORIGIN,
                 json.dumps({"cache_key": row["cache_key"], "field": FIELD,
                             "corrected_integrity_issue_ids": closed, "policy_unchanged": True})))
            report["normalized"] += 1
            report["issues_corrected"] += len(closed)
        db.commit()
        report.update(applied=True, backup=str(backup))
        return report
    finally:
        db.close()  # Any failure rolls back writes, resolutions and both logs.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup")
    args = parser.parse_args()
    print(json.dumps(normalize(args.database, apply=args.apply, backup=args.backup), indent=2))


if __name__ == "__main__":
    main()
