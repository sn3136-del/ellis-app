"""H1B counsel layer (Agent C): deterministic RFE risk rules, composite
severity, grounded narrative drafts, honest evidence compilation, and the
party wall on every counsel surface."""
import datetime as dt
import json

import pytest
from sqlalchemy import select

from app import models
from app.h1b import counsel
from app.h1b import models as h1b_models
from app.h1b.disclaimer import DISCLAIMER_VERSION, disclaimer
from app.providers import kimi

from .conftest import AUTH, AUTH2, PASSPORT_MRZ

PETITIONER_AUTH = {"Authorization": "Bearer dev-token",
                   "X-Org-Id": "org1", "X-User-Id": "hr1"}
ADMIN_AUTH = {"Authorization": "Bearer admin-token",
              "X-Org-Id": "org1", "X-User-Id": "admin1"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def _bind_petitioner(db, case_id, user_id="hr1"):
    pet = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case_id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    pet.user_id = user_id
    db.commit()
    return pet


def _employer_profile(client, **overrides):
    body = {"legal_name": "Trip.com US Inc", "fein": "12-3456789"}
    body.update(overrides)
    r = client.post("/h1b/employer-profiles", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["employer_profile_id"]


def _write_pet_answers(client, case_id, answers):
    r = client.post(f"/h1b/cases/{case_id}/party/petitioner/answers",
                    json={"answers": answers}, headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text


def _submit_item(client, case_id, item_id, text):
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": f"{item_id}.pdf", "mime": "application/pdf",
        "size_bytes": 2048, "checklist_item_id": item_id, "text": text},
        headers=AUTH)
    assert up.status_code == 200, (item_id, up.text)
    s = client.post(f"/cases/{case_id}/checklist/{item_id}/submit",
                    json={"document_id": up.json()["id"], "confirm": True},
                    headers=AUTH)
    assert s.status_code == 200, (item_id, s.text)
    return up.json()["id"]


DEGREE_TEXT = ("学位证书 Degree of Master of Science conferred upon WEI ZHANG. "
               "Bachelor of Engineering.")
GRADUATION_TEXT = ("毕业证书 Graduation certificate. Has completed the course "
                   "of study. 毕业证")
CRED_EVAL_TEXT = ("Credential evaluation report. Foreign degree equivalency: "
                  "US master's degree equivalent. Evaluator: sample agency. "
                  "WES reference.")
FINANCIALS_TEXT = ("Audited financial statements. Income statement and balance "
                   "sheet. Federal tax return Form 1120.")


# ---------------------------------------------------------------------------
# ctx factory for hermetic rule-engine tests (case_context monkeypatched)
# ---------------------------------------------------------------------------

_ALL_ITEMS = {"credential_evaluation", "degree_certificate",
              "graduation_certificate", "employer_financials"}


def _clean_ctx():
    """A fully prepared extension case: every rule stays silent on it."""
    return {
        "parent_answers": {"full_name": "WEI ZHANG"},
        "petitioner_answers": {"wage_level": "II",
                               "job_title": "Software Engineer",
                               "wage_offer": 150000,
                               "prevailing_wage": 120000,
                               "export_control_license_required": False},
        "employer": h1b_models.EmployerProfile(
            legal_name="Trip.com US Inc", fein="123456789",
            naics_code="561510", address_line1="1 Biscayne Blvd",
            city="Miami", state="FL", year_established=2005,
            parent_company_name="", parent_company_country=""),
        "checklist_items": [],
        "checklist_ids": set(_ALL_ITEMS),
        "required": set(_ALL_ITEMS),
        "satisfied": set(_ALL_ITEMS),
        "degree_facts": {},
        "degree_fact_sources": {},
        "beneficiary_nationality": "",
        "case_kind": "extension",
        "today": dt.date(2026, 8, 10),
    }


def _risks_for_ctx(monkeypatch, ctx):
    monkeypatch.setattr(counsel, "case_context", lambda db, case: ctx)
    return counsel.rfe_risks(None, None)


def _grounds(risks):
    return {r["ground"] for r in risks}


# ---------------------------------------------------------------------------
# rule data integrity
# ---------------------------------------------------------------------------

def test_risk_rules_integrity_and_coverage():
    grounds = [r["ground"] for r in counsel.RISK_RULES]
    assert len(grounds) == len(set(grounds)), "duplicate ground keys"
    for rule in counsel.RISK_RULES:
        assert rule["base_severity"] in counsel.SEVERITIES, rule["ground"]
        assert rule["uscis_wording"].strip(), rule["ground"]
        assert rule["curing_evidence"], rule["ground"]
        assert "H1B_RFE_TAXONOMY" in rule["cite"], rule["ground"]
        for name in rule["signals"]:
            assert name in counsel.SIGNALS, (rule["ground"], name)
    assert set(grounds) >= {
        "wage_level_1", "generic_job_title", "degree_field_mismatch",
        "missing_credential_evaluation", "single_chinese_degree_certificate",
        "third_party_worksite", "foreign_parent_young_subsidiary",
        "export_control_unanswered", "export_control_china_sensitive",
        "late_filing_risk", "lca_wage_below_prevailing",
        "missing_pay_evidence_for_extension"}


def test_counsel_i18n_parity():
    entries = [counsel.DRAFT_LABEL, *counsel.COUNSEL_STRINGS.values()]
    entries += [rule["title"] for rule in counsel.RISK_RULES]
    for entry in entries:
        for locale in ("en", "zh-CN", "zh-Hant"):
            assert entry.get(locale), (entry, locale)


# ---------------------------------------------------------------------------
# each rule fires on a crafted ctx and every rule is silent on the clean one
# ---------------------------------------------------------------------------

def test_every_rule_silent_on_clean_case(monkeypatch):
    assert _risks_for_ctx(monkeypatch, _clean_ctx()) == []


def _mut_wage_level(ctx):
    ctx["petitioner_answers"]["wage_level"] = "I"


def _mut_generic_title(ctx):
    ctx["petitioner_answers"]["job_title"] = "Business Analyst"


def _mut_degree_mismatch(ctx):
    ctx["parent_answers"]["degree_field"] = "Electronic Commerce"
    ctx["petitioner_answers"]["acceptable_degree_fields"] = \
        "Computer Science, Software Engineering"


def _mut_no_cred_eval(ctx):
    ctx["satisfied"] -= {"credential_evaluation"}


def _mut_single_degree_cert(ctx):
    ctx["satisfied"] -= {"graduation_certificate"}


def _mut_third_party(ctx):
    ctx["petitioner_answers"]["h1b_worksite_third_party"] = "yes"


def _mut_foreign_parent_young(ctx):
    ctx["employer"].parent_company_country = "China"
    ctx["employer"].year_established = 2025


def _mut_export_unanswered(ctx):
    del ctx["petitioner_answers"]["export_control_license_required"]


def _mut_china_sensitive(ctx):
    ctx["beneficiary_nationality"] = "China"
    ctx["employer"].naics_code = "334413"


def _mut_status_expired(ctx):
    ctx["parent_answers"]["current_status_expiry"] = "2026-01-01"


def _mut_wage_below(ctx):
    ctx["petitioner_answers"]["wage_offer"] = 100000
    ctx["petitioner_answers"]["prevailing_wage"] = 120000


def _mut_no_pay_evidence(ctx):
    ctx["satisfied"] -= {"employer_financials"}


_MUTATIONS = {
    "wage_level_1": _mut_wage_level,
    "generic_job_title": _mut_generic_title,
    "degree_field_mismatch": _mut_degree_mismatch,
    "missing_credential_evaluation": _mut_no_cred_eval,
    "single_chinese_degree_certificate": _mut_single_degree_cert,
    "third_party_worksite": _mut_third_party,
    "foreign_parent_young_subsidiary": _mut_foreign_parent_young,
    "export_control_unanswered": _mut_export_unanswered,
    "export_control_china_sensitive": _mut_china_sensitive,
    "late_filing_risk": _mut_status_expired,
    "lca_wage_below_prevailing": _mut_wage_below,
    "missing_pay_evidence_for_extension": _mut_no_pay_evidence,
}


@pytest.mark.parametrize("ground", sorted(_MUTATIONS))
def test_rule_fires_only_on_its_crafted_case(monkeypatch, ground):
    ctx = _clean_ctx()
    _MUTATIONS[ground](ctx)
    risks = _risks_for_ctx(monkeypatch, ctx)
    # The crafted mutation trips exactly its own rule — nothing else wakes up,
    # which is the 'stays silent on a clean one' guarantee per rule.
    assert _grounds(risks) == {ground}
    risk = risks[0]
    assert risk["signals_fired"], ground
    assert risk["uscis_wording"] and risk["curing_evidence"] and risk["cite"]


# ---------------------------------------------------------------------------
# severity: composites escalate, pinned rules never move
# ---------------------------------------------------------------------------

def test_single_composite_keeps_base_severity(monkeypatch):
    ctx = _clean_ctx()
    _mut_wage_level(ctx)
    (risk,) = _risks_for_ctx(monkeypatch, ctx)
    assert risk["severity"] == "elevated"


def test_two_composites_escalate_each_other(monkeypatch):
    ctx = _clean_ctx()
    _mut_wage_level(ctx)
    _mut_third_party(ctx)
    risks = _risks_for_ctx(monkeypatch, ctx)
    by_ground = {r["ground"]: r for r in risks}
    assert by_ground["wage_level_1"]["severity"] == "high"
    assert by_ground["third_party_worksite"]["severity"] == "high"


def test_china_sensitive_stays_review_advised_even_in_composite(monkeypatch):
    ctx = _clean_ctx()
    _mut_wage_level(ctx)
    _mut_third_party(ctx)
    _mut_china_sensitive(ctx)
    risks = _risks_for_ctx(monkeypatch, ctx)
    by_ground = {r["ground"]: r for r in risks}
    # Pinned: Ellis cannot know the industry from a NAICS prefix, so this
    # ground never claims more than 'review advised'.
    assert by_ground["export_control_china_sensitive"]["severity"] == "advisory"
    assert by_ground["wage_level_1"]["severity"] == "high"


def test_pinned_high_rules(monkeypatch):
    ctx = _clean_ctx()
    _mut_status_expired(ctx)
    _mut_wage_below(ctx)
    risks = _risks_for_ctx(monkeypatch, ctx)
    by_ground = {r["ground"]: r for r in risks}
    assert by_ground["late_filing_risk"]["severity"] == "high"
    assert by_ground["lca_wage_below_prevailing"]["severity"] == "high"
    # Sorted most severe first.
    assert [r["severity"] for r in risks] == sorted(
        (r["severity"] for r in risks),
        key=lambda s: -counsel.SEVERITIES.index(s))


# ---------------------------------------------------------------------------
# individual predicate honesty
# ---------------------------------------------------------------------------

def test_prc_detection_is_exact_never_substring():
    sig = counsel.SIGNALS["beneficiary_nationality_prc"]
    for value in ("CHN", "China", "china", "中国", "People's Republic of China"):
        ctx = _clean_ctx()
        ctx["beneficiary_nationality"] = value
        assert sig(ctx), value
    for value in ("TWN", "Taiwan", "Taiwan, Republic of China", "Singapore", ""):
        ctx = _clean_ctx()
        ctx["beneficiary_nationality"] = value
        assert not sig(ctx), value


def test_wage_comparison_units_and_unknowns():
    sig = counsel.SIGNALS["wage_offer_below_prevailing_wage"]
    ctx = _clean_ctx()
    ctx["petitioner_answers"].update({"wage_offer": "$100,000",
                                      "prevailing_wage": "120,000"})
    assert sig(ctx)
    # Incomparable units never fire — honesty over guessing.
    ctx["petitioner_answers"].update({"wage_offer_unit": "hour",
                                      "prevailing_wage_unit": "year"})
    assert not sig(ctx)
    # Unknown values never fire.
    ctx2 = _clean_ctx()
    ctx2["petitioner_answers"].pop("prevailing_wage")
    assert not sig(ctx2)


def test_generic_title_qualifier_suppresses():
    sig = counsel.SIGNALS["job_title_generic_without_qualifier"]
    for title, expected in (("Business Analyst", True),
                            ("Project Coordinator", True),
                            ("Data Analyst", False),
                            ("Software Engineer", False),
                            ("", False)):
        ctx = _clean_ctx()
        ctx["petitioner_answers"]["job_title"] = title
        assert sig(ctx) is expected, title


def test_degree_field_containment_both_directions():
    sig = counsel.SIGNALS["degree_field_not_in_acceptable_fields"]
    ctx = _clean_ctx()
    ctx["parent_answers"]["degree_field"] = "Computer Science"
    ctx["petitioner_answers"]["acceptable_degree_fields"] = \
        "Computer Science and Engineering or Mathematics"
    assert not sig(ctx)          # contained → no mismatch
    ctx["parent_answers"]["degree_field"] = "Electronic Commerce"
    assert sig(ctx)
    ctx["parent_answers"].pop("degree_field")
    assert not sig(ctx)          # unknown is asked, never presumed mismatched


def test_export_control_false_is_a_real_answer():
    sig = counsel.SIGNALS["export_control_attestation_absent"]
    ctx = _clean_ctx()
    assert not sig(ctx)                                  # explicit False answer
    ctx["petitioner_answers"]["export_control_license_required"] = ""
    assert sig(ctx)                                      # blank is unanswered
    del ctx["petitioner_answers"]["export_control_license_required"]
    assert sig(ctx)


def test_worksite_differs_from_employer_address():
    sig = counsel.SIGNALS["worksite_differs_from_employer_address"]
    ctx = _clean_ctx()
    ctx["petitioner_answers"]["worksite_address_line1"] = "1 Biscayne Blvd"
    assert not sig(ctx)                                  # same address
    ctx["petitioner_answers"]["worksite_address_line1"] = "500 Client Way"
    assert sig(ctx)
    ctx2 = _clean_ctx()
    ctx2["employer"] = None                              # nothing to compare
    ctx2["petitioner_answers"]["worksite_address_line1"] = "500 Client Way"
    assert not sig(ctx2)


# ---------------------------------------------------------------------------
# API: rfe-risks endpoint + party-wall redaction
# ---------------------------------------------------------------------------

def _risky_case(client, db):
    profile_id = _employer_profile(
        client, naics_code="334413", year_established=dt.date.today().year,
        parent_company_name="Trip.com Group Ltd",
        parent_company_country="China")
    out = _create_case(client, employer_profile_id=profile_id)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id)
    _write_pet_answers(client, case_id,
                       {"wage_level": "I", "wage_offer": 60000,
                        "prevailing_wage": 75000})
    r = client.post(f"/h1b/cases/{case_id}/party/beneficiary/answers",
                    json={"answers": {"citizenship_country": "China"}},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    return case_id


def test_rfe_risks_endpoint_fires_expected_grounds(client, db):
    case_id = _risky_case(client, db)
    r = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks", headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    grounds = {x["ground"] for x in body["risks"]}
    assert grounds >= {"wage_level_1", "lca_wage_below_prevailing",
                       "foreign_parent_young_subsidiary",
                       "export_control_china_sensitive",
                       "export_control_unanswered",
                       "missing_credential_evaluation",
                       "missing_pay_evidence_for_extension"}
    by_ground = {x["ground"]: x for x in body["risks"]}
    # Composite escalation on the real pipeline, pinned advisory intact.
    assert by_ground["wage_level_1"]["severity"] == "high"
    assert by_ground["export_control_china_sensitive"]["severity"] == "advisory"
    assert by_ground["lca_wage_below_prevailing"]["facts"]["wage_offer"] == 60000
    # Eligibility-adjacent output always carries the disclaimer.
    assert body["attorney_disclaimer"] == disclaimer("en")
    assert body["disclaimer_version"] == DISCLAIMER_VERSION
    # Tenancy: another org never reads the risks.
    r2 = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks", headers=AUTH2)
    assert r2.status_code in (403, 404)


def test_rfe_risks_redacts_wage_numbers_for_beneficiary(client, db):
    case_id = _risky_case(client, db)
    ben = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks", headers=AUTH)
    assert ben.status_code == 200, ben.text
    risks = ben.json()["risks"]
    assert {x["ground"] for x in risks} >= {"wage_level_1",
                                            "lca_wage_below_prevailing"}
    by_ground = {x["ground"]: x for x in risks}
    assert by_ground["lca_wage_below_prevailing"]["facts"]["wage_offer"] == \
        counsel.REDACTED
    assert by_ground["wage_level_1"]["facts"]["wage_level"] == counsel.REDACTED
    dump = json.dumps(risks)
    assert "60000" not in dump and "75000" not in dump

    # The bound petitioner and the admin keep the full numbers.
    for headers in (PETITIONER_AUTH, ADMIN_AUTH):
        full = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks",
                          headers=headers).json()["risks"]
        facts = {x["ground"]: x["facts"] for x in full}
        assert facts["lca_wage_below_prevailing"]["wage_offer"] == 60000
        assert facts["lca_wage_below_prevailing"]["prevailing_wage"] == 75000


