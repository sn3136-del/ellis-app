"""Key-free official US government feeds: Federal Register + USCIS Employer Hub.

Every test is hermetic. Each provider has exactly ONE network seam
(`federal_register._http_json`, `uscis_employer_hub._http_bytes`); the tests
replace it and assert what did — and did not — pass through. Nothing here
touches the real network, and the employer-hub lookup path asserts that it
never even reaches for the seam.

The invariants under test are honesty invariants, and one of them is unusual
enough to state plainly: a FAILED staleness check must never be able to look
like an all-clear. An empty rule list means "the Federal Register has no such
rule"; a failure raises, and the dict-returning alarm answers `stale: None`
(explicitly unknown). A false all-clear on a fee change is the exact failure
this feed exists to prevent.
"""
import csv

import pytest

from app import config
from app.h1b import guidance
from app.providers import federal_register as fr
from app.providers import uscis_employer_hub as hub


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------
class _Http:
    """Scripted stand-in for a provider's ONE HTTP seam. Records every call so a
    test can prove what reached (or never reached) the network."""

    def __init__(self, *responses, repeat=False):
        self.responses = list(responses)
        self.repeat = repeat
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {args} {kwargs}")
        return self.responses[0] if self.repeat else self.responses.pop(0)


class _Boom:
    """A seam that raises — a stand-in for an unreachable host."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise RuntimeError("network down")


class _Never:
    """A seam that must never be called. Fails the test loudly if it is."""

    def __call__(self, *a, **k):
        raise AssertionError("this code path must never touch the network")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """Both feeds start enabled, with empty caches and an EMPTY employer-hub
    cache dir, regardless of any real .env or committed reference data."""
    fr.reset_cache()
    hub.reset_cache()
    monkeypatch.setenv("ELLIS_FEDERAL_REGISTER", "on")
    monkeypatch.setenv("ELLIS_USCIS_EMPLOYER_HUB", "on")
    monkeypatch.setenv("ELLIS_USCIS_EMPLOYER_HUB_DIR", str(tmp_path / "hub"))
    config.settings.cache_clear()
    yield
    fr.reset_cache()
    hub.reset_cache()
    config.settings.cache_clear()


# A verbatim-shaped slice of the live API response (verified 2026-08-11). Two
# real rules that genuinely move Ellis's curated facts: an e-filing IFR and an
# H-1B fee rule.
_FR_RESULTS = [
    {
        "title": "Mandatory Electronic Filing (e-Filing)",
        "document_number": "2026-16313",
        "publication_date": "2026-08-11",
        "effective_on": "2026-08-11",
        "html_url": "https://www.federalregister.gov/documents/2026/08/11/2026-16313/mandatory-electronic-filing-e-filing",
        "agencies": [{"raw_name": "DEPARTMENT OF HOMELAND SECURITY",
                      "name": "Homeland Security Department", "id": 227,
                      "slug": "homeland-security-department"}],
        "abstract": ("This interim final rule amends DHS regulations to provide "
                     "that USCIS may require mandatory electronic filing "
                     "(e-filing) of certain benefit requests."),
        "type": "Rule",
        "action": "Interim final rule (IFR) with request for comments.",
    },
    {
        "title": "9-11 Response and Biometric Entry-Exit Fee for H-1B and L-1 Visas",
        "document_number": "2026-16231",
        "publication_date": "2026-08-10",
        "effective_on": "2026-09-09",
        "html_url": "https://www.federalregister.gov/documents/2026/08/10/2026-16231/9-11-response-and-biometric-entry-exit-fee-for-h-1b-and-l-1-visas",
        "agencies": [{"raw_name": "DEPARTMENT OF HOMELAND SECURITY",
                      "name": "Homeland Security Department", "id": 227,
                      "slug": "homeland-security-department"}],
        "abstract": ("DHS is amending the regulations concerning the 9-11 "
                     "Response and Biometric Entry-Exit Fee for certain H-1B "
                     "and L-1 Visas."),
        "type": "Rule",
        "action": "Final rule.",
    },
]


def _fr_body(results=None, count=None):
    results = _FR_RESULTS if results is None else results
    return {"description": "Documents matching 'H-1B'",
            "count": len(results) if count is None else count,
            "total_pages": 1, "results": results}


def _one_term(monkeypatch, body):
    """Stub the seam and pin the search to a SINGLE term, so a test asserts one
    round trip instead of one per default term."""
    http = _Http((200, body), repeat=True)
    monkeypatch.setattr(fr, "_http_json", http)
    return http


# ===========================================================================
# Federal Register — request shape
# ===========================================================================
def test_search_sends_the_documented_api_contract(monkeypatch):
    http = _one_term(monkeypatch, _fr_body())
    fr.search_immigration_rules("2026-08-09", terms=("H-1B",))

    assert len(http.calls) == 1
    url = http.calls[0]["args"][1]
    params = http.calls[0]["kwargs"]["params"]
    assert url == "https://www.federalregister.gov/api/v1/documents.json"
    assert params["conditions[term]"] == "H-1B"
    assert params["conditions[publication_date][gte]"] == "2026-08-09"
    # Agency slugs verified against GET /api/v1/agencies.
    assert "u-s-citizenship-and-immigration-services" in params["conditions[agencies][]"]
    assert "employment-and-training-administration" in params["conditions[agencies][]"]
    assert params["conditions[type][]"] == list(fr.DOCUMENT_TYPES)
    # Only the fields Ellis actually reads are requested.
    assert "effective_on" in params["fields[]"]
    assert params["order"] == "newest"


def test_search_parses_the_verified_live_shape(monkeypatch):
    _one_term(monkeypatch, _fr_body())
    rules = fr.search_immigration_rules("2026-08-01", terms=("H-1B",))

    assert [r["document_number"] for r in rules] == ["2026-16313", "2026-16231"]
    fee_rule = rules[1]
    assert fee_rule["publication_date"] == "2026-08-10"
    # The API calls it effective_on; Ellis normalizes to effective_date.
    assert fee_rule["effective_date"] == "2026-09-09"
    assert fee_rule["agencies"] == ["Homeland Security Department"]
    assert fee_rule["type"] == "Rule"
    assert fee_rule["source"] == "federal_register"
    assert fee_rule["html_url"].startswith("https://www.federalregister.gov/")


def test_search_dedupes_across_terms_and_records_which_matched(monkeypatch):
    http = _Http((200, _fr_body()), (200, _fr_body([_FR_RESULTS[0]])))
    monkeypatch.setattr(fr, "_http_json", http)
    rules = fr.search_immigration_rules("2026-08-01",
                                        terms=("H-1B", "electronic filing"))
    assert len(http.calls) == 2
    # Two terms, three hits, two distinct rules.
    assert [r["document_number"] for r in rules] == ["2026-16313", "2026-16231"]
    assert rules[0]["matched_terms"] == ["H-1B", "electronic filing"]
    assert rules[1]["matched_terms"] == ["H-1B"]


# ===========================================================================
# Federal Register — the staleness alarm
# ===========================================================================
def test_alarm_fires_when_a_rule_postdates_the_curated_as_of(monkeypatch):
    _one_term(monkeypatch, _fr_body())
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))

    assert res["live"] is True
    assert res["stale"] is True
    assert res["action"] == "human_review"
    assert res["count"] == 2
    assert [r["document_number"] for r in res["rules"]] == ["2026-16313",
                                                            "2026-16231"]
    # The routing hint tells the reviewer WHICH curated fact to re-check.
    by_number = {r["document_number"]: r["fact_areas"] for r in res["rules"]}
    assert "signature_efiling" in by_number["2026-16313"]
    assert "fees" in by_number["2026-16231"]
    assert "re-verify" in res["note"].lower()


def test_alarm_defaults_to_the_curated_guidance_as_of(monkeypatch):
    http = _one_term(monkeypatch, _fr_body([]))
    res = fr.check_curated_facts_stale(terms=("H-1B",))
    assert res["as_of"] == guidance.AS_OF
    assert http.calls[0]["kwargs"]["params"][
        "conditions[publication_date][gte]"] == guidance.AS_OF


def test_alarm_stays_silent_when_nothing_postdates_as_of(monkeypatch):
    _one_term(monkeypatch, _fr_body([]))
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["live"] is True
    assert res["stale"] is False   # a REAL all-clear, from a real answer
    assert res["rules"] == [] and res["count"] == 0
    assert res["action"] == "none"


def test_alarm_ignores_a_rule_published_on_the_as_of_date(monkeypatch):
    """The API's gte filter is inclusive, but a rule published ON the curated
    date was already in front of the human who curated that day. Only strictly
    newer rules are news."""
    same_day = {**_FR_RESULTS[1], "publication_date": "2026-08-09"}
    _one_term(monkeypatch, _fr_body([same_day]))
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["stale"] is False and res["rules"] == []
    # ...but it was really fetched: the boundary is filtered locally, so the
    # query stays inclusive and nothing one day off is missed.
    assert res["total_matched"] == 1


def _numbers_in(value):
    """Every number anywhere in a nested payload."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers_in(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in _numbers_in(v)]
    return []


