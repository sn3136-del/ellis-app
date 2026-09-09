import pytest

@pytest.mark.parametrize('path', ['/database/records', '/database/freshness', '/database/issues', '/database/changes'])
def test_policy_and_quality_reads_must_not_be_reused_by_browser_or_proxy(client, path):
    response = client.get(path, headers={'Authorization': 'Bearer admin-token', 'X-Org-Id': 'platform', 'X-User-Id': 'test-owner'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'

def test_policy_errors_also_cannot_be_cached(client):
    response = client.get('/database/records')
    assert response.status_code in (401, 403)
    assert response.headers['cache-control'] == 'private, no-store'
