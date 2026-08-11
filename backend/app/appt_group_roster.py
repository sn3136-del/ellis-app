"""US GROUP APPOINTMENTS — the roster a human coordinator submits.

The US consular scheduler has exactly one sanctioned batch path, and it is not
automation: the official GROUP APPOINTMENT. Its rules are narrow, and Ellis
honours them literally rather than conveniently.

  * a group is "10 or more individuals travelling together"; tour groups are a
    named qualifying example;
  * "families and relatives travelling together do not qualify and must
    request individual appointments";
  * the request carries, per member, a passport number, a 10-character DS-160
    confirmation number, and an MRV fee receipt number;
  * "the applicant must complete his or her own Form DS-160";
  * "Our agents cannot schedule group appointments" — a human GROUP
    COORDINATOR submits the request, the consulate approves it, the approval
    EXPIRES, and after approval the coordinator books each member's slot;
  * the typical ceiling is 50 travellers per request.

So this module has no hands. It reads member cases, says exactly who is
missing what so the coordinator can chase them, and renders an artifact the
coordinator transcribes or uploads themselves.

THE HARD LINE, and why it is a line: there is deliberately NO slot search, NO
booking call, NO portal driver and NO HTTP client anywhere in this file.
Automated slot search on the US scheduler gets the TRAVELLER's appointment and
visa cancelled — roughly 2,000 were cancelled in India in 2025 — so the
sanctioned batch path has to stay a paperwork path, and paperwork is all this
module can physically do. ``tests/test_appt_group_roster.py`` enforces that by
auditing this module's own imports and by exercising it with the socket layer
torn out.

Three honesty rules are enforced here rather than trusted to a caller:

  1. AN ELIGIBILITY VERDICT IS NEVER INVENTED. A group whose composition Ellis
     cannot see is reported ``unverified`` — not eligible — because a group
     request rejected at the consulate costs the whole group its dates.
  2. AN IDENTIFIER IS NEVER MADE TO LOOK VALID. A nine-character confirmation
     number is rejected and reported, never padded, and a value that fails
     validation is dropped from the roster rather than passed on for
     transcription into a government system.
  3. PASSPORT NUMBERS STAY OUT OF THE SUMMARY. The roster structure — the
     thing that gets serialized into API responses and logs — carries only
     ``passport_number_present``. The coordinator's own artifact prints the
     numbers only when the caller explicitly asks for them.
"""
from __future__ import annotations

import csv
import io
import re
import textwrap
from datetime import date

from . import dates
from .providers import pdfgen

# ---------------------------------------------------------------------------
# The official rules. Quoted where the wording itself is the rule, because the
# coordinator has to be able to read Ellis's reason and the consulate's page
# and see the same sentence.
# ---------------------------------------------------------------------------

MIN_GROUP_SIZE = 10
GROUP_SIZE_CAP = 50
REMINDER_DAYS = 7                     # re-request warning window before expiry

OFFICIAL_MIN_SIZE = "10 or more individuals travelling together"
OFFICIAL_FAMILY_EXCLUSION = (
    "families and relatives travelling together do not qualify and must "
    "request individual appointments")
OFFICIAL_OWN_DS160 = "the applicant must complete his or her own Form DS-160"
OFFICIAL_NO_AGENT_SCHEDULING = "Our agents cannot schedule group appointments."

# Stated in code so a test can assert it, and so a future reader cannot mistake
# the omission for an oversight.
NO_AUTOMATION = {
    "slot_search": ("never — automated slot search cancels the TRAVELLER's "
                    "appointment and visa"),
    "booking": "never — the coordinator books each member's slot themselves",
    "portal_automation": "none — Ellis renders an artifact, the human submits it",
    "agent_scheduling": OFFICIAL_NO_AGENT_SCHEDULING,
}

VERDICT_ELIGIBLE = "eligible"
VERDICT_INELIGIBLE = "ineligible"
VERDICT_UNVERIFIED = "unverified"


# ---------------------------------------------------------------------------
# Identifier formats.
# ---------------------------------------------------------------------------