def test_rfe_risks_localized_titles(client, db):
    case_id = _risky_case(client, db)
    body = client.get(f"/h1b/cases/{case_id}/counsel/rfe-risks?locale=zh-CN",
                      headers=ADMIN_AUTH).json()
    by_ground = {x["ground"]: x for x in body["risks"]}
    assert by_ground["wage_level_1"]["title"] == \
        counsel.RISK_RULES[0]["title"]["zh-CN"]
    assert body["attorney_disclaimer"] == disclaimer("zh-CN")
    assert by_ground["wage_level_1"]["severity_label"] == \
        counsel.COUNSEL_STRINGS["severity.high"]["zh-CN"]


# ---------------------------------------------------------------------------
# narrative drafting
# ---------------------------------------------------------------------------

def _narrative_case(client, db):
    profile_id = _employer_profile(client)
    out = _create_case(client, employer_profile_id=profile_id)
    case_id = out["case_id"]
    _bind_petitioner(db, case_id)
    _write_pet_answers(client, case_id, {
        "job_title": "Software Engineer", "soc_code": "15-1252",
        "job_duties": "design and build reservation systems",
        "wage_offer": 132000, "wage_offer_unit": "year",
        "worksite_city": "Miami", "worksite_state": "FL"})
    return case_id


def test_narrative_draft_is_labeled_grounded_and_honest(client, db):
    case_id = _narrative_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/counsel/narrative",
                    json={"kind": "support_letter"}, headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["label"] == "DRAFT — for review by a licensed immigration attorney"
    assert out["draft_text"].startswith(out["label"])
    assert disclaimer("en") in out["draft_text"]
    assert out["engine"] == "local_template"           # hermetic: no live key
    # Provided facts are present and traceable...
    assert "132000" in out["draft_text"]
    assert "Software Engineer" in out["draft_text"]
    assert out["grounded_facts"]["worksite_location"] == "Miami, FL"
    assert out["fact_sources"]["job_title"] == "party_answers.petitioner"
    # ...and facts NOT provided are absent, never invented: no degree was ever
    # supplied, so no institution or degree name may appear anywhere.
    assert out["missing_facts"] == ["degree_field"]
    assert "University" not in out["draft_text"]
    assert "Master" not in out["draft_text"]
    assert "degree_field" in out["draft_text"]         # the gap is NAMED instead
    assert out["attorney_disclaimer"] == disclaimer("en")


