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
import os
import pathlib
import re
import threading
from functools import lru_cache

from .authority import hostname, is_government_host

OVERRIDES = pathlib.Path(__file__).resolve().parents[3] / "data" / \
    "database_seed" / "verified_overrides.json"

# Operator-written overrides live OUTSIDE the deployable tree, so a deploy
# rsync can never clobber what a Trip.com operator wrote through the console,
# and the daily /var/lib/ellis backup carries them. Entries here pass exactly
# the same gates as the seed file and are loaded AFTER it, so an operator
# correction wins over the shipped fact for the fields it names.
_OP_LOCK = threading.Lock()


def operator_overrides_path() -> pathlib.Path:
    env = os.environ.get("ELLIS_OPERATOR_OVERRIDES")
    if env:
        return pathlib.Path(env)
    var = pathlib.Path("/var/lib/ellis")
    if var.is_dir():
        return var / "operator_overrides.json"
    return OVERRIDES.parent / "operator_overrides.json"

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
    # Which mission handles this applicant. Verified jurisdiction rules were
    # being written and then silently dropped here, which is why that column
    # stayed empty on every record while the facts sat in the seed.
    "consular_jurisdiction",
    # Names the fields a destination was checked for and does not publish, so
    # a correct blank reads as "not publicly available" instead of a gap.
    "unpublished_fields",
    # The page the answer cites. A link that has gone dead, or that was never
    # about this nationality in the first place, could be corrected on the
    # record and not on the page the customer reads, because the customer page
    # shows the answer's own source_url and nothing could reach it. Any URL
    # written here passes the same government-host gate as the override's own
    # provenance, so this cannot be used to smuggle in an unofficial page.
    "source_url",
    # §4.2.1: where no single official source settles a route, several
    # high-credibility sources are cross-checked and "每条来源须逐条绑定URL",
    # each source bound to its own URL. A record carried exactly one URL, so
    # a route checked against three ministries could show only one of them and
    # the other two were unauditable. Each entry is gated individually.
    "corroborating_sources",
})

# Fields whose value is a link. Whatever an override puts in one of these has
# to satisfy the same rule as the override's provenance: an official page or
# nothing at all.
_URL_FIELDS = ("source_url", "official_portal_url")


# A verification writes prose, and some of that prose is the reviewer arguing
# with the claim in front of them rather than telling a traveller anything.
# Eleven overrides reached the display page carrying sentences like "CORRECTION
# TO THE SUBMITTED CLAIM" and "the claim's risk label is backwards, I am
# correcting it", and the customer page renders exceptions under "Good to know".
# A field a customer reads may not contain the workings.
_REVIEWER_VOICE = (
    "the claim", "correction to the submitted", "i am correcting",
    "the submitted row", "material detail", "should not be published",
    "do not publish", "the agent ", "as an ai", "my earlier",
    "purpose scope the claim", "is wrong and dangerous",
)
# The fields whose text a customer actually reads.
_CUSTOMER_TEXT = ("exceptions", "application_channel_detail", "requirement_detail",
                  "permitted_stay", "processing_time", "required_documents",
                  "entry_requirements", "consular_jurisdiction")


def _clean_corroborating(value):
    """Keep only entries that name an official page and say what it said.

    An entry is {url, quote} at minimum. A URL that fails the government-host
    gate is dropped rather than the whole list, so one bad citation cannot
    take the good ones with it, and an entry with no quote is dropped because
    a bare link is not evidence of agreement."""
    if not isinstance(value, (list, tuple)):
        return None
    kept = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not url or not quote:
            continue
        if not is_government_host(hostname(url)):
            continue
        entry = {"url": url, "quote": quote[:600]}
        for k in ("checked_at", "authority", "agrees"):
            if item.get(k) not in (None, ""):
                entry[k] = item[k]
        kept.append(entry)
    return kept or None


def _reads_like_review(value) -> bool:
    """True when this text is the reviewer talking, not the answer."""
    if isinstance(value, (list, tuple)):
        return any(_reads_like_review(v) for v in value)
    low = str(value or "").lower()
    return any(marker in low for marker in _REVIEWER_VOICE)


