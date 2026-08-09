"""H1B filing coordinator: a ready step becomes a real linked child filing
case with explicitly assembled, party-partitioned answers — never a wholesale
merge of both parties, and never a release without verified predecessors."""
from sqlalchemy import select

from app import models
from app.h1b import filing as h1b_filing
from app.h1b import models as h1b_models
from app.h1b import steps as h1b_steps
from app.visa_snapshot.models import CaseRouteGuidance

from .conftest import AUTH, PASSPORT_MRZ

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
    db.commit()
    return out


def _step(db, case_id, step_key):
    return db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id,
        h1b_models.H1bCaseStep.step_key == step_key)).scalars().first()


def _release(client, case_id, step_key):
    return client.post(f"/h1b/cases/{case_id}/steps/{step_key}/release",
                       headers=AUTH)


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
    lca = _step(db, case_id, "lca")
    h1b_steps.mark_step_verified(db, lca, receipts={
        "lca_number": "I-200-26123-456789"}, actor="test")
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
        h1b_steps.mark_step_verified(db, _step(db, case_id, key),
                                     receipts={}, actor="test")
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

def test_parent_deletion_leaves_children_as_separate_cases(client, db):
    from app import privacy
    out = _create_case(client, db)
    case_id = out["case_id"]
    child_id = _release(client, case_id, "lca").json()["child_case_id"]

    privacy.delete_case(db, case_id)
    assert db.get(models.VisaApplication, case_id) is None
    assert db.execute(select(h1b_models.H1bCaseStep).where(
        h1b_models.H1bCaseStep.application_id == case_id)).scalars().all() == []
    # The child is a real, separate case: it survives with its own guidance
    # and can itself be erased later — deleting the parent must not crash it.
    child = db.get(models.VisaApplication, child_id)
    assert child is not None
    cg = db.execute(select(CaseRouteGuidance).where(
        CaseRouteGuidance.case_id == child_id)).scalars().first()
    assert cg is not None
    privacy.delete_case(db, child_id)
    assert db.get(models.VisaApplication, child_id) is None
