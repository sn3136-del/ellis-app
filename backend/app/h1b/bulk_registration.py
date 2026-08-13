"""USCIS Organizational Account bulk registration: Ellis GENERATES the file, a
human uploads it.

There is no USCIS registration API. The myUSCIS organizational account accepts
a bulk upload of up to 2,500 beneficiaries per file; the only automatable half
is producing a file that is *already correct*, so the human's step is "review,
sign in, upload" rather than "retype 2,500 rows".

Doctrine enforced here rather than trusted to callers:

- NOTHING IS AUTO-FILLED. Every cell is a party's own recorded answer (or a
  fact extracted from an ACCEPTED document). A missing or unparseable value is
  reported as a per-row error and the cell stays EMPTY. A registration is a
  statement to USCIS under 8 CFR 214.2(h)(8)(iii); unfilled beats wrong, and a
  guessed value is never written. In particular a full name is NEVER split into
  surname/given names — which token is the family name is a guess Ellis will
  not make on a government file.
- NOTHING IS SILENTLY DROPPED. Every requested case appears in `rows` with its
  errors. Rows that are not ready are excluded from the uploadable file by
  default (so the human never uploads a batch USCIS will reject) and named
  explicitly in `excluded`; `include_invalid=True` writes them with blank cells
  and a loud caveat instead.
- THE TEMPLATE IS CURATED DATA. The column set carries a source URL and an
  as_of date and every payload says plainly that the operator must diff it
  against the template USCIS publishes for the season before uploading. Ellis
  cannot fetch the template (it lives behind the organizational-account login,
  which is a personal act).
- ELLIS NEVER REGISTERS. No submission, no login, no payment of the $215
  registration fee. This module ends at bytes on disk.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from typing import Iterable

from sqlalchemy import select

from .. import models
from ..dates import normalize_any, to_display
from . import models as h1b_models

# ---------------------------------------------------------------------------
# Curated template facts. Re-verify against the live template each season.
# ---------------------------------------------------------------------------

TEMPLATE_VERSION = "uscis-org-account-bulk-registration-2026-08-11"
TEMPLATE_AS_OF = "2026-08-11"
TEMPLATE_SOURCE = ("https://www.uscis.gov/working-in-the-united-states/"
                   "temporary-workers/h-1b-specialty-occupations/"
                   "h-1b-electronic-registration-process")
WEIGHTED_RULE_SOURCE = "https://www.federalregister.gov/d/2025-23853"
WEIGHTED_RULE_EFFECTIVE = "2026-02-27"

# 8 CFR 214.2(h)(8)(iii) / USCIS org-account bulk upload: one file, 2,500 rows.
MAX_BENEFICIARIES = 2500

# The weighted selection rule (published 2025-12-29, effective 2026-02-27)
# first applies to the FY2027 cap: the registration must state the offered
# wage, SOC code, OEWS wage level and worksite. Earlier fiscal years collect
# identity only.
FIRST_WEIGHTED_FISCAL_YEAR = 2027

TEMPLATE_CAVEAT = (
    "Ellis generates this file from the case record; it does not upload it. "
    "Before uploading, diff these column headers against the bulk-upload "
    "template your myUSCIS organizational account offers for this fiscal year "
    f"(column set curated {TEMPLATE_AS_OF} from {TEMPLATE_SOURCE}). USCIS "
    "revises the template between seasons and Ellis cannot read it — it lives "
    "behind your account login.")

UPLOAD_INSTRUCTIONS = (
    "1. Review every row, especially any row Ellis could not complete. "
    "2. Sign in to your myUSCIS organizational account yourself. "
    "3. Upload this file in the H-1B registration bulk-upload screen. "
    "4. Pay the registration fee and submit yourself. Ellis never signs in, "
    "uploads, pays, or submits on your behalf.")


class BulkLimitExceeded(ValueError):
    """More beneficiaries than one USCIS bulk file may carry."""

    def __init__(self, requested: int):
        self.requested = requested
        self.limit = MAX_BENEFICIARIES
        super().__init__(
            f"a USCIS bulk registration file carries at most "
            f"{MAX_BENEFICIARIES} beneficiaries; {requested} were requested. "
            f"Split them across files.")


# ---------------------------------------------------------------------------
# Column set.
# ---------------------------------------------------------------------------

class Column:
    """One template column. `group` is 'identity' (always collected) or
    'weighted' (required only from FIRST_WEIGHTED_FISCAL_YEAR onward)."""

    __slots__ = ("key", "header", "required", "group", "asked_of", "answer_keys")

    def __init__(self, key: str, header: str, *, required: bool, group: str,
                 asked_of: str, answer_keys: tuple[str, ...] = ()):
        self.key = key
        self.header = header
        self.required = required
        self.group = group
        self.asked_of = asked_of          # which party owns the fact
        self.answer_keys = answer_keys or (key,)

    def as_dict(self) -> dict:
        return {"key": self.key, "header": self.header,
                "required": self.required, "group": self.group,
                "asked_of": self.asked_of}


COLUMNS: tuple[Column, ...] = (
    Column("beneficiary_last_name", "Beneficiary Last/Family Name",
           required=True, group="identity", asked_of="beneficiary",
           answer_keys=("surname", "last_name", "family_name")),
    Column("beneficiary_first_name", "Beneficiary First/Given Name",
           required=True, group="identity", asked_of="beneficiary",
           answer_keys=("given_names", "first_name")),
    Column("beneficiary_middle_name", "Beneficiary Middle Name",
           required=False, group="identity", asked_of="beneficiary",
           answer_keys=("middle_name",)),
    Column("beneficiary_gender", "Beneficiary Gender", required=True,
           group="identity", asked_of="beneficiary",
           answer_keys=("sex", "gender")),
    Column("beneficiary_date_of_birth", "Beneficiary Date of Birth (MM/DD/YYYY)",
           required=True, group="identity", asked_of="beneficiary",
           answer_keys=("birth_date", "date_of_birth")),
    Column("beneficiary_country_of_birth", "Beneficiary Country of Birth",
           required=True, group="identity", asked_of="beneficiary",
           answer_keys=("birth_country", "country_of_birth")),
    Column("beneficiary_country_of_citizenship",
           "Beneficiary Country of Citizenship", required=True,
           group="identity", asked_of="beneficiary",
           answer_keys=("citizenship_country", "nationality",
                        "passport_nationality")),
    Column("passport_number", "Passport or Travel Document Number",
           required=True, group="identity", asked_of="beneficiary",
           answer_keys=("passport_number",)),
    Column("passport_expiration_date",
           "Passport or Travel Document Expiration Date (MM/DD/YYYY)",
           required=True, group="identity", asked_of="beneficiary",
           answer_keys=("passport_expiry", "passport_expiry_date",
                        "expiry_date")),
    Column("passport_country_of_issuance",
           "Passport or Travel Document Country of Issuance", required=True,
           group="identity", asked_of="beneficiary",
           answer_keys=("passport_issuing_country", "issuing_country")),
    # --- weighted-selection columns (FY2027+) ------------------------------
    Column("soc_code", "SOC Code", required=True, group="weighted",
           asked_of="petitioner", answer_keys=("soc_code",)),
    Column("offered_wage", "Offered Wage", required=True, group="weighted",
           asked_of="petitioner", answer_keys=("wage_offer",)),
    Column("offered_wage_unit", "Offered Wage Unit", required=True,
           group="weighted", asked_of="petitioner",
           answer_keys=("wage_offer_unit",)),
    Column("oews_wage_level", "OEWS Wage Level", required=True,
           group="weighted", asked_of="petitioner", answer_keys=("wage_level",)),
    Column("worksite_city", "Worksite City", required=True, group="weighted",
           asked_of="petitioner", answer_keys=("worksite_city",)),
    Column("worksite_state", "Worksite State", required=True, group="weighted",
           asked_of="petitioner", answer_keys=("worksite_state",)),
    Column("worksite_postal_code", "Worksite ZIP Code", required=False,
           group="weighted", asked_of="petitioner",
           answer_keys=("worksite_postal_code",)),
)

COLUMNS_BY_KEY = {c.key: c for c in COLUMNS}


def columns_for(fiscal_year: int) -> tuple[Column, ...]:
    if fiscal_year >= FIRST_WEIGHTED_FISCAL_YEAR:
        return COLUMNS
    return tuple(c for c in COLUMNS if c.group == "identity")


# ---------------------------------------------------------------------------
# Value vocabularies (curated; normalization is a transform, never a guess).
# ---------------------------------------------------------------------------

_GENDER_VALUES = {
    "m": "Male", "male": "Male", "f": "Female", "female": "Female",
    "x": "X",
}
# X is accepted because some USCIS forms now carry it; the template may not.
_GENDER_NEEDS_CONFIRMATION = ("X",)

_WAGE_LEVELS = {
    "i": "I", "1": "I", "level i": "I", "level 1": "I",
    "ii": "II", "2": "II", "level ii": "II", "level 2": "II",
    "iii": "III", "3": "III", "level iii": "III", "level 3": "III",
    "iv": "IV", "4": "IV", "level iv": "IV", "level 4": "IV",
    "below level i": "Below Level I", "below_level_i": "Below Level I",
    "below i": "Below Level I", "below 1": "Below Level I",
}

# Weighted rule: entries per registration by OEWS level of the OFFERED wage.
SELECTION_ENTRIES = {"IV": 4, "III": 3, "II": 2, "I": 1, "Below Level I": 1}

_WAGE_UNITS = {
    "year": "Year", "yearly": "Year", "annual": "Year", "annually": "Year",
    "yr": "Year", "hour": "Hour", "hourly": "Hour", "hr": "Hour",
    "month": "Month", "monthly": "Month", "week": "Week", "weekly": "Week",
    "bi-weekly": "Bi-Weekly", "biweekly": "Bi-Weekly",
}

# USPS codes USCIS accepts as a worksite state (50 states + DC + territories).
_US_STATES = frozenset("""
AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO
MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
AS GU MP PR VI
""".split())

# A name as it appears in a machine-readable travel document. Deliberately
# permissive on scripts (a name is the beneficiary's own) but rejects digits
# and template junk that would land on a federal filing.
_NAME_BAD = re.compile(r"[0-9<>@#$%^*_=+\\|{}\[\]]")
_SOC_RE = re.compile(r"^\d{2}-\d{4}$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_PASSPORT_RE = re.compile(r"^[A-Za-z0-9]{5,20}$")


def _today() -> _dt.date:  # seam so tests are deterministic
    return _dt.date.today()


def next_cap_fiscal_year(today: _dt.date | None = None) -> int:
    """The fiscal year the NEXT cap registration season registers for.

    Registration for FY N runs in ~March of calendar year N-1 (FY2027
    registered March 2026; FY2028 registers ~March 2027). On or before
    March 31 the current calendar year's season is the live one."""
    today = today or _today()
    return today.year + (1 if today.month <= 3 else 2)


