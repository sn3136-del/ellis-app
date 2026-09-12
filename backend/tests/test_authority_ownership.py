import pytest

from app.visa_snapshot import authority, authority_ownership as owners, evidence_validator as ev, freshness


@pytest.mark.parametrize('host,country', [
    ('www.kdmid.ru', 'RUS'), ('evisa.kdmid.ru', 'RUS'), ('electronic-visa.kdmid.ru', 'RUS'),
    ('kremlin.ru', 'RUS'), ('www.service-public.fr', 'FRA'), ('www.boe.es', 'ESP'),
    ('consulat.ma', 'MAR'), ('ksavisa.sa', 'SAU'), ('baochinhphu.vn', 'VNM'),
    ('laoembassy.com', 'LAO'), ('bolivianembassy.co.uk', 'BOL'),
    ('myanmarconsulatehk.org', 'MMR'), ('syrembassy.cn', 'SYR'), ('maliembassy.us', 'MLI'),
    ('www.meco.org.tw', 'PHL'), ('evisa.gouv.tg', 'TGO'), ('bahrain.bh', 'BHR'),
    ('beninembassy.us', 'BEN'), ('www.visitsaudi.com', 'SAU'), ('visa.visitsaudi.com', 'SAU'),
    ('vietnamembassydelhi.in', 'VNM'),
])
def test_reviewed_mission_and_government_ownership_matches_only_its_country(host, country):
    assert authority.is_government_host(host)
    assert ev.jurisdiction_matches('https://' + host + '/visa', country)
    for wrong in {'USA', 'GBR', 'FRA', 'CHN', 'HKG', 'RUS', 'ZZZ'} - {country}:
        assert not ev.jurisdiction_matches('https://' + host + '/visa', wrong)
    assert not ev.jurisdiction_matches('https://' + host + '.example.com/visa', country)
    owned_suffix = max((s for s in (*authority.GOV_SUFFIXES, *authority.EXACT_OFFICIAL_HOSTS)
                        if host == s or host.endswith('.' + s)), key=len)
    assert not ev.jurisdiction_matches('https://fake-' + owned_suffix + '/visa', country)


def test_all_trusted_suffixes_have_reviewed_ownership_or_explicit_unsupported_scope():
    unassigned = {s for s in set(authority.GOV_SUFFIXES) | authority.EXACT_OFFICIAL_HOSTS
                  if not owners.government_owner(s)}
    assert unassigned == set(owners.UNASSIGNED)
    for host in unassigned:
        assert not ev.jurisdiction_matches('https://' + host + '/visa', 'RUS')
        assert not ev.jurisdiction_matches('https://' + host + '/visa', 'ZZZ')
    assert ev.jurisdiction_matches('https://eur-lex.europa.eu/eli/reg/2018/1806/oj', 'FRA')
    assert not ev.jurisdiction_matches('https://other.europa.eu/visa', 'FRA')


@pytest.mark.parametrize('host', ['beninembassy.us', 'www.visitsaudi.com', 'visa.visitsaudi.com', 'vietnamembassydelhi.in'])
def test_new_delegated_hosts_do_not_authorize_unreviewed_subdomains(host):
    assert not authority.is_government_host('unreviewed.' + host)
    assert not authority.is_government_host(host + '.example.com')
    assert not authority.is_government_host('fake-' + host)


def test_kdmid_ownership_does_not_turn_program_eligibility_into_a_visa_obligation():
    route = {'passport_nationality': 'FRA', 'destination_country': 'RUS',
             'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}
    raw = {'page_relevant': True, 'page_is_nationality_specific': True}
    assert freshness._source_authority_matches('https://evisa.kdmid.ru/', route)
    assert not freshness._supports_route('French citizens are eligible for the unified e-visa.', raw,
        route, {'disposition': 'VISA_REQUIRED'}, {}, 'https://evisa.kdmid.ru/', '2026-09-09')
    assert freshness._supports_route('French citizens must obtain a visa for tourism in Russia.', raw,
        route, {'disposition': 'VISA_REQUIRED'}, {}, 'https://evisa.kdmid.ru/', '2026-09-09')
