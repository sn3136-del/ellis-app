"""guard-20260912 T3: one jurisdiction rule, two documented exemptions, and
the traveller's own government as a third state."""
import hashlib
import json

import pytest

from app.visa_snapshot import source_authority as sa

LAW = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230"
ACT = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32018R1806"


def route(nat, dest):
    return {"passport_nationality": nat, "destination_country": dest, "travel_purpose": "tourism"}


def test_destination_government_is_competent():
    assert sa.is_competent("https://www.mofa.go.jp/j_info/visit/visa/index.html", route("CHN", "JPN"))
    assert sa.is_competent("https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600",
                           route("THA", "AUS"), "required_documents")
    assert sa.classify("https://evisa.kdmid.ru/", route("MYS", "RUS")) == sa.KIND_DESTINATION
    assert sa.classify("https://www.k-eta.go.kr/portal/apply/index.do", route("IDN", "KOR")) == sa.KIND_DESTINATION


@pytest.mark.parametrize("dest", ["KHM", "DZA"])
def test_traveller_government_is_never_competent(dest):
    url = "https://www.immd.gov.hk/eng/services/visas/visit-transit/visit-visa-entry-permit.html"
    assert not sa.is_competent(url, route("HKG", dest))
    assert not sa.is_competent(url, route("HKG", dest), "permitted_stay")
    assert sa.classify(url, route("HKG", dest)) == sa.KIND_TRAVELLER
    # A third country's page is a third party, not the traveller's.
    assert sa.classify(url, route("CHN", dest)) == sa.KIND_THIRD_PARTY


def test_traveller_government_can_corroborate_when_it_names_group_and_destination():
    url = "https://www.immd.gov.hk/eng/services/visas/visit-transit/visit-visa-entry-permit.html"
    yes = "HKSAR passport holders do not require a visa to visit Cambodia for up to 30 days."
    no_dest = "HKSAR passport holders do not require a visa for visits of up to 30 days."
    no_group = "No visa is required to visit Cambodia for up to 30 days."
    assert sa.is_corroborating(url, route("HKG", "KHM"), statement=yes)
    assert not sa.is_corroborating(url, route("HKG", "KHM"), statement=no_dest)
    assert not sa.is_corroborating(url, route("HKG", "KHM"), statement=no_group)
    assert not sa.is_corroborating(url, route("HKG", "KHM"))
    # The destination itself corroborates with any statement.
    assert sa.is_corroborating("https://www.evisa.gov.kh/", route("HKG", "KHM"))
    # Lifted verbatim from freshness._supports_route: freshness uses the same function.
    from app.visa_snapshot import freshness
    import inspect
    src = inspect.getsource(freshness._supports_route)
    assert "is_corroborating(source_url, route, statement=statement)" in src


@pytest.mark.parametrize("dest", ["ITA", "DEU", "ESP", "FRA"])
def test_eu_law_covers_every_schengen_destination(dest):
    assert sa.is_competent(LAW, route("JPN", dest), "disposition")
    assert sa.is_competent(ACT, route("JPN", dest), "permitted_stay_days")
    authority = sa.authority_for(LAW, route("JPN", dest))
    assert authority.kind == sa.KIND_EU_VISA_LAW and authority.instrument == "2018/1806"
    assert authority.exemption == "eu_visa_law"


def test_eu_law_without_a_named_instrument_is_not_competent():
    bare = "https://eur-lex.europa.eu/legal-content/EN/TXT/"
    assert not sa.is_competent(bare, route("JPN", "ITA"))
    assert sa.classify(bare, route("JPN", "ITA")) == sa.KIND_THIRD_PARTY
    # A citation beside the URL can name it.
    assert sa.is_competent(bare, route("JPN", "ITA"), citation="Regulation (EU) 2018/1806, Annex II")
    # The ETIAS page names no instrument in its URL and is not the visa list.
    assert not sa.is_competent("https://travel-europe.europa.eu/etias_en", route("JPN", "ESP"))
    assert sa.is_competent("https://travel-europe.europa.eu/etias_en", route("JPN", "ESP"),
                           citation="Regulation (EU) 2018/1240")


