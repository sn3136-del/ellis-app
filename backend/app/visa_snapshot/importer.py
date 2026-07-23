"""Idempotent JSONL importer for the 2026-07-23 route snapshot.

Guarantees (brief section 11):
  1. reads JSONL line by line          9. detects material changes
  2. validates every line             10. creates review tasks on change/conflict
  3. normalizes inputs                11. dry-run support
  4. computes the route key           12. transactional apply
  5. computes canonical content hash  13. rollback (new head pointer, history kept)
  6. stores immutable versions        14. immutable audit records
  7. never overwrites prior versions  15. redacted report (no personal values —
  8. detects duplicates                   snapshot data holds no applicant PII)
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from . import SNAPSHOT_DATE
from .models import (HumanReviewTask, VisaRoute, VisaRouteVersion)
from .routekey import RouteInput, canonical_hash, route_key
from .validation import validate_record


@dataclass
class ImportReport:
    file: str = ""
    mode: str = "dry-run"
    total_lines: int = 0
    valid: int = 0
    invalid: int = 0
    new_routes: int = 0
    new_versions: int = 0          # material changes on existing routes
    duplicates: int = 0            # identical content hash → skipped
    review_tasks_created: int = 0
    errors: list = field(default_factory=list)   # [{line, errors[]}] capped

    def as_dict(self) -> dict:
        return {
            "file": self.file, "mode": self.mode, "snapshot_date": SNAPSHOT_DATE,
            "total_lines": self.total_lines, "valid": self.valid, "invalid": self.invalid,
            "new_routes": self.new_routes, "new_versions": self.new_versions,
            "duplicates": self.duplicates, "review_tasks_created": self.review_tasks_created,
            "errors": self.errors[:50],
        }


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if line:
                yield lineno, line


def _normalize_and_key(rec: dict) -> tuple[str, dict]:
    """Recompute the route key from the record's own route components (never
    trust a precomputed key) and return (key, rec-with-key)."""
    inp = RouteInput(
        passport_nationality=rec["passport_nationality"],
        passport_issuing_country=rec["passport_issuing_country"],
        travel_document_type=rec["travel_document_type"],
        lawful_country_of_residence=rec["lawful_country_of_residence"],
        destination_country=rec["destination_country"],
        visa_category=rec["visa_category"],
        visa_subtype=rec.get("visa_subtype"),
        travel_purpose=rec.get("travel_purpose", "tourism"),
        policy_period=rec.get("applicable_arrival_start") or None,
        consular_jurisdiction=rec.get("consular_jurisdiction") or None,
    )
    key = route_key(inp)
    rec = dict(rec, route_key=key)
    return key, rec


def validate_file(path: Path, *, max_errors: int = 50) -> ImportReport:
    rep = ImportReport(file=str(path), mode="validate")
    for lineno, line in _iter_jsonl(path):
        rep.total_lines += 1
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            rep.invalid += 1
            if len(rep.errors) < max_errors:
                rep.errors.append({"line": lineno, "errors": [f"invalid json: {e.msg}"]})
            continue
        errs = validate_record("route", rec)
        if not errs:
            try:
                _normalize_and_key(rec)
            except Exception as e:  # noqa: BLE001 - normalization error is a data error
                errs = [f"normalization: {e}"]
        if errs:
            rep.invalid += 1
            if len(rep.errors) < max_errors:
                rep.errors.append({"line": lineno, "errors": errs[:10]})
        else:
            rep.valid += 1
    return rep


def import_file(db, path: Path, *, apply: bool = False, actor: str = "importer",
                batch_id: str | None = None) -> ImportReport:
    """Dry-run by default; apply=True commits transactionally (all or nothing)."""
    from .. import audit

    rep = ImportReport(file=str(path), mode="apply" if apply else "dry-run")
    batch = batch_id or uuid.uuid4().hex[:12]
    try:
        for lineno, line in _iter_jsonl(path):
            rep.total_lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                rep.invalid += 1
                rep.errors.append({"line": lineno, "errors": [f"invalid json: {e.msg}"]})
                continue
            errs = validate_record("route", rec)
            if errs:
                rep.invalid += 1
                if len(rep.errors) < 50:
                    rep.errors.append({"line": lineno, "errors": errs[:10]})
                continue
            try:
                key, rec = _normalize_and_key(rec)
            except Exception as e:  # noqa: BLE001
                rep.invalid += 1
                if len(rep.errors) < 50:
                    rep.errors.append({"line": lineno, "errors": [f"normalization: {e}"]})
                continue
            rep.valid += 1
            chash = canonical_hash(rec)
            rec["content_hash"] = chash

            head = db.execute(select(VisaRoute).where(VisaRoute.route_key == key)).scalar_one_or_none()
            if head is None:
                rep.new_routes += 1
                if apply:
                    head = VisaRoute(
                        snapshot_date=SNAPSHOT_DATE, route_key=key,
                        passport_nationality=rec["passport_nationality"],
                        passport_issuing_country=rec["passport_issuing_country"],
                        travel_document_type=rec["travel_document_type"],
                        lawful_country_of_residence=rec["lawful_country_of_residence"],
                        destination_country=rec["destination_country"],
                        visa_category=rec["visa_category"],
                        visa_subtype=rec.get("visa_subtype") or "any",
                        current_version=1,
                        disposition=rec.get("disposition", "RESEARCH_INCOMPLETE"),
                        research_status=rec["research_status"],
                        human_review_status=rec["human_review_status"],
                        fee_status=rec["fee_status"],
                        portal_verification_status=rec["portal_verification_status"],
                        adapter_status=rec["adapter_status"],
                        conflict_status=rec["conflict_status"],
                    )
                    db.add(head)
                    db.flush()
                    db.add(VisaRouteVersion(route_id=head.id, version=1, record=rec,
                                            content_hash=chash, import_batch_id=batch))
                continue

            # Existing route: duplicate (same content) or material change.
            latest = db.execute(
                select(VisaRouteVersion).where(VisaRouteVersion.route_id == head.id)
                .order_by(VisaRouteVersion.version.desc())
            ).scalars().first()
            if latest and latest.content_hash == chash:
                rep.duplicates += 1
                continue
            rep.new_versions += 1
            rep.review_tasks_created += 1
            if apply:
                new_v = (latest.version if latest else 0) + 1
                if latest:
                    latest.superseded_by = new_v  # annotate; never overwrite content
                db.add(VisaRouteVersion(route_id=head.id, version=new_v, record=rec,
                                        content_hash=chash, import_batch_id=batch))
                head.current_version = new_v
                head.disposition = rec.get("disposition", head.disposition)
                head.research_status = rec["research_status"]
                head.human_review_status = rec["human_review_status"]
                head.fee_status = rec["fee_status"]
                head.portal_verification_status = rec["portal_verification_status"]
                head.adapter_status = rec["adapter_status"]
                head.conflict_status = rec["conflict_status"]
                db.add(HumanReviewTask(
                    snapshot_date=SNAPSHOT_DATE, kind="route_review", subject_id=key,
                    title=f"Material change on import (v{new_v})",
                    detail={"batch": batch, "old_hash": latest.content_hash if latest else None,
                            "new_hash": chash}))
        if apply:
            if rep.invalid:
                # Transactional apply: any invalid line aborts the whole batch.
                db.rollback()
                rep.mode = "apply-aborted-invalid-lines"
            else:
                from .. import audit as _audit
                _audit.record(db, org_id="global", application_id="",
                              action="routes_import_apply",
                              detail={k: v for k, v in rep.as_dict().items() if k != "errors"},
                              actor=actor)
                db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    return rep


def history(db, key: str) -> list[dict]:
    head = db.execute(select(VisaRoute).where(VisaRoute.route_key == key)).scalar_one_or_none()
    if not head:
        return []
    rows = db.execute(select(VisaRouteVersion).where(VisaRouteVersion.route_id == head.id)
                      .order_by(VisaRouteVersion.version)).scalars().all()
    return [{"version": r.version, "content_hash": r.content_hash,
             "superseded_by": r.superseded_by, "import_batch_id": r.import_batch_id,
             "created_at": str(r.created_at), "is_current": r.version == head.current_version}
            for r in rows]


def rollback(db, key: str, version: int, *, actor: str = "admin") -> dict:
    """Point the head at a prior immutable version. History is preserved —
    nothing is deleted; an audit record is written."""
    from .. import audit
    head = db.execute(select(VisaRoute).where(VisaRoute.route_key == key)).scalar_one_or_none()
    if not head:
        raise KeyError(f"unknown route_key: {key}")
    target = db.execute(select(VisaRouteVersion).where(
        VisaRouteVersion.route_id == head.id, VisaRouteVersion.version == version)
    ).scalar_one_or_none()
    if not target:
        raise KeyError(f"route has no version {version}")
    prev = head.current_version
    head.current_version = version
    rec = target.record or {}
    head.disposition = rec.get("disposition", head.disposition)
    head.research_status = rec.get("research_status", head.research_status)
    head.human_review_status = rec.get("human_review_status", head.human_review_status)
    head.fee_status = rec.get("fee_status", head.fee_status)
    head.portal_verification_status = rec.get("portal_verification_status", head.portal_verification_status)
    head.adapter_status = rec.get("adapter_status", head.adapter_status)
    head.conflict_status = rec.get("conflict_status", head.conflict_status)
    audit.record(db, org_id="global", application_id="", action="routes_rollback",
                 detail={"route_key": key, "from_version": prev, "to_version": version},
                 actor=actor)
    db.commit()
    return {"route_key": key, "from_version": prev, "to_version": version}
