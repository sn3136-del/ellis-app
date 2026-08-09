"""H1B platform-integration pins (Agent C).

Three integration points where the H1B edition rides shared tourist machinery
and must NOT be allowed to behave like a tourist case:

  #7  Released-flow route resolution: an h1b_* child resolves its portal family
      by PURPOSE (visa_type), never through the purpose-blind pair_key, so a
      petition filing can never bind the tourist USA adapter. H1B adapters are
      unreleased today, so an H1B step fails closed honestly instead.
  #16 Privacy export: export_case must INCLUDE the two-party CaseParty rows and
      the H1bCaseStep pipeline (receipts) — the bundle legally must return them,
      not only erase them.
  #17 Required applicant info: H1B cases carry their own per-visa_type required
      set (employer/job/wage for petitioner steps, identity for the beneficiary
      consular leg) so a live start never 409s demanding tourist facts.

All hermetic: no network, real fixtures, precise per-test cleanup.
"""
import pytest
from sqlalchemy import select

from app import models, personal_gate, privacy
from app.adapter_factory import models as fm
from app.global_routes.models import (FamilyAdapterLink, PortalFamily,
                                       RoutePairPolicy, pair_key)
from app.h1b import filing as h1b_filing
from app.h1b import models as h1b_models
from app.portal import released_flow

from .conftest import AUTH

# The 16 release gates that must all pass for the deterministic (no-admin)
# personal-gate derivation to fire (mirrors test_released_flow_bridge).
_RELEASE_GATES = (
    "official_portal_identity_confirmed", "destination_and_jurisdiction_correct",
    "no_mock_or_synthetic_driver", "safe_navigation_succeeded",
    "required_fields_mapped", "selectors_verified_repeated_sessions",
    "account_flow_mapped_where_applicable", "upload_flow_mapped_where_applicable",
    "applicant_confirmation_gates_preserved", "captcha_otp_handoffs_preserved",
    "payment_confirmation_preserved", "submission_confirmation_preserved",
    "no_irreversible_action_executed_in_testing", "structured_provider_errors",
    "security_scan_passed", "regression_tests_passed")


# A nationality/pair that no seed data uses, so binding it to a released
# tourist family never mutates a real route policy. String(3) fits "QZZ".
_FAKE_NAT = "QZZ"


