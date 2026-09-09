from copy import deepcopy
import json

import pytest

from app.visa_snapshot import scheduled_policies as sp, kimi_primary as kp

ROUTE = {"passport_nationality": "HKG", "destination_country": "THA",
         "travel_document_type": "ordinary_passport", "travel_purpose": "tourism",
         "arrival_date": "2026-09-15"}
BASE = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
        "permitted_stay_days": 60, "permitted_stay": "60 days",
        "government_fee": {"amount": 0, "currency": None}, "visa_products": [],
        "arrival_card": {"required": True, "name": "Independent arrival filing"}}


@pytest.mark.parametrize("bad", [None, "not json", "{}", '[{"route": {"nationality": "UNKNOWN"}}]'])
def test_missing_or_rejected_schedule_cannot_release_cached_rules(tmp_path, monkeypatch, bad):
    path = tmp_path / 'scheduled.json'
    if bad is not None:
        path.write_text(bad)
    monkeypatch.setattr(sp, 'POLICIES', path)
    monkeypatch.setattr(sp, '_CACHE', {'version': None, 'rows': []})
    original = deepcopy(BASE)
    proof = {'verifier': 'human', 'fields': ['arrival_card']}
    g, actual = sp.apply(BASE, proof, ROUTE)
    assert g['scheduled_policy_conflict']['reason'] == 'scheduled_policy_store_unavailable'
    assert {k:v for k,v in g.items() if k != 'scheduled_policy_conflict'} == original
    assert actual == proof and BASE == original
    assert any('scheduled_policy' in x for x in kp.serve_time_invariants(g))


def test_corrupted_warm_store_is_not_silently_replaced_by_old_rule(tmp_path, monkeypatch):
    data = sp.POLICIES.read_text()
    path = tmp_path / 'scheduled.json'; path.write_text(data)
    monkeypatch.setattr(sp, 'POLICIES', path)
    monkeypatch.setattr(sp, '_CACHE', {'version': None, 'rows': []})
    assert sp.apply(BASE, None, ROUTE)[0]['permitted_stay_days'] == 30
    path.write_text('{broken')
    failed, _ = sp.apply(BASE, None, ROUTE)
    assert failed['permitted_stay_days'] == 60
    assert failed['scheduled_policy_conflict']
    path.write_text(data)
    recovered, _ = sp.apply(failed, None, ROUTE)
    assert recovered['permitted_stay_days'] == 30
    assert not recovered.get('scheduled_policy_conflict')


def test_valid_empty_store_is_distinct_and_recovers_only_its_own_error(tmp_path, monkeypatch):
    path = tmp_path / 'scheduled.json'
    monkeypatch.setattr(sp, 'POLICIES', path)
    monkeypatch.setattr(sp, '_CACHE', {'version': None, 'rows': []})
    failed, _ = sp.apply(BASE, None, ROUTE)
    path.write_text('[]')
    assert sp.apply(failed, None, ROUTE) == (BASE, None)
    other = dict(BASE, scheduled_policy_conflict={'reason': 'independent disputed condition'})
    assert sp.apply(other, None, ROUTE) == (other, None)


def test_cold_start_unknown_schedule_scope_does_not_guess_unaffected_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, 'POLICIES', tmp_path / 'missing.json')
    monkeypatch.setattr(sp, '_CACHE', {'version': None, 'rows': []})
    g, _ = sp.apply(BASE, None, dict(ROUTE, destination_country='CAN'))
    assert g['scheduled_policy_conflict']['reason'] == 'scheduled_policy_store_unavailable'


@pytest.mark.parametrize('marker', [
    {'reason': 'independent disputed condition', 'fields': ['entry_requirements']},
    'invalid pre-existing marker', ['entry condition under review'],
])
def test_store_recovery_never_clears_an_independent_conflict(tmp_path, monkeypatch, marker):
    path = tmp_path / 'scheduled.json'
    monkeypatch.setattr(sp, 'POLICIES', path)
    monkeypatch.setattr(sp, '_CACHE', {'version': None, 'rows': []})
    original = dict(BASE, scheduled_policy_conflict=marker)
    failed, _ = sp.apply(original, None, ROUTE)
    repeated, _ = sp.apply(failed, None, ROUTE)
    assert failed == repeated
    path.write_text('[]')
    assert sp.apply(repeated, None, ROUTE) == (original, None)
    assert sp.apply(original, None, ROUTE) == (original, None)