def test_narrative_is_petitioner_or_admin_only(client, db):
    case_id = _narrative_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/counsel/narrative",
                    json={"kind": "support_letter"}, headers=AUTH)
    assert r.status_code == 403, r.text
    r = client.post(f"/h1b/cases/{case_id}/counsel/narrative",
                    json={"kind": "support_letter"}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text


def test_narrative_unknown_kind_rejected(client, db):
    case_id = _narrative_case(client, db)
    r = client.post(f"/h1b/cases/{case_id}/counsel/narrative",
                    json={"kind": "eligibility_opinion"}, headers=PETITIONER_AUTH)
    assert r.status_code == 422, r.text


class _FakeProvider:
    def __init__(self, draft, name="kimi-k3-fake"):
        self.name = name
        self._draft = draft

    def _chat(self, system, user, **kwargs):
        return {"draft_text": self._draft}


def test_live_draft_with_invented_numbers_is_dropped(client, db, monkeypatch):
    """The deterministic grounding guard: a live draft containing a number that
    is not among the provided facts is rejected and the deterministic local
    template ships instead."""
    case_id = _narrative_case(client, db)
    parent = db.get(models.VisaApplication, case_id)
    monkeypatch.setattr(kimi, "get_provider",
                        lambda: _FakeProvider("We proudly offer a wage of "
                                              "999777 USD per year."))
    out = counsel.draft_narrative(db, None, parent)
    assert out["engine"] == "local_template"
    assert "999777" not in out["draft_text"]
    assert out["draft_text"].startswith(counsel.DRAFT_LABEL["en"])


def test_live_draft_grounded_in_facts_is_used(client, db, monkeypatch):
    case_id = _narrative_case(client, db)
    parent = db.get(models.VisaApplication, case_id)
    monkeypatch.setattr(kimi, "get_provider",
                        lambda: _FakeProvider("Trip.com US Inc offers 132000 "
                                              "per year for the Software "
                                              "Engineer position."))
    out = counsel.draft_narrative(db, None, parent)
    assert out["engine"] == "kimi-k3-fake"
    assert "Trip.com US Inc offers 132000" in out["draft_text"]
    # Label and disclaimer are applied deterministically, never left to the model.
    assert out["draft_text"].startswith(counsel.DRAFT_LABEL["en"])
    assert disclaimer("en") in out["draft_text"]


# ---------------------------------------------------------------------------
# evidence index + cover
# ---------------------------------------------------------------------------

def _evidence_case(client, db):
    out = _create_case(client)          # extension, in-US, prior H-1B holder
    case_id = out["case_id"]
    _bind_petitioner(db, case_id)
    # Submit the passport and ONE degree certificate; everything else stays
    # honestly missing.
    up = client.post(f"/cases/{case_id}/documents", json={
        "name": "passport.pdf", "mime": "application/pdf", "size_bytes": 1024,
        "checklist_item_id": "passport", "text": PASSPORT_MRZ}, headers=AUTH)
    assert up.status_code == 200, up.text
    s = client.post(f"/cases/{case_id}/checklist/passport/submit",
                    json={"document_id": up.json()["id"], "confirm": True},
                    headers=AUTH)
    assert s.status_code == 200, s.text
    _submit_item(client, case_id, "degree_certificate", DEGREE_TEXT)
    return case_id


def test_evidence_index_order_missing_honesty_and_acceptance(client, db):
    case_id = _evidence_case(client, db)

    r = client.get(f"/h1b/cases/{case_id}/counsel/evidence-index",
                   headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    exhibits = body["exhibits"]
    # Checklist order, sequential numbering.
    assert [e["item_id"] for e in exhibits] == [
        "passport", "degree_certificate", "graduation_certificate",
        "transcript", "credential_evaluation", "resume", "prior_i797",
        "i94_record", "support_letter", "job_description", "fein_evidence",
        "employer_financials", "corporate_relationship"]
    assert [e["exhibit_no"] for e in exhibits] == list(range(1, 14))
    by_item = {e["item_id"]: e for e in exhibits}
    # The explicit Submit records the applicant's approval into the
    # signed-material state (checklist_intake doctrine), so a submitted
    # document reads 'accepted'.
    assert by_item["passport"]["status"] == "accepted"
    assert by_item["degree_certificate"]["status"] == "accepted"
    assert by_item["degree_certificate"]["document_id"]
    # Required-but-missing items are INCLUDED, honestly, without a document.
    assert by_item["graduation_certificate"]["status"] == "missing"
    assert by_item["graduation_certificate"]["document_id"] is None
    # Optional absent items (current visa) and not-yet-required certified LCA
    # are not exhibits and not fake gaps.
    assert "current_visa" not in by_item and "certified_lca" not in by_item
    assert body["counts"]["missing"] == 11
    # Party tags ride through.
    assert by_item["support_letter"]["party"] == "petitioner"
    assert by_item["passport"]["party"] == "beneficiary"

    # A withdrawn approval downgrades honestly to 'submitted' — the binding
    # still exists but the document is no longer in the approved material state.
    doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case_id,
        models.StoredDocument.doc_type == "degree_certificate")).scalars().first()
    doc.approved = False
    db.commit()
    body = client.get(f"/h1b/cases/{case_id}/counsel/evidence-index",
                      headers=PETITIONER_AUTH).json()
    by_item = {e["item_id"]: e for e in body["exhibits"]}
    assert by_item["degree_certificate"]["status"] == "submitted"
    assert body["attorney_disclaimer"] == disclaimer("en")


