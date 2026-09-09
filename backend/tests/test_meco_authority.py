import pytest

from app.visa_snapshot.authority import is_government_host
from app.visa_snapshot.evidence_validator import jurisdiction_matches, validate_disposition


@pytest.mark.parametrize("host", ["meco.org.tw", "www.meco.org.tw"])
def test_meco_is_the_philippine_mission_not_a_taiwan_destination_authority(host):
    url = f"https://{host}/services/visa-services/visa-free-entry-for-taiwan-passport-holders----"
    assert is_government_host(host)
    assert jurisdiction_matches(url, "PHL")
    for destination in ("TWN", "CHN", "JPN", "USA", "ZZZ"):
        assert not jurisdiction_matches(url, destination)


@pytest.mark.parametrize("host", [
    "other.org.tw", "fake-meco.org.tw", "meco.org.tw.example.com",
    "unreviewed.meco.org.tw", "unreviewed.www.meco.org.tw",
])
def test_meco_directory_review_does_not_authorize_neighbors_or_subdomains(host):
    assert not is_government_host(host)
    assert not jurisdiction_matches(f"https://{host}/visa", "PHL")


def test_official_meco_text_can_ground_only_the_applicable_route():
    url = "https://www.meco.org.tw/services/visa-services/visa-free-entry-for-taiwan-passport-holders----"
    # A literal sentence from the reviewed mission page, not synthesized scope.
    text = "Taiwan passport holders are permitted to enter the Philippines without a visa for a stay of up to fourteen (14) days"
    route = {"passport_nationality": "TWN", "destination_country": "PHL",
             "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
    pages = [{"url": url, "text": text}]
    assert validate_disposition(route, "VISA_EXEMPT", [url], pages)["ok"]
    assert not validate_disposition(dict(route, passport_nationality="IND"), "VISA_EXEMPT", [url], pages)["ok"]
    assert not validate_disposition(dict(route, destination_country="TWN"), "VISA_EXEMPT", [url], pages)["ok"]
    assert not validate_disposition(dict(route, travel_document_type="diplomatic_passport"), "VISA_EXEMPT", [url], pages)["ok"]
