"""QC refresh reports exactly the engine outcome, including partial checks."""
import pytest
from app import main
from app.visa_snapshot import kimi_primary as kp, freshness

ADMIN = {'authorization': 'Bearer admin-token', 'x-org-id': 'integrity', 'x-user-id': 'operator'}


@pytest.mark.parametrize('report,suspended', [
    ({'outcome': 'checked', 'consistent': True, 'renewed': True,
      'verified_fields': ['disposition', 'government_fee'], 'unverified_fields': [], 'source_reads': 2}, False),
    ({'outcome': 'checked', 'consistent': True, 'renewed': False,
      'verified_fields': ['government_fee'], 'unverified_fields': ['disposition'], 'source_reads': 1}, False),
    ({'outcome': 'provider_error', 'source_reads': 1}, True),
    ({'outcome': 'fetch_failed', 'source_reads': 0}, False),
])
def test_refresh_api_preserves_actual_evidence_and_rereads_the_same_canonical_route(client, monkeypatch, report, suspended):
    stages = []
    def guidance(db, route, **kwargs):
        stages.append((kwargs['stage'], kp.cache_key(route)))
        return {'guidance': {'disposition': 'VISA_REQUIRED'}, 'held': True}
    monkeypatch.setattr(kp, 'get_route_guidance', guidance)
    monkeypatch.setattr(freshness, 'recheck_route', lambda *_a, **_k: report)
    monkeypatch.setattr(kp, 'provider_suspension', lambda: {'reason': 'private provider message'} if suspended else None)
    response = client.post('/database/routes/research', headers=ADMIN,
        json={'nationality': 'HKG', 'destination': 'VNM', 'travel_purpose': 'tourism'})
    assert response.status_code == 200
    result = response.json()['research']
    assert result['outcome'] == report['outcome']
    assert result['renewed'] == (report.get('renewed') is True)
    assert result['verified_fields'] == report.get('verified_fields', [])
    assert result['unverified_fields'] == report.get('unverified_fields', [])
    assert result['source_reads'] == report['source_reads']
    assert result['provider_unavailable'] is suspended
    assert [s[0] for s in stages] == ['full', 'core']
    assert stages[0][1] == stages[1][1] == 'HKG|HKG|VNM|tourism|default|unknown|v6'
    assert 'private provider message' not in response.text
    assert response.headers['cache-control'] == 'private, no-store'