def test_alarm_never_rewrites_a_curated_fact(monkeypatch):
    """The whole doctrine in one test: the alarm cites rules, it does not carry
    amounts, and guidance.FEES is byte-identical afterwards."""
    _one_term(monkeypatch, _fr_body())
    before = {k: dict(v) for k, v in guidance.FEES.items()}
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))

    # The alarm did not touch the curated facts.
    assert guidance.FEES == before

    # A rule is a citation: title, number, dates, link, routing hint. There is
    # no amount field and no dollar figure anywhere in the payload.
    for rule in res["rules"]:
        assert set(rule) >= {"title", "document_number", "publication_date",
                             "effective_date", "html_url", "fact_areas"}
        assert not any(k for k in rule if "amount" in k or "fee" in k)
    assert "$" not in repr(res["rules"])
    curated_amounts = {f["amount"] for f in guidance.FEES.values()}
    assert not (set(_numbers_in(res["rules"])) & curated_amounts)


# ===========================================================================
# Federal Register — honest degradation (never a false all-clear)
# ===========================================================================
def test_search_raises_rather_than_returning_an_empty_list(monkeypatch):
    """The core honesty invariant. `[]` means "no such rule exists"; a failure
    must never be able to wear that costume."""
    boom = _Boom()
    monkeypatch.setattr(fr, "_http_json", boom)
    with pytest.raises(fr.FederalRegisterUnavailable):
        fr.search_immigration_rules("2026-08-09", terms=("H-1B",))
    assert boom.calls == 1


