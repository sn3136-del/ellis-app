from copy import deepcopy
from pathlib import Path
import json
import pytest
from scripts import convert_reviewed_india_entry_ports as c
from scripts.prepare_reviewed_product_patch import PatchRejected
from app.visa_snapshot import tstation as t

D = Path(__file__).resolve().parents[2] / 'data/database_seed'
M = json.loads((D/'reviewed_india_entry_ports_manifest_20260910.json').read_text())
O = json.loads((D/'reviewed_india_entry_ports_overlay_20260910.json').read_text())
L = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in M['routes']]


@pytest.fixture(scope='module')
def result(): return c.convert(M, L)


def test_full_prepared_rebuild_and_source_scope(result):
    overlay, report = result
    assert overlay == O and c.build_manifest(M['specification'], L) == M
    assert len(report['routes']) == 17 and len(overlay['entries']) == 15
    assert sum(len(r['records']) for r in report['routes']) == 75
    assert sum(x['route_changed'] for x in c.CONTRACT.values()) == 13
    assert sum(len(x['products']) for x in c.CONTRACT.values()) == 68
    assert not any(report[k] for k in ('raw_writes','operator_writes','issue_changes','renew_fresh_until','confidence_changed','new_release'))


@pytest.mark.parametrize('index', range(17))
@pytest.mark.parametrize('field', c.BASELINE_KEYS)
def test_all_six_layers_for_every_context_reject_drift(index, field):
    layers = deepcopy(L)
    value = layers[index][field]
    if isinstance(value, dict): value['unreviewed'] = True
    else: value.append({'unreviewed': True})
    with pytest.raises(PatchRejected): c.validate(M['specification'], layers)


@pytest.mark.parametrize('mode', ['missing','duplicate','extra','source_text','source_body','source_url','source_date','source_method','missing_source','extra_source','extra_fact','grade','product_type','land_scope','drop_air','proof_status','policy_date','wrong_document'])
def test_source_or_closed_instruction_tampering_rejects(mode):
    manifest = deepcopy(M); layers = deepcopy(L); spec = manifest['specification']; change = spec['routes'][0]['changes']
    if mode == 'missing': layers.pop()
    elif mode == 'duplicate': layers[-1] = deepcopy(layers[0])
    elif mode == 'extra': layers.append(deepcopy(layers[0]))
    elif mode == 'source_text': spec['sources'][0]['text'] += ' false'
    elif mode == 'source_body': spec['sources'][0]['capture_body_sha256'] = '0'*64
    elif mode == 'source_url': spec['sources'][0]['url'] = 'https://pib.gov.in/unrelated'
    elif mode == 'source_date': spec['sources'][0]['checked_at'] = '2027-01-01'
    elif mode == 'source_method': spec['sources'][0]['method'] = 'unreviewed'
    elif mode == 'missing_source': spec['sources'] = []
    elif mode == 'extra_source': spec['sources'].append(deepcopy(spec['sources'][0]))
    elif mode == 'extra_fact': change['visa_category'] = 'New'
    elif mode == 'grade': change['confidence'] = 'high'
    elif mode == 'product_type': change['visa_products'][0]['type'] = 'Paper visa'
    elif mode == 'land_scope': change['entry_requirements'] = change['entry_requirements'].replace('designated e-Visa airport, seaport or land port','any land border')
    elif mode == 'drop_air': change['entry_requirements'] = change['entry_requirements'].split('For international arrivals by air')[0]
    elif mode == 'proof_status': change['visa_products'][0]['field_provenance']['entry_requirements']['status'] = 'reviewed'
    elif mode == 'policy_date': change['policy_valid_until'] = '2027-01-01'
    elif mode == 'wrong_document': spec['routes'][0]['route']['travel_document_type'] = 'diplomatic_passport'
    with pytest.raises(PatchRejected): c.convert(manifest, layers)


def test_only_exact_sentences_and_their_partial_proofs_change(result):
    for layer, preview in zip(L, result[1]['routes'], strict=True):
        assert layer['cache_key'] == preview['cache_key']; config = c.CONTRACT[layer['cache_key']]
        before = layer['merged_guidance']; after = preview['guidance']
        assert c._mask_changed(before, config) == c._mask_changed(after, config)
        owners = [(before, after, layer['source_provenance']['field_provenance'], preview['source_provenance']['field_provenance'])] if config['route_changed'] else []
        for item in config['products']:
            a, b = before['visa_products'][item['index']], after['visa_products'][item['index']]
            owners.append((a, b, a['field_provenance'], b['field_provenance']))
        for a, b, ap, bp in owners:
            assert a['entry_requirements'].replace(c.OLD, c.NEW) == b['entry_requirements']
            assert c.OLD not in b['entry_requirements'] and c.NEW in b['entry_requirements']
            old, new = ap['entry_requirements'], bp['entry_requirements']
            assert new['status'] == 'partial' and new['verification_scope'] == old['verification_scope']
            assert new['verified_elements'] == old['verified_elements'] + [c.NEW]
            for key in old.keys() - {'verified_elements','retained_unverified_elements','supporting_evidence'}: assert new[key] == old[key]
            assert new['supporting_evidence'][:-1] == old['supporting_evidence']
            proof = new['supporting_evidence'][-1]
            assert proof['verified_element'] == c.NEW and proof['source_url'] == c.PIB
            assert proof['subject'] == new['subject'] and proof['quote'] in M['specification']['sources'][0]['text']
            assert c.OLD not in ' '.join(new['retained_unverified_elements'])
            assert 'Have return/onward travel and sufficient funds; biometrics' in ' '.join(new['retained_unverified_elements'])