def test_evidence_index_is_petitioner_or_admin_only(client, db):
    case_id = _evidence_case(client, db)
    r = client.get(f"/h1b/cases/{case_id}/counsel/evidence-index", headers=AUTH)
    assert r.status_code == 403, r.text
    r = client.get(f"/h1b/cases/{case_id}/counsel/evidence-index",
                   headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text


def test_evidence_cover_pdf_stored_and_signed_url(client, db):
    case_id = _evidence_case(client, db)

    # The beneficiary cannot compile the petitioner's filing pack.
    r = client.post(f"/h1b/cases/{case_id}/counsel/evidence-cover", headers=AUTH)
    assert r.status_code == 403, r.text

    r = client.post(f"/h1b/cases/{case_id}/counsel/evidence-cover",
                    headers=PETITIONER_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime"] == "application/pdf" and body["document_id"]
    assert body["attorney_disclaimer"] == disclaimer("en")
    # No filesystem paths or credentials in the URL (preview-pattern contract).
    url = body["url"]
    assert "file://" not in url and "/Users/" not in url

    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content.startswith(b"%PDF")
    # The cover carries the caption, the exhibits, the HONEST missing section,
    # and the attorney disclaimer.
    assert b"EVIDENCE INDEX" in resp.content
    assert b"Exhibit 1" in resp.content
    assert b"REQUIRED ITEMS NOT YET ON FILE" in resp.content
    assert b"MISSING" in resp.content
    assert b"not a law firm" in resp.content
    assert b"WEI ZHANG" in resp.content

    # The artifact is honestly classed as a locally generated document.
    doc = db.get(models.StoredDocument, body["document_id"])
    assert doc.execution_class == "LOCAL_PROVIDER"
    assert doc.doc_type == "h1b_evidence_cover"

    # Tampered signature is refused.
    tampered = url.rsplit("sig=", 1)[0] + "sig=" + "0" * 32
    assert client.get(tampered).status_code == 401


def test_counsel_endpoints_404_on_non_h1b_case(client):
    cid = client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com",
        "destination_country": "Mockland"}).json()["id"]
    assert client.get(f"/h1b/cases/{cid}/counsel/rfe-risks",
                      headers=AUTH).status_code == 404
    assert client.post(f"/h1b/cases/{cid}/counsel/narrative",
                       json={"kind": "support_letter"},
                       headers=ADMIN_AUTH).status_code == 404