# ---------------------------------------------------------------------------
# Fact sourcing.
# ---------------------------------------------------------------------------

def _accepted_passport_fields(db, case_id: str) -> dict:
    """extracted_fields of an ACCEPTED (approved or explicitly submitted)
    passport document — never a bare upload, never a rejected page."""
    submitted = {s.document_id for s in db.execute(
        select(models.ChecklistSubmission).where(
            models.ChecklistSubmission.application_id == case_id,
            models.ChecklistSubmission.status == "submitted")).scalars().all()}
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == case_id,
        models.StoredDocument.doc_type == "passport")).scalars().all()
    for doc in docs:
        if (doc.page_classification or {}).get("reject"):
            continue
        if doc.approved or doc.id in submitted:
            return dict(doc.extracted_fields or {})
    return {}


# Passport extracted_fields alias -> template column key.
_PASSPORT_FIELD_ALIASES = {
    "beneficiary_last_name": ("surname",),
    "beneficiary_first_name": ("given_names",),
    "beneficiary_date_of_birth": ("birth_date", "date_of_birth"),
    "beneficiary_gender": ("sex",),
    "beneficiary_country_of_citizenship": ("nationality",),
    "passport_number": ("passport_number",),
    "passport_expiration_date": ("passport_expiry", "expiry_date"),
    "passport_country_of_issuance": ("issuing_country",),
}


