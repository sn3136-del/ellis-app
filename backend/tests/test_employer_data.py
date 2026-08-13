"""Employer-data seams — HRIS import and business-entity verification.

Every test is hermetic twice over: each provider's single HTTP seam is replaced,
AND an autouse fixture makes the real `httpx.request` explode. A test that
somehow reached the network fails loudly instead of passing slowly.

The invariants under test are honesty invariants, not plumbing:

  * Unconfigured means `available: False` with a reason and NO fields. Never a
    half-invented employee, never a guessed verdict.
  * An imported HRIS value is a SUGGESTION. It arrives `confirmed: False`, it is
    not answer-shaped, and nothing in the module can write `CaseParty.answers` —
    proved structurally, not promised.
  * A value with no honest Ellis equivalent is DROPPED, not coerced. An
    unmappable pay period yields a wage with no period; unfilled beats wrong.
  * A verification MISS is `unverified`, never `invalid`, and `exists` is never
    False. Absence of evidence is not evidence of fraud.
  * Unknown is None everywhere, and None is not False: an unknown entity age is
    not a young company, an unscreened watchlist is not a clean one.
  * The FEIN goes to the verifier and comes back nowhere.
"""
import datetime as dt
import inspect

import pytest

from app import config
from app.providers import entity_verify, hris


# ---------------------------------------------------------------------------
# Scripted seams
# ---------------------------------------------------------------------------
class _Http:
    """Stand-in for a provider's ONE HTTP seam. Records every call so a test can
    prove what reached — or never reached — the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {method} {url}")
        return self.responses.pop(0)


class _Boom:
    """A seam that raises — an unreachable host."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise RuntimeError("connection reset by peer at 10.0.0.7:443")


