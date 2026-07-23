"""Grounded Kimi K3 extraction for on-demand route research (brief §9).

Kimi receives ONLY the text of officially-fetched pages (each tagged with a
source id) and must return structured requirements where EVERY field cites the
source ids it came from. A deterministic grounding validator then:
  * drops any field citing an unknown source id (hallucination -> rejected),
  * drops any substantive field with no citation at all,
  * records what was rejected (auditable).

Kimi can therefore never invent a requirement/fee/portal/jurisdiction into the
record: uncited output is discarded before anything is stored. Kimi is also
never the sole evidence — the fetched pages themselves are stored as
SourceEvidence, and 'verified' status still requires government-domain sources.
Injectable for tests via set_extractor(). The Moonshot key stays backend-only.
"""
from __future__ import annotations

import json

EXTRACTION_VERSION = "ex1"

# Fields Kimi may extract; each must be {"value": ..., "sources": ["s1", ...]}.
EXTRACTABLE_FIELDS = (
    "visa_requirement",          # one of the dispositions, for THIS nationality
    "maximum_stay", "permitted_entries", "exemption_conditions",
    "required_documents", "conditional_documents",
    "passport_validity_rule", "blank_page_rule",
    "biometrics_required", "interview_required", "personal_appearance_required",
    "appointment_required", "application_channel",
    "competent_embassy_or_consulate", "consular_jurisdiction",
    "official_application_portal", "authorized_visa_center",
    "government_fee_amount", "government_fee_currency", "other_fees",
    "refundability", "accepted_payment_methods", "processing_time_guidance",
    "third_party_preparation_allowed", "third_party_submission_allowed",
    "representative_conditions", "applicant_declaration_required",
    "transit_conditions", "effective_from", "effective_until",
)

_SYSTEM = """You extract tourist-visa requirements for ONE exact route from
OFFICIAL source pages provided below. Absolute rules:
- Use ONLY the provided page texts. Never use outside knowledge.
- Every field MUST cite the source ids (e.g. ["s1","s3"]) whose text supports it.
- If the pages do not state a field, OMIT it entirely. NEVER guess.
- Missing information is NEVER visa-free access.
- If two pages disagree on a field, include BOTH values in a "conflicts" list:
  [{"field":..., "values":[{"value":..., "sources":[...]}, ...]}].
- Translate non-English content faithfully; quote amounts/currencies exactly.
Reply JSON: {"fields": {<name>: {"value": <v>, "sources": ["s1"...]}, ...},
"conflicts": [...], "summary": "<=400 chars"} using ONLY these field names: """ + ", ".join(EXTRACTABLE_FIELDS)


_EXTRACTOR = None


def set_extractor(fn) -> None:
    """Inject callable(system, user) -> dict (tests). None resets to live Kimi."""
    global _EXTRACTOR
    _EXTRACTOR = fn


class KimiUnavailable(Exception):
    pass


def _live_extract(system: str, user: str) -> dict:
    from ..config import settings
    from ..providers.kimi import LiveKimiProvider
    s = settings()
    if not (s.moonshot_api_key and s.kimi_enabled):
        raise KimiUnavailable("Kimi K3 not configured — extraction unavailable")
    return LiveKimiProvider()._chat(system, user, json_mode=True)


def extract(route: dict, pages: list[dict], *, max_retries: int = 2) -> dict:
    """pages: [{id, url, hostname, text}]. Returns:
    {ok, fields:{name:{value,sources:[urls]}}, conflicts:[...], rejected:[...],
     summary, model} — with every surviving field grounded in a fetched page."""
    known_ids = {p["id"] for p in pages}
    id_to_url = {p["id"]: p["url"] for p in pages}
    corpus = "\n\n".join(
        f"[{p['id']}] {p['url']} (host {p['hostname']}):\n{p['text'][:8000]}" for p in pages)
    user = json.dumps({"route": route}) + "\n\nSOURCE PAGES:\n" + corpus

    last_err = None
    for _ in range(max_retries + 1):
        try:
            raw = (_EXTRACTOR or _live_extract)(_SYSTEM, user)
            break
        except KimiUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            raw = None
    if raw is None:
        raise KimiUnavailable(f"Kimi extraction failed after retries: {last_err}")

    fields_in = raw.get("fields") or {}
    fields_out: dict = {}
    rejected: list[dict] = []
    for name, payload in fields_in.items():
        if name not in EXTRACTABLE_FIELDS:
            rejected.append({"field": name, "reason": "unknown_field"})
            continue
        if not isinstance(payload, dict) or "value" not in payload:
            rejected.append({"field": name, "reason": "malformed"})
            continue
        cites = [c for c in (payload.get("sources") or []) if c in known_ids]
        if not cites:
            # HALLUCINATION REJECTION: no valid citation -> the field is dropped.
            rejected.append({"field": name, "reason": "ungrounded_no_valid_citation",
                             "claimed_sources": payload.get("sources")})
            continue
        fields_out[name] = {"value": payload["value"],
                            "sources": [id_to_url[c] for c in cites]}

    conflicts_out = []
    for c in raw.get("conflicts") or []:
        vals = []
        for v in c.get("values") or []:
            cites = [x for x in (v.get("sources") or []) if x in known_ids]
            if cites:
                vals.append({"value": v.get("value"), "sources": [id_to_url[x] for x in cites]})
        if len(vals) >= 2:
            conflicts_out.append({"field": c.get("field"), "values": vals})

    from ..config import settings
    return {"ok": True, "fields": fields_out, "conflicts": conflicts_out,
            "rejected": rejected, "summary": str(raw.get("summary", ""))[:400],
            "model": settings().kimi_model, "extraction_version": EXTRACTION_VERSION}
