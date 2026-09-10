"""Exact delegated permit-application facts; CTS is not a government host.

Only the captured Hong Kong Chinese-resident permit instructions and the
three registered field/value/proof pairs are recognized. No visa eligibility,
source headline, policy date, or other CTS content gains authority here.
"""
from copy import deepcopy
import hashlib,json
def _same(a,b):
    return json.dumps(a,sort_keys=True,ensure_ascii=False,separators=(",",":"))==json.dumps(b,sort_keys=True,ensure_ascii=False,separators=(",",":"))
from ._reviewed_hkg_mainland_contract import CASE,DELEGATED_ID,DELEGATED_HASH,DELEGATED_PROOFS,SOURCES
SCOPE='hkg_mainland_permit_review'
FIELDS=frozenset(DELEGATED_PROOFS)

def _identity(route):
    return {k:route.get(k) for k in ('passport_nationality','destination_country','travel_purpose','travel_document_type')}

def _route_matches(route,*,serving=False):
    normalized=dict(route)
    normalized.setdefault('passport_nationality',normalized.get('nationality'))
    normalized.setdefault('destination_country',normalized.get('destination'))
    if _identity(normalized)!=_identity(CASE['route']):return False
    return not serving or normalized.get('lawful_country_of_residence')=='HKG'

def subject(route,product=None):
    out=_identity(CASE['route'])
    if product is not None:
        out.update(product_type=product['type'],disposition=product.get('disposition'),requirement_detail=product.get('requirement_detail'))
    return out

def expected_proof(field,product=None):
    result=deepcopy(DELEGATED_PROOFS[field]);result['subject']=subject(CASE['route'],product)
    return result

def field_supported(proof,route,field,value,*,product=None):
    if not _route_matches(route) or field not in FIELDS:return False
    if product is not None and (product.get('type') not in {p['current_name'] for p in CASE['products']}
            or product.get('disposition')!='CONDITIONAL' or product.get('requirement_detail')!='conditional_visa_free'):
        return False
    return (_same(value,CASE['changes'][field]['new']) and _same(proof,expected_proof(field,product)))

def _registered(p):
    return isinstance(p,dict) and (str(p.get('authority_binding_id') or '').startswith('hkg-mainland-')
                                  or p.get('source_url')==SOURCES['cts_pdf']['url'])

def _claims(fields,provenance):
    if isinstance(provenance,dict):
        if provenance.get('verification_scope')==SCOPE:return True
        fp=provenance.get('field_provenance')
        if isinstance(fp,dict) and any(_registered(p) for p in fp.values()):return True
    products=fields.get('visa_products') if isinstance(fields,dict) else None
    if not isinstance(products,list):return False
    return any(isinstance(p,dict) and isinstance(p.get('field_provenance'),dict)
               and any(_registered(q) for q in p['field_provenance'].values()) for p in products)

def guidance_errors(route,guidance,provenance,*,serving=True):
    if not isinstance(guidance,dict) or not _claims(guidance,provenance):return []
    if not _route_matches(route,serving=serving):return ['delegated_permit_application_scope_conflict']
    if not isinstance(provenance,dict) or not isinstance(provenance.get('field_provenance'),dict):
        return ['delegated_permit_application_proof_shape_conflict']
    proofs=provenance['field_provenance']
    for field in FIELDS:
        if not field_supported(proofs.get(field),route,field,guidance.get(field)):
            return ['delegated_permit_application_proof_conflict']
    products=guidance.get('visa_products')
    if not isinstance(products,list) or any(not isinstance(p,dict) for p in products):
        return ['delegated_permit_product_shape_conflict']
    if [p.get('type') for p in products]!=[p['current_name'] for p in CASE['products']]:
        return ['delegated_permit_product_set_conflict']
    for product in products:
        pp=product.get('field_provenance')
        if not isinstance(pp,dict):return ['delegated_permit_product_proof_shape_conflict']
        if product.get('type') in {p['current_name'] for p in CASE['products']} or any(_registered(p) for p in pp.values()):
            for field in FIELDS:
                if not field_supported(pp.get(field),route,field,product.get(field),product=product):
                    return ['delegated_permit_product_application_proof_conflict']
    return []

def entry_errors(entry):
    if not isinstance(entry,dict):return []
    fields=entry.get('fields') or {};p=entry.get('field_provenance') or {}
    if not isinstance(p,dict):return ['delegated_permit_entry_proof_shape_conflict']
    decision=p.get('disposition')
    provenance=dict(decision if isinstance(decision,dict) else {},field_provenance=p)
    return guidance_errors(entry.get('route') or {},fields,provenance,serving=False)
