"""Exact-layer HKSAR/US ordinary-tourist Malaysia source review; detached only.

No database, issue, generation, freshness or confidence writes. Source excerpts
bind only listed fields. Unknown photo/biometric and strict typed passport rules
remain unknown, never Not published. A successful conversion is not a release.
"""
from copy import deepcopy
from datetime import date
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from scripts.convert_reviewed_malaysia_fields import (PASSPORT, FUNDS, TICKET, LODGING, INSURANCE, EXTENSION, EXTENSION_VALUE)

NATS = ('HKG', 'USA')
KEYS = {f'{n}|{n}|MYS|tourism|default|unknown|v6' for n in NATS}
HK_QUOTE = 'CITIZEN | TYPE OF DOCUMENT | VISA FREE PERIOD FOR VISIT NOT EXCEEDING HONG KONG | PASSPORT | 90 days'
US_QUOTE = 'American citizens do not require a visa to visit Malaysia for business or social reasons if their stay in Malaysia is 90 days or less.'
HK_PASSPORT = 'Passport MUST remain valid for more than 6 months from the entry date into Malaysia.'
MDAC_WINDOW = 'Please note that your trip must be within 3 days (including the date of submission)'
MDAC_RULE = 'Starting from 01 January 2024 it is MANDATORY to complete and submit MALAYSIA DIGITAL ARRIVAL CARD (MDAC) within 3 days before arrival in Malaysia, except for the specified categories.'
MDAC_EXEMPT = 'CATEGORIES EXEMPTED from filling the MDAC are: i. All citizens of Singapore; ii. Holders of Diplomatic and Official Passports; iii. Malaysian Permanent Residents and Malaysian Long Term Pass Holders; iv. Brunei Common Certificate of Identification (GCI); v. Holders of Brunei Malaysia Frequent Traveler Facility; vi. Thailand Border Pass Holder; and vii. Indonesian Cross Border Pass (PLB) holders;'
HK_NATIONALITY = 'Hong Kong and Macao passport holders are required to select “Hong Kong” or “Macao” as their nationality during MDAC registration to ensure the correct visa-free period is granted.'
MDAC_NOTE = ('For this ordinary-passport tourist route, complete MDAC unless you hold Malaysian permanent residence or a Malaysian long-term pass. Diplomatic and official passports have a separate exemption. The published MDAC exemption list does not contain a child-under-three exemption.')
UNKNOWN = {'photo_requirements': 'No standalone photo requirement for this visa-exempt MDAC route was established by the reviewed official sources. Visa-application photo instructions are not evidence for an exempt visit.',
           'biometrics_required': 'These sources do not settle whether this visitor must give biometrics at immigration. A visa exemption does not prove that border biometrics are unnecessary.',
           'passport_validity_requirement': 'The HKSAR-specific consulate instruction says more than six months from entry. The current typed rule cannot express a strict greater-than boundary; the exact sourced text is retained.'}
# Each captured excerpt is immutable; page update dates are not policy expiry.
SOURCE_PINS = {'entry_tourists': 'dc5ae0bb631a0b01fa29bfc4619a33ef9694d0b9614b66516d8cc2a93fda1d3d', 'extension': '057d45d9f1e3205b8f9b3281513e3aaffdce2357d23e0ffae57d2644777329cc', 'insurance': '35df461da4746ec2fc9a2595e5e2f79c3e078f2f3b2bbf3e7acc7faad2c0e035', 'entry_pdf': '9ddd16a758fa5a249e57e9ff107370e0397daae3ced86d8a3a49fb116536fddb', 'hkg_exemption': '421c97da33154807318af43b62af15b2f9861dc6477a41d6bd1e2aaff2942dd6', 'usa_exemption': '34430092efac2a29b42235824dde894a66e10234b2c03e28c22d80107d6eb10c', 'mdac_window': 'af2c627329dc8b64f65f5ba8d83b7323ba2d12d7eeb3936196a5ee5e3616e643', 'mdac_scope': 'd245749cf7fa274862e700c393e40bfe0254f20d5f8eccfd67e2116ad6fc2672'}
URLS = {'entry_tourists': 'https://www.motac.gov.my/en/pengumuman/entry-requirements-for-foreign-tourists-for-immigration-inspection-at-international-entry-points/', 'extension': 'https://www.imi.gov.my/index.php/en/main-services/pass/visitor-pass/social-visit-pass/short-term-social-visit-pass/', 'insurance': 'https://www.tourism.gov.my/media/view/malaysia-relaxes-covid-19-testing-rules-travel-insurance-for-inbound-travellers', 'entry_pdf': 'https://www.imi.gov.my/wp-content/uploads/2023/02/2023-CR-Portal-JIM-24022023-BI.pdf', 'hkg_exemption': 'https://www.kln.gov.my/web/chn_hong-kong/requirement_foreigner', 'usa_exemption': 'https://www.kln.gov.my/web/usa_los-angeles/post/visa-application-for-refugee-and-re-entry-permit-travel-document', 'mdac_window': 'https://imigresen-online.imi.gov.my/mdac/register', 'mdac_scope': 'https://www.kln.gov.my/web/sgp_singapore/requirement_foreigner'}

