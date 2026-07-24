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


class _CallFailed(Exception):
    """A non-fatal model call error (e.g. provider content-filter / 400)."""
    def __init__(self, cause):
        super().__init__(str(cause))
        self.cause = cause


def _corpus(pages: list[dict]) -> str:
    return "\n\n".join(
        f"[{p['id']}] {p['url']} (host {p['hostname']}):\n{p['text'][:8000]}" for p in pages)


# Hard wall-clock cap per model sub-call. The HTTP client has its own timeout,
# but a wedged connection (observed live: a Kimi call stuck ESTABLISHED for
# 15+ minutes) must never stall research. Injected extractors (tests) are
# called directly — no thread — so test behavior is unchanged.
MODEL_CALL_HARD_TIMEOUT = 180.0


def _call_model(route: dict, pages: list[dict], *, max_retries: int) -> dict:
    user = json.dumps({"route": route}) + "\n\nSOURCE PAGES:\n" + _corpus(pages)
    last_err = None
    for _ in range(max_retries + 1):
        try:
            if _EXTRACTOR is not None:
                return _EXTRACTOR(_SYSTEM, user)
            import concurrent.futures
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(_live_extract, _SYSTEM, user)
            try:
                return fut.result(timeout=MODEL_CALL_HARD_TIMEOUT)
            except concurrent.futures.TimeoutError:
                last_err = TimeoutError(
                    f"model call hard-timeout after {MODEL_CALL_HARD_TIMEOUT:.0f}s")
                continue
            finally:
                ex.shutdown(wait=False)
        except KimiUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise _CallFailed(last_err)


def _same_value(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def extract(route: dict, pages: list[dict], *, max_retries: int = 2) -> dict:
    """pages: [{id, url, hostname, text}]. Returns:
    {ok, fields:{name:{value,sources:[urls]}}, conflicts:[...], rejected:[...],
     skipped_sources:[...], summary, model} — every surviving field grounded in a
    fetched page.

    Robust to provider content filters: some official government homepages (e.g.
    a foreign-ministry front page carrying political news) are rejected by the
    model provider's own content filter with a 400. Rather than losing the whole
    route, the extractor tries the full set first and, on a non-fatal call
    failure, splits the page set and retries each half — isolating and honestly
    SKIPPING only the offending page(s) while still extracting the visa-relevant
    pages. A skipped page is recorded (auditable), never guessed around."""
    known_ids = {p["id"] for p in pages}
    id_to_url = {p["id"]: p["url"] for p in pages}

    raw_list: list[dict] = []
    skipped: list[dict] = []

    def _recurse(subpages: list[dict]) -> None:
        if not subpages:
            return
        try:
            raw_list.append(_call_model(route, subpages, max_retries=max_retries))
        except KimiUnavailable:
            raise
        except _CallFailed as e:
            if len(subpages) == 1:
                skipped.append({"url": subpages[0]["url"], "id": subpages[0]["id"],
                                "error": str(e.cause)[:160]})
                return
            mid = len(subpages) // 2
            _recurse(subpages[:mid])
            _recurse(subpages[mid:])

    _recurse(pages)

    if not raw_list:
        if skipped:
            # Every fetched page was rejected by the provider's content filter.
            # Honest: sources are still stored; no grounded fields exist.
            from ..config import settings
            return {"ok": True, "fields": {}, "conflicts": [],
                    "rejected": [{"field": s["id"], "reason": "provider_content_filter",
                                  "url": s["url"]} for s in skipped],
                    "skipped_sources": skipped, "summary": "",
                    "model": settings().kimi_model, "extraction_version": EXTRACTION_VERSION}
        raise KimiUnavailable("Kimi extraction produced no result")

    # Merge grounded fields across sub-calls. Same field, same value -> union of
    # sources; same field, DIFFERENT value across pages -> a conflict.
    fields_out: dict = {}
    conflicts_map: dict = {}
    rejected: list[dict] = []
    for raw in raw_list:
        for name, payload in (raw.get("fields") or {}).items():
            if name not in EXTRACTABLE_FIELDS:
                rejected.append({"field": name, "reason": "unknown_field"})
                continue
            if not isinstance(payload, dict) or "value" not in payload:
                rejected.append({"field": name, "reason": "malformed"})
                continue
            cites = [c for c in (payload.get("sources") or []) if c in known_ids]
            if not cites:
                rejected.append({"field": name, "reason": "ungrounded_no_valid_citation",
                                 "claimed_sources": payload.get("sources")})
                continue
            srcs = [id_to_url[c] for c in cites]
            if name not in fields_out:
                fields_out[name] = {"value": payload["value"], "sources": srcs}
            elif _same_value(fields_out[name]["value"], payload["value"]):
                fields_out[name]["sources"] = sorted(set(fields_out[name]["sources"]) | set(srcs))
            else:
                conflicts_map.setdefault(name, [dict(fields_out[name])])
                conflicts_map[name].append({"value": payload["value"], "sources": srcs})

    conflicts_out = []
    # Model-reported conflicts within a single call.
    for raw in raw_list:
        for c in raw.get("conflicts") or []:
            vals = []
            for v in c.get("values") or []:
                cites = [x for x in (v.get("sources") or []) if x in known_ids]
                if cites:
                    vals.append({"value": v.get("value"), "sources": [id_to_url[x] for x in cites]})
            if len(vals) >= 2:
                conflicts_out.append({"field": c.get("field"), "values": vals})
    # Cross-page disagreements discovered while merging.
    for name, vals in conflicts_map.items():
        # A merged conflict removes the field from the definitive set.
        fields_out.pop(name, None)
        conflicts_out.append({"field": name, "values": vals})

    summary = next((str(r.get("summary", "")) for r in raw_list if r.get("summary")), "")[:400]
    from ..config import settings
    return {"ok": True, "fields": fields_out, "conflicts": conflicts_out,
            "rejected": rejected, "skipped_sources": skipped, "summary": summary,
            "model": settings().kimi_model, "extraction_version": EXTRACTION_VERSION}