# CEAC's confirmation is described by the scheduler as a "10-digit confirmation
# number", but the values CEAC actually prints are uppercase alphanumeric (e.g.
# AA00ABCD12). The invariant that always holds is the LENGTH, so that is what is
# checked: exactly ten uppercase alphanumeric characters. Anything else — nine
# characters, eleven, a value with punctuation — is rejected rather than
# reshaped, because a "fixed" confirmation number points at somebody else's
# form, or at no form at all.
DS160_CONFIRMATION_RE = re.compile(r"^[A-Z0-9]{10}$")

# MRV receipts are issued by whichever bank/agent took the fee (CITIC in China,
# a different shape in every other market), so there is no single true pattern
# and an over-strict one would reject real receipts. This check therefore only
# catches empty and obvious-garbage values; format is the bank's business.
MRV_RECEIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/-]{4,29}$")


# ---------------------------------------------------------------------------
# Group composition vocabulary.
# ---------------------------------------------------------------------------

FAMILY_GROUP_KINDS = {"family", "families", "relatives", "household",
                      "family_group", "family_and_relatives"}
NON_FAMILY_GROUP_KINDS = {
    "tour_group", "tour", "tourgroup", "travel_group", "organization",
    "organisation", "company", "business", "corporate", "school",
    "student_group", "delegation", "conference", "crew", "team", "choir",
    "sports_team", "religious_group", "mission",
}

FAMILY_RELATIONS = {
    "spouse", "husband", "wife", "partner", "domestic_partner", "fiance",
    "fiancee", "child", "children", "son", "daughter", "stepson",
    "stepdaughter", "stepchild", "parent", "mother", "father", "stepmother",
    "stepfather", "sibling", "brother", "sister", "grandparent",
    "grandmother", "grandfather", "grandchild", "grandson", "granddaughter",
    "cousin", "aunt", "uncle", "nephew", "niece", "relative", "family",
    "in_law",
}
NON_FAMILY_RELATIONS = {
    "unrelated", "none", "colleague", "coworker", "co_worker", "employee",
    "classmate", "student", "friend", "tour_member", "teammate", "delegate",
    "crew", "staff", "business_associate", "client", "guest",
}
# Relations that say nothing about family status. They neither prove nor
# disprove the exclusion, so a roster made only of these stays UNVERIFIED.
NEUTRAL_RELATIONS = {"", "self", "principal", "lead", "leader", "coordinator",
                     "group_leader", "tour_leader", "applicant", "member",
                     "traveller", "traveler", "unknown"}


def _token(value) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def _is_family_relation(rel: str) -> bool:
    return rel in FAMILY_RELATIONS or rel.endswith("_in_law")


def _is_non_family_relation(rel: str) -> bool:
    return rel in NON_FAMILY_RELATIONS


# ---------------------------------------------------------------------------
# Reading a member. Callers hold members in whatever shape their surface has:
# a plain dict, a VisaApplication row, or just a case id. All three are read
# here; a member Ellis cannot read is reported as unreadable, never skipped
# (a silently dropped member is a traveller who shows up without a slot).
# ---------------------------------------------------------------------------

_NAME_KEYS = ("name", "full_name", "member_name", "applicant_name",
              "traveller_name", "traveler_name")
_PASSPORT_KEYS = ("passport_number", "passport_no", "passport")
_DS160_KEYS = ("ds160_confirmation", "ds160_confirmation_number",
               "ds_160_confirmation", "ds160", "ds160_number")
_MRV_KEYS = ("mrv_receipt", "mrv_receipt_number", "mrv_receipt_no",
             "mrv_number", "mrv")
_RELATION_KEYS = ("relationship", "relation", "group_relation",
                  "member_relation")
_FAMILY_ID_KEYS = ("family_id", "household_id", "family_group_id")
_CASE_ID_KEYS = ("case_id", "application_id", "id")


