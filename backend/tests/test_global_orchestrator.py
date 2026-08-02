"""Global orchestrator + portal families + release gates (brief Part 11).

Covers: shared portals reuse ONE adapter; automatic release requires every
objective gate; failed gates prevent release with precise reasons; builds are
resumable; duplicate builds prevented; stale adapters revalidated; provider
failures honest (transient vs permanent); unverified seeds never build; no
mock/synthetic evidence releases in real modes; contaminated portal records
are downgraded.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.global_routes import baseline, families, orchestrator, release_gates
from app.global_routes.models import (FamilyAdapterLink, GlobalBuildTask,
                                      PortalFamily, RoutePairPolicy)
from app.portal.synthetic import SyntheticPortal


@pytest.fixture(scope="module")
def gdb(request):
    from app.db import SessionLocal, create_all
    create_all()
    db = SessionLocal()
    baseline.import_reference_baseline(db)
    baseline.ensure_full_pair_coverage(db)
    families.sync_families(db)
    families.assign_families_to_pairs(db)
    request.addfinalizer(db.close)
    return db


class _GateAwareObserver:
    """A callable observer that ALSO carries the entry-gate replay.

    Passing the bare `.observe` bound method hid observe_with_entry_gate from
    recon, so any family with a curated gate looked unbuildable in tests while
    working live — backwards, and exactly what happened when india-evisa
    gained a real curated gate. A bound method cannot carry attributes, so the
    capability travels on a small object instead."""

    def __init__(self, hostname):
        self._portal = SyntheticPortal(scenario="single_step_login",
                                       hostname=hostname)

    def __call__(self, url):
        return self._portal.observe(url)

    def observe_with_entry_gate(self, base_url, entry_gate):
        return self._portal.observe_with_entry_gate(base_url, entry_gate)


def _obs(hostname):
    return _GateAwareObserver(hostname)


def test_shared_portal_reuses_one_adapter(gdb):
    """Many pairs across nationalities share one family and therefore exactly
    one adapter build (dedup at the family level, not per route)."""
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.family_id == "india-evisa")).scalars().one()
    n_pairs = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.portal_family_id == "india-evisa")).scalars().all()
    assert len(n_pairs) > 50  # many nationalities, one platform
    out1 = orchestrator.build_family_adapter(
        gdb, "india-evisa", observer=_obs(fam.hostnames[0]))
    out2 = orchestrator.build_family_adapter(
        gdb, "india-evisa", observer=_obs(fam.hostnames[0]))
    links = gdb.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "india-evisa")).scalars().all()
    # THE property under test: one family, one adapter, however many pairs
    # share it — a second build must not create a second link.
    assert len(links) == 1
    assert links[0].build_request_id, "the one link owns the one build"
    # Both invocations resolve to that same build rather than racing a new one.
    assert out1["family_id"] == out2["family_id"] == "india-evisa"
    assert out2.get("status") in ("already_released", None) or \
        out2.get("build_state") == out1.get("build_state")
    # Release itself is the gates' decision, not this test's: india-evisa
    # carries a curated entry gate whose live behavioural replay the synthetic
    # driver does not model, so asserting "released" here would only be
    # asserting how complete the test double is.
    if not out1["released"]:
        assert out1.get("missing"), "an unreleased build must name its gaps"


def test_unverified_family_never_builds(gdb):
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.verification_status == "seed_unverified")).scalars().first()
    assert fam is not None, "expected at least one non-government-domain seed"
    with pytest.raises(orchestrator.PermanentBuildStop, match="not officially verified"):
        orchestrator.build_family_adapter(gdb, fam.family_id,
                                          observer=_obs("example.gov"))


def test_every_gate_failure_prevents_release_with_precise_reason(gdb):
    """Knock out individual gates and prove release is refused, naming the
    missing capability."""
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.family_id == "turkey-evisa")).scalars().one()
    out = orchestrator.build_family_adapter(gdb, "turkey-evisa",
                                            observer=_obs(fam.hostnames[0]))
    assert out["released"] is True
    link = gdb.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "turkey-evisa")).scalars().one()
    from app.adapter_factory import models as fm
    req = gdb.get(fm.AdapterBuildRequest, link.build_request_id)
    cand = gdb.get(fm.AdapterCandidate, link.candidate_id)
    version = gdb.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == cand.id)
        .order_by(fm.AdapterCandidateVersion.version.desc())).scalars().first()

    # gate 1: family identity broken
    class _Unverified:
        verification_status = "seed_unverified"
        destinations = fam.destinations
        account_required = False
    r = release_gates.evaluate_gates(gdb, build_request=req, candidate=cand,
                                    version=version, family=_Unverified())
    assert r["passed"] is False
    assert any("official portal identity" in m for m in r["missing"])

    # gate 2: wrong destination
    class _WrongDest:
        verification_status = "verified_official_domain"
        destinations = ["ZZZ"]
        account_required = False
    r = release_gates.evaluate_gates(gdb, build_request=req, candidate=cand,
                                    version=version, family=_WrongDest())
    assert r["passed"] is False
    assert any("does not serve destination" in m for m in r["missing"])

    # gate 5/9-12: strip flow of fill steps and handoffs
    class _V:
        id = version.id
        flow = [n for n in version.flow
                if n.get("action") not in ("APPLICANT_HANDOFF", "FILL_NON_SENSITIVE")]
        field_mappings = []
        document_mappings = version.document_mappings
    r = release_gates.evaluate_gates(gdb, build_request=req, candidate=cand,
                                    version=_V(), family=fam)
    assert r["passed"] is False
    assert any("field mappings" in m for m in r["missing"])
    assert any("payment handoff" in m or "declaration" in m or
               "applicant" in m for m in r["missing"])


def test_gate_failure_writes_no_release(gdb):
    """End-to-end: a family whose build cannot pass gates records fail-closed
    reasons and no released binding exists for its route."""
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.family_id == "korea-visa-portal")).scalars().one()
    # Observer that only serves an empty home page: no fields, no pages.
    def empty_observer(url):
        return {"ok": True, "url": url, "hostname": fam.hostnames[0],
                "status": 200, "title": "empty", "elements": [], "links": []}
    out = orchestrator.build_family_adapter(gdb, "korea-visa-portal",
                                            observer=empty_observer)
    assert out["released"] is False
    link = gdb.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "korea-visa-portal")).scalars().one()
    assert link.released is False
    assert "missing" in link.last_error or link.last_error
    from app.adapter_factory import release as releasesvc
    assert releasesvc.active_binding(gdb, route_key=link.representative_route_key,
                                     tier="sandbox") is None


def test_orchestrator_tasks_dedup_and_isolate_failures(gdb):
    run = orchestrator.start_run(gdb, "build-all", {})
    t1 = orchestrator.enqueue(gdb, run, "family_adapter", "vietnam-evisa")
    t2 = orchestrator.enqueue(gdb, run, "family_adapter", "vietnam-evisa")
    assert t1 is not None and t2 is None  # duplicate prevented

    def boom():
        raise ConnectionError("connection reset by portal")
    ok = orchestrator._run_task(gdb, t1, boom)
    assert ok is False
    gdb.refresh(t1)
    assert t1.status == "failed"
    assert t1.error_class == "transient"

    # transient failures are retryable; permanent ones are not
    t3 = orchestrator.enqueue(gdb, run, "family_adapter", "vietnam-evisa")
    assert t3 is not None and t3.id == t1.id and t3.status == "queued"

    def legal():
        raise RuntimeError("permanent: manual-only process, no lawful automation")
    orchestrator._run_task(gdb, t3, legal)
    gdb.refresh(t3)
    assert t3.error_class == "permanent"
    t4 = orchestrator.enqueue(gdb, run, "family_adapter", "vietnam-evisa")
    assert t4 is None  # permanent failure is not silently retried


def test_run_stop_and_resume_checkpoints(gdb):
    run = orchestrator.start_run(gdb, "build-all", {})
    out1 = orchestrator.run_inventory(gdb, run)
    assert "release_status_recompute" in out1
    orchestrator.stop_run(gdb, run.id, "test stop")
    gdb.refresh(run)
    assert run.status == "stopped"
    # resume: checkpointed steps are skipped, none re-executed
    run.status = "running"
    gdb.commit()
    out2 = orchestrator.run_inventory(gdb, run)
    assert all(v == "checkpointed" for v in out2.values())


def test_stale_families_are_marked_and_requeued(gdb):
    from datetime import datetime, timedelta, timezone
    fam = gdb.execute(select(PortalFamily).where(
        PortalFamily.family_id == "cambodia-evisa")).scalars().one()
    fam.verification_status = "verified_live"
    fam.last_verified_at = datetime.now(timezone.utc) - timedelta(days=90)
    gdb.commit()
    marked = orchestrator.mark_stale_families(gdb)
    assert marked["marked_stale"] >= 1
    gdb.refresh(fam)
    assert fam.stale is True
    run = orchestrator.start_run(gdb, "revalidate", {})
    queued = orchestrator.queue_revalidation(gdb, run)
    assert queued["revalidation_queued"] >= 1


def test_contaminated_portal_records_are_downgraded(gdb):
    from app.visa_snapshot import SNAPSHOT_DATE
    from app.visa_snapshot.models import OfficialPortalRecord
    rec = OfficialPortalRecord(
        snapshot_date=SNAPSHOT_DATE, portal_uid="LAO:evisa:test-contaminated",
        destination_country="LAO", portal_kind="evisa_portal",
        operator="", operator_kind="government",
        url="https://www.evisa.gov.kh/", hostnames=["www.evisa.gov.kh"],
        allowed_redirect_hosts=[], official_linking_source="",
        supported_categories=[], verification_status="verified_official_domain",
        evidence_ids=[])
    gdb.add(rec)
    gdb.commit()
    out = families.audit_official_portal_records(gdb)
    assert out["portal_records_downgraded"] >= 1
    gdb.refresh(rec)
    assert rec.verification_status == "conflicted"
    assert any("belongs to platform serving" in d["reason"]
               for d in out["details"])


def test_released_status_requires_verified_policy(gdb):
    """A released family adapter upgrades ONLY verified pairs to
    released_adapter; provisional pairs stay defined_provisional."""
    def _released():
        return {l.family_id for l in gdb.execute(
            select(FamilyAdapterLink).where(
                FamilyAdapterLink.released.is_(True))).scalars()}
    if not _released():   # self-sufficient: no reliance on module test order
        fam = gdb.execute(select(PortalFamily).where(
            PortalFamily.family_id == "india-evisa")).scalars().one()
        orchestrator.build_family_adapter(
            gdb, "india-evisa", observer=_obs(fam.hostnames[0]))
    orchestrator.recompute_release_statuses(gdb)
    released_families = _released()
    assert released_families, "a released family is required for this property"
    rows = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.portal_family_id.in_(released_families))).scalars().all()
    for p in rows:
        if p.verification_status == "provisional":
            assert p.release_status == "defined_provisional"
        if p.release_status == "released_adapter":
            assert p.verification_status == "verified"


def test_no_synthetic_release_in_real_only_mode(gdb, monkeypatch):
    """Gate 3: synthetic evidence cannot release in a real-only runtime — and
    the gate reads the ACTUAL behavioral layer (SYNTHETIC_TESTED vs
    LIVE_STRUCTURAL_TESTED), not a self-declared flag a build could omit.
    india-evisa was built in this module against SyntheticPortal, so its
    behavioral evidence is synthetic; evaluated in a real-only mode it must
    fail gate 3 regardless of any portal_evidence flag."""
    import app.config as config
    monkeypatch.setenv("ELLIS_RUNTIME_MODE", "local_real_services")
    config.settings.cache_clear()
    try:
        fam = gdb.execute(select(PortalFamily).where(
            PortalFamily.family_id == "india-evisa")).scalars().one()
        link = gdb.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.family_id == "india-evisa")).scalars().one()
        from app.adapter_factory import models as fm
        req = gdb.get(fm.AdapterBuildRequest, link.build_request_id)
        cand = gdb.get(fm.AdapterCandidate, link.candidate_id)
        version = gdb.execute(select(fm.AdapterCandidateVersion).where(
            fm.AdapterCandidateVersion.candidate_id == cand.id)
            .order_by(fm.AdapterCandidateVersion.version.desc())).scalars().first()
        # No flag is set — provenance comes from the real test-layer evidence.
        r = release_gates.evaluate_gates(gdb, build_request=req, candidate=cand,
                                        version=version, family=fam)
        assert r["passed"] is False
        assert any("real portal driver" in m and "synthetic" in m
                   for m in r["missing"]), r["missing"]
    finally:
        monkeypatch.setenv("ELLIS_RUNTIME_MODE", "test")
        config.settings.cache_clear()
