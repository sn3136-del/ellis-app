"""H1B filing coordinator: a ready step becomes a real linked child filing
case with explicitly assembled, party-partitioned answers — never a wholesale
merge of both parties, and never a release without verified predecessors."""
import pytest
from sqlalchemy import select

from app import models
from app.h1b import filing as h1b_filing
from app.h1b import models as h1b_models
from app.h1b import steps as h1b_steps
from app.visa_snapshot.models import CaseRouteGuidance

from .conftest import AUTH, PASSPORT_MRZ

# The release/verify endpoints now enforce per-party authorization: only the
# step's acting party (or an admin) may act. AUTH is the beneficiary (user1);
# the petitioner party is bound to a distinct HR operator so petitioner-acting
# filings are released by the petitioner, exactly as production requires.
AUTH_HR = {"Authorization": "Bearer dev-token", "X-Org-Id": "org1",
           "X-User-Id": "hr1"}

PETITIONER_JOB_ANSWERS = {
    "job_title": "Software Engineer",
    "soc_code": "15-1252",
    "soc_title": "Software Developers",
    "wage_offer": 132000,
    "wage_offer_unit": "Year",
    "prevailing_wage": 121000,
    "worksite_address_line1": "285 Fulton St",
    "worksite_city": "New York",
    "worksite_state": "NY",
    "worksite_postal_code": "10007",
    "employment_start_date": "2026-10-01",
    "employment_end_date": "2029-09-30",
    "full_time_position": True,
    # Deliberately NOT in the shared vocabulary — must never reach a filing.
    "internal_hr_note": "flagged for relocation budget review",
}


def _create_case(client, db, **overrides):
    """An extension case with a real employer profile bound to the petitioner
    party, petitioner job answers seeded on the party row."""
    r = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-3456789",
        "naics_code": "541511", "address_line1": "285 Fulton St",
        "city": "New York", "state": "NY", "postal_code": "10007",
        "signatory_name": "PAT DOE", "signatory_title": "HR Director",
        "signatory_email": "hr@tripus.example.com",
        "parent_company_name": "Trip.com Group Ltd",
        "parent_company_country": "China"}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = {"case_kind": "extension",
            "beneficiary_full_name": "WEI ZHANG",
            "beneficiary_email": "wei.zhang@example.com",
            "beneficiary_abroad": False, "beneficiary_in_us": True,
            "first_h1b": False,
            "employer_profile_id": r.json()["employer_profile_id"]}
    body.update(overrides)
    r = client.post("/h1b/cases", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    petitioner = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == out["case_id"],
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    petitioner.answers = dict(PETITIONER_JOB_ANSWERS)
    # Bind the petitioner party to a distinct operator so petitioner-acting
    # filings are released under the petitioner's own account (the endpoint now
    # enforces per-party authorization).
    petitioner.user_id = "hr1"
    db.commit()
    return out


def _step(db, case_id, step_key):
    return db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == step_key)).scalars().first()


def _release(client, case_id, step_key, headers=None):
    # The acting party releases its own step: the beneficiary for the consular
    # leg, the petitioner (hr1) for the DOL/USCIS filings.
    if headers is None:
        headers = AUTH if step_key == "ds160_consular" else AUTH_HR
    return client.post(f"/h1b/cases/{case_id}/steps/{step_key}/release",
                       headers=headers)


# The offline government artifact an admin accepts as verification evidence for
# a step whose outcome arrives outside a portal run (certified LCA / I-797).
_OFFLINE_TYPE_FOR = {"lca": "certified_lca", "registration": "prior_i797",
                     "i129": "prior_i797"}


def _accepted_offline_doc(db, case_id, step_key, org_id="org1"):
    doc = models.StoredDocument(
        org_id=org_id, application_id=case_id,
        name=f"{step_key}-evidence.pdf", mime="application/pdf", size_bytes=2048,
        doc_type=_OFFLINE_TYPE_FOR[step_key], ocr_status="done", approved=True)
    db.add(doc)
    db.commit()
    return doc