class _Never:
    """A seam that must never be called at all."""

    def __call__(self, *a, **k):
        raise AssertionError("the provider made a network call it should not have")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Both providers unconfigured regardless of any real .env, and the real
    transport disabled outright."""
    import httpx

    def _no_network(*a, **k):
        raise AssertionError("a test reached the real network")

    monkeypatch.setattr(httpx, "request", _no_network)
    for name in ("ELLIS_HRIS_PROVIDER", "ELLIS_HRIS_API_KEY",
                 "ELLIS_ENTITY_VERIFY", "ELLIS_ENTITY_VERIFY_KEY"):
        monkeypatch.setenv(name, "")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


def _configure_hris(monkeypatch, provider, key="hris-test-key"):
    monkeypatch.setenv("ELLIS_HRIS_PROVIDER", provider)
    monkeypatch.setenv("ELLIS_HRIS_API_KEY", key)
    config.settings.cache_clear()


def _configure_entity(monkeypatch, provider, key="entity-test-key"):
    monkeypatch.setenv("ELLIS_ENTITY_VERIFY", provider)
    monkeypatch.setenv("ELLIS_ENTITY_VERIFY_KEY", key)
    config.settings.cache_clear()


# ---------------------------------------------------------------------------
# Provider payload fixtures, shaped to the vendors' documented contracts
# ---------------------------------------------------------------------------
def _merge_employee(**over):
    employee = {
        "id": "aaaa-1111",
        "remote_id": "TRIP-4471",
        "employee_number": "4471",
        "display_full_name": "Li Wei",
        "work_email": "li.wei@trip.example",
        "employment_status": "ACTIVE",
        "start_date": "2024-03-01T00:00:00Z",
        # Deliberately present and deliberately never imported.
        "ssn": "123-45-6789",
        "date_of_birth": "1994-07-02T00:00:00Z",
        "ethnicity": "ASIAN_OR_INDIAN_SUBCONTINENT",
        "marital_status": "SINGLE",
        "personal_email": "liwei.personal@example.com",
        "home_location": {"street_1": "88 Private Lane", "city": "Sunnyvale"},
        "employments": [{
            "id": "emp-2",
            "job_title": "Senior Software Engineer",
            "pay_rate": 1234567,
            "pay_period": "YEAR",
            "pay_currency": "USD",
            "employment_type": "FULL_TIME",
            "effective_date": "2026-01-01T00:00:00Z",
        }],
        "work_location": {
            "street_1": "400 Concar Dr",
            "street_2": "Floor 3",
            "city": "San Mateo",
            "state": "CA",
            "zip_code": "94402",
            "country": "US",
        },
        "company": {
            "legal_name": "Trip.com Travel Singapore US Inc.",
            "display_name": "Trip.com US",
            "eins": ["93-1234567"],
        },
    }
    employee.update(over)
    return employee


def _finch_employment(**over):
    body = {
        "id": "ind-9",
        "first_name": "Li",
        "last_name": "Wei",
        "title": "Senior Software Engineer",
        "employment": {"type": "employee", "subtype": "full_time"},
        "start_date": "2024-03-01",
        "is_active": True,
        "location": {"line1": "400 Concar Dr", "line2": "Floor 3",
                     "city": "San Mateo", "state": "CA",
                     "postal_code": "94402", "country": "US"},
        # Finch states income.amount in CENTS.
        "income": {"unit": "yearly", "amount": 12345600, "currency": "usd"},
    }
    body.update(over)
    return (200, {"responses": [{"individual_id": "ind-9", "code": 200,
                                 "body": body}]})


_FINCH_COMPANY = (200, {"id": "co-1", "legal_name": "Trip.com US Inc.",
                        "ein": "93-1234567",
                        # Never imported: bank details.
                        "accounts": [{"routing_number": "121000248",
                                      "account_number": "000123456789"}]})


def _middesk_business(*, status="approved", tasks=None, registrations=None,
                      **over):
    business = {
        "object": "business",
        "id": "biz_123",
        "name": "Trip.com Travel Singapore US Inc.",
        "status": status,
        "registrations": registrations if registrations is not None else [
            {"status": "active", "state": "DE", "file_number": "77",
             "registration_date": "2025-06-15", "entity_type": "CORPORATION"},
            {"status": "active", "state": "CA", "file_number": "78",
             "registration_date": "2025-09-02", "entity_type": "CORPORATION"},
        ],
        "review": {"tasks": tasks if tasks is not None else [
            {"key": "name", "status": "success"},
            {"key": "address_verification", "status": "success"},
            {"key": "tin", "status": "success"},
            {"key": "watchlist", "status": "success"},
            {"key": "sos_active", "status": "success"},
        ]},
    }
    business.update(over)
    return business


# ===========================================================================
# HRIS — configuration and honest unavailability
# ===========================================================================
def test_hris_unconfigured_reports_unavailable_with_a_reason_and_no_fields(monkeypatch):
    monkeypatch.setattr(hris, "_http_json", _Never())
    assert hris.is_configured() is False

    result = hris.fetch_employee("TRIP-4471")

    assert result["available"] is False
    assert result["fields"] == {}
    assert "no HRIS provider is configured" in result["reason"]
    assert result["provider"] == "none"
    assert result["fetched_at"] is None


def test_hris_provider_named_without_a_key_is_not_configured(monkeypatch):
    monkeypatch.setenv("ELLIS_HRIS_PROVIDER", "merge")
    monkeypatch.setenv("ELLIS_HRIS_API_KEY", "")
    config.settings.cache_clear()
    monkeypatch.setattr(hris, "_http_json", _Never())

    assert hris.is_configured() is False
    assert hris.fetch_employee("TRIP-4471")["available"] is False


def test_hris_unknown_provider_raises_rather_than_guessing_an_endpoint(monkeypatch):
    monkeypatch.setenv("ELLIS_HRIS_PROVIDER", "bamboo-ish")
    monkeypatch.setenv("ELLIS_HRIS_API_KEY", "k")
    config.settings.cache_clear()
    monkeypatch.setattr(hris, "_http_json", _Never())

    with pytest.raises(hris.UnknownHrisProvider):
        hris.fetch_employee("TRIP-4471")


def test_hris_refuses_a_lookup_with_no_identifier_before_any_network_call(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    seam = _Never()
    monkeypatch.setattr(hris, "_http_json", seam)

    with pytest.raises(hris.HrisLookupInput):
        hris.fetch_employee("")


def test_merge_without_an_account_token_reads_nobodys_hris(monkeypatch):
    """Merge's account token selects WHICH employer is read. Without it there is
    no safe default, so the seam refuses instead of reading a tenant at random."""
    _configure_hris(monkeypatch, "merge")
    monkeypatch.setattr(hris, "_http_json", _Never())

    result = hris.fetch_employee("TRIP-4471")

    assert result["available"] is False
    assert "account token" in result["reason"]


def test_hris_network_failure_degrades_without_leaking_transport_internals(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    boom = _Boom()
    monkeypatch.setattr(hris, "_http_json", boom)

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert boom.calls == 1
    assert result["available"] is False
    assert result["fields"] == {}
    assert "unreachable" in result["reason"]
    assert "10.0.0.7" not in result["reason"]


def test_hris_http_error_is_reported_as_a_status_not_a_guess(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    monkeypatch.setattr(hris, "_http_json", _Http((503, {})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert result["available"] is False
    assert "503" in result["reason"]


# ===========================================================================
# HRIS — Merge
# ===========================================================================
def test_merge_normalizes_the_documented_contract(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    seam = _Http((200, {"results": [_merge_employee()]}))
    monkeypatch.setattr(hris, "_http_json", seam)

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert result["available"] is True
    fields = result["fields"]
    assert fields["job_title"] == "Senior Software Engineer"
    # Merge states pay_rate in MAJOR units, and a seven-figure salary must not
    # come back as '1.23457e+06'.
    assert fields["pay_rate"] == "1234567"
    assert fields["pay_period"] == "YEAR"
    assert fields["employment_start_date"] == "2024-03-01"
    assert fields["worksite_line1"] == "400 Concar Dr"
    assert fields["worksite_city"] == "San Mateo"
    assert fields["worksite_state"] == "CA"
    assert fields["worksite_postal_code"] == "94402"
    assert fields["employer_legal_name"] == "Trip.com Travel Singapore US Inc."
    assert fields["employer_fein"] == "931234567"


def test_merge_sends_both_required_headers_and_never_asks_for_raw_remote_data(monkeypatch):
    _configure_hris(monkeypatch, "merge", key="merge-key")
    seam = _Http((200, {"results": [_merge_employee()]}))
    monkeypatch.setattr(hris, "_http_json", seam)

    hris.fetch_employee("TRIP-4471", account_token="acct-1")

    call = seam.calls[0]
    assert call["headers"]["Authorization"] == "Bearer merge-key"
    assert call["headers"]["X-Account-Token"] == "acct-1"
    # include_remote_data is the raw upstream blob — the one that carries SSNs.
    assert "include_remote_data" not in call["params"]
    assert call["params"]["remote_id"] == "TRIP-4471"


def test_merge_identity_is_reproved_locally_so_an_ignored_filter_cannot_leak_a_stranger(monkeypatch):
    """Merge's OpenAPI schema does not list work_email as a filter. If the
    server ignores it and returns the roster, the first row is someone else."""
    _configure_hris(monkeypatch, "merge")
    stranger = _merge_employee(work_email="someone.else@trip.example",
                               remote_id="TRIP-0001")
    monkeypatch.setattr(hris, "_http_json",
                        _Http((200, {"results": [stranger]})))

    result = hris.fetch_employee(email="li.wei@trip.example",
                                 account_token="acct-1")

    assert result["available"] is False
    assert result["fields"] == {}
    assert "no employee in the HRIS matched" in result["reason"]


def test_merge_two_matches_are_not_resolved_by_picking_one(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    monkeypatch.setattr(hris, "_http_json", _Http(
        (200, {"results": [_merge_employee(), _merge_employee(id="bbbb-2")]})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert result["available"] is False
    assert "will not choose between them" in result["reason"]


def test_merge_several_eins_leave_the_fein_to_a_human(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    employee = _merge_employee()
    employee["company"]["eins"] = ["93-1234567", "47-7654321"]
    monkeypatch.setattr(hris, "_http_json", _Http((200, {"results": [employee]})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert "employer_fein" not in result["fields"]
    assert any("more than one employer identification number" in w
               for w in result["warnings"])


def test_merge_uses_the_newest_employment_record(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    employee = _merge_employee()
    employee["employments"] = [
        {"job_title": "Software Engineer", "pay_rate": 150000,
         "pay_period": "YEAR", "effective_date": "2024-03-01T00:00:00Z"},
        {"job_title": "Senior Software Engineer", "pay_rate": 195000,
         "pay_period": "YEAR", "effective_date": "2026-01-01T00:00:00Z"},
    ]
    monkeypatch.setattr(hris, "_http_json", _Http((200, {"results": [employee]})))

    fields = hris.fetch_employee("TRIP-4471", account_token="acct-1")["fields"]

    assert fields["job_title"] == "Senior Software Engineer"
    assert fields["pay_rate"] == "195000"


def test_merge_undated_employment_history_is_flagged_not_silently_picked(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    employee = _merge_employee()
    employee["employments"] = [
        {"job_title": "Software Engineer", "pay_rate": 150000, "pay_period": "YEAR"},
        {"job_title": "Analyst", "pay_rate": 110000, "pay_period": "YEAR"},
    ]
    monkeypatch.setattr(hris, "_http_json", _Http((200, {"results": [employee]})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert any("no effective date" in w for w in result["warnings"])


def test_merge_inactive_employee_is_flagged(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    monkeypatch.setattr(hris, "_http_json", _Http(
        (200, {"results": [_merge_employee(employment_status="INACTIVE")]})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert any("not active" in w for w in result["warnings"])


def test_hris_never_imports_ssn_birth_date_or_home_address(monkeypatch):
    """The normalizers are allowlists. A provider that volunteers an SSN cannot
    get one through this module."""
    _configure_hris(monkeypatch, "merge")
    monkeypatch.setattr(hris, "_http_json",
                        _Http((200, {"results": [_merge_employee()]})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert set(result["fields"]) <= set(hris.FIELD_KEYS)
    blob = repr(result)
    for secret in ("123-45-6789", "1994-07-02", "ASIAN_OR_INDIAN",
                   "88 Private Lane", "liwei.personal@example.com"):
        assert secret not in blob
    for banned in hris.NEVER_IMPORTED:
        assert banned not in result["fields"]


def test_merge_unparseable_start_date_is_dropped_not_guessed(monkeypatch):
    _configure_hris(monkeypatch, "merge")
    monkeypatch.setattr(hris, "_http_json", _Http(
        (200, {"results": [_merge_employee(start_date="last March")]})))

    result = hris.fetch_employee("TRIP-4471", account_token="acct-1")

    assert "employment_start_date" not in result["fields"]
    assert any("could not be read as a date" in w for w in result["warnings"])


# ===========================================================================
# HRIS — Finch
# ===========================================================================
def test_finch_converts_income_cents_to_major_units(monkeypatch):
    """The single most dangerous difference between the two providers: Finch
    states income.amount in CENTS. 12,345,600 cents is $123,456, not $12.3M."""
    _configure_hris(monkeypatch, "finch")
    monkeypatch.setattr(hris, "_http_json",
                        _Http(_finch_employment(), _FINCH_COMPANY))

    fields = hris.fetch_employee("ind-9")["fields"]

    assert fields["pay_rate"] == "123456"
    assert fields["pay_period"] == "yearly"
    assert fields["job_title"] == "Senior Software Engineer"
    assert fields["employment_start_date"] == "2024-03-01"
    assert fields["worksite_postal_code"] == "94402"
    assert fields["employer_legal_name"] == "Trip.com US Inc."
    assert fields["employer_fein"] == "931234567"


def test_finch_never_imports_company_bank_accounts(monkeypatch):
    _configure_hris(monkeypatch, "finch")
    monkeypatch.setattr(hris, "_http_json",
                        _Http(_finch_employment(), _FINCH_COMPANY))

    result = hris.fetch_employee("ind-9")

    assert "000123456789" not in repr(result)
    assert "121000248" not in repr(result)


def test_finch_sends_its_version_header_and_the_documented_batch_body(monkeypatch):
    _configure_hris(monkeypatch, "finch", key="finch-token")
    seam = _Http(_finch_employment(), _FINCH_COMPANY)
    monkeypatch.setattr(hris, "_http_json", seam)

    hris.fetch_employee("ind-9")

    call = seam.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/employer/employment")
    assert call["headers"]["Finch-API-Version"] == hris.FINCH_API_VERSION
    assert call["headers"]["Authorization"] == "Bearer finch-token"
    assert call["json_body"] == {"requests": [{"individual_id": "ind-9"}]}


def test_finch_refuses_an_email_lookup_rather_than_scanning_ssns(monkeypatch):
    _configure_hris(monkeypatch, "finch")
    monkeypatch.setattr(hris, "_http_json", _Never())

    result = hris.fetch_employee(email="li.wei@trip.example")

    assert result["available"] is False
    assert "individual_id" in result["reason"]
    assert "SSN" in result["reason"]


def test_finch_reads_only_the_row_that_echoes_the_requested_id(monkeypatch):
    """A batch API that reorders or pads its responses must not be able to hand
    this employee somebody else's wage."""
    _configure_hris(monkeypatch, "finch")
    monkeypatch.setattr(hris, "_http_json", _Http(
        (200, {"responses": [{"individual_id": "ind-OTHER", "code": 200,
                              "body": {"title": "VP", "income": {
                                  "unit": "yearly", "amount": 90000000}}}]})))

    result = hris.fetch_employee("ind-9")

    assert result["available"] is False
    assert "no employee in the HRIS matched" in result["reason"]


