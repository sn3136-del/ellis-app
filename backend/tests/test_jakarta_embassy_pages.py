from urllib.parse import quote

import pytest

from app.visa_snapshot.source_authority import authority_for, is_competent
from app.visa_snapshot.authority import is_government_host

BASE = "https://sites.google.com/view/koreanembassy2"
PATHS = ("", "/informasi-umum", "/visa-jangka-pendek/kunjungan-wisata-umumc-3-9",
         "/참고사항/공통서류-안내")


@pytest.mark.parametrize("path", PATHS)
def test_exact_embassy_published_pages_are_korean_authority(path):
    for url in (BASE + path, BASE + quote(path)):
        a = authority_for(url, {"destination_country": "KOR"})
        assert a.kind == "destination" and a.owner == "KOR"
        assert a.appointed_by.endswith("seq=748814")
        assert is_competent(url, "KOR", "required_documents")
        assert not is_competent(url, "IDN", "required_documents")
    assert not is_government_host("sites.google.com")


@pytest.mark.parametrize("url", [
    BASE + "/unreviewed", BASE + "/informasi-umum/child", BASE + "evil",
    BASE.replace("koreanembassy2", "koreanembassy3"),
    BASE.replace("sites.google.com", "sites.google.com.evil.test"),
    BASE.replace("sites.google.com", "evil.test@sites.google.com"),
    BASE.replace("sites.google.com", "sites.google.com:443"),
    BASE.replace("https:", "http:"), BASE + "?url=https://evil.test",
    BASE + "/../some-other-site", BASE + "/%2e%2e/some-other-site",
    BASE + "/informasi-umum%252fevil", "https://google.com/view/koreanembassy2",
])
def test_other_sites_and_spoofed_paths_are_not_accepted(url):
    assert not is_competent(url, "KOR", "disposition")


def test_exact_owned_quote_keeps_actual_url():
    from app.visa_snapshot.record_evidence import _owned
    route = {"passport_nationality": "IDN", "destination_country": "KOR",
             "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
    value = "3 months"
    proof = {"status": "reviewed", "verifier": "ai", "verified_at": "2026-09-13",
             "subject": route, "reviewed_value": value,
             "source_url": BASE + PATHS[2],
             "quote": "Visa wisata umum (C-3-9), masa berlaku 3 bulan, masa izin tinggal 30 hari"}
    quotes = _owned(proof, route, None, "validity", value)
    assert quotes and quotes[0]["source_url"] == proof["source_url"]
    assert not _owned(proof, dict(route, passport_nationality="THA"), None, "validity", value)
    assert not _owned(proof, route, None, "validity", "90 days")
