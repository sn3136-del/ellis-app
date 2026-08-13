"""Field-quality seams — address validation and visa-photo compliance.

Every test is hermetic: each module's single HTTP seam is replaced, and the
tests assert what did — and did not — pass through it. Nothing here touches the
real network, and no applicant photo ever leaves the process.

The invariants under test are honesty invariants:

  * Unconfigured means UNAVAILABLE with a reason, never a verdict. The photo
    checker is the one exception, and only in the direction that is safe: its
    deterministic pre-check still runs, and the result says out loud that it
    is PARTIAL.
  * A 'corrected' address is a SUGGESTION. It never auto-overwrites, because a
    worksite address on an LCA is a legal statement and only the human who
    signs may change it. `auto_apply` is False on every result, and
    `apply_correction` moves nothing unless `accepted` is the literal True.
  * 'unverified' is not 'invalid'.
  * Every photo failure carries a how_to_fix a human can actually act on.
  * A rule that was not checked reads as 'unknown', never as compliant.
"""
import json

import pytest

from app import config
from app.providers import address_verify, photo_check


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------
class _Http:
    """Scripted stand-in for a module's ONE HTTP seam. Records every call so a
    test can prove what reached (or never reached) the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unscripted HTTP call: {method} {url}")
        return self.responses.pop(0)


class _Boom:
    """A seam that must never be reached, or that stands in for an unreachable
    host. Records the attempt either way."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise RuntimeError("network down")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Both seams unconfigured regardless of any real .env present."""
    for key in ("ELLIS_ADDRESS_VERIFY", "ELLIS_ADDRESS_VERIFY_KEY",
                "ELLIS_PHOTO_CHECK", "REGULA_BASE_URL", "REGULA_API_KEY"):
        monkeypatch.setenv(key, "")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


def _configure_address(monkeypatch, provider="smarty", key="auth-id:auth-token"):
    monkeypatch.setenv("ELLIS_ADDRESS_VERIFY", provider)
    monkeypatch.setenv("ELLIS_ADDRESS_VERIFY_KEY", key)
    config.settings.cache_clear()


def _configure_photo(monkeypatch, base="https://faces.internal.example"):
    monkeypatch.setenv("ELLIS_PHOTO_CHECK", "regula")
    monkeypatch.setenv("REGULA_BASE_URL", base)
    config.settings.cache_clear()


# ---------------------------------------------------------------------------
# Fixtures: an address, and real image headers
# ---------------------------------------------------------------------------
US_ADDRESS = {"address1": "1 Hacker Way", "locality": "Menlo Park",
              "administrative_area": "CA", "postal_code": "94025"}


def _smarty_candidate(*, address1="1 Hacker Way", locality="Menlo Park",
                      administrative_area="CA", postal_code="94025",
                      verification="Verified", precision="DeliveryPoint"):
    return {
        "address1": address1,
        "address2": f"{locality} {administrative_area} {postal_code}",
        "components": {"country_iso_3": "USA", "locality": locality,
                       "administrative_area": administrative_area,
                       "postal_code": postal_code,
                       "thoroughfare": "Hacker Way"},
        "analysis": {"verification_status": verification,
                     "address_precision": precision,
                     "max_address_precision": "DeliveryPoint"},
    }


def _pad(data: bytes, size: int | None) -> bytes:
    if size and size > len(data):
        return data + b"\x00" * (size - len(data))
    return data


def _png(width: int, height: int, size: int | None = None) -> bytes:
    head = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00")
    return _pad(head, size)


