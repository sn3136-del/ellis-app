"""The API shares the worker guard, including legacy serialized metadata."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import text

from app.visa_snapshot.models import KimiRouteGuidanceCache
from .test_operator_release_cas import release_session, HEADERS, BODY


@pytest.mark.parametrize('encoding', ['compact', 'pretty', 'unicode_reordered'])
def test_release_preserves_formatted_metadata_without_false_conflict(client, release_session, encoding):
    session, row_id = release_session
    row = session.get(KimiRouteGuidanceCache, row_id)
    original = deepcopy(row.verification)
    original['source_note'] = 'Visa · 日本'
    if encoding == 'compact':
        encoded = json.dumps(original, separators=(',', ':'))
    elif encoding == 'pretty':
        encoded = json.dumps(original, indent=2)
    else:
        encoded = json.dumps(dict(reversed(list(original.items()))), ensure_ascii=False)
    session.execute(text('UPDATE kimi_route_guidance_cache SET verification=:value WHERE id=:id'),
                    {'value': encoded, 'id': row_id})
    session.commit()
    response = client.post('/database/approve', headers=HEADERS, json=BODY)
    assert response.status_code == 200, response.text
    session.refresh(row)
    actual = deepcopy(row.verification)
    assert actual.pop('operator_released')['by'] == 'operator'
    assert actual == original
