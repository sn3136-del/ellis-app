"""Passport renewal: a real, linked renewal case for an expired or
insufficient-validity passport.

Triggering (evaluated deterministically from the passport-validity verdict):
 - the passport is expired,
 - remaining validity is insufficient for the selected trip,
 - the passport lacks the required post-entry validity, or
 - the applicant manually chooses "Renew my passport".

Kimi determines — for the passport ISSUING country and the applicant's
circumstances — the renewal eligibility, the online/mail/in-person path, the
required form and documents, photo requirements, old-passport surrender,
name-change evidence, fees, normal and expedited processing times, appointment
requirement, and the submission/delivery method. The answer is bounded by the
same hard deadline as route guidance, validated deterministically, and NEVER
invents a portal URL. The deterministic RENEWAL_AUTHORITIES map in
passport_validity.py remains the source for the official authority link.

The renewal case is a real VisaApplication (visa_type="passport_renewal")
linked bidirectionally to the travel case, reusing the same OCR/document
pipeline, checklist, confirmations, and (for online renewal portals) the same
adapter + Browserbase machinery — with every irreversible step still requiring
the applicant's explicit confirmation. When the renewed passport is uploaded
and approved, its OCR result updates the travel case's passport fields, the
destination validity re-evaluates, and the travel case resumes.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import models
from .passport_validity import RENEWAL_AUTHORITIES, parse_expiry, renewal_instructions


def passport_validity_parse(value) -> str | None:
    """Normalize an extracted date (MRZ YYMMDD or ISO) to ISO, or None."""
    d = parse_expiry(str(value or ""))
    return d.isoformat() if d else None

CONTINUATION_KIND = "passport_renewal"

MANDATORY_FIELDS = ("eligible_for_renewal", "channel", "required_documents",
                    "government_fee", "processing_time_normal")

ALL_FIELDS = MANDATORY_FIELDS + (
    "renewal_form", "photo_requirements", "old_passport_surrender",
    "name_change_evidence", "processing_time_expedited", "appointment_required",
    "submission_method", "delivery_method", "official_portal_url",
    "in_person_locations", "uncertainty", "confidence",
)

_SYSTEM = """You are a passport-renewal requirements engine. For the passport
ISSUING COUNTRY and applicant circumstances in the user message, answer from
your knowledge of that country's official passport-renewal rules. Reply STRICT
JSON with these fields (null + an "uncertainty" entry when genuinely unknown):
eligible_for_renewal: true|false|null (false only when a fact given makes a
  standard renewal impossible, e.g. must apply as first-time applicant)
channel: online | mail | in_person | online_or_mail | varies
renewal_form: the official form name/number or null (NEVER invent one)
required_documents: array of short strings
photo_requirements: short string or null
old_passport_surrender: short string describing whether/how the existing
  passport must be submitted, or null
name_change_evidence: short string (when a name changed) or null
government_fee: {"amount": number|null, "currency": string|null}
processing_time_normal, processing_time_expedited: short strings or null
appointment_required: true|false|null
submission_method, delivery_method: short strings or null
official_portal_url: the official government renewal portal URL or null (NEVER invent)
in_person_locations: array of short strings naming REAL office types (e.g.
  "acceptance facility", "passport agency") — never specific fictional centers
