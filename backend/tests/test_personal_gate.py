"""Personal-test safety gate (brief item #6): the 15 gates, admin-only marking,
required applicant info, live preflight, and the hard block on live routes."""
import pytest

from tests.conftest import AUTH
from app import personal_gate, models
from app.execution import ExecutionClass as EC

ADMIN = {"Authorization": "Bearer admin-token", "X-Org-Id": "org1", "X-User-Id": "gate-admin"}

ROUTE = dict(destination="Japan", visa_type="tourist", nationality="Chinese", residence="Singapore")


def test_exactly_the_fifteen_brief_gates():
    assert len(personal_gate.GATES) == 15
    for k in ("official_portal_verified", "official_evidence_stored", "requirements_verified",
              "fees_verified", "auth_implemented", "fields_documents_mapped",
              "checkpoints_mapped", "representative_policy_reviewed", "contract_tests_passing",
              "staging_tested", "admin_approval_recorded", "kill_switch_rollback_configured",
              "monitoring_enabled", "final_review_enabled", "explicit_confirmation_configured"):
        assert k in personal_gate.GATES, k


def test_unknown_route_has_every_gate_incomplete(db):
    r = personal_gate.readiness(db, destination="Nowhere")
    assert len(r["missing_gates"]) == 15
    assert r["route_approved_for_live"] is False
    assert r["mode"] == "preparation_and_handoff"


def test_non_admin_cannot_set_gate(client):
    r = client.post("/admin/routes/readiness", headers=AUTH, json={
        **ROUTE, "gate": "official_portal_verified", "complete": True, "evidence": "x"})
    assert r.status_code == 403


def test_gate_requires_evidence_and_unknown_gate_rejected(client):
    r = client.post("/admin/routes/readiness", headers=ADMIN, json={
        **ROUTE, "gate": "official_portal_verified", "complete": True, "evidence": "  "})
    assert r.status_code == 400 and "evidence" in r.json()["detail"]
    r = client.post("/admin/routes/readiness", headers=ADMIN, json={
        **ROUTE, "gate": "not_a_gate", "complete": True, "evidence": "x"})
    assert r.status_code == 400


def test_all_fifteen_gates_must_pass(client, db):
    keys = list(personal_gate.GATES)
    # Complete 14 of 15 — still preparation mode.
    for k in keys[:-1]:
        r = client.post("/admin/routes/readiness", headers=ADMIN, json={
            **ROUTE, "gate": k, "complete": True,
            "evidence": f"reviewed: {k} (test evidence)"})
        assert r.status_code == 200, r.text
    status = client.get("/routes/readiness", headers=AUTH, params=ROUTE).json()
    assert status["missing_gates"] == [keys[-1]]
    assert status["route_approved_for_live"] is False
    # The 15th completes the route.
    client.post("/admin/routes/readiness", headers=ADMIN, json={
        **ROUTE, "gate": keys[-1], "complete": True, "evidence": "final confirmation configured"})
    status = client.get("/routes/readiness", headers=AUTH, params=ROUTE).json()
    assert status["route_approved_for_live"] is True and status["mode"] == "live_ready"
    # Non-admin readiness is redacted: no evidence / admin identity leaked.
    assert "evidence" not in status["gates"]["official_portal_verified"]
    assert "by" not in status["gates"]["official_portal_verified"]
    # Admin readiness records who/when/evidence.
    admin_status = client.get("/routes/readiness", headers=ADMIN, params=ROUTE).json()
    g = admin_status["gates"]["official_portal_verified"]
    assert g["by"] == "gate-admin" and g["evidence"] and g["at"]


def test_missing_applicant_info_detection(db):
    a = models.Applicant(org_id="o", user_id="u", full_name="A", email="a@e.com")
    db.add(a); db.flush()
    app_row = models.VisaApplication(org_id="o", user_id="u", applicant_id=a.id,
                                     destination_country="Japan",
                                     answers={"passport_nationality": "Chinese",
                                              "prior_visa_refusals": "none"})
    db.add(app_row); db.commit()
    missing = {m["key"] for m in personal_gate.missing_applicant_info(app_row)}
    assert "passport_nationality" not in missing        # provided
    assert "prior_visa_refusals" not in missing         # "none" is a valid answer
    # RETIRED question (2026-07-28): the wizard no longer asks about an
    # existing portal account — its absence means "no" (a fresh applicant has
    # none; account creation is covered by the signed authorization) and must
    # never block a live start. An explicit answer still satisfies it too.
    assert "has_portal_account" not in missing
    assert {"current_residence", "visa_subtype", "travel_purpose", "intended_arrival",
            "intended_departure", "birth_date",
            "representative_submission_permitted"} <= missing


def test_live_route_blocked_without_gates(db):
    a = models.Applicant(org_id="o", user_id="u", full_name="A", email="a@e.com")
    db.add(a); db.flush()
    app_row = models.VisaApplication(org_id="o", user_id="u", applicant_id=a.id,
                                     destination_country="Elbonia", answers={})
    db.add(app_row); db.commit()
    # A live class without gates raises PreparationOnlyMode…
    with pytest.raises(personal_gate.PreparationOnlyMode) as ei:
        personal_gate.assert_ready_for_live_action(db, app_row, EC.LIVE_PRODUCTION)
    assert len(ei.value.missing_gates) == 15
    # …while MOCK passes through (cannot touch a real portal, labeled MOCK).
    personal_gate.assert_ready_for_live_action(db, app_row, EC.MOCK)


