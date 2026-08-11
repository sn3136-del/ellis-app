"""Optional third-party corroboration providers — all four, all honest.

Sherpa (route second opinion), Regula (prior visa stickers), DHS E-Verify
(employer corroboration) and WES/ECE (credential evaluation referral) share one
contract: unconfigured, `is_configured()` is False and every caller gets an
explicit unavailable answer with a reason. None of them ever becomes the
answer — Ellis's own curated, sourced record does.

Every test is hermetic. The two providers that speak HTTP have exactly one
network seam (`_http_json`); it is replaced in every test, and the tests assert
what did — and did not — pass through it. No test touches the real network.
"""
import pytest

from app import config
from app.providers import credential_eval, document_read, everify, requirements_oracle


class _Http:
    """Scripted stand-in for a provider's ONE HTTP seam. Records every call so
    a test can prove what reached (or never reached) the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, json_body=None):
        self.calls.append({"method": method, "url": url,
                           "headers": headers or {}, "json": json_body or {}})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {method} {url}")
        return self.responses.pop(0)


class _Boom:
    """A seam that raises — an unreachable host. Records that it was reached,
    so a test can assert an unconfigured provider never called out at all."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise RuntimeError("network down")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Every test starts with all four providers UNCONFIGURED and an empty
    oracle cache, regardless of any real .env present."""
    requirements_oracle.reset_cache()
    for key in ("SHERPA_API_KEY", "REGULA_API_KEY", "REGULA_BASE_URL",
                "EVERIFY_CLIENT_ID", "EVERIFY_CLIENT_SECRET",
                "ELLIS_CREDENTIAL_EVAL"):
        monkeypatch.setenv(key, "")
    config.settings.cache_clear()
    yield
    requirements_oracle.reset_cache()
    config.settings.cache_clear()


def _configure_sherpa(monkeypatch, key="sherpa-test-key"):
    monkeypatch.setenv("SHERPA_API_KEY", key)
    config.settings.cache_clear()


def _configure_regula(monkeypatch, key="regula-test-license"):
    monkeypatch.setenv("REGULA_API_KEY", key)
    config.settings.cache_clear()


def _configure_everify(monkeypatch):
    monkeypatch.setenv("EVERIFY_CLIENT_ID", "ev-client")
    monkeypatch.setenv("EVERIFY_CLIENT_SECRET", "ev-secret")
    config.settings.cache_clear()


def _configure_credential_eval(monkeypatch, partner="wes"):
    monkeypatch.setenv("ELLIS_CREDENTIAL_EVAL", partner)
    config.settings.cache_clear()


# ---------------------------------------------------------------------------
# The shared contract: unconfigured is unavailable, with a reason, offline.
# ---------------------------------------------------------------------------
def test_every_provider_is_unconfigured_by_default():
    assert requirements_oracle.is_configured() is False
    assert document_read.is_configured() is False
    assert everify.is_configured() is False
    assert credential_eval.is_configured() is False


def test_unconfigured_callers_get_available_false_with_a_reason(monkeypatch):
    sherpa_seam, regula_seam = _Boom(), _Boom()
    monkeypatch.setattr(requirements_oracle, "_http_json", sherpa_seam)
    monkeypatch.setattr(document_read, "_http_json", regula_seam)

    route = requirements_oracle.check_route("CHN", "USA")
    visa = document_read.read_prior_visa(b"page-bytes")
    employer = everify.corroborate_employer(everify_company_id="123456")
    referral = credential_eval.referral()

    for result in (route, visa, employer, referral):
        assert result["available"] is False
        assert result.get("note") or result.get("reason")
    assert "not configured" in route["note"]
    assert "not configured" in visa["note"]
    assert "not configured" in employer["reason"]
    assert "configured" in referral["reason"]
    # Nothing is fabricated in place of the answer.
    assert route["verdict"] == "UNKNOWN" and route["documents"] == []
    assert visa["fields"] == {}
    assert employer["enrolled"] is None
    # And no unconfigured provider reached the network.
    assert sherpa_seam.calls == 0 and regula_seam.calls == 0


# ===========================================================================
# Sherpa Requirements API — the route second opinion
# ===========================================================================
# A response shaped like the documented /v3/trips payload: a LEVEL_3 (paper
# visa) verdict whose procedure is referenced from a NESTED grouping.
_SHERPA_VISA_REQUIRED = {
    "data": {
        "type": "TRIP",
        "attributes": {
            "headline": "You need a visa for Vietnam if you have a Chinese passport.",
            "travelOpenness": "LEVEL_3",
            "informationGroups": [
                {"name": "Visa Requirements", "type": "VISA_REQUIREMENTS",
                 "enforcement": "MANDATORY",
                 "groupings": [
                     {"name": "Vietnam", "enforcement": "MANDATORY",
                      "data": [],
                      "groupings": [
                          {"name": "Da Nang", "enforcement": "MANDATORY",
                           "data": [{"type": "PROCEDURE", "id": "proc-1"}]}]}]},
                {"name": "Quarantine", "type": "QUARANTINE", "groupings": []},
            ],
        },
    },
    "included": [
        {"id": "proc-1", "type": "PROCEDURE", "attributes": {
            "category": "DOC_REQUIREMENT", "subCategory": "BEFORE_ARRIVAL",
            "title": "Tourist visa", "description": "Apply before you travel.",
            "enforcement": "MANDATORY", "documentTypes": ["VISA"],
            "lastUpdatedAt": "2026-07-01T08:04:00.000Z",
            "sources": [{"type": "GOVERNMENT", "title": "Vietnam Immigration",
                         "url": "https://evisa.gov.vn/"}]}},
        {"id": "product-9", "type": "PRODUCT", "attributes": {"price": 25}},
    ],
}

# The documented older shape: no travelOpenness, so the verdict falls back to
# the visa group's enforcement.
_SHERPA_NOT_REQUIRED = {
    "data": {"type": "TRIP", "attributes": {
        "headline": "You don't need a visa for Italy if you have a Canadian passport.",
        "informationGroups": [
            {"name": "Visa Requirements", "type": "VISA_REQUIREMENTS",
             "enforcement": "NOT_REQUIRED",
             "groupings": [{"name": "Italy", "enforcement": "NOT_REQUIRED",
                            "data": [{"type": "PROCEDURE", "id": "proc-it"}]}]}]}},
    "included": [
        {"id": "proc-it", "type": "PROCEDURE", "attributes": {
            "title": "Visa is not required", "category": "DOC_REQUIREMENT",
            "enforcement": "NOT_REQUIRED", "documentTypes": ["VISA"],
            "sources": [{"type": "GOVERNMENT",
                         "title": "Ministry of Foreign Affairs of Italy",
                         "url": "https://vistoperitalia.esteri.it/home/en"}]}},
    ],
}


def test_sherpa_parses_a_visa_required_route(monkeypatch):
    _configure_sherpa(monkeypatch)
    http = _Http((200, _SHERPA_VISA_REQUIRED))
    monkeypatch.setattr(requirements_oracle, "_http_json", http)

    res = requirements_oracle.check_route("CHN", "VNM",
                                          departure_date="2026-09-12")

    assert res["available"] is True
    assert res["verdict"] == "VISA_REQUIRED"      # travelOpenness LEVEL_3
    assert res["travel_openness"] == "LEVEL_3"
    assert res["documents"] == ["VISA"]
    # The procedure was found through a NESTED grouping, two levels down.
    assert [p["id"] for p in res["procedures"]] == ["proc-1"]
    assert res["procedures"][0]["title"] == "Tourist visa"
    assert res["fetched_at"]
    # source_url is the ORACLE endpoint — never a government page.
    assert res["source_url"].endswith("/v3/trips")
    assert res["source"] == "sherpa"
    # The government links the vendor cites come back separately, as leads.
    assert res["government_sources"] == [
        {"title": "Vietnam Immigration", "type": "GOVERNMENT",
         "url": "https://evisa.gov.vn/"}]
    # Vendor prose never reaches an applicant.
    assert res["applicant_visible"] is False
    assert res["caveat"] == requirements_oracle.REDISTRIBUTION_CAVEAT


def test_sherpa_sends_the_documented_request(monkeypatch):
    _configure_sherpa(monkeypatch)
    http = _Http((200, _SHERPA_VISA_REQUIRED))
    monkeypatch.setattr(requirements_oracle, "_http_json", http)

    requirements_oracle.check_route("CHN", "VNM", origin="CHN",
                                    departure_date="2026-09-12")

    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["headers"]["x-api-key"] == "sherpa-test-key"
    assert call["headers"]["content-type"] == "application/vnd.api+json"
    attrs = call["json"]["data"]["attributes"]
    assert call["json"]["data"]["type"] == "TRIP"
    assert attrs["traveller"] == {"passports": ["CHN"]}
    nodes = attrs["travelNodes"]
    assert [n["type"] for n in nodes] == ["ORIGIN", "DESTINATION"]
    assert nodes[0]["locationCode"] == "CHN"
    assert nodes[1]["locationCode"] == "VNM"
    assert nodes[0]["departure"]["date"] == "2026-09-12"


def test_sherpa_falls_back_to_enforcement_when_there_is_no_openness(monkeypatch):
    _configure_sherpa(monkeypatch)
    monkeypatch.setattr(requirements_oracle, "_http_json",
                        _Http((200, _SHERPA_NOT_REQUIRED)))
    res = requirements_oracle.check_route("CAN", "ITA", departure_date="2026-09-12")
    assert res["verdict"] == "NO_VISA_REQUIRED"
    assert res["travel_openness"] == ""
    assert res["enforcement"] == "NOT_REQUIRED"


def test_sherpa_hedged_enforcement_stays_unknown(monkeypatch):
    """MAY_BE_REQUIRED is Sherpa hedging. Hedging is not a verdict, and it must
    never turn into a disagreement with Ellis's record."""
    _configure_sherpa(monkeypatch)
    body = {"data": {"attributes": {"informationGroups": [
        {"type": "VISA_REQUIREMENTS", "enforcement": "MAY_BE_REQUIRED",
         "groupings": []}]}}}
    monkeypatch.setattr(requirements_oracle, "_http_json", _Http((200, body)))
    res = requirements_oracle.check_route("CHN", "VNM")
    assert res["available"] is True and res["verdict"] == "UNKNOWN"
    assert requirements_oracle.compare_with_curated(
        "EMBASSY_VISA_REQUIRED", res)["result"] == "unknown"


