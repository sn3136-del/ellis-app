"""Regression tests for the adversarial-review findings on global coverage.

Each test pins a confirmed defect: provenance laundering into the matrix,
digit-concatenated stay days, provisional no-admission counted as covered,
scoped adapter phases draining the global queue, degenerate flows releasing,
stale test layers, unreleasable families frozen by task dedup, unverified
channels shown to applicants, and released coverage surviving revocation.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.global_routes import baseline, families, orchestrator, release_gates
from app.global_routes.models import (FamilyAdapterLink, GlobalBuildTask,
                                      PortalFamily, RoutePairPolicy)
from app.portal.synthetic import SyntheticPortal
from app.visa_snapshot.matrix import matrix_key
from app.visa_snapshot.models import RouteMatrixEntry

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "orgR", "X-User-Id": "u1"}


@pytest.fixture(scope="module")
def gdb(request):
    from app.db import SessionLocal, create_all
    from app.visa_snapshot.matrix import populate
    create_all()
    db = SessionLocal()
    populate(db)
    baseline.import_reference_baseline(db)
    baseline.apply_research_overrides(db)
    baseline.ensure_full_pair_coverage(db)
    baseline.sync_matrix_dispositions(db)
    families.sync_families(db)
    families.assign_families_to_pairs(db)
    orchestrator.recompute_release_statuses(db)
    request.addfinalizer(db.close)
    return db


# ---- CRITICAL: provisional data must never reach the shared matrix ---------

def test_provisional_pairs_never_touch_the_matrix(gdb):
    """The matrix has no provenance column; its consumers read researched
    dispositions as verified. Only official_research/verified pairs may sync."""
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.verification_status == "provisional",
        RoutePairPolicy.disposition == "EMBASSY_VISA_REQUIRED")).scalars().first()
    assert p is not None
    row = gdb.execute(select(RouteMatrixEntry).where(
        RouteMatrixEntry.matrix_key == matrix_key(
            p.passport_nationality, "ordinary_passport",
            p.destination_country, "tourist_visa"))).scalars().first()
    assert row is not None
    assert row.disposition == "RESEARCH_INCOMPLETE", (
        "provisional reference data was laundered into route_matrix_entries")


def test_verified_pairs_do_sync_to_matrix(gdb):
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.verification_status == "verified",
        RoutePairPolicy.source == "official_research",
        RoutePairPolicy.disposition == "VISA_FREE")).scalars().first()
    assert p is not None
    row = gdb.execute(select(RouteMatrixEntry).where(
        RouteMatrixEntry.matrix_key == matrix_key(
            p.passport_nationality, "ordinary_passport",
            p.destination_country, "visa_free_entry"))).scalars().first()
    assert row is not None and row.disposition == "VISA_FREE"


# ---- stay-day parsing ------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("90 days per 180 days", 90),
    ("90 days in any 180-day period", 90),
    ("30 days (extendable) or up to 60 days depending on visa type", 30),
    ("60 days per entry; extendable once by up to 30 days", 60),
    ("visa on arrival", None),
    ("6 months", None),          # no 'day' unit -> honestly None
    (9018, None),                # out of range
    (90, 90),
])
def test_parse_stay_days_first_number_only(text, expected):
    assert baseline._parse_stay_days(text) == expected


def test_no_absurd_verified_stay_days(gdb):
    bad = gdb.execute(select(func.count(RoutePairPolicy.id)).where(
        RoutePairPolicy.verification_status == "verified",
        RoutePairPolicy.max_stay_days > 366)).scalar_one()
    assert bad == 0


# ---- provisional no-admission is never covered coverage --------------------

def test_provisional_no_admission_not_counted_as_covered(gdb):
    rows = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.route_outcome == "NO_AVAILABLE_TOURIST_ROUTE",
        RoutePairPolicy.verification_status == "provisional")).scalars().all()
    assert rows, "dataset contains provisional no-admission pairs"
    for p in rows:
        assert p.release_status == "defined_provisional"


# ---- scoped adapter phase never drains the global queue --------------------

def test_scoped_adapter_phase_confined_to_filter(gdb):
    run = orchestrator.start_run(gdb, "build-family", {})
    # queue an unrelated family task that must NOT run under a scoped command
    orchestrator.enqueue(gdb, run, "family_adapter", "japan-evisa")

    def obs_factory(fid):
        fam = gdb.execute(select(PortalFamily).where(
            PortalFamily.family_id == fid)).scalars().one()
        return SyntheticPortal(scenario="single_step_login",
                               hostname=(fam.hostnames or ["x.gov"])[0]).observe

    out = orchestrator.run_adapter_phase(gdb, run, observer_factory=obs_factory,
                                         only_family="georgia-evisa")
    japan = gdb.execute(select(GlobalBuildTask).where(
        GlobalBuildTask.dedup_key == "family_adapter:japan-evisa")).scalars().one()
    assert japan.status == "queued", "scoped run executed an out-of-scope task"
    assert out["queued"] <= 1


# ---- degenerate flows can never release ------------------------------------

def test_degenerate_two_node_flow_fails_gates(gdb):
    """A NAVIGATE+COMPLETE flow with no fill steps must fail gate 5 even when
    the spec's mapping list is non-empty."""
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.family_id == "moldova-evisa")).scalars().one()

    def empty_observer(url):
        return {"ok": True, "url": url, "hostname": (fam.hostnames or [""])[0],
                "status": 200, "title": "portal", "elements": [], "links": []}
    out = orchestrator.build_family_adapter(gdb, "moldova-evisa",
                                            observer=empty_observer)
    assert out["released"] is False
    assert any("field mappings" in m or "form page" in m for m in out["missing"])


