"""guard-20260912 T5: the scheme registry, captured lists, load gates."""
import json
from pathlib import Path

import pytest

from app.visa_snapshot import permission_eligibility as pe, scheme_registry as sr

SEED = Path(__file__).resolve().parents[2] / "data" / "database_seed" / "scheme_lists.json"


def shipped():
    return json.loads(SEED.read_text(encoding="utf-8"))


def entry(sid):
    return next(e for e in shipped() if e["id"] == sid)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "scheme_lists.json"
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(path))
    sr.reload()

    def write(entries, raw=None):
        path.write_text(raw if raw is not None else json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        sr.reload()
    yield write
    sr.reload()


def test_shipped_file_loads_with_no_store_errors():
    sr.reload()
    status = sr.store_status()
    assert status["errors"] == [] and not status["store_unavailable"]
    assert status["established"] == ["aus_eta_601", "aus_evisitor_651", "kor_keta"]
    assert set(status["not_established"]) >= {"usa_esta_vwp", "can_eta", "gbr_eta", "nzl_nzeta", "lka_eta",
                                              "isr_eta_il", "som_etas", "rus_unified_evisa", "ind_evisa",
                                              "etias_annex_ii"}
    for e in shipped():
        if e["list_state"] != "established":
            assert e["eligible_nationalities"] == [] and e["list_quote"] == ""


def test_australia_entry_is_identical_to_the_old_program():
    sr.reload()
    loaded = next(e for e in sr.entries() if e["id"] == "aus_eta_601")
    assert loaded["list_state"] == "established"
    assert set(loaded["eligible_nationalities"]) == set(pe.PROGRAMS[0].eligible_nationalities)
    assert len(loaded["eligible_nationalities"]) == 33
    assert loaded["source_url"] == pe.PROGRAMS[0].source_url
    assert set(loaded["excluded_documents"]) >= {"identity_certificate", "refugee_travel_document",
                                                 "non_citizen_passport", "emergency_travel_document"}


def test_korea_list_was_read_from_the_page_and_excludes_indonesia():
    sr.reload()
    keta = next(e for e in sr.entries() if e["id"] == "kor_keta")
    assert keta["source_url"] == "https://www.k-eta.go.kr/portal/guide/viewetaalification.do"
    assert "INDONESIA" not in keta["list_quote"].upper()
    assert not sr.covers(keta, "IDN")
    for nat in ("JPN", "GBR", "USA", "AUS", "HKG", "TWN", "SGP", "CAN", "FRA", "ESP", "RUS", "THA", "MYS"):
        assert sr.covers(keta, nat), nat
    assert not sr.covers(keta, "PHL") and not sr.covers(keta, "VNM") and not sr.covers(keta, "CHN")
    assert sr.quote_hash(keta["list_quote"]) == keta["quote_sha256"]


def test_list_quote_must_contain_every_listed_nationality(registry):
    e = entry("kor_keta")
    registry([dict(e, eligible_nationalities=e["eligible_nationalities"] + ["IDN"])])
    assert sr.entries() == []
    status = sr.store_status()
    assert status["errors"][0]["id"] == "kor_keta"
    assert "IDN is not its own list item in list_quote" in status["errors"][0]["errors"]


def test_quote_hash_mismatch_drops_the_entry(registry):
    e = entry("aus_eta_601")
    registry([dict(e, list_quote=e["list_quote"] + "\nIndonesia")])
    assert sr.for_destination("AUS") == []
    assert any("quote_sha256 does not match" in x for x in sr.store_status()["errors"][0]["errors"])


def test_non_destination_source_drops_the_entry(registry):
    e = entry("kor_keta")
    registry([dict(e, source_url="https://travel.state.gov/content/travel/en/us-visas.html")])
    assert sr.for_destination("KOR") == []
    status = sr.store_status()
    assert status["errors"][0]["id"] == "kor_keta"
    assert any("destination government owns" in x for x in status["errors"][0]["errors"])
    # Union law is not admissible for a nationality list either.
    registry([dict(entry("aus_eta_601"), destination="ITA",
                   source_url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230")])
    assert sr.for_destination("ITA") == []


def test_future_checked_at_is_dropped(registry):
    registry([dict(entry("kor_keta"), checked_at="2099-01-01")])
    assert sr.for_destination("KOR") == []
    assert any("in the future" in x for x in sr.store_status()["errors"][0]["errors"])


def test_malformed_file_keeps_previous_entries_and_sets_store_unavailable(registry):
    registry([entry("kor_keta")])
    assert [e["id"] for e in sr.entries()] == ["kor_keta"]
    registry(None, raw="{not json")
    assert [e["id"] for e in sr.entries()] == ["kor_keta"]
    assert sr.store_status()["store_unavailable"] is True
    registry([entry("kor_keta"), entry("aus_eta_601")])
    assert sr.store_status()["store_unavailable"] is False
    assert len(sr.entries()) == 2


def test_bad_pattern_and_bad_iso3_are_dropped(registry):
    registry([dict(entry("kor_keta"), patterns=["(unclosed"])])
    assert sr.entries() == []
    registry([dict(entry("kor_keta"), eligible_nationalities=["Japan"])])
    assert sr.entries() == []
    assert any("not an ISO3 code" in x for x in sr.store_status()["errors"][0]["errors"])


def test_select_resolves_by_program_id_then_portal_then_pattern_then_detail():
    sr.reload()
    assert sr.select({"program_id": "aus_evisitor_651", "type": "Electronic Travel Authority"}, "AUS")["id"] == "aus_evisitor_651"
    assert sr.select({"type": "Online visa", "official_portal_url":
                      "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601"},
                     "AUS")["id"] == "aus_eta_601"
    assert sr.select({"type": "ETA (subclass 601)"}, "AUS")["id"] == "aus_eta_601"
    assert sr.select({"type": "eVisitor (subclass 651)"}, "AUS")["id"] == "aus_evisitor_651"
    assert sr.select({"type": "Electronic authorisation", "requirement_detail": "eta_electronic_authorization"},
                     "AUS")["id"] == "aus_eta_601"
    assert sr.select({"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED"}, "AUS", headline=True)["id"] == "aus_eta_601"
    # An e-visa detail alone never picks the eVisitor: Australia has other e-visas.
    assert sr.select({"type": "Visitor visa (subclass 600)", "requirement_detail": "evisa"}, "AUS") is None
    assert sr.select({"type": "K-ETA"}, "KOR")["id"] == "kor_keta"
    assert sr.select({"type": "Tourist visa"}, "KOR") is None
    assert sr.select({"type": "ETA"}, "JPN") is None


def test_product_named_in_another_language_still_resolves_by_program_id():
    sr.reload()
    assert sr.select({"program_id": "kor_keta", "type": "전자여행허가"}, "KOR")["id"] == "kor_keta"
    assert sr.select({"program_id": "aus_eta_601", "type": "澳大利亚电子旅行授权"}, "AUS")["id"] == "aus_eta_601"
    assert sr.select({"program_id": "no_such", "type": "K-ETA"}, "KOR") is None


def test_country_code_reads_page_spellings():
    for item, code in (("CHINA P. R.(HONG KONG)", "HKG"), ("UK-BRITISH CITIZEN(GBR)", "GBR"),
                       ("Taiwan (excluding official or diplomatic passports)", "TWN"),
                       ("Vatican City - passport must indicate that you are a national of the Vatican City (Holy See)", "VAT"),
                       ("United Kingdom – British Citizen", "GBR"), ("SLOVAK", "SVK"), ("| ANDORRA", "AND")):
        assert sr.country_code(item) == code, item
    assert sr.country_code("Allowed Period of Stay : 90 Days") is None
    assert sr.country_code("UK-BRITISH NATIONAL OVERSEAS(GBN)") is None


def test_program_id_is_carried_through_the_product_row_and_linted():
    from app.visa_snapshot import tstation, verified_overrides as vo
    g = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "requirement_detail": "eta_electronic_authorization",
         "visa_products": [{"type": "ETA", "program_id": "aus_eta_601", "fee": {"amount": 20, "currency": "AUD"}}]}
    row = tstation.records_for_route({"passport_nationality": "JPN", "destination_country": "AUS",
                                      "travel_purpose": "tourism"}, g)[0]
    assert row["_program_id"] == "aus_eta_601"
    assert vo._field_errors({"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
                             "visa_products": [{"type": "ETA", "program_id": "aus_eta_601"}]}) == []
    assert vo._field_errors({"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
                             "visa_products": [{"type": "ETA", "program_id": "Not An Id"}]})
