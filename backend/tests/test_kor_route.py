"""CHN -> KOR resolves to its VERIFIED real process: an authorized-visa-center
paper application with appointment (short-term visit C-3-9), released as a
non-portal WORKFLOW — never an online-submission adapter, never K-ETA/eVisa.

The pair is produced by the standard research-override path so it is fully
reproducible against any DB:
  python -m app.routes_import apply data/global_visa_routes.jsonl
  python -m app.global_routes apply-research
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.global_routes import ELECTRONIC_OUTCOMES, baseline, families, resolver
from app.global_routes.models import RoutePairPolicy, pair_key
from app.visa_snapshot import importer, pipeline
from app.visa_snapshot.models import (AuthorizedVisaCenter,
                                      ConsularJurisdictionRule)

KOR_PAIR = pair_key("CHN", "ordinary_passport", "KOR")
OFFICIAL_URLS = [
    "https://www.visa.go.kr/openPage.do?LANG_TYPE=EN&MENU_ID=1020408",
    "https://overseas.mofa.go.kr/cn-shanghai-ko/brd/m_534/view.do?seq=1344586",
    "https://www.mofa.go.kr/www/brd/m_4080/view.do?seq=376381",
]


@pytest.fixture(scope="module")
def gdb(request):
    """The exact standard sequence an operator runs against a target DB:
    baseline import, curated verified route-record import (routes_import
    apply), then the apply-research steps."""
    import json
    import tempfile
    from pathlib import Path

    from app.db import SessionLocal, create_all
    from app.global_routes.orchestrator import recompute_release_statuses
    create_all()
    db = SessionLocal()
    baseline.import_reference_baseline(db)
    # Import ONLY the curated verified records from the canonical file: the
    # historical on-demand records in the same file are exports of other DBs'
    # state and other test modules legitimately re-research those routes.
    # Read the REPO copy (read-only): the session's ELLIS_DATA_DIR copy is
    # legitimately rewritten from DB state by export_all() in other modules.
    repo_file = Path(__file__).resolve().parents[2] / "data" / "global_visa_routes.jsonl"
    src = repo_file.read_text().splitlines()
    curated = [l for l in src
               if json.loads(l).get("record_id", "").startswith("curated-")]
    assert len(curated) >= 2, "curated CHN->KOR / CHN->VNM records missing"
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write("\n".join(curated) + "\n")
        curated_path = Path(fh.name)
    rep = importer.import_file(db, curated_path, apply=True)
    curated_path.unlink(missing_ok=True)
    assert rep.invalid == 0, rep.errors
    baseline.apply_research_overrides(db)
    baseline.ensure_full_pair_coverage(db)
    baseline.sync_matrix_dispositions(db)
    families.sync_families(db)
    families.assign_families_to_pairs(db)
    pipeline.ingest_all_research_jurisdictions(db)
    recompute_release_statuses(db)
    request.addfinalizer(db.close)
    return db


def _pair(db) -> RoutePairPolicy:
    return db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == KOR_PAIR)).scalars().one()


# ---- pair policy: verified visa-center workflow, never electronic ----------

def test_pair_is_verified_authorized_visa_center_workflow(gdb):
    p = _pair(gdb)
    assert p.route_outcome == "AUTHORIZED_VISA_CENTER"
    assert p.disposition == "AUTHORIZED_VISA_CENTER_REQUIRED"
    assert p.route_outcome not in ELECTRONIC_OUTCOMES          # never EVISA
    assert p.route_outcome != "ELECTRONIC_AUTHORIZATION"       # never K-ETA
    assert p.verification_status == "verified"
    assert p.source == "official_research"
    assert p.primary_category == "tourist_visa"                # C-3-9 lives in
    assert p.release_status == "released_workflow"             # notes/metadata
    assert p.portal_family_id == ""                            # no portal bind
    for url in OFFICIAL_URLS:
        assert url in (p.official_source_urls or [])


def test_pair_notes_state_the_verified_process(gdb):
    notes = _pair(gdb).notes
    assert "C-3-9" in notes
    assert "APPOINTMENT" in notes.upper()
    assert "Korea Visa Application Center" in notes
    assert "physical passport" in notes
    assert "visa.go.kr" in notes and "e-form" in notes
    assert "status check" in notes
    assert "K-ETA" in notes and "NOT available" in notes
    assert "2026-12-31" in notes and "individuals are excluded" in notes


def test_released_workflow_counts_as_coverage(gdb):
    from app.global_routes import dashboard
    assert "released_workflow" in dashboard._COVERED_STATUSES
    cov = dashboard.coverage(gdb)
    assert cov["by_release_status"].get("released_workflow", 0) >= 1
    assert cov["totals"]["released_combinations"] >= \
        cov["by_release_status"].get("released_workflow", 0)


# ---- resolver: outcome + jurisdiction + confirmation points ----------------

def test_resolver_returns_visa_center_outcome(gdb):
    rec = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                                 residence="CHN",
                                 residence_subdivision="Shanghai")
    assert rec["route_outcome"] == "AUTHORIZED_VISA_CENTER"
    assert rec["release_status"] == "released_workflow"
    assert rec["verification_status"] == "verified"
    assert rec["governing_adapter"]["required"] is False
    assert rec["governing_adapter"]["status"] == "not_required"
    assert rec["official_channel"] is None      # nothing electronic is claimed
    assert set(rec["applicant_confirmations"]) >= {
        "appointment_confirmation", "final_application_review",
        "personal_appearance"}


def test_jiangsu_residence_yields_the_shanghai_center(gdb):
    for province in ("Jiangsu", "Shanghai", "Zhejiang", "Anhui", "jiangsu"):
        rec = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                                     residence="CHN",
                                     residence_subdivision=province)
        j = rec["jurisdiction"]
        assert j["status"] == "verified", (province, j)
        assert j["competent_post_name"] == \
            "Korea Visa Application Center (KVAC) Shanghai"
        assert j["competent_post_kind"] == "authorized_visa_center"
        assert "visaforkorea-sh.com" in j["competent_post_url"]
        conditions = " ".join(j["conditions"])
        assert "Appointment mandatory since 2026-06-01" in conditions
        assert "RMB 280" in conditions and "RMB 120" in conditions
        assert "7 working days" in conditions


def test_other_districts_stay_honest_manual_selection(gdb):
    # An uncovered province is never guessed onto the Shanghai center.
    rec = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                                 residence="CHN",
                                 residence_subdivision="Guangdong")
    assert rec["route_outcome"] == "AUTHORIZED_VISA_CENTER"
    assert rec["jurisdiction"]["status"] == "manual_selection_required"
    # Residence country alone (no province) never yields a province-scoped post.
    rec2 = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                                  residence="CHN")
    assert rec2["jurisdiction"]["status"] == "manual_selection_required"
    assert rec2["jurisdiction"]["options"]     # the real posts are listed
    # No residence at all -> explicit manual-jurisdiction outcome.
    rec3 = resolver.resolve_route(gdb, nationality="CHN", destination="KOR")
    assert rec3["route_outcome"] == "REQUIRES_MANUAL_JURISDICTION_SELECTION"
    assert rec3["jurisdiction"]["status"] == "residence_required"


def test_resolution_is_deterministic_for_kor(gdb):
    a = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                               residence="CHN", residence_subdivision="Jiangsu")
    b = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                               residence="CHN", residence_subdivision="Jiangsu")
    assert a == b


# ---- no adapter is ever built or bound for this physical route -------------

def test_no_adapter_build_attempted_for_the_pair(gdb):
    from app.global_routes.orchestrator import families_needing_adapters
    p = _pair(gdb)
    assert p.portal_family_id == ""
    # The pair contributes to no family's build queue.
    for fam in families_needing_adapters(gdb):
        governed = gdb.execute(select(RoutePairPolicy).where(
            RoutePairPolicy.portal_family_id == fam.family_id)).scalars().all()
        assert all(g.pair_key != KOR_PAIR for g in governed)


def test_keta_family_is_never_selected_for_chn(gdb):
    # No CHN pair anywhere is bound to the K-ETA portal family.
    bound = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.passport_nationality == "CHN",
        RoutePairPolicy.portal_family_id == "korea-keta")).scalars().all()
    assert bound == []
    # Even a stale K-ETA binding on the verified pair is cleared, never served.
    p = _pair(gdb)
    p.portal_family_id = "korea-keta"
    gdb.commit()
    families.assign_families_to_pairs(gdb)
    gdb.refresh(p)
    assert p.portal_family_id == ""
    rec = resolver.resolve_route(gdb, nationality="CHN", destination="KOR",
                                 residence="CHN")
    assert rec["route_outcome"] != "ELECTRONIC_AUTHORIZATION"
    assert rec["official_channel"] is None


# ---- overrides are idempotent and never regress to provisional -------------

def test_reimport_never_downgrades_the_verified_pair(gdb):
    from app.global_routes.orchestrator import recompute_release_statuses
    baseline.import_reference_baseline(gdb)     # skips existing pairs
    baseline.apply_research_overrides(gdb)      # idempotent re-apply
    recompute_release_statuses(gdb)
    p = _pair(gdb)
    assert p.route_outcome == "AUTHORIZED_VISA_CENTER"
    assert p.verification_status == "verified"
    assert p.release_status == "released_workflow"


def test_provisional_reference_rows_for_other_pairs_untouched(gdb):
    # Only CHN carries official research for destination KOR.
    verified_kor = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.destination_country == "KOR",
        RoutePairPolicy.source == "official_research")).scalars().all()
    assert [p.passport_nationality for p in verified_kor] == ["CHN"]
    others = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.destination_country == "KOR",
        RoutePairPolicy.source == "reference_dataset").limit(50)).scalars().all()
    assert others
    for p in others:
        assert p.verification_status == "provisional"
        assert p.release_status == "defined_provisional"


# ---- jurisdiction ingest: honest verification + idempotence ----------------

def test_jurisdiction_rule_is_verified_via_official_link(gdb):
    rules = gdb.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.destination_country == "KOR",
        ConsularJurisdictionRule.residence_jurisdiction == "CHN")).scalars().all()
    assert len(rules) == 1
    r = rules[0]
    assert r.verification_status == "verified"   # via gov official_linking_source
    assert r.covers_nationalities == ["CHN"]
    assert set(r.residence_subdivisions) == {"Shanghai", "Jiangsu",
                                             "Zhejiang", "Anhui"}
    assert any("overseas.mofa.go.kr" in e for e in r.evidence_ids)


def test_authorized_visa_center_row_created(gdb):
    centers = gdb.execute(select(AuthorizedVisaCenter).where(
        AuthorizedVisaCenter.destination_country == "KOR")).scalars().all()
    assert len(centers) == 1
    assert centers[0].name == "Korea Visa Application Center (KVAC) Shanghai"
    assert centers[0].verification_status == "verified"


def test_chn_vnm_resolves_to_evisa_via_vietnam_family(gdb):
    """Companion override applied through the SAME mechanism: CHN -> VNM is a
    verified EVISA route governed by the vietnam-evisa portal family. The
    release status is derived by the orchestrator recompute — released_adapter
    only when the family adapter is actually released, never forced."""
    from app.global_routes.models import FamilyAdapterLink
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key ==
        pair_key("CHN", "ordinary_passport", "VNM"))).scalars().one()
    assert p.route_outcome == "EVISA"
    assert p.disposition == "EVISA_REQUIRED"
    assert p.primary_category == "evisa_tourist"
    assert p.verification_status == "verified"
    assert p.source == "official_research"
    assert p.max_stay_days == 90
    assert p.portal_family_id == "vietnam-evisa"
    assert "https://evisa.gov.vn/" in (p.official_source_urls or [])
    assert "no portal account required" in p.notes
    link = gdb.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "vietnam-evisa")).scalars().first()
    expected = "released_adapter" if (link is not None and link.released) \
        else "defined_verified"
    assert p.release_status == expected

    rec = resolver.resolve_route(gdb, nationality="CHN", destination="VNM",
                                 residence="CHN")
    assert rec["route_outcome"] == "EVISA"
    assert rec["verification_status"] == "verified"
    assert rec["governing_adapter"]["required"] is True
    assert rec["governing_adapter"]["family_id"] == "vietnam-evisa"
    assert rec["official_channel"]["family_id"] == "vietnam-evisa"
    assert set(rec["applicant_confirmations"]) >= {
        "exact_payment_confirmation", "final_application_review",
        "final_submission_confirmation"}


def test_jurisdiction_ingest_is_idempotent(gdb):
    pipeline.ingest_all_research_jurisdictions(gdb)
    pipeline.ingest_all_research_jurisdictions(gdb)
    n_rules = len(gdb.execute(select(ConsularJurisdictionRule).where(
        ConsularJurisdictionRule.destination_country == "KOR")).scalars().all())
    n_centers = len(gdb.execute(select(AuthorizedVisaCenter).where(
        AuthorizedVisaCenter.destination_country == "KOR")).scalars().all())
    assert n_rules == 1 and n_centers == 1