def test_finch_company_failure_costs_the_employer_facts_not_the_import(monkeypatch):
    _configure_hris(monkeypatch, "finch")
    monkeypatch.setattr(hris, "_http_json",
                        _Http(_finch_employment(), (500, {})))

    result = hris.fetch_employee("ind-9")

    assert result["available"] is True
    assert result["fields"]["job_title"] == "Senior Software Engineer"
    assert "employer_fein" not in result["fields"]
    assert any("employer record could not be read" in w
               for w in result["warnings"])


def test_finch_inactive_employment_is_flagged(monkeypatch):
    _configure_hris(monkeypatch, "finch")
    monkeypatch.setattr(hris, "_http_json",
                        _Http(_finch_employment(is_active=False), _FINCH_COMPANY))

    result = hris.fetch_employee("ind-9")

    assert any("inactive" in w for w in result["warnings"])


# ===========================================================================
# HRIS — mapping onto Ellis's petitioner vocabulary
# ===========================================================================
_MAPPABLE = {
    "job_title": "Senior Software Engineer",
    "pay_rate": "123456",
    "pay_period": "YEAR",
    "pay_currency": "USD",
    "employment_type": "FULL_TIME",
    "employment_start_date": "2024-03-01",
    "worksite_line1": "400 Concar Dr",
    "worksite_city": "San Mateo",
    "worksite_state": "CA",
    "worksite_postal_code": "94402",
    "employer_legal_name": "Trip.com Travel Singapore US Inc.",
    "employer_trade_name": "Trip.com US",
    "employer_fein": "931234567",
}


