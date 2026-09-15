from app.visa_snapshot.authority import is_government_host
from app.visa_snapshot.authority_ownership import government_owner
from app.visa_snapshot.source_authority import is_competent


def test_ottawa_mission_is_vietnamese_not_canadian_authority():
    url = 'https://vietnamembassy.ca/consular-services/visa-application/'
    assert is_government_host('vietnamembassy.ca')
    assert government_owner('vietnamembassy.ca') == 'VNM'
    assert is_competent(url, {'passport_nationality': 'CAN', 'destination_country': 'VNM'}, 'passport_validity')
    assert not is_competent(url, {'passport_nationality': 'VNM', 'destination_country': 'CAN'}, 'passport_validity')


def test_ottawa_mission_does_not_authorize_lookalikes_or_other_hosts():
    for host in ('visa.vietnamembassy.ca', 'vietnamembassy.ca.example.com', 'fakevietnamembassy.ca'):
        assert not is_government_host(host)
        assert government_owner(host) is None
