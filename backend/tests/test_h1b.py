"""H1B edition P1: two-party case creation, party-tagged checklist,
pipeline sequencing honesty, doc-type registries, and the disclaimer contract."""
import pytest
from sqlalchemy import select

from app import models
from app.h1b import models as h1b_models
from app.h1b import steps as h1b_steps
from app.h1b.checklist import derive_h1b_checklist, items_for_party
from app.h1b.disclaimer import ATTORNEY_DISCLAIMER, disclaimer
from app.h1b.guidance import CASE_KINDS, FEES, build_guidance, step_plan
from app.providers.doc_classifier import (CANONICAL_TYPES,
                                          classify_supporting_document)
from app.visa_snapshot.intake_flow import (MANUAL_DOC_TYPES,
                                           doc_type_for_label)

from .conftest import AUTH, AUTH2


# ---------- step plans ----------

def test_extension_plan_skips_registration_and_consular():
    plan = step_plan("extension", beneficiary_abroad=False)
    assert [s["step_key"] for s in plan] == ["lca", "i129"]
    assert plan[1]["depends_on"] == ["lca"]


def test_cap_plan_has_registration_and_consular_leg():
    plan = step_plan("cap_initial", beneficiary_abroad=True)
    keys = [s["step_key"] for s in plan]
    assert keys == ["lca", "registration", "i129", "ds160_consular"]
    i129 = next(s for s in plan if s["step_key"] == "i129")
    assert sorted(i129["depends_on"]) == ["lca", "registration"]
    consular = plan[-1]
    assert consular["acting_party"] == "beneficiary"
    assert consular["depends_on"] == ["i129"]


def test_unknown_case_kind_rejected():
    with pytest.raises(ValueError):
        step_plan("lottery_hack", beneficiary_abroad=True)


def test_guidance_is_curated_with_sources():
    g = build_guidance("extension", beneficiary_abroad=False)
    assert g["status"] == "curated"
    assert g["year_round"] is True
    assert g["sources"], "curated guidance must carry official sources"
    assert all(u.startswith("https://") for u in g["sources"])
    # Fee facts present and sourced; no invented numbers without a source.
    for fee in FEES.values():
        assert fee.get("source", "").startswith("https://")


# ---------- checklist ----------

def test_checklist_is_party_tagged_and_covers_both_parties():
    items = derive_h1b_checklist(case_kind="extension", beneficiary_abroad=False,
                                 beneficiary_in_us=True)
    assert all("party" in i for i in items)
    ben = items_for_party(items, "beneficiary")
    pet = items_for_party(items, "petitioner")
    assert {i["id"] for i in ben} >= {"passport", "degree_certificate",
                                      "graduation_certificate", "transcript",
                                      "credential_evaluation", "i94_record"}
    assert {i["id"] for i in pet} >= {"support_letter", "job_description",
                                      "fein_evidence", "employer_financials",
                                      "corporate_relationship", "certified_lca"}


def test_chinese_degree_documents_are_distinct_items():
    items = derive_h1b_checklist(case_kind="extension", beneficiary_abroad=True)
    ids = [i["id"] for i in items]
    assert "degree_certificate" in ids and "graduation_certificate" in ids


def test_certified_lca_flips_required_after_certification():
    before = derive_h1b_checklist(case_kind="extension", beneficiary_abroad=False)
    after = derive_h1b_checklist(case_kind="extension", beneficiary_abroad=False,
                                 lca_certified=True)
    lca_before = next(i for i in before if i["id"] == "certified_lca")
    lca_after = next(i for i in after if i["id"] == "certified_lca")
    assert lca_before["required"] is False and lca_after["required"] is True


def test_i94_only_for_in_us_beneficiary():
    abroad = derive_h1b_checklist(case_kind="extension", beneficiary_abroad=True)
    in_us = derive_h1b_checklist(case_kind="extension", beneficiary_abroad=False,
                                 beneficiary_in_us=True)
    assert not any(i["id"] == "i94_record" for i in abroad)
    assert any(i["id"] == "i94_record" for i in in_us)