def test_mapping_reaches_the_canonical_petition_vocabulary():
    out = hris.to_party_answers(_MAPPABLE)
    keys = out["suggestions"]

    assert keys["job_title"]["value"] == "Senior Software Engineer"
    assert keys["wage_offer"]["value"] == "123456"
    assert keys["wage_offer_unit"]["value"] == "year"
    assert keys["worksite_address_line1"]["value"] == "400 Concar Dr"
    assert keys["worksite_address_city"]["value"] == "San Mateo"
    assert keys["worksite_address_state"]["value"] == "CA"
    assert keys["worksite_address_zip"]["value"] == "94402"
    assert keys["employer_legal_name"]["value"].startswith("Trip.com")
    assert keys["employer_dba"]["value"] == "Trip.com US"
    assert keys["employer_fein"]["value"] == "931234567"
    assert keys["employment_start_date"]["value"] == "2024-03-01"
    assert keys["full_time_position"]["value"] == "Yes"


def test_every_imported_value_arrives_unconfirmed_and_not_answer_shaped():
    out = hris.to_party_answers(_MAPPABLE)

    assert out["unconfirmed"] is True
    assert out["writes_answers"] is False
    assert out["suggestions"], "the fixture should produce suggestions"
    for key, suggestion in out["suggestions"].items():
        assert suggestion["confirmed"] is False, key
        # A dict, not a scalar: a caller that splatted this into
        # CaseParty.answers would produce something visibly broken rather than
        # a quietly filed government form.
        assert isinstance(suggestion, dict), key
        assert suggestion["source"] == hris.SOURCE


