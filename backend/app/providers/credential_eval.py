"""Credential evaluation (WES / ECE) — a REFERRAL and tracking seam.

WHY A REFERRAL AND NOT AN INTEGRATION
-------------------------------------
The H-1B checklist requires a third-party evaluation of a foreign degree
(`checklist.credential_evaluation`). Neither WES nor ECE publishes an ordering
API: an applicant creates their own account, pays their own fee, and arranges
for their institution's verified documents to reach the evaluator. There is no
sanctioned surface for Ellis to place that order, and inventing one would be
inventing a fact about someone's degree.

So this module does the honest thing: it tells the applicant WHERE to order and
what it will take, and it TRACKS what Ellis actually observed. Nothing here
ever claims a report exists. `record_evaluation_received` refuses to record a
receipt without an accepted uploaded document — the report is real when the
document is in the case, and not one moment earlier.

CHINESE CREDENTIALS
-------------------
A Chinese degree reaches an evaluator with a verification report, not just the
certificates. The two certificates Ellis already collects map onto two
verification tracks:

  * 毕业证 graduation certificate and transcripts — verified through CHSI
    (chsi.com.cn), the Ministry of Education's student-information portal
    operated by CHESICC.
  * 学位证 degree certificate — historically verified by CDGDC
    (cdgdc.edu.cn); degree verification was reported consolidated under CSSD,
    through the same CHSI portal, from 15 August 2022.

That last point is the applicant's timing risk: the verification report can
take longer than the evaluation itself, and it is ordered first. Both the
evaluator's document requirements and the verification route are the PARTNER's
and the Chinese ministry's rules, not rules Ellis curates from a government
source it controls — so the note says where to confirm them, and every
turnaround figure carries the date it was read.
"""
from __future__ import annotations

import datetime as _dt

from ..config import settings

SOURCE = "credential_eval"

# The date the partner-published figures below were last read. Turnaround and
# document requirements are the partner's own, and they change; a stale number
# presented as current is the failure mode this date exists to prevent.
FACTS_AS_OF = "2026-08-11"

_TURNAROUND_CAVEAT = (
    "Turnaround is the partner's own published figure as of "
    f"{FACTS_AS_OF} and starts only once the partner has received and accepted "
    "every required document. Confirm on the partner's site before relying on "
    "a date.")

CHINA_VERIFICATION_NOTE = (
    "Chinese credentials need a verification report before an evaluation can "
    "be completed. Transcripts and the graduation certificate (毕业证) are "
    "verified through CHSI (https://www.chsi.com.cn), the Ministry of "
    "Education portal operated by CHESICC; the degree certificate (学位证) was "
    "verified by CDGDC (https://www.cdgdc.edu.cn) until degree verification "
    "was consolidated under CSSD through the same CHSI portal from 15 August "
    "2022. Order the verification report FIRST — it is usually the long pole — "
    "and confirm the exact report your evaluator requires with the evaluator, "
    "since it is their requirement, not a filing rule.")

PARTNERS: dict[str, dict] = {
    "wes": {
        "name": "World Education Services (WES)",
        "order_url": "https://www.wes.org/",
        "typical_turnaround": ("about 7 business days after WES receives and "
                               "approves all required documents"),
        "naces_member": True,
    },
    "ece": {
        "name": "Educational Credential Evaluators (ECE)",
        "order_url": "https://www.ece.org/",
        "typical_turnaround": ("about 5 business days after ECE receives all "
                               "required documents"),
        "naces_member": True,
    },
}

# Statuses this module records. There is deliberately no 'in_progress' fetched
# from a partner: Ellis cannot see inside a partner's queue and never pretends
# to. 'ordered' is what the applicant told Ellis; 'received' is what Ellis has.
STATUSES = ("ordered", "received")


class UnknownEvaluationPartner(ValueError):
    """The partner is not one Ellis has a referral for. Raised rather than
    guessed: a made-up order URL sends an applicant's fee to nowhere."""


class EvaluationNotOnFile(ValueError):
    """Raised when a caller tries to record a report Ellis does not hold. A
    report exists when its document is uploaded and accepted, never before."""


def _normalize(partner) -> str:
    return str(partner or "").strip().lower()


def configured_partner() -> str:
    """The partner this deployment refers to ('' when none is configured)."""
    p = _normalize(settings().credential_eval_partner)
    return p if p in PARTNERS else ""