def test_search_http_error_raises(monkeypatch):
    monkeypatch.setattr(fr, "_http_json", _Http((503, {})))
    with pytest.raises(fr.FederalRegisterUnavailable):
        fr.search_immigration_rules("2026-08-09", terms=("H-1B",))


def test_alarm_unreachable_is_unknown_not_all_clear(monkeypatch):
    monkeypatch.setattr(fr, "_http_json", _Boom())
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["live"] is False
    assert res["stale"] is None      # NOT False — unknown is not "fine"
    assert res["rules"] == []
    assert res["action"] == "retry_or_verify_manually"
    assert "unreachable" in res["note"]


def test_alarm_http_error_is_unknown_not_all_clear(monkeypatch):
    monkeypatch.setattr(fr, "_http_json", _Http((500, {})))
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["live"] is False and res["stale"] is None
    assert "500" in res["note"]


def test_disabled_feed_never_reaches_the_network(monkeypatch):
    monkeypatch.setenv("ELLIS_FEDERAL_REGISTER", "off")
    config.settings.cache_clear()
    monkeypatch.setattr(fr, "_http_json", _Never())

    assert fr.is_configured() is False
    res = fr.check_curated_facts_stale("2026-08-09")
    assert res["live"] is False and res["stale"] is None
    assert "disabled" in res["note"]
    with pytest.raises(fr.FederalRegisterUnavailable):
        fr.search_immigration_rules("2026-08-09")


def test_bad_date_refuses_before_any_network_call(monkeypatch):
    monkeypatch.setattr(fr, "_http_json", _Never())
    with pytest.raises(fr.InvalidSearchWindow):
        fr.search_immigration_rules("09/08/2026", terms=("H-1B",))
    with pytest.raises(fr.InvalidSearchWindow):
        fr.search_immigration_rules("", terms=("H-1B",))
    with pytest.raises(fr.InvalidSearchWindow):
        fr.check_curated_facts_stale("not-a-date")