def _verify_step_offline(db, case_id, step_key, receipts=None):
    """Verify a step the way an admin would with an accepted offline artifact —
    the guarded path mark_step_verified now requires."""
    step = _step(db, case_id, step_key)
    doc = _accepted_offline_doc(db, case_id, step_key)
    h1b_steps.mark_step_verified(db, step, receipts=receipts or {}, actor="admin",
                                 offline_evidence_document_id=doc.id)
    return step


# ---------- answer partitioning ----------

def test_release_ready_lca_creates_partitioned_child(client, db):
    out = _create_case(client, db)
    case_id = out["case_id"]
    r = _release(client, case_id, "lca")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["already_exists"] is False
    assert payload["visa_type"] == "h1b_lca"
    assert payload["attorney_disclaimer"]

    db.expire_all()
    child = db.get(models.VisaApplication, payload["child_case_id"])
    assert child is not None
    assert child.visa_type == "h1b_lca"
    assert child.destination_country == "United States"
    a = child.answers
    # Whitelisted petitioner job/wage/worksite facts + employer profile.
    assert a["job_title"] == "Software Engineer"
    assert a["wage_offer"] == 132000
    assert a["worksite_city"] == "New York"
    assert a["employer_legal_name"] == "Trip.com US Inc"
    assert a["employer_fein"] == "123456789"
    # Beneficiary-private facts never enter the DOL filing.
    assert "email" not in a
    assert "full_name" not in a
    # Non-whitelisted petitioner answers stay on the party row.
    assert "internal_hr_note" not in a
    # Unanswered nullable attestations are asked, never defaulted.
    assert "h1b_dependent_employer" not in a
    assert "willful_violator" not in a
    # Bidirectional link + honest step status.
    assert a["h1b_parent_case_id"] == case_id
    step = _step(db, case_id, "lca")
    assert step.child_case_id == child.id
    assert step.status == "in_progress"


def test_i129_child_gets_beneficiary_identity_but_not_email(client, db):
    out = _create_case(client, db)
    case_id = out["case_id"]
    _verify_step_offline(db, case_id, "lca",
                         receipts={"lca_number": "I-200-26123-456789"})
    db.refresh(i129 := _step(db, case_id, "i129"))
    assert i129.status == "ready"

    r = _release(client, case_id, "i129")
    assert r.status_code == 200, r.text
    db.expire_all()
    child = db.get(models.VisaApplication, r.json()["child_case_id"])
    assert child.visa_type == "h1b_i129"
    a = child.answers
    assert a["full_name"] == "WEI ZHANG"          # identity, shared
    assert a["employer_legal_name"] == "Trip.com US Inc"
    assert "email" not in a                        # contact detail, private
    assert "internal_hr_note" not in a


def test_ds160_child_is_beneficiary_only_and_carries_passport(client, db):
    out = _create_case(client, db, beneficiary_abroad=True,
                       beneficiary_in_us=False)
    case_id = out["case_id"]
    # The parent's accepted passport (real upload pipeline, MRZ specimen).
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "passport.pdf", "mime": "application/pdf", "size_bytes": 1024,
        "checklist_item_id": "passport", "text": PASSPORT_MRZ}, headers=AUTH)
    assert up.status_code == 200, up.text

    for key in ("lca", "i129"):
        _verify_step_offline(db, case_id, key)
    r = _release(client, case_id, "ds160_consular")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["visa_type"] == "h1b_ds160"
    assert payload["documents_carried"] == 1

    db.expire_all()
    child = db.get(models.VisaApplication, payload["child_case_id"])
    a = child.answers
    assert a["full_name"] == "WEI ZHANG" and a["email"]
    assert a["h1b_parent_case_id"] == case_id
    # No employer/petitioner fact leaks into the beneficiary's filing.
    assert "employer_legal_name" not in a and "employer_fein" not in a
    assert "job_title" not in a and "wage_offer" not in a
    # Passport carried by sha — same bytes, same provenance, no re-upload.
    parent_doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case_id,
        models.StoredDocument.doc_type == "passport")).scalars().first()
    child_doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == child.id,
        models.StoredDocument.doc_type == "passport")).scalars().first()
    assert child_doc is not None
    assert child_doc.sha256 == parent_doc.sha256


# ---------- sequencing honesty ----------