def values(nat, layer):
    passport = 'Passport valid for more than 6 months from entry into Malaysia' if nat == 'HKG' else 'Passport valid for six months or more from entry into Malaysia'
    exceptions = ([layer['merged_guidance']['exceptions'][0], MDAC_NOTE, 'HKSAR passport holders must select Hong Kong as their nationality when completing MDAC.'] if nat == 'HKG' else [EXTENSION_VALUE, layer['merged_guidance']['exceptions'][1], MDAC_NOTE])
    result = dict(disposition='VISA_EXEMPT', requirement_detail='unconditional_visa_free', permitted_stay='90 days', permitted_stay_days=90,
        source_url=URLS['hkg_exemption' if nat == 'HKG' else 'usa_exemption'],
        passport_validity='More than 6 months from entry into Malaysia' if nat == 'HKG' else '6 months beyond date of entry',
        required_documents=[passport, 'Valid return ticket or onward ticket to the next destination country', 'Proof of sufficient funds for the stay', 'Proof of accommodation booking or confirmed lodging for the full stay', 'Completed Malaysia Digital Arrival Card (MDAC), unless covered by an official exemption'],
        onward_travel_evidence='A valid return ticket to the home country or an onward ticket to the next destination country is required.',
        accommodation_evidence='Proof of accommodation booking or confirmed lodging arrangements for the full stay is required.',
        financial_evidence='Proof of sufficient funds for the stay is required.', insurance_required=False,
        arrival_card={'required':True, 'name':'Malaysia Digital Arrival Card (MDAC)', 'submission_window':'Submit before arrival; your arrival must be within 3 days including the date of submission.'},
        application_channel_detail='No visa application is required for a tourism visit of up to 90 days. Complete MDAC before arrival unless covered by an official MDAC exemption; arrival must be within 3 days including submission day.',
        exceptions=exceptions, photo_requirements=None, biometrics_required=None)
    if nat == 'HKG': result['passport_validity_requirement'] = None
    return result

def evidence(nat, field):
    sid = 'hkg_exemption' if nat == 'HKG' else 'usa_exemption'; verdict = (sid, HK_QUOTE if nat == 'HKG' else US_QUOTE)
    scope = [('mdac_scope', MDAC_RULE), ('mdac_scope', MDAC_EXEMPT)]
    mapping = {**{f:[verdict] for f in ('disposition','requirement_detail','permitted_stay','permitted_stay_days','source_url')},
       'passport_validity': [('hkg_exemption',HK_PASSPORT)] if nat == 'HKG' else [('entry_pdf',PASSPORT)],
       'required_documents': ([('hkg_exemption',HK_PASSPORT)] if nat == 'HKG' else [('entry_pdf',PASSPORT)]) + [('entry_pdf',FUNDS),('entry_tourists',TICKET),('entry_tourists',LODGING)] + scope,
       'onward_travel_evidence':[('entry_tourists',TICKET)], 'accommodation_evidence':[('entry_tourists',LODGING)],
       'financial_evidence':[('entry_pdf',FUNDS)], 'insurance_required':[('insurance',INSURANCE)],
       'arrival_card':scope+[('mdac_window',MDAC_WINDOW)], 'application_channel_detail':[verdict]+scope+[('mdac_window',MDAC_WINDOW)],
       'exceptions':scope+([('hkg_exemption',HK_NATIONALITY)] if nat=='HKG' else [('extension',EXTENSION)])}
    return mapping.get(field, [])

