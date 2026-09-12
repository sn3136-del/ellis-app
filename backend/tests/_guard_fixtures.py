"""Shared seeding for the guard-20260912 tests: twelve real cached routes
(captured from the 11 September database copy) and the open monitor
findings on them, loaded into the session test database and removed again."""
import contextlib
import json
from datetime import datetime
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROWS = FIXTURES / "surface_parity_rows.json"
ISSUES = FIXTURES / "surface_parity_issues.json"
GOLDEN = FIXTURES / "surface_parity_golden.json"

# The record keys the golden fixture compares: the 25 cells and every
# per-record state tag the console and the workbook read.
GOLDEN_KEYS = None


def golden_keys():
    from app.visa_snapshot import tstation
    return tuple(tstation.FIELD_ORDER) + (
        'confidence_level', 'source_url', 'data_source', '_cache_key', '_status', '_contradictions',
        '_released', '_route_held', '_held', '_review_required', '_publication_state',
        '_publication_reason', '_disputed', '_source_check', '_unpublished', '_evidence_low',
        '_product_index', '_separate_permission', 'visa_fee_qualifier', 'max_stay_text',
        'validity_text', 'freshness_valid_until', 'corroborating_sources')


def fixture_rows() -> list[dict]:
    return json.loads(ROWS.read_text(encoding="utf-8"))


def fixture_issues() -> list[dict]:
    return json.loads(ISSUES.read_text(encoding="utf-8"))


def fixture_keys() -> list[str]:
    return [r["cache_key"] for r in fixture_rows()]


@contextlib.contextmanager
def seeded(db, *, rows=None, issues=None):
    """Seed the fixture rows (and their open findings) into ``db``; delete
    them again afterwards so the session database stays clean."""
    from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache
    rows = fixture_rows() if rows is None else rows
    issues = fixture_issues() if issues is None else issues
    keys = [r["cache_key"] for r in rows]
    db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.in_(keys)).delete(
        synchronize_session=False)
    db.query(DatabaseIssueReport).filter(DatabaseIssueReport.cache_key.in_(keys)).delete(
        synchronize_session=False)
    for r in rows:
        db.add(KimiRouteGuidanceCache(
            cache_key=r["cache_key"], route=r["route"], guidance=r["guidance"], status=r["status"],
            model=r["model"], verification=r["verification"], missing_fields=r["missing_fields"],
            contradictions=r["contradictions"],
            generated_at=datetime.fromisoformat(r["generated_at"]),
            fresh_until=datetime.fromisoformat(r["fresh_until"])))
    for i in issues:
        db.add(DatabaseIssueReport(org_id="platform", cache_key=i["cache_key"], route=i["route"],
                                   field=i["field"], note=i["note"], reported_by=i["reported_by"],
                                   status=i["status"], proposal=i["proposal"]))
    db.commit()
    try:
        yield keys
    finally:
        db.rollback()
        db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.in_(keys)).delete(
            synchronize_session=False)
        db.query(DatabaseIssueReport).filter(DatabaseIssueReport.cache_key.in_(keys)).delete(
            synchronize_session=False)
        db.commit()


def normalised(records: list[dict]) -> list[dict]:
    keys = golden_keys()
    return json.loads(json.dumps([{k: rec.get(k) for k in keys} for rec in records],
                                 ensure_ascii=False, sort_keys=True, default=str))