def test_sherpa_assumed_inputs_are_stated_not_hidden(monkeypatch):
    _configure_sherpa(monkeypatch)
    monkeypatch.setattr(requirements_oracle, "_http_json",
                        _Http((200, _SHERPA_VISA_REQUIRED)))
    res = requirements_oracle.check_route("CHN", "VNM")
    assert res["origin_assumed"] is True and res["origin"] == "CHN"
    assert res["departure_date_assumed"] is True and res["departure_date"]


# --- input validation: refuse before the network -----------------------------
@pytest.mark.parametrize("kwargs", [
    {"nationality": "CN", "destination": "USA"},        # alpha-2
    {"nationality": "CHN", "destination": "usa1"},      # not a code
    {"nationality": "", "destination": "USA"},          # missing
    {"nationality": "CHN", "destination": "USA", "origin": "XX"},
])
def test_sherpa_rejects_bad_codes_before_any_call(monkeypatch, kwargs):
    _configure_sherpa(monkeypatch)
    boom = _Boom()
    monkeypatch.setattr(requirements_oracle, "_http_json", boom)
    with pytest.raises(requirements_oracle.InvalidRouteInput):
        requirements_oracle.check_route(**kwargs)
    assert boom.calls == 0


@pytest.mark.parametrize("bad_date", ["12/09/2026", "2026-13-01", "soon"])
def test_sherpa_rejects_bad_dates_before_any_call(monkeypatch, bad_date):
    _configure_sherpa(monkeypatch)
    boom = _Boom()
    monkeypatch.setattr(requirements_oracle, "_http_json", boom)
    with pytest.raises(requirements_oracle.InvalidRouteInput):
        requirements_oracle.check_route("CHN", "USA", departure_date=bad_date)
    assert boom.calls == 0