@pytest.fixture()
def released(db):
    """Attach a released sandbox adapter to a portal family, and/or bind a route
    pair to a family — undoing EXACTLY what it changed on teardown.

    The session DB is shared and the real H1B families (usa-dol-flag,
    usa-uscis-myaccount, usa-ceac) ship as seed data, so this get-or-creates
    every row and records a precise undo (delete what it inserted, restore what
    it mutated) rather than wiping shared tables."""
    undo = []            # LIFO teardown callables
    candidate_ids = []

    def _delete_where(table, col, value):
        for row in db.execute(select(table).where(col == value)).scalars().all():
            db.delete(row)

    def release_family(family_id, route_key, *, verification_status="verified_live"):
        fam = db.execute(select(PortalFamily).where(
            PortalFamily.family_id == family_id)).scalars().first()
        if fam is None:
            db.add(PortalFamily(
                family_id=family_id, name=family_id, kind="immigration_authority",
                operator="Test Operator", base_url="https://portal.test.gov/",
                hostnames=["portal.test.gov"], destinations=["USA"],
                account_required=False, verification_status=verification_status))
            undo.append(lambda fid=family_id: _delete_where(
                PortalFamily, PortalFamily.family_id, fid))
        # A seed family (verified_official_domain) is left untouched — its
        # status already resolves.
        cand = fm.AdapterCandidate(
            build_request_id="req-" + route_key, route_key=route_key,
            adapter_id="ad-" + route_key, current_version=1, status="released")
        db.add(cand)
        db.flush()
        candidate_ids.append(cand.id)
        db.add(fm.AdapterCandidateVersion(
            candidate_id=cand.id, version=1,
            manifest={"route_key": route_key,
                      "allowed_hostnames": ["portal.test.gov"]},
            flow=[{"node_id": "done", "action": "COMPLETE"}],
            field_mappings=[], document_mappings=[],
            evidence_rules={"banner_text_sufficient": False},
            kill_switch_key="ks-" + route_key))
        rel = fm.AdapterRelease(
            candidate_id=cand.id, candidate_version=1, route_key=route_key,
            tier="sandbox", released_by="deterministic-release-engine",
            release_kind="deterministic_auto", evidence_package={}, active=True)
        db.add(rel)
        db.flush()
        db.add(fm.AdapterRuntimeBinding(
            route_key=route_key, tier="sandbox", candidate_id=cand.id,
            candidate_version=1, release_id=rel.id))
        gates = {name: True for name in _RELEASE_GATES}
        report = {"passed": True, "missing": [], "gates": gates}
        link = db.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.family_id == family_id)).scalars().first()
        if link is None:
            db.add(FamilyAdapterLink(
                family_id=family_id, candidate_id=cand.id,
                representative_route_key=route_key, status="released",
                released=True, release_tier="sandbox", gate_report=report))
            undo.append(lambda fid=family_id: _delete_where(
                FamilyAdapterLink, FamilyAdapterLink.family_id, fid))
        else:
            snap = {k: getattr(link, k) for k in (
                "candidate_id", "representative_route_key", "status", "released",
                "release_tier", "gate_report")}
            undo.append(lambda l=link, s=snap: [setattr(l, k, v)
                                                for k, v in s.items()])
            link.candidate_id = cand.id
            link.representative_route_key = route_key
            link.status = "released"
            link.released = True
            link.release_tier = "sandbox"
            link.gate_report = report
        db.commit()

    def bind_pair(nat, doc, dest, family_id):
        pk = pair_key(nat, doc, dest)
        pol = db.execute(select(RoutePairPolicy).where(
            RoutePairPolicy.pair_key == pk)).scalars().first()
        if pol is None:
            db.add(RoutePairPolicy(
                snapshot_date="2026-08-09", pair_key=pk, passport_nationality=nat,
                travel_document_type=doc, destination_country=dest,
                portal_family_id=family_id, disposition="VISA_REQUIRED",
                route_outcome="VISA", verification_status="verified"))
            undo.append(lambda k=pk: _delete_where(
                RoutePairPolicy, RoutePairPolicy.pair_key, k))
        else:
            orig = pol.portal_family_id
            undo.append(lambda p=pol, o=orig: setattr(p, "portal_family_id", o))
            pol.portal_family_id = family_id
        db.commit()

    yield type("Released", (), {"family": staticmethod(release_family),
                                "pair": staticmethod(bind_pair)})

    for cid in candidate_ids:
        for table, col in ((fm.AdapterRuntimeBinding, fm.AdapterRuntimeBinding.candidate_id),
                           (fm.AdapterRelease, fm.AdapterRelease.candidate_id),
                           (fm.AdapterExecution, fm.AdapterExecution.candidate_id),
                           (fm.AdapterCandidateVersion, fm.AdapterCandidateVersion.candidate_id),
                           (fm.AdapterCandidate, fm.AdapterCandidate.id)):
            _delete_where(table, col, cid)
    for fn in reversed(undo):
        fn()
    db.commit()


def _app(db, *, visa_type, answers=None, dest="United States", org="org1"):
    applicant = models.Applicant(org_id=org, user_id="user1",
                                 full_name="WEI ZHANG", email="wei@example.com")
    db.add(applicant)
    db.flush()
    row = models.VisaApplication(
        org_id=org, user_id="user1", applicant_id=applicant.id,
        destination_country=dest, visa_type=visa_type, state="DRAFT",
        answers=answers or {})
    db.add(row)
    db.commit()
    return row


# ============================================================================
# #7  Route resolution binds by purpose, never the tourist adapter
# ============================================================================

def test_h1b_lca_child_never_binds_tourist_usa_adapter(db, released):
    """The core finding-#7 pin. A released tourist USA family is bound to the
    beneficiary's (nationality|passport|USA) pair — so the OLD purpose-blind
    pair_key path WOULD bind it. A tourist case still resolves it (guard), but
    the h1b_lca child must NOT: its family is usa-dol-flag, which has no released
    adapter today, so it fails closed honestly instead of riding the tourist
    adapter."""
    released.family("test-usa-tourist", "rk-tourist-usa")
    released.pair(_FAKE_NAT, "ordinary_passport", "USA", "test-usa-tourist")

    tourist = _app(db, visa_type="tourist",
                   answers={"passport_nationality": _FAKE_NAT,
                            "travel_document_type": "ordinary_passport"})
    tourist_route = released_flow.resolve_released_route(db, tourist)
    # Guard: the tourist product still resolves through the pair_key path.
    assert tourist_route is not None
    assert tourist_route.family.family_id == "test-usa-tourist"

    lca_child = _app(db, visa_type="h1b_lca",
                     answers={"passport_nationality": _FAKE_NAT,
                              "travel_document_type": "ordinary_passport",
                              "h1b_parent_case_id": "p", "h1b_step_key": "lca"})
    # The petition filing NEVER rides the released tourist adapter; it stops.
    assert released_flow.resolve_released_route(db, lca_child) is None