# ---------- doc-type registries (the four-sync contract) ----------

H1B_TYPES = ("degree_certificate", "graduation_certificate", "transcript",
             "resume_cv", "prior_i797", "i94_record", "credential_evaluation",
             "employer_support_letter", "job_description", "fein_evidence",
             "employer_financials", "corporate_relationship_evidence",
             "certified_lca")


def test_h1b_types_in_canonical_and_manual_registries():
    for t in H1B_TYPES:
        assert t in CANONICAL_TYPES, t
        assert t in MANUAL_DOC_TYPES, t
    assert "passport" not in MANUAL_DOC_TYPES


def test_checklist_satisfied_by_types_are_canonical():
    items = derive_h1b_checklist(case_kind="cap_initial", beneficiary_abroad=True,
                                 beneficiary_in_us=True)
    for item in items:
        for t in item["satisfied_by"]:
            assert t in CANONICAL_TYPES, f"{item['id']} -> {t}"


def test_lca_label_is_not_swallowed_by_form_trap():
    # "form" historically maps any label to the non-uploadable destination_form.
    assert doc_type_for_label("Labor Condition Application (Form ETA-9035)") == "certified_lca"
    assert doc_type_for_label("FEIN evidence (IRS CP-575)") == "fein_evidence"
    assert doc_type_for_label("Prior I-797 Notice of Action") == "prior_i797"


def test_keyword_classification_for_h1b_fixtures():
    cases = {
        "certified_lca": "U.S. Department of Labor OFLC. Labor Condition "
                         "Application ETA-9035E. Prevailing wage: $132,000. "
                         "SOC code 15-1252. Wage level II. Certification date.",
        "prior_i797": "Form I-797, Notice of Action. Receipt number "
                      "WAC2412345678. Notice type: Approval notice.",
        "degree_certificate": "学位证书 Degree of Master of Science conferred "
                              "upon the graduate. Bachelor of Engineering.",
        "transcript": "Official transcript. GPA 3.8. Credit hours 120. "
                      "Semester 1 成绩单 academic record.",
        "employer_support_letter": "Letter of support for H-1B petition. The "
                                   "position is a specialty occupation. In "
                                   "support of the beneficiary.",
        "fein_evidence": "IRS CP 575. Employer Identification Number EIN "
                         "assigned. Federal tax identification.",
    }
    for expected, text in cases.items():
        assert classify_supporting_document(text) == expected, expected


def test_bank_statement_still_classifies_after_h1b_types():
    text = ("Bank statement. Opening balance 4,000. Closing balance 5,000. "
            "Account statement. IBAN DE00. Transaction list.")
    assert classify_supporting_document(text) == "bank_statement"


# ---------- disclaimer ----------

def test_disclaimer_localized_and_never_empty():
    for locale in ("en", "zh-CN", "zh-Hant"):
        assert ATTORNEY_DISCLAIMER[locale]
        assert disclaimer(locale) == ATTORNEY_DISCLAIMER[locale]
    assert "attorney" in disclaimer("en")
    assert "律师" in disclaimer("zh-CN") and "律師" in disclaimer("zh-Hant")
    assert disclaimer("fr-FR") == ATTORNEY_DISCLAIMER["en"]


# ---------- API: creation, pipeline, tenancy ----------