def proof(nat, field, layer):
    if field in UNKNOWN:
        return dict(status='unknown',verifier='ai',reason=UNKNOWN[field],subject=_subject(layer['route']))
    p = dict(status='reviewed',verifier='ai',verified_at='2026-09-10',subject=_subject(layer['route']),
         scope_note='AI review of this exact HKSAR or US ordinary-passport tourism field only. No source-wide grade, product, expiry or cache-freshness certification.',
         evidence=[dict(source_id=sid,source_url=URLS[sid],quote=q) for sid,q in evidence(nat,field)])
    if field=='exceptions':
        vals=values(nat,layer)['exceptions']; retained = vals[0] if nat=='HKG' else vals[1]
        p.update(verified_elements=[v for v in vals if v!=retained],retained_unverified_elements=[retained])
    return p

def _validate(spec, layers):
    if set(spec)!= {'schema_version','kind','id','sources','routes'} or spec['schema_version']!=1 or spec['kind']!='reviewed_major_malaysia_fields' or spec['id']!='major-malaysia-fields-20260910':
        raise PatchRejected('Unexpected Malaysia review specification')
    if date.today()<date(2026,9,10): raise PatchRejected('Source review is in the future')
    sources=_sources(spec)
    if set(sources)!=set(SOURCE_PINS) or any(sources[s]['sha256']!=SOURCE_PINS[s] or sources[s]['url']!=URLS[s] or sources[s].get('checked_at')!='2026-09-10' for s in sources):
        raise PatchRejected('Source capture is not the pinned reviewed excerpt')
    current=_current_map(layers,KEYS)
    if len(spec['routes'])!=2 or {r.get('cache_key') for r in spec['routes']}!=KEYS:
        raise PatchRejected('Exactly the two reviewed routes are required')
    from scripts.convert_reviewed_general_batch import _check_proof
    for r in spec['routes']:
        if set(r)!= {'cache_key','route','baseline_sha256','changes'}: raise PatchRejected('Extra route instruction')
        l=current[r['cache_key']]; nat=r['cache_key'].split('|')[0]
        expected=dict(passport_nationality=nat,lawful_country_of_residence=nat,destination_country='MYS',visa_category='tourist_visa',travel_purpose='tourism',arrival_date=None,consular_jurisdiction=None,travel_document_type='ordinary_passport',transit_countries=None)
        # This legacy default USA key predates explicit document/transit keys.
        # Bind the exact observed omission, not an arbitrary document wildcard.
        if nat == 'USA':
            expected.pop('travel_document_type'); expected.pop('transit_countries')
        if r['route']!=expected or l['route']!=expected or any(k not in l for k in BASELINE_KEYS) or r['baseline_sha256']!={k:digest(l[k]) for k in BASELINE_KEYS}:
            raise PatchRejected('Exact six-layer baseline or passport scope differs')
        if l['operator_entries'] or l['merged_guidance'].get('visa_products') not in (None,[]): raise PatchRejected('Existing operator or visa product scope cannot be replaced')
        expected_raw_products = [] if nat == 'HKG' else [{'type':'Visa-exempt tourist entry','entry':None,'validity':'90 days per entry','max_stay_days':90,'fee':{'amount':None,'currency':None},'notes':'No advance visa required for ordinary U.S. passport holders'}]
        if l['raw_guidance'].get('visa_products') != expected_raw_products: raise PatchRejected('Raw product set is not the reviewed legacy baseline')
        g=l['merged_guidance']
        if (g.get('disposition'),g.get('requirement_detail'),g.get('permitted_stay'),g.get('permitted_stay_days'))!=('VISA_EXEMPT','unconditional_visa_free','90 days',90): raise PatchRejected('Baseline verdict/stay changed')
        expected_changes=[dict(field=f,old_raw=l['raw_guidance'].get(f),old_merged=g.get(f),new=v,proof=proof(nat,f,l)) for f,v in values(nat,l).items()]
        if digest(r['changes'])!=digest(expected_changes): raise PatchRejected('Changed value/proof scope is not the reviewed correction')
        for c in r['changes']:
            _check_proof(deepcopy(c['proof']),sources,expected,c['field'],c['new'])
    return current