def test_mapping_cannot_write_case_party_answers():
    """Structural, not aspirational: the module holds no session and no public
    function accepts a case, a party or a db handle, so 'it never auto-writes'
    is a property of the code."""
    party_answers = {"job_title": "Analyst", "wage_offer": "100000"}
    before = dict(party_answers)

    out = hris.to_party_answers(_MAPPABLE)

    assert party_answers == before
    assert out["suggestions"]["job_title"]["value"] != party_answers["job_title"]

    forbidden = {"db", "session", "party", "case", "application", "answers"}
    for name, fn in vars(hris).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        params = set(inspect.signature(fn).parameters)
        assert not (params & forbidden), f"{name} takes {params & forbidden}"


def test_an_unmappable_pay_period_leaves_the_wage_period_unset():
    """Ellis's wage vocabulary has no 'quarter'. Guessing one turns a correct
    salary into a wage violation, so the period is dropped and said out loud."""
    out = hris.to_party_answers({**_MAPPABLE, "pay_period": "QUARTER"})

    assert "wage_offer" in out["suggestions"]
    assert "wage_offer_unit" not in out["suggestions"]
    assert any(d["answer_key"] == "wage_offer_unit" for d in out["dropped"])
    assert any("QUARTER" in w for w in out["warnings"])
    assert "period could not be mapped" in out["suggestions"]["wage_offer"]["note"]


def test_finch_pay_period_vocabulary_maps_too():
    out = hris.to_party_answers({**_MAPPABLE, "pay_period": "bi_weekly"})
    assert out["suggestions"]["wage_offer_unit"]["value"] == "biweek"


def test_an_ambiguous_employment_type_does_not_answer_the_full_time_attestation():
    out = hris.to_party_answers({**_MAPPABLE, "employment_type": "INTERN"})

    assert "full_time_position" not in out["suggestions"]
    assert any(d["answer_key"] == "full_time_position" for d in out["dropped"])
    assert any("INTERN" in w for w in out["warnings"])


def test_part_time_maps_to_no_rather_than_being_dropped():
    out = hris.to_party_answers({**_MAPPABLE, "employment_type": "PART_TIME"})
    assert out["suggestions"]["full_time_position"]["value"] == "No"


def test_the_hire_date_carries_its_petition_caveat():
    """The HRIS hire date and the requested H-1B start date are different facts,
    and on an extension they are never the same one."""
    out = hris.to_party_answers(_MAPPABLE)
    note = out["suggestions"]["employment_start_date"]["note"]

    assert "hire date" in note
    assert "extension" in note