def _create_case(client, **overrides):
    body = {"case_kind": "extension",
            "beneficiary_full_name": "WEI ZHANG",
            "beneficiary_email": "wei.zhang@example.com",
            "beneficiary_abroad": False, "beneficiary_in_us": True,
            "first_h1b": False}
    body.update(overrides)
    r = client.post("/h1b/cases", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_case_returns_steps_checklist_disclaimer(client):
    out = _create_case(client)
    assert out["case_kind"] == "extension"
    assert [s["step_key"] for s in out["steps"]] == ["lca", "i129"]
    assert out["steps"][0]["status"] == "ready"
    assert out["steps"][1]["status"] == "blocked"
    assert out["attorney_disclaimer"] == disclaimer("en")
    assert any(i["party"] == "petitioner" for i in out["checklist"])


def test_create_case_zh_locale_disclaimer(client):
    out = _create_case(client, locale="zh-CN")
    assert out["attorney_disclaimer"] == disclaimer("zh-CN")


def test_create_case_rejects_unknown_kind(client):
    r = client.post("/h1b/cases", json={
        "case_kind": "instant_approval", "beneficiary_full_name": "X",
        "beneficiary_email": "x@example.com"}, headers=AUTH)
    assert r.status_code == 422


def test_parties_created_one_per_role(client, db):
    out = _create_case(client)
    rows = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == out["case_id"])).scalars().all()
    assert sorted(p.role for p in rows) == ["beneficiary", "petitioner"]


def test_default_creation_binds_caller_as_beneficiary(client, db):
    out = _create_case(client)
    rows = {p.role: p for p in db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == out["case_id"])).scalars().all()}
    assert rows["beneficiary"].user_id == "user1"
    assert rows["petitioner"].user_id == ""


def test_employer_creation_binds_caller_as_petitioner(client, db):
    """The employer-console bug: creating a case bound the EMPLOYER as the
    worker, so their own console addressed them as the employee. acting_as
    'petitioner' binds the creator to the filing seat and leaves the worker's
    seat open (invited) until they join."""
    out = _create_case(client, acting_as="petitioner")
    rows = {p.role: p for p in db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == out["case_id"])).scalars().all()}
    assert rows["petitioner"].user_id == "user1"
    assert rows["beneficiary"].user_id == ""
    assert rows["beneficiary"].status == "invited"


def test_creation_rejects_unknown_acting_as(client):
    r = client.post("/h1b/cases", json={
        "case_kind": "extension", "beneficiary_full_name": "X",
        "beneficiary_email": "x@example.com", "acting_as": "attorney"},
        headers=AUTH)
    assert r.status_code == 422


def test_claim_binds_unbound_seat_and_is_idempotent(client, db):
    out = _create_case(client, acting_as="petitioner")  # beneficiary unbound
    case_id = out["case_id"]
    # A different org member (the worker) claims the open beneficiary seat.
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/claim",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200 and r.json()["claimed"] is True
    # Claiming again is a no-op, never an error.
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/claim",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200 and r.json()["already_yours"] is True
    # A seat someone else operates is never transferable by claim.
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/claim",
                    headers=AUTH)
    assert r.status_code == 409
    # And the claim never crosses orgs.
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/claim",
                    headers=AUTH2)
    assert r.status_code in (403, 404)


def test_pipeline_requires_owner_org(client):
    out = _create_case(client)
    r = client.get(f"/h1b/cases/{out['case_id']}/pipeline", headers=AUTH2)
    assert r.status_code in (403, 404)
    r = client.get(f"/h1b/cases/{out['case_id']}/pipeline", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["parties"]


def test_case_route_guidance_registered_for_journey(client, db):
    from app.visa_snapshot.models import CaseRouteGuidance
    out = _create_case(client)
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == out["case_id"])).scalars().first()
    assert cg is not None
    assert cg.continuation_kind == "h1b_petition"
    assert cg.checklist


def test_employer_profile_fein_validation(client):
    r = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-34"}, headers=AUTH)
    assert r.status_code == 422
    r = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-3456789",
        "parent_company_name": "Trip.com Group Ltd",
        "parent_company_country": "China"}, headers=AUTH)
    assert r.status_code == 200
    r = client.get("/h1b/employer-profiles", headers=AUTH)
    profiles = r.json()["profiles"]
    assert any(p["fein_last4"] == "6789" for p in profiles)


# ---------- sequencing honesty ----------

