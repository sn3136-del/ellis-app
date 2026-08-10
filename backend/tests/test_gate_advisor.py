"""A parked build must explain itself, and the explanation must never act.

The advisor translates the sixteen-gate report and the grounding chokepoint's
own refusals into next steps a person performs by hand. These tests hold the
two halves: the translation is right (login-walled forms ask for a consented
attended observation, vocabulary gaps ask a human to grow ELLIS_FIELDS, refused
mappings name the artifact and the field, missing handoffs name their kind),
and the advisor is inert (no release action exists, no row moves, no gate
result changes, no vocabulary is added).

It is family-agnostic on purpose: the same code decodes a tourist e-visa
portal and a work-visa petition portal, so both are exercised here. Family ids
carry a -gadv suffix only because the test database is session-shared with the
seeded registry rows; every other field is the real family's shape.
"""
import copy

from sqlalchemy import select, func

from app.adapter_factory import gate_advisor
from app.adapter_factory import models as fm
from app.global_routes.models import FamilyAdapterLink, PortalFamily
from app.global_routes.release_gates import GATE_NAMES
from tests.conftest import AUTH

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "org1",
         "X-User-Id": "admin1"}

NO_FORM = ("missing: grounded applicant field mappings wired into the flow "
           "(no form page was mappable from public observation)")


def _report(**failures) -> dict:
    """A full sixteen-gate report with the named gates failing."""
    gates = {n: {"passed": True, "reason": "satisfied"} for n in GATE_NAMES}
    for name, reason in failures.items():
        gates[name] = {"passed": False, "reason": reason}
    missing = [f"{k}: {v['reason']}" for k, v in gates.items() if not v["passed"]]
    return {"passed": not missing, "missing": missing, "gates": gates}


def _parked_build(db, *, suffix, family_id, name, kind, host, destination,
                  visa_type, account_required, gate_report=None,
                  generation_basis=None, flow=None, link=True, family=True,
                  state="MANUAL_REVIEW_REQUIRED"):
    rk = f"rk|gadv|{suffix}"
    if family:
        db.add(PortalFamily(family_id=family_id, name=name, kind=kind,
                            operator="Immigration Authority",
                            base_url=f"https://{host}/", hostnames=[host],
                            destinations=[destination],
                            account_required=account_required,
                            verification_status="verified_official_domain"))
    req = fm.AdapterBuildRequest(
        org_id="global", user_id="orchestrator", route_key=rk,
        destination=destination, visa_type=visa_type, state=state,
        consent_given=True,
        portal_evidence={"hostnames": [host], "family_id": family_id,
                         "account_required": account_required})
    db.add(req)
    db.flush()
    cand = fm.AdapterCandidate(build_request_id=req.id, route_key=rk,
                               adapter_id=f"gadv-{suffix}", current_version=1,
                               status="tests_failed")
    db.add(cand)
    db.flush()
    req.current_candidate_id = cand.id
    spec = fm.AdapterSpecification(build_request_id=req.id, route_key=rk,
                                   version=1, allowed_hostnames=[host],
                                   flow=[], field_mappings=[],
                                   generation_basis=generation_basis or {})
    db.add(spec)
    db.flush()
    ver = fm.AdapterCandidateVersion(
        candidate_id=cand.id, version=1, specification_id=spec.id,
        manifest={"route_key": rk, "allowed_hostnames": [host]},
        flow=flow or [], field_mappings=[], document_mappings=[])
    db.add(ver)
    if link:
        db.add(FamilyAdapterLink(family_id=family_id, build_request_id=req.id,
                                 candidate_id=cand.id,
                                 representative_route_key=rk, status=state,
                                 gate_report=gate_report or {}, released=False))
    db.commit()
    return req


def _actions(advice) -> list[str]:
    return [f["action"] for f in advice["fixes"]]


def _by_action(advice, action) -> list[dict]:
    return [f for f in advice["fixes"] if f["action"] == action]


# ---- gate 5 on a login-walled portal: the consented attended observation ----
def test_login_walled_work_visa_build_is_advised_to_observe_attended(db):
    req = _parked_build(
        db, suffix="uscis", family_id="usa-uscis-myaccount-gadv",
        name="U.S. myUSCIS online account", kind="immigration_authority",
        host="my.uscis.gov", destination="USA", visa_type="h1b_petition",
        account_required=True, gate_report=_report(required_fields_mapped=NO_FORM))
    advice = gate_advisor.advise(db, req.id)
    assert advice["blocking_gate"] == "required_fields_mapped"
    fixes = _by_action(advice, "start_attended_observation")
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix["kind"] == "login_walled_form"
    assert fix["family_id"] == "usa-uscis-myaccount-gadv"
    # It says WHY the portal can never be mapped credential-free, and that the
    # applicant's consent is the thing that makes the observation legitimate.
    assert "account" in fix["detail"] and "consent" in fix["detail"].lower()
    assert NO_FORM in fix["detail"]
    # Every fix carries the whole contract.
    for f in advice["fixes"]:
        assert {"kind", "title", "detail", "action"} <= set(f)