def is_configured() -> bool:
    """True only when this deployment names a partner Ellis has a referral for.
    Unconfigured, `referral` reports unavailable and the checklist item stays
    exactly what it already is: a document the applicant must supply."""
    return bool(configured_partner())


def partner_info(partner: str) -> dict:
    """Everything Ellis can honestly say about one evaluation partner.

    Returns {partner, name, order_url, typical_turnaround, verification_note,
    as_of, caveat}. `verification_note` is the CHSI/CDGDC guidance for Chinese
    credentials. Raises UnknownEvaluationPartner for anything but wes|ece."""
    key = _normalize(partner)
    info = PARTNERS.get(key)
    if not info:
        raise UnknownEvaluationPartner(
            f"unknown credential evaluation partner: expected one of "
            f"{'|'.join(sorted(PARTNERS))}")
    return {
        "partner": key,
        "name": info["name"],
        "order_url": info["order_url"],
        "typical_turnaround": info["typical_turnaround"],
        "verification_note": CHINA_VERIFICATION_NOTE,
        "naces_member": info["naces_member"],
        "as_of": FACTS_AS_OF,
        "caveat": _TURNAROUND_CAVEAT,
        "source": SOURCE,
        # The applicant orders and pays. Ellis never places the order: paying a
        # fee and signing a partner's terms are personal acts.
        "ordered_by": "applicant",
    }


def referral() -> dict:
    """The configured partner's referral, or an honest unavailable result.

    Returns {available, ...partner_info} — available False (with a reason) when
    no partner is configured, so a caller never renders a blank order link."""
    key = configured_partner()
    if not key:
        return {"available": False, "source": SOURCE,
                "reason": "no credential evaluation partner is configured",
                "verification_note": CHINA_VERIFICATION_NOTE}
    return {"available": True, **partner_info(key)}


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def record_evaluation_ordered(*, partner: str, ordered_at: str = "",
                              reference: str = "") -> dict:
    """Record that the APPLICANT says they ordered an evaluation.

    This is a tracking note, not a fact about the evaluation: `report_on_file`
    stays False and no checklist item is satisfied by it. Its value is the
    timeline — an evaluation ordered late is the single most common reason a
    petition waits.

    Raises UnknownEvaluationPartner for anything but wes|ece."""
    info = partner_info(partner)
    return {
        "status": "ordered",
        "partner": info["partner"],
        "partner_name": info["name"],
        "ordered_at": str(ordered_at or "").strip() or _now_iso(),
        "reference": str(reference or "").strip(),
        # Nothing exists yet. Said out loud so no caller infers otherwise.
        "report_on_file": False,
        "satisfies_checklist_item": False,
        "typical_turnaround": info["typical_turnaround"],
        "as_of": info["as_of"],
        "caveat": info["caveat"],
        "source": SOURCE,
        "note": ("applicant-reported order; the checklist item is satisfied "
                 "only when the report itself is uploaded and accepted"),
    }


def record_evaluation_received(*, partner: str, document_id: str,
                               accepted: bool = True,
                               received_at: str = "") -> dict:
    """Record that the evaluation REPORT is in the case.

    Args:
      document_id: the accepted upload holding the report. Required.
      accepted:    whether document intake accepted it. A rejected or
                   still-processing upload is not a report.

    Raises EvaluationNotOnFile when there is no accepted document — the whole
    point of this function is that it cannot be used to claim a report exists.
    Raises UnknownEvaluationPartner for anything but wes|ece."""
    info = partner_info(partner)
    doc_id = str(document_id or "").strip()
    if not doc_id:
        raise EvaluationNotOnFile(
            "an evaluation report is recorded only from an uploaded document")
    if not accepted:
        raise EvaluationNotOnFile(
            "the uploaded evaluation report was not accepted by intake")
    return {
        "status": "received",
        "partner": info["partner"],
        "partner_name": info["name"],
        "document_id": doc_id,
        "received_at": str(received_at or "").strip() or _now_iso(),
        "report_on_file": True,
        # The document exists; whether it says what the petition needs is a
        # review question, not something this seam decides.
        "satisfies_checklist_item": True,
        "source": SOURCE,
        "note": ("report document is on file; its contents still require "
                 "human review against the position's degree requirement"),
    }