def test_a_non_usd_wage_is_flagged():
    out = hris.to_party_answers({**_MAPPABLE, "pay_currency": "CNY"})
    assert any("CNY" in w and "USD" in w for w in out["warnings"])


def test_the_fein_suggestion_warns_that_the_hris_entity_may_not_be_the_petitioner():
    out = hris.to_party_answers(_MAPPABLE)
    assert "PETITIONING entity" in out["suggestions"]["employer_fein"]["note"]


def test_mapping_empty_fields_produces_nothing_rather_than_blanks():
    out = hris.to_party_answers({})
    assert out["suggestions"] == {}
    assert out["writes_answers"] is False


def test_hris_capability_states_what_it_will_not_do(monkeypatch):
    assert hris.capability()["configured"] is False
    _configure_hris(monkeypatch, "merge")
    cap = hris.capability()
    assert cap["configured"] is True
    assert cap["provider"] == "merge"
    assert cap["writes_answers"] is False
    assert cap["values_are_confirmed"] is False


# ===========================================================================
# Entity verification — configuration and honest unavailability
# ===========================================================================
def test_entity_verify_unconfigured_claims_nothing(monkeypatch):
    monkeypatch.setattr(entity_verify, "_http_json", _Never())
    assert entity_verify.is_configured() is False

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.")

    assert result["available"] is False
    assert result["exists"] is None
    assert result["entity_age_years"] is None
    assert result["employee_count_band"] is None
    assert result["addresses_match"] is None
    assert result["watchlist_hits"] is None
    assert "no entity verification provider is configured" in result["reason"]


def test_entity_verify_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("ELLIS_ENTITY_VERIFY", "creditsafe-ish")
    monkeypatch.setenv("ELLIS_ENTITY_VERIFY_KEY", "k")
    config.settings.cache_clear()
    monkeypatch.setattr(entity_verify, "_http_json", _Never())

    with pytest.raises(entity_verify.UnknownEntityVerifyProvider):
        entity_verify.verify_employer(legal_name="Trip.com US Inc.")


def test_entity_verify_requires_a_legal_name_before_any_network_call(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Never())

    with pytest.raises(entity_verify.EntityVerifyInput):
        entity_verify.verify_employer(legal_name="  ")


def test_entity_verify_network_failure_degrades_without_leaking_internals(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    boom = _Boom()
    monkeypatch.setattr(entity_verify, "_http_json", boom)

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.")

    assert result["available"] is False
    assert result["exists"] is None
    assert "unreachable" in result["reason"]
    assert "10.0.0.7" not in result["reason"]


# ===========================================================================
# Entity verification — Middesk
# ===========================================================================
_TODAY = dt.date(2026, 8, 11)


def test_middesk_settled_verification_reports_age_address_and_watchlist(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json",
                        _Http((201, _middesk_business())))

    result = entity_verify.verify_employer(
        legal_name="Trip.com Travel Singapore US Inc.", fein="93-1234567",
        address={"line1": "400 Concar Dr", "city": "San Mateo",
                 "state": "CA", "postal_code": "94402"},
        today=_TODAY)

    assert result["available"] is True
    assert result["verdict"] == "verified"
    assert result["exists"] is True
    # Earliest registration (2025-06-15) against 2026-08-11 is one year old.
    assert result["entity_registration_date"] == "2025-06-15"
    assert result["entity_age_years"] == 1
    assert result["addresses_match"] is True
    assert result["tin_match"] is True
    assert result["watchlist_hits"] == 0
    assert result["reference"] == "biz_123"


def test_middesk_sends_the_documented_body_and_basic_auth(monkeypatch):
    _configure_entity(monkeypatch, "middesk", key="middesk-key")
    seam = _Http((201, _middesk_business()))
    monkeypatch.setattr(entity_verify, "_http_json", seam)

    entity_verify.verify_employer(
        legal_name="Trip.com US Inc.", fein="93-1234567",
        address={"line1": "400 Concar Dr", "city": "San Mateo",
                 "state": "CA", "postal_code": "94402"}, today=_TODAY)

    call = seam.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/businesses")
    assert call["auth"] == ("middesk-key", "")
    assert call["json_body"]["tin"] == {"tin": "931234567"}
    assert call["json_body"]["addresses"][0]["city"] == "San Mateo"
    # The FEIN belongs in the body, never in a URL or a query string.
    assert "931234567" not in call["url"]
    assert not (call.get("params") or {})


def test_the_fein_is_sent_to_the_verifier_and_echoed_nowhere(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json",
                        _Http((201, _middesk_business())))

    result = entity_verify.verify_employer(
        legal_name="Trip.com US Inc.", fein="93-1234567", today=_TODAY)

    assert "931234567" not in repr(result)
    assert "93-1234567" not in repr(result)


def test_a_verification_miss_is_unverified_and_never_invalid(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        status="rejected",
        registrations=[],
        tasks=[{"key": "name", "status": "failure"},
               {"key": "sos_inactive", "status": "failure"}]))))

    result = entity_verify.verify_employer(
        legal_name="Trip.com Travel Singapore US Inc.", today=_TODAY)

    assert result["verdict"] == "unverified"
    assert result["verdict"] in entity_verify.VERDICTS
    # Absence of evidence is not evidence of fraud.
    assert result["exists"] is None
    assert result["exists"] is not False
    assert "unverified" in result["reason"]
    assert "invalid" not in repr(result).lower()