def test_h1b_lca_child_resolves_to_dol_family_when_released(db, released):
    """Positive step-keyed path: once usa-dol-flag carries a released adapter,
    the h1b_lca child resolves to IT (never a pair_key/tourist family)."""
    released.family("usa-dol-flag", "rk-usa-dol-flag")
    lca_child = _app(db, visa_type="h1b_lca",
                     answers={"passport_nationality": _FAKE_NAT})
    route = released_flow.resolve_released_route(db, lca_child)
    assert route is not None
    assert route.family.family_id == "usa-dol-flag"
    assert route.route_key == "rk-usa-dol-flag"


def test_h1b_registration_and_i129_map_to_uscis(db, released):
    """registration and i129 both resolve to usa-uscis-myaccount; with no
    released adapter they fail closed, with one they bind that family."""
    reg = _app(db, visa_type="h1b_registration",
               answers={"passport_nationality": _FAKE_NAT})
    i129 = _app(db, visa_type="h1b_i129",
                answers={"passport_nationality": _FAKE_NAT})
    # Unreleased today: honest stop, never a tourist bind.
    assert released_flow.resolve_released_route(db, reg) is None
    assert released_flow.resolve_released_route(db, i129) is None

    released.family("usa-uscis-myaccount", "rk-usa-uscis")
    for child in (reg, i129):
        route = released_flow.resolve_released_route(db, child)
        assert route is not None
        assert route.family.family_id == "usa-uscis-myaccount"


def test_parent_h1b_umbrella_never_binds_tourist_adapter(db, released):
    """The umbrella 'h1b' parent has no filing family of its own, so even with a
    released tourist USA family bound to its pair it resolves to nothing — the
    parent never runs a tourist portal."""
    released.family("test-usa-tourist", "rk-tourist-usa")
    released.pair(_FAKE_NAT, "ordinary_passport", "USA", "test-usa-tourist")
    parent = _app(db, visa_type="h1b",
                  answers={"passport_nationality": _FAKE_NAT,
                           "travel_document_type": "ordinary_passport"})
    assert released_flow.resolve_released_route(db, parent) is None


def test_map_covers_every_child_visa_type():
    """Every h1b child visa_type filing.VISA_TYPE_BY_STEP can produce has a
    purpose-specific portal family — a new step must not silently fall back to
    the pair_key path."""
    for visa_type in h1b_filing.VISA_TYPE_BY_STEP.values():
        assert visa_type in released_flow.H1B_PORTAL_FAMILY_BY_VISA_TYPE, visa_type


# ============================================================================
# #7 (gate side)  Deterministic gate completion resolves H1B by purpose too
# ============================================================================

def test_h1b_route_never_inherits_tourist_release_gates(db, released):
    """personal_gate.deterministic_gate_completion must resolve the H1B family by
    PURPOSE. A released tourist family is bound to the (nationality|passport|USA)
    pair; the tourist route derives off it (sanity), but NO H1B visa_type does —
    none of them map to that family, so an H1B route can never be marked
    live-ready off tourist release evidence."""
    released.family("test-usa-tourist", "rk-tourist-usa")
    released.pair(_FAKE_NAT, "ordinary_passport", "USA", "test-usa-tourist")

    tourist = personal_gate.deterministic_gate_completion(
        db, destination="United States", visa_type="tourist", nationality=_FAKE_NAT)
    assert tourist, "tourist route should still derive gates from its release"

    for vt in ("h1b_lca", "h1b_registration", "h1b_i129", "h1b_ds160", "h1b"):
        derived = personal_gate.deterministic_gate_completion(
            db, destination="United States", visa_type=vt, nationality=_FAKE_NAT)
        assert derived == {}, f"{vt} inherited tourist release evidence"