def test_sherpa_validates_before_checking_configuration(monkeypatch):
    """A typo must not be excused just because the provider happens to be off."""
    with pytest.raises(requirements_oracle.InvalidRouteInput):
        requirements_oracle.check_route("CHINA", "USA")


# --- honest degradation ------------------------------------------------------
def test_sherpa_network_failure_degrades_honestly(monkeypatch):
    _configure_sherpa(monkeypatch)
    monkeypatch.setattr(requirements_oracle, "_http_json", _Boom())
    res = requirements_oracle.check_route("CHN", "USA")
    assert res["available"] is False and res["verdict"] == "UNKNOWN"
    assert res["documents"] == [] and res["procedures"] == []
    assert "unreachable" in res["note"]


def test_sherpa_http_error_degrades_honestly(monkeypatch):
    _configure_sherpa(monkeypatch)
    monkeypatch.setattr(requirements_oracle, "_http_json", _Http((403, {})))
    res = requirements_oracle.check_route("CHN", "USA")
    assert res["available"] is False and "403" in res["note"]


# --- the short TTL Sherpa's terms ask for ------------------------------------
def test_sherpa_caches_live_results_for_one_hour(monkeypatch):
    _configure_sherpa(monkeypatch)
    http = _Http((200, _SHERPA_VISA_REQUIRED))
    monkeypatch.setattr(requirements_oracle, "_http_json", http)
    a = requirements_oracle.check_route("CHN", "VNM", departure_date="2026-09-12")
    b = requirements_oracle.check_route("CHN", "VNM", departure_date="2026-09-12")
    assert a is b and len(http.calls) == 1
    assert requirements_oracle.CACHE_TTL_SECONDS == 3600


