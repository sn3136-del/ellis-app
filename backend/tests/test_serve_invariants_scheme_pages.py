"""Authorization-page matching must not confuse applicability with a URL.

The negative product guards remain; a cited scheme page can also explain
who is exempt. Proof gaps are reported separately from contradictions.
"""
from pathlib import Path

import pytest

from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo

SEED = Path(__file__).resolve().parents[2] / "data" / "database_seed" / "verified_overrides.json"
KETA_PORTAL = "https://www.k-eta.go.kr/portal/apply/index.do"
EXEMPT_ON_SCHEME = ("disposition VISA_EXEMPT but the cited page is a travel authorisation "
                    "scheme, which is not an unconditional exemption")
REQUIRED_ON_SCHEME = ("disposition VISA_REQUIRED but the cited page is a travel "
                      "authorisation or border-formality page, which is not a visa")


@pytest.fixture
def shipped(monkeypatch, tmp_path):
    monkeypatch.setattr(vo, "OVERRIDES", SEED)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    vo.reload()
    yield
    vo.reload()


def route(nat, dest="KOR", purpose="tourism"):
    return {"passport_nationality": nat, "destination_country": dest, "travel_purpose": purpose,
            "travel_document_type": "ordinary_passport"}


def test_scheme_url_alone_does_not_establish_an_authorization_obligation():
    g = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
         "source_url": KETA_PORTAL}
    assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(g)
    # The same shape with no detail at all, and with the page only as the portal.
    assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(dict(g, requirement_detail=None))
    assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(
        {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
         "official_portal_url": "https://www.k-eta.go.kr/portal/guide/viewetaalification.do"})
    # A category that states the scheme's condition is not a citation.
    assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(
        {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
         "visa_category": "Visa-free entry (K-ETA exempt until 31 December 2026)",
         "source_url": "https://www.mofa.go.kr/www/brd/m_4080/view.do?seq=353132"})


def test_conditional_and_transit_exemptions_on_a_scheme_page_are_allowed():
    for detail in ("conditional_visa_free", "transit_visa_free"):
        g = {"disposition": "VISA_EXEMPT", "requirement_detail": detail, "source_url": KETA_PORTAL}
        assert not [p for p in kp.serve_time_invariants(g) if "travel authorisation" in p], detail
    eta = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
           "requirement_detail": "eta_electronic_authorization", "source_url": KETA_PORTAL,
           "application_channel": "online_portal"}
    assert not [p for p in kp.serve_time_invariants(eta) if "travel authorisation" in p]


def test_visa_required_on_a_scheme_page_still_contradicts():
    g = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
         "source_url": "https://travel-europe.europa.eu/etias_en"}
    assert REQUIRED_ON_SCHEME in kp.serve_time_invariants(g)
    # The category alone still counts for this verdict, verbatim as before.
    by_category = {"disposition": "VISA_REQUIRED", "requirement_detail": "evisa",
                   "visa_category": "ESTA", "source_url": "https://www.cbp.gov/"}
    assert REQUIRED_ON_SCHEME in kp.serve_time_invariants(by_category)
    # validate_answer keeps its own wording of the same rule.
    _, _, contradictions = kp.validate_answer(dict(g, visa_category="Tourist visa", confidence="high"))
    assert any("ETIAS/ESTA/eTA/EES" in c for c in contradictions)


