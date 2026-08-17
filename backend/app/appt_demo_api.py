"""Appointments demo: Kimi K3 picks the applicant's nearest official centre.

Scope: this endpoint serves the APPOINTMENTS DEMO surface only. It reads no
case data, writes nothing, and never touches a government site — it answers a
single question ("of these official centres, which is nearest this address, and
why?") with the same Kimi K3 engine the rest of Ellis uses.

Demo-safety rules, deliberate:
  * a SHORT timeout — a live demo must never hang on a model call;
  * every failure (no key, timeout, bad JSON, unknown id) returns
    available:false rather than an error, so the caller silently falls back to
    its own deterministic distance sort and the flow always completes;
  * the model may only CHOOSE from the centres it is given: an id outside the
    list is rejected, so it can never invent a consulate.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/appointments/demo", tags=["appointments"])

# Live demos are unforgiving: budget the model call tightly and fall back.
_TIMEOUT_SECONDS = 8.0

_SYSTEM = (
    "You help a traveller find the OFFICIAL visa appointment centre closest to "
    "their home address. You are given the applicant's address and a list of "
    "real centres, each with an id, name, city and street address. Choose the "
    "single nearest centre by real-world geography. Reply ONLY as JSON: "
    '{"centre_id": "<one id from the list>", "reason": "<one short sentence, '
    'max 18 words, naming the applicant\'s district/city and the centre\'s>"}. '
    "The centre_id MUST be one of the given ids. Never invent a centre."
)


class NearestIn(BaseModel):
    address: str
    centres: list[dict] = []      # [{id, name, city, address}]


@router.post("/nearest-centre")
def nearest_centre(body: NearestIn):
    """Kimi K3's pick of the nearest centre, or available:false so the caller
    uses its own distance calculation. Never raises."""
    ids = [str(c.get("id") or "") for c in (body.centres or []) if c.get("id")]
    if not body.address.strip() or not ids:
        return {"available": False, "reason": "address or centre list missing"}
    try:
        from .providers import kimi as kimi_mod
        provider = kimi_mod.get_provider()
        chat = getattr(provider, "_chat", None)
        if chat is None:
            return {"available": False, "reason": "no reasoning provider configured"}
        payload = {
            "applicant_address": body.address.strip(),
            "centres": [{"id": c.get("id"), "name": c.get("name"),
                         "city": c.get("city"), "address": c.get("address")}
                        for c in body.centres],
        }
        import json as _json
        # K3 quirks, learned the hard way: temperature=0.0 is REJECTED (HTTP
        # 400) by the reasoning model, and a small max_tokens starves its
        # reasoning budget — so no temperature, and headroom for the answer.
        out = chat(_SYSTEM, _json.dumps(payload, ensure_ascii=False),
                   json_mode=True, timeout=_TIMEOUT_SECONDS,
                   max_tokens=600) or {}
    except Exception:  # noqa: BLE001 - ANY failure degrades to the caller's own math
        return {"available": False, "reason": "the reasoning engine did not answer"}
    picked = str(out.get("centre_id") or "").strip()
    if picked not in ids:
        # A hallucinated or empty id is refused outright — the caller's
        # deterministic sort is always the safer answer.
        return {"available": False, "reason": "no valid centre returned"}
    return {"available": True, "centre_id": picked,
            "note": str(out.get("reason") or "")[:200],
            "engine": getattr(provider, "name", "kimi")}