def test_step_cannot_release_without_verified_predecessor(client, db):
    out = _create_case(client)
    case_id = out["case_id"]
    steps = h1b_steps.steps_for_case(db, case_id)
    lca = next(s for s in steps if s.step_key == "lca")
    i129 = next(s for s in steps if s.step_key == "i129")
    assert lca.status == "ready" and i129.status == "blocked"

    # A recompute without evidence must NOT unblock the successor.
    h1b_steps.recompute_readiness(db, case_id)
    db.refresh(i129)
    assert i129.status == "blocked"

    # No child confirmation row exists, so verification reads False.
    assert h1b_steps.child_filing_verified(db, lca.child_case_id) is False

    # Doctrine (finding #6): 'verified' is never inferred from a receipt dict —
    # without real government evidence, mark_step_verified refuses and the
    # successor stays blocked.
    with pytest.raises(h1b_steps.EvidenceRequired):
        h1b_steps.mark_step_verified(db, lca,
                                     receipts={"lca_number": "I-200-26123-456789"},
                                     actor="test")
    db.refresh(i129)
    assert i129.status == "blocked"

    # An admin-accepted offline government artifact (a certified LCA on this
    # case) IS evidence — only then does the successor unblock.
    doc = models.StoredDocument(
        org_id="org1", application_id=case_id, name="certified-lca.pdf",
        mime="application/pdf", doc_type="certified_lca", approved=True)
    db.add(doc)
    db.commit()
    h1b_steps.mark_step_verified(db, lca, receipts={"lca_number": "I-200-26123-456789"},
                                 actor="test", offline_evidence_document_id=doc.id)
    db.refresh(i129)
    assert i129.status == "ready"
    db.refresh(lca)
    assert lca.lca_number == "I-200-26123-456789"


# ---------- end-to-end with sample documents ----------

SAMPLE_DOCS = {
    # beneficiary
    "degree_certificate": "学位证书 Degree of Master of Science conferred upon "
                          "WEI ZHANG. Bachelor of Engineering.",
    "graduation_certificate": "毕业证书 Graduation certificate. Has completed "
                              "the course of study. 毕业证",
    "transcript": "Official transcript 成绩单. GPA 3.7. Credit hours 128. "
                  "Semester records, academic record.",
    "credential_evaluation": "Credential evaluation report. Foreign degree "
                             "equivalency: US master's degree equivalent. "
                             "Evaluator: sample agency. WES reference.",
    "resume": "Resume 简历. Curriculum vitae. Professional summary. Work "
              "experience: software engineer, Trip.com.",
    "prior_i797": "Form I-797 Notice of Action. Receipt number WAC2498765432. "
                  "Notice type: Approval notice. Petitioner and beneficiary.",
    "i94_record": "Form I-94 Arrival/Departure record. Admit until 10/01/2026. "
                  "Class of admission H-1B. Most recent date of entry.",
    # petitioner
    "support_letter": "Letter of support for H-1B petition. The position is a "
                      "specialty occupation. In support of the beneficiary.",
    "job_description": "Job description. Duties and responsibilities. Minimum "
                       "requirements. Position overview and essential functions.",
    "fein_evidence": "IRS CP 575. Employer Identification Number EIN assigned "
                     "12-3456789. Federal tax identification.",
    "employer_financials": "Audited financial statements. Income statement and "
                           "balance sheet. Federal tax return Form 1120.",
    "corporate_relationship": "Certificate of incorporation. Wholly owned "
                              "subsidiary of Trip.com Group. Parent company "
                              "ownership structure. Organizational chart.",
}