def test_there_is_no_invalid_verdict_anywhere_in_the_vocabulary():
    assert entity_verify.VERDICTS == ("verified", "unverified", "pending")
    assert "invalid" not in entity_verify.VERDICTS
    assert entity_verify.capability()["can_report_invalid"] is False


def test_a_mismatched_fein_or_address_is_reported_as_a_mismatch_not_a_fake_company(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        tasks=[{"key": "name", "status": "success"},
               {"key": "address_verification", "status": "failure"},
               {"key": "tin", "status": "failure"},
               {"key": "watchlist", "status": "success"},
               {"key": "sos_active", "status": "success"}]))))

    result = entity_verify.verify_employer(
        legal_name="Trip.com US Inc.", fein="93-0000000",
        address={"line1": "1 Nowhere Rd", "city": "San Mateo", "state": "CA"},
        today=_TODAY)

    assert result["addresses_match"] is False
    assert result["tin_match"] is False
    # The company was still found; only the submitted details disagree.
    assert result["exists"] is True
    assert result["verdict"] == "verified"
    assert "invalid" not in repr(result).lower()


def test_a_partial_address_match_stays_unknown_rather_than_becoming_a_boolean(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        tasks=[{"key": "name", "status": "success"},
               {"key": "address_verification", "status": "warning"},
               {"key": "sos_active", "status": "success"}]))))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)

    assert result["addresses_match"] is None


def test_a_pending_verification_claims_nothing(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json",
                        _Http((201, _middesk_business(status="in_review"))))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)

    assert result["available"] is True
    assert result["verdict"] == "pending"
    assert result["exists"] is None
    assert result["entity_age_years"] is None
    assert result["addresses_match"] is None
    assert result["reference"] == "biz_123"


def test_a_pending_verification_can_be_rechecked(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    seam = _Http((200, _middesk_business()))
    monkeypatch.setattr(entity_verify, "_http_json", seam)

    result = entity_verify.recheck("biz_123")

    assert seam.calls[0]["method"] == "GET"
    assert seam.calls[0]["url"].endswith("/businesses/biz_123")
    assert result["verdict"] == "verified"


def test_a_webhook_shaped_payload_is_read_the_same_way(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http(
        (201, {"data": {"object": _middesk_business()}})))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)

    assert result["exists"] is True
    assert result["entity_age_years"] == 1


def test_a_missing_registration_date_leaves_age_unknown_not_zero(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json",
                        _Http((201, _middesk_business(registrations=[]))))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)

    assert result["entity_age_years"] is None
    assert any("entity age is unknown" in w for w in result["warnings"])


def test_employee_count_is_unknown_not_small(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json",
                        _Http((201, _middesk_business())))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)

    assert result["employee_count_band"] is None
    assert any("unknown, not small" in w for w in result["warnings"])


def test_an_unscreened_watchlist_is_none_and_a_finding_is_flagged(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        tasks=[{"key": "name", "status": "success"},
               {"key": "sos_active", "status": "success"}]))))

    unscreened = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                               today=_TODAY)
    assert unscreened["watchlist_hits"] is None

    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        tasks=[{"key": "name", "status": "success"},
               {"key": "watchlist", "status": "failure"},
               {"key": "sos_active", "status": "success"}],
        watchlist={"hits": [{"list": "OFAC SDN"}]}))))

    hit = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                        today=_TODAY)
    assert hit["watchlist_hits"] == 1
    assert any("lead for human review" in w for w in hit["warnings"])


def test_an_inactive_sos_filing_is_surfaced(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        tasks=[{"key": "name", "status": "success"},
               {"key": "sos_inactive", "status": "failure"}]))))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)

    assert any("INACTIVE" in w for w in result["warnings"])


# ===========================================================================
# Entity verification — D&B
# ===========================================================================
def test_dnb_without_a_credential_pair_says_exactly_what_is_missing(monkeypatch):
    _configure_entity(monkeypatch, "dnb", key="just-one-value")
    monkeypatch.setattr(entity_verify, "_http_json", _Never())

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.")

    assert result["available"] is False
    assert "client_id:client_secret" in result["reason"]


