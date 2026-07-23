"""Importer, matrix, pipeline, and CLI behavior for the 2026-07-23 snapshot."""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.visa_snapshot import SNAPSHOT_DATE, importer, matrix, pipeline
from app.visa_snapshot.models import (HumanReviewTask, RouteMatrixEntry,
                                      SnapshotResearchBatch, SourceEvidence,
                                      VisaFeeVersion, VisaPolicy, VisaRoute,
                                      VisaRouteVersion)
from app.visa_snapshot.routekey import RouteInput, route_key


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _rec(**kw):
    base = {
        "snapshot_date": SNAPSHOT_DATE, "record_id": "r-00000001", "version": 1,
        "route_key": "recomputed-anyway",
        "passport_nationality": "CHN", "passport_issuing_country": "CHN",
        "travel_document_type": "ordinary_passport",
        "lawful_country_of_residence": "USA", "destination_country": "JPN",
        "visa_category": "tourist_visa", "visa_subtype": "single_entry",
        "travel_purpose": "tourism", "disposition": "EMBASSY_VISA_REQUIRED",
        "research_status": "verified", "human_review_status": "not_reviewed",
        "fee_status": "unknown", "portal_verification_status": "unknown",
        "adapter_status": "none", "conflict_status": "none",
        "content_hash": "0" * 64,
    }
    base.update(kw)
    return base


