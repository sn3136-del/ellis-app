from app.visa_snapshot.kimi_primary import serve_time_invariants


def test_source_store_failure_is_an_independent_serve_time_hold():
    guidance = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                'government_fee': {'amount': 0, 'currency': None}, 'visa_products': []}
    assert not serve_time_invariants(guidance)
    guidance['source_verification_store_unavailable'] = {'store': 'reviewed corrections'}
    assert any('source verification store' in issue for issue in serve_time_invariants(guidance))
    guidance['source_verification_store_unavailable'] = False
    assert not serve_time_invariants(guidance)
