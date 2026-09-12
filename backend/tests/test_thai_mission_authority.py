"""Exact MFA-listed Thai missions own Thai entry policy, not host-country policy."""
import pytest

from app.visa_snapshot.authority import is_government_host
from app.visa_snapshot.authority_ownership import government_owner
from app.visa_snapshot.evidence_validator import jurisdiction_matches


@pytest.mark.parametrize('host', ['doha.thaiembassy.org', 'yangon.thaiembassy.org', 'www.thaiembassy.fr'])
def test_mfa_listed_missions_are_exact_thai_authorities(host):
    assert is_government_host(host)
    assert government_owner(host) == 'THA'
    assert jurisdiction_matches('https://' + host + '/visa', 'THA')
    for country in ('QAT', 'MMR', 'FRA', 'IND', 'USA'):
        assert not jurisdiction_matches('https://' + host + '/visa', country)


@pytest.mark.parametrize('host', [
    'thaiembassy.org', 'other.thaiembassy.org', 'thaiembassy.fr',
    'fake-doha.thaiembassy.org', 'doha.thaiembassy.org.example.com',
    'unreviewed.doha.thaiembassy.org', 'unreviewed.yangon.thaiembassy.org',
    'unreviewed.www.thaiembassy.fr', 'www.thaiembassy.fr.example.com',
])
def test_mission_directory_does_not_authorize_parent_neighbors_or_children(host):
    assert not is_government_host(host)
    assert government_owner(host) is None
    assert not jurisdiction_matches('https://' + host + '/visa', 'THA')