# ---- CROSS-EDITION: the same blocker on a TOURIST family, same advice ----
def test_login_walled_tourist_family_gets_the_same_advice(db):
    """kenya-eta is a tourist ETA portal that shows no form until an account
    signs in — exactly the H1B petition portals' problem. The advisor is keyed
    by family_id and reads account_required off the family record, so it must
    reach the identical conclusion with no edition in sight."""
    req = _parked_build(
        db, suffix="kenya", family_id="kenya-eta-gadv",
        name="Kenya Electronic Travel Authorisation", kind="eta_portal",
        host="etakenya.go.ke", destination="KEN", visa_type="tourist",
        account_required=True, gate_report=_report(required_fields_mapped=NO_FORM))
    advice = gate_advisor.advise(db, req.id)
    assert advice["blocking_gate"] == "required_fields_mapped"
    assert advice["family_id"] == "kenya-eta-gadv"
    fixes = _by_action(advice, "start_attended_observation")
    assert len(fixes) == 1 and fixes[0]["kind"] == "login_walled_form"
    assert "tourist" not in fixes[0]["detail"].lower()   # nothing edition-shaped


def test_consented_observation_not_recorded_is_named_as_the_gap(db):
    """The signed-in structure exists but the consent for it does not: the
    advice must be the consent, not another crawl. The family record here still
    says account_required=False (a stale seed row); the gate's own words are
    what the advisor believes, so the advice is right anyway."""
    req = _parked_build(
        db, suffix="uae", family_id="uae-icp-gadv", name="UAE ICP",
        kind="immigration_authority", host="icp.gov.ae", destination="ARE",
        visa_type="tourist", account_required=False,
        gate_report=_report(required_fields_mapped=(
            "missing: the applicant's consent to learn this portal from their "
            "signed-in session was not recorded")))
    fix = _by_action(gate_advisor.advise(db, req.id),
                     "start_attended_observation")[0]
    assert "consent" in fix["detail"].lower()
    assert "authorized_observation" in fix["detail"]


# ---- CROSS-EDITION: a public tourist portal's gate 5 is a curation gap ----
def test_public_tourist_portal_form_gap_is_advised_as_curation(db):
    req = _parked_build(
        db, suffix="vnm", family_id="vietnam-evisa-gadv", name="Vietnam e-Visa",
        kind="evisa_portal", host="evisa.gov.vn", destination="VNM",
        visa_type="tourist", account_required=False,
        gate_report=_report(required_fields_mapped=NO_FORM))
    advice = gate_advisor.advise(db, req.id)
    assert advice["blocking_gate"] == "required_fields_mapped"
    assert _by_action(advice, "start_attended_observation") == []
    fix = _by_action(advice, "curate_form_path")[0]
    assert "form_paths" in fix["detail"] and "entry_gate" in fix["detail"]


# ---- proposed vocabulary is a candidate list, never an extension ----
def test_proposed_vocabulary_is_advised_as_candidates_only(db):
    """CROSS-EDITION: a tourist arrival-card family proposes vocabulary and is
    told a human must accept it. Nothing is added by asking."""
    from app.adapter_factory.specgen import ELLIS_FIELDS
    before = list(ELLIS_FIELDS)
    req = _parked_build(
        db, suffix="tdac", family_id="thailand-tdac-gadv",
        name="Thailand Digital Arrival Card", kind="arrival_card",
        host="tdac.immigration.go.th", destination="THA", visa_type="tourist",
        account_required=False,
        gate_report=_report(required_fields_mapped=NO_FORM),
        generation_basis={"proposed_vocabulary": [
            {"ellis_field": "boarding_country", "portal_field": "ddlBoardCountry",
             "label": "Country where you boarded", "artifact_id": "art-77"},
            "traveller_type",
        ]})
    advice = gate_advisor.advise(db, req.id)
    fix = _by_action(advice, "add_vocabulary")[0]
    assert fix["kind"] == "vocabulary_gap"
    assert fix["fields"] == ["boarding_country", "traveller_type"]
    assert "boarding_country" in fix["detail"] and "traveller_type" in fix["detail"]
    # Advisory in the wording, and advisory in fact.
    assert "human accepts" in fix["detail"] or "a human" in fix["detail"]
    assert list(ELLIS_FIELDS) == before