def _key(nat: str, dest: str, purpose: str = "tourism",
         doc: str = "") -> str:
    """Ordinary-passport facts key on route alone; a fact verified for a
    SPECIFIC document (a diplomatic or service passport follows bilateral
    agreements, not tourist rules) carries the document in its key and only
    ever matches that document."""
    base = f"{str(nat).upper()}|{str(dest).upper()}|{str(purpose).lower()}"
    doc = str(doc or "").strip().lower()
    return f"{base}|{doc}" if doc and doc != "ordinary_passport" else base


_CACHE: dict = {"mtime": None, "table": {}}


def _table() -> dict:
    """route key -> override entry, rebuilt whenever either file changes on
    disk so a newly verified fact reaches readers without a restart. Entries
    missing a source or a date, or citing a non-government domain, are
    dropped with no effect."""
    op = operator_overrides_path()
    try:
        mtime = (OVERRIDES.stat().st_mtime if OVERRIDES.is_file() else None,
                 op.stat().st_mtime if op.is_file() else None)
    except OSError:
        mtime = None
    if _CACHE["mtime"] == mtime and _CACHE["table"] is not None:
        return _CACHE["table"]
    table = _load_table()
    _CACHE["mtime"], _CACHE["table"] = mtime, table
    return table


def _read_rows(path: pathlib.Path) -> list:
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken file must not break lookups
        return []
    return rows if isinstance(rows, list) else []


def _load_table() -> dict:
    # Seed first, then operator entries MERGED per field on top. An operator
    # who corrects one field must not wipe the seed's other verified facts:
    # a console edit of processing_time once shadowed a route's entire
    # verified entry and the flagship fee vanished from the served answer
    # (2026-09-01). Entry-level provenance follows the newest editor; the
    # seed's note is kept alongside so the trail stays readable.
    table = _parse_rows(_read_rows(OVERRIDES), {})
    ops = _parse_rows(_read_rows(operator_overrides_path()), {})
    for k, op in ops.items():
        base = table.get(k)
        if base is None:
            table[k] = op
            continue
        merged_fields = dict(base["fields"])
        merged_fields.update(op["fields"])
        table[k] = {"fields": merged_fields,
                    "source_url": op["source_url"],
                    "verified_at": op["verified_at"],
                    "verified_by": op["verified_by"],
                    "note": (base.get("note") or "").strip()}
        if op.get("note"):
            table[k]["note"] = (table[k]["note"] + " | " + op["note"]).strip(" |")[:400]
    return table


def _parse_rows(rows, table: dict) -> dict:
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
        for k in _URL_FIELDS:
            v = str(clean.get(k) or "").strip()
            if v and not is_government_host(hostname(v)):
                clean.pop(k, None)
        for k in _CUSTOMER_TEXT:
            if k in clean and _reads_like_review(clean[k]):
                clean.pop(k, None)
        if "corroborating_sources" in clean:
            cleaned = _clean_corroborating(clean["corroborating_sources"])
            if cleaned:
                clean["corroborating_sources"] = cleaned
            else:
                clean.pop("corroborating_sources", None)
        if not clean:
            continue
        table[_key(route["nationality"], route["destination"],
                   route.get("travel_purpose", "tourism"),
                   route.get("travel_document_type", ""))] = {
            "fields": clean, "source_url": url, "verified_at": when,
            "verified_by": str(r.get("verified_by") or "").strip(),
            "note": str(r.get("note") or "").strip()[:400],
        }
    return table


def reload() -> None:
    """Forget the cached table (tests swap the file path)."""
    _CACHE["mtime"], _CACHE["table"] = None, None