def test_blocked_step_409s_naming_its_dependency(client, db):
    out = _create_case(client, db)
    r = _release(client, out["case_id"], "i129")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["unverified_dependencies"] == ["lca"]
    assert "lca" in detail["reason"]


def test_unknown_step_404s(client, db):
    out = _create_case(client, db)
    # extension plan has no registration step.
    r = _release(client, out["case_id"], "registration")
    assert r.status_code == 404


def test_release_is_idempotent(client, db):
    out = _create_case(client, db)
    case_id = out["case_id"]
    first = _release(client, case_id, "lca")
    assert first.status_code == 200
    second = _release(client, case_id, "lca")
    assert second.status_code == 200
    assert second.json()["child_case_id"] == first.json()["child_case_id"]
    assert second.json()["already_exists"] is True
    children = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.visa_type == "h1b_lca")).scalars().all()
    assert len([c for c in children
                if (c.answers or {}).get("h1b_parent_case_id") == case_id]) == 1


def test_child_case_has_its_own_route_guidance(client, db):
    out = _create_case(client, db)
    r = _release(client, out["case_id"], "lca")
    child_id = r.json()["child_case_id"]
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == child_id)).scalars().first()
    assert cg is not None
    assert cg.continuation_kind == "h1b_filing"
    assert cg.route_key == "h1b_step:lca"
    assert cg.guidance["status"] == "curated"
    assert cg.guidance["guidance"]["portal"] == "flag.dol.gov"
    # The registered continuation advances into the standard preparation stage.
    from app.checklist_intake import NEXT_STAGE_BY_KIND
    assert NEXT_STAGE_BY_KIND["h1b_filing"] == "application_preparation"


def test_release_step_refuses_non_ready_even_without_endpoint(client, db):
    out = _create_case(client, db)
    i129 = _step(db, out["case_id"], "i129")
    parent = db.get(models.VisaApplication, out["case_id"])

    class _P:
        org_id = parent.org_id
        user_id = "test"

    try:
        h1b_filing.release_step(db, parent=parent, step=i129, principal=_P())
        raise AssertionError("release_step accepted a blocked step")
    except ValueError as e:
        assert "blocked" in str(e)


# ---------- erasure ----------

def test_parent_deletion_erases_child_filing_cases(client, db):
    """Right-to-erasure follows H1bCaseStep.child_case_id (finding #6): the LCA
    child filing case holds employer FEIN/wage in its answers and owns a fresh
    petitioner Applicant, none of which any other erasure path reaches. Deleting
    the parent must cascade to the child, its guidance, and its petitioner
    Applicant — leaving no orphaned PII."""
    from app import privacy
    out = _create_case(client, db)
    case_id = out["case_id"]
    child_id = _release(client, case_id, "lca").json()["child_case_id"]
    db.expire_all()
    child = db.get(models.VisaApplication, child_id)
    petitioner_applicant_id = child.applicant_id
    # The child really does carry petitioner-private filing facts.
    assert str(child.answers.get("wage_offer")) == "132000"

    result = privacy.delete_case(db, case_id)
    assert db.get(models.VisaApplication, case_id) is None
    assert db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id)).scalars().all() == []
    # The child filing case, its route guidance, and its petitioner Applicant
    # are all gone — no orphaned PII survives.
    assert db.get(models.VisaApplication, child_id) is None
    assert db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == child_id)).scalars().first() is None
    assert db.get(models.Applicant, petitioner_applicant_id) is None
    assert result["counts"].get("h1b_child_cases") == 1


def test_applicant_erasure_reaches_petitioner_child_cases(client, db):
    """delete_applicant(beneficiary) erases the parent, which cascades to the
    petitioner-acting child cases even though they carry a DIFFERENT applicant_id
    that delete_applicant would never enumerate directly (finding #6)."""
    from app import privacy
    out = _create_case(client, db)
    case_id = out["case_id"]
    child_id = _release(client, case_id, "lca").json()["child_case_id"]
    db.expire_all()
    parent = db.get(models.VisaApplication, case_id)
    beneficiary_applicant_id = parent.applicant_id
    petitioner_applicant_id = db.get(models.VisaApplication, child_id).applicant_id
    assert petitioner_applicant_id != beneficiary_applicant_id

    privacy.delete_applicant(db, beneficiary_applicant_id)
    db.expire_all()
    assert db.get(models.VisaApplication, case_id) is None
    assert db.get(models.VisaApplication, child_id) is None
    assert db.get(models.Applicant, petitioner_applicant_id) is None
    assert db.get(models.Applicant, beneficiary_applicant_id) is None