def test_the_advisor_reads_specgens_own_vocabulary_shape(db):
    """specgen records candidates as suggested_ellis_field + the portal element
    it came from, and the suggested name is derived from the portal's LABEL, so
    it need not equal the name the mapping proposed. The residual refusal list
    must still not repeat the same element."""
    req = _parked_build(
        db, suffix="vshape", family_id="laos-evisa-gadv", name="Laos e-Visa",
        kind="evisa_portal", host="laoevisa.gov.la", destination="LAO",
        visa_type="tourist", account_required=False,
        gate_report=_report(required_fields_mapped=NO_FORM),
        generation_basis={
            "proposed_vocabulary": [
                {"portal_field": "txtVillage", "page_key": "application",
                 "suggested_ellis_field": "village_of_stay",
                 "label": "Village where you will stay", "input_type": "text",
                 "artifact_id": "art-91"}],
            "rejected_mappings": [
                {"proposal": {"portal_field": "txtVillage", "ellis_field": "village",
                              "artifact_id": "art-91", "page_key": "application",
                              "selector": "#txtVillage"},
                 "reasons": ["unknown_ellis_field"]}]})
    advice = gate_advisor.advise(db, req.id)
    fix = _by_action(advice, "add_vocabulary")[0]
    assert fix["fields"] == ["village_of_stay"]
    assert fix["candidates"][0]["portal_field"] == "txtVillage"
    assert _by_action(advice, "review_rejected_mapping") == []


# ---- refused mappings name the artifact and the field ----
def test_refused_mappings_name_the_artifact_and_the_field(db):
    req = _parked_build(
        db, suffix="reject", family_id="india-evisa-gadv", name="India e-Visa",
        kind="evisa_portal", host="indianvisaonline.gov.in", destination="IND",
        visa_type="tourist", account_required=False,
        gate_report=_report(required_fields_mapped=NO_FORM),
        generation_basis={
            "proposed_vocabulary": [{"ellis_field": "host_relationship"}],
            "rejected_mappings": [
                {"proposal": {"portal_field": "txtSurname", "ellis_field": "surname",
                              "artifact_id": "art-11", "page_key": "application",
                              "selector": "div > div:nth-child(4) input"},
                 "reasons": ["non_deterministic_selector"]},
                {"proposal": {"portal_field": "ddlNationality",
                              "ellis_field": "nationality", "artifact_id": "art-12",
                              "page_key": "application", "selector": "#wrong"},
                 "reasons": ["selector_mismatch_with_observation"]},
                {"proposal": {"portal_field": "txtHostRel",
                              "ellis_field": "host_relationship",
                              "artifact_id": "art-13", "page_key": "application",
                              "selector": "#txtHostRel"},
                 "reasons": ["unknown_ellis_field"]},
                {"proposal": {"portal_field": "txtCaste", "ellis_field": "caste",
                              "artifact_id": "art-14", "page_key": "application",
                              "selector": "#txtCaste"},
                 "reasons": ["unknown_ellis_field"]},
            ]})
    advice = gate_advisor.advise(db, req.id)
    fixes = {f["reason"]: f for f in _by_action(advice, "review_rejected_mapping")}
    assert set(fixes) == {"non_deterministic_selector",
                          "selector_mismatch_with_observation",
                          "unknown_ellis_field"}
    assert "art-11" in fixes["non_deterministic_selector"]["detail"]
    assert "txtSurname" in fixes["non_deterministic_selector"]["detail"]
    assert "art-12" in fixes["selector_mismatch_with_observation"]["detail"]
    assert "ddlNationality" in fixes["selector_mismatch_with_observation"]["detail"]
    # RESIDUAL only: host_relationship was already proposed as vocabulary, so
    # it belongs to that fix and is not re-reported as an unexplained refusal.
    unknown = fixes["unknown_ellis_field"]
    assert unknown["count"] == 1
    assert "txtCaste" in unknown["detail"] and "art-14" in unknown["detail"]
    assert "txtHostRel" not in unknown["detail"]


