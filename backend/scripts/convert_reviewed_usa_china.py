"""Detached exact-layer current US-resident tourist application correction.

Preserves raw/history/operator/issue state and every unrelated fact/proof. It
changes only the reviewed existing fields/options; no individual visa grant,
policy completeness, blanket product eligibility or freshness is fabricated.
"""
from copy import deepcopy
from datetime import date
from scripts._reviewed_usa_china_contract import CONTRACT,SPECIFICATION_SHA256
from scripts.convert_reviewed_product_validity import BASELINE_KEYS,_sources,_current_map
from scripts.prepare_reviewed_product_patch import PatchRejected,digest
MANIFEST='reviewed_usa_china_manifest_20260910.json'
# Fields that describe the filing procedure at the Chinese missions in the
# United States and therefore apply to applicants lawfully resident there.
# Nationwide entry rules (the verdict, stays, passport clocks at the border,
# arrival card, exceptions, health and entry evidence) are not scoped.
# passport_validity_requirement is the border entry clock and stays nationwide.
RESIDENCE_SCOPED_FIELDS=('application_channel','application_channel_detail','route_workflow_type','forms',
 'appointment_required','biometrics_required','interview_required','photo_requirements','processing_time',
 'account_registration_steps','submission_process','payment_process','government_fee','required_documents')
OVERLAY='reviewed_usa_china_overlay_20260910.json'

def _need(ok,message):
 if not ok:raise PatchRejected(message)

def _validate(spec,layers):
 from app.visa_snapshot.evidence_validator import quote_in_text,jurisdiction_matches
 from app.visa_snapshot.authority import is_government_host,hostname
 _need(isinstance(spec,dict) and digest(spec)==SPECIFICATION_SHA256,'Exact reviewed field/source specification changed')
 _need(date.today()>=date(2026,9,10),'Review date is in the future')
 _need(isinstance(layers,list) and len(layers)==1,'Exactly one current route required')
 current=_current_map(layers,{CONTRACT['cache_key']});layer=current[CONTRACT['cache_key']]
 _need(all(k in layer for k in BASELINE_KEYS),'Every six-layer baseline required')
 _need({k:digest(layer[k]) for k in BASELINE_KEYS}==CONTRACT['baseline_sha256'],'Current six-layer state changed')
 _need(layer['route']==CONTRACT['route'] and not layer['operator_entries'],'Wrong route or operator scope')
 sources=_sources(spec)
 for source in sources.values():
  _need(source.get('checked_at')=='2026-09-10' and is_government_host(hostname(source['url'])),'Source is not the reviewed dated official publisher')
  if source['id']=='us_state_entry':
   _need(source['url']=='https://travel.state.gov/en/international-travel/travel-advisories/china.html' and source['authority'].startswith('United States Department of State'),'Origin guidance cannot be attributed to a destination authority')
  else:_need(jurisdiction_matches(source['url'],'CHN'),'Destination source ownership changed')
 changes=spec['changes'];_need([c['field'] for c in changes]==CONTRACT['fields'],'Reviewed field set changed')
 for change in changes:
  f=change['field'];p=change['proof']
  _need(change['old_raw']==layer['raw_guidance'].get(f) and change['old_merged']==layer['merged_guidance'].get(f),'Reviewed old value changed')
  if p['status']=='unknown':
   _need(change['new'] is None and set(p)=={'status','verifier','reason'} and p['reason'],'Unknown cannot gain a source, date or absence credit')
  else:
   _need(p['status']=='reviewed' and p['verified_at']=='2026-09-10' and p['verifier']=='ai','Invalid field review')
   for e in p['verification_scope']['evidence']:
    _need(e['source_id'] in sources and e['source_url']==sources[e['source_id']]['url'] and quote_in_text(e['quote'],sources[e['source_id']]['text']),'Field lacks literal captured evidence')
   _need(p['subject']=={k:CONTRACT['route'][k] for k in ('passport_nationality','destination_country','travel_purpose','travel_document_type')},'Field subject changed')
 for patch,old in zip(spec['products'],layer['merged_guidance']['visa_products'],strict=True):
  _need(patch['type']==old['type'] and patch['old_sha256']==digest(old),'Existing product scope changed')
  for field,p in patch['new']['field_provenance'].items():
   if p['status']=='unknown':
    _need(patch['new'][field] is None and set(p)=={'status','verifier','reason'},'Unknown product term gained credit')
   else:
    for e in p['verification_scope']['evidence']:
     _need(e['source_url']==sources[e['source_id']]['url'] and quote_in_text(e['quote'],sources[e['source_id']]['text']),'Product field lacks own captured evidence')
    _need(p['subject']['product_type']==old['type'],'Sibling proof contamination')
 return layer

def build_manifest(spec,layers):
 layer=_validate(spec,layers)
 return {'schema_version':1,'kind':'usa_china_tourism_exact_layers','specification':deepcopy(spec),'baseline':{k:deepcopy(layer[k]) for k in BASELINE_KEYS},'cache_key':CONTRACT['cache_key']}

def _strip_reviewed(g):
 g=deepcopy(g)
 for field in CONTRACT['fields']:g.pop(field,None)
 for p in g.get('visa_products') or []:
  for f in ('entry','validity','max_stay_days','permitted_stay','notes','disposition','requirement_detail'):p.pop(f,None)
  own=p.get('field_provenance')
  if isinstance(own,dict):
   for f in ('entry','validity','max_stay_days','permitted_stay','notes','fee','disposition','requirement_detail'):own.pop(f,None)
   if not own:p.pop('field_provenance',None)
 return g

