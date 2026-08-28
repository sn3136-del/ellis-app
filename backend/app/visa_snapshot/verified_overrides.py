"""Human-verified facts that outrank the model's answer.

WHY THIS EXISTS. The Database's fast path answers from Kimi's own knowledge
(see kimi_primary's header: no official-source fetching runs there). That is
instant and usually right, but it is only as current as the model — and visa
policy moves. An audit against official government sources on 2026-08-22 found
ten routes where the model reported the wrong DISPOSITION because the policy
had changed: the Philippines and Russia going visa-free for Chinese nationals,
the China-Singapore mutual exemption, HKSAR passports being eTA-eligible for
Canada and not visa nationals for the UK, K-ETA waivers for Hong Kong and the
United States, Hong Kong's exclusion from India's e-Visa list and from Taiwan's
visa regime, and Indonesia being visa-on-arrival rather than visa-free for US
nationals.

Guessing harder does not fix that. A checked fact does. An override is a fact
a PERSON verified against a named official page on a named date, and it wins
over the model for exactly the fields it names — nothing else is touched, and
an answer that carries one says so, with the source and the date, instead of
presenting a model recollection as established fact.

RULES, all deliberate:
  * An override MUST carry a source_url on an official government domain and a
    verified_at date. Without both it is ignored — an unsourced correction is
    just a different guess.
  * It overrides ONLY the fields it lists. The rest of the answer is the
    model's, and is still labelled as such.
  * It never invents a route. If no answer exists for that route, there is
    nothing to override.
  * It is visible: the answer reports source_verified so a reader can see
    which facts were checked, by whom, and when.
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache

from .authority import hostname, is_government_host

OVERRIDES = pathlib.Path(__file__).resolve().parents[3] / "data" / \
    "database_seed" / "verified_overrides.json"

# Fields an override is allowed to correct. Anything else in the file is
# ignored rather than trusted, so a malformed entry cannot reshape an answer.
OVERRIDABLE = frozenset({
    "disposition", "requirement_detail", "visa_category", "permitted_stay",
    "permitted_stay_days", "application_channel", "application_channel_detail",
    "government_fee", "official_portal_url", "visa_products", "processing_time",
    "exceptions", "required_documents", "confidence",
    # A mandatory pre-arrival filing (Malaysia's MDAC, the SG Arrival Card) is
    # the difference between boarding and not boarding, so a verified fact
    # must be able to correct it.
    "arrival_card",
})


def _key(nat: str, dest: str, purpose: str = "tourism") -> str:
    return f"{str(nat).upper()}|{str(dest).upper()}|{str(purpose).lower()}"


_CACHE: dict = {"mtime": None, "table": {}}


def _table() -> dict:
    """route key -> override entry, rebuilt whenever the file changes on disk
    so a newly verified fact reaches readers without a restart. Entries
    missing a source or a date, or citing a non-government domain, are
    dropped with no effect."""
    try:
        mtime = OVERRIDES.stat().st_mtime if OVERRIDES.is_file() else None
    except OSError:
        mtime = None
    if _CACHE["mtime"] == mtime and _CACHE["table"] is not None:
        return _CACHE["table"]
    table = _load_table()
    _CACHE["mtime"], _CACHE["table"] = mtime, table
    return table


def _load_table() -> dict:
    if not OVERRIDES.is_file():
        return {}
    try:
        rows = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken file must not break lookups
        return {}
    table = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        route = r.get("route") or {}
        url = str(r.get("source_url") or "").strip()
        when = str(r.get("verified_at") or "").strip()
        fields = r.get("fields") or {}
        if not (route.get("nationality") and route.get("destination")):
            continue
        if not url or not when or not isinstance(fields, dict) or not fields:
            continue
        if not is_government_host(hostname(url)):
            continue          # an override must cite an official source
        clean = {k: v for k, v in fields.items() if k in OVERRIDABLE}
        if not clean:
            continue
        table[_key(route["nationality"], route["destination"],
                   route.get("travel_purpose", "tourism"))] = {
            "fields": clean, "source_url": url, "verified_at": when,
            "verified_by": str(r.get("verified_by") or "").strip(),
            "note": str(r.get("note") or "").strip()[:400],
        }
    return table


def reload() -> None:
    """Forget the cached table (tests swap the file path)."""
    _CACHE["mtime"], _CACHE["table"] = None, None


def find(route: dict) -> dict | None:
    # Overrides are verified for ORDINARY passports unless their route says
    # otherwise. A diplomatic or service passport answer is a different
    # policy; letting the ordinary-passport fact claim it produced "visa-free
    # Japan, verified" records for diplomatic variants.
    doc = str(route.get("travel_document_type") or "ordinary_passport")
    if doc not in ("", "ordinary_passport"):
        return None
    return _table().get(_key(route.get("passport_nationality", ""),
                             route.get("destination_country", ""),
                             route.get("travel_purpose", "tourism")))


# Fields that only describe APPLYING for a visa. When a verified override
# says no visa is needed, whatever the model wrote in these is about an
# application that does not happen, and would contradict the verdict on the
# same page ("No visa needed ... processing time about 3 working days").
# NOTE: arrival_card is deliberately NOT here. A visa-free route can still
# require a pre-arrival filing, and dropping it would strand a traveller.
_APPLICATION_ONLY = ("processing_time", "forms", "account_registration_steps",
                     "payment_process", "submission_process",
                     "official_portal_url", "government_fee",
                     # An independent re-verification found visa-free answers
                     # still carrying the machinery of an application: a
                     # "Tourist eVisa" category, an eVisa workflow type, a
                     # channel sentence sending the traveller to a portal, and
                     # a product table with fees. A verified visa-free verdict
                     # clears those too.
                     "visa_products", "visa_category", "application_channel",
                     "application_channel_detail", "route_workflow_type")


def _drop_application_leftovers(merged: dict, fields: dict) -> None:
    """A verified visa-free verdict clears application-only leftovers, unless
    the override itself supplied them. Never invents a value: it removes
    claims that the verified verdict has made false."""
    if str(fields.get("disposition") or "").upper() != "VISA_EXEMPT":
        return
    for k in _APPLICATION_ONLY:
        if k in fields:
            continue          # the verified fact wins, whatever it says
        merged.pop(k, None)
    # Both are about applying for a visa. With no visa to apply for they are
    # false, whatever the model said. (This previously only wrote False when
    # the value was ALREADY falsy, so a visa-free answer could still show
    # "Appointment: Required".)
    merged["appointment_required"] = False
    merged["interview_required"] = False


def apply(guidance: dict, route: dict) -> tuple[dict, dict | None]:
    """Return (guidance, provenance). The guidance is a COPY with the verified
    fields replaced; provenance names the source, the date and the fields so
    the answer can show what was checked rather than implying all of it was."""
    hit = find(route or {})
    if not hit or not isinstance(guidance, dict):
        return guidance, None
    merged = dict(guidance)
    merged.update(hit["fields"])
    _drop_application_leftovers(merged, hit["fields"])
    return merged, {
        "source_url": hit["source_url"], "verified_at": hit["verified_at"],
        "verified_by": hit["verified_by"], "note": hit["note"],
        "fields": sorted(hit["fields"].keys()),
    }