def _write_jsonl(tmp_path, recs, name="routes.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return p


def test_validate_file_reports_bad_lines(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(json.dumps(_rec()) + "\nnot-json\n" +
                 json.dumps(_rec(research_status="nope")) + "\n")
    rep = importer.validate_file(p)
    assert rep.total_lines == 3 and rep.valid == 1 and rep.invalid == 2


def test_dry_run_makes_no_changes(db, tmp_path):
    p = _write_jsonl(tmp_path, [_rec()])
    rep = importer.import_file(db, p, apply=False)
    assert rep.new_routes == 1 and rep.mode == "dry-run"
    assert db.execute(select(VisaRoute)).scalars().first() is None


def test_apply_then_duplicate_then_material_change(db, tmp_path):
    p = _write_jsonl(tmp_path, [_rec()])
    rep = importer.import_file(db, p, apply=True)
    assert rep.new_routes == 1
    head = db.execute(select(VisaRoute)).scalars().one()
    assert head.current_version == 1

    # Re-import identical file: duplicate detected, no new version.
    rep2 = importer.import_file(db, p, apply=True)
    assert rep2.duplicates == 1 and rep2.new_routes == 0 and rep2.new_versions == 0

    # Material change -> immutable v2 + review task; v1 preserved.
    p3 = _write_jsonl(tmp_path, [_rec(disposition="EVISA_REQUIRED",
                                      evisa_available=True)], name="v2.jsonl")
    rep3 = importer.import_file(db, p3, apply=True)
    assert rep3.new_versions == 1
    versions = db.execute(select(VisaRouteVersion).order_by(VisaRouteVersion.version)).scalars().all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].superseded_by == 2
    assert versions[0].record["disposition"] == "EMBASSY_VISA_REQUIRED"  # never overwritten
    head = db.execute(select(VisaRoute)).scalars().one()
    assert head.current_version == 2 and head.disposition == "EVISA_REQUIRED"
    tasks = db.execute(select(HumanReviewTask)).scalars().all()
    assert any(t.kind == "route_review" for t in tasks)


def test_apply_aborts_on_any_invalid_line(db, tmp_path):
    p = _write_jsonl(tmp_path, [_rec(), _rec(research_status="bogus",
                                             destination_country="KOR")])
    rep = importer.import_file(db, p, apply=True)
    assert rep.mode == "apply-aborted-invalid-lines"
    assert db.execute(select(VisaRoute)).scalars().first() is None  # all-or-nothing


def test_history_and_rollback(db, tmp_path):
    importer.import_file(db, _write_jsonl(tmp_path, [_rec()]), apply=True)
    importer.import_file(db, _write_jsonl(tmp_path, [_rec(disposition="EVISA_REQUIRED")],
                                          name="b.jsonl"), apply=True)
    key = route_key(RouteInput(
        passport_nationality="CHN", passport_issuing_country="CHN",
        travel_document_type="ordinary_passport", lawful_country_of_residence="USA",
        destination_country="JPN", visa_category="tourist_visa", visa_subtype="single_entry"))
    hist = importer.history(db, key)
    assert [h["version"] for h in hist] == [1, 2]
    out = importer.rollback(db, key, 1)
    assert out["to_version"] == 1
    head = db.execute(select(VisaRoute)).scalars().one()
    assert head.current_version == 1 and head.disposition == "EMBASSY_VISA_REQUIRED"
    # History intact after rollback.
    assert [h["version"] for h in importer.history(db, key)] == [1, 2]


def test_route_key_recomputed_never_trusted(db, tmp_path):
    p = _write_jsonl(tmp_path, [_rec(route_key="rk1|forged-route-key-value")])
    importer.import_file(db, p, apply=True)
    head = db.execute(select(VisaRoute)).scalars().one()
    assert head.route_key.startswith("rk1|nat=CHN|iss=CHN")


def test_matrix_expected_count_formula():
    n_nat = 206
    n_dest = 249
    n_cat = 6
    n_odoc = 7
    assert matrix.expected_count() == n_nat * n_dest * n_cat + n_odoc * n_dest * n_cat


def test_matrix_entries_shape_and_not_applicable():
    it = matrix.iter_entries()
    first = next(it)
    assert set(first) >= {"matrix_key", "passport_nationality", "destination_country",
                          "visa_category", "disposition", "excluded"}
    # Find a own-state entry: ABW nationality? use CHN -> CHN
    found = None
    for e in matrix.iter_entries():
        if e["passport_nationality"] == "CHN" and e["destination_country"] == "CHN":
            found = e
            break
    assert found and found["disposition"] == "NOT_APPLICABLE" and not found["excluded"]


def test_matrix_populate_idempotent_subset(db, monkeypatch):
    sample = []
    for i, e in enumerate(matrix.iter_entries()):
        if i >= 500:
            break
        sample.append(e)
    monkeypatch.setattr(matrix, "iter_entries", lambda: iter(sample))
    r1 = matrix.populate(db)
    assert r1["inserted"] == 500
    r2 = matrix.populate(db)
    assert r2["inserted"] == 0 and r2["total_now"] == 500  # idempotent


def _research(dest="JPN", status="verified"):
    return {
        "destination": dest, "retrieved_at": "2026-07-23T12:00:00Z",
        "research_status": status,
        "sources": [{
            "search_query": "japan official immigration tourist visa china",
            "original_url": "https://www.mofa.go.jp/j_info/visit/visa/index.html",
            "final_url": "https://www.mofa.go.jp/j_info/visit/visa/index.html",
            "redirect_chain": [], "final_hostname": "www.mofa.go.jp",
            "source_authority": "destination_foreign_ministry",
            "relevant_excerpt": "Visa information for foreign nationals visiting Japan.",
            "retrieved_at": "2026-07-23T12:00:00Z", "page_language": "en",
        }, {
            "search_query": "japan visa blog", "original_url": "https://travelblog.example/japan",
            "final_url": "https://travelblog.example/japan",
            "final_hostname": "travelblog.example",
            "source_authority": "destination_government",  # a LIE — must be ignored
            "relevant_excerpt": "blog", "retrieved_at": "2026-07-23T12:00:00Z",
        }],
        "dispositions": [
            {"nationalities": ["CHN"], "category": "tourist_visa",
             "disposition": "EMBASSY_VISA_REQUIRED",
             "source_urls": ["https://www.mofa.go.jp/j_info/visit/visa/index.html"]},
            {"nationalities": ["ALL_OTHERS"], "category": "eta",
             "disposition": "NOT_APPLICABLE", "source_urls": []},
        ],
        "policies": [{"policy_kind": "visa_exemption_list", "policy_name": "Japan visa exemptions",
                      "applies_to_nationalities": ["USA", "SGP"],
                      "official_source_urls": ["https://www.mofa.go.jp/j_info/visit/visa/short/novisa.html"]}],
        "fees": [{"visa_category": "tourist_visa", "government_fee_amount": 3000,
                  "government_fee_currency": "JPY",
                  "source_urls": ["https://www.mofa.go.jp/j_info/visit/visa/index.html"]}],
        "portals": [{"portal_kind": "evisa_portal", "url": "https://www.evisa.mofa.go.jp/",
                     "operator_kind": "government"},
                    {"portal_kind": "authorized_visa_center", "url": "https://visa.vfsglobal.com/chn/zh/jpn",
                     "operator": "VFS Global", "operator_kind": "contracted_visa_center"}],
        "passport_validity": {"rule_kind": "valid_through_departure",
                              "condition_text": "Passport must be valid for the stay.",
                              "source_urls": ["https://www.mofa.go.jp/"]},
        "unresolved": ["Group tourist visas for CHN nationals need center-specific confirmation"],
    }


def test_pipeline_end_to_end_with_research_file(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESEARCH_DIR", tmp_path)
    (tmp_path / "JPN").mkdir()
    (tmp_path / "JPN" / "research.json").write_text(json.dumps(_research()))

    # Seed matrix rows for JPN so LINK has something to update.
    rows = [e for e in matrix.iter_entries()
            if e["destination_country"] == "JPN" and e["passport_nationality"] in ("CHN", "USA")]
    import uuid
    from sqlalchemy import insert
    db.execute(insert(RouteMatrixEntry),
               [dict(e, id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, route_id=None) for e in rows])
    db.commit()

    db.add(SnapshotResearchBatch(snapshot_date=SNAPSHOT_DATE, batch_key=f"{SNAPSHOT_DATE}:JPN",
                                 destination_country="JPN", stage="GROUP"))
    db.commit()
    b = db.execute(select(SnapshotResearchBatch)).scalars().one()
    b = pipeline.process_batch(db, b)
    assert b.result == "complete" and b.stage == "EXPORT_SNAPSHOT"

    # Evidence: gov domain verified; blog is NON-authoritative despite its claim.
    ev = {e.final_hostname: e for e in db.execute(select(SourceEvidence)).scalars()}
    assert ev["www.mofa.go.jp"].verification_status == "verified"
    assert ev["travelblog.example"].verification_status == "unverified"
    assert ev["travelblog.example"].source_authority == "non_authoritative"

    # Matrix linked: CHN tourist_visa row researched.
    row = db.execute(select(RouteMatrixEntry).where(
        RouteMatrixEntry.passport_nationality == "CHN",
        RouteMatrixEntry.destination_country == "JPN",
        RouteMatrixEntry.visa_category == "tourist_visa")).scalars().one()
    assert row.disposition == "EMBASSY_VISA_REQUIRED"

    assert db.execute(select(VisaPolicy)).scalars().first() is not None
    fee = db.execute(select(VisaFeeVersion)).scalars().one()
    assert fee.fee_status == "verified"
    assert db.execute(select(HumanReviewTask)).scalars().first() is not None


def test_pipeline_awaits_missing_research_honestly(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESEARCH_DIR", tmp_path)
    db.add(SnapshotResearchBatch(snapshot_date=SNAPSHOT_DATE, batch_key=f"{SNAPSHOT_DATE}:KOR",
                                 destination_country="KOR", stage="GROUP"))
    db.commit()
    b = db.execute(select(SnapshotResearchBatch)).scalars().one()
    b = pipeline.process_batch(db, b)
    assert b.result == "awaiting_research_input"      # honest, never guessed
    assert b.stage == "DISCOVER_OFFICIAL_SOURCES"     # resumable at the same stage


def test_untrusted_research_cannot_set_definitive_disposition(db, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESEARCH_DIR", tmp_path)
    (tmp_path / "JPN").mkdir()
    r = _research(status="research_incomplete")
    (tmp_path / "JPN" / "research.json").write_text(json.dumps(r))
    rows = [e for e in matrix.iter_entries()
            if e["destination_country"] == "JPN" and e["passport_nationality"] == "CHN"]
    import uuid
    from sqlalchemy import insert
    db.execute(insert(RouteMatrixEntry),
               [dict(e, id=uuid.uuid4().hex, snapshot_date=SNAPSHOT_DATE, route_id=None) for e in rows])
    db.commit()
    db.add(SnapshotResearchBatch(snapshot_date=SNAPSHOT_DATE, batch_key=f"{SNAPSHOT_DATE}:JPN",
                                 destination_country="JPN", stage="GROUP"))
    db.commit()
    b = db.execute(select(SnapshotResearchBatch)).scalars().one()
    pipeline.process_batch(db, b)
    row = db.execute(select(RouteMatrixEntry).where(
        RouteMatrixEntry.passport_nationality == "CHN",
        RouteMatrixEntry.visa_category == "tourist_visa")).scalars().one()
    assert row.disposition == "RESEARCH_INCOMPLETE"   # never guessed into a real disposition