def test_sherpa_cache_expires_and_refetches(monkeypatch):
    _configure_sherpa(monkeypatch)
    http = _Http((200, _SHERPA_VISA_REQUIRED), (200, _SHERPA_NOT_REQUIRED))
    monkeypatch.setattr(requirements_oracle, "_http_json", http)
    clock = [1_000_000.0]
    monkeypatch.setattr(requirements_oracle.time, "time", lambda: clock[0])

    first = requirements_oracle.check_route("CHN", "VNM", departure_date="2026-09-12")
    clock[0] += requirements_oracle.CACHE_TTL_SECONDS + 1
    second = requirements_oracle.check_route("CHN", "VNM", departure_date="2026-09-12")

    assert first["verdict"] == "VISA_REQUIRED"
    assert second["verdict"] == "NO_VISA_REQUIRED"   # a genuinely fresh read
    assert len(http.calls) == 2


def test_sherpa_does_not_cache_a_degraded_result(monkeypatch):
    _configure_sherpa(monkeypatch)
    boom = _Boom()
    monkeypatch.setattr(requirements_oracle, "_http_json", boom)
    requirements_oracle.check_route("CHN", "USA")
    requirements_oracle.check_route("CHN", "USA")
    assert boom.calls == 2   # a failure is never pinned in place of an answer


# ---------------------------------------------------------------------------
# compare_with_curated — the curation loop's actual question
# ---------------------------------------------------------------------------
def test_comparison_agrees_across_vocabularies():
    for curated, verdict in (("VISA_FREE", "NO_VISA_REQUIRED"),
                             ("VISA_EXEMPT", "NO_VISA_REQUIRED"),
                             ("ETA_REQUIRED", "ELECTRONIC_AUTHORIZATION"),
                             ("VISA_ON_ARRIVAL", "ELECTRONIC_AUTHORIZATION"),
                             ("EMBASSY_VISA_REQUIRED", "VISA_REQUIRED"),
                             ("AUTHORIZED_VISA_CENTER", "VISA_REQUIRED"),
                             ("NO_TOURIST_ROUTE", "ENTRY_RESTRICTED")):
        out = requirements_oracle.compare_with_curated(curated, verdict)
        assert out["result"] == "agree", (curated, verdict)
        assert out["review_flag"] is False


def test_comparison_disagreement_raises_a_review_flag_only():
    curated = {"disposition": "VISA_FREE", "sources": ["gov.example"]}
    snapshot = dict(curated)

    out = requirements_oracle.compare_with_curated(curated, "VISA_REQUIRED")

    assert out["result"] == "disagree"
    assert out["review_flag"] is True
    assert out["curated"] == "VISA_FREE" and out["oracle"] == "VISA_REQUIRED"
    assert "human review" in out["note"]
    # The curated record is untouched: a disagreement never overwrites Ellis.
    assert curated == snapshot


def test_comparison_distinguishes_electronic_from_paper():
    """LEVEL_2 vs LEVEL_3 is a real disagreement: an applicant sent to an
    embassy when an eVisa would do (or the reverse) loses weeks."""
    out = requirements_oracle.compare_with_curated("EVISA_REQUIRED", "VISA_REQUIRED")
    assert out["result"] == "disagree" and out["review_flag"] is True