def test_parent_deletion_erases_orphaned_employer_profile(client, db):
    """An EmployerProfile used only by this case is org-scoped (no
    application_id) and escapes the per-case cascade. delete_case must erase it
    once no case references it, so petitioner FEIN/signatory PII actually has an
    erasure path (finding #7)."""
    from app import privacy
    out = _create_case(client, db)
    case_id = out["case_id"]
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    profile_id = pet.employer_profile_id
    assert db.get(h1b_models.EmployerProfile, profile_id) is not None

    result = privacy.delete_case(db, case_id)
    assert db.get(h1b_models.EmployerProfile, profile_id) is None
    assert result["counts"].get("employer_profiles") == 1


def test_shared_employer_profile_survives_while_another_case_uses_it(client, db):
    """The org-reuse pattern (one petitioner profile, many beneficiaries): a
    profile still referenced by another case is NOT erased when one case goes."""
    from app import privacy
    out1 = _create_case(client, db)
    pet1 = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == out1["case_id"],
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    profile_id = pet1.employer_profile_id
    out2 = client.post("/h1b/cases", json={
        "case_kind": "extension", "beneficiary_full_name": "LI MING",
        "beneficiary_email": "li.ming@example.com",
        "beneficiary_abroad": False, "beneficiary_in_us": True,
        "first_h1b": False, "employer_profile_id": profile_id},
        headers=AUTH).json()

    privacy.delete_case(db, out1["case_id"])
    assert db.get(h1b_models.EmployerProfile, profile_id) is not None  # still used
    privacy.delete_case(db, out2["case_id"])
    assert db.get(h1b_models.EmployerProfile, profile_id) is None      # now orphaned


# ---------- acting-party identity (the Applicant side-channel) ----------

def test_petitioner_child_applicant_is_petitioner_not_beneficiary(client, db):
    """The child filing's Applicant row is the identity the workflow registers
    the portal account with and sends every notification to. For a petitioner-
    acting filing it MUST be the petitioner, never the beneficiary — otherwise
    the DOL account and the employer's perjury action prompts carry the worker's
    name and land in the worker's inbox."""
    out = _create_case(client, db)
    case_id = out["case_id"]
    child_id = _release(client, case_id, "lca").json()["child_case_id"]

    db.expire_all()
    child = db.get(models.VisaApplication, child_id)
    parent = db.get(models.VisaApplication, case_id)
    applicant = db.get(models.Applicant, child.applicant_id)
    # The petitioner drives the DOL filing — its own signatory email, not the
    # beneficiary's.
    assert applicant.email == "hr@tripus.example.com"
    assert applicant.email != "wei.zhang@example.com"
    assert "wei" not in (applicant.full_name or "").lower()
    # The beneficiary's Applicant row is a different, untouched identity.
    assert child.applicant_id != parent.applicant_id


def test_consular_child_keeps_the_beneficiary_identity(client, db):
    out = _create_case(client, db, beneficiary_abroad=True, beneficiary_in_us=False)
    case_id = out["case_id"]
    for key in ("lca", "i129"):
        _verify_step_offline(db, case_id, key)
    child_id = _release(client, case_id, "ds160_consular").json()["child_case_id"]

    db.expire_all()
    child = db.get(models.VisaApplication, child_id)
    parent = db.get(models.VisaApplication, case_id)
    # The consular leg is the beneficiary's own act → the beneficiary Applicant.
    assert child.applicant_id == parent.applicant_id
    assert db.get(models.Applicant, child.applicant_id).email == "wei.zhang@example.com"


# ---------- DS-160 whitelist (petitioner facts never reach the beneficiary) ----