def test_case_preflight_endpoint_mock_route(client):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    pre = client.get(f"/cases/{cid}/live-preflight", headers=AUTH).json()
    assert pre["execution_class"] == "MOCK"
    assert pre["is_live_route"] is False
    assert pre["mode"] == "mock_preparation"
    assert pre["live_ready"] is False
    assert pre["missing_applicant_info"]        # required info still surfaced


def test_mock_start_still_allowed(client):
    # The hard gate must not break the existing mock flow (it is not live).
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]
    r = client.post(f"/cases/{cid}/start", headers=AUTH)
    assert r.status_code == 200


def test_signals_path_also_enforces_passport_block_regression(client):
    # REGRESSION (review-confirmed high): the passport block must hold on the
    # /signals path too, not only /start — otherwise an expired-passport case can
    # be driven to completion by skipping /start.
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland",
        "answers": {"expiry_date": "120415"}}).json()["id"]
    assert client.post(f"/cases/{cid}/start", headers=AUTH).status_code == 409
    # The alternate entry point is blocked identically.
    r = client.post(f"/cases/{cid}/signals/approve_review", headers=AUTH, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "passport_validity"


def test_worker_does_not_start_a_passport_blocked_case_regression(db):
    # REGRESSION (review-confirmed high): the background worker calls
    # service.signal and must be subject to the same guard (enforcement lives at
    # the durable transition point, not in the HTTP handler).
    from app import worker, models
    a = models.Applicant(org_id="o", user_id="u", full_name="A", email="a@e.com")
    db.add(a); db.flush()
    app_row = models.VisaApplication(org_id="o", user_id="u", applicant_id=a.id,
                                     destination_country="Mockland",
                                     answers={"expiry_date": "120415"})
    db.add(app_row); db.commit()
    worker.tick_once(db)                       # must NOT advance the blocked case
    db.refresh(app_row)
    assert app_row.state == "DRAFT"


def test_every_required_applicant_fact_has_a_source():
    """CONTRACT (2026-07-28 regression): the live preflight blocks a start on
    any REQUIRED_APPLICANT_INFO key with no answer. Every key must therefore
    have a REAL source — the intake wizard, a documented synonym, a runtime
    derivation, or an explicit retirement default. Removing a wizard question
    without doing one of those broke "Start application" for every case while
    the whole suite stayed green (the UI and the gate had no test that knew
    about each other).

    If this fails: either restore the question in INTAKE_FIELDS, add a synonym,
    or retire the key in _RETIRED_INFO_DEFAULTS — never delete the check.
    Retiring is only for operational facts; an answer that becomes a statement
    on a government form (prior refusals) must stay a real question."""
    from app import personal_gate
    from app.visa_snapshot.api import CASE_ANSWER_ALIASES, INTAKE_FIELDS
    from app.visa_snapshot.intake_flow import PROFILE_FIELDS

    # A source only counts when it GUARANTEES a value: an intake field that is
    # required, or that carries a product default. A `required: False` field
    # with no default is exactly the hole that broke every live start on
    # 2026-07-28 — the wizard happened to ask it, so the gate passed, until a
    # UI change stopped asking and nothing failed until runtime.
    collected = {f["key"] for f in INTAKE_FIELDS
                 if f.get("required") or "default" in f}
    # A required fact may reach the case answers by any of four routes:
    #   1. the wizard asks for it by name            (INTAKE_FIELDS)
    #   2. the wizard asks under another name        (CASE_ANSWER_ALIASES)
    #   3. a documented gate-side synonym            (_INFO_SYNONYMS)
    #   4. the passport itself supplies it           (PROFILE_FIELDS — the
    #      passport upload is a mandatory checklist item on every route)
    # ...or the gate derives it at runtime from a verified source.
    for case_key, intake_key in CASE_ANSWER_ALIASES.items():
        if intake_key in collected:
            collected.add(case_key)
    collected |= set(PROFILE_FIELDS)
    derived = {
        "visa_subtype",                       # from the verified route guidance
        "representative_submission_permitted",  # from the signed authorization
    }
    unsourced = []
    for key in personal_gate.REQUIRED_APPLICANT_INFO:
        names = {key, *(personal_gate._INFO_SYNONYMS.get(key) or ())}
        if names & collected or key in derived:
            continue
        if key in personal_gate._RETIRED_INFO_DEFAULTS:
            continue
        unsourced.append(key)
    assert not unsourced, (
        f"required applicant info with no source: {unsourced} — the wizard no "
        "longer collects it and it is neither derived nor retired, so every "
        "live start will 409 on it")

    # A retired key must genuinely be gone from the wizard's REQUIRED set:
    # retiring one the wizard still asks would hide a real unanswered question.
    required_keys = {f["key"] for f in INTAKE_FIELDS if f.get("required")}
    for key in personal_gate._RETIRED_INFO_DEFAULTS:
        names = {key, *(personal_gate._INFO_SYNONYMS.get(key) or ())}
        assert not (names & required_keys), (
            f"{key} is marked retired but the wizard still requires it")

    # Government-form statements are never defaulted away.
    assert "prior_visa_refusals" not in personal_gate._RETIRED_INFO_DEFAULTS
