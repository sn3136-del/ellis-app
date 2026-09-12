"""Actual SQLite and endpoint races cannot close a changed source proposal."""
from copy import deepcopy
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import Base, SessionLocal
from app.visa_snapshot.models import DatabaseIssueReport
from app.visa_snapshot import issue_revision as revision

ADMIN={'authorization':'Bearer admin-token','x-org-id':'revision-review','x-user-id':'reviewer'}

def issue(**changes):
    values=dict(cache_key='HKG|HKG|SGP|tourism|default|unknown|v6',route={'passport_nationality':'HKG','destination_country':'SGP'},field='permitted_stay',note='Check the exact entry rule',reported_by='freshness_monitor',proposal={'fields':{'permitted_stay':{'page_says':'e-Pass determines stay','quote':'actual source quote'}}})
    values.update(changes);return DatabaseIssueReport(**values)

@pytest.mark.parametrize('proposal',[None,{}, {'text':'visa required 签证'}])
def test_cas_updates_only_owned_status_fields_and_preserves_source(tmp_path,proposal):
    engine=create_engine('sqlite:///'+str(tmp_path/'issues.db'))
    DatabaseIssueReport.__table__.create(engine)
    with Session(engine) as db:
        row=issue(proposal=proposal);db.add(row);db.commit();original=revision.snapshot(row)
        revision.check_revision(row,revision.revision_sha256(original))
        row.status='dismissed';row.resolution='Reviewed as superseded'
        revision.save_revision(db,row,original);db.commit();after=revision.snapshot(row)
        assert after['status']=='dismissed' and after['resolution']=='Reviewed as superseded'
        assert {k:v for k,v in after.items() if k not in {'status','resolution','updated_at'}}=={k:v for k,v in original.items() if k not in {'status','resolution','updated_at'}}
    engine.dispose()

@pytest.mark.parametrize('change',[{'proposal':{'new':'different source finding'}},{'status':'acknowledged'},{'route':{'passport_nationality':'USA'}},{'resolution':'Another operator reviewed this'}])
def test_two_sessions_detect_change_after_initial_revision_check(tmp_path,change):
    engine=create_engine('sqlite:///'+str(tmp_path/'issues.db'))
    DatabaseIssueReport.__table__.create(engine)
    with Session(engine) as first:
        row=issue();first.add(row);first.commit();original=revision.check_revision(row,revision.revision_sha256(revision.snapshot(row)))
        with Session(engine) as second:
            newer=second.get(DatabaseIssueReport,row.id)
            for key,value in change.items():setattr(newer,key,value)
            second.commit()
        row.status='dismissed';row.resolution='Stale operator review'
        with pytest.raises(HTTPException) as error:revision.save_revision(first,row,original)
        assert error.value.status_code==409
        first.expire_all();current=first.get(DatabaseIssueReport,original['id'])
        for key,value in change.items():assert getattr(current,key)==value
        assert current.status!='dismissed'
    engine.dispose()

def test_endpoint_returns_revision_and_refuses_stale_proposal(client,db):
    row=issue();db.add(row);db.commit();identity=row.id
    response=client.get('/database/issues',headers=ADMIN);assert response.status_code==200
    current=next(x for x in response.json()['issues'] if x['id']==identity)
    row.proposal={'fields':{'permitted_stay':{'page_says':'new rule'}}};db.commit()
    response=client.post('/database/issues/'+identity,headers=ADMIN,json={'status':'dismissed','resolution':'Old evidence','expected_issue_sha256':current['revision_sha256']})
    assert response.status_code==409
    db.expire_all();assert db.get(DatabaseIssueReport,identity).status=='open'

@pytest.mark.parametrize('send_revision',[True,False])
def test_endpoint_atomic_guard_catches_proposal_changed_during_request(client,db,monkeypatch,send_revision):
    row=issue();db.add(row);db.commit();identity=row.id
    current=next(x for x in client.get('/database/issues',headers=ADMIN).json()['issues'] if x['id']==identity)
    original_save=revision.save_revision
    def race(session,record,before):
        with SessionLocal() as writer:
            newer=writer.get(DatabaseIssueReport,identity);newer.proposal={'new_source':'arrived after request read'};writer.commit()
        return original_save(session,record,before)
    monkeypatch.setattr(revision,'save_revision',race)
    body={'status':'dismissed','resolution':'Old source'}
    if send_revision:body['expected_issue_sha256']=current['revision_sha256']
    response=client.post('/database/issues/'+identity,headers=ADMIN,json=body)
    assert response.status_code==409
    db.expire_all();current=db.get(DatabaseIssueReport,identity)
    assert current.status=='open' and current.proposal=={'new_source':'arrived after request read'}

def test_endpoint_current_revision_applies_normal_audited_status_update(client,db):
    row=issue();db.add(row);db.commit();identity=row.id;original=deepcopy(row.proposal)
    current=next(x for x in client.get('/database/issues',headers=ADMIN).json()['issues'] if x['id']==identity)
    response=client.post('/database/issues/'+identity,headers=ADMIN,json={'status':'dismissed','resolution':'Superseded by reviewed canonical correction','expected_issue_sha256':current['revision_sha256']})
    assert response.status_code==200,response.text
    db.expire_all();after=db.get(DatabaseIssueReport,identity)
    assert after.status=='dismissed' and after.proposal==original
    assert after.resolved_by=='reviewer' and after.resolved_at


def test_audit_failure_rolls_back_status_in_the_same_transaction(client,db,monkeypatch):
    from app import audit
    row=issue();db.add(row);db.commit();identity=row.id
    def fail(*args,**kwargs):raise RuntimeError('Injected audit failure before commit')
    monkeypatch.setattr(audit,'record',fail)
    with pytest.raises(RuntimeError,match='Injected audit failure'):
        client.post('/database/issues/'+identity,headers=ADMIN,json={'status':'dismissed','resolution':'Reviewed correction'})
    db.expire_all();assert db.get(DatabaseIssueReport,identity).status=='open'