def _raw_value(column: Column, *, parent_answers: dict, petitioner_answers: dict,
               passport_fields: dict) -> tuple[object, str]:
    """(value, provenance) for one column, or ('', '') when nothing is on
    file. Party-correct: identity columns never read petitioner answers and
    weighted columns never read beneficiary answers."""
    if column.asked_of == "petitioner":
        for key in column.answer_keys:
            value = petitioner_answers.get(key)
            if value not in (None, ""):
                return value, f"party_answers.petitioner.{key}"
        return "", ""
    for key in column.answer_keys:
        value = parent_answers.get(key)
        if value not in (None, ""):
            return value, f"case_answers.{key}"
    for key in _PASSPORT_FIELD_ALIASES.get(column.key, ()):
        value = passport_fields.get(key)
        if value not in (None, ""):
            return value, f"accepted_passport.{key}"
    return "", ""


# ---------------------------------------------------------------------------
# Per-cell validation. Each returns (cell_text, error|None, caveat|None).
# A validator NEVER repairs a value it cannot read — it reports and blanks.
# ---------------------------------------------------------------------------

def _err(column: str, code: str, message: str) -> dict:
    return {"column": column, "code": code, "message": message}


def _v_name(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = " ".join(str(raw).split())
    if _NAME_BAD.search(text):
        return "", _err(col.key, "unusable_characters",
                        f"{col.header}: contains characters that cannot appear "
                        f"in a name on a federal filing; correct it on the "
                        f"case and regenerate"), None
    if len(text) > 60:
        return "", _err(col.key, "too_long",
                        f"{col.header}: longer than the 60 characters the "
                        f"template accepts; confirm the passport spelling"), None
    return text, None, None


def _v_gender(col: Column, raw) -> tuple[str, dict | None, str | None]:
    mapped = _GENDER_VALUES.get(str(raw).strip().lower())
    if not mapped:
        return "", _err(col.key, "unmappable_value",
                        f"{col.header}: {str(raw)!r} is not a value USCIS "
                        f"accepts; ask the beneficiary rather than guessing"), None
    caveat = None
    if mapped in _GENDER_NEEDS_CONFIRMATION:
        caveat = (f"{col.header} is 'X'. Confirm this season's template accepts "
                  f"it before uploading; USCIS has not carried X on every form.")
    return mapped, None, caveat


def _v_past_date(col: Column, raw) -> tuple[str, dict | None, str | None]:
    iso = normalize_any(raw, kind="birth", us_numeric=True)
    if not iso:
        return "", _err(col.key, "unparseable_date",
                        f"{col.header}: {str(raw)!r} is not a date Ellis can "
                        f"read; it is never guessed onto a federal file"), None
    if _dt.date.fromisoformat(iso) >= _today():
        return "", _err(col.key, "date_not_in_past",
                        f"{col.header}: {to_display(iso)} is not in the past"), None
    return to_display(iso), None, None


def _v_expiry_date(col: Column, raw) -> tuple[str, dict | None, str | None]:
    iso = normalize_any(raw, kind="expiry", us_numeric=True)
    if not iso:
        return "", _err(col.key, "unparseable_date",
                        f"{col.header}: {str(raw)!r} is not a date Ellis can "
                        f"read; it is never guessed onto a federal file"), None
    if _dt.date.fromisoformat(iso) < _today():
        # A fact, not a verdict: the recorded document has expired.
        return "", _err(col.key, "passport_expired",
                        f"{col.header}: the recorded passport expired on "
                        f"{to_display(iso)}. A registration states a valid "
                        f"travel document; record the current passport"), None
    return to_display(iso), None, None


def _v_country(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = " ".join(str(raw).split())
    try:
        from ..visa_snapshot.registry import _country_index
        entry = (_country_index().get(text.upper())
                 or _country_index().get(text.lower()))
    except Exception:  # noqa: BLE001 — registry unavailable: no normalization
        return text, None, (f"{col.header}: Ellis could not reach its country "
                            f"registry, so {text!r} is written through "
                            f"unchecked. Verify it against the template list.")
    if entry is None:
        return "", _err(col.key, "unknown_country",
                        f"{col.header}: {text!r} is not a country Ellis "
                        f"recognizes; USCIS uses its own country list, so the "
                        f"value must be corrected on the case"), None
    return entry["name"], None, None


def _v_passport_number(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = str(raw).strip().replace(" ", "").replace("<", "")
    if not _PASSPORT_RE.match(text):
        return "", _err(col.key, "unusable_passport_number",
                        f"{col.header}: {text!r} is not a usable travel-document "
                        f"number (letters and digits, 5-20 characters)"), None
    return text.upper(), None, None


def _v_soc(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = str(raw).strip()
    if not _SOC_RE.match(text):
        return "", _err(col.key, "malformed_soc_code",
                        f"{col.header}: {text!r} is not an SOC code in "
                        f"##-#### form"), None
    caveat = None
    try:                                  # official-title check, degrades quietly
        from . import wage_data
        if wage_data.is_available() and not wage_data.soc_title(text):
            caveat = (f"{col.header} {text} is not in the DOL OFLC SOC list "
                      f"Ellis has cached; confirm it is the code on the LCA.")
    except Exception:  # noqa: BLE001 — the seam is advisory only
        caveat = None
    return text, None, caveat


def _v_wage(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = str(raw).strip().replace(",", "").replace("$", "")
    try:
        amount = float(text)
    except ValueError:
        return "", _err(col.key, "unparseable_wage",
                        f"{col.header}: {str(raw)!r} is not a number"), None
    if amount <= 0:
        return "", _err(col.key, "wage_not_positive",
                        f"{col.header}: must be greater than zero"), None
    return (f"{amount:.2f}" if amount % 1 else str(int(amount))), None, None


def _v_wage_unit(col: Column, raw) -> tuple[str, dict | None, str | None]:
    mapped = _WAGE_UNITS.get(str(raw).strip().lower())
    if not mapped:
        return "", _err(col.key, "unmappable_value",
                        f"{col.header}: {str(raw)!r} is not a pay period USCIS "
                        f"accepts"), None
    return mapped, None, None


def _v_wage_level(col: Column, raw) -> tuple[str, dict | None, str | None]:
    mapped = _WAGE_LEVELS.get(str(raw).strip().lower())
    if not mapped:
        return "", _err(col.key, "unmappable_value",
                        f"{col.header}: {str(raw)!r} is not an OEWS wage "
                        f"level (I, II, III, IV, or Below Level I)"), None
    caveat = None
    if mapped == "Below Level I":
        caveat = ("A below-Level-I offered wage is permitted only with "
                  "alternative wage-source documentation and receives one "
                  f"selection entry ({WEIGHTED_RULE_SOURCE}).")
    return mapped, None, caveat


def _v_state(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = str(raw).strip().upper()
    if text not in _US_STATES:
        return "", _err(col.key, "unknown_state",
                        f"{col.header}: {str(raw)!r} is not a USPS state or "
                        f"territory code"), None
    return text, None, None


def _v_zip(col: Column, raw) -> tuple[str, dict | None, str | None]:
    text = str(raw).strip()
    if not _ZIP_RE.match(text):
        return "", _err(col.key, "malformed_zip",
                        f"{col.header}: {text!r} is not a US ZIP code"), None
    return text, None, None


def _v_text(col: Column, raw) -> tuple[str, dict | None, str | None]:
    return " ".join(str(raw).split()), None, None


VALIDATORS = {
    "beneficiary_last_name": _v_name,
    "beneficiary_first_name": _v_name,
    "beneficiary_middle_name": _v_name,
    "beneficiary_gender": _v_gender,
    "beneficiary_date_of_birth": _v_past_date,
    "beneficiary_country_of_birth": _v_country,
    "beneficiary_country_of_citizenship": _v_country,
    "passport_number": _v_passport_number,
    "passport_expiration_date": _v_expiry_date,
    "passport_country_of_issuance": _v_country,
    "soc_code": _v_soc,
    "offered_wage": _v_wage,
    "offered_wage_unit": _v_wage_unit,
    "oews_wage_level": _v_wage_level,
    "worksite_city": _v_text,
    "worksite_state": _v_state,
    "worksite_postal_code": _v_zip,
}


# ---------------------------------------------------------------------------
# Row build.
# ---------------------------------------------------------------------------

def _build_row(db, case, columns: tuple[Column, ...]) -> dict:
    parent_answers = dict(case.answers or {})
    petitioner = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == case.id,
        h1b_models.CaseParty.role == "petitioner")).scalars().first()
    petitioner_answers = dict((petitioner.answers or {}) if petitioner else {})
    passport_fields = _accepted_passport_fields(db, case.id)

    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    errors: list[dict] = []
    caveats: list[str] = []

    for column in columns:
        raw, provenance = _raw_value(
            column, parent_answers=parent_answers,
            petitioner_answers=petitioner_answers,
            passport_fields=passport_fields)
        if raw in (None, ""):
            values[column.key] = ""
            if column.required:
                errors.append(_err(
                    column.key, "missing_required_value",
                    f"{column.header}: not on file. Ask the {column.asked_of} "
                    f"for it — Ellis never invents a value that becomes a "
                    f"statement to USCIS."))
            continue
        cell, error, caveat = VALIDATORS[column.key](column, raw)
        values[column.key] = cell
        sources[column.key] = provenance if cell else ""
        if error:
            errors.append(error)
        if caveat:
            caveats.append(caveat)

    return {"case_id": case.id, "values": values, "sources": sources,
            "errors": errors, "caveats": caveats}


def _case_kind(case) -> str:
    return str((case.answers or {}).get("h1b_case_kind") or "")


def _load_cases(db, org_id: str, case_ids: Iterable[str]) -> dict:
    ids = list(case_ids)
    if not ids:
        return {}
    rows = db.execute(select(models.VisaApplication).where(
        models.VisaApplication.id.in_(ids))).scalars().all()
    return {r.id: r for r in rows if r.org_id == org_id and r.visa_type == "h1b"}


def _csv_bytes(columns: tuple[Column, ...], rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([c.header for c in columns])
    for row in rows:
        writer.writerow([row["values"].get(c.key, "") for c in columns])
    # utf-8-sig: Excel misreads a plain-UTF-8 CSV of non-Latin names, and the
    # beneficiary's own spelling of their name must survive the round trip.
    return buf.getvalue().encode("utf-8-sig")


def _xlsx_bytes(columns: tuple[Column, ...], rows: list[dict]) -> tuple[bytes | None, str]:
    """XLSX when openpyxl is installed, else (None, reason). Ellis does not
    hand-roll a spreadsheet container: a malformed workbook that USCIS silently
    misreads is worse than an honest CSV."""
    try:
        from openpyxl import Workbook           # type: ignore
    except Exception:  # noqa: BLE001
        return None, ("openpyxl is not installed, so Ellis produced CSV only. "
                      "The USCIS bulk upload accepts CSV.")
    wb = Workbook()
    ws = wb.active
    ws.title = "Beneficiaries"
    ws.append([c.header for c in columns])
    for row in rows:
        ws.append([row["values"].get(c.key, "") for c in columns])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), ""


def build_workbook(db, org_id: str, case_ids, *, fiscal_year: int | None = None,
                   include_invalid: bool = False) -> dict:
    """Build the USCIS bulk-registration file for `case_ids` under `org_id`.

    Returns {rows, errors_by_row, csv_bytes, xlsx_bytes, ready_count, ...}.
    Row numbers are 1-based DATA rows (the header is row 0 in the file).

    Raises BulkLimitExceeded above MAX_BENEFICIARIES — the cap is USCIS's, and
    a silently truncated file would drop real beneficiaries.
    """
    ids = list(case_ids or [])
    if len(ids) > MAX_BENEFICIARIES:
        raise BulkLimitExceeded(len(ids))

    fy = int(fiscal_year) if fiscal_year else next_cap_fiscal_year()
    columns = columns_for(fy)
    weighted = fy >= FIRST_WEIGHTED_FISCAL_YEAR
    found = _load_cases(db, org_id, ids)

    rows: list[dict] = []
    errors_by_row: dict[int, list[dict]] = {}
    seen_beneficiary: dict[tuple[str, str], int] = {}

    for index, case_id in enumerate(ids, start=1):
        case = found.get(case_id)
        if case is None:
            row = {"row": index, "case_id": case_id, "values": {},
                   "sources": {}, "caveats": [],
                   "errors": [_err("", "case_not_available",
                                   "no H-1B case with this id in your "
                                   "organization")]}
        elif _case_kind(case) != "cap_initial":
            row = {"row": index, "case_id": case_id, "values": {},
                   "sources": {}, "caveats": [],
                   "errors": [_err("", "not_a_cap_case",
                                   f"case kind is "
                                   f"{_case_kind(case) or 'unset'}; only "
                                   f"cap_initial cases are registered in the "
                                   f"H-1B cap lottery")]}
        else:
            row = {"row": index, **_build_row(db, case, columns)}

        # Beneficiary-centric selection (one registration per beneficiary per
        # employer per FY): a duplicate inside one file is a real integrity
        # defect USCIS treats as invalidating, so it is an error, not a silent
        # de-dup.
        key = (row["values"].get("passport_number", ""),
               row["values"].get("beneficiary_date_of_birth", ""))
        if all(key):
            first = seen_beneficiary.get(key)
            if first is not None:
                row["errors"].append(_err(
                    "passport_number", "duplicate_beneficiary_in_batch",
                    f"the same beneficiary already appears on row {first}; "
                    f"duplicate registrations for one beneficiary by one "
                    f"employer invalidate the registrations"))
            else:
                seen_beneficiary[key] = index

        row["ready"] = not row["errors"]
        if row["errors"]:
            errors_by_row[index] = row["errors"]
        rows.append(row)

    ready_rows = [r for r in rows if r["ready"]]
    file_rows = rows if include_invalid else ready_rows
    csv_bytes = _csv_bytes(columns, file_rows)
    xlsx_bytes, xlsx_note = _xlsx_bytes(columns, file_rows)

    caveats = [TEMPLATE_CAVEAT]
    if weighted:
        caveats.append(
            "FY%d is a weighted-selection year (rule effective %s): the "
            "registration states the offered wage, SOC code, OEWS wage level "
            "and worksite, and those must match the LCA and I-129 exactly. A "
            "mismatch is a denial or revocation ground (%s)."
            % (fy, WEIGHTED_RULE_EFFECTIVE, WEIGHTED_RULE_SOURCE))
    if include_invalid and len(file_rows) != len(ready_rows):
        caveats.append(
            "This file INCLUDES rows Ellis could not complete; their cells are "
            "blank. USCIS will reject them. Fix them or regenerate without "
            "include_invalid.")
    if xlsx_note:
        caveats.append(xlsx_note)
    for row in rows:
        caveats.extend(row.get("caveats") or [])

    entries = 0
    if weighted:
        for row in ready_rows:
            entries += SELECTION_ENTRIES.get(
                row["values"].get("oews_wage_level", ""), 0)

    return {
        "org_id": org_id,
        "fiscal_year": fy,
        "weighted_selection": weighted,
        "template_version": TEMPLATE_VERSION,
        "template_as_of": TEMPLATE_AS_OF,
        "template_source": TEMPLATE_SOURCE,
        "columns": [c.as_dict() for c in columns],
        "rows": rows,
        "errors_by_row": errors_by_row,
        "ready_count": len(ready_rows),
        "requested_count": len(ids),
        "max_beneficiaries": MAX_BENEFICIARIES,
        "excluded": [{"row": r["row"], "case_id": r["case_id"],
                      "codes": sorted({e["code"] for e in r["errors"]})}
                     for r in rows if not r["ready"]],
        "rows_in_file": len(file_rows),
        "csv_bytes": csv_bytes,
        "xlsx_bytes": xlsx_bytes,
        "format": "csv",
        "filename": f"h1b-registration-FY{fy}-{len(file_rows)}-beneficiaries.csv",
        # Mechanical restatement of the weighted rule, for the operator's
        # planning only — never a prediction of selection.
        "selection_entries_total": entries if weighted else None,
        "caveats": caveats,
        "upload_instructions": UPLOAD_INSTRUCTIONS,
    }