def _pick(source: dict, keys) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _read_member(db, entry) -> dict:
    """Flatten one member entry into the fields the group request needs.

    ``readable`` is False when Ellis was handed a case id (or a row) it could
    not resolve — the member still appears on the roster, marked, so the
    coordinator sees that somebody is unaccounted for.
    """
    flat, case_id, readable = {}, "", True

    if isinstance(entry, str):
        case_id = entry.strip()
        row = None
        if db is not None and case_id:
            try:
                row = db.get(_visa_application_model(), case_id)
            except Exception:            # an unreadable case is reported, not raised
                row = None
        if row is None:
            readable = False
        else:
            entry = row
    if isinstance(entry, dict):
        flat = dict(entry)
        answers = flat.get("answers")
        if isinstance(answers, dict):
            merged = dict(answers)
            merged.update({k: v for k, v in flat.items() if v not in (None, "")})
            flat = merged
        case_id = case_id or _pick(flat, _CASE_ID_KEYS)
    elif not isinstance(entry, str):
        # An ORM row (VisaApplication): its answers hold the applicant's own
        # values, and the display name comes from the Applicant record.
        answers = getattr(entry, "answers", None)
        flat = dict(answers) if isinstance(answers, dict) else {}
        case_id = str(getattr(entry, "id", "") or "") or case_id
        applicant_id = getattr(entry, "applicant_id", "")
        if db is not None and applicant_id and not _pick(flat, _NAME_KEYS):
            try:
                applicant = db.get(_applicant_model(), applicant_id)
            except Exception:
                applicant = None
            if applicant is not None:
                flat["full_name"] = applicant.full_name

    return {
        "case_id": case_id,
        "readable": readable,
        "name": _pick(flat, _NAME_KEYS),
        "passport_number": _pick(flat, _PASSPORT_KEYS),
        "ds160_raw": _pick(flat, _DS160_KEYS),
        "mrv_raw": _pick(flat, _MRV_KEYS),
        "relation": _token(_pick(flat, _RELATION_KEYS)),
        "family_id": _pick(flat, _FAMILY_ID_KEYS),
    }


def _visa_application_model():
    from . import models
    return models.VisaApplication


def _applicant_model():
    from . import models
    return models.Applicant


# ---------------------------------------------------------------------------
# Per-member readiness.
# ---------------------------------------------------------------------------

def _normalize_ds160(raw: str) -> tuple[str, str]:
    """(value, problem). Whitespace is a copy/paste artifact and is removed;
    nothing else is. A value that does not validate is returned empty so it
    cannot be transcribed into the portal."""
    cleaned = re.sub(r"\s+", "", str(raw or "")).upper()
    if not cleaned:
        return "", ""
    if not DS160_CONFIRMATION_RE.match(cleaned):
        return "", (f"DS-160 confirmation number is not 10 characters "
                    f"(the value on file has {len(cleaned)})")
    return cleaned, ""