def test_comparison_coarse_oracle_answer_is_not_a_disagreement():
    """AUTHORIZATION_REQUIRED means 'something is needed' without saying which
    kind. That agrees with any authorization Ellis names."""
    for curated in ("EVISA_REQUIRED", "EMBASSY_VISA_REQUIRED"):
        out = requirements_oracle.compare_with_curated(
            curated, "AUTHORIZATION_REQUIRED")
        assert out["result"] == "agree"
    # It does contradict "nothing is required".
    out = requirements_oracle.compare_with_curated("VISA_FREE",
                                                   "AUTHORIZATION_REQUIRED")
    assert out["result"] == "disagree"


@pytest.mark.parametrize("curated,verdict", [
    ("RESEARCH_INCOMPLETE", "VISA_REQUIRED"),
    ("HUMAN_REVIEW_REQUIRED", "NO_VISA_REQUIRED"),
    ("CONDITIONAL", "VISA_REQUIRED"),
    ("", "VISA_REQUIRED"),
    ("VISA_FREE", "UNKNOWN"),
    ("VISA_FREE", ""),
])
def test_comparison_is_unknown_when_either_side_is_not_confident(curated, verdict):
    out = requirements_oracle.compare_with_curated(curated, verdict)
    assert out["result"] == "unknown"
    assert out["review_flag"] is False


def test_comparison_of_an_unavailable_oracle_is_unknown():
    """A degraded oracle result must never read as a contradiction."""
    unavailable = requirements_oracle.check_route("CHN", "USA")  # unconfigured
    out = requirements_oracle.compare_with_curated("VISA_FREE", unavailable)
    assert out["result"] == "unknown" and out["review_flag"] is False


def test_comparison_accepts_a_whole_oracle_result(monkeypatch):
    _configure_sherpa(monkeypatch)
    monkeypatch.setattr(requirements_oracle, "_http_json",
                        _Http((200, _SHERPA_VISA_REQUIRED)))
    res = requirements_oracle.check_route("CHN", "VNM", departure_date="2026-09-12")
    out = requirements_oracle.compare_with_curated("EMBASSY_VISA_REQUIRED", res)
    assert out["result"] == "agree"
    assert out["result"] in requirements_oracle.COMPARISON_RESULTS


# ===========================================================================
# Regula — prior visa stickers, and never an identity source
# ===========================================================================
def _text_field(field_type, value, *, probability=98, validity=1, name=""):
    return {"fieldType": field_type, "fieldName": name, "lcid": 0,
            "value": value, "validityStatus": validity,
            "valueList": [{"source": "VISUAL", "value": value,
                           "probability": probability, "pageIndex": 0}]}


# A response shaped like Regula's ContainerList: the visa sticker fields Ellis
# wants, alongside the identity fields it must never carry out of the module.
_REGULA_VISA_PAGE = {
    "ProcessingFinished": 1,
    "ContainerList": {"Count": 2, "List": [
        {"result_type": 33, "Status": {"overallStatus": 1}},
        {"result_type": 36, "Text": {"status": 1, "fieldList": [
            _text_field(29, "12345678901", name="Visa ID"),
            _text_field(196, "AB1234567", name="Visa number"),
            _text_field(30, "H", name="Visa class"),
            _text_field(100, "H-1B", name="Visa type"),
            _text_field(101, "2024-03-15", probability=95),
            _text_field(102, "2027-03-14", probability=91),
            _text_field(104, "M", name="Number of entries"),
            # Identity fields the reader happily returns and Ellis drops.
            _text_field(8, "ZHANG", name="Surname"),
            _text_field(9, "WEI", name="Given names"),
            _text_field(2, "E12345678", name="Document number"),
            _text_field(1, "CHN", name="Issuing state code"),
        ]}},
    ]},
}


def test_regula_reads_only_the_visa_sticker_fields(monkeypatch):
    _configure_regula(monkeypatch)
    monkeypatch.setattr(document_read, "_http_json",
                        _Http((200, _REGULA_VISA_PAGE)))

    res = document_read.read_prior_visa(b"jpeg-bytes")

    assert res["available"] is True
    assert res["fields"] == {
        "visa_id": "12345678901",
        "visa_number": "AB1234567",
        "visa_class": "H",
        "visa_type": "H-1B",
        "visa_valid_from": "2024-03-15",
        "visa_valid_until": "2027-03-14",
        "number_of_entries": "M",
    }
    # Lowest per-field probability, as a 0-1 confidence.
    assert res["confidence"] == pytest.approx(0.91)
    assert res["warnings"] == []