def test_dnb_answers_existence_and_admits_what_it_cannot_read(monkeypatch):
    _configure_entity(monkeypatch, "dnb", key="cid:csecret")
    seam = _Http(
        (200, {"access_token": "tok-1"}),
        (200, {"matchCandidates": [
            {"confidenceCode": 6, "organization": {"duns": "111111111"}},
            {"confidenceCode": 9, "organization": {"duns": "999999999"}}]}))
    monkeypatch.setattr(entity_verify, "_http_json", seam)

    result = entity_verify.verify_employer(
        legal_name="Trip.com US Inc.",
        address={"line1": "400 Concar Dr", "city": "San Mateo"})

    assert result["available"] is True
    assert result["exists"] is True
    assert result["verdict"] == "verified"
    # Strongest confidence wins.
    assert result["reference"] == "999999999"
    # And the seam does not pretend to facts it could not verify a schema for.
    assert result["entity_age_years"] is None
    assert result["employee_count_band"] is None
    assert result["addresses_match"] is None
    assert any("behind a customer login" in w for w in result["warnings"])
    assert seam.calls[0]["auth"] == ("cid", "csecret")


def test_dnb_no_match_is_unverified_not_invalid(monkeypatch):
    _configure_entity(monkeypatch, "dnb", key="cid:csecret")
    monkeypatch.setattr(entity_verify, "_http_json", _Http(
        (200, {"access_token": "tok-1"}), (200, {"matchCandidates": []})))

    result = entity_verify.verify_employer(legal_name="Nonesuch Holdings LLC")

    assert result["verdict"] == "unverified"
    assert result["exists"] is None
    assert "invalid" not in repr(result).lower()


def test_dnb_rejected_credentials_do_not_become_a_missing_company(monkeypatch):
    _configure_entity(monkeypatch, "dnb", key="cid:csecret")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((401, {})))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.")

    assert result["available"] is False
    assert result["exists"] is None
    assert "refused the configured credentials" in result["reason"]


# ===========================================================================
# Entity verification — the counsel-engine seam
# ===========================================================================
def test_counsel_signals_speak_the_taxonomy_and_stay_advisory(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json",
                        _Http((201, _middesk_business())))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)
    signals = entity_verify.counsel_signals(result)

    # Registered 2025-06-15, read 2026-08-11: one year old, under the line.
    assert signals["signals"]["entity_age_under_2_years"] is True
    assert signals["signals"]["business_address_unverified"] is False
    assert signals["signals"]["entity_not_publicly_verifiable"] is False
    assert signals["signals"]["watchlist_finding_present"] is False
    assert signals["advisory"] is True
    assert signals["overrides_ellis_answer"] is False
    assert all("H1B_RFE_TAXONOMY" in c for c in signals["cites"].values())


def test_an_established_employer_does_not_fire_the_young_entity_signal(monkeypatch):
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        registrations=[{"status": "active", "state": "DE",
                        "registration_date": "2011-04-02"}]))))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)
    signals = entity_verify.counsel_signals(result)

    assert result["entity_age_years"] == 15
    assert signals["signals"]["entity_age_under_2_years"] is False


def test_unknown_signals_are_none_and_never_a_reassuring_false(monkeypatch):
    """A risk engine that reads an unknown entity age as 'not young' has
    invented a reassurance nobody supplied."""
    _configure_entity(monkeypatch, "middesk")
    monkeypatch.setattr(entity_verify, "_http_json", _Http((201, _middesk_business(
        registrations=[],
        tasks=[{"key": "name", "status": "success"},
               {"key": "sos_active", "status": "success"}]))))

    result = entity_verify.verify_employer(legal_name="Trip.com US Inc.",
                                           today=_TODAY)
    signals = entity_verify.counsel_signals(result)["signals"]

    assert signals["entity_age_under_2_years"] is None
    assert signals["business_address_unverified"] is None
    assert signals["watchlist_finding_present"] is None


def test_an_unavailable_or_pending_verification_fires_no_signal_at_all():
    unavailable = {"available": False, "verdict": "unverified"}
    pending = {"available": True, "verdict": "pending"}

    for res in (unavailable, pending, {}, None):
        signals = entity_verify.counsel_signals(res)["signals"]
        assert set(signals) == set(entity_verify.SIGNAL_CITES)
        assert all(value is None for value in signals.values()), res


def test_an_unverified_employer_signals_a_review_not_a_fraud_finding():
    result = {"available": True, "verdict": "unverified",
              "entity_age_years": None, "addresses_match": None,
              "watchlist_hits": None, "provider": "middesk"}

    out = entity_verify.counsel_signals(result)

    assert out["signals"]["entity_not_publicly_verifiable"] is True
    assert "never one proven not to exist" in out["note"]
    assert out["advisory"] is True
