"""Install a reviewed four-route source packet with CAS and no scheduler signals.

Build is read-only. Apply changes only scoped operator entries, explicitly
reviewed raw uncertainty if supplied, update timestamps and change-log rows.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib,json,logging,os,sqlite3,sys,tempfile,uuid
from pathlib import Path

KEYS={'HKG|HKG|VNM|tourism|default|unknown|v6','IDN|IDN|KOR|tourism|default|unknown|v6',
      'MYS|MYS|RUS|tourism|default|unknown|v6','THA|THA|AUS|tourism|default|unknown|v6'}
BACKEND=Path('/opt/ellis/backend');DATABASE=Path('/var/lib/ellis/ellis.db')
OPERATORS=Path('/var/lib/ellis/operator_overrides.json');STATUS=Path('/var/lib/ellis/freshness-sweep-status.json')
def need(ok,msg):
 if not ok:raise RuntimeError(msg)
def stamp():return datetime.now(timezone.utc).isoformat()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,default=str,separators=(',',':')).encode()).hexdigest()
def save(p,body,attributes=None):
 data=body if isinstance(body,bytes) else (json.dumps(body,ensure_ascii=False,indent=2)+'\n').encode()
 fd,tmp=tempfile.mkstemp(prefix='.'+p.name+'-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb')as f:
   f.write(data);f.flush();mode,uid,gid=attributes or (0o600,os.getuid(),os.getgid())
   os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),uid,gid);os.fsync(f.fileno())
  os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def connect(ro=False):
 c=sqlite3.connect('file:'+str(DATABASE)+('?mode=ro'if ro else'?mode=rw'),uri=True,timeout=5)
 c.row_factory=sqlite3.Row;return c
def code_pin():return digest({str(p.relative_to(BACKEND)):sha(p)for p in sorted((BACKEND/'app').rglob('*.py'))})
def seed_pin():
 p=BACKEND.parent/'data/database_seed';return digest({str(f.relative_to(p)):sha(f)for f in sorted(p.rglob('*.json'))if f.name!='operator_overrides.json'})
def packet(path):
 b=json.loads(path.read_text());entries=b['packets'];need({e['cache_key']for e in entries}==KEYS and len(entries)==4,'Exact four-route scope required')
 for e in entries:
  key=e['cache_key'];parts=key.split('|');op=e['operator_entry'];f=op['fields']
  need(op['route']=={'nationality':parts[0],'destination':parts[2],'travel_purpose':'tourism','travel_document_type':'ordinary_passport'},'Incorrect route/document identity')
  need(op.get('verifier')=='ai' and op.get('field_provenance'),'Actual AI source authorship required')
  need(not any(k.startswith('_')or k in {'confidence','confidence_level','held','publication_state','operator_released','source_verified','unpublished_fields'}for k in f),'No grade/publication/blanket-absence changes')
  need(not e.get('issue_ids')and not e.get('issue_decisions'),'Issue transitions are outside this installer')
  cas=e.get('raw_cas_repair')
  need(not cas or cas['field']=='uncertainty','Only explicitly reviewed uncertainty can change in raw guidance')
 return b
def current(c,kp,vo):
 result={}
 for key in sorted(KEYS):
  rows=c.execute('select * from kimi_route_guidance_cache where cache_key=?',(key,)).fetchall();need(len(rows)==1,'Missing canonical route')
  r=dict(rows[0]);route=json.loads(r['route']);raw=json.loads(r['guidance'])
  need(kp.canonical_key(kp.cache_key(route))==key,'Canonical route identity changed')
  g,p=vo.apply(deepcopy(raw),route)
  issues=[dict(x)for x in c.execute('select * from database_issue_reports where cache_key=? order by id',(key,))]
  result[key]={'row':r,'route':route,'raw':raw,'guidance':g,'provenance':p,'issues':issues}
 return result
def assert_baseline(b,old):
 for p in b['packets']:
  prior=old[p['cache_key']]
  for k,v in p['expected_field_diff'].items():
   need(prior['guidance'].get(k)==v['before'],'Reviewed field drift: '+p['cache_key']+' '+k)
   need(p['operator_entry']['fields'].get(k)==v['after'],'Expected change mismatch')
  if p.get('raw_cas_repair'):need(prior['raw'].get('uncertainty')==p['raw_cas_repair']['before'],'Raw uncertainty drift')
def preview(b,old,op_bytes,vo,kp,ts,ev):
 ops=json.loads(op_bytes);need(isinstance(ops,list),'Invalid operator store')
 next_ops=ops+[p['operator_entry']for p in b['packets']]
 outbytes=(json.dumps(next_ops,ensure_ascii=False,indent=2)+'\n').encode();result={}
 prior=os.environ.get('ELLIS_OPERATOR_OVERRIDES')
 with tempfile.TemporaryDirectory(prefix='ellis-four-quotes-preview-')as tmp:
  f=Path(tmp)/'operators.json';f.write_bytes(outbytes)
  try:
   os.environ['ELLIS_OPERATOR_OVERRIDES']=str(f);vo.reload()
   with vo.current_store_table()as table:
    need(not getattr(table,'store_errors',()),'Candidate source store errors')
    for p in b['packets']:
     key=p['cache_key'];o=old[key];raw=deepcopy(o['raw'])
     if p.get('raw_cas_repair'):raw['uncertainty']=deepcopy(p['raw_cas_repair']['after'])
     g,prov=vo.apply(raw,o['route']);fields=p['operator_entry']['fields']
     need(not vo._field_errors(fields),'Candidate structural field error')
     need(all(g.get(k)==v for k,v in fields.items()),'Candidate field discarded: '+key)
     changed={k for k in set(g)|set(o['guidance'])if g.get(k)!=o['guidance'].get(k)}
     expected=set(p['expected_field_diff'])|({'uncertainty'}if p.get('raw_cas_repair')else set())
     need(changed==expected,'Unreviewed field delta: '+key)
     need(not kp.serve_time_invariants(g)and not kp.validate_answer(g)[2],'Candidate invariant conflict: '+key)
     rows=ts.records_for_route(o['route'],g,prov,disputed_fields=[])
     need(len(rows)==(2 if key.startswith('HKG|')else 1),'Product count changed')
     need(all(r['visa_requirement']=='Visa Required in Advance'and r.get('confidence_level')in('High','Medium')and not r.get('_evidence_low')for r in rows),'Route requirement/confidence regression')
     override=table[vo._key(o['route']['passport_nationality'],o['route']['destination_country'],'tourism','ordinary_passport')]
     for i,r in enumerate(rows):
      r['_product_index']=i;r['_field_evidence']=ev.for_record(r,o['route'],g,prov,{},active_override=override)
      need(r['_field_evidence'].get('visa_requirement'),'Requirement quote absent: '+key)
     result[key]={'guidance':g,'provenance':prov,'rows':rows,'raw':raw}
  finally:
   if prior is None:os.environ.pop('ELLIS_OPERATOR_OVERRIDES',None)
   else:os.environ['ELLIS_OPERATOR_OVERRIDES']=prior
   vo.reload()
 return result,outbytes
def build(b,vo,kp,ts,ev):
 with vo.operator_write_lock(timeout_seconds=5):
  op=OPERATORS.read_bytes();code=code_pin();seeds=seed_pin();c=connect(True)
  try:
   c.execute('BEGIN');old=current(c,kp,vo);c.rollback()
  finally:c.close()
  assert_baseline(b,old);projected,newbytes=preview(b,old,op,vo,kp,ts,ev)
  need(op==OPERATORS.read_bytes()and code==code_pin()and seeds==seed_pin(),'Source drift during preview')
  return {'schema':1,'packet_sha256':digest(b),'helper_sha256':sha(Path(__file__)),'built_at':stamp(),
   'source_code_sha256':code,'seed_sha256':seeds,'operator_before_sha256':hashlib.sha256(op).hexdigest(),
   'operator_after_sha256':hashlib.sha256(newbytes).hexdigest(),'baseline_sha256':digest(old),
   'baseline':old,'preview':projected,'sweep_status_sha256':sha(STATUS),'production_mutations':False,'paid_calls':0}
def apply(b,m,receipt,vo,kp,ts,ev):
 need(not receipt.exists(),'Existing receipt requires review before retry')
 need(m['packet_sha256']==digest(b)and m['helper_sha256']==sha(Path(__file__)),'Packet/helper changed after review')
 with vo.operator_write_lock(timeout_seconds=5):
  need(code_pin()==m['source_code_sha256']and seed_pin()==m['seed_sha256'],'Code/seed drift')
  op=OPERATORS.read_bytes();need(hashlib.sha256(op).hexdigest()==m['operator_before_sha256'],'Operator drift')
  c=connect();swapped=False;committing=False
  stat=OPERATORS.stat();attrs=(stat.st_mode&0o7777,stat.st_uid,stat.st_gid)
  report={'state':'prepared','started_at':stamp(),'manifest_sha256':digest(m),'packet_sha256':digest(b),'service_signals_sent':0,'issue_changes':0,'verification_changes':0}
  try:
   c.execute('BEGIN IMMEDIATE');old=current(c,kp,vo);need(digest(old)==m['baseline_sha256'],'Route or issue changed after preview')
   assert_baseline(b,old);projected,newbytes=preview(b,old,op,vo,kp,ts,ev)
   need(digest(projected)==digest(m['preview'])and hashlib.sha256(newbytes).hexdigest()==m['operator_after_sha256'],'Preview changed')
   backup=receipt.with_name(receipt.stem+'-operator-before.json');need(not backup.exists(),'Backup already exists')
   save(backup,op);need(sha(backup)==m['operator_before_sha256'],'Backup checksum mismatch');report['operator_backup']=str(backup);save(receipt,report)
   need(OPERATORS.read_bytes()==op,'Operator drift before swap');swapped=True;save(OPERATORS,newbytes,attrs)
   ids=[]
   for p in b['packets']:
    key=p['cache_key'];r=old[key]['row'];newraw=json.dumps(projected[key]['raw'],ensure_ascii=False);now=stamp()
    n=c.execute('update kimi_route_guidance_cache set guidance=?,updated_at=? where cache_key=? and guidance=? and route=? and verification IS ?',(newraw,now,key,r['guidance'],r['route'],r['verification'])).rowcount
    need(n==1,'Canonical CAS lost')
    changes={k:{'from':v['before'],'to':v['after']}for k,v in p['expected_field_diff'].items()}
    changes['field_provenance']={'from':old[key]['provenance'].get('field_provenance'),'to':p['operator_entry']['field_provenance']}
    if p.get('raw_cas_repair'):changes['uncertainty']={'from':p['raw_cas_repair']['before'],'to':p['raw_cas_repair']['after']}
    ident=uuid.uuid4().hex;ids.append(ident)
    c.execute('insert into database_change_log(id,cache_key,route,action,origin,changes,note,created_at,updated_at)values(?,?,?,?,?,?,?,?,?)',(ident,key,r['route'],'modify','operator-edit',json.dumps(changes,ensure_ascii=False),'Trip.com four-route official field quotation review by Codex AI agents; exact source URLs and field/product ownership retained. No manual grade, publication, dispute or freshness verification override.',now,now))
   committing=True;c.commit();report.update(state='committed',finished_at=stamp(),change_log_ids=ids,operator_after_sha256=sha(OPERATORS))
  except BaseException:
   if committing:
    report['state']='commit_outcome_requires_review';save(receipt,report);raise
   c.rollback()
   if swapped:
    need(OPERATORS.read_bytes()in(op,newbytes),'External operator write; preserve for review')
    if OPERATORS.read_bytes()==newbytes:save(OPERATORS,op,attrs)
   if receipt.exists():report['state']='rolled_back_uncommitted_change';save(receipt,report)
   raise
  finally:c.close();vo.reload()
 report['sweep_status_unchanged']=sha(STATUS)==m['sweep_status_sha256'];save(receipt,report);return report
def main():
 os.umask(0o077);logging.disable(logging.CRITICAL)
 mode,p,m=sys.argv[1:4];p=Path(p).resolve();m=Path(m).resolve();need(mode in {'build','apply'},'Expected build/apply')
 os.environ.update(ELLIS_OPERATOR_OVERRIDES=str(OPERATORS),ELLIS_BACKGROUND_RENEWAL='0',DATABASE_URL='sqlite:///file:'+str(DATABASE)+'?mode=ro&uri=true')
 sys.path.insert(0,str(BACKEND))
 from app.visa_snapshot import verified_overrides as vo,kimi_primary as kp,tstation as ts,record_evidence as ev
 b=packet(p)
 if mode=='build':
  need(not m.exists(),'Manifest exists');result=build(b,vo,kp,ts,ev);save(m,result)
  print(json.dumps({'state':'read_only_preview_built','manifest_sha256':sha(m),'routes':len(result['preview'])}))
 else:
  need(len(sys.argv)==5 and sha(m)==sys.argv[4],'Exact reviewed manifest checksum required')
  print(json.dumps(apply(b,json.loads(m.read_text()),m.with_name(m.stem+'-receipt.json'),vo,kp,ts,ev)))
if __name__=='__main__':main()
