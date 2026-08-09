"""H1B petition field vocabulary (H1B_ARCHITECTURE, "Portal families and
adapters"): the vocabulary must exist BEFORE the first FLAG/myUSCIS
observation, because the specgen validator rejects any mapping onto an
unknown Ellis field. Attestation keys are Yes/No chips at every ask site,
never a text box; employment dates ride the canonical ISO pipeline with the
USCIS/FLAG 'MM/DD/YYYY' portal format; middle_name is asked, never split
out of given_names — a guessed middle name becomes a wrong statement on a
government form."""
import inspect
import re
from pathlib import Path

from app import dates
from app.adapter_factory import runtime
from app.adapter_factory.specgen import ELLIS_FIELDS, KEY_QUESTIONS

H1B_FIELDS = [
    "employer_legal_name", "employer_dba", "employer_fein", "employer_naics",
    "employer_contact_name", "employer_contact_email", "employer_contact_phone",
    "job_title", "soc_code", "soc_title", "wage_offer", "wage_offer_unit",
    "prevailing_wage", "pw_tracking_number",
    "worksite_address_line1", "worksite_address_city",
    "worksite_address_state", "worksite_address_zip",
    "employment_start_date", "employment_end_date",
    "full_time_position", "h1b_dependent_employer", "willful_violator",
    "middle_name", "birth_country", "citizenship_country",
]

ATTESTATION_KEYS = ("full_time_position", "h1b_dependent_employer",
                    "willful_violator")

APP_DIR = Path(__file__).resolve().parents[1] / "app"


# ---------- the vocabulary exists ----------

def test_every_h1b_petition_field_is_canonical():
    missing = [k for k in H1B_FIELDS if k not in ELLIS_FIELDS]
    assert not missing, f"unmapped fields would be rejected as unknown: {missing}"


def test_the_vocabulary_has_no_duplicates():
    dupes = [k for k in set(ELLIS_FIELDS) if ELLIS_FIELDS.count(k) > 1]
    assert not dupes, dupes


# ---------- attestations are two chips, everywhere ----------

def test_attestation_keys_carry_canonical_yes_no_wording():
    for key in ATTESTATION_KEYS:
        canon = KEY_QUESTIONS.get(key)
        assert canon, f"{key} has no canonical question"
        assert canon["kind"] == "select"
        assert canon["options"] == ["Yes", "No"]
        assert canon["question"].endswith("?")


def test_attestation_keys_render_as_two_chips_at_runtime():
    for key in ATTESTATION_KEYS:
        assert key in runtime._BOOLEAN_KEYS


def test_every_yes_no_question_is_a_boolean_key():
    """The two sync points may never drift: a key whose canonical answer is
    Yes/No but that the runtime treats as free text re-creates the dead save
    button (2026-08-04, Singapore)."""
    for key, canon in KEY_QUESTIONS.items():
        if canon.get("options") == ["Yes", "No"]:
            assert key in runtime._BOOLEAN_KEYS, key


def test_the_petitioner_s_own_words_meet_the_chips():
    out = runtime._with_derived_answers({
        "full_time_position": "yes",
        "h1b_dependent_employer": "no",
        "willful_violator": "never",
    })
    assert out["full_time_position"] == "Yes"
    assert out["h1b_dependent_employer"] == "No"
    assert out["willful_violator"] == "No"
    # An unrecognised word stays verbatim and fails honestly to a re-ask.
    kept = runtime._with_derived_answers({"willful_violator": "maybe"})
    assert kept["willful_violator"] == "maybe"


# ---------- employment dates ride the canonical pipeline ----------

def test_employment_date_keys_are_recognized_as_dates():
    for key in ("employment_start_date", "employment_end_date"):
        kind = dates.date_kind_for_key(key)
        assert kind == "expiry", (key, kind)   # the ±30y present-window pivot


def test_employment_dates_normalize_iso_in_and_format_mm_dd_yyyy_out():
    kind = dates.date_kind_for_key("employment_start_date")
    assert dates.normalize_any("2027-10-01", kind=kind) == "2027-10-01"
    # USCIS/FLAG portal format, per the adapter-declared pattern.
    assert dates.to_portal("2027-10-01", "MM/DD/YYYY") == "10/01/2027"
    # Non-ISO input fails closed: a portal never receives a guessed date.
    assert dates.to_portal("10/01/2027", "MM/DD/YYYY") == ""


# ---------- middle_name is asked, never derived ----------

def test_middle_name_is_never_derived_from_given_names():
    """The MRZ gives one given-names field; which token is a 'middle name' is
    a fact only the beneficiary knows. The sanctioned derivation site
    (runtime._with_derived_answers) must never touch it, and no app source
    may split one out of given_names."""
    assert "middle_name" not in inspect.getsource(runtime._with_derived_answers)
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "middle_name" not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "middle_name" in line and re.search(r"\bsplit\b|given_names",
                                                   line):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, offenders
