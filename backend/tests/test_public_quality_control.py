"""Open evaluation has full QC actions with honest, non-authenticated attribution."""
import asyncio
from datetime import date
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import security
from app.visa_snapshot import kimi_primary, tstation, verified_overrides as vo
from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseChangeLog

PUBLIC={'X-Ellis-Token':'public-quality-control','X-Org-Id':'forged-org','X-User-Id':'test-browser','X-Role':'admin'}

@pytest.fixture
def open_qc(monkeypatch):
    monkeypatch.setattr(security,'settings',lambda:SimpleNamespace(
        clerk_secret_key='',require_secure_admin=True,public_quality_control=True,
        admin_token='private-owner-credential-not-for-browser-12345',
        admin_user_id='owner',dev_api_token='dev-token'))


def test_open_qc_needs_no_private_key_and_binds_public_audit_identity(open_qc):
    p=asyncio.run(security.get_principal(authorization='',x_ellis_token=PUBLIC['X-Ellis-Token'],
        x_org_id='forged-org',x_user_id='owner',x_role='admin'))
    assert p.role=='quality_tester' and p.org_id=='platform'
    assert p.user_id.startswith('public-qc-') and p.user_id!='owner'
    security.require_quality_control(p)
    with pytest.raises(HTTPException):security.require_admin(p)


def test_closed_installation_does_not_accept_public_marker(monkeypatch):
    monkeypatch.setattr(security,'settings',lambda:SimpleNamespace(
        clerk_secret_key='',require_secure_admin=True,public_quality_control=False,
        admin_token='private-owner-credential-not-for-browser-12345',admin_user_id='owner',dev_api_token='dev-token'))
    with pytest.raises(HTTPException):
        asyncio.run(security.get_principal(authorization='',x_ellis_token='public-quality-control',x_org_id='platform',x_user_id='browser',x_role='admin'))


@pytest.mark.parametrize('path',['/database/records','/database/freshness','/database/issues','/database/changes','/database/changes.csv','/database/export.xlsx','/database/asks'])
def test_public_qc_tabs_and_exports_open(client,open_qc,path):
    assert client.get(path,headers=PUBLIC).status_code==200


def test_qc_role_does_not_become_unrelated_adapter_admin(client,open_qc):
    response=client.post('/admin/adapters',headers=PUBLIC,json={'country':'Test','visa_type':'tourist','config':{}})
    assert response.status_code==403


def test_public_edit_is_attributed_and_cannot_claim_verified_accuracy(client,db,open_qc,tmp_path,monkeypatch):
    seed=tmp_path/'seed.json';seed.write_text('[]')
    ops=tmp_path/'operators.json';ops.write_text('[]')
    monkeypatch.setattr(vo,'OVERRIDES',seed)
    monkeypatch.setattr(vo,'operator_overrides_path',lambda:ops);vo.reload()
    route={'passport_nationality':'CAN','passport_issuing_country':'CAN','destination_country':'JPN',
        'travel_purpose':'business','travel_document_type':'diplomatic_passport'}
    raw={'disposition':'VISA_EXEMPT','permitted_stay':'30 days','permitted_stay_days':30,'application_channel':'not_required'}
    row=KimiRouteGuidanceCache(cache_key=kimi_primary.cache_key(route),route=route,guidance=raw,verification={})
    db.add(row);db.commit()
    body={'nationality':'CAN','destination':'JPN','travel_purpose':'business','travel_document_type':'diplomatic_passport',
          'fields':{'permitted_stay':'31 days','permitted_stay_days':31},
          'source_url':'https://www.mofa.go.jp/visa/','note':'Public tester edits this field for evaluation.'}
    try:
        response=client.post('/database/records/edit',headers=PUBLIC,json=body)
        assert response.status_code==200,response.text
        saved=json.loads(ops.read_text())[0]
        assert saved['verifier']=='public' and 'Public Quality Control tester' in saved['verified_by']
        merged,provenance=vo.apply(raw,route)
        assert merged['permitted_stay_days']==31
        assert tstation._confidence(merged,provenance,grounded_ok=True,complete=True)=='Low'
        before=ops.read_bytes()
        bad=dict(body,fields={'government_fee':{'amount':100,'currency':'USD'}})
        rejected=client.post('/database/records/edit',headers=PUBLIC,json=bad)
        assert rejected.status_code==422 and ops.read_bytes()==before
        assert db.query(DatabaseChangeLog).filter_by(cache_key=row.cache_key).count()>=1
    finally:
        db.delete(row);db.commit();vo.reload()