def test_sample_documents_walk_the_whole_checklist(client, db):
    """A full two-party intake on sample documents: every required item gets a
    sample upload, explicit Submit fulfills it, and the stage completes into
    petition_preparation. Sample docs exercise the real pipeline — upload
    validation, OCR text tier, keyword classification, provenance verdicts —
    with zero simulated government output."""
    from .conftest import PASSPORT_MRZ
    out = _create_case(client)          # extension, in-US, first_h1b=False
    case_id = out["case_id"]

    required = [i for i in out["checklist"]
                if i["required"] and i["kind"] == "document"]
    assert {i["id"] for i in required} == set(SAMPLE_DOCS) | {"passport"}

    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "passport.pdf", "mime": "application/pdf", "size_bytes": 1024,
        "checklist_item_id": "passport", "text": PASSPORT_MRZ},
        headers=AUTH)
    assert up.status_code == 200, up.text
    s = client.post(f"/cases/{case_id}/checklist/passport/submit",
                    json={"document_id": up.json()["id"], "confirm": True},
                    headers=AUTH)
    assert s.status_code == 200, s.text

    for item_id, text in SAMPLE_DOCS.items():
        up = client.post(f"/cases/{case_id}/documents", json={
            "name": f"{item_id}.pdf", "mime": "application/pdf",
            "size_bytes": 2048, "checklist_item_id": item_id, "text": text},
            headers=AUTH)
        assert up.status_code == 200, (item_id, up.text)
        s = client.post(f"/cases/{case_id}/checklist/{item_id}/submit",
                        json={"document_id": up.json()["id"], "confirm": True},
                        headers=AUTH)
        assert s.status_code == 200, (item_id, s.text)

    done = client.post(f"/cases/{case_id}/checklist/complete", headers=AUTH)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["completed"] is True
    assert body["next_stage"] == "petition_preparation"


def test_cross_party_upload_stays_advisory(client):
    """Employer financials uploaded to the beneficiary's transcript item gets a
    mismatch verdict but still submits with explicit confirmation — the
    applicant's word outranks the classifier, per the intake doctrine."""
    out = _create_case(client)
    case_id = out["case_id"]
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "wrong.pdf", "mime": "application/pdf", "size_bytes": 2048,
        "checklist_item_id": "transcript",
        "text": SAMPLE_DOCS["employer_financials"]}, headers=AUTH)
    assert up.status_code == 200
    s = client.post(f"/cases/{case_id}/checklist/transcript/submit",
                    json={"document_id": up.json()["id"], "confirm": True},
                    headers=AUTH)
    assert s.status_code == 200, s.text


def test_privacy_cascade_includes_h1b_tables(client, db):
    from app import privacy
    out = _create_case(client)
    case_id = out["case_id"]
    assert h1b_models.CaseParty in privacy._CASE_CHILD_MODELS
    assert h1b_models.H1bCaseStep in privacy._CASE_CHILD_MODELS
    privacy.delete_case(db, case_id)
    assert db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id)).scalars().all() == []
    assert db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id)).scalars().all() == []


# ============================================================================
# Agent B — per-party authorization, the verify caller, statutory windows,
# FEIN on the filing path, and the CaseParty.answers writer. Each test FAILS on
# the pre-change endpoints and passes after.
# ============================================================================

# A distinct petitioner principal (same org, different user) and an admin. In
# this build the petitioner CaseParty is unbound until an account operates it;
# the tests bind it explicitly, exactly as a P3 employer session would.
PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "hr1"}
ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin1"}


def _bind_petitioner(db, case_id, user_id="hr1"):
    """Bind an account to the petitioner party (the P3 employer session does this
    for real; here it makes the petitioner principal able to act)."""
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    pet.user_id = user_id
    db.commit()
    return pet


# ---------- #3/#14/#20 per-party authorization ----------

