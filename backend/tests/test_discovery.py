"""Portal discovery: official-domain classification, disabled-draft only,
Kimi tool allowlist, admin review."""
from tests.conftest import AUTH
from app.portal import discovery
from app.providers.kimi import validate_tool_call, ToolSecurityError


def test_domain_classification():
    assert discovery.classify_domain("https://evisa.gov.vn/apply")["is_government_domain"] is True
    assert discovery.classify_domain("https://india.gov.in")["is_government_domain"] is True
    assert discovery.classify_domain("https://totally-fake-visa.example.com")["is_government_domain"] is False
    assert discovery.classify_domain("https://vfsglobal.com/x")["is_known_contractor"] is True


def test_query_variants_cover_official_terms():
    qs = discovery.query_variants("Vietnam")
    joined = " ".join(qs).lower()
    assert "official" in joined and "e-visa" in joined and "embassy" in joined


def test_discovery_returns_disabled_draft():
    d = discovery.discover_official_visa_portal(country="Vietnam")
    assert d["adapter_status"] == "disabled_draft"
    assert d["production_enabled"] is False
    assert d["requires_admin_review"] is True
    # No search provider in tests → honest 'unavailable', never fabricated hits.
    assert d["candidates"] == []
    assert "unavailable" in d["search_status"]


def test_discovery_tool_is_allowlisted_but_readonly():
    # Kimi may propose it (read-only); dangerous tools still rejected.
    validate_tool_call("discover_official_visa_portal", {"country": "Vietnam", "visa_type": "tourist"})
    import pytest
    for bad in ("book_appointment", "submit_application", "pay_fee"):
        with pytest.raises(ToolSecurityError):
            validate_tool_call(bad, {})


def test_discovery_api_creates_disabled_draft_and_review(client):
    r = client.post("/discovery", headers=AUTH, json={"country": "Vietnam"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["adapter_status"] == "disabled_draft" and body["production_enabled"] is False
    did = body["draft_id"]
    # Admin review marks the draft only — never creates a live adapter.
    rv = client.post(f"/discovery/drafts/{did}/review", headers=AUTH, json={"decision": "approved"})
    assert rv.status_code == 200
    assert "no live adapter" in rv.json()["note"]