uncertainty: array of {"field":..., "reason":...}
confidence: high | medium | low
Rules: never guess a fee or URL; unknown means null + uncertainty entry; keep
every string short — no prose."""


class RenewalUnavailable(Exception):
    """Kimi not configured — honest, never fabricated."""


def _facts(travel_case: models.VisaApplication, verdict: dict) -> dict:
    """Sanitized renewal facts — no name, no passport number, no images."""
    answers = travel_case.answers or {}
    return {
        "passport_issuing_country": str(answers.get("issuing_country")
                                        or answers.get("passport_issuing_country")
                                        or answers.get("nationality") or "").upper(),
        "applicant_country_of_residence": str(answers.get("current_residence")
                                              or answers.get("lawful_country_of_residence") or "").upper(),
        "applicant_age": answers.get("age"),
        "passport_status": verdict.get("status"),
        "passport_expiry_date": verdict.get("expiry_date"),
        "purpose": "adult tourist passport renewal",
    }


def cache_key(facts: dict) -> str:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return "|".join(("RENEWAL", facts.get("passport_issuing_country", ""),
                     facts.get("applicant_country_of_residence", ""), month, "v1"))


def validate_answer(raw: dict) -> tuple[dict, list]:
    clean = {k: raw[k] for k in ALL_FIELDS if k in (raw or {})}
    missing = [k for k in MANDATORY_FIELDS if clean.get(k) in (None, "", [], {})
               and clean.get(k) is not False]
    if not isinstance(clean.get("government_fee"), dict):
        clean.pop("government_fee", None)
        if "government_fee" not in missing:
            missing.append("government_fee")
    return clean, missing


def get_renewal_guidance(db, travel_case: models.VisaApplication, verdict: dict) -> dict:
    """One bounded, validated Kimi call (with a single malformed-retry) under
    the same hard deadline discipline as route guidance; cached per issuing
    country × residence × month."""
    from .visa_snapshot import kimi_primary
    from .visa_snapshot.models import KimiRouteGuidanceCache

    facts = _facts(travel_case, verdict)
    issuing = facts["passport_issuing_country"]
    key = cache_key(facts)
    row = db.execute(select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()
    if row is not None:
        return {"status": row.status, "guidance": row.guidance, "cached": True,
                "missing_fields": row.missing_fields,
                "authority": renewal_instructions(issuing)}

    if not kimi_primary.is_available():
        raise RenewalUnavailable("Kimi K3 not configured — renewal analysis unavailable")

    started = time.monotonic()
    deadline = started + kimi_primary._deadline_seconds()

    def budget() -> float:
        r = deadline - time.monotonic()
        if r < kimi_primary.MIN_CALL_BUDGET_SECONDS:
            raise kimi_primary.GuidanceTimeout()
        return r

    user = json.dumps(facts)
    try:
        raw = kimi_primary._call(_SYSTEM, user, timeout=budget(),
                                 max_tokens=kimi_primary.PASS1_MAX_TOKENS)
    except (kimi_primary.GuidanceUnavailable, kimi_primary.GuidanceTimeout,
            kimi_primary.GuidanceProviderError):
        raise
    except Exception:  # noqa: BLE001 - one retry below
        raw = None
    clean, missing = validate_answer(raw or {})
    if missing:
        try:
            raw2 = kimi_primary._call(
                _SYSTEM, user + f"\n\nYour previous answer was incomplete. Missing: "
                f"{', '.join(missing)}. Reply the FULL corrected JSON.",
                timeout=budget(), max_tokens=kimi_primary.PASS1_MAX_TOKENS)
            clean2, missing2 = validate_answer(raw2 or {})
            if len(missing2) < len(missing):
                clean, missing = clean2, missing2
        except (kimi_primary.GuidanceUnavailable, kimi_primary.GuidanceTimeout,
                kimi_primary.GuidanceProviderError):
            raise
        except Exception:  # noqa: BLE001
            pass

    status = "KIMI_PRIMARY" if not missing else "KIMI_UNCERTAIN"
    if status == "KIMI_PRIMARY":
        ttl = int(os.getenv("ELLIS_KIMI_GUIDANCE_TTL_DAYS", 14) or 14)
        row = KimiRouteGuidanceCache(
            cache_key=key, route=facts, status=status, guidance=clean,
            missing_fields=missing, contradictions=[],
            model=os.getenv("KIMI_GUIDANCE_MODEL", "").strip() or "kimi",
            verification={}, fresh_until=datetime.now(timezone.utc) + timedelta(days=ttl))
        db.add(row)
        db.commit()
    return {"status": status, "guidance": clean, "cached": False,
            "missing_fields": missing, "authority": renewal_instructions(issuing)}


def derive_renewal_checklist(guidance: dict) -> list[dict]:
    """Deterministic renewal checklist from the validated guidance fields —
    reuses the route checklist deriver so item semantics (document/form/check)
    and satisfaction matching stay identical across case kinds."""
    from .visa_snapshot import intake_flow
    g = guidance or {}
    pseudo = {
        "disposition": "VISA_REQUIRED",   # forms are required, not optional
        "required_documents": g.get("required_documents") or [],
        "photo_requirements": g.get("photo_requirements"),
        "forms": [g.get("renewal_form")] if g.get("renewal_form") else [],
    }
    items = intake_flow.derive_document_checklist(pseudo)
    if g.get("old_passport_surrender"):
        items.append({"id": "old_passport_surrender",
                      "label": f"Existing passport — {g['old_passport_surrender']}",
                      "kind": "check", "required": True, "satisfied_by": []})
    if g.get("name_change_evidence"):
        items.append({"id": "name_change_evidence",
                      "label": f"Name-change evidence — {g['name_change_evidence']}",
                      "kind": "document", "required": False,
                      "satisfied_by": ["document"]})
    return items


def should_offer_renewal(verdict: dict) -> bool:
    """Deterministic trigger: only expired / insufficient-validity passports
    (or an explicit applicant request, handled by the endpoint) offer renewal —
    a valid passport never shows it."""
    return (verdict or {}).get("status") in ("expired", "insufficient_validity")


def get_linked_renewal(db, travel_case: models.VisaApplication):
    rid = (travel_case.answers or {}).get("renewal_case_id")
    return db.get(models.VisaApplication, rid) if rid else None


def create_renewal_case(db, *, org_id: str, user_id: str,
                        travel_case: models.VisaApplication, verdict: dict,
                        manual: bool = False) -> dict:
    """Create (or reuse — idempotent) the linked passport-renewal case."""
    from .visa_snapshot.models import CaseRouteGuidance
    from . import audit

    existing = get_linked_renewal(db, travel_case)
    if existing is not None:
        cg = db.execute(select(CaseRouteGuidance).where(
            CaseRouteGuidance.case_id == existing.id)).scalars().first()
        return {"renewal_case_id": existing.id, "already_exists": True,
                "state": existing.state,
                "guidance": (cg.guidance if cg else None)}

    if not manual and not should_offer_renewal(verdict):
        raise ValueError("passport does not need renewal for this trip")

    guidance_result = get_renewal_guidance(db, travel_case, verdict)
    answers = dict(travel_case.answers or {})
    issuing = str(answers.get("issuing_country") or answers.get("passport_issuing_country")
                  or answers.get("nationality") or "").upper()
    authority = RENEWAL_AUTHORITIES.get(issuing, {})

    renewal_answers = {
        "linked_travel_case_id": travel_case.id,
        "issuing_country": issuing,
        "nationality": answers.get("nationality") or answers.get("passport_nationality"),
        "full_name": answers.get("full_name"),
        "email": answers.get("email"),
        "birth_date": answers.get("birth_date"),
        "current_passport_number": answers.get("passport_number"),
        "current_passport_expiry": (verdict or {}).get("expiry_date")
                                   or answers.get("expiry_date"),
        "renewal_reason": (verdict or {}).get("status") or "applicant_requested",
    }
    renewal_case = models.VisaApplication(
        org_id=org_id, user_id=user_id, applicant_id=travel_case.applicant_id,
        destination_country=authority.get("authority", issuing or "passport authority"),
        visa_type="passport_renewal",
        answers={k: v for k, v in renewal_answers.items() if v is not None})
    db.add(renewal_case)
    db.flush()

    # Reuse the travel case's accepted passport document (same OCR provenance,
    # same bytes — deduped by sha256, never a second upload).
    src_docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == travel_case.id,
        models.StoredDocument.doc_type == "passport")).scalars().all()
    carried = 0
    for d in src_docs:
        if not (d.page_classification or {}).get("accepted_as_passport_identity", True):
            continue
        copy = models.StoredDocument(
            org_id=org_id, application_id=renewal_case.id, name=d.name,
            mime=d.mime, size_bytes=d.size_bytes, sha256=d.sha256,
            storage_ref=d.storage_ref, doc_type="passport", ocr_status="done",
            execution_class=d.execution_class,
            page_classification=d.page_classification,
            extracted_fields=d.extracted_fields,
            passport_profile=d.passport_profile,
            quality_warnings=d.quality_warnings, approved=d.approved)
        db.add(copy)
        db.flush()
        blob = db.get(models.DocumentBlob, d.id)
        if blob is not None:
            db.add(models.DocumentBlob(document_id=copy.id, org_id=org_id,
                                       mime=blob.mime, content=blob.content))
        carried += 1
        break

    cg = CaseRouteGuidance(
        org_id=org_id, case_id=renewal_case.id, intake_id=None,
        route_key=f"renewal:{issuing}",
        disposition="PASSPORT_RENEWAL",
        continuation_kind=CONTINUATION_KIND,
        guidance=guidance_result,
        checklist=derive_renewal_checklist(guidance_result.get("guidance") or {}))
    db.add(cg)

    travel_answers = dict(travel_case.answers or {})
    travel_answers["renewal_case_id"] = renewal_case.id
    travel_case.answers = travel_answers
    db.commit()

    audit.record(db, org_id=org_id, application_id=renewal_case.id,
                 action="renewal_case_created",
                 detail={"travel_case_id": travel_case.id, "issuing_country": issuing,
                         "reason": renewal_answers["renewal_reason"],
                         "documents_carried": carried}, actor=user_id)
    audit.record(db, org_id=org_id, application_id=travel_case.id,
                 action="renewal_case_linked",
                 detail={"renewal_case_id": renewal_case.id}, actor=user_id)
    return {"renewal_case_id": renewal_case.id, "already_exists": False,
            "state": renewal_case.state, "guidance": guidance_result,
            "documents_carried": carried}


def propagate_renewed_passport(db, renewal_case: models.VisaApplication,
                               doc: models.StoredDocument, *, actor: str) -> dict | None:
    """When a NEW passport biodata page is approved inside a renewal case:
    update the linked travel case's passport fields from the OCR result,
    re-evaluate destination validity, and resume the travel case. Returns the
    travel case's fresh validity verdict, or None when nothing propagated."""
    from . import audit, passport_validity

    travel_id = (renewal_case.answers or {}).get("linked_travel_case_id")
    if not travel_id or renewal_case.visa_type != "passport_renewal":
        return None
    if doc.doc_type != "passport" or not \
            (doc.page_classification or {}).get("accepted_as_passport_identity", True):
        return None
    fields = doc.extracted_fields or {}

    def val(key):
        f = fields.get(key) or {}
        return f.get("value") if isinstance(f, dict) else None

    new_number = val("passport_number")
    new_expiry = passport_validity_parse(val("expiry_date"))
    old_number = (renewal_case.answers or {}).get("current_passport_number")
    if not new_expiry or (old_number and new_number == old_number):
        return None  # not a NEW passport — nothing to propagate

    travel_case = db.get(models.VisaApplication, travel_id)
    if travel_case is None:
        return None
    answers = dict(travel_case.answers or {})
    if new_number:
        answers["passport_number"] = new_number
    answers["expiry_date"] = new_expiry
    answers["passport_expiry_date"] = new_expiry
    issue = passport_validity_parse(val("issue_date")) or val("issue_date")
    if issue:
        answers["passport_issue_date"] = issue
    travel_case.answers = answers
    db.commit()

    verdict = passport_validity.check_case_passport(db, travel_case)
    audit.record(db, org_id=travel_case.org_id, application_id=travel_case.id,
                 action="passport_renewed_and_updated",
                 detail={"renewal_case_id": renewal_case.id,
                         "validity_status": verdict.get("status")}, actor=actor)
    return verdict