def build_manifest(spec,layers):
    current=_validate(spec,layers)
    return dict(schema_version=1,kind='major_malaysia_exact_layers',specification=deepcopy(spec),routes=[dict(cache_key=k,baseline={f:deepcopy(current[k][f]) for f in BASELINE_KEYS}) for k in sorted(KEYS)])

def convert(manifest,layers):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    baseline=[dict(r['baseline'],cache_key=r['cache_key']) for r in manifest['routes']]
    if build_manifest(manifest['specification'],baseline)!=manifest: raise PatchRejected('Altered manifest')
    current=_validate(manifest['specification'],layers); entries=[]; previews=[]
    for r in manifest['specification']['routes']:
        l=current[r['cache_key']]; nat=r['route']['passport_nationality']; key=vo._key(nat,'MYS','tourism','ordinary_passport')
        prior=vo._parse_rows(l['seed_entries'],{}).get(key)
        if not prior: raise PatchRejected('Original MDAC source ownership missing')
        original,checked=vo.merge_verified_fields(l['raw_guidance'],prior['fields'],source_url=prior['source_url'])
        original_prov=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
        if original!=l['merged_guidance'] or original_prov!=l['source_provenance']: raise PatchRejected('Original merged source reconstruction changed')
        output=deepcopy(prior); output.update(route=dict(nationality=nat,destination='MYS',travel_purpose='tourism',travel_document_type='ordinary_passport'),partial_review=True,review_id=manifest['specification']['id'])
        for c in r['changes']:
            output['fields'][c['field']]=deepcopy(c['new']); output['field_provenance'][c['field']]=vo._provenance(field_provenance(c['proof'],r['route'],c['field']))
        verdict=output['field_provenance']['disposition']
        for f in ('source_url','verified_at','verified_by','verifier','note'): output[f]=verdict[f]
        parsed=vo._parse_rows([output],{}).get(key)
        expected_proofs=deepcopy(output['field_provenance'])
        for f,p in expected_proofs.items():
            if p.get('status') == 'unknown': p.pop('subject',None)  # existing loader's empty-fact proof shape
        if parsed is None or parsed['fields']!=output['fields'] or parsed['field_provenance']!=expected_proofs: raise PatchRejected('Loader altered reviewed data/proof')
        effective,fields=vo.merge_verified_fields(l['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
        prov=dict(verdict,fields=sorted(fields),field_provenance=parsed['field_provenance'])
        allowed=set(values(nat,l)); before_other={k:v for k,v in original.items() if k not in allowed}; after_other={k:v for k,v in effective.items() if k not in allowed}
        if before_other.get('forms') == [] and 'forms' not in after_other: before_other.pop('forms')
        if before_other!=after_other: raise PatchRejected('Unreviewed guidance changed')
        if set(serve_time_invariants(effective))-set(serve_time_invariants(original)): raise PatchRejected('Correction added a serving conflict')
        records=tstation.records_for_route(r['route'],effective,prov)
        if len(records)!=1 or effective.get('confidence')!=original.get('confidence'): raise PatchRejected('Product or confidence scope changed')
        entries.append(output); previews.append(dict(cache_key=r['cache_key'],guidance=effective,provenance=prov,records=records,changed_fields=sorted(f for f in allowed if original.get(f)!=effective.get(f)),fields_in_scope=sorted(allowed),verified_fields=sorted(allowed-set(UNKNOWN)),unresolved_fields=sorted(allowed&set(UNKNOWN))))
    return dict(schema_version=1,kind='reviewed_overlay_conversion',review_id=manifest['specification']['id'],entries=entries,status='detached; exact-layer preflight required'),dict(routes=previews,new_grounded_check=False,renew_fresh_until=False,new_release=False,raw_or_issue_writes=False)

def verify_prepared(spec,layers,prepared_manifest,prepared_overlay):
    manifest=build_manifest(spec,layers)
    overlay,report=convert(manifest,layers)
    if digest(manifest)!=digest(prepared_manifest) or digest(overlay)!=digest(prepared_overlay):
        raise PatchRejected('Prepared manifest or complete overlay differs from exact current rebuild')
    return report