def _normalize_mrv(raw: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", "", str(raw or ""))
    if not cleaned:
        return "", ""
    if not MRV_RECEIPT_RE.match(cleaned):
        return "", "the value on file does not look like an MRV fee receipt number"
    return cleaned, ""


def _member_record(raw: dict, *, include_passport_numbers: bool) -> dict:
    missing: list[str] = []
    if not raw["readable"]:
        missing.append(f"Ellis could not read case {raw['case_id'] or '(no id)'}")
    if not raw["name"]:
        missing.append("the traveller's name as printed in the passport")

    passport_present = bool(raw["passport_number"])
    if not passport_present:
        missing.append("passport number")

    ds160, ds160_problem = _normalize_ds160(raw["ds160_raw"])
    if ds160_problem:
        missing.append(ds160_problem)
    elif not ds160:
        missing.append(f"own DS-160 confirmation number (10 characters) — "
                       f"{OFFICIAL_OWN_DS160}")

    mrv, mrv_problem = _normalize_mrv(raw["mrv_raw"])
    if mrv_problem:
        missing.append(f"a valid MRV fee receipt number — {mrv_problem}")
    elif not mrv:
        missing.append("MRV fee receipt number (the fee must be paid first)")

    record = {
        "case_id": raw["case_id"],
        "name": raw["name"],
        "passport_number_present": passport_present,
        "ds160_confirmation": ds160,
        "mrv_receipt": mrv,
        "ready": not missing,
        "missing": missing,
    }
    if include_passport_numbers and passport_present:
        record["passport_number"] = raw["passport_number"]
    return record


def _flag_shared_identifiers(records: list[dict], raws: list[dict]) -> None:
    """Two members holding one identifier is not a formatting slip: a shared
    DS-160 confirmation means somebody reused another traveller's form, which
    the official rule forbids outright. Both members are marked, because Ellis
    cannot know which one is the impostor."""
    checks = (
        ([r["ds160_confirmation"] for r in records],
         f"its OWN DS-160 confirmation number — this one is also on record for "
         f"{{others}}; {OFFICIAL_OWN_DS160}"),
        ([r["mrv_receipt"] for r in records],
         "its own MRV fee receipt number — this one is also on record for {others}"),
        ([r["passport_number"] for r in raws],
         "a distinct passport number — the same number is on record for {others}"),
    )
    for values, template in checks:
        seen: dict[str, list[int]] = {}
        for index, value in enumerate(values):
            if value:
                seen.setdefault(value, []).append(index)
        for indexes in seen.values():
            if len(indexes) < 2:
                continue
            for index in indexes:
                others = ", ".join(
                    records[i]["name"] or records[i]["case_id"] or f"member {i + 1}"
                    for i in indexes if i != index)
                records[index]["missing"].append(template.format(others=others))
                records[index]["ready"] = False


# ---------------------------------------------------------------------------
# Group composition: is this a family group?
# ---------------------------------------------------------------------------

def _family_only(raws: list[dict], group_kind: str):
    """True (family group), False (not a family group), or None — Ellis cannot
    tell, which is a verdict of its own and never rounds down to True."""
    kind = _token(group_kind)
    if kind in FAMILY_GROUP_KINDS:
        return True
    if kind in NON_FAMILY_GROUP_KINDS:
        return False

    ids = [r["family_id"] for r in raws]
    if len(raws) > 1 and all(ids) and len(set(ids)) == 1:
        return True                      # one household id across everybody

    relations = [r["relation"] for r in raws]
    if any(_is_non_family_relation(rel) for rel in relations):
        return False
    family = [rel for rel in relations if _is_family_relation(rel)]
    if not family:
        return None
    undecided = [rel for rel in relations
                 if not _is_family_relation(rel) and rel not in NEUTRAL_RELATIONS]
    if undecided:
        return None                      # family members plus strangers: unclear
    return True


# ---------------------------------------------------------------------------
# Expiration of the approved request (item 4). Dates go through app/dates.py so
# the display format is the one the rest of the product uses.
# ---------------------------------------------------------------------------

def expiry_status(expires_on, *, today: date | None = None,
                  reminder_days: int = REMINDER_DAYS) -> dict:
    """The state of the group request's expiration, honestly.

    An unrecorded or unparseable date is reported as ``unknown``. Ellis never
    invents an expiry: a wrong one either hurries a coordinator into a
    duplicate request or lets a real one lapse silently.
    """
    today = today or date.today()
    if isinstance(expires_on, date):
        iso = expires_on.isoformat()
    else:
        iso = dates.normalize_any(expires_on, kind="expiry", today=today)
    if not iso:
        note = ("expiration date not recorded — read it from the consulate's "
                "approval of the group request; Ellis does not guess one")
        return {"known": False, "status": "unknown", "expires_on": "",
                "display": "", "days_remaining": None, "expired": False,
                "reminder_due": False, "note": note}

    year, month, day = (int(part) for part in iso.split("-"))
    remaining = (date(year, month, day) - today).days
    display = dates.to_display(iso)
    if remaining < 0:
        status, note = "expired", (
            f"the group request expired on {display} — the coordinator must "
            f"submit a new Group Scheduling Request")
    elif remaining <= reminder_days:
        status, note = "expiring", (
            f"the group request expires on {display} ({remaining} "
            f"day{'' if remaining == 1 else 's'} left) — submit it, or "
            f"re-request before it lapses")
    else:
        status, note = "active", (
            f"the group request is valid until {display} ({remaining} days left)")
    return {"known": True, "status": status, "expires_on": iso,
            "display": display, "days_remaining": remaining,
            "expired": status == "expired",
            "reminder_due": status in ("expiring", "expired"), "note": note}


def record_expiration(roster: dict, expires_on, *, today: date | None = None,
                      reminder_days: int = REMINDER_DAYS) -> dict:
    """Attach (or update) the request's expiry on a roster. An expired request
    is not submittable — a new one has to be requested — so that consequence is
    written into the roster rather than left to the reader."""
    out = dict(roster)
    status = expiry_status(expires_on, today=today, reminder_days=reminder_days)
    out["expiry"] = status
    reasons = [r for r in (out.get("reasons") or [])
               if not r.startswith("the group request expired")]
    if status["expired"]:
        reasons.append(status["note"])
        out["submittable"] = False
    else:
        out["submittable"] = bool(out.get("submittable")) and not status["expired"]
    out["reasons"] = reasons
    return out


# ---------------------------------------------------------------------------
# The roster.
# ---------------------------------------------------------------------------

def build_roster(db, member_cases, *, group_kind: str = "", expires_on="",
                 today: date | None = None,
                 include_passport_numbers: bool = False) -> dict:
    """Assemble the US group-appointment roster for a human coordinator.

    ``member_cases`` may hold dicts, VisaApplication rows, or case ids (read
    through ``db``). ``group_kind`` is the coordinator's own declaration —
    "tour_group", "company", "family" — and is what settles the family
    exclusion when the member records carry no relationships.

    Returns the eligibility verdict, the reasons behind it, and every member
    with exactly what they are missing. It performs no scheduling of any kind.
    """
    entries = list(member_cases or [])
    raws = [_read_member(db, entry) for entry in entries]
    members = [_member_record(raw, include_passport_numbers=include_passport_numbers)
               for raw in raws]
    _flag_shared_identifiers(members, raws)

    size = len(members)
    cap_ok = size <= GROUP_SIZE_CAP
    family = _family_only(raws, group_kind)

    reasons: list[str] = []
    verdict = VERDICT_ELIGIBLE

    if size < MIN_GROUP_SIZE:
        verdict = VERDICT_INELIGIBLE
        reasons.append(
            f"a group appointment is for {OFFICIAL_MIN_SIZE}; this roster has "
            f"{size}. Each traveller must request an individual appointment.")
    if family is True:
        verdict = VERDICT_INELIGIBLE
        reasons.append(
            f"{OFFICIAL_FAMILY_EXCLUSION} — Ellis read this roster as a family "
            f"group.")
    elif family is None:
        if verdict == VERDICT_ELIGIBLE:
            verdict = VERDICT_UNVERIFIED
        reasons.append(
            f"Ellis cannot confirm this is not a family group: no group type "
            f"was declared and the members carry no relationship. Declare the "
            f"group type (tour group, company, school, family) — "
            f"{OFFICIAL_FAMILY_EXCLUSION}.")
    if not cap_ok:
        verdict = VERDICT_INELIGIBLE
        batches = -(-size // GROUP_SIZE_CAP)
        reasons.append(
            f"a group request is capped at {GROUP_SIZE_CAP} travellers; this "
            f"roster has {size}. Split it into {batches} requests of at most "
            f"{GROUP_SIZE_CAP}.")

    not_ready = [m["name"] or m["case_id"] or f"member {i + 1}"
                 for i, m in enumerate(members) if not m["ready"]]
    ready_count = size - len(not_ready)
    if not_ready:
        reasons.append(
            f"{len(not_ready)} of {size} members are missing details the group "
            f"request requires; each member's 'missing' list names them.")

    eligible = verdict == VERDICT_ELIGIBLE
    roster = {
        "eligible": eligible,
        "verdict": verdict,
        "reasons": reasons,
        "members": members,
        "size": size,
        "cap_ok": cap_ok,
        "min_size": MIN_GROUP_SIZE,
        "cap": GROUP_SIZE_CAP,
        "group_kind": _token(group_kind),
        "family_group": family,
        "ready_count": ready_count,
        "not_ready": not_ready,
        "submittable": bool(eligible and size and ready_count == size),
        "batches": [[m["case_id"] for m in members[i:i + GROUP_SIZE_CAP]]
                    for i in range(0, size, GROUP_SIZE_CAP)],
        "identifiers_included": bool(include_passport_numbers),
        "official_notes": [OFFICIAL_MIN_SIZE, OFFICIAL_FAMILY_EXCLUSION,
                           OFFICIAL_OWN_DS160, OFFICIAL_NO_AGENT_SCHEDULING],
        "human_action": {
            "actor": "group coordinator (a human, signed in themselves)",
            "step": "submit the Group Scheduling Request in the consular scheduler",
            "then": "after approval, the coordinator books each member's slot",
            "ellis_role": ("prepares and checks the roster; it never searches "
                           "for or books an appointment slot"),
        },
        "automation": dict(NO_AUTOMATION),
    }
    return record_expiration(roster, expires_on, today=today)


# ---------------------------------------------------------------------------
# The artifact the coordinator submits.
# ---------------------------------------------------------------------------

COLUMNS = ["no", "name", "passport_number", "ds160_confirmation",
           "mrv_receipt", "ready", "missing"]

_ARTIFACT_TITLE = "US Group Scheduling Request - roster"


def _passport_cell(member: dict) -> str:
    if member.get("passport_number"):
        return str(member["passport_number"])
    return "on file" if member.get("passport_number_present") else "MISSING"


def _rows(roster: dict) -> list[dict]:
    rows = []
    for index, member in enumerate(roster.get("members") or [], start=1):
        rows.append({
            "no": index,
            "name": member.get("name") or "",
            "passport_number": _passport_cell(member),
            "ds160_confirmation": member.get("ds160_confirmation") or "MISSING",
            "mrv_receipt": member.get("mrv_receipt") or "MISSING",
            "ready": "yes" if member.get("ready") else "no",
            "missing": "; ".join(member.get("missing") or []),
        })
    return rows


def _csv_safe(value) -> str:
    """A roster is opened in a spreadsheet by the coordinator, and a name
    beginning with '=' would be run as a formula there. Neutralize the lead
    character; everything else is left exactly as it is."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([_csv_safe(row.get(col, "")) for col in COLUMNS])
    return buf.getvalue()


# Typography pdfgen's latin-1 encoder cannot carry but that carries no meaning:
# transliterated so a dash never becomes a "lost characters" warning.
_TYPOGRAPHY = {"—": " - ", "–": "-", "‘": "'", "’": "'",
               "“": '"', "”": '"', "…": "...", " ": " ",
               "•": "*"}


def _pdf_text(value: str) -> str:
    """pdfgen writes latin-1. Punctuation it cannot carry is transliterated;
    a Chinese tour group's names cannot be transliterated, so what is lost is
    SAID to be lost — the CSV carries the real name — rather than printed as a
    row of question marks or silently blanked."""
    text = str(value or "")
    for bad, good in _TYPOGRAPHY.items():
        text = text.replace(bad, good)
    kept = text.encode("latin-1", "ignore").decode("latin-1")
    if kept == text:
        return text
    return (kept + " [see CSV]").strip() if kept.strip() else "[see CSV for name]"


def _wrap(text: str, indent: str = "  ") -> list[str]:
    """Wrap to the page width pdfgen's 11pt Helvetica actually fits."""
    return textwrap.wrap(_pdf_text(text), width=88, initial_indent=indent,
                         subsequent_indent=indent + "  ") or [indent.rstrip()]


def _cell(value: str, width: int) -> str:
    text = str(value or "")
    if len(text) > width:
        text = text[:width - 1] + "."
    return text.ljust(width)


_LINES_PER_PAGE = 58


def _text_pdf_pages(lines: list[str], *, title: str) -> bytes:
    pages = [lines[i:i + _LINES_PER_PAGE]
             for i in range(0, len(lines), _LINES_PER_PAGE)] or [[""]]
    if len(pages) == 1:
        return pdfgen.text_pdf(pages[0], title=title)
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for page in pages:
        writer.append(PdfReader(io.BytesIO(pdfgen.text_pdf(page, title=title))))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _status_line(roster: dict) -> str:
    if roster.get("submittable"):
        return "READY FOR THE COORDINATOR TO SUBMIT"
    if roster.get("verdict") == VERDICT_INELIGIBLE:
        return "DO NOT SUBMIT - this group does not qualify for a group appointment"
    if roster.get("verdict") == VERDICT_UNVERIFIED:
        return "DO NOT SUBMIT YET - group eligibility is unverified"
    return "NOT READY - members are missing required details"


def _artifact_lines(roster: dict, rows: list[dict], title: str) -> list[str]:
    expiry = roster.get("expiry") or {}
    lines = [
        title,
        "=" * 72,
        "Prepared by Ellis for the group coordinator to submit in person.",
        "Ellis does not search for, hold, or book appointment slots.",
        "",
        f"STATUS: {_status_line(roster)}",
        "",
        "GROUP",
        f"  Travellers on this roster : {roster.get('size', 0)} "
        f"(minimum {roster.get('min_size', MIN_GROUP_SIZE)}, "
        f"maximum {roster.get('cap', GROUP_SIZE_CAP)})",
        f"  Declared group type       : {roster.get('group_kind') or 'not declared'}",
        f"  Eligibility               : {roster.get('verdict', VERDICT_UNVERIFIED)}",
        f"  Members ready             : {roster.get('ready_count', 0)}"
        f" of {roster.get('size', 0)}",
        f"  Request expiry            : "
        f"{expiry.get('display') or 'not recorded'}",
    ]
    if expiry.get("note"):
        lines += _wrap(expiry["note"], indent="  ")
    if roster.get("reasons"):
        lines += ["", "WHAT THE CONSULATE WOULD SAY ABOUT THIS ROSTER"]
        for reason in roster["reasons"]:
            lines += _wrap(f"- {reason}", indent="  ")
    if not roster.get("identifiers_included"):
        lines += [""] + _wrap(
            "Passport numbers are withheld from this copy; the roster records "
            "only that each one is on file. Regenerate with "
            "include_passport_numbers=True to print them for transcription.",
            indent="  ")

    lines += ["", "MEMBERS", "  " + _cell("#", 4) + _cell("Name", 26)
              + _cell("Passport", 14) + _cell("DS-160", 12)
              + _cell("MRV receipt", 16) + "Ready",
              "  " + "-" * 76]
    for row in rows:
        lines.append(
            "  " + _cell(str(row["no"]), 4) + _cell(_pdf_text(row["name"]), 26)
            + _cell(_pdf_text(row["passport_number"]), 14)
            + _cell(row["ds160_confirmation"], 12)
            + _cell(_pdf_text(row["mrv_receipt"]), 16) + row["ready"])

    chase = [row for row in rows if row["missing"]]
    if chase:
        lines += ["", "CHASE THESE BEFORE SUBMITTING"]
        for row in chase:
            lines.append(f"  {row['no']}. {_pdf_text(row['name']) or '(unnamed)'}")
            for item in row["missing"].split("; "):
                lines += _wrap(f"- {item}", indent="       ")

    lines += [
        "",
        "WHAT HAPPENS NEXT - every step below is a human's",
        "  1. The group coordinator signs in and submits this Group Scheduling",
        "     Request themselves. " + OFFICIAL_NO_AGENT_SCHEDULING,
        "  2. " + OFFICIAL_OWN_DS160[0].upper() + OFFICIAL_OWN_DS160[1:] + ".",
        "  3. The consulate approves the request; the approval expires.",
        "  4. After approval the coordinator books each member's slot.",
        "  5. If the approval lapses, the coordinator requests a new one.",
        "",
        "Ellis never searches for or books an appointment slot: automated slot",
        "search gets the traveller's appointment and visa cancelled.",
    ]
    return lines


def export_group_request(roster: dict, *, title: str = _ARTIFACT_TITLE) -> dict:
    """The human-submittable artifact: a structured table plus a printable PDF.

    Rendered for ANY roster, including one that does not qualify — the chase
    list is most of its value — but a roster that must not be submitted says so
    on its first page, so the artifact can never be mistaken for a filing.
    """
    roster = roster or {}
    rows = _rows(roster)
    lines = _artifact_lines(roster, rows, title)
    return {
        "kind": "us_group_scheduling_request",
        "title": title,
        "status": _status_line(roster),
        "submittable": bool(roster.get("submittable")),
        "verdict": roster.get("verdict", VERDICT_UNVERIFIED),
        "size": roster.get("size", 0),
        "expiry": roster.get("expiry") or {},
        "columns": list(COLUMNS),
        "rows": rows,
        "csv": _csv_text(rows),
        "lines": lines,
        "pdf": _text_pdf_pages(lines, title=title),
        "filename": "us-group-scheduling-request.pdf",
        "csv_filename": "us-group-scheduling-request.csv",
        "submitted_by": "the group coordinator, by hand",
        "ellis_role": ("prepared and checked this roster; it does not search "
                       "for or book appointment slots"),
    }
