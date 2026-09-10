"""Prepare an absent route and its scoped overlay after exact baseline CAS.

Pure conversion only. The caller installs the reviewed registry/overlay and
then uses materialize_reviewed_routes in the same deployment cohort. No DB,
issue, history, cache expiry or grounded-check write occurs here.
"""
from copy import deepcopy
from datetime import date
import hashlib,json
from app.visa_snapshot import reviewed_social_authority as authority, kimi_primary as kp, verified_overrides as overrides
from scripts import materialize_reviewed_routes as materializer

BASELINE_FIELDS=('cache_key','route','raw_row_present','canonical_alias_keys','raw_guidance','raw_metadata',
                 'merged_guidance','source_provenance','seed_entries','operator_entries','prospective_empty_base_overlay_probe')

class CandidateRejected(ValueError): pass

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def build_manifest(materialization, layer):
    if not isinstance(layer,dict) or any(k not in layer for k in BASELINE_FIELDS): raise CandidateRejected('Incomplete absent-route baseline')
    candidate={'schema_version':1,'kind':'reviewed_social_new_route','id':materialization['id'],
               'cache_key':layer['cache_key'],'expected_layer_sha256':{k:digest(layer[k]) for k in BASELINE_FIELDS},
               'materialization':deepcopy(materialization)}
    convert(candidate,[layer])
    return candidate

def convert(manifest,current_layers):
    if manifest.get('schema_version')!=1 or manifest.get('kind')!='reviewed_social_new_route': raise CandidateRejected('Invalid manifest')
    candidates=[x for x in current_layers if x.get('cache_key')==manifest.get('cache_key')]
    if len(candidates)!=1:raise CandidateRejected('Expected exactly one current absent-key capture')
    layer=candidates[0];hashes=manifest.get('expected_layer_sha256')
    if not isinstance(hashes,dict) or set(hashes)!=set(BASELINE_FIELDS): raise CandidateRejected('Incomplete baseline hashes')
    for field in BASELINE_FIELDS:
        if field not in layer or digest(layer[field])!=hashes[field]: raise CandidateRejected('Baseline changed: '+field)
    if (layer['raw_row_present'] is not False or layer['canonical_alias_keys']!=[]
        or any(layer[k] is not None for k in ('raw_guidance','raw_metadata','merged_guidance','source_provenance'))):
        raise CandidateRejected('Canonical/legacy absence is not established')
    data=deepcopy(manifest['materialization']);entries=data.get('routes');sources={c['id']:c for c in data['sources']}
    if not isinstance(entries,list) or len(entries)!=1 or len(sources)!=len(data['sources']):raise CandidateRejected('One exact route required')
    e=entries[0];raw=e['route'];route={'passport_nationality':raw['nationality'],'passport_issuing_country':raw['nationality'],
         'destination_country':raw['destination'],'travel_document_type':raw['travel_document_type'],'travel_purpose':raw['travel_purpose']}
    if kp.cache_key(route)!=layer['cache_key'] or e.get('expected_absent') is not True:raise CandidateRejected('Wrong route or missing expected-absence contract')
    g=e['guidance'];proofs=e['field_provenance'];proof=proofs['disposition']
    if not authority.binding_for(proof,route,field='disposition',value=g['disposition']):raise CandidateRejected('No current reviewed authority binding')
    if e.get('policy_valid_from')!=proof.get('effective_from') or e.get('policy_valid_through') is not None:raise CandidateRejected('Policy start differs or unreviewed expiry introduced')
    if date.fromisoformat(e['policy_valid_from'])>date.today():raise CandidateRejected('Policy not yet effective')
    if overrides._field_errors(g) or kp.serve_time_invariants(g):raise CandidateRejected('Guidance violates structural invariants')
    for field,value in g.items():
        if field=='confidence' or value in materializer.UNKNOWN:continue
        materializer._proof(field,value,proofs.get(field),sources,route,date.today())
    materializer._proof('policy_valid_from',e['policy_valid_from'],proofs['policy_valid_from'],sources,route,date.today())
    entry=dict(deepcopy(proof),route=deepcopy(raw),verified_by='Ellis AI official embassy source review',
               fields=deepcopy(g),field_provenance=deepcopy(proofs))
    if not authority.entry_supported(entry):raise CandidateRejected('Overlay contains unreviewed facts or source metadata')
    overlay={'schema_version':1,'kind':'reviewed_overlay_conversion','manifest_id':manifest['id'],
             'baseline_sha256':deepcopy(hashes),'entries':[entry]}
    data['reviewed_social_cas']={'schema_version':1,'baseline':deepcopy(layer),
        'expected_layer_sha256':deepcopy(hashes),'installed_overlay_entry':deepcopy(entry)}
    return overlay,data,{'cache_key':layer['cache_key'],'canonical_row_absent':True,'legacy_aliases_absent':True,
           'existing_seed_entries':len(layer['seed_entries']),'existing_operator_entries':len(layer['operator_entries']),
           'reviewed_stay_days':g['permitted_stay_days'],'authority_owner':'IDN','authority_role':'origin_government_mission',
           'effective_from':e['policy_valid_from'],'policy_end_unknown':True,'facts_are_not_grounded_check':True,
           'requires_atomic_materialization_and_existing_issue_preservation':True}


