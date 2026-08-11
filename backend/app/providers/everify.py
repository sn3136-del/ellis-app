"""DHS E-Verify — a seam, deliberately not an integration.

WHAT E-VERIFY IS, AND WHY ELLIS DOES NOT RUN IT
-----------------------------------------------
E-Verify is the EMPLOYER's post-hire employment-eligibility system. An enrolled
employer creates a case from a completed Form I-9 AFTER a person is hired, and
DHS/SSA answer with an employment authorization result. It is not part of
filing an H-1B petition: nothing on the I-129, the LCA, or the H-1B filing
checklist is produced or satisfied by an E-Verify case. (E-Verify enrollment is
a precondition for the STEM OPT extension and for some state contracting rules
— still the employer's own obligation, still not a petition step.)

So Ellis does NOT run E-Verify cases. Creating a case is an act performed by
the employer, about a specific hired person, under the employer's own
memorandum of understanding with DHS, with legal duties attached to the result
(tentative nonconfirmations, notice, contest periods). Ellis performing that on
an employer's credentials would be Ellis making an employment decision on
someone's behalf. `create_case` exists only to refuse, in code, so the boundary
is visible where a future contributor would look for it.

WHAT THE SEAM IS ACTUALLY FOR
-----------------------------
An employer's E-Verify enrollment is CORROBORATING EVIDENCE of a bona fide
employer — one signal among the FEIN evidence, financials, and corporate
relationship documents the petition already collects. `corroborate_employer`
reports what Ellis actually knows about that enrollment and, critically, never
claims DHS confirmed it: an employer-declared company id is a CLAIM
(`verified: False`), because Ellis did not ask DHS and there is no sanctioned
lookup here that would let it.

Unconfigured, everything degrades honestly: `is_configured()` is False and
callers get `available: False` with a reason.
"""
from __future__ import annotations

from ..config import settings

SOURCE = "everify"

# What this seam is and is not, in one place a caller can read at runtime.
ROLE = "employer_post_hire_employment_eligibility"
PETITION_ROLE = "not_a_petition_step"

_SCOPE_NOTE = (
    "E-Verify is the employer's post-hire employment-eligibility system. Ellis "
    "does not create or manage E-Verify cases as part of an H-1B petition; an "
    "employer's enrollment is corroborating evidence of a bona fide employer, "
    "nothing more.")

# The bases on which Ellis may report anything at all about enrollment. There
# is deliberately no 'dhs_lookup': Ellis does not query DHS here.
EVIDENCE_BASES = ("employer_declared_company_id", "uploaded_evidence", "none")


class EmploymentEligibilityOutOfScope(RuntimeError):
    """Raised by `create_case`. Running an employment-eligibility case is the
    employer's own act under their own MOU with DHS, and it is not a step in
    filing a petition. This is a refusal, not a missing feature."""


def is_configured() -> bool:
    """True only when this deployment holds an E-Verify client pair. Even then
    Ellis creates no cases — configuration only enables the corroboration seam
    for a deployment that has an employer relationship to record."""
    s = settings()
    return bool(s.everify_client_id and s.everify_client_secret)


def capability() -> dict:
    """What this provider will and will not do, honestly, at runtime."""
    return {
        "source": SOURCE,
        "configured": is_configured(),
        "role": ROLE,
        "petition_role": PETITION_ROLE,
        # Stated as False forever, not "not yet".
        "supports_case_creation": False,
        "supports_dhs_enrollment_lookup": False,
        "supports_employer_corroboration": True,
        "note": _SCOPE_NOTE,
    }


def create_case(*args, **kwargs):
    """Never implemented. Creating an E-Verify case is the employer's personal
    act about a hired person, with statutory duties attached to the answer.

    Always raises EmploymentEligibilityOutOfScope."""
    raise EmploymentEligibilityOutOfScope(
        "Ellis does not create E-Verify cases: it is the employer's own "
        "post-hire act and not a step in filing a petition")


def _unavailable(reason: str) -> dict:
    return {"available": False, "enrolled": None, "verified": False,
            "basis": "none", "source": SOURCE, "role": ROLE,
            "supports_case_creation": False, "note": _SCOPE_NOTE,
            "reason": reason}


def corroborate_employer(*, everify_company_id: str = "",
                         evidence_document_id: str = "") -> dict:
    """Report what Ellis knows about an employer's E-Verify enrollment.

    Args:
      everify_company_id:   the company id the EMPLOYER stated. A claim.
      evidence_document_id: an accepted upload evidencing enrollment (an
                            E-Verify MOU or participation notice). Also a
                            claim until a human reviews it, but a documented one.

    Returns {available, enrolled, verified, basis, source, reason}:
      available False when there is nothing to report (unconfigured, or no
                evidence on file). Never a fabricated enrollment.
      enrolled  True only when SOMETHING was actually recorded; it reflects the
                employer's own assertion, never a DHS confirmation.
      verified  Always False. Ellis did not ask DHS, so it never says DHS said
                so. A human confirms enrollment against the employer's own
                documents.
      basis     one of EVIDENCE_BASES — where the claim came from.
    """
    if not is_configured():
        return _unavailable("E-Verify corroboration is not configured")

    doc_id = str(evidence_document_id or "").strip()
    company_id = str(everify_company_id or "").strip()
    if doc_id:
        basis = "uploaded_evidence"
    elif company_id:
        basis = "employer_declared_company_id"
    else:
        return _unavailable("no E-Verify enrollment evidence on file")

    return {
        "available": True,
        # An assertion by the employer, reported as such.
        "enrolled": True,
        # Ellis never asked DHS, so Ellis never says DHS agreed.
        "verified": False,
        "basis": basis,
        "company_id": company_id,
        "evidence_document_id": doc_id,
        "source": SOURCE,
        "role": ROLE,
        "supports_case_creation": False,
        "note": _SCOPE_NOTE,
        "reason": ("employer-asserted E-Verify enrollment, corroborating "
                   "evidence only — confirm against the employer's own "
                   "E-Verify memorandum of understanding"),
    }
