"""Independent SQL storage-format and race checks for exact-issue CAS."""
import json
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from app.visa_snapshot.models import DatabaseIssueReport
from app.visa_snapshot import issue_revision as revision

@pytest.fixture
def engine(tmp_path):
 e=create_engine('sqlite:///'+str(tmp_path/'independent-issue.db'));DatabaseIssueReport.__table__.create(e)
 yield e
 e.dispose()

def new_issue():
 return DatabaseIssueReport(cache_key='HKG|HKG|SGP|tourism|default|unknown|v6',route={'passport_nationality':'HKG','destination_country':'SGP'},field='disposition',note='exact official claim',proposal={'fields':{'disposition':'签证'}})

@pytest.mark.parametrize('column',['proposal','route'])
@pytest.mark.parametrize('style',['unicode','compact','indent','order','null','scalar'])
def test_semantically_unchanged_json_storage_does_not_block_review(engine,column,style):
 value={'z':'签证','a':{'b':[None,False,1,1.5]}}
 if style=='null':value=None
 if style=='scalar':value='literal string'
 kwargs={'ensure_ascii':False} if style=='unicode' else {'separators':(',',':')} if style=='compact' else {'indent':2} if style=='indent' else {'sort_keys':True} if style=='order' else {}
 with Session(engine) as db:
  row=new_issue();db.add(row);db.commit();identity=row.id
  db.execute(text('UPDATE database_issue_reports SET '+column+'=:value WHERE id=:id'),{'value':json.dumps(value,**kwargs),'id':identity});db.commit();db.expire_all()
  row=db.get(DatabaseIssueReport,identity);before=revision.snapshot(row)
  revision.check_revision(row,revision.revision_sha256(before))
  row.status='dismissed';row.resolution='Reviewed actual unchanged issue'
  revision.save_revision(db,row,before);db.commit();after=revision.snapshot(row)
  assert after[column]==value and after['status']=='dismissed'
  assert {k:v for k,v in before.items() if k not in {'status','resolution','updated_at'}}=={k:v for k,v in after.items() if k not in {'status','resolution','updated_at'}}

@pytest.mark.parametrize('column,value',[('note','New source warning'),('field','government_fee'),('reported_by','new_monitor'),('org_id','new_org')])
def test_all_non_json_columns_remain_in_atomic_guard(engine,column,value):
 with Session(engine) as first:
  row=new_issue();other=new_issue();first.add_all([row,other]);first.commit();identity=row.id;untouched=revision.snapshot(other);before=revision.snapshot(row)
  with Session(engine) as second:
   current=second.get(DatabaseIssueReport,identity);setattr(current,column,value);second.commit()
  row.status='dismissed';row.resolution='stale resolution'
  with pytest.raises(HTTPException) as caught:revision.save_revision(first,row,before)
  assert caught.value.status_code==409
  first.expire_all();assert revision.snapshot(first.get(DatabaseIssueReport,other.id))==untouched
  current=first.get(DatabaseIssueReport,identity);assert getattr(current,column)==value and current.status=='open'

@pytest.mark.parametrize('race_kind',['proposal','format_only','status','other_issue'])
def test_race_after_raw_json_read_and_before_update(engine,monkeypatch,race_kind):
 from sqlalchemy.sql.dml import Update
 with Session(engine) as first:
  row=new_issue();other=new_issue();first.add_all([row,other]);first.commit();identity=row.id;other_id=other.id
  before=revision.snapshot(row);row.status='dismissed';row.resolution='reviewed pre-race state'
  execute=first.execute;interleaved=[]
  def racing_execute(statement,*args,**kwargs):
   if isinstance(statement,Update) and not interleaved:
    interleaved.append(True)
    with Session(engine) as second:
     if race_kind=='format_only':
      second.execute(text('UPDATE database_issue_reports SET proposal=:p WHERE id=:id'),{'p':json.dumps(before['proposal'],ensure_ascii=False,indent=2),'id':identity})
     else:
      target=second.get(DatabaseIssueReport,other_id if race_kind=='other_issue' else identity)
      if race_kind=='proposal':target.proposal={'new':'different late source claim'}
      else:target.status='acknowledged'
     second.commit()
   return execute(statement,*args,**kwargs)
  monkeypatch.setattr(first,'execute',racing_execute)
  if race_kind=='other_issue':
   revision.save_revision(first,row,before);first.commit();assert row.status=='dismissed'
  else:
   with pytest.raises(HTTPException) as error:revision.save_revision(first,row,before)
   assert error.value.status_code==409
  assert interleaved
  first.expire_all();current=first.get(DatabaseIssueReport,identity);other=first.get(DatabaseIssueReport,other_id)
  if race_kind=='proposal':assert current.status=='open' and current.proposal=={'new':'different late source claim'}
  elif race_kind=='status':assert current.status=='acknowledged'
  elif race_kind=='format_only':assert current.status=='open' and current.proposal==before['proposal']
  else:assert current.status=='dismissed' and other.status=='acknowledged'