def test_h1b_gate_completion_derives_from_its_own_released_family(db, released):
    """When the H1B step's OWN family is released, the gate derivation fires for
    it (proving the branch resolves by the purpose map, not pair_key)."""
    released.family("usa-dol-flag", "rk-usa-dol-flag")
    derived = personal_gate.deterministic_gate_completion(
        db, destination="United States", visa_type="h1b_lca", nationality=_FAKE_NAT)
    assert derived, "h1b_lca should derive from usa-dol-flag's release"
    assert "usa-dol-flag" in derived["admin_approval_recorded"]["evidence"]


# ============================================================================
# #16  Privacy export includes CaseParty + H1bCaseStep
# ============================================================================

def _create_h1b_case_with_employer(client):
    prof = client.post("/h1b/employer-profiles", headers=AUTH, json={
        "legal_name": "Trip.com US Inc", "fein": "123456789",
        "signatory_name": "Jane Officer",
        "signatory_email": "jane@tripcom.example"}).json()["employer_profile_id"]
    out = client.post("/h1b/cases", headers=AUTH, json={
        "case_kind": "extension", "beneficiary_full_name": "WEI ZHANG",
        "beneficiary_email": "wei.zhang@example.com",
        "beneficiary_abroad": False, "beneficiary_in_us": True,
        "first_h1b": False, "employer_profile_id": prof})
    assert out.status_code == 200, out.text
    return out.json()["case_id"]


def test_export_includes_both_parties_and_pipeline_steps(client, db):
    case_id = _create_h1b_case_with_employer(client)
    bundle = privacy.export_case(db, case_id)

    parties = {p["role"]: p for p in bundle["case_parties"]}
    assert set(parties) == {"beneficiary", "petitioner"}
    assert parties["beneficiary"]["display_name"] == "WEI ZHANG"
    assert parties["petitioner"]["display_name"] == "Trip.com US Inc"
    # The petitioner party carries the employer binding the export must surface.
    assert parties["petitioner"]["employer_profile_id"]

    steps = {s["step_key"] for s in bundle["h1b_steps"]}
    assert steps == {"lca", "i129"}          # the extension plan
    for s in bundle["h1b_steps"]:
        # Receipt columns are part of the portable bundle (empty until verified).
        assert "lca_number" in s and "uscis_receipt_number" in s

    # Secret-free: no provider secret patterns and no vault refs anywhere.
    import json as _json
    blob = _json.dumps(bundle)
    assert "sk-" not in blob and "BEGIN PRIVATE KEY" not in blob
    assert "credential_ref" not in blob and "session_ref" not in blob


def test_export_and_erasure_cover_the_same_h1b_tables(client, db):
    """The models.py docstring promises export AND erasure stay complete via
    _CASE_CHILD_MODELS; export reads them into the bundle, erasure removes them.
    This pins both halves so neither drifts from the other."""
    case_id = _create_h1b_case_with_employer(client)
    assert h1b_models.CaseParty in privacy._CASE_CHILD_MODELS
    assert h1b_models.H1bCaseStep in privacy._CASE_CHILD_MODELS
    bundle = privacy.export_case(db, case_id)
    assert bundle["case_parties"] and bundle["h1b_steps"]

    privacy.delete_case(db, case_id)
    assert db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id)).scalars().all() == []
    assert db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id)).scalars().all() == []


def test_tourist_export_unaffected_by_h1b_keys(client, db):
    """A tourist case exports with empty party/step lists — never a regression
    of the tourist bundle."""
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com",
        "destination_country": "Mockland", "visa_type": "tourist"}).json()["id"]
    bundle = privacy.export_case(db, cid)
    assert bundle["case_parties"] == [] and bundle["h1b_steps"] == []
    assert bundle["applicant"]["email"] == "a@e.com"


# ============================================================================
# #17  H1B has an h1b-aware REQUIRED_APPLICANT_INFO source
# ============================================================================

_TOURIST_ONLY_KEYS = {"travel_purpose", "intended_arrival", "intended_departure",
                      "visa_subtype", "current_residence"}


def test_h1b_case_never_demands_tourist_required_info(db):
    """The umbrella parent and every child stop demanding the tourist-shaped
    keys that no H1B surface asks and no H1B answer point can satisfy."""
    for vt in ("h1b", "h1b_lca", "h1b_registration", "h1b_i129", "h1b_ds160"):
        row = _app(db, visa_type=vt, answers={})
        missing = {m["key"] for m in personal_gate.missing_applicant_info(row, db=db)}
        assert not (missing & _TOURIST_ONLY_KEYS), (vt, missing)