def append_operator_entry(entry: dict) -> dict:
    """Persist one operator-written override, applying the same gates the
    loader applies, but LOUDLY: a rejected entry raises ValueError naming the
    reason, so the console can tell the operator exactly what to fix instead
    of silently dropping their work."""
    route = entry.get("route") or {}
    if not (route.get("nationality") and route.get("destination")):
        raise ValueError("the route needs a nationality and a destination")
    url = str(entry.get("source_url") or "").strip()
    if not url or not is_government_host(hostname(url)):
        raise ValueError("every edit must cite a page on an official "
                         "government domain")
    if not str(entry.get("note") or "").strip():
        raise ValueError("say what was checked and why, in the note")
    fields = entry.get("fields") or {}
    unknown = [k for k in fields if k not in OVERRIDABLE]
    if unknown:
        raise ValueError(f"these fields cannot be edited: {sorted(unknown)}")
    clean = {k: v for k, v in fields.items() if k in OVERRIDABLE}
    for k in _URL_FIELDS:
        v = str(clean.get(k) or "").strip()
        if v and not is_government_host(hostname(v)):
            raise ValueError(f"{k} must be a page on an official government "
                             "domain")
    for k in _CUSTOMER_TEXT:
        if k in clean and _reads_like_review(clean[k]):
            raise ValueError(f"{k} reads like a reviewer's argument, not a "
                             "customer-facing fact. State the fact plainly")
    if not clean:
        raise ValueError("no editable fields were given")
    entry = dict(entry, fields=clean)
    path = operator_overrides_path()
    with _OP_LOCK:
        rows = _read_rows(path)
        # One entry per route: a re-edit replaces the previous operator entry
        # for the same route instead of stacking shadowed duplicates.
        def _rk(r):
            rr = r.get("route") or {}
            return (rr.get("nationality"), rr.get("destination"),
                    rr.get("travel_purpose", "tourism"),
                    rr.get("travel_document_type", ""))
        rows = [r for r in rows if _rk(r) != _rk(entry)]
        rows.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(path)
    reload()
    return entry


def find(route: dict) -> dict | None:
    # A diplomatic or service passport answer is a different policy: it
    # matches ONLY an override verified for that document. Ordinary-passport
    # routes match only document-less overrides.
    doc = str(route.get("travel_document_type") or "ordinary_passport")
    return _table().get(_key(route.get("passport_nationality", ""),
                             route.get("destination_country", ""),
                             route.get("travel_purpose", "tourism"),
                             doc))


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
    docs = merged.get("required_documents")
    if docs is not None and "required_documents" not in fields:
        merged["required_documents"] = _documents_without_an_application(docs)
    # A verdict of "no authorisation is required" cannot sit beside a sentence
    # saying one is. Singapore to Spain read "No visa and no travel
    # authorisation" in one field and "require only an approved ETIAS to enter
    # Spain" in the next. exceptions was on no clean-up list, so the model's
    # stale claim outlived the verdict that falsified it.
    if str(merged.get("application_channel") or "").lower() in (
            "not_required", "none", "no_application_required",
            "none_or_port_of_entry") and "exceptions" not in fields:
        merged["exceptions"] = _exceptions_without_a_required_authorisation(
            merged.get("exceptions"))


# Items that exist only to feed an application form. On a route where nothing
# is applied for they are not documents a traveller needs, they are the
# residue of an application that does not happen. Japan to Italy listed "email
# address, payment card" on a verified visa-free verdict, left over from an
# ETIAS the European Commission has not yet brought into operation.
_APPLICATION_ONLY_DOCUMENTS = (
    "email address", "e-mail address", "email", "payment card", "credit card",
    "debit card", "bank card", "application form", "visa application form",
    "online application form", "application fee", "payment method",
)


def _documents_without_an_application(docs):
    """Drop the items that only make sense when there is a form to submit.

    What survives is what a border officer can actually ask to see: the
    passport, the onward ticket, the accommodation booking, the funds."""
    def keep(item: str) -> bool:
        low = str(item).strip().lower()
        return bool(low) and not any(m in low for m in _APPLICATION_ONLY_DOCUMENTS)

    if isinstance(docs, (list, tuple)):
        kept = [d for d in docs if keep(d)]
        return kept if kept else docs
    text = str(docs or "")
    if not text:
        return docs
    kept = [part.strip() for part in text.split(",") if keep(part)]
    return ", ".join(kept) if kept else docs