# ---- missing handoffs are named by kind ----
def test_missing_handoffs_are_named_by_kind(db):
    req = _parked_build(
        db, suffix="handoff", family_id="japan-evisa-gadv", name="Japan e-Visa",
        kind="evisa_portal", host="www.evisa.mofa.go.jp", destination="JPN",
        visa_type="tourist", account_required=True,
        gate_report=_report(
            account_flow_mapped_where_applicable=(
                "missing: account/login flow mapping (credentials applicant handoff)"),
            captcha_otp_handoffs_preserved=(
                "missing: applicant handoff node(s) for ['otp'] "
                "(observed: ['captcha'], declared: ['otp'])"),
            payment_confirmation_preserved=(
                "missing: exact-amount applicant payment handoff before any "
                "payment step"),
            submission_confirmation_preserved=(
                "missing: applicant declaration/final-confirmation handoff "
                "before submission")))
    advice = gate_advisor.advise(db, req.id)
    kinds = [f["handoff_kind"] for f in _by_action(advice, "declare_handoff")]
    assert kinds == ["credentials", "otp", "payment_credentials",
                     "legally_personal_declaration"]
    # The kind the gate OBSERVED is not the kind that is missing.
    assert "captcha" not in kinds
    for f in _by_action(advice, "declare_handoff"):
        assert f["gate"] in GATE_NAMES
        assert f["handoff_kind"] in f["title"]


def test_every_failing_gate_reaches_the_operator_verbatim(db):
    req = _parked_build(
        db, suffix="verbatim", family_id="egypt-evisa-gadv", name="Egypt e-Visa",
        kind="evisa_portal", host="visa2egypt.gov.eg", destination="EGY",
        visa_type="tourist", account_required=False,
        gate_report=_report(security_scan_passed=(
            "missing: static security scan (STATIC_VALIDATED)")))
    advice = gate_advisor.advise(db, req.id)
    assert advice["blocking_gate"] == "security_scan_passed"
    fix = _by_action(advice, "review_blocker")[0]
    assert fix["gate"] == "security_scan_passed"
    assert "STATIC_VALIDATED" in fix["detail"]


def test_a_build_with_no_gate_report_is_honest_about_it(db):
    """Parked before the gates ran: the open review tasks are the next steps,
    and no gate is claimed to have failed."""
    req = _parked_build(
        db, suffix="nogates", family_id="oman-evisa-gadv", name="Oman e-Visa",
        kind="evisa_portal", host="evisa.rop.gov.om", destination="OMN",
        visa_type="tourist", account_required=False, link=False, family=False)
    db.add(fm.AdapterReviewTask(candidate_id=req.current_candidate_id,
                                kind="recon_unavailable",
                                reason="live structural reconnaissance is not "
                                       "yet wired for this portal"))
    db.commit()
    advice = gate_advisor.advise(db, req.id)
    assert advice["gate_report_source"] == "none"
    assert advice["blocking_gate"] == "" and advice["gates_failing"] == []
    assert "parked before the release gates ran" in advice["human_summary"]
    fix = _by_action(advice, "review_blocker")[0]
    assert fix["kind"] == "build_parked" and "recon" in fix["detail"]


def test_gates_are_recomputed_read_only_when_none_were_recorded(db):
    """An applicant-initiated build has no family link and so no stored report.
    Reading the gates to advise on them never records them."""
    req = _parked_build(
        db, suffix="recompute", family_id="morocco-evisa-gadv",
        name="Morocco e-Visa", kind="evisa_portal", host="acces-maroc.ma",
        destination="MAR", visa_type="tourist", account_required=False,
        link=False)
    links_before = db.execute(select(func.count()).select_from(
        FamilyAdapterLink)).scalar_one()
    advice = gate_advisor.advise(db, req.id)
    assert advice["gate_report_source"] == "recomputed_read_only"
    assert advice["blocking_gate"] in GATE_NAMES
    assert db.execute(select(func.count()).select_from(
        FamilyAdapterLink)).scalar_one() == links_before