@pytest.mark.parametrize("nat", ["HKG", "TWN", "JPN", "SGP", "CAN", "USA", "GBR", "FRA", "ESP", "AUS"])
def test_korea_exemptions_are_not_overruled_by_list_membership_or_source_url(shipped, nat):
    """The served answer for each route: the model's stored K-ETA answer under
    the shipped override, which writes an unconditional exemption citing the
    K-ETA portal. The URL alone cannot overrule the recorded exemption; source-proof
    and policy-interval checks still run separately."""
    raw = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
           "requirement_detail": "eta_electronic_authorization", "visa_category": "K-ETA",
           "application_channel": "online_portal", "source_url": "https://www.k-eta.go.kr",
           "official_portal_url": "https://www.k-eta.go.kr",
           "government_fee": {"amount": 10000, "currency": "KRW"}}
    merged, provenance = vo.apply(raw, route(nat))
    assert merged["disposition"] == "VISA_EXEMPT"
    assert merged["requirement_detail"] == "unconditional_visa_free"
    assert kp.is_authorization_page(merged.get("source_url"), merged.get("official_portal_url"))
    problems = kp.serve_time_invariants(merged)
    assert EXEMPT_ON_SCHEME not in problems
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, raw, cached=True, stale=False,
                                                 released=True), route(nat))
    assert EXEMPT_ON_SCHEME not in out["contradictions"]
    assert not any("is on its list, so entry is not unconditional" in c for c in out["contradictions"])


def test_a_non_korea_exemption_on_its_own_government_page_is_untouched(shipped):
    # Malaysia to Japan style: a visa-free verdict on the destination's
    # ordinary foreign-ministry page is not a scheme page.
    g = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
         "source_url": "https://www.mofa.go.jp/j_info/visit/visa/short/novisa.html"}
    assert not [p for p in kp.serve_time_invariants(g) if "travel authorisation" in p]


def test_detail_only_override_corrects_a_detail_less_row():
    """_verdict_implied_by_detail reads the detail case-insensitively, so an
    override that supplies only a detail corrects a verdict with none."""
    assert vo._verdict_implied_by_detail({"requirement_detail": "EVISA"}, {}) == "VISA_REQUIRED"
    assert vo._verdict_implied_by_detail({}, {"requirement_detail": None}) is None
    detail_less = {"disposition": "VISA_EXEMPT", "requirement_detail": None,
                   "application_channel": "not_required"}
    merged, fields = vo.merge_verified_fields(detail_less, {"requirement_detail": "evisa"},
                                              source_url="https://evisa.gov.vn/")
    assert fields["disposition"] == "VISA_REQUIRED"
    assert merged["disposition"] == "VISA_REQUIRED" and merged["requirement_detail"] == "evisa"


def test_a_scheme_whose_list_is_not_established_withholds_nothing(tmp_path, monkeypatch):
    """The registry's fail-safe rule applies to the page rule: ETIAS, ESTA and
    the UK ETA are placeholders, so a visa-free verdict citing the ETIAS
    information page (AUS to ESP, SGP to ESP, USA to EST and USA to VAT on
    the local copy) is reported by the sweep and never withheld. Establishing the membership list still does not establish an obligation
    or invalidate a separate exemption."""
    import json
    from app.visa_snapshot import scheme_registry as sr
    etias = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
             "source_url": "https://travel-europe.europa.eu/etias_en"}
    esta = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
            "official_portal_url": "https://esta.cbp.dhs.gov/"}
    assert kp.is_authorization_page(etias["source_url"]) and kp.is_authorization_page(esta["official_portal_url"])
    assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(etias)
    assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(esta)
    seed = Path(__file__).resolve().parents[2] / "data" / "database_seed" / "scheme_lists.json"
    entries = json.loads(seed.read_text(encoding="utf-8"))
    quote = "Japan\nFrance"
    for e in entries:
        if e["id"] == "usa_esta_vwp":
            e.update(list_state="established", eligible_nationalities=["JPN", "FRA"], list_quote=quote,
                     quote_sha256=sr.quote_hash(quote), checked_at="2026-09-12",
                     source_url="https://esta.cbp.dhs.gov/")
    path = tmp_path / "scheme_lists.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(path))
    sr.reload()
    try:
        assert "usa_esta_vwp" in sr.store_status()["established"]
        assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(esta)
        assert EXEMPT_ON_SCHEME not in kp.serve_time_invariants(etias)
    finally:
        monkeypatch.delenv("ELLIS_SCHEME_LISTS")
        sr.reload()
