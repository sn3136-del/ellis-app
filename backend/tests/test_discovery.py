"""Portal discovery: official-domain classification, disabled-draft only,
Kimi tool allowlist, admin review, evidence completeness, change detection."""
import pytest

from tests.conftest import AUTH
from app.portal import discovery
from app.portal.discovery import SearchResult, PortalSnapshot
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


def test_query_variants_include_all_required_brief_forms():
    qs = discovery.query_variants("Japan", nationality="Chinese", residence="Singapore")
    joined = " ".join(qs).lower()
    for needle in ("japan embassy", "embassy tourist visa", "official japan tourist visa",
                   "official japan e-visa", "japan immigration tourist visa",
                   "ministry of foreign affairs visa", "consulate tourist visa",
                   "authorized japan visa application center", "japan visa chinese",
                   "japan visa singapore"):
        assert needle in joined, needle


def test_query_variants_omit_nationality_residence_when_absent():
    qs = discovery.query_variants("Japan")
    assert not any("residents" in q for q in qs)


@pytest.fixture
def fake_search():
    """Register a controlled fake search provider, always cleared afterwards."""
    def provider(query):
        return [
            SearchResult(url="https://totally-fake-visa.example.com/apply",
                         title="Cheap Japan Visa!!!", excerpt="pay us to get your visa fast"),
            SearchResult(url="https://www.mofa.go.jp/visa/tourist",
                         title="Ministry of Foreign Affairs of Japan — Visa",
                         excerpt="Official tourist visa information."),
        ]
    discovery.set_search_provider(provider)
    yield
    discovery.set_search_provider(None)


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


def test_draft_carries_full_evidence_fields():
    d = discovery.discover_official_visa_portal(country="Japan", visa_type="tourist",
                                                nationality="Chinese", residence="Singapore",
                                                reviewer="admin-1", verification_timestamp="2026-07-22T00:00:00Z")
    for key in ("country", "visa_type", "applicant_nationality", "applicant_residence",
                "queries", "candidates", "verified_candidates", "official_verification_urls",
                "portal_operator", "domain_evidence", "verification_timestamp", "effective_date",
                "reviewer", "review_status", "portal_limitations", "review_checklist"):
        assert key in d, key
    assert d["applicant_nationality"] == "Chinese"
    assert d["review_status"] == "pending_admin_review"
    assert d["adapter_status"] == "disabled_draft" and d["production_enabled"] is False


def test_search_never_trusts_first_result(fake_search):
    # First result is a fake commercial site; only the government domain is verified.
    d = discovery.discover_official_visa_portal(country="Japan")
    assert len(d["candidates"]) == 2
    assert d["candidates"][0]["is_government_domain"] is False   # first result NOT trusted
    assert len(d["verified_candidates"]) == 1
    assert d["verified_candidates"][0]["host"].endswith("go.jp")
    assert d["portal_operator"].endswith("go.jp")
    # Every candidate carries a source hash for evidence.
    assert all(c["source_hash"] for c in d["candidates"])
    # Even with a verified gov domain, the adapter is still DISABLED (no AI activation).
    assert d["adapter_status"] == "disabled_draft" and d["production_enabled"] is False


def test_change_detection_flags_material_changes():
    a = PortalSnapshot(url="https://mofa.go.jp/visa", content="v1").fingerprint()
    b = PortalSnapshot(url="https://mofa.go.jp/visa", content="v2").fingerprint()
    moved = PortalSnapshot(url="https://mofa.go.jp/visa-new", content="v1").fingerprint()
    assert discovery.detect_portal_change(None, a)["changed"] is False       # first seen
    assert discovery.detect_portal_change(a, a)["changed"] is False          # identical
    assert discovery.detect_portal_change(a, b)["changed"] is True           # content changed
    assert discovery.detect_portal_change(a, moved)["changed"] is True       # url changed


def test_scan_for_changes_unavailable_without_fetcher():
    out = discovery.scan_for_changes([{"adapter_id": "x", "url": "https://mofa.go.jp/visa",
                                       "previous_fingerprint": None}])
    assert out["status"] == "monitoring_unavailable" and out["pause"] == []


def test_scan_for_changes_pauses_changed_adapter():
    prev = PortalSnapshot(url="https://mofa.go.jp/visa", content="v1").fingerprint()

    def fetcher(url):
        return PortalSnapshot(url=url, content="v2-CHANGED", fetched_at="2026-07-22")

    out = discovery.scan_for_changes(
        [{"adapter_id": "jp-tourist", "url": "https://mofa.go.jp/visa", "previous_fingerprint": prev}],
        fetcher=fetcher)
    assert out["status"] == "ok"
    assert len(out["pause"]) == 1
    assert out["pause"][0]["adapter_id"] == "jp-tourist"
    assert "portal content changed" in out["pause"][0]["reasons"]


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
