from copy import deepcopy
from datetime import date
import json
import pytest
from app.visa_snapshot import policy_intervals as m


def provenance():
    return {'fields': ['disposition'], 'field_provenance': {'disposition': {
        'verifier': 'ai', 'verified_at': '2026-09-09', 'source_url': 'https://www.meco.org.tw/news/detail/1178',
        'effective_from': '2026-07-01', 'effective_to': '2027-06-30',
        'quote': 'These regulations will remain in effect from 01 July 2026 to 30 June 2027.'}}}


@pytest.mark.parametrize('arrival,held', [('2026-06-30', True), ('2026-07-01', False), ('2027-06-30', False), ('2027-07-01', True)])
def test_inclusive_bounds_and_no_replacement_claim(arrival, held):
    g = {'disposition': 'VISA_EXEMPT', 'visa_products': [{'type': 'unresolved raw claim', 'fee': {'amount': 999}}], 'arrival_card': {'required': True}}
    original = deepcopy(g)
    out = m.annotate(g, provenance(), {'arrival_date': arrival})
    assert bool(out.get(m.MARKER)) is held
    assert {k:v for k,v in out.items() if k != m.MARKER} == original
    assert g == original


@pytest.mark.parametrize('value', ['2027-02-30', 'tomorrow', 20270701, {}, False])
def test_malformed_boundary_holds(value):
    p = provenance(); p['field_provenance']['disposition']['effective_to'] = value
    assert m.annotate({'disposition':'VISA_EXEMPT'}, p, {'arrival_date':'2026-09-09'})[m.MARKER]['fields'][0]['reason'] == 'invalid_policy_interval'


def test_refresh_cannot_erase_announced_expiry():
    prior = provenance()['field_provenance']['disposition']
    newer = m.inherit_bounds(prior, {'verified_at':'2027-07-02', 'verifier':'ai', 'source_url':'https://evisa.gov.ph/page/policy'})
    assert newer['effective_to'] == '2027-06-30'
    assert newer['policy_interval_evidence']['effective_to']['source_url'] == prior['source_url']
    assert newer['policy_interval_evidence']['effective_to']['verified_at'] == '2026-09-09'
    p = {'field_provenance': {'disposition': newer}}
    assert m.annotate({'disposition':'VISA_EXEMPT'}, p, {'arrival_date':'2027-07-02'})[m.MARKER]


def test_new_explicit_reviewed_date_supersedes_expiry():
    prior = provenance()['field_provenance']['disposition']
    newer = m.inherit_bounds(prior, {'effective_to':'2028-06-30'})
    assert newer['effective_to'] == '2028-06-30'
    assert 'effective_to' not in newer['policy_interval_evidence']
    assert not m.annotate({'disposition':'VISA_EXEMPT'}, {'field_provenance':{'disposition':newer}}, {'arrival_date':'2027-07-02'}).get(m.MARKER)


def test_date_on_side_field_cannot_change_visa_rule():
    p = {'fields':['passport_validity'], 'field_provenance': {'passport_validity': provenance()['field_provenance']['disposition']}}
    assert not m.annotate({'disposition':'VISA_EXEMPT'}, p, {'arrival_date':'2028-01-01'}).get(m.MARKER)


def test_invalid_request_date_uses_current_date_and_removes_stale_derived_marker(monkeypatch):
    monkeypatch.setattr(m,'_today',lambda:date(2026,9,9))
    assert not m.annotate({'disposition':'VISA_EXEMPT',m.MARKER:{'old':True}}, provenance(), {'arrival_date':'invalid'}).get(m.MARKER)


@pytest.mark.parametrize('value', [None, False, 1, [], 'bad'])
def test_malformed_unrelated_provenance_container_does_not_crash(value):
    assert m.annotate({'disposition':'VISA_EXEMPT'}, {'fields':value,'field_provenance':value}, value) == {'disposition':'VISA_EXEMPT'}


def test_operator_recheck_persists_prior_expiry_and_its_original_evidence(tmp_path, monkeypatch):
    from app.visa_snapshot import verified_overrides as vo
    route = {'passport_nationality':'TWN','destination_country':'PHL','travel_purpose':'tourism','travel_document_type':'ordinary_passport','arrival_date':'2027-07-02'}
    seed = {'route':{'nationality':'TWN','destination':'PHL','travel_purpose':'tourism'},
            'source_url':'https://www.meco.org.tw/news/detail/1178','verified_at':'2026-09-09','verifier':'ai',
            'note':'Official fourteen-day privilege with an announced interval.',
            'effective_from':'2026-07-01','effective_to':'2027-06-30',
            'quote':'These regulations will remain in effect from 01 July 2026 to 30 June 2027.',
            'fields':{'disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free','permitted_stay_days':14}}
    seed_path=tmp_path/'seed.json'; seed_path.write_text(json.dumps([seed]))
    op_path=tmp_path/'operators.json'
    monkeypatch.setattr(vo,'OVERRIDES',seed_path)
    monkeypatch.setattr(vo,'operator_overrides_path',lambda:op_path)
    vo.reload()
    edit={'route':seed['route'],'source_url':'https://evisa.gov.ph/page/policy',
          'verified_at':'2027-07-02','verifier':'ai','note':'Later source read repeats the same visa verdict.',
          'fields':{'disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free'}}
    saved=vo.append_operator_entry(edit)
    assert saved['field_provenance']['disposition']['effective_to']=='2027-06-30'
    assert saved['field_provenance']['disposition']['policy_interval_evidence']['effective_to']['source_url']==seed['source_url']
    vo.reload()
    g,p=vo.apply({'disposition':'VISA_EXEMPT'},route)
    assert g[m.MARKER]['fields'][0]['reason']=='policy_expired'
    assert p['field_provenance']['disposition']['verified_at']=='2027-07-02'
    # A second side-field edit does not change the decision's policy clock.
    vo.append_operator_entry(dict(edit,fields={'permitted_stay_days':14}))
    assert vo.apply({'disposition':'VISA_EXEMPT'},route)[0][m.MARKER]
    # A subsequent official extension supersedes the inherited old notice,
    # despite the operator field's more recent (but non-date) recheck stamp.
    extension = dict(seed, effective_to='2028-06-30', verified_at='2027-06-20',
                     quote='These regulations will remain in effect until 30 June 2028.')
    seed_path.write_text(json.dumps([extension]))
    vo.reload()
    extended, proof = vo.apply({'disposition':'VISA_EXEMPT'}, route)
    assert not extended.get(m.MARKER)
    assert proof['field_provenance']['disposition']['effective_to'] == '2028-06-30'
    assert proof['field_provenance']['disposition']['policy_interval_evidence']['effective_to']['verified_at'] == '2027-06-20'
    vo.reload()