def convert(manifest,layers):
 from app.visa_snapshot import verified_overrides as vo,tstation,kimi_primary as kp
 _need(isinstance(manifest,dict) and set(manifest)=={'schema_version','kind','specification','baseline','cache_key'},'Malformed manifest')
 baseline=dict(deepcopy(manifest['baseline']),cache_key=manifest['cache_key'])
 _need(digest(build_manifest(manifest['specification'],[baseline]))==digest(manifest),'Prepared manifest differs from reviewed rebuild')
 layer=_validate(manifest['specification'],layers);r=layer['route'];spec=manifest['specification']
 key=vo._key('USA','CHN','tourism','ordinary_passport');prior=vo._parse_rows(layer['seed_entries'],{}).get(key)
 _need(prior,'Effective source ownership missing')
 before,checked=vo.merge_verified_fields(layer['raw_guidance'],prior['fields'],source_url=prior['source_url'])
 bp=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
 _need(digest(before)==digest(layer['merged_guidance']) and digest(bp)==digest(layer['source_provenance']),'Canonical reader baseline mismatch')
 _need(digest(prior['fields'].get('visa_products'))==digest(before['visa_products']),'Existing products are not wholly seed-owned')
 out=deepcopy(prior)
 for c in spec['changes']:
  out['fields'][c['field']]=deepcopy(c['new']);out['field_provenance'][c['field']]=deepcopy(c['proof'])
 out['fields']['visa_products']=[deepcopy(p['new']) for p in spec['products']]
 # Retain the historical collection-level provenance: each new fact has own
 # evidence; this field patch never claims to have reviewed the full menu.
 out.update(route={'nationality':'USA','destination':'CHN','travel_purpose':'tourism','travel_document_type':'ordinary_passport'},review_id=spec['id'],partial_review=True,field_review_scope='existing_us_tourism_fields_and_individual_grant_terms_only')
 scoped=[f for f in CONTRACT['fields'] if f in RESIDENCE_SCOPED_FIELDS and f in out['fields']]
 out['applicability']={'lawful_country_of_residence':CONTRACT['route']['lawful_country_of_residence'],'fields':scoped}
 parsed=vo._parse_rows([out],{}).get(key);_need(parsed,'Serving loader rejected candidate')
 _need(set(parsed.get('field_applicability') or {})==set(scoped),'Residence applicability was not retained by the loader')
 _need(vo._applicable(parsed,{'lawful_country_of_residence':'CAN'}) is not None and not (set(scoped)&set(vo._applicable(parsed,{'lawful_country_of_residence':'CAN'})['fields'])),'Residence boundary is not enforced by the lookup')
 after,checked2=vo.merge_verified_fields(layer['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
 ap=dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed),fields=sorted(checked2),field_provenance=parsed['field_provenance'])
 _need(digest(_strip_reviewed(before))==digest(_strip_reviewed(after)),'An unrelated fact or product identity changed')
 for c in spec['changes']:_need(after.get(c['field'])==c['new'],'Serving loader dropped corrected field '+c['field'])
 _need(after['visa_products']==out['fields']['visa_products'],'Serving changed product facts or own proofs')
 for f,p in bp['field_provenance'].items():
  if f not in CONTRACT['fields']:_need(ap['field_provenance'].get(f)==p,'Unrelated field authorship changed')
 _need(before['confidence']==after['confidence'],'Self-rating cannot increase')
 # Corrected raw invariants may disappear, but no new contradiction is accepted.
 _need(not kp.serve_time_invariants(after),'Correction introduces or retains a serving invariant conflict')
 records=tstation.records_for_route(r,after,ap)
 _need(len(records)==3 and all(x['confidence_level']!='High' for x in records),'No product removal or High grade is permitted')  # checked with gaps is Medium, never High
 _need(all(x.get('info_validity') is None for x in records),'Tariff end must not become whole-product policy expiry')
 _need(all(x.get('max_stay_duration') is None and x.get('validity_duration') is None and x.get('entries') is None for x in records),'Individual grant became a numeric guarantee')
 report={'routes':[{'cache_key':CONTRACT['cache_key'],'route':r,'guidance':after,'source_provenance':ap,'records':records,**kp.application_instructions(after,route=r,source_verified=ap),'workflow_plan':kp.derive_workflow_plan(after,route=r,source_verified=ap)}], 'contexts_checked':1,'route_fields_reviewed':len(CONTRACT['fields']),'existing_products_preserved':3,'product_grants_guaranteed':False,'raw_writes':False,'operator_writes':False,'issue_changes':False,'renew_fresh_until':False,'confidence_changed':False}
 return {'schema_version':1,'kind':'reviewed_overlay_conversion','review_id':spec['id'],'entries':[out],'status':'detached; exact six-layer preflight required'},report

def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
 manifest=build_manifest(spec,layers);overlay,report=convert(manifest,layers)
 _need(digest(manifest)==digest(prepared_manifest) and digest(overlay)==digest(prepared_overlay),'Complete prepared artifacts differ from current reviewed rebuild')
 return report