def _jpeg(width: int, height: int, size: int | None = None) -> bytes:
    app0_payload = b"JFIF\x00" + bytes(9)
    app0 = b"\xff\xe0" + (2 + len(app0_payload)).to_bytes(2, "big") + app0_payload
    sof = (b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
           + height.to_bytes(2, "big") + width.to_bytes(2, "big")
           + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    return _pad(b"\xff\xd8" + app0 + sof + b"\xff\xd9", size)


def _all_pass_details(config_names=None):
    names = config_names or photo_check._QUALITY_CONFIG
    return [{"name": n, "group": "Image", "result": 1, "value": 1}
            for n in names]


def _detect_body(details):
    return {"results": {"detections": [
        {"faceIndex": 0, "quality": {"score": 100, "details": details}}]}}


# ===========================================================================
# Address verification
# ===========================================================================
def test_address_unconfigured_is_unavailable_and_never_calls_out(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(address_verify, "_http", boom)

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert address_verify.is_configured() is False
    assert res["available"] is False
    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert res["normalized"] is None
    assert res["changes"] == {}
    assert res["source"] == address_verify.SOURCE_NONE
    assert res["auto_apply"] is False
    assert res["requires_human_acceptance"] is False
    # Unverified is not invalid, and the payload says so.
    assert "not invalid" in res["note"]
    assert boom.calls == 0


def test_unrecognized_provider_is_no_provider(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(address_verify, "_http", boom)
    _configure_address(monkeypatch, provider="melissa", key="k")

    assert address_verify.active_provider() == ""
    assert address_verify.is_configured() is False
    res = address_verify.verify_address(US_ADDRESS, "US")
    assert res["available"] is False
    assert "not configured" in res["note"]
    assert boom.calls == 0


def test_known_provider_without_credentials_degrades(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(address_verify, "_http", boom)
    _configure_address(monkeypatch, provider="smarty", key="")

    assert address_verify.is_configured() is False
    res = address_verify.verify_address(US_ADDRESS, "US")
    assert res["available"] is False
    assert res["source"] == address_verify.SOURCE_SMARTY
    assert "credentials" in res["note"]
    assert boom.calls == 0


@pytest.mark.parametrize("address,country", [
    ("1 Hacker Way", "US"),                       # not a dict
    ({"address1": "1 Hacker Way"}, ""),           # no country anywhere
    ({"locality": "Paris"}, "FR"),                # no street line, no freeform
    ({"address1": ["1 Hacker Way"]}, "US"),       # not text
])
def test_bad_address_raises_before_any_call(monkeypatch, address, country):
    boom = _Boom()
    monkeypatch.setattr(address_verify, "_http", boom)
    _configure_address(monkeypatch)

    with pytest.raises(address_verify.InvalidAddress):
        address_verify.verify_address(address, country)
    assert boom.calls == 0


def test_country_may_ride_on_the_address(monkeypatch):
    http = _Http((200, [_smarty_candidate()]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    address_verify.verify_address(dict(US_ADDRESS, country="United States"))
    assert http.calls[0]["params"]["country"] == "United States"


def test_smarty_verified_as_typed(monkeypatch):
    http = _Http((200, [_smarty_candidate()]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert res["available"] is True
    assert res["status"] == address_verify.STATUS_VERIFIED
    assert res["changes"] == {}
    assert res["requires_human_acceptance"] is False
    assert res["auto_apply"] is False
    assert res["warnings"] == []
    assert res["normalized"]["address1"] == "1 Hacker Way"
    assert res["components"]["country_iso_3"] == "USA"
    assert res["source"] == address_verify.SOURCE_SMARTY
    # The street suggestion is the vendor's first postal line; the envelope
    # lines are components, never a second street field.
    assert "formatted_lines" in res["components"]
    assert "address2" not in (res["normalized"] or {})


def test_smarty_casing_and_punctuation_are_not_a_correction(monkeypatch):
    http = _Http((200, [_smarty_candidate()]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(
        dict(US_ADDRESS, address1="1 hacker way."), "US")

    assert res["status"] == address_verify.STATUS_VERIFIED
    assert res["changes"] == {}


def test_corrected_address_is_a_suggestion_and_never_auto_applies(monkeypatch):
    http = _Http((200, [_smarty_candidate(address1="1 Hacker Way")]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    original = dict(US_ADDRESS, address1="1 Hackers Way")
    snapshot = dict(original)

    res = address_verify.verify_address(original, "US")

    assert res["status"] == address_verify.STATUS_CORRECTED
    assert res["requires_human_acceptance"] is True
    # The constant that makes the rule structural.
    assert res["auto_apply"] is False
    assert res["changes"]["address1"] == {"from": "1 Hackers Way",
                                          "to": "1 Hacker Way"}
    assert "SUGGESTION" in res["note"]
    # The address handed in was not touched.
    assert original == snapshot
    # And nothing moves until a human says so — with the literal True.
    assert address_verify.apply_correction(
        original, res, accepted=False) == snapshot
    assert address_verify.apply_correction(
        original, res, accepted="yes")["address1"] == "1 Hackers Way"
    assert address_verify.apply_correction(
        original, res, accepted=1)["address1"] == "1 Hackers Way"
    accepted = address_verify.apply_correction(original, res, accepted=True)
    assert accepted["address1"] == "1 Hacker Way"
    # Still not touched: apply_correction returns a new dict.
    assert original == snapshot


def test_accepted_correction_lands_on_the_callers_own_field_names(monkeypatch):
    http = _Http((200, [_smarty_candidate(address1="1 Hacker Way")]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    caller = {"street": "1 Hackers Way", "city": "Menlo Park",
              "state": "CA", "zip": "94025", "case_id": "abc"}
    res = address_verify.verify_address(caller, "US")
    merged = address_verify.apply_correction(caller, res, accepted=True)

    assert merged["street"] == "1 Hacker Way"
    assert "address1" not in merged
    assert merged["case_id"] == "abc"


def test_an_added_field_is_a_suggestion_not_a_silent_fill(monkeypatch):
    http = _Http((200, [_smarty_candidate()]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    typed = {"address1": "1 Hacker Way", "locality": "Menlo Park",
             "administrative_area": "CA"}
    res = address_verify.verify_address(typed, "US")

    assert res["status"] == address_verify.STATUS_CORRECTED
    assert res["changes"]["postal_code"] == {"from": "", "to": "94025"}
    assert "postal_code" not in address_verify.apply_correction(
        typed, res, accepted=False)


def test_review_prompt_asks_rather_than_asserts(monkeypatch):
    http = _Http((200, [_smarty_candidate(address1="1 Hacker Way")]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(
        dict(US_ADDRESS, address1="1 Hackers Way"), "US")
    prompt = address_verify.review_prompt(res)

    assert "suggests" in prompt
    assert "government form" in prompt
    assert "1 Hacker Way" in prompt
    # Nothing to decide on a verified address.
    assert address_verify.review_prompt(
        {"status": address_verify.STATUS_VERIFIED}) == ""


@pytest.mark.parametrize("verification,precision,fragment", [
    ("Partial", "Locality", "only to Locality precision"),
    ("Ambiguous", "Premise", "several possible matches"),
    ("None", "None", "could not match this address"),
])
def test_smarty_non_verified_never_becomes_a_verdict(
        monkeypatch, verification, precision, fragment):
    http = _Http((200, [_smarty_candidate(verification=verification,
                                          precision=precision)]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert res["available"] is True
    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert res["requires_human_acceptance"] is False
    assert any(fragment in w for w in res["warnings"])
    assert "not invalid" in res["note"]


def test_multiple_candidates_is_never_verified(monkeypatch):
    http = _Http((200, [_smarty_candidate(),
                        _smarty_candidate(address1="1 Hacker Way Apt 2")]))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert any("2 candidate addresses" in w for w in res["warnings"])


def test_smarty_empty_result_set_is_unverified_not_wrong(monkeypatch):
    http = _Http((200, []))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert res["available"] is True
    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert res["normalized"] is None
    assert "not invalid" in res["note"]


def test_address_transport_failure_degrades_without_inventing(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(address_verify, "_http", boom)
    _configure_address(monkeypatch)

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert boom.calls == 1
    assert res["available"] is False
    assert res["normalized"] is None
    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert "unreachable" in res["note"]


@pytest.mark.parametrize("code", [401, 402, 429, 500])
def test_address_http_error_never_leaks_the_credential(monkeypatch, code):
    http = _Http((code, {"errors": [{"message": "auth-id super-secret-key"}]}))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch, key="super-secret-key")

    res = address_verify.verify_address(US_ADDRESS, "US")

    assert res["available"] is False
    assert "super-secret-key" not in json.dumps(res)


def test_smarty_auth_pair_and_embedded_key(monkeypatch):
    http = _Http((200, [_smarty_candidate()]), (200, [_smarty_candidate()]))
    monkeypatch.setattr(address_verify, "_http", http)

    _configure_address(monkeypatch, key="the-id:the-token")
    address_verify.verify_address(US_ADDRESS, "US")
    params = http.calls[0]["params"]
    assert params["auth-id"] == "the-id"
    assert params["auth-token"] == "the-token"
    assert "key" not in params
    assert params["address1"] == "1 Hacker Way"
    assert params["locality"] == "Menlo Park"

    _configure_address(monkeypatch, key="embedded-key")
    address_verify.verify_address(US_ADDRESS, "US")
    assert http.calls[1]["params"]["key"] == "embedded-key"
    assert "auth-id" not in http.calls[1]["params"]


def _loqate_body(avc, *, address1="1 Hacker Way", locality="Menlo Park",
                 postal="94025", flat=False):
    match = {"AVC": avc, "AQI": "A", "Address1": address1,
             "Locality": locality, "AdministrativeArea": "CA",
             "PostalCode": postal, "CountryName": "United States"}
    return {"Items": [match]} if flat else {"Items": [{"Matches": [match]}]}


def test_loqate_verified_and_corrected(monkeypatch):
    http = _Http((200, _loqate_body("V44-I44-P6-100")),
                 (200, _loqate_body("V44-I44-P6-100",
                                    address1="1 Hacker Way")))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch, provider="loqate", key="loqate-key")

    res = address_verify.verify_address(US_ADDRESS, "US")
    assert res["source"] == address_verify.SOURCE_LOQATE
    assert res["status"] == address_verify.STATUS_VERIFIED
    body = http.calls[0]["json_body"]
    assert body["Key"] == "loqate-key"
    assert body["Addresses"][0]["Country"] == "US"
    assert "1 Hacker Way" in body["Addresses"][0]["Address"]

    res2 = address_verify.verify_address(
        dict(US_ADDRESS, address1="1 Hackers Way"), "US")
    assert res2["status"] == address_verify.STATUS_CORRECTED
    assert res2["auto_apply"] is False


def test_loqate_flat_items_shape_is_tolerated(monkeypatch):
    http = _Http((200, _loqate_body("V44-I44-P6-100", flat=True)))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch, provider="loqate", key="k")

    assert address_verify.verify_address(US_ADDRESS, "US")["status"] == \
        address_verify.STATUS_VERIFIED


@pytest.mark.parametrize("avc,fragment", [
    ("P33-I44-P6-080", "only part of the address matched"),
    ("A33-I44-P6-080", "several reference records"),
    ("U00-I00-P0-000", "could not verify"),
    ("R00-I00-P0-000", "minimum verification standard"),
])
def test_loqate_non_verified_codes(monkeypatch, avc, fragment):
    http = _Http((200, _loqate_body(avc)))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch, provider="loqate", key="k")

    res = address_verify.verify_address(US_ADDRESS, "US")
    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert any(fragment in w for w in res["warnings"])


def test_loqate_unreadable_code_is_never_promoted_to_verified(monkeypatch):
    http = _Http((200, _loqate_body("Z99-I44-P6-100")))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch, provider="loqate", key="k")

    res = address_verify.verify_address(US_ADDRESS, "US")
    assert res["status"] == address_verify.STATUS_UNVERIFIED
    assert any("does not recognize" in w for w in res["warnings"])


def test_loqate_error_item_degrades(monkeypatch):
    http = _Http((200, {"Items": [{"Error": "2", "Description": "Unknown key"}]}))
    monkeypatch.setattr(address_verify, "_http", http)
    _configure_address(monkeypatch, provider="loqate", key="k")

    res = address_verify.verify_address(US_ADDRESS, "US")
    assert res["available"] is False
    assert res["normalized"] is None


# ===========================================================================
# Photo compliance
# ===========================================================================
def test_photo_unconfigured_still_helps_and_says_it_is_partial(monkeypatch):
    boom = _Boom()
    monkeypatch.setattr(photo_check, "_http_json", boom)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    assert photo_check.is_configured() is False
    assert res["available"] is False
    assert res["source"] == photo_check.SOURCE_PRECHECK
    assert res["partial"] is True
    # A pass on a partial check is never "compliant".
    assert res["compliant"] == "unknown"
    assert res["compliant"] is not True
    assert res["failures"] == []
    assert res["note"].startswith("PARTIAL CHECK")
    # The deterministic rules really ran...
    assert set(res["checks_performed"]) == {
        "file_format", "file_size", "image_dimensions", "aspect_ratio"}
    # ...and the ones nobody checked are named, not glossed over.
    for rule in ("BackgroundUniformity", "Smile", "DarkGlasses", "HeadHeightRatio"):
        assert rule in res["checks_not_performed"]
    assert boom.calls == 0


def test_photo_provider_without_a_host_is_unconfigured(monkeypatch):
    """No default endpoint: Ellis will not pick a host to send a face to."""
    boom = _Boom()
    monkeypatch.setattr(photo_check, "_http_json", boom)
    monkeypatch.setenv("ELLIS_PHOTO_CHECK", "regula")
    monkeypatch.setenv("REGULA_BASE_URL", "")
    config.settings.cache_clear()

    assert photo_check.active_provider() == "regula"
    assert photo_check.is_configured() is False
    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")
    assert res["available"] is False
    assert "no photo-compliance service is configured" in res["note"]
    assert boom.calls == 0


def test_deterministic_failure_is_a_real_failure(monkeypatch):
    monkeypatch.setattr(photo_check, "_http_json", _Boom())

    res = photo_check.check_photo(_jpeg(300, 300, 20_000), "us_visa_digital")

    assert res["compliant"] is False
    rules = [f["rule"] for f in res["failures"]]
    assert "image_dimensions" in rules
    failure = next(f for f in res["failures"] if f["rule"] == "image_dimensions")
    assert "300x300" in failure["detail"]
    assert "600x600" in failure["detail"]
    assert failure["checked_by"] == photo_check.SOURCE_PRECHECK
    assert "camera" in failure["how_to_fix"].lower()


def test_oversized_and_wrong_shape_photo_gets_every_fixable_reason(monkeypatch):
    monkeypatch.setattr(photo_check, "_http_json", _Boom())

    res = photo_check.check_photo(_jpeg(4032, 3024, 3_000_000),
                                  "us_visa_digital")

    rules = {f["rule"] for f in res["failures"]}
    assert {"file_size", "image_dimensions", "aspect_ratio"} <= rules
    assert res["compliant"] is False
    for failure in res["failures"]:
        assert failure["how_to_fix"].strip()
        assert failure["detail"].strip()


def test_every_failure_carries_a_human_readable_fix(monkeypatch):
    """Across pre-check and vendor paths alike: no failure without a fix."""
    _configure_photo(monkeypatch)
    details = _all_pass_details()
    for detail in details:
        if detail["name"] in ("BackgroundUniformity", "Smile", "DarkGlasses",
                              "Yaw", "MedicalMask", "EyesRed"):
            detail["result"] = 0
            detail["range"] = {"min": 0, "max": 10}
            detail["value"] = 42
    http = _Http((200, _detect_body(details)))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(300, 300, 20_000), "us_visa_digital")

    assert len(res["failures"]) >= 7
    for failure in res["failures"]:
        assert failure["rule"]
        assert failure["detail"].strip()
        assert len(failure["how_to_fix"].strip()) > 20
        assert failure["checked_by"] in (photo_check.SOURCE_PRECHECK,
                                         photo_check.SOURCE_REGULA)
    # Both checkers are represented, each owning its own findings.
    assert {f["checked_by"] for f in res["failures"]} == {
        photo_check.SOURCE_PRECHECK, photo_check.SOURCE_REGULA}
    background = next(f for f in res["failures"]
                      if f["rule"] == "BackgroundUniformity")
    assert "plain" in background["how_to_fix"].lower()
    assert "accepted range 0 to 10" in background["detail"]


@pytest.mark.parametrize("bad", [b"", "not bytes", 12, None])
def test_bad_photo_input_raises(bad):
    with pytest.raises(photo_check.InvalidPhotoImage):
        photo_check.check_photo(bad, "us_visa_digital")


def test_absurdly_large_upload_raises_before_any_check():
    with pytest.raises(photo_check.InvalidPhotoImage):
        photo_check.check_photo(b"\xff\xd8\xff" +
                                bytes(photo_check.MAX_IMAGE_BYTES),
                                "us_visa_digital")


@pytest.mark.parametrize("spec", [None, "", {}, "atlantis_visa", 7])
def test_missing_or_unknown_spec_raises_rather_than_defaulting(spec):
    """Ellis never picks a country's photo rules on the applicant's behalf."""
    with pytest.raises(photo_check.InvalidPhotoSpec):
        photo_check.check_photo(_jpeg(600, 600, 40_000), spec)


def test_every_published_spec_carries_a_source_and_an_as_of():
    for name in photo_check.SPECS:
        spec = photo_check.spec_for(name)
        assert spec["source"].startswith("http")
        assert spec["as_of"]
        assert spec["notes"]


def test_unreadable_file_reports_format_and_skips_the_pixel_rules(monkeypatch):
    monkeypatch.setattr(photo_check, "_http_json", _Boom())

    res = photo_check.check_photo(b"%PDF-1.7 not a photo at all",
                                  "us_visa_digital")

    assert res["compliant"] is False
    fmt = next(f for f in res["failures"] if f["rule"] == "file_format")
    assert "JPEG" in fmt["how_to_fix"]
    assert "image_dimensions" in res["checks_not_performed"]
    assert "aspect_ratio" in res["checks_not_performed"]
    assert any("could not read the pixel dimensions" in w
               for w in res["warnings"])


def test_wrong_format_is_named_for_the_human(monkeypatch):
    monkeypatch.setattr(photo_check, "_http_json", _Boom())

    res = photo_check.check_photo(_png(600, 600, 40_000), "us_visa_digital")

    fmt = next(f for f in res["failures"] if f["rule"] == "file_format")
    assert "PNG" in fmt["detail"]
    assert "JPEG" in fmt["detail"]


def test_specs_without_a_rule_report_it_as_unchecked(monkeypatch):
    monkeypatch.setattr(photo_check, "_http_json", _Boom())

    res = photo_check.check_photo(_png(350, 450, 60_000), "schengen_visa")

    # 35:45 shape passes; the pixel minimum this spec does not publish is
    # reported as not checked rather than silently passed.
    assert [f["rule"] for f in res["failures"]] == []
    assert "aspect_ratio" in res["checks_performed"]
    assert "image_dimensions" in res["checks_not_performed"]
    assert res["compliant"] == "unknown"

    square = photo_check.check_photo(_png(600, 600, 60_000), "schengen_visa")
    assert any(f["rule"] == "aspect_ratio" for f in square["failures"])


def test_uk_minimum_file_size_catches_a_messenger_compressed_photo(monkeypatch):
    monkeypatch.setattr(photo_check, "_http_json", _Boom())

    res = photo_check.check_photo(_png(600, 750, 30_000), "uk_digital")

    failure = next(f for f in res["failures"] if f["rule"] == "file_size")
    assert "50 KB" in failure["detail"]
    assert "compressed" in failure["how_to_fix"]


def test_read_image_header_reads_both_formats():
    assert photo_check.read_image_header(_png(413, 531)) == {
        "format": "png", "width": 413, "height": 531}
    assert photo_check.read_image_header(_jpeg(600, 600)) == {
        "format": "jpeg", "width": 600, "height": 600}
    assert photo_check.read_image_header(b"\x00\x01\x02\x03") == {
        "format": "", "width": None, "height": None}


def test_vendor_all_clear_is_the_only_way_to_reach_compliant(monkeypatch):
    _configure_photo(monkeypatch)
    http = _Http((200, _detect_body(_all_pass_details())))
    monkeypatch.setattr(photo_check, "_http_json", http)
    photo = _jpeg(600, 600, 40_000)

    res = photo_check.check_photo(photo, "us_visa_digital")

    assert photo_check.is_configured() is True
    assert res["available"] is True
    assert res["compliant"] is True
    assert res["partial"] is False
    assert res["failures"] == []
    assert res["checks_not_performed"] == []
    assert res["source"] == photo_check.SOURCE_REGULA
    # Never a promise of acceptance.
    assert "not a promise of acceptance" in res["note"]
    # Recency is asked, never assumed — no image check can establish it.
    assert res["human_confirmation_required"][0]["rule"] == "recency"
    assert "6 months" in res["human_confirmation_required"][0]["question"]


def test_vendor_request_carries_only_the_documented_body(monkeypatch):
    import base64

    _configure_photo(monkeypatch, base="https://faces.internal.example/")
    http = _Http((200, _detect_body(_all_pass_details())))
    monkeypatch.setattr(photo_check, "_http_json", http)
    photo = _jpeg(600, 600, 40_000)

    res = photo_check.check_photo(photo, "us_visa_digital")

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == "https://faces.internal.example/api/detect"
    body = call["json_body"]
    assert set(body) == {"tag", "image", "processParam"}
    assert body["image"] == base64.b64encode(photo).decode()
    assert body["processParam"]["quality"]["config"] == list(
        photo_check._QUALITY_CONFIG)
    # The photo goes to the service and nowhere else — not into the result.
    assert body["image"] not in json.dumps(res)


def test_a_vendor_failure_is_reported_with_its_fix(monkeypatch):
    _configure_photo(monkeypatch)
    details = _all_pass_details()
    for detail in details:
        if detail["name"] == "MouthOpen":
            detail["result"] = 0
    http = _Http((200, _detect_body(details)))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    assert res["available"] is True
    assert res["compliant"] is False
    failure = next(f for f in res["failures"] if f["rule"] == "MouthOpen")
    assert failure["checked_by"] == photo_check.SOURCE_REGULA
    assert "mouth" in failure["how_to_fix"].lower()


@pytest.mark.parametrize("value", [2, "maybe", None, "", {"nested": 1}])
def test_an_unassessed_characteristic_reads_as_unknown_not_compliant(
        monkeypatch, value):
    _configure_photo(monkeypatch)
    details = _all_pass_details()
    details[0]["result"] = value
    http = _Http((200, _detect_body(details)))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    assert res["available"] is True
    assert res["compliant"] == "unknown"
    assert res["compliant"] is not True
    assert res["failures"] == []
    assert res["partial"] is True
    assert details[0]["name"] in res["checks_not_performed"]


def test_no_face_is_a_failure_a_human_can_act_on(monkeypatch):
    _configure_photo(monkeypatch)
    http = _Http((200, {"results": {"detections": []}}))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    assert res["available"] is True
    assert res["compliant"] is False
    failure = next(f for f in res["failures"] if f["rule"] == "face_detected")
    assert "portrait" in failure["how_to_fix"]


def test_a_second_face_is_caught(monkeypatch):
    _configure_photo(monkeypatch)
    body = _detect_body(_all_pass_details())
    body["results"]["detections"].append({"faceIndex": 1, "quality": {}})
    http = _Http((200, body))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    failure = next(f for f in res["failures"] if f["rule"] == "OtherFaces")
    assert "2 faces" in failure["detail"]
    assert res["compliant"] is False


def test_vendor_unreachable_falls_back_to_the_partial_check(monkeypatch):
    _configure_photo(monkeypatch)
    boom = _Boom()
    monkeypatch.setattr(photo_check, "_http_json", boom)

    clean = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")
    assert boom.calls == 1
    assert clean["available"] is False
    assert clean["partial"] is True
    assert clean["compliant"] == "unknown"
    assert clean["source"] == photo_check.SOURCE_PRECHECK
    assert any("unavailable" in w for w in clean["warnings"])
    assert "unreachable" in clean["note"]
    assert clean["note"].startswith("PARTIAL CHECK")

    # A deterministic violation still stands on its own with no vendor.
    bad = photo_check.check_photo(_jpeg(300, 300, 20_000), "us_visa_digital")
    assert bad["compliant"] is False
    assert any(f["rule"] == "image_dimensions" for f in bad["failures"])


def test_vendor_http_error_never_becomes_a_verdict(monkeypatch):
    _configure_photo(monkeypatch)
    http = _Http((500, {"message": "internal"}))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    assert res["available"] is False
    assert res["compliant"] == "unknown"
    assert "HTTP 500" in res["note"]
    assert "internal" not in json.dumps(res)


def test_unreadable_vendor_response_is_not_a_photo_without_a_face(monkeypatch):
    _configure_photo(monkeypatch)
    http = _Http((200, {"code": 0}))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), "us_visa_digital")

    assert res["available"] is False
    assert res["compliant"] == "unknown"
    assert not any(f["rule"] == "face_detected" for f in res["failures"])


def test_a_spec_may_narrow_the_ask_without_weakening_the_verdict(monkeypatch):
    """A deployment whose service supports fewer characteristics narrows the
    ASK. What it does not do is let an unassessed rule read as a pass."""
    _configure_photo(monkeypatch)
    narrow = dict(photo_check.spec_for("us_visa_digital"),
                  quality_config=("BackgroundUniformity", "MouthOpen"))
    http = _Http((200, _detect_body(_all_pass_details(
        ("BackgroundUniformity", "MouthOpen")))))
    monkeypatch.setattr(photo_check, "_http_json", http)

    res = photo_check.check_photo(_jpeg(600, 600, 40_000), narrow)

    assert http.calls[0]["json_body"]["processParam"]["quality"]["config"] == [
        "BackgroundUniformity", "MouthOpen"]
    assert res["compliant"] is True
    assert res["checks_not_performed"] == []