# A sentence that says the traveller must hold an authorisation, on a route
# whose verified verdict is that none is required. The explanatory sentences
# stay: "ETIAS is an entry authorisation, not a visa" and "ETIAS is not in
# operation" are both true and both useful. Only the assertion goes.
_ASSERTS_AN_AUTHORISATION = re.compile(
    r"(requires?\s+(only\s+)?an?\s+(approved\s+)?(ETIAS|ESTA|K-ETA|ETA|"
    r"electronic travel authoris\w*)"
    r"|must\s+(hold|obtain|apply\s+for|have)\s+an?\s+(approved\s+)?"
    r"(ETIAS|ESTA|K-ETA|ETA|electronic travel authoris\w*)"
    r"|need\s+an?\s+(approved\s+)?(ETIAS|ESTA|K-ETA|ETA)"
    r"|(ETIAS|ESTA|K-ETA)\s+is\s+required)", re.I)


def _exceptions_without_a_required_authorisation(items):
    """Drop only the sentences that contradict a no-authorisation verdict."""
    if not isinstance(items, (list, tuple)):
        if items and _ASSERTS_AN_AUTHORISATION.search(str(items)):
            return None
        return items
    kept = [x for x in items
            if not _ASSERTS_AN_AUTHORISATION.search(str(x or ""))]
    return kept if kept != list(items) else items


# Which requirement_detail subcategories can truthfully sit under each
# verified disposition. A detail outside its verdict's family is a leftover
# from the un-overridden model answer and reads as a contradiction
# ("Visa required" badge next to "unconditional visa-free").
_DETAIL_FAMILY = {
    "VISA_EXEMPT": ("unconditional_visa_free", "conditional_visa_free",
                    "transit_visa_free"),
    "VISA_ON_ARRIVAL": ("evisa_on_arrival", "paper_visa_on_arrival"),
    "ELECTRONIC_AUTHORIZATION_REQUIRED": ("eta_electronic_authorization",),
    "VISA_REQUIRED": ("evisa", "paper_visa"),
}


def _drop_exemption_leftovers(merged: dict, fields: dict,
                              original: dict) -> None:
    """The mirror of _drop_application_leftovers: a verified visa-REQUIRED
    verdict clears the model's exemption claims. Never invents a value."""
    verdict = str(fields.get("disposition") or "").upper()
    if not verdict or verdict == "VISA_EXEMPT":
        return
    if "requirement_detail" not in fields:
        detail = str(merged.get("requirement_detail") or "")
        family = _DETAIL_FAMILY.get(verdict)
        if detail and family is not None and detail not in family:
            merged.pop("requirement_detail", None)
    # When the verdict actually FLIPPED (model said exempt, source says a
    # visa is needed) the model's exemption narrative is falsified with it.
    flipped = str(original.get("disposition") or "").upper() == "VISA_EXEMPT"
    if flipped:
        pat = re.compile(r"免签|免簽|visa[- ]?free|visa[- ]?exempt|exemption",
                         re.I)
        if "exceptions" not in fields:
            v = merged.get("exceptions")
            if isinstance(v, list):
                kept = [x for x in v if not pat.search(str(x))]
                if len(kept) != len(v):
                    merged["exceptions"] = kept
            elif isinstance(v, str) and pat.search(v):
                merged.pop("exceptions", None)
        # The prose fields the model wrote for its exempt answer contradict
        # the verified visa-required verdict just as loudly as the enum did.
        for k in ("permitted_stay", "application_channel_detail",
                  "visa_category"):
            if k not in fields and isinstance(merged.get(k), str) \
                    and pat.search(merged[k]):
                merged.pop(k, None)


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
    _drop_exemption_leftovers(merged, hit["fields"], guidance)
    return merged, {
        "source_url": hit["source_url"], "verified_at": hit["verified_at"],
        "verified_by": hit["verified_by"], "note": hit["note"],
        "fields": sorted(hit["fields"].keys()),
    }