def test_a_real_green_build_is_never_told_it_is_blocked(db):
    """End to end against the factory's own output, not a fixture: a synthetic
    build that went all the way to a sandbox release must produce no fixes and
    no claim of a park."""
    from app.adapter_factory.build_workflow import (create_request,
                                                    record_consent, run_build)
    from app.adapter_factory import auto_release
    from app.portal.synthetic import SyntheticPortal
    host = "portal.gov.example"
    req = create_request(
        db, org_id="orgGADV", user_id="applicant-1", application_id="",
        route_key="rk|gadv|green", destination="Testland", visa_type="tourist",
        portal_evidence={"hostnames": [host], "operator": "Test Authority",
                         "verification": "synthetic_test_portal"},
        runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")
    run_build(db, req.id, observer=SyntheticPortal(
        scenario="single_step_login", hostname=host).observe)
    auto_release.evaluate_build(db, req.id)
    db.refresh(req)
    advice = gate_advisor.advise(db, req.id)
    assert advice["released"] is True
    assert advice["blocking_gate"] == "" and advice["fixes"] == []
    assert "parked" not in advice["human_summary"]
    assert "nothing is blocking it" in advice["human_summary"]


# ---- the advisor is inert ----
def test_advisor_never_advises_release_and_never_mutates(db):
    assert "release" not in gate_advisor.ADVISORY_ACTIONS
    req = _parked_build(
        db, suffix="inert", family_id="nigeria-immigration-gadv",
        name="Nigeria Immigration Service", kind="evisa_portal",
        host="portal.immigration.gov.ng", destination="NGA",
        visa_type="tourist", account_required=True,
        gate_report=_report(required_fields_mapped=NO_FORM,
                            submission_confirmation_preserved=(
                                "missing: applicant declaration/final-"
                                "confirmation handoff before submission")),
        generation_basis={"proposed_vocabulary": [{"ellis_field": "state_of_origin"}]})
    link = db.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "nigeria-immigration-gadv")).scalars().one()
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    before = {
        "report": copy.deepcopy(link.gate_report),
        "released": link.released, "tier": link.release_tier,
        "status": link.status, "cand_status": cand.status, "state": req.state,
        "releases": db.execute(select(func.count()).select_from(
            fm.AdapterRelease)).scalar_one(),
        "bindings": db.execute(select(func.count()).select_from(
            fm.AdapterRuntimeBinding)).scalar_one(),
        "cap_releases": db.execute(select(func.count()).select_from(
            fm.AdapterCapabilityRelease)).scalar_one(),
        "versions": db.execute(select(func.count()).select_from(
            fm.AdapterCandidateVersion)).scalar_one(),
    }
    advice = gate_advisor.advise(db, req.id)
    assert advice["advisory"] is True and advice["released"] is False
    assert advice["fixes"], "a blocked build must get at least one next step"
    for f in advice["fixes"]:
        assert f["action"] in gate_advisor.ADVISORY_ACTIONS
        assert "release" not in f["action"]
    # Nothing was written, and nothing is even pending on the session.
    assert not db.new and not db.dirty and not db.deleted
    db.expire_all()
    link = db.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == "nigeria-immigration-gadv")).scalars().one()
    cand = db.get(fm.AdapterCandidate, req.current_candidate_id)
    req = db.get(fm.AdapterBuildRequest, req.id)
    assert link.gate_report == before["report"]
    assert (link.released, link.release_tier, link.status) == (
        before["released"], before["tier"], before["status"])
    assert cand.status == before["cand_status"] and req.state == before["state"]
    for table, key in ((fm.AdapterRelease, "releases"),
                       (fm.AdapterRuntimeBinding, "bindings"),
                       (fm.AdapterCapabilityRelease, "cap_releases"),
                       (fm.AdapterCandidateVersion, "versions")):
        assert db.execute(select(func.count()).select_from(
            table)).scalar_one() == before[key]


def test_advice_is_deterministic_from_a_build_or_a_version(db):
    req = _parked_build(
        db, suffix="determ", family_id="zambia-evisa-gadv", name="Zambia e-Visa",
        kind="evisa_portal", host="evisa.zambiaimmigration.gov.zm",
        destination="ZMB", visa_type="tourist", account_required=True,
        gate_report=_report(required_fields_mapped=NO_FORM))
    first = gate_advisor.advise(db, req.id)
    assert first == gate_advisor.advise(db, req.id)
    # The build row, and the candidate version, resolve to the same advice.
    assert gate_advisor.advise(db, req) == first
    ver = db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == req.current_candidate_id)
        ).scalars().one()
    assert gate_advisor.advise(db, ver) == first


# ---- the endpoint ----
def test_advice_endpoint_is_admin_only(client, db):
    req = _parked_build(
        db, suffix="api", family_id="rwanda-irembo-gadv", name="Rwanda Irembo",
        kind="evisa_portal", host="irembo.gov.rw", destination="RWA",
        visa_type="tourist", account_required=True,
        gate_report=_report(required_fields_mapped=NO_FORM))
    assert client.get(f"/factory/builds/{req.id}/advice",
                      headers=AUTH).status_code == 403
    r = client.get(f"/factory/builds/{req.id}/advice", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blocking_gate"] == "required_fields_mapped"
    assert body["human_summary"]
    assert "start_attended_observation" in [f["action"] for f in body["fixes"]]
    assert "release" not in [f["action"] for f in body["fixes"]]


def test_advice_endpoint_404s_on_an_unknown_build(client):
    r = client.get("/factory/builds/does-not-exist/advice", headers=ADMIN)
    assert r.status_code == 404