def test_regula_output_can_never_satisfy_identity(monkeypatch):
    """The whole point. Identity comes from a checksum-valid TD3 MRZ on the
    biodata page and from nowhere else — so no identity field may leave this
    module even when the vendor returns one."""
    _configure_regula(monkeypatch)
    monkeypatch.setattr(document_read, "_http_json",
                        _Http((200, _REGULA_VISA_PAGE)))

    res = document_read.read_prior_visa(b"jpeg-bytes")

    serialized = repr(res)
    for leaked in ("ZHANG", "WEI", "E12345678"):
        assert leaked not in serialized
    for banned in document_read.IDENTITY_FIELDS_NEVER_RETURNED:
        assert banned not in res["fields"]
    assert res["identity_source"] is False
    assert document_read.can_establish_identity(res) is False
    # Unconditionally, for any result at all.
    assert document_read.can_establish_identity({"fields": {"surname": "X"}}) is False
    assert document_read.can_establish_identity() is False


def test_regula_whitelist_holds_no_identity_field():
    values = set(document_read._VISA_FIELDS.values())
    assert values.isdisjoint(set(document_read.IDENTITY_FIELDS_NEVER_RETURNED))
    assert values and all(v.startswith(("visa_", "duration_", "number_of_"))
                          for v in values)


def test_regula_sends_the_documented_request(monkeypatch):
    _configure_regula(monkeypatch)
    http = _Http((200, _REGULA_VISA_PAGE))
    monkeypatch.setattr(document_read, "_http_json", http)

    document_read.read_prior_visa(b"jpeg-bytes")

    call = http.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/api/process")
    params = call["json"]["processParam"]
    assert params["scenario"] == "FullProcess"
    assert params["resultTypeOutput"] == [36]     # Result.TEXT
    assert params["dateFormat"] == "yyyy-MM-dd"   # Ellis is ISO-internal
    image = call["json"]["List"][0]["ImageData"]["image"]
    import base64
    assert base64.b64decode(image) == b"jpeg-bytes"
    # The license rides in the documented place, and only when configured.
    assert call["json"]["systemInfo"]["license"] == "regula-test-license"


def test_regula_self_hosted_base_url_is_used(monkeypatch):
    monkeypatch.setenv("REGULA_BASE_URL", "http://regula.internal:8080")
    config.settings.cache_clear()
    http = _Http((200, _REGULA_VISA_PAGE))
    monkeypatch.setattr(document_read, "_http_json", http)

    assert document_read.is_configured() is True
    document_read.read_prior_visa(b"jpeg-bytes")
    assert http.calls[0]["url"] == "http://regula.internal:8080/api/process"
    # No license key set → nothing invented in its place.
    assert "systemInfo" not in http.calls[0]["json"]


def test_regula_normalizes_a_printed_date_to_iso(monkeypatch):
    _configure_regula(monkeypatch)
    body = {"ContainerList": {"List": [{"result_type": 36, "Text": {"fieldList": [
        _text_field(101, "15 MAR 2024"), _text_field(102, "not a date")]}}]}}
    monkeypatch.setattr(document_read, "_http_json", _Http((200, body)))

    res = document_read.read_prior_visa(b"jpeg-bytes")

    assert res["fields"]["visa_valid_from"] == "2024-03-15"
    # An unreadable date is left exactly as printed and flagged, never guessed.
    assert res["fields"]["visa_valid_until"] == "not a date"
    assert any("could not be read as a date" in w for w in res["warnings"])


def test_regula_reports_a_page_with_no_sticker(monkeypatch):
    _configure_regula(monkeypatch)
    body = {"ContainerList": {"List": [{"result_type": 36, "Text": {"fieldList": [
        _text_field(8, "ZHANG", name="Surname")]}}]}}
    monkeypatch.setattr(document_read, "_http_json", _Http((200, body)))

    res = document_read.read_prior_visa(b"jpeg-bytes")

    assert res["available"] is True and res["fields"] == {}
    assert res["confidence"] is None
    assert res["warnings"] == [
        "no visa-sticker fields were recognized on this page"]