def test_hkg_regular_vnm_and_japan_korea_voa_remain_exact(result):
    old = {l['cache_key']: l for l in L}
    for r in result[1]['routes']:
        l = old[r['cache_key']]
        if r['cache_key'].startswith(('HKG|','VNM|')):
            assert r['guidance'] == l['merged_guidance'] and r['source_provenance'] == l['source_provenance']
        if r['cache_key'].startswith(('JPN|','KOR|')):
            assert r['guidance']['entry_requirements'] == l['merged_guidance']['entry_requirements']
            assert r['guidance']['visa_products'][0] == l['merged_guidance']['visa_products'][0]
            assert 'Six' in r['guidance']['visa_products'][0]['entry_requirements'] or 'six' in r['guidance']['visa_products'][0]['entry_requirements']


def test_all_record_cells_and_grades_except_entry_text_unchanged(result):
    old_rows = []; new_rows = []
    for l, r in zip(L, result[1]['routes'], strict=True):
        old = t.records_for_route(l['route'],l['merged_guidance'],l['source_provenance']); new = r['records']
        old_rows.extend(old); new_rows.extend(new)
        for a, b in zip(old,new,strict=True):
            a = deepcopy(a); b = deepcopy(b); a.pop('entry_requirements'); b.pop('entry_requirements'); assert a == b
    assert t.acceptance_summary(old_rows) == t.acceptance_summary(new_rows)
    assert all(r['confidence_level'] == 'Low' for r in new_rows)


@pytest.mark.parametrize('issue',[False,True])
@pytest.mark.parametrize('pending',[False,True])
def test_actual_cached_readers_preserve_holds_raw_dates_history(result, monkeypatch, tmp_path, issue, pending):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.visa_snapshot import verified_overrides as vo, kimi_primary as kp, records_guard
    from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog
    before_table = vo._parse_rows([e for l in L for e in l['seed_entries']], {})
    after_table = vo._parse_rows(result[0]['entries'], deepcopy(before_table))
    engine = create_engine('sqlite:///:memory:')
    for model in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog): model.__table__.create(engine)
    monkeypatch.setattr(kp,'_call',lambda *a,**k:pytest.fail('No model regeneration'))
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES',str(tmp_path/'none.json'));monkeypatch.setattr(vo,'_table',lambda:after_table)
    with Session(engine) as db:
        for l in L:
            db.add(KimiRouteGuidanceCache(cache_key=l['cache_key'], route=deepcopy(l['route']), guidance=deepcopy(l['raw_guidance']),verification={'detail_pending':pending},fresh_until=datetime(2099,1,1,tzinfo=timezone.utc),status='KIMI_PRIMARY'))
            db.add(DatabaseChangeLog(cache_key=l['cache_key'],action='add',origin='fixture',changes={'unchanged':True}))
            if issue: db.add(DatabaseIssueReport(cache_key=l['cache_key'],route=deepcopy(l['route']),field='passport_validity',status='open',reported_by='freshness_monitor',proposal={'fields':{'passport_validity':{'record_holds':'old','page_says':'new'}}}))
        db.commit()
        def snapshot(): return {m.__name__:[{col.name:deepcopy(getattr(r,col.name)) for col in m.__table__.columns} for r in db.scalars(select(m))] for m in (KimiRouteGuidanceCache,DatabaseIssueReport,DatabaseChangeLog)}
        initial = snapshot()
        for l in L:
            with patch.object(vo,'_table',return_value=before_table): before = kp.get_route_guidance(db,l['route']); old = records_guard.apply_records_hold(l['route'],deepcopy(before),db)
            after = kp.get_route_guidance(db,l['route']); public = records_guard.apply_records_hold(l['route'],deepcopy(after),db)
            assert before['cached'] and after['cached'] and bool(old.get('held')) == bool(public.get('held'))
            if issue or pending: assert public['held']
            elif not public.get('held'):
                g = public['guidance']; assert c.OLD not in (g.get('entry_requirements') or '')
                assert all(c.OLD not in p.get('entry_requirements','') for p in g.get('visa_products',[]))
                assert g['arrival_card'] == before['guidance']['arrival_card']
        assert snapshot() == initial
