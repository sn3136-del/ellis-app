"""Learning a portal from a real applicant's own signed-in session.

Nineteen of the world's e-visa portals — Kenya, UAE, Nigeria, Thailand, Japan,
India and more — render no application form at all until an account signs in.
Ellis's build-time recon is credential-free by design, so it can never see
those fields, and `required_fields_mapped` fails honestly every time. Those
countries are not blocked by a bug; they are blocked by the design that keeps
recon safe.

This is the narrow, consented exception. One applicant, who is going to make
that application anyway, agrees that their run may ALSO teach Ellis the shape
of the form. Ellis records the STRUCTURE it sees — field names, ids, types,
selectors — and nothing else. Every later applicant from that country then gets
the full automated flow.

What makes this safe enough to exist:

  1. IT IS NOT SILENT. Nothing is recorded without a consent the applicant gave
     for THIS purpose, in addition to the ordinary build consent. No consent,
     no artifact — enforced here, not by the caller.
  2. STRUCTURE ONLY, THROUGH THE SAME CHOKEPOINT. Observations pass through
     recon.sanitize_structure, the function that already strips values, text
     and anything applicant-shaped. A test plants a passport number and email
     in the page and proves neither survives.
  3. IT NEVER LAUNDERS ITS PROVENANCE. Artifacts are marked
     `authorized_session`, so a gate report says how the evidence was obtained
     instead of implying a credential-free observation.
  4. IT OBSERVES, IT DOES NOT ACT. There is no path here that fills, clicks or
     submits anything. The applicant drives their own application; Ellis
     watches the shape of the page.
"""
from __future__ import annotations

CONTENT_CLASS = "authorized_session"

# The applicant is told exactly this before anything is recorded. Versioned so
# a consent given under older wording is never treated as consent to new terms.
CONSENT_TEXT_VERSION = "authorized-observation-2026-08-02"
CONSENT_TEXT = (
    "This application is for a portal Ellis has not learned yet, because its "
    "form only appears after you sign in. If you agree, Ellis will record the "
    "STRUCTURE of the pages you go through — the names and types of the form "
    "fields, never what you type into them, never your documents, and never "
    "your password. That lets Ellis complete this application for future "
    "travellers. Your own application is unaffected either way, and you can "
    "decline without changing anything about your case."
)


class ObservationRefused(Exception):
    """Recording was attempted without the applicant's consent for it."""


def consent_record(build_request) -> dict:
    return ((build_request.portal_evidence or {}).get("authorized_observation")
            or {})


def has_consent(build_request) -> bool:
    """Consent for THIS purpose, under the CURRENT wording. An older version is
    not consent to new terms."""
    rec = consent_record(build_request)
    return bool(rec.get("consented")
                and rec.get("text_version") == CONSENT_TEXT_VERSION)


def record_consent(db, build_request, *, application_id: str, actor: str) -> dict:
    """The applicant agrees their signed-in run may teach Ellis this portal."""
    from datetime import datetime, timezone
    from . import audit
    rec = {"consented": True, "text_version": CONSENT_TEXT_VERSION,
           "by": actor, "application_id": application_id,
           "at": datetime.now(timezone.utc).isoformat()}
    build_request.portal_evidence = dict(build_request.portal_evidence or {},
                                         authorized_observation=rec)
    db.commit()
    audit.record(db, org_id=build_request.org_id, application_id=application_id,
                 action="authorized_observation_consented",
                 detail={"text_version": CONSENT_TEXT_VERSION,
                         "route_key": (build_request.route_key or "")[:120]},
                 actor=actor)
    return rec


def withdraw_consent(db, build_request, *, actor: str = "") -> None:
    """Consent can be taken back; nothing further is recorded after that."""
    from . import audit
    build_request.portal_evidence = dict(build_request.portal_evidence or {},
                                         authorized_observation={"consented": False})
    db.commit()
    audit.record(db, org_id=build_request.org_id,
                 application_id="", action="authorized_observation_withdrawn",
                 detail={}, actor=actor or "applicant")


def observe(db, build_request, *, recon_job_id: str, observation: dict,
            page_key: str, is_form: bool = False):
    """Record ONE page's structure from the applicant's signed-in session.

    `observation` is the raw structural read; it goes through recon's own
    sanitizer before anything is stored, so values, text and personal data are
    gone before they reach the database.
    """
    from .adapter_factory import models as fm, recon
    if not has_consent(build_request):
        raise ObservationRefused(
            "the applicant has not consented to Ellis learning this portal "
            "from their signed-in session")
    sanitized = recon.sanitize_structure(observation or {})
    art = fm.AdapterReconArtifact(
        recon_job_id=recon_job_id,
        page_key=page_key[:120],
        hostname=str((observation or {}).get("hostname") or "")[:200],
        url_pattern=str(sanitized.get("url_pattern") or "")[:500],
        structure=sanitized,
        # Provenance is recorded, never disguised: a gate report must be able
        # to say this came from a consented signed-in run.
        content_class=(recon.ENTRY_GATED_FORM_CLASS if is_form else CONTENT_CLASS))
    db.add(art)
    db.commit()
    return art


def artifacts_for(db, recon_job_id: str) -> list:
    """Every artifact this module recorded for a job."""
    from sqlalchemy import select
    from .adapter_factory import models as fm
    rows = db.execute(select(fm.AdapterReconArtifact).where(
        fm.AdapterReconArtifact.recon_job_id == recon_job_id)).scalars().all()
    return [a for a in rows if a.content_class in (
        CONTENT_CLASS, "application_form")]


def provenance_note(build_request) -> str:
    """One honest line for the gate report and the adapter's own record."""
    rec = consent_record(build_request)
    if not rec.get("consented"):
        return "credential-free public observation only"
    return (f"form structure learned from a consented signed-in applicant "
            f"session ({rec.get('text_version')}, {str(rec.get('at'))[:10]})")