def test_empty_term_list_refuses_before_any_network_call(monkeypatch):
    monkeypatch.setattr(fr, "_http_json", _Never())
    with pytest.raises(fr.InvalidSearchWindow):
        fr.search_immigration_rules("2026-08-09", terms=())


def test_accepts_a_date_object(monkeypatch):
    import datetime as dt
    http = _one_term(monkeypatch, _fr_body([]))
    fr.search_immigration_rules(dt.date(2026, 8, 9), terms=("H-1B",))
    assert http.calls[0]["kwargs"]["params"][
        "conditions[publication_date][gte]"] == "2026-08-09"


# ===========================================================================
# Federal Register — malformed payloads degrade, never raise
# ===========================================================================
@pytest.mark.parametrize("body", [
    {},                                        # no envelope at all
    {"results": None},                         # results is not a list
    {"results": "nope", "count": "many"},      # wrong types throughout
    {"count": None, "results": []},            # null count
])
def test_malformed_envelope_degrades_to_no_rules(monkeypatch, body):
    _one_term(monkeypatch, body)
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["live"] is True      # the server really answered
    assert res["stale"] is False and res["rules"] == []


def test_malformed_entries_are_dropped_not_invented(monkeypatch):
    body = _fr_body([
        "not-a-dict",
        {"title": "no document number", "publication_date": "2026-08-10"},
        {**_FR_RESULTS[1], "publication_date": "not-a-date",
         "effective_on": "13/40/2026", "agencies": "not-a-list"},
        _FR_RESULTS[0],
    ])
    _one_term(monkeypatch, body)
    rules = fr.search_immigration_rules("2026-08-01", terms=("H-1B",))

    numbers = [r["document_number"] for r in rules]
    assert "2026-16313" in numbers          # the good one survives
    assert len(rules) == 2                  # the string and the number-less one are gone
    broken = next(r for r in rules if r["document_number"] == "2026-16231")
    # Unreadable dates become None; they are never invented or half-parsed.
    assert broken["publication_date"] is None
    assert broken["effective_date"] is None
    assert broken["agencies"] == []


def test_unreadable_publication_date_cannot_trigger_the_alarm(monkeypatch):
    """A rule whose date Ellis cannot read must not be silently treated as
    newer than as_of — an unreadable date is not evidence of staleness."""
    _one_term(monkeypatch, _fr_body([{**_FR_RESULTS[1],
                                      "publication_date": None}]))
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["stale"] is False and res["rules"] == []


def test_an_undated_rule_is_surfaced_instead_of_silently_dropped(monkeypatch):
    """...and it must not VANISH either. The API returned it for a
    `publication_date >= as_of` query, so saying "no rule matched" about it
    would be the false all-clear this whole module exists to prevent."""
    _one_term(monkeypatch, _fr_body([{**_FR_RESULTS[1],
                                      "publication_date": None}]))
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    # Unreadable date is not evidence of staleness...
    assert res["stale"] is False
    assert res["rules"] == []
    # ...but the rule is still in front of the human, with its citation.
    assert res["undated_count"] == 1
    assert res["undated_rules"][0]["document_number"] == "2026-16231"
    assert res["undated_rules"][0]["publication_date"] is None
    assert "fact_areas" in res["undated_rules"][0]
    assert res["action"] == "human_review"
    assert "no readable publication date" in res["note"]


def test_a_clean_all_clear_reports_no_undated_rules(monkeypatch):
    _one_term(monkeypatch, _fr_body([]))
    res = fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    assert res["stale"] is False
    assert res["undated_rules"] == [] and res["undated_count"] == 0
    assert res["action"] == "none"


# ===========================================================================
# Federal Register — caching
# ===========================================================================
def test_caches_a_live_result_per_term(monkeypatch):
    http = _one_term(monkeypatch, _fr_body())
    a = fr.search_immigration_rules("2026-08-09", terms=("H-1B",))
    b = fr.search_immigration_rules("2026-08-09", terms=("H-1B",))
    assert [r["document_number"] for r in a] == [r["document_number"] for r in b]
    assert len(http.calls) == 1          # one round trip, not two


def test_a_different_window_is_a_different_cache_key(monkeypatch):
    http = _one_term(monkeypatch, _fr_body())
    fr.search_immigration_rules("2026-08-09", terms=("H-1B",))
    fr.search_immigration_rules("2026-01-01", terms=("H-1B",))
    assert len(http.calls) == 2