def test_ds160_never_leaks_petitioner_facts_from_parent_answers(client, db):
    """parent.answers is beneficiary answers only by convention: the generic
    /cases/{id}/answers endpoint merges arbitrary keys into it. The consular
    child must take a beneficiary whitelist, never a wholesale copy, so employer
    job/wage/worksite facts and internal notes cannot ride into the DS-160."""
    out = _create_case(client, db, beneficiary_abroad=True, beneficiary_in_us=False)
    case_id = out["case_id"]

    parent = db.get(models.VisaApplication, case_id)
    merged = dict(parent.answers or {})
    merged.update({"job_title": "Software Engineer", "wage_offer": 132000,
                   "prevailing_wage": 121000, "worksite_city": "New York",
                   "internal_hr_note": "relocation budget flagged"})
    parent.answers = merged
    db.commit()

    for key in ("lca", "i129"):
        _verify_step_offline(db, case_id, key)
    child_id = _release(client, case_id, "ds160_consular").json()["child_case_id"]

    a = db.get(models.VisaApplication, child_id).answers
    assert a["full_name"] == "WEI ZHANG" and a["email"] == "wei.zhang@example.com"
    for leaked in ("job_title", "wage_offer", "prevailing_wage",
                   "worksite_city", "internal_hr_note"):
        assert leaked not in a


# ---------- verification requires real government evidence ----------

def test_mark_step_verified_refuses_without_evidence(client, db):
    out = _create_case(client, db)
    case_id = out["case_id"]
    lca = _step(db, case_id, "lca")
    with pytest.raises(h1b_steps.EvidenceRequired):
        h1b_steps.mark_step_verified(db, lca, receipts={"lca_number": "I-200-x"},
                                     actor="test")
    db.refresh(lca)
    assert lca.status != "verified"
    # The next real filing must not have unblocked off a bare receipt dict.
    db.refresh(i129 := _step(db, case_id, "i129"))
    assert i129.status == "blocked"


def test_mark_step_verified_rejects_unaccepted_or_wrong_offline_doc(client, db):
    out = _create_case(client, db)
    case_id = out["case_id"]

    # Present but not admin-accepted → not evidence.
    unaccepted = models.StoredDocument(
        org_id="org1", application_id=case_id, name="lca.pdf",
        mime="application/pdf", doc_type="certified_lca", approved=False)
    db.add(unaccepted)
    # Accepted but wrong type → not evidence.
    wrong = models.StoredDocument(
        org_id="org1", application_id=case_id, name="cv.pdf",
        mime="application/pdf", doc_type="resume_cv", approved=True)
    db.add(wrong)
    db.commit()

    for doc in (unaccepted, wrong):
        with pytest.raises(h1b_steps.EvidenceRequired):
            h1b_steps.mark_step_verified(db, _step(db, case_id, "lca"),
                                         receipts={}, actor="admin",
                                         offline_evidence_document_id=doc.id)

    # An accepted certified LCA on the case IS admin-reviewed evidence.
    ok = models.StoredDocument(
        org_id="org1", application_id=case_id, name="lca-cert.pdf",
        mime="application/pdf", doc_type="certified_lca", approved=True)
    db.add(ok)
    db.commit()
    h1b_steps.mark_step_verified(db, _step(db, case_id, "lca"), receipts={},
                                 actor="admin", offline_evidence_document_id=ok.id)
    db.refresh(lca := _step(db, case_id, "lca"))
    assert lca.status == "verified"


def test_child_filing_verified_rejects_mock_confirmation(client, db):
    """persist_workflow writes a SubmissionConfirmation for ANY execution class,
    so the confirmation alone must not verify a step. Only a completed adapter
    execution with government-host outcome evidence does."""
    from app.adapter_factory import models as fm
    out = _create_case(client, db)
    case_id = out["case_id"]
    child_id = _release(client, case_id, "lca").json()["child_case_id"]

    db.add(models.SubmissionConfirmation(application_id=child_id,
                                         reference_no="REF-MOCK-1"))
    db.commit()
    assert h1b_steps.child_filing_verified(db, child_id) is False

    ex = fm.AdapterExecution(org_id="org1", application_id=child_id,
                             candidate_id="cand1", candidate_version=1,
                             status="completed")
    db.add(ex)
    db.commit()
    db.add(fm.AdapterOutcomeEvidence(execution_id=ex.id, hostname="flag.dol.gov",
                                     state_category="submitted"))
    db.commit()
    assert h1b_steps.child_filing_verified(db, child_id) is True