def test_eu_law_does_not_cover_a_national_field():
    assert not sa.is_competent(LAW, route("JPN", "ITA"), "required_documents")
    assert not sa.is_competent(LAW, route("JPN", "ITA"), "processing_time")
    assert not sa.is_competent(LAW, route("JPN", "ITA"), "official_portal_url")
    assert sa.is_competent(LAW, route("JPN", "ITA"), "government_fee")
    assert set(sa.EU_LAW_FIELDS) == {"disposition", "requirement_detail", "permitted_stay",
                                     "permitted_stay_days", "government_fee"}


def test_eu_law_does_not_cover_a_non_schengen_destination():
    assert not sa.is_competent(LAW, route("JPN", "GBR"))
    assert not sa.is_competent(LAW, route("JPN", "IRL"))
    assert sa.classify(LAW, route("JPN", "GBR")) == sa.KIND_THIRD_PARTY


def _provider(**over):
    quote = "Applications for a visa to Italy are lodged at the VFS Global visa application centre."
    entry = {"id": "ita_vfs_hk", "destination": "ITA", "host": "visa.vfsglobal.com",
             "authorised_fields": ["application_channel", "appointment_required"],
             "appointed_by_url": "https://conshongkong.esteri.it/en/servizi-consolari-e-visti/servizi-per-il-cittadino-straniero/visti/",
             "appointment_quote": quote, "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
             "checked_at": "2026-09-01", "valid_until": "2099-01-01"}
    entry.update(over)
    return entry


@pytest.fixture
def providers(tmp_path, monkeypatch):
    path = tmp_path / "providers.json"
    monkeypatch.setenv("ELLIS_AUTHORISED_PROVIDERS", str(path))
    sa.reload_providers()

    def write(entries):
        path.write_text(json.dumps(entries), encoding="utf-8")
        sa.reload_providers()
    yield write
    sa.reload_providers()


def test_shipped_register_is_empty():
    import pathlib
    seed = pathlib.Path(sa._PROVIDERS_PATH).resolve()
    assert json.loads(seed.read_text(encoding="utf-8")) == []


def test_provider_entry_needs_a_destination_owned_appointment_page(providers):
    url = "https://visa.vfsglobal.com/hkg/en/ita/apply-visa"
    providers([_provider()])
    assert sa.is_competent(url, route("HKG", "ITA"), "application_channel")
    assert sa.classify(url, route("HKG", "ITA")) == sa.KIND_PROVIDER
    # Appointed by a page the destination does not own: dropped, with a store error.
    providers([_provider(appointed_by_url="https://www.gov.uk/apply-uk-visa")])
    assert not sa.is_competent(url, route("HKG", "ITA"), "application_channel")
    status = sa.provider_store_status()
    assert status["entries"] == 0 and status["errors"][0]["id"] == "ita_vfs_hk"
    assert any("destination government owns" in e for e in status["errors"][0]["errors"])


def test_provider_scope_never_covers_the_verdict(providers):
    url = "https://visa.vfsglobal.com/hkg/en/ita/apply-visa"
    providers([_provider()])
    assert not sa.is_competent(url, route("HKG", "ITA"), "disposition")
    assert not sa.is_competent(url, route("HKG", "ITA"), "requirement_detail")
    assert not sa.is_competent(url, route("HKG", "ITA"), "government_fee")
    providers([_provider(authorised_fields=["application_channel", "disposition"])])
    assert not sa.is_competent(url, route("HKG", "ITA"), "application_channel")
    assert any("never competent for the verdict" in e for e in sa.provider_store_status()["errors"][0]["errors"])


def test_stale_provider_entry_supports_nothing(providers):
    url = "https://visa.vfsglobal.com/hkg/en/ita/apply-visa"
    providers([_provider(valid_until="2026-01-01")])
    assert not sa.is_competent(url, route("HKG", "ITA"), "application_channel")
    assert sa.classify(url, route("HKG", "ITA")) == sa.KIND_NON_GOVERNMENT
    providers([_provider(quote_sha256="0" * 64)])
    assert not sa.is_competent(url, route("HKG", "ITA"), "application_channel")
    providers([_provider(checked_at="2099-01-01")])
    assert not sa.is_competent(url, route("HKG", "ITA"), "application_channel")


def test_non_government_page_is_never_anything():
    assert sa.classify("https://www.ivisa.com/vietnam", route("HKG", "VNM")) == sa.KIND_NON_GOVERNMENT
    assert not sa.is_competent("https://www.ivisa.com/vietnam", route("HKG", "VNM"))
    assert not sa.is_corroborating("https://www.ivisa.com/vietnam", route("HKG", "VNM"), statement="x")