def validate_materialization_cas(data, entries, db):
    """Recheck actual installed layers under the materializer's DB transaction.

    The reviewed candidate must be the exact final applicable seed entry; only
    that new entry is removed before comparing the preserved ordered baseline.
    This does not trust a detached preflight to authorize a later insertion.
    Returned file fingerprints are checked again before the DB commit.
    """
    from pathlib import Path
    bound=[e for e in entries if authority.binding_for(e['field_provenance'].get('disposition'),e['route'])]
    if not bound:return None
    contract=data.get('reviewed_social_cas')
    if len(bound)!=1 or len(entries)!=1 or not isinstance(contract,dict) or contract.get('schema_version')!=1:
        raise CandidateRejected('Social insertion requires the exact reviewed layer CAS contract')
    baseline=contract.get('baseline');hashes=contract.get('expected_layer_sha256')
    if not isinstance(baseline,dict) or not isinstance(hashes,dict) or set(hashes)!=set(BASELINE_FIELDS):
        raise CandidateRejected('Incomplete insertion baseline contract')
    for field in BASELINE_FIELDS:
        if field not in baseline or digest(baseline[field])!=hashes[field]:
            raise CandidateRejected('Insertion baseline tampered: '+field)
    if (baseline['raw_row_present'] is not False or baseline['canonical_alias_keys']!=[]
        or any(baseline[k] is not None for k in ('raw_guidance','raw_metadata','merged_guidance','source_provenance'))):
        raise CandidateRejected('Insertion was not reviewed against canonical absence')
    entry=bound[0];key=entry['cache_key'];candidate=contract.get('installed_overlay_entry')
    if key!=baseline['cache_key'] or kp.cache_key(baseline['route'])!=key:
        raise CandidateRejected('Insertion baseline belongs to another route')
    if any(kp.canonical_key(k)==key for (k,) in db.execute('select cache_key from kimi_route_guidance_cache')):
        raise CandidateRejected('Canonical or alias absence changed before insertion')
    if (not authority.entry_supported(candidate) or candidate['fields']!=entry['guidance']
        or candidate['field_provenance']!=entry['field_provenance']):
        raise CandidateRejected('Installed candidate differs from reviewed materialization')
    route=entry['route']
    scope=overrides._key(route['passport_nationality'],route['destination_country'],route['travel_purpose'],route['travel_document_type'])
    def scoped(rows):
        return [r for r in rows if overrides._key(r.get('route',{}).get('nationality'),
            r.get('route',{}).get('destination'),r.get('route',{}).get('travel_purpose','tourism'),
            r.get('route',{}).get('travel_document_type',''))==scope]
    # Read the exact files again, without relying on the serving table cache.
    errors=[];paths=[overrides.OVERRIDES,*overrides._reviewed_overlay_paths(),overrides.operator_overrides_path()]
    fingerprints={str(path):hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None for path in paths}
    seeds=overrides._read_verification_store(paths[0],'core_seed',required=True,errors=errors)
    for path in paths[1:-1]:
        seeds+=overrides._read_verification_store(path,'reviewed_overlay',reviewed=True,errors=errors)
    operators=overrides._read_verification_store(paths[-1],'operator_overrides',errors=errors)
    if errors:raise CandidateRejected('Current source stores are invalid: '+','.join(errors))
    seeds=scoped(seeds);operators=scoped(operators)
    if not seeds or seeds[-1]!=candidate or sum(r==candidate for r in seeds)!=1:
        raise CandidateRejected('Exact reviewed overlay must be installed once as final applicable seed')
    if digest(seeds[:-1])!=hashes['seed_entries'] or digest(operators)!=hashes['operator_entries']:
        raise CandidateRejected('Preserved seed or operator baseline changed before insertion')
    return {'fingerprints':fingerprints,'overlay_paths':[str(x) for x in paths[1:-1]],
            'authority_registry_sha256':hashlib.sha256(authority.REGISTRY_PATH.read_bytes()).hexdigest()}


def assert_materialization_files_unchanged(token):
    if token is None:return
    from pathlib import Path
    if [str(x) for x in overrides._reviewed_overlay_paths()]!=token['overlay_paths']:
        raise CandidateRejected('Reviewed overlay order changed during insertion')
    if hashlib.sha256(authority.REGISTRY_PATH.read_bytes()).hexdigest()!=token['authority_registry_sha256']:
        raise CandidateRejected('Official account registry changed during insertion')
    for name,expected in token['fingerprints'].items():
        path=Path(name);actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if actual!=expected:raise CandidateRejected('Source file changed during insertion: '+name)