def test_step_verifies_on_child_government_evidence(client, db):
    from app.adapter_factory import models as fm
    out = _create_case(client, db)
    case_id = out["case_id"]
    child_id = _release(client, case_id, "lca").json()["child_case_id"]
    db.add(models.SubmissionConfirmation(application_id=child_id, reference_no="R1"))
    ex = fm.AdapterExecution(org_id="org1", application_id=child_id,
                             candidate_id="c", candidate_version=1,
                             status="completed")
    db.add(ex)
    db.commit()
    db.add(fm.AdapterOutcomeEvidence(execution_id=ex.id, hostname="flag.dol.gov",
                                     state_category="submitted"))
    db.commit()

    lca = _step(db, case_id, "lca")
    h1b_steps.mark_step_verified(db, lca, receipts={"lca_number": "I-200-y"},
                                 actor="worker")
    db.refresh(lca)
    assert lca.status == "verified" and lca.lca_number == "I-200-y"
    db.refresh(i129 := _step(db, case_id, "i129"))
    assert i129.status == "ready"


# ---------- certified_lca required-flip on LCA verification ----------

def test_lca_verification_flips_certified_lca_required(client, db):
    out = _create_case(client, db)
    case_id = out["case_id"]

    def _certified_item():
        cg = db.execute(select(CaseRouteGuidance).where(
            CaseRouteGuidance.case_id == case_id)).scalars().first()
        return cg.checklist, next(i for i in cg.checklist
                                  if i["id"] == "certified_lca")

    _, before = _certified_item()
    assert before["required"] is False

    _verify_step_offline(db, case_id, "lca")
    db.expire_all()

    checklist, after = _certified_item()
    assert after["required"] is True
    # Re-derivation preserved the rest of the case shape (in-US, non-first-timer).
    prior = next(i for i in checklist if i["id"] == "prior_i797")
    assert prior["required"] is True
    assert any(i["id"] == "i94_record" for i in checklist)


# ---------- concurrent release (the duplicate-child race) ----------

def test_concurrent_release_shares_one_child(client, db):
    """Two releases that both read the step as 'ready' before either commits
    must not both mint a child. The DB-level claim (ready -> in_progress) means
    only the winner creates the filing case; the loser returns the same child."""
    from app.db import SessionLocal
    from app.security import Principal
    out = _create_case(client, db)
    case_id = out["case_id"]
    parent0 = db.get(models.VisaApplication, case_id)
    principal = Principal(org_id=parent0.org_id, user_id="user1")

    s_win = SessionLocal()
    s_lose = SessionLocal()
    s_lose.expire_on_commit = False
    try:
        # The loser reads the ready step, then ends its read transaction while
        # KEEPING the stale ready/empty snapshot in memory (so it will race).
        step_lose = _step(s_lose, case_id, "lca")
        parent_lose = s_lose.get(models.VisaApplication, case_id)
        assert step_lose.status == "ready" and not step_lose.child_case_id
        s_lose.commit()

        # The winner releases fully and commits its child.
        parent_win = s_win.get(models.VisaApplication, case_id)
        step_win = _step(s_win, case_id, "lca")
        r_win = h1b_filing.release_step(s_win, parent=parent_win, step=step_win,
                                        principal=principal)
        assert r_win["already_exists"] is False

        # The loser still believes the step is releasable; the claim sends it to
        # the winner's child instead of creating a duplicate.
        r_lose = h1b_filing.release_step(s_lose, parent=parent_lose,
                                         step=step_lose, principal=principal)
        assert r_lose["already_exists"] is True
        assert r_lose["child_case_id"] == r_win["child_case_id"]
    finally:
        s_win.close()
        s_lose.close()

    children = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.visa_type == "h1b_lca")).scalars().all()
    assert len([c for c in children
                if (c.answers or {}).get("h1b_parent_case_id") == case_id]) == 1