def test_beneficiary_cannot_release_petitioner_step(client, db):
    """The lca step is petitioner-acting (ETA-9035, penalty of perjury). The
    beneficiary who created the case operates the beneficiary party and must be
    refused; only the bound petitioner (or an admin) may release it."""
    out = _create_case(client)                     # AUTH becomes the beneficiary
    case_id = out["case_id"]
    r = client.post(f"/h1b/cases/{case_id}/steps/lca/release", headers=AUTH)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["acting_party"] == "petitioner"

    _bind_petitioner(db, case_id, "hr1")
    r = client.post(f"/h1b/cases/{case_id}/steps/lca/release", headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["visa_type"] == "h1b_lca"

    # And an admin may act on either party's step.
    out2 = _create_case(client)
    r = client.post(f"/h1b/cases/{out2['case_id']}/steps/lca/release",
                    headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text


def test_pipeline_read_is_scoped_to_caller_party(client, db):
    """The petitioner party payload (employer contact, FEIN-bearing profile) must
    not appear in the beneficiary's pipeline read, and vice versa (finding
    #3/#20 reads)."""
    out = _create_case(client)
    case_id = out["case_id"]
    r = client.get(f"/h1b/cases/{case_id}/pipeline", headers=AUTH)
    assert r.status_code == 200
    assert {p["role"] for p in r.json()["parties"]} == {"beneficiary"}

    _bind_petitioner(db, case_id, "hr1")
    r = client.get(f"/h1b/cases/{case_id}/pipeline", headers=PETITIONER_AUTH)
    assert {p["role"] for p in r.json()["parties"]} == {"petitioner"}

    r = client.get(f"/h1b/cases/{case_id}/pipeline", headers=ADMIN_AUTH)
    assert {p["role"] for p in r.json()["parties"]} == {"beneficiary", "petitioner"}


# ---------- #4/#12 the missing verify caller ----------

def test_verify_endpoint_unblocks_successor_only_with_government_evidence(client, db):
    """POST verify flips lca->verified and i129->ready only when the child filing
    holds real government evidence. Without it the endpoint honestly 409s and the
    successor stays blocked — the deadlock (#4/#12) is gone, evidence honesty
    (#6/#8) is kept."""
    from app.adapter_factory import models as fm
    out = _create_case(client)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id, "hr1")
    rel = client.post(f"/h1b/cases/{case_id}/steps/lca/release", headers=PETITIONER_AUTH)
    assert rel.status_code == 200, rel.text
    child_id = rel.json()["child_case_id"]

    # No evidence yet: honest 409, i129 stays blocked.
    v = client.post(f"/h1b/cases/{case_id}/steps/lca/verify", json={},
                    headers=PETITIONER_AUTH)
    assert v.status_code == 409, v.text
    pipe = client.get(f"/h1b/cases/{case_id}/pipeline", headers=ADMIN_AUTH).json()
    assert next(s for s in pipe["steps"] if s["step_key"] == "i129")["status"] == "blocked"

    # A bare SubmissionConfirmation (mock/sandbox) is NOT enough on its own.
    db.add(models.SubmissionConfirmation(application_id=child_id,
                                         reference_no="REF-SANDBOX"))
    db.commit()
    v = client.post(f"/h1b/cases/{case_id}/steps/lca/verify", json={},
                    headers=PETITIONER_AUTH)
    assert v.status_code == 409, v.text

    # Real government evidence: a completed execution with a government-host
    # (flag.dol.gov) outcome. Now verification flips lca and unblocks i129.
    ex = fm.AdapterExecution(org_id="org1", application_id=child_id,
                             candidate_id="cand1", candidate_version=1,
                             status="completed")
    db.add(ex)
    db.flush()
    db.add(fm.AdapterOutcomeEvidence(execution_id=ex.id, hostname="flag.dol.gov",
                                     state_category="submitted"))
    db.commit()
    v = client.post(f"/h1b/cases/{case_id}/steps/lca/verify",
                    json={"receipts": {"lca_number": "I-200-26123-456789"}},
                    headers=PETITIONER_AUTH)
    assert v.status_code == 200, v.text
    statuses = {s["step_key"]: s["status"] for s in v.json()["steps"]}
    assert statuses["lca"] == "verified"
    assert statuses["i129"] == "ready"


def test_verify_admin_offline_evidence_path(client, db):
    """An admin may record an accepted offline government artifact (a certified
    LCA on the case) as verification; a party cannot self-attest one."""
    out = _create_case(client)
    case_id = out["case_id"]
    doc = models.StoredDocument(
        org_id="org1", application_id=case_id, name="certified-lca.pdf",
        mime="application/pdf", doc_type="certified_lca", approved=True)
    db.add(doc)
    db.commit()
    _bind_petitioner(db, case_id, "hr1")

    # A party cannot record an offline government outcome.
    r = client.post(f"/h1b/cases/{case_id}/steps/lca/verify",
                    json={"offline_evidence_document_id": doc.id},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 403, r.text

    # The admin can, and the successor unblocks.
    r = client.post(f"/h1b/cases/{case_id}/steps/lca/verify",
                    json={"offline_evidence_document_id": doc.id,
                          "receipts": {"lca_number": "I-200-OFFLINE-01"}},
                    headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    statuses = {s["step_key"]: s["status"] for s in r.json()["steps"]}
    assert statuses["lca"] == "verified" and statuses["i129"] == "ready"


# ---------- #9 statutory registration window ----------

def test_registration_window_helper_open_and_closed():
    import datetime as dt
    from app.h1b import api as h1b_api
    assert h1b_api._registration_window_status(dt.date(2026, 8, 9))["open"] is False
    assert h1b_api._registration_window_status(dt.date(2027, 3, 10))["open"] is True
    assert h1b_api._registration_window_status(dt.date(2027, 4, 1))["open"] is False


def test_cap_registration_release_honors_window(client, db, monkeypatch):
    import datetime as dt
    from app.h1b import api as h1b_api
    prof = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-3456789"}, headers=AUTH).json()
    out = _create_case(client, case_kind="cap_initial",
                       employer_profile_id=prof["employer_profile_id"])
    case_id = out["case_id"]
    _bind_petitioner(db, case_id, "hr1")

    # Out of window: honest 409 naming the next window; no child filing opened.
    monkeypatch.setattr(h1b_api, "_today", lambda: dt.date(2026, 8, 9))
    r = client.post(f"/h1b/cases/{case_id}/steps/registration/release",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["next_window"]
    pipe = client.get(f"/h1b/cases/{case_id}/pipeline", headers=ADMIN_AUTH).json()
    reg = next(s for s in pipe["steps"] if s["step_key"] == "registration")
    assert reg["status"] == "ready" and reg["child_case_id"] is None

    # In window: the release proceeds into a real registration filing.
    monkeypatch.setattr(h1b_api, "_today", lambda: dt.date(2027, 3, 10))
    r = client.post(f"/h1b/cases/{case_id}/steps/registration/release",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["visa_type"] == "h1b_registration"


# ---------- #15 FEIN on the filing path ----------

def test_employer_profile_without_fein_cannot_bind_to_case(client):
    """FEIN validation lived only on profile creation and was skippable (an empty
    FEIN was accepted). Binding such a profile to a filing case — where the FEIN
    becomes a statement on ETA-9035/I-129 — is refused."""
    prof = client.post("/h1b/employer-profiles",
                       json={"legal_name": "No FEIN Inc"}, headers=AUTH)
    assert prof.status_code == 200                      # empty FEIN allowed here
    pid = prof.json()["employer_profile_id"]
    r = client.post("/h1b/cases", json={
        "case_kind": "extension", "beneficiary_full_name": "WEI ZHANG",
        "beneficiary_email": "wei.zhang@example.com",
        "employer_profile_id": pid}, headers=AUTH)
    assert r.status_code == 422, r.text
    assert "FEIN" in r.json()["detail"]


def test_release_refuses_when_bound_profile_fein_invalid(client, db):
    prof = client.post("/h1b/employer-profiles", json={
        "legal_name": "Trip.com US Inc", "fein": "12-3456789"},
        headers=AUTH).json()
    out = _create_case(client, employer_profile_id=prof["employer_profile_id"])
    case_id = out["case_id"]
    _bind_petitioner(db, case_id, "hr1")
    # The bound profile's FEIN is corrupted after the fact; the filing path must
    # re-validate rather than carry a malformed FEIN onto a federal form.
    profile = db.get(h1b_models.EmployerProfile, prof["employer_profile_id"])
    profile.fein = ""
    db.commit()
    r = client.post(f"/h1b/cases/{case_id}/steps/lca/release", headers=PETITIONER_AUTH)
    assert r.status_code == 409, r.text
    assert "FEIN" in r.json()["detail"]["reason"]


# ---------- CaseParty.answers writer (the missing petitioner writer) ----------

def test_party_answers_writer_is_scoped_and_feeds_the_partition(client, db):
    """The petitioner writes job/wage facts onto CaseParty.answers (the writer
    finding #2 said nothing in production had); the beneficiary cannot write
    them, perjury attestations are refused as free answers, and the whitelisted
    facts flow into the lca child while non-shared keys stay on the party row."""
    out = _create_case(client)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id, "hr1")

    # The beneficiary cannot write petitioner facts.
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": {"job_title": "Software Engineer"}},
                    headers=AUTH)
    assert r.status_code == 403, r.text

    # A penalty-of-perjury attestation is never a free-form answer.
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": {"willful_violator": False}},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 422, r.text

    # The petitioner writes their job facts.
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": {"job_title": "Software Engineer",
                                      "wage_offer": 132000,
                                      "internal_hr_note": "budget review"}},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    assert set(r.json()["written_keys"]) == {"job_title", "wage_offer",
                                             "internal_hr_note"}

    # Released lca child carries the whitelisted facts; the non-shared note stays.
    rel = client.post(f"/h1b/cases/{case_id}/steps/lca/release",
                      headers=PETITIONER_AUTH)
    assert rel.status_code == 200, rel.text
    db.expire_all()
    child = db.get(models.VisaApplication, rel.json()["child_case_id"])
    a = child.answers
    assert a["job_title"] == "Software Engineer" and a["wage_offer"] == 132000
    assert "internal_hr_note" not in a


def test_beneficiary_party_answers_land_on_parent_case(client, db):
    """Beneficiary facts stay on the parent case (where the filing partition and
    intake machinery read them)."""
    out = _create_case(client)
    case_id = out["case_id"]
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/answers",
                    json={"answers": {"birth_country": "China"}}, headers=AUTH)
    assert r.status_code == 200, r.text
    db.expire_all()
    parent = db.get(models.VisaApplication, case_id)
    assert (parent.answers or {}).get("birth_country") == "China"