# ---- stale test layers -----------------------------------------------------

def test_latest_test_run_per_layer_wins(gdb):
    from app.adapter_factory import models as fm
    link = gdb.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "georgia-evisa")).scalars().first()
    if link is None or not link.candidate_id:
        pytest.skip("georgia adapter not built in this session")
    version = gdb.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == link.candidate_id)
        .order_by(fm.AdapterCandidateVersion.version.desc())).scalars().first()
    layers_before = release_gates._layers(gdb, version.id)
    assert layers_before.get("CONTRACT_TESTED") is True
    gdb.add(fm.AdapterTestRun(candidate_version_id=version.id, layer="contract",
                              classification="CONTRACT_FAILED", passed=False,
                              summary={}, executed_by="test"))
    gdb.commit()
    layers_after = release_gates._layers(gdb, version.id)
    assert "CONTRACT_TESTED" not in layers_after, (
        "an old green contract run masked the newer failure")
    assert layers_after.get("CONTRACT_FAILED") is False


# ---- unreleased families stay retryable ------------------------------------

def test_gates_not_passed_marks_task_failed_and_retryable(gdb):
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.family_id == "azerbaijan-evisa")).scalars().one()
    run = orchestrator.start_run(gdb, "build-family", {})

    def empty_factory(fid):
        def obs(url):
            return {"ok": True, "url": url, "hostname": (fam.hostnames or [""])[0],
                    "status": 200, "title": "t", "elements": [], "links": []}
        return obs

    orchestrator.run_adapter_phase(gdb, run, observer_factory=empty_factory,
                                   only_family="azerbaijan-evisa")
    task = gdb.execute(select(GlobalBuildTask).where(
        GlobalBuildTask.dedup_key == "family_adapter:azerbaijan-evisa")).scalars().one()
    assert task.status == "failed"
    assert task.error_class == "transient"    # retryable via retry-failed
    assert "gates not passed" in task.error
    run2 = orchestrator.start_run(gdb, "retry", {})
    requeued = orchestrator.enqueue(gdb, run2, "family_adapter", "azerbaijan-evisa")
    assert requeued is not None and requeued.status == "queued"


# ---- released coverage reverts on revocation -------------------------------

def test_release_reverts_after_kill_switch(gdb):
    def obs_factory_host(fid):
        fam = gdb.execute(select(PortalFamily).where(
            PortalFamily.family_id == fid)).scalars().one()
        return SyntheticPortal(scenario="single_step_login",
                               hostname=(fam.hostnames or ["x.gov"])[0]).observe
    out = orchestrator.build_family_adapter(
        gdb, "uzbekistan-evisa", observer=obs_factory_host("uzbekistan-evisa"))
    assert out["released"] is True
    link = gdb.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "uzbekistan-evisa")).scalars().one()
    from app.adapter_factory import release as releasesvc
    releasesvc.engage_kill_switch(gdb, candidate_id=link.candidate_id,
                                  actor="admin-human", is_admin=True,
                                  reason="drill")
    orchestrator.recompute_release_statuses(gdb)
    gdb.refresh(link)
    assert link.released is False
    assert "revoked" in (link.status + link.last_error)


# ---- applicant surface hides unverified channels ---------------------------

def test_applicant_endpoint_hides_unverified_channel(client, gdb):
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.portal_family_id != "")).scalars().all()
    target = None
    for pair in p:
        fam = gdb.execute(select(PortalFamily).where(
            PortalFamily.family_id == pair.portal_family_id)).scalars().first()
        if fam is not None and fam.verification_status == "seed_unverified":
            target = pair
            break
    if target is None:
        pytest.skip("no pair bound to an unverified family")
    r = client.get("/global/route-outcome",
                   params={"nationality": target.passport_nationality,
                           "destination": target.destination_country},
                   headers=H)
    assert r.status_code == 200
    assert r.json()["official_channel"] is None


# ---- error classification --------------------------------------------------

def test_classify_error_permanent_marker_wins():
    assert orchestrator.classify_error(
        "RuntimeError: permanent: portal family x is not officially verified "
        "(seed_unverified) — build refused") == "permanent"
    assert orchestrator.classify_error("connection reset") == "transient"
    assert orchestrator.classify_error(
        "release gates not passed (retryable): missing: x") == "transient"


# ---- csv integrity ---------------------------------------------------------

def test_tampered_baseline_csv_refused(gdb, tmp_path, monkeypatch):
    fake = tmp_path / "baseline.csv"
    fake.write_text("Passport,Destination,Requirement\nUSA,FRA,visa free\n")
    monkeypatch.setattr(baseline, "BASELINE_CSV", fake)
    with pytest.raises(RuntimeError, match="sha256"):
        baseline.import_reference_baseline(gdb)