def test_failures_are_never_cached(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(fr, "_http_json", boom)
    fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    fr.check_curated_facts_stale("2026-08-09", terms=("H-1B",))
    # A failure must not be pinned in place of an answer a retry could obtain.
    assert boom.calls == 2


def test_cached_result_cannot_be_mutated_by_a_caller(monkeypatch):
    _one_term(monkeypatch, _fr_body())
    first = fr.search_immigration_rules("2026-08-09", terms=("H-1B",))
    first.clear()
    second = fr.search_immigration_rules("2026-08-09", terms=("H-1B",))
    assert len(second) == 2


# ===========================================================================
# USCIS H-1B Employer Data Hub
# ===========================================================================
# The government file's real columns, verified against the FY2023 export.
_HUB_COLUMNS = ["Fiscal Year", "Employer", "Initial Approval", "Initial Denial",
                "Continuing Approval", "Continuing Denial", "NAICS", "Tax ID",
                "State", "City", "ZIP"]

# Deliberately synthetic employers. A committed fixture that looked like a real
# government approval record would be exactly the artifact this product must
# never produce.
_HUB_ROWS_2023 = [
    # One employer, three pre-aggregated rows (two sites plus a row with no
    # location reported) — a company total is the SUM across them.
    ["2023", "ELLIS TEST WORKS INC", "2", "1", "5", "0", "54", "4242", "NY", "NEW YORK", "10016"],
    ["2023", "ELLIS TEST WORKS INC", "1", "0", "3", "1", "54", "4242", "CA", "SAN JOSE", "95110"],
    ["2023", "ELLIS TEST WORKS INC", "0", "0", "1", "0", "54", "4242", "", "", ""],
    # A distinct employer that happens to share the same last four FEIN digits.
    ["2023", "ELLIS TEST WORKS HOLDINGS LLC", "0", "0", "2", "0", "54", "4242", "TX", "AUSTIN", "78701"],
    # An employer with no adjudications at all — it has no approval rate.
    ["2023", "ELLIS TEST QUIET CO", "0", "0", "0", "0", "51", "9999", "WA", "SEATTLE", "98101"],
]

_HUB_ROWS_2022 = [
    ["2022", "ELLIS TEST WORKS INC", "4", "0", "0", "0", "54", "4242", "NY", "NEW YORK", "10016"],
]


def _write_hub_csv(year, rows, *, columns=None):
    path = hub.year_path(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns or _HUB_COLUMNS)
        w.writerows(rows)
    hub.reset_cache()
    return path


# --- honest unavailability --------------------------------------------------
def test_lookup_is_honestly_unavailable_with_no_cached_file(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    assert hub.is_configured() is False
    assert hub.available_years() == []

    res = hub.lookup_employer("Ellis Test Works Inc")
    assert res["available"] is False
    assert res["live"] is False
    assert res["status"] == hub.UNAVAILABLE
    assert res["matches"] == []          # nothing fabricated
    # The reason names the missing file AND why there is no API to fall back on.
    assert "no cached" in res["note"]
    assert "no machine-readable API" in res["note"]
    assert res["source"] == "uscis_h1b_employer_data_hub"


def test_disabled_feed_is_honestly_unavailable(monkeypatch):
    _write_hub_csv(2023, _HUB_ROWS_2023)
    monkeypatch.setenv("ELLIS_USCIS_EMPLOYER_HUB", "off")
    config.settings.cache_clear()
    monkeypatch.setattr(hub, "_http_bytes", _Never())

    assert hub.is_configured() is False
    res = hub.lookup_employer("Ellis Test Works Inc")
    assert res["available"] is False and res["matches"] == []
    assert "disabled" in res["note"]


def test_uncached_requested_year_is_unavailable_not_empty(monkeypatch):
    _write_hub_csv(2023, _HUB_ROWS_2023)
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    res = hub.lookup_employer("Ellis Test Works Inc", years=[2019])
    assert res["available"] is False and res["matches"] == []
    assert "not cached" in res["note"]


def test_lookup_never_touches_the_network(monkeypatch):
    """The request path is a pure offline read. The only seam in this module is
    the explicit refresh, and a lookup must never reach for it."""
    _write_hub_csv(2023, _HUB_ROWS_2023)
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    res = hub.lookup_employer("Ellis Test Works Inc")
    assert res["available"] is True and res["matches"]


# --- reading the government file --------------------------------------------
def test_lookup_sums_the_pre_aggregated_rows(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    _write_hub_csv(2022, _HUB_ROWS_2022)
    hub.reset_cache()

    res = hub.lookup_employer("Ellis Test Works Inc")
    assert res["status"] == "ok" and res["match_kind"] == "exact_normalized"
    m = res["matches"][0]
    assert m["employer"] == "ELLIS TEST WORKS INC"
    assert m["row_count"] == 4                       # 3 rows in FY23 + 1 in FY22
    assert m["totals"] == {"initial_approval": 7, "initial_denial": 1,
                           "continuing_approval": 9, "continuing_denial": 1}
    # Per fiscal year, keyed by year string.
    assert m["fiscal_years"]["2022"]["initial_approval"] == 4
    assert m["fiscal_years"]["2023"]["continuing_approval"] == 9
    # 16 approvals of 18 adjudications.
    assert m["approval_rate"] == round(16 / 18, 4)
    # A row with no location reported does not invent one.
    assert {"city": "", "state": "", "zip": ""} not in m["locations"]
    assert {"city": "SAN JOSE", "state": "CA", "zip": "95110"} in m["locations"]


def test_no_adjudications_has_no_approval_rate(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    m = hub.lookup_employer("Ellis Test Quiet Co")["matches"][0]
    assert sum(m["totals"].values()) == 0
    # None, not 0.0 — 0.0 would read as "always denied".
    assert m["approval_rate"] is None


def test_name_normalization_matches_the_government_spelling(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    res = hub.lookup_employer("  Ellis Test Works, Inc.  ")
    assert res["match_kind"] == "exact_normalized"
    assert res["matches"][0]["employer"] == "ELLIS TEST WORKS INC"


def test_substring_match_is_labelled_a_candidate(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    res = hub.lookup_employer("Ellis Test Works")
    # No employer is spelled exactly that, so both candidates come back —
    # labelled `contains`, never presented as an identity match.
    assert res["match_kind"] == "contains"
    assert {m["employer"] for m in res["matches"]} == {
        "ELLIS TEST WORKS INC", "ELLIS TEST WORKS HOLDINGS LLC"}


def test_employer_absent_from_the_cache_is_not_evidence_of_never_filing(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    res = hub.lookup_employer("Some Company That Is Not There")
    assert res["available"] is True      # the data was really consulted
    assert res["status"] == hub.NOT_FOUND
    assert res["matches"] == [] and res["match_kind"] == "none"
    assert "not evidence" in res["note"]


# --- the FEIN is only four digits, and says so ------------------------------
def test_fein_narrows_but_never_confirms_and_is_never_echoed(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)

    full_fein = "12-3454242"
    res = hub.lookup_employer(fein=full_fein)

    assert res["fein_match"] == "last4_only"
    assert "does not confirm" in res["fein_match_note"]
    # Two DIFFERENT employers share those four digits — proof that last-four is
    # not an identity.
    assert {m["employer"] for m in res["matches"]} == {
        "ELLIS TEST WORKS INC", "ELLIS TEST WORKS HOLDINGS LLC"}
    # The full FEIN a caller passed never appears anywhere in the answer.
    flat = repr(res)
    assert "123454242" not in flat and full_fein not in flat
    assert res["query"]["fein_last4"] == "4242"


def test_name_and_fein_together_narrow_to_one(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    res = hub.lookup_employer("Ellis Test Quiet Co", fein="9999")
    assert [m["employer"] for m in res["matches"]] == ["ELLIS TEST QUIET CO"]

    # A right name with the wrong four digits is NOT a match, and does not fall
    # back to some other employer.
    miss = hub.lookup_employer("Ellis Test Quiet Co", fein="0000")
    assert miss["matches"] == [] and miss["status"] == hub.NOT_FOUND


def test_lookup_refuses_garbage_before_reading_any_file(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    with pytest.raises(hub.InvalidEmployerQuery):
        hub.lookup_employer("   ")
    with pytest.raises(hub.InvalidEmployerQuery):
        hub.lookup_employer(None, fein=None)
    with pytest.raises(hub.InvalidEmployerQuery):
        hub.lookup_employer(fein="12")           # fewer than four digits
    with pytest.raises(hub.InvalidEmployerQuery):
        hub.lookup_employer("Ellis Test Works Inc", limit=0)


# --- malformed data degrades, never raises ----------------------------------
def test_missing_columns_degrade_to_zero_counts_not_a_crash(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, [["2023", "ELLIS TEST WORKS INC"]],
                   columns=["Fiscal Year", "Employer"])
    res = hub.lookup_employer("Ellis Test Works Inc")
    m = res["matches"][0]
    assert m["totals"] == {k: 0 for k in
                           ("initial_approval", "initial_denial",
                            "continuing_approval", "continuing_denial")}
    assert m["approval_rate"] is None
    assert m["tax_id_last4"] == []       # absent, not invented


def test_junk_count_values_degrade_to_zero(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, [
        ["2023", "ELLIS TEST WORKS INC", "n/a", "", "3", "-", "54", "4242",
         "NY", "NEW YORK", "10016"]])
    m = hub.lookup_employer("Ellis Test Works Inc")["matches"][0]
    assert m["totals"]["initial_approval"] == 0
    assert m["totals"]["continuing_approval"] == 3


def test_rows_with_no_employer_are_dropped(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, [
        ["2023", "", "1", "0", "0", "0", "51", "8070", "DE", "WILMINGTON", "19801"],
        ["2023", "ELLIS TEST WORKS INC", "1", "0", "0", "0", "54", "4242",
         "NY", "NEW YORK", "10016"]])
    # A count that cannot be attributed to an employer is not attributed at all.
    res = hub.lookup_employer("Ellis Test Works Inc")
    assert len(res["matches"]) == 1
    assert res["matches"][0]["totals"]["initial_approval"] == 1


def test_unreadable_file_is_an_unavailable_year_not_a_partial_answer(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    path = hub.year_path(2023)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x01\x02 not a csv at all")
    hub.reset_cache()
    res = hub.lookup_employer("Ellis Test Works Inc")
    assert res["matches"] == []          # no crash, no invented row
    assert res["status"] == hub.NOT_FOUND
    # ...and the year that could not be read SAYS SO. A year that answered
    # nothing because it was unreadable looks exactly like a year that was read
    # and held nothing, unless the failure travels on the result.
    assert res["unreadable_years"] == [2023]
    assert res["coverage"]["unreadable_years"] == [2023]
    assert res["coverage"]["years_searched"] == []
    assert "could not be read" in res["note"]


def test_a_file_that_is_not_the_government_export_is_unreadable(monkeypatch):
    """Well-formed CSV, wrong file. Every row would be dropped for want of an
    employer column, and the lookup would report a clean "not found"."""
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, [["2023", "something else"]],
                   columns=["Fiscal Year", "Not The Employer Column"])
    res = hub.lookup_employer("Ellis Test Works Inc")
    assert res["matches"] == []
    assert res["unreadable_years"] == [2023]


def test_directory_that_does_not_exist_is_an_empty_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("ELLIS_USCIS_EMPLOYER_HUB_DIR", str(tmp_path / "nope"))
    config.settings.cache_clear()
    assert hub.available_years() == []
    assert hub.is_available() is False


# --- cache invalidation -----------------------------------------------------
def test_a_refreshed_year_file_invalidates_the_parse_cache(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    first = hub.lookup_employer("Ellis Test Works Inc")["matches"][0]
    assert first["totals"]["initial_approval"] == 3

    path = _write_hub_csv(2023, [
        ["2023", "ELLIS TEST WORKS INC", "99", "0", "0", "0", "54", "4242",
         "NY", "NEW YORK", "10016"]])
    import os as _os
    _os.utime(path, (0, 0))              # force a distinct mtime
    hub._CACHE.clear()
    second = hub.lookup_employer("Ellis Test Works Inc")["matches"][0]
    assert second["totals"]["initial_approval"] == 99


# --- the refresh path (explicit, opt-in, never automatic) -------------------
def test_fetch_year_plans_without_touching_the_network(monkeypatch):
    never = _Never()
    monkeypatch.setattr(hub, "_http_bytes", never)
    plan = hub.fetch_year(2023)
    assert plan["published"] is True
    assert plan["url"].endswith("h1b_datahubexport-2023.csv")
    assert "ok" not in plan             # planned, not performed


def test_fetch_year_refuses_a_year_uscis_does_not_publish(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    res = hub.fetch_year(2026, execute=True)
    assert res["ok"] is False
    assert res["published"] is False
    assert "no bulk export" in res["error"]
    assert hub.PUBLISHED_BULK_YEARS[-1] == 2023


def test_fetch_year_writes_the_cache_and_enables_lookups(monkeypatch):
    payload = ("\n".join([",".join(_HUB_COLUMNS)] +
                         [",".join(r) for r in _HUB_ROWS_2023])).encode()
    monkeypatch.setattr(hub, "_http_bytes", _Http((200, payload)))
    assert hub.is_configured() is False

    res = hub.fetch_year(2023, execute=True)
    assert res["ok"] is True and res["bytes"] == len(payload)
    assert hub.is_configured() is True
    assert hub.lookup_employer("Ellis Test Works Inc")["matches"][0][
        "totals"]["initial_approval"] == 3


def test_fetch_year_failures_degrade_honestly(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Boom())
    res = hub.fetch_year(2023, execute=True)
    assert res["ok"] is False and "unreachable" in res["error"]
    assert hub.available_years() == []   # nothing written, nothing invented

    monkeypatch.setattr(hub, "_http_bytes", _Http((404, b"")))
    res = hub.fetch_year(2023, execute=True)
    assert res["ok"] is False and "404" in res["error"]
    assert hub.available_years() == []


def test_refresh_status_reports_the_gap_honestly(monkeypatch):
    monkeypatch.setattr(hub, "_http_bytes", _Never())
    _write_hub_csv(2023, _HUB_ROWS_2023)
    st = hub.refresh_status()
    assert st["enabled"] is True and st["configured"] is True
    assert st["years_loaded"] == [2023]
    assert 2009 in st["missing_published_years"]
    # The coverage note says out loud that newer fiscal years are NOT here.
    assert st["coverage"]["latest_published_bulk_year"] == 2023
    assert "Tableau" in st["coverage"]["note"]
    assert "no machine-readable API" in st["access_note"]


# ===========================================================================
# Capability reporting
# ===========================================================================
def test_capabilities_report_both_feeds_honestly(monkeypatch):
    """A capability is what this deployment can ANSWER, not what it enabled.

    The Federal Register API is key-free, so the switch really is the whole
    gate. The Employer Data Hub has no API at all: with the switch on but no
    cached government file, the honest answer is False — reporting True there
    would advertise employer lookups that cannot be performed.
    """
    caps = config.capabilities()
    assert caps["federal_register"] is True
    # Switch on (the autouse fixture sets it), cache dir empty.
    assert caps["uscis_employer_hub_enabled"] is True
    assert caps["uscis_employer_hub"] is False
    assert hub.is_configured() is False

    # It flips only when a real fiscal-year file is on disk.
    _write_hub_csv(2023, _HUB_ROWS_2023)
    assert config.capabilities()["uscis_employer_hub"] is True

    monkeypatch.setenv("ELLIS_FEDERAL_REGISTER", "off")
    monkeypatch.setenv("ELLIS_USCIS_EMPLOYER_HUB", "0")
    config.settings.cache_clear()
    caps = config.capabilities()
    assert caps["federal_register"] is False
    # The switch still overrides data that is present.
    assert caps["uscis_employer_hub"] is False
    assert caps["uscis_employer_hub_enabled"] is False


def test_providers_are_import_safe_offline(monkeypatch):
    """Neither module may perform a network call at import time. Re-executing
    both with httpx itself poisoned proves it: any import-time request would
    raise instead of quietly reaching uscis.gov or federalregister.gov."""
    import importlib

    import httpx

    def _poison(*a, **k):
        raise AssertionError("import time must never touch the network")

    monkeypatch.setattr(httpx, "request", _poison)
    monkeypatch.setattr(httpx, "get", _poison)

    for mod in (fr, hub):
        reloaded = importlib.reload(mod)
        assert reloaded.SOURCE
    # And the reloaded modules still answer honestly with no data present.
    assert hub.lookup_employer("Ellis Test Works Inc")["available"] is False
