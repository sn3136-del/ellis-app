"""Snapshot CLI (brief section 11).

  python -m app.visa_snapshot build --as-of 2026-07-23
  python -m app.visa_snapshot resume --as-of 2026-07-23
  python -m app.visa_snapshot status --as-of 2026-07-23
  python -m app.visa_snapshot validate --as-of 2026-07-23
  python -m app.visa_snapshot export --as-of 2026-07-23
  python -m app.visa_snapshot verify-completeness --as-of 2026-07-23

One-time manual tooling only. No schedules, no daemons, no recurring refresh.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from sqlalchemy import select

from . import SNAPSHOT_DATE, SNAPSHOT_LABEL
from .models import VisaSnapshot


def _die_unless_snapshot_date(as_of: str):
    if as_of != SNAPSHOT_DATE:
        print(json.dumps({"error": f"this build supports only the frozen snapshot "
                                   f"{SNAPSHOT_DATE}; got {as_of}"}))
        sys.exit(2)


def _session():
    from ..db import SessionLocal, create_all
    create_all()
    return SessionLocal()


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()[:40]
    except Exception:  # noqa: BLE001
        return ""


def cmd_build(as_of: str) -> dict:
    from .matrix import populate
    from .pipeline import enumerate_batches
    db = _session()
    try:
        snap = db.execute(select(VisaSnapshot).where(
            VisaSnapshot.snapshot_date == as_of)).scalar_one_or_none()
        if not snap:
            snap = VisaSnapshot(snapshot_date=as_of, label=SNAPSHOT_LABEL,
                                git_commit=_git_commit())
            db.add(snap)
            db.commit()
        m = populate(db, progress=lambda n: print(f"  matrix rows inserted: {n}", file=sys.stderr))
        b = enumerate_batches(db)
        snap.status = "building"
        snap.counts = {"matrix": m, "batches_created": b["created"]}
        db.commit()
        return {"snapshot": as_of, "matrix": m, "batches": b}
    finally:
        db.close()


def cmd_resume(as_of: str) -> dict:
    from .pipeline import resume
    db = _session()
    try:
        return resume(db)
    finally:
        db.close()


def cmd_status(as_of: str) -> dict:
    from .coverage import metrics
    from .matrix import completeness
    from .pipeline import status
    db = _session()
    try:
        return {"pipeline": status(db), "matrix": completeness(db), "metrics": metrics(db)}
    finally:
        db.close()


def cmd_validate(as_of: str) -> dict:
    from .registry import SNAPSHOT_DIR
    from .validation import validate_jsonl
    out = SNAPSHOT_DIR / as_of
    results = {}
    mapping = {
        "global_visa_routes.jsonl": "route",
        "global_visa_policies.jsonl": "policy",
        "global_fee_schedules.jsonl": "fee",
        "global_portals.jsonl": "portal",
        "global_consular_jurisdictions.jsonl": "consular_jurisdiction",
        "global_source_evidence.jsonl": "source_evidence",
    }
    for fname, kind in mapping.items():
        path = out / fname
        if not path.exists():
            results[fname] = {"missing": True}
            continue
        r = validate_jsonl(kind, path)
        results[fname] = {"records": r["records"], "invalid": r["invalid"], "valid": r["valid"],
                          "errors": r["errors"][:5]}
    cov = out / "coverage_report.json"
    if cov.exists():
        from .validation import validate_record
        errs = validate_record("coverage_report", json.loads(cov.read_text()))
        results["coverage_report.json"] = {"valid": not errs, "errors": errs[:5]}
    else:
        results["coverage_report.json"] = {"missing": True}
    return results


def cmd_export(as_of: str) -> dict:
    from .export import export_all
    db = _session()
    try:
        return export_all(db, git_commit=_git_commit())
    finally:
        db.close()


def cmd_verify_completeness(as_of: str) -> dict:
    from .coverage import metrics
    from .matrix import completeness
    db = _session()
    try:
        mc = completeness(db)
        met = metrics(db)
        ok = mc["created_entries"] == mc["expected_entries"] and mc["expected_entries"] > 0
        return {"matrix_complete": ok, "matrix": mc, "metrics": met,
                "warning": "matrix completeness is NOT requirements coverage"}
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.visa_snapshot")
    ap.add_argument("command", choices=["build", "resume", "status", "validate",
                                        "export", "verify-completeness"])
    ap.add_argument("--as-of", default=SNAPSHOT_DATE)
    args = ap.parse_args(argv)
    _die_unless_snapshot_date(args.as_of)
    fn = {"build": cmd_build, "resume": cmd_resume, "status": cmd_status,
          "validate": cmd_validate, "export": cmd_export,
          "verify-completeness": cmd_verify_completeness}[args.command]
    print(json.dumps(fn(args.as_of), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
