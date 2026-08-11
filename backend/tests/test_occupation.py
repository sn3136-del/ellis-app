"""Occupation/industry classification providers — SOC (NIOCCS/O*NET) and NAICS.

Every test is hermetic: the provider's single HTTP seam (`_http_json`) is
replaced, and each test asserts what did — or did not — pass through it. No test
touches the real network.

The invariants under test are honesty invariants. A classification is a
SUGGESTION with a confidence, never an authoritative code: on bad input the
provider refuses before any call; on a network failure it degrades with
`live: False` and NO fabricated code; NIOCCS always carries its "no longer
supported" caveat; and O*NET, unconfigured, says so rather than guessing.
"""
import pytest

from app import config
from app.providers import occupation


# The verified NIOCCS sample: occupation → SOC 2018, industry → NAICS 2017.
_NIOCCS_SAMPLE = {
    "Occupation": [
        {"Code": "15-1252", "Title": "Software Developers", "Probability": 0.98},
        {"Code": "15-1211", "Title": "Computer Systems Analysts", "Probability": 0.41},
    ],
    "Industry": [
        {"Code": "54151", "Title": "Computer Systems Design and Related Services",
         "Probability": "0.87"},
    ],
}


class _Http:
    """Scripted stand-in for the provider's ONE HTTP seam. Records every call so
    a test can prove what reached (or never reached) the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, params=None):
        self.calls.append({"method": method, "url": url,
                           "headers": headers or {}, "params": params or {}})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {method} {url}")
        return self.responses.pop(0)


class _Boom:
    """A seam that raises — a stand-in for an unreachable host. Records that it
    was reached, so a test can assert a retry did (or did not) call again."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise RuntimeError("network down")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Every test starts with an empty classification cache and O*NET
    unconfigured, regardless of any real .env present."""
    occupation.reset_cache()
    monkeypatch.setenv("ONET_API_KEY", "")
    config.settings.cache_clear()
    yield
    occupation.reset_cache()
    config.settings.cache_clear()


def _configure_onet(monkeypatch, key="onet-test-key"):
    monkeypatch.setenv("ONET_API_KEY", key)
    config.settings.cache_clear()


# ---------------------------------------------------------------------------
# NIOCCS — parsing the verified sample
# ---------------------------------------------------------------------------
def test_nioccs_parses_verified_sample(monkeypatch):
    http = _Http((200, _NIOCCS_SAMPLE))
    monkeypatch.setattr(occupation, "_http_json", http)

    res = occupation.classify_nioccs(
        industry_text="computer systems design",
        occupation_text="software developer", n=3)

    assert res["live"] is True
    assert res["source"] == "nioccs"
    assert res["caveat"] == occupation.NIOCCS_CAVEAT
    # SOC 2018 from the occupation side.
    assert res["soc"][0] == {"code": "15-1252", "title": "Software Developers",
                             "probability": 0.98}
    assert [s["code"] for s in res["soc"]] == ["15-1252", "15-1211"]
    # NAICS 2017 from the industry side; a numeric-string probability coerces.
    assert res["naics"] == [{"code": "54151",
                             "title": "Computer Systems Design and Related Services",
                             "probability": 0.87}]
    # The single call carried the documented query params.
    assert http.calls[0]["params"] == {"i": "computer systems design",
                                        "o": "software developer", "n": 3,
                                        "t": "json"}


def test_nioccs_drops_entry_with_no_code(monkeypatch):
    body = {"Occupation": [{"Title": "no code here", "Probability": 0.9},
                           {"Code": "15-1252", "Title": "Software Developers"}],
            "Industry": []}
    monkeypatch.setattr(occupation, "_http_json", _Http((200, body)))
    res = occupation.classify_nioccs(occupation_text="dev")
    assert [s["code"] for s in res["soc"]] == ["15-1252"]
    assert res["soc"][0]["probability"] is None  # missing probability, not invented
    assert res["naics"] == []


# ---------------------------------------------------------------------------
# NIOCCS — input validation (refuse before any network call)
# ---------------------------------------------------------------------------
def test_nioccs_rejects_empty_input(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(occupation, "_http_json", boom)
    with pytest.raises(occupation.InvalidClassificationInput):
        occupation.classify_nioccs(industry_text="   ", occupation_text="")
    assert boom.calls == 0  # refused BEFORE the network


def test_nioccs_rejects_non_string_input(monkeypatch):
    monkeypatch.setattr(occupation, "_http_json", _Boom())
    with pytest.raises(occupation.InvalidClassificationInput):
        occupation.classify_nioccs(occupation_text=15)  # type: ignore[arg-type]


def test_nioccs_rejects_bad_n(monkeypatch):
    monkeypatch.setattr(occupation, "_http_json", _Boom())
    with pytest.raises(occupation.InvalidClassificationInput):
        occupation.classify_nioccs(occupation_text="dev", n=0)


# ---------------------------------------------------------------------------
# NIOCCS — honest degradation, never a fabricated code
# ---------------------------------------------------------------------------
def test_nioccs_network_failure_degrades_honestly(monkeypatch):
    monkeypatch.setattr(occupation, "_http_json", _Boom())
    res = occupation.classify_nioccs(industry_text="x", occupation_text="dev")
    assert res["live"] is False
    assert res["soc"] == [] and res["naics"] == []  # nothing fabricated
    assert res["source"] == "nioccs"
    assert res["caveat"] == occupation.NIOCCS_CAVEAT
    assert "unreachable" in res["note"]


def test_nioccs_http_error_degrades_honestly(monkeypatch):
    monkeypatch.setattr(occupation, "_http_json", _Http((503, {})))
    res = occupation.classify_nioccs(occupation_text="dev")
    assert res["live"] is False
    assert res["soc"] == [] and res["naics"] == []
    assert "503" in res["note"]


# ---------------------------------------------------------------------------
# NIOCCS — caching: live results cached, degraded results are not
# ---------------------------------------------------------------------------
def test_nioccs_caches_live_result(monkeypatch):
    http = _Http((200, _NIOCCS_SAMPLE))
    monkeypatch.setattr(occupation, "_http_json", http)
    a = occupation.classify_nioccs(industry_text="A", occupation_text="dev")
    b = occupation.classify_nioccs(industry_text="A", occupation_text="dev")
    assert a is b               # same cached object
    assert len(http.calls) == 1  # only ONE network round trip


def test_nioccs_does_not_cache_degraded_result(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(occupation, "_http_json", boom)
    occupation.classify_nioccs(occupation_text="dev")
    occupation.classify_nioccs(occupation_text="dev")
    # A failure must not be pinned in place of a real answer a retry could get.
    assert boom.calls == 2


# ---------------------------------------------------------------------------
# O*NET — unconfigured degrades honestly
# ---------------------------------------------------------------------------
def test_onet_unconfigured_is_honestly_unavailable(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(occupation, "_http_json", boom)
    res = occupation.search_onet("software developer")
    assert res == {"matches": [], "source": "onet", "live": False,
                   "note": "O*NET API key not configured"}
    assert boom.calls == 0  # no key → never reaches the network


def test_onet_rejects_empty_keyword():
    with pytest.raises(occupation.InvalidClassificationInput):
        occupation.search_onet("   ")


# ---------------------------------------------------------------------------
# O*NET — configured parses matches and sends the API key
# ---------------------------------------------------------------------------
def test_onet_configured_parses_matches(monkeypatch):
    _configure_onet(monkeypatch)
    body = {"occupation": [
        {"code": "15-1252.00", "title": "Software Developers"},
        {"code": "15-1211.00", "title": "Computer Systems Analysts"},
    ]}
    http = _Http((200, body))
    monkeypatch.setattr(occupation, "_http_json", http)

    res = occupation.search_onet("software developer")
    assert res["live"] is True
    assert res["source"] == "onet"
    assert res["matches"] == [
        {"code": "15-1252.00", "title": "Software Developers"},
        {"code": "15-1211.00", "title": "Computer Systems Analysts"},
    ]
    # The free key is presented as X-API-Key on the one call.
    assert http.calls[0]["headers"]["X-API-Key"] == "onet-test-key"
    assert http.calls[0]["params"] == {"keyword": "software developer"}


def test_onet_configured_network_failure_degrades(monkeypatch):
    _configure_onet(monkeypatch)
    monkeypatch.setattr(occupation, "_http_json", _Boom())
    res = occupation.search_onet("dev")
    assert res["live"] is False and res["matches"] == []
    assert "unreachable" in res["note"]


def test_onet_configured_http_error_degrades(monkeypatch):
    _configure_onet(monkeypatch)
    monkeypatch.setattr(occupation, "_http_json", _Http((403, {})))
    res = occupation.search_onet("dev")
    assert res["live"] is False and res["matches"] == []
    assert "403" in res["note"]


# ---------------------------------------------------------------------------
# NAICS — offline validation against the committed 2022 reference
# ---------------------------------------------------------------------------
def test_validate_naics_valid():
    res = occupation.validate_naics("541511")
    assert res == {"valid": True, "code": "541511",
                   "title": "Custom Computer Programming Services",
                   "source": "census"}


def test_validate_naics_normalizes_formatting():
    # Humans type separators; normalization strips them but never repairs.
    res = occupation.validate_naics(" 5415-1 ")
    assert res["valid"] is True and res["code"] == "54151"


def test_validate_naics_invalid():
    res = occupation.validate_naics("999999")
    assert res == {"valid": False, "code": "999999", "source": "census"}
    # An empty code is invalid, not an exception.
    assert occupation.validate_naics("")["valid"] is False


# ---------------------------------------------------------------------------
# Capability reporting — O*NET presence is reported honestly
# ---------------------------------------------------------------------------
def test_capabilities_reports_onet_honestly(monkeypatch):
    assert config.capabilities()["onet_occupation_search"] is False
    _configure_onet(monkeypatch)
    assert config.capabilities()["onet_occupation_search"] is True
