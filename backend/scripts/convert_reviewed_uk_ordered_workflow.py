"""Exact17-context source-ordered workflow correction; detached conversion only."""
from copy import deepcopy
from datetime import date
from scripts._reviewed_uk_ordered_workflow_contract import CONTRACT,SPECIFICATION_SHA256
from scripts.convert_reviewed_product_validity import BASELINE_KEYS,_current_map,_sources
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
from app.visa_snapshot.ordered_application_instructions import STEPS,field_proof,WORKFLOW_ID
MANIFEST='reviewed_uk_ordered_workflow_manifest_20260910.json'
OVERLAY='reviewed_uk_ordered_workflow_overlay_20260910.json'
TEXT=[s['text'] for s in STEPS]
def _require(ok,message):
 if not ok:raise PatchRejected(message)
def _validate(spec,layers):
 from app.visa_snapshot.evidence_validator import jurisdiction_matches,quote_in_text
 _require(isinstance(spec,dict) and digest(spec)==SPECIFICATION_SHA256,'Exact reviewed source/workflow specification changed')
 _require(date.today()>=date(2026,9,10),'Source review date is future')
 _require(isinstance(layers,list) and len(layers)==17,'Exactly17 current route contexts required')
 current=_current_map(layers,set(CONTRACT));sources=_sources(spec)
 for step in STEPS:
  for proof in step['evidence']:
   source=sources.get(proof['source_id']);_require(source and source['url']==proof['source_url'] and source['checked_at']=='2026-09-10' and jurisdiction_matches(source['url'],'GBR') and quote_in_text(proof['quote'],source['text']),'Ordered step lacks its exact captured applicable source')
 for key,c in CONTRACT.items():
  l=current[key];_require(all(k in l for k in BASELINE_KEYS),'All six baselines required')
  _require({k:digest(l[k]) for k in BASELINE_KEYS}==c['baseline_sha256'],'Current six-layer state changed: '+key)
  _require(not l['operator_entries'],'Unreviewed operator layer')
  g=l['merged_guidance'];ps=g.get('visa_products') or []
  if c['default']:_require(g.get('disposition')=='VISA_REQUIRED' and g.get('application_channel')=='online_portal' and g.get('appointment_required') is True and g.get('biometrics_required') is True,'Required online-then-VAC default scope absent')
  for p in c['products']:
   old=ps[p['index']];_require(old['type']==p['type'] and digest(old)==p['old_sha256'] and old.get('application_channel')=='online_portal','Reviewed own Standard Visitor product changed')
 return current
def build_manifest(spec,layers):
 current=_validate(spec,layers)
 return {'schema_version':1,'kind':'uk_ordered_workflow_exact_layers','specification':deepcopy(spec),'routes':[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(CONTRACT)]}
def _without_changes(value,default,names):
 value=deepcopy(value)
 if default:value.pop('submission_process',None)
 for p in value.get('visa_products') or []:
  if p['type'] not in names:continue
  p.pop('submission_process',None)
  if isinstance(p.get('field_provenance'),dict):
   p['field_provenance'].pop('submission_process',None)
   if not p['field_provenance']:p.pop('field_provenance')
 return value
def convert(manifest,layers):
 from app.visa_snapshot import verified_overrides as vo,tstation,kimi_primary as kp
 _require(isinstance(manifest,dict) and set(manifest)=={'schema_version','kind','specification','routes'},'Malformed reviewed manifest')
 baselines=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
 _require(digest(build_manifest(manifest['specification'],baselines))==digest(manifest),'Manifest differs from exact reviewed reconstruction')
 current=_validate(manifest['specification'],layers);entries=[];previews=[]
 for key in sorted(CONTRACT):
  l=current[key];r=l['route'];c=CONTRACT[key];identity=vo._key(r['passport_nationality'],'GBR','tourism','ordinary_passport')
  prior=vo._parse_rows(l['seed_entries'],{}).get(identity);_require(prior,'Original source ownership absent')
  before,checked=vo.merge_verified_fields(l['raw_guidance'],prior['fields'],source_url=prior['source_url'])
  prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
  _require(digest(before)==digest(l['merged_guidance']) and digest(prov)==digest(l['source_provenance']),'Original canonical reader/provenance mismatch')
  out=deepcopy(prior);names={p['type'] for p in c['products']}
  if c['default']:
   out['fields']['submission_process']=deepcopy(TEXT);out['field_provenance']['submission_process']=vo._provenance(field_proof(r))
  if names:
   _require(digest(prior['fields'].get('visa_products'))==digest(before.get('visa_products')),'Products are not wholly seed-owned')
   products=deepcopy(before['visa_products'])
   for p in products:
    if p['type'] in names:p['submission_process']=deepcopy(TEXT);p.setdefault('field_provenance',{})['submission_process']=field_proof(r,p)
   out['fields']['visa_products']=products
  expected=deepcopy(out);out.update(route={'nationality':r['passport_nationality'],'destination':'GBR','travel_purpose':'tourism','travel_document_type':'ordinary_passport'},review_id=WORKFLOW_ID,partial_review=True,field_review_scope='ordered_standard_visitor_submission_stages_only')
  parsed=vo._parse_rows([out],{}).get(identity);_require(digest(parsed)==digest(expected),'Serving loader changed proof ownership or facts')
  after,checked2=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
  ap=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),fields=sorted(checked2),field_provenance=parsed['field_provenance'])
  _require(set(checked2)==set(checked)|({'submission_process'} if c['default'] else set()),'Unrelated verification credit changed')
  _require(digest(_without_changes(before,c['default'],names))==digest(_without_changes(after,c['default'],names)),'Unreviewed fact, fee, condition or product changed')
  a,b=deepcopy(prov),deepcopy(ap)
  if c['default']:
   for v in (a,b):v['fields']=[f for f in v['fields'] if f!='submission_process'];v['field_provenance'].pop('submission_process',None)
  _require(digest(a)==digest(b),'Unrelated route proof changed')
  _require(kp.serve_time_invariants(before)==kp.serve_time_invariants(after),'Unrelated invariant changed')
  if c['default'] or names:entries.append(out)
  previews.append(dict(cache_key=key,route=r,guidance=after,source_provenance=ap,records=tstation.records_for_route(r,after,ap),apply_steps=kp.canonical_steps(after,route=r,source_verified=ap)))
 return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=WORKFLOW_ID,entries=entries,status='detached; exact six-layer preflight required'),dict(routes=previews,contexts_checked=17,default_workflows_reviewed=6,product_workflows_reviewed=60,raw_writes=False,operator_writes=False,issue_changes=False,renew_fresh_until=False,confidence_changed=False)
def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
 manifest=build_manifest(spec,layers);overlay,report=convert(manifest,layers)
 _require(digest(manifest)==digest(prepared_manifest) and digest(overlay)==digest(prepared_overlay),'Complete prepared artifacts differ from current reviewed rebuild')
 return report