def test_regula_surfaces_a_failed_validity_check(monkeypatch):
    _configure_regula(monkeypatch)
    body = {"ContainerList": {"List": [{"result_type": 36, "Text": {"fieldList": [
        _text_field(29, "12345678901", validity=0)]}}]}}
    monkeypatch.setattr(document_read, "_http_json", _Http((200, body)))
    res = document_read.read_prior_visa(b"jpeg-bytes")
    assert res["fields"]["visa_id"] == "12345678901"
    assert res["warnings"] == ["visa_id failed the reader's validity check"]


@pytest.mark.parametrize("payload", [b"", "not bytes", None])
def test_regula_rejects_bad_input_before_any_call(monkeypatch, payload):
    _configure_regula(monkeypatch)
    boom = _Boom()
    monkeypatch.setattr(document_read, "_http_json", boom)
    with pytest.raises(document_read.InvalidDocumentImage):
        document_read.read_prior_visa(payload)
    assert boom.calls == 0


def test_regula_rejects_an_oversized_image(monkeypatch):
    _configure_regula(monkeypatch)
    boom = _Boom()
    monkeypatch.setattr(document_read, "_http_json", boom)
    with pytest.raises(document_read.InvalidDocumentImage):
        document_read.read_prior_visa(b"x" * (document_read.MAX_IMAGE_BYTES + 1))
    assert boom.calls == 0


def test_regula_degrades_honestly(monkeypatch):
    _configure_regula(monkeypatch)
    monkeypatch.setattr(document_read, "_http_json", _Boom())
    res = document_read.read_prior_visa(b"jpeg-bytes")
    assert res["available"] is False and res["fields"] == {}
    assert "unreachable" in res["note"]

    monkeypatch.setattr(document_read, "_http_json", _Http((403, {})))
    res = document_read.read_prior_visa(b"jpeg-bytes")
    assert res["available"] is False and "license" in res["note"]

    monkeypatch.setattr(document_read, "_http_json", _Http((500, {})))
    res = document_read.read_prior_visa(b"jpeg-bytes")
    assert res["available"] is False and "500" in res["note"]


def test_regula_failure_note_never_leaks_the_document(monkeypatch):
    _configure_regula(monkeypatch)

    class _Leaky:
        def __call__(self, *a, **k):
            raise RuntimeError("failed uploading ZHANG/E12345678 page bytes")

    monkeypatch.setattr(document_read, "_http_json", _Leaky())
    res = document_read.read_prior_visa(b"jpeg-bytes")
    assert "ZHANG" not in repr(res) and "E12345678" not in repr(res)


# ===========================================================================
# DHS E-Verify — a seam that is deliberately not an integration
# ===========================================================================
def test_everify_never_creates_a_case(monkeypatch):
    with pytest.raises(everify.EmploymentEligibilityOutOfScope):
        everify.create_case(employer="Acme", employee="Zhang")
    _configure_everify(monkeypatch)
    # Configuring credentials does not buy a filing path.
    with pytest.raises(everify.EmploymentEligibilityOutOfScope):
        everify.create_case()


def test_everify_capability_is_honest_both_ways(monkeypatch):
    cap = everify.capability()
    assert cap["configured"] is False
    assert cap["supports_case_creation"] is False
    assert cap["supports_dhs_enrollment_lookup"] is False
    assert cap["petition_role"] == "not_a_petition_step"

    _configure_everify(monkeypatch)
    cap = everify.capability()
    assert cap["configured"] is True
    assert cap["supports_case_creation"] is False


def test_everify_configured_without_evidence_reports_nothing(monkeypatch):
    _configure_everify(monkeypatch)
    res = everify.corroborate_employer()
    assert res["available"] is False and res["enrolled"] is None
    assert "no E-Verify enrollment evidence" in res["reason"]


def test_everify_reports_an_employer_claim_as_a_claim(monkeypatch):
    _configure_everify(monkeypatch)
    res = everify.corroborate_employer(everify_company_id="1234567")
    assert res["available"] is True
    assert res["enrolled"] is True
    # Ellis never asked DHS, so Ellis never says DHS agreed.
    assert res["verified"] is False
    assert res["basis"] == "employer_declared_company_id"
    assert res["basis"] in everify.EVIDENCE_BASES
    assert res["supports_case_creation"] is False

    res = everify.corroborate_employer(evidence_document_id="doc-42")
    assert res["basis"] == "uploaded_evidence" and res["verified"] is False