def test_h1b_petitioner_step_demands_employer_facts_and_clears_when_present(db):
    """An h1b_lca child with no answers is missing the employer/job/wage facts
    the ETA-9035 states; supplying exactly those clears the gate (no leftover
    tourist keys keep it 409ing)."""
    empty = _app(db, visa_type="h1b_lca", answers={})
    missing = {m["key"] for m in personal_gate.missing_applicant_info(empty, db=db)}
    assert {"employer_fein", "employer_legal_name", "job_title", "wage_offer",
            "prevailing_wage"} <= missing

    full = _app(db, visa_type="h1b_lca", answers={
        "employer_legal_name": "Trip.com US Inc", "employer_fein": "123456789",
        "job_title": "Software Engineer", "wage_offer": "150000",
        "prevailing_wage": "142000"})
    assert personal_gate.missing_applicant_info(full, db=db) == []


def test_beneficiary_consular_step_demands_identity_only(db):
    """The ds160 consular leg needs beneficiary identity, not employer facts."""
    empty = _app(db, visa_type="h1b_ds160", answers={})
    missing = {m["key"] for m in personal_gate.missing_applicant_info(empty, db=db)}
    assert {"nationality", "full_name", "birth_date", "passport_number"} <= missing
    assert not (missing & {"employer_fein", "job_title"})


def test_live_start_lists_h1b_facts_not_tourist_facts(db):
    """A live-class preflight on an under-populated h1b_lca child raises
    PreparationOnlyMode whose missing_info names H1B facts — never the tourist
    keys that finding #17 showed a live start would otherwise 409 on."""
    from app.execution import ExecutionClass as EC
    row = _app(db, visa_type="h1b_lca", answers={})
    with pytest.raises(personal_gate.PreparationOnlyMode) as ei:
        personal_gate.assert_ready_for_live_action(db, row, EC.LIVE_PRODUCTION)
    missing = set(ei.value.missing_info)
    assert "employer_fein" in missing
    assert not (missing & _TOURIST_ONLY_KEYS)


def test_every_h1b_gate_required_fact_has_a_guaranteed_source():
    """CONTRACT (mirrors test_personal_gate's tourist guaranteed-source pin,
    for the H1B entry points). Every fact H1B_REQUIRED_APPLICANT_INFO demands
    must have a REAL source that guarantees the value, or a live H1B start will
    409 on a fact no surface can supply — exactly the guaranteed-source bug
    class, reintroduced through a new entry point.

    The four H1B sources, none of them the tourist wizard:
      1. the H1B creation endpoint seeds beneficiary identity      (parent-seeded)
      2. EmployerProfile columns feed petitioner filings           (filing._EMPLOYER_PROFILE_KEYS)
      3. petitioner party answers feed job/wage facts              (filing.PETITIONER_SHARED_KEYS)
      4. the parent's beneficiary identity + passport OCR pipeline  (filing.BENEFICIARY_IDENTITY_KEYS + PROFILE_FIELDS)
    """
    from app.visa_snapshot.intake_flow import PROFILE_FIELDS

    guaranteed = set()
    guaranteed |= {"full_name", "email"}                       # create_h1b_case seeds these
    guaranteed |= {key for _attr, key in h1b_filing._EMPLOYER_PROFILE_KEYS}
    guaranteed |= set(h1b_filing.PETITIONER_SHARED_KEYS)
    guaranteed |= set(h1b_filing.BENEFICIARY_IDENTITY_KEYS)
    guaranteed |= set(PROFILE_FIELDS)                          # passport OCR (mandatory item)

    unsourced = {}
    for visa_type, required in personal_gate.H1B_REQUIRED_APPLICANT_INFO.items():
        gap = [k for k in required if k not in guaranteed]
        if gap:
            unsourced[visa_type] = gap
    assert not unsourced, (
        f"H1B required info with no guaranteed source: {unsourced} — no H1B "
        "surface supplies it, so every live start will 409 on it")

    # An employer perjury attestation must never be a required-and-defaulted
    # fact here (those stay ask-if-absent on the filing itself, per doctrine).
    for required in personal_gate.H1B_REQUIRED_APPLICANT_INFO.values():
        assert "h1b_dependent_employer" not in required
        assert "willful_violator" not in required