# ---------- residual: cross-party child reads on the generic case endpoint ----------

def test_generic_child_case_read_hides_the_other_partys_facts(client, db):
    """After the petitioner releases the LCA child, the child's answers carry
    employer FEIN/wage. The generic GET /cases/{child} is org-scoped, so the
    beneficiary could read them — the read half of the party wall. The child
    read must return those facts only to the acting party or an admin."""
    out = _create_case(client)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id, "hr1")
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": {"job_title": "Software Engineer",
                                      "wage_offer": 132000}},
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    rel = client.post(f"/h1b/cases/{case_id}/steps/lca/release",
                      headers=PETITIONER_AUTH)
    assert rel.status_code == 200, rel.text
    child_id = rel.json()["child_case_id"]

    # The acting petitioner sees the filing's facts.
    mine = client.get(f"/cases/{child_id}", headers=PETITIONER_AUTH).json()
    assert mine["answers"].get("wage_offer") == 132000

    # The beneficiary sees the filing exists, never the employer's facts.
    theirs = client.get(f"/cases/{child_id}", headers=AUTH).json()
    assert "wage_offer" not in theirs["answers"]
    assert "employer_fein" not in theirs["answers"]
    assert theirs["answers"].get("h1b_parent_case_id") == case_id

    # An admin retains the full view.
    admin = client.get(f"/cases/{child_id}", headers=ADMIN_AUTH).json()
    assert admin["answers"].get("wage_offer") == 132000

    # Non-H1B parent read is untouched by the scoping.
    parent = client.get(f"/cases/{case_id}", headers=AUTH).json()
    assert parent["answers"].get("full_name") == "WEI ZHANG"