def test_everify_has_no_dhs_lookup_basis():
    assert "dhs_lookup" not in everify.EVIDENCE_BASES


# ===========================================================================
# WES / ECE — a referral and tracking seam, never a claimed report
# ===========================================================================
@pytest.mark.parametrize("partner", ["wes", "ece"])
def test_partner_info_is_complete(partner):
    info = credential_eval.partner_info(partner)
    assert info["partner"] == partner
    assert info["name"] and info["order_url"].startswith("https://")
    assert "business days" in info["typical_turnaround"]
    assert info["ordered_by"] == "applicant"
    assert info["as_of"] == credential_eval.FACTS_AS_OF
    assert "Confirm on the partner's site" in info["caveat"]
    # The Chinese-credential route rides on every referral.
    note = info["verification_note"]
    assert "CHSI" in note and "CDGDC" in note
    assert "毕业证" in note and "学位证" in note


def test_partner_info_is_case_insensitive_and_refuses_strangers():
    assert credential_eval.partner_info(" WES ")["partner"] == "wes"
    for bad in ("", "naces", "wes-inc", None):
        with pytest.raises(credential_eval.UnknownEvaluationPartner):
            credential_eval.partner_info(bad)


def test_referral_degrades_but_still_teaches(monkeypatch):
    res = credential_eval.referral()
    assert res["available"] is False
    assert "no credential evaluation partner" in res["reason"]
    # No partner does not mean no guidance: the verification route still holds.
    assert "CHSI" in res["verification_note"]

    _configure_credential_eval(monkeypatch, "ece")
    assert credential_eval.is_configured() is True
    assert credential_eval.configured_partner() == "ece"
    res = credential_eval.referral()
    assert res["available"] is True and res["partner"] == "ece"


def test_an_unknown_configured_partner_is_not_configured(monkeypatch):
    _configure_credential_eval(monkeypatch, "some-other-vendor")
    assert credential_eval.is_configured() is False
    assert credential_eval.configured_partner() == ""
    assert credential_eval.referral()["available"] is False


def test_ordering_claims_nothing_about_a_report():
    res = credential_eval.record_evaluation_ordered(
        partner="wes", ordered_at="2026-08-01T00:00:00+00:00", reference="X1")
    assert res["status"] == "ordered"
    assert res["report_on_file"] is False
    assert res["satisfies_checklist_item"] is False
    assert res["reference"] == "X1"
    assert res["ordered_at"] == "2026-08-01T00:00:00+00:00"

    stamped = credential_eval.record_evaluation_ordered(partner="ece")
    assert stamped["ordered_at"]     # stamped, never left blank

    with pytest.raises(credential_eval.UnknownEvaluationPartner):
        credential_eval.record_evaluation_ordered(partner="acme")


def test_a_report_exists_only_when_its_document_does():
    with pytest.raises(credential_eval.EvaluationNotOnFile):
        credential_eval.record_evaluation_received(partner="wes", document_id="")
    with pytest.raises(credential_eval.EvaluationNotOnFile):
        credential_eval.record_evaluation_received(
            partner="wes", document_id="doc-1", accepted=False)

    res = credential_eval.record_evaluation_received(
        partner="wes", document_id="doc-1")
    assert res["status"] == "received"
    assert res["report_on_file"] is True
    assert res["satisfies_checklist_item"] is True
    assert res["document_id"] == "doc-1"
    assert "human review" in res["note"]


# ===========================================================================
# Capability reporting — every seam is reported honestly, and none of them
# is ever a filing path.
# ===========================================================================
def test_capabilities_report_each_provider_honestly(monkeypatch):
    caps = config.capabilities()
    assert caps["sherpa_requirements"] is False
    assert caps["regula_document_read"] is False
    assert caps["everify_employer"] is False
    assert caps["credential_evaluation_partner"] == "none"

    _configure_sherpa(monkeypatch)
    _configure_regula(monkeypatch)
    _configure_everify(monkeypatch)
    _configure_credential_eval(monkeypatch, "wes")

    caps = config.capabilities()
    assert caps["sherpa_requirements"] is True
    assert caps["regula_document_read"] is True
    assert caps["everify_employer"] is True
    assert caps["credential_evaluation_partner"] == "wes"
