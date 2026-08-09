"""Party-tagged H1B document checklist. Deterministic (renewal.py precedent:
no Kimi for statutory routes). Item schema matches derive_document_checklist's
{id, label, kind, required, satisfied_by} with an extra 'party' key that rides
verbatim through CaseRouteGuidance.checklist and apply_checklist_status.

Item ids are stable — ChecklistSubmission is UNIQUE(application_id, item_id),
so renaming an id orphans submissions silently. Never rename; add.

China tailoring: Chinese degrees come as TWO documents (学位证 degree
certificate and 毕业证 graduation certificate) plus a third-party credential
evaluation for the I-129. Labels carry both languages.
"""
from __future__ import annotations


def derive_h1b_checklist(*, case_kind: str, beneficiary_abroad: bool,
                         beneficiary_in_us: bool = False,
                         first_h1b: bool = True,
                         lca_certified: bool = False) -> list[dict]:
    items: list[dict] = [
        # ---- beneficiary ----
        {"id": "passport", "label": "Passport biodata page 护照资料页",
         "kind": "document", "required": True, "satisfied_by": ["passport"],
         "party": "beneficiary"},
        {"id": "degree_certificate",
         "label": "Degree certificate 学位证",
         "kind": "document", "required": True,
         "satisfied_by": ["degree_certificate"], "party": "beneficiary"},
        {"id": "graduation_certificate",
         "label": "Graduation certificate 毕业证",
         "kind": "document", "required": True,
         "satisfied_by": ["graduation_certificate"], "party": "beneficiary"},
        {"id": "transcript", "label": "Academic transcripts 成绩单",
         "kind": "document", "required": True, "satisfied_by": ["transcript"],
         "party": "beneficiary"},
        {"id": "credential_evaluation",
         "label": "Credential evaluation of the Chinese degree (third-party report)",
         "kind": "document", "required": True,
         "satisfied_by": ["credential_evaluation"], "party": "beneficiary"},
        {"id": "resume", "label": "Resume / CV 简历", "kind": "document",
         "required": True, "satisfied_by": ["resume_cv"], "party": "beneficiary"},
        {"id": "prior_i797",
         "label": "Prior I-797 approval notices (if any previous H-1B)",
         "kind": "document", "required": not first_h1b,
         "satisfied_by": ["prior_i797"], "party": "beneficiary"},
        {"id": "current_visa", "label": "Current US visa (if any)",
         "kind": "document", "required": False, "satisfied_by": ["prior_visa"],
         "party": "beneficiary"},
        # ---- petitioner ----
        {"id": "support_letter", "label": "Employer support letter",
         "kind": "document", "required": True,
         "satisfied_by": ["employer_support_letter"], "party": "petitioner"},
        {"id": "job_description", "label": "Detailed job description",
         "kind": "document", "required": True,
         "satisfied_by": ["job_description"], "party": "petitioner"},
        {"id": "fein_evidence", "label": "FEIN evidence (IRS CP-575 or equivalent)",
         "kind": "document", "required": True, "satisfied_by": ["fein_evidence"],
         "party": "petitioner"},
        {"id": "employer_financials",
         "label": "Employer financial documents (ability to pay)",
         "kind": "document", "required": True,
         "satisfied_by": ["employer_financials", "bank_statement"],
         "party": "petitioner"},
        {"id": "corporate_relationship",
         "label": "Corporate relationship evidence (US entity and parent company)",
         "kind": "document", "required": True,
         "satisfied_by": ["corporate_relationship_evidence"],
         "party": "petitioner"},
        # Flips to required when the LCA step verifies; the deriver re-runs via
        # current_checklist healing, so the flip is durable and automatic.
        {"id": "certified_lca", "label": "Certified LCA (ETA-9035, from DOL FLAG)",
         "kind": "document", "required": bool(lca_certified),
         "satisfied_by": ["certified_lca"], "party": "petitioner"},
    ]
    if beneficiary_in_us:
        items.insert(8, {"id": "i94_record", "label": "Most recent I-94 record",
                         "kind": "document", "required": True,
                         "satisfied_by": ["i94_record"], "party": "beneficiary"})
    return items


def items_for_party(items: list[dict], party: str) -> list[dict]:
    return [i for i in items if i.get("party", "beneficiary") == party]
