"""Two-route ETA601 workflow-only corrections with exact six-layer CAS.

Pure preparation/conversion; no store, issue, product or freshness writes.
The initial ETA app route and later requested ImmiAccount response are kept
separate. Existing product and verdict proof owners remain unchanged.
"""
from copy import deepcopy
from datetime import date
import hashlib
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.convert_reviewed_product_patch import field_provenance, _subject
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

CASE_KEYS = {'USA|USA|AUS|tourism|default|unknown|v6', 'HKG|HKG|AUS|tourism|default|unknown|v6'}

ROUTES = {'HKG|HKG|AUS|tourism|default|unknown|v6': {'arrival_date': None,
                                            'consular_jurisdiction': None,
                                            'destination_country': 'AUS',
                                            'lawful_country_of_residence': 'HKG',
                                            'passport_nationality': 'HKG',
                                            'transit_countries': None,
                                            'travel_document_type': 'ordinary_passport',
                                            'travel_purpose': 'tourism',
                                            'visa_category': 'tourist_visa'},
 'USA|USA|AUS|tourism|default|unknown|v6': {'arrival_date': None,
                                            'consular_jurisdiction': None,
                                            'destination_country': 'AUS',
                                            'lawful_country_of_residence': 'USA',
                                            'passport_nationality': 'USA',
                                            'transit_countries': None,
                                            'travel_document_type': 'ordinary_passport',
                                            'travel_purpose': 'tourism',
                                            'visa_category': 'tourist_visa'}}

VALUES = {'account_registration_steps': ['Download and open the official Australian ETA app.',
                                'Have your valid eligible passport ready and use a mobile device with a '
                                'camera, NFC and location services enabled.',
                                'Have a valid email address and payment method ready. You must be '
                                'physically present if another person helps you apply.'],
 'payment_process': ['Pay the mandatory AUD20 Australian ETA app service fee in the app before '
                     'submitting. There is no separate Visa Application Charge.'],
 'photo_requirements': 'A live facial image must be taken through the Australian ETA app. Have your '
                       'passport available to scan; be physically present if someone applies on your '
                       'behalf.',
 'submission_process': ['Scan your passport, take your photo, and answer the app questions, including '
                        'criminal convictions, previous names and contact details in Australia.',
                        'Check all application details and answers, pay the service fee and submit '
                        'through the Australian ETA app.',
                        'Check email, including junk mail, for the written decision. Results are '
                        'immediate in most cases, but additional information or verification can delay '
                        'the result.',
                        "If Home Affairs sends a request for further information, follow the letter's "
                        'ImmiAccount link, provide the requested documents and Form 1554. Do not lodge '
                        'another ETA app application for the same request.']}

EVIDENCE = {'account_registration_steps': [{'quote': 'Download the Australian ETA app for free from the Apple '
                                          'Store (Apple) or Google Play store (Android).',
                                 'quote_sha256': 'd6dbf49307cb708335fa1cc097ffdae73e8b128a081439c6f2255d9e14a13efc',
                                 'source_id': 'eta_howto_current',
                                 'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                                {'quote': 'To apply for an ETA using the Australian ETA app, you need '
                                          'to have:\n'
                                          'A mobile device with a camera which can take your photo and '
                                          'an image of the Passport data page.\n'
                                          'Near-field communication (NFC) enabled on your mobile '
                                          'device.\n'
                                          'Location services enabled on your mobile device.\n'
                                          'A valid email address.\n'
                                          'A valid payment method ready to pay the service fee.',
                                 'quote_sha256': '93b6430b2c4c2c3feb9a1e8a3700b74f46678488032388e273b19b48a3a64bb7',
                                 'source_id': 'eta_howto_current',
                                 'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                                {'quote': 'You can get help from a friend or family member to apply '
                                          'through the app but you must be physically with them when '
                                          'you apply.',
                                 'quote_sha256': '99b7726aa6172f3aadecea3b2e174c2ee02fcd3da346905f22a682cb76b7e18b',
                                 'source_id': 'eta_howto_current',
                                 'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'}],
 'payment_process': [{'quote': 'There is no Visa Application Charge (VAC) however there is an '
                               'application service fee of AUD20 to use the Australian ETA app.',
                      'quote_sha256': 'dccfd31cb5eefbc7ea7ae88dc7cb141e9a94ea582f1375b768ce71c763e39613',
                      'source_id': 'eta_about_current',
                      'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#About'},
                     {'quote': 'pay the service fee\nsubmit the application.',
                      'quote_sha256': 'fbafc73aa68252461fa61cf5f3bc7986fb461045938f9554714bd49b9734461d',
                      'source_id': 'eta_howto_current',
                      'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'}],
 'photo_requirements': [{'quote': 'Travel industry representatives can use the Australian ETA app to '
                                  'help you apply. They must take a live facial image of you when they '
                                  'lodge the application. We will not grant you a visa if you do not '
                                  'take a live image.',
                         'quote_sha256': 'ff6a5bd07c130c1cec4bc9c13423ac6e416130efbb5f86f51522f06afd7f1dc4',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                        {'quote': 'If an agent applies on your behalf, you must still be physically '
                                  'present when they make the application. You will need to take your '
                                  'photo and have your passport available to scan.',
                         'quote_sha256': 'a242387a390a046b143f96e42a0ca4adfab997cba2b3dfe5c6254fa674cdc9bc',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'}],
 'submission_process': [{'quote': 'scan your passport to pre-fill your name, date of birth, passport '
                                  'number and other details\n'
                                  'take a photo of yourself\n'
                                  'answer some questions, including whether you have any criminal '
                                  'convictions, any prior names and contact details in Australia\n'
                                  'pay the service fee\n'
                                  'submit the application.',
                         'quote_sha256': 'c416de16dcd3f29ed05608e2cd3c10b4fca6f484260d6e1eec766948a246604d',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                        {'quote': 'In most cases, we will tell you of the result of your ETA '
                                  'application immediately. Sometimes there will be a delay before you '
                                  'receive your notification. Check your emails, including your junk '
                                  'mail folder for a written notification from us.',
                         'quote_sha256': '5d886fd9297f5c33a14210addd4b39c67c91c4216d8224001e7f0536acebfbf8',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                        {'quote': 'Your application might take longer to process if:\n'
                                  "you don't fill it in correctly\n"
                                  'we need more information from you\n'
                                  'your information is hard to verify.',
                         'quote_sha256': '2c996ba9f469b93db3c392a8a80d2f13658ecb4799ded8324a7350f95ccaeebc',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                        {'quote': 'You will receive a letter detailing how to provide this information. '
                                  'You must:\n'
                                  'use the link in the letter to submit an online form directly in '
                                  'ImmiAccount\n'
                                  'answer all questions in the online form and attach the required '
                                  'supporting documentation\n'
                                  'complete and attach Form 1554: ETA Request for further processing to '
                                  'the online form.',
                         'quote_sha256': '6716ac4926feaeaae21ac914d5dbed58f6df9287f953c5638b43f71d2095a122',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
                        {'quote': 'Do not submit further ETA applications through the Australian ETA '
                                  'app. You will receive the same results.',
                         'quote_sha256': '373cdabe3f520dfe2333e25cc1ae1d6d6666c30c5bd475b716f406aa8dd6c0f1',
                         'source_id': 'eta_howto_current',
                         'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'}]}

SOURCES = {'eta_about_current': {'checked_at': '2026-09-10',
                       'sha256': 'd52c703fded9c080c1c1cfe29b866d2028cd63f5dbc2505f4a3a1c365176d32a',
                       'url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#About'},
 'eta_eligibility_current': {'checked_at': '2026-09-10',
                             'sha256': '1f9f2343011cdfdee4ff08badff5b8289f0a8cb9be05b9c81b0e1cf516c5ff48',
                             'url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#Eligibility'},
 'eta_howto_current': {'checked_at': '2026-09-10',
                       'sha256': '821d997aa6f8be9ee033b6a4ac8cb282dabeeb14d8adf97876c1b5715cea94fb',
                       'url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo'},
 'eta_overview_current': {'checked_at': '2026-09-10',
                          'sha256': '9cb15fab8ec2c19493f6f01f08fc971531ecee79d6db56eb24c7a52357e1dd8f',
                          'url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601'}}

FIELDS=frozenset(VALUES)


def _validate(spec,layer):
    from scripts.convert_reviewed_general_batch import _check_proof
    if (set(spec)!={'schema_version','kind','status','id','cache_key','route','captured_at','baseline_sha256','changes','preserve','eligibility_evidence','sources'}
        or spec['schema_version']!=1 or spec['kind']!='detached_eta_workflow_field_correction_specification'
        or not isinstance(spec['id'],str) or not spec['id'].strip()):
        raise PatchRejected('Malformed ETA workflow field specification')
    key=spec['cache_key'];route=spec['route']
    if key not in CASE_KEYS or route!=ROUTES[key] or route!=layer['route']:
        raise PatchRejected('Only these two exact ordinary-passport tourism routes were reviewed')
    if layer['operator_entries']:raise PatchRejected('Operator-owned routes need a separate reviewed integration contract')
    sources=_sources(spec)
    if set(sources)!=set(SOURCES) or any(any(sources[s].get(k)!=v for k,v in expected.items()) for s,expected in SOURCES.items()):
        raise PatchRejected('Current exact captured ETA source catalog changed')
    eligibility=sources['eta_eligibility_current']['text']
    member='Hong Kong (SAR of China)' if route['passport_nationality']=='HKG' else 'United States of America'
    if (member not in eligibility or 'You must hold a valid passport from one of these countries or jurisdictions to be eligible for an ETA.' not in eligibility
        or 'You cannot apply for an ETA with a non-citizen passport' not in eligibility):
        raise PatchRejected('Exact passport eligibility and document exclusions must remain captured')
    g=layer['merged_guidance'];products=g.get('visa_products')
    if (g.get('disposition')!='ELECTRONIC_AUTHORIZATION_REQUIRED' or not isinstance(products,list) or len(products)!=1
        or products[0].get('requirement_detail')!='eta_electronic_authorization'
        or not products[0].get('field_provenance',{}).get('disposition')):
        raise PatchRejected('Existing independently reviewed sole ETA product changed')
    changes=spec['changes']
    if not isinstance(changes,list) or len(changes)!=len(FIELDS) or {c.get('field') for c in changes}!=FIELDS:
        raise PatchRejected('Exactly the four reviewed ETA workflow fields are required')
    for change in changes:
        if set(change)!={'field','old_raw','old_merged','new','proof'}:raise PatchRejected('Unexpected field mutation')
        f=change['field'];proof=change['proof']
        if (change['old_raw']!=layer['raw_guidance'].get(f) or change['old_merged']!=g.get(f) or change['new']!=VALUES[f]):
            raise PatchRejected('ETA workflow old/new values differ from reviewed scope: '+f)
        if (not isinstance(proof,dict) or set(proof)!={'status','verifier','verified_at','subject','scope_note','evidence'}
            or proof['status']!='reviewed' or proof['verifier']!='ai' or proof['subject']!=_subject(route)
            or proof['evidence']!=EVIDENCE[f] or not isinstance(proof['scope_note'],str) or not proof['scope_note'].strip()):
            raise PatchRejected('Each ETA workflow field needs exact scoped AI evidence')
        for item in proof['evidence']:
            if hashlib.sha256(item['quote'].encode()).hexdigest()!=item['quote_sha256']:
                raise PatchRejected('ETA literal quote hash changed')
        try:day=date.fromisoformat(proof['verified_at'])
        except (TypeError,ValueError) as exc:raise PatchRejected('Invalid field review date') from exc
        if day>date.today() or any(sources[e['source_id']]['checked_at']!=day.isoformat() for e in proof['evidence']):
            raise PatchRejected('Review date must match its actual current source capture')
        _check_proof(deepcopy(proof),sources,route,f,change['new'])


def build_manifest(specifications,layers):
    if not isinstance(specifications,list) or len(specifications)!=2 or {s.get('cache_key') for s in specifications}!=CASE_KEYS:
        raise PatchRejected('Exactly the reviewed Hong Kong and US ETA routes are required')
    current=_current_map(layers,CASE_KEYS);entries=[]
    for spec in sorted(specifications,key=lambda s:s['cache_key']):
        layer=current[spec['cache_key']]
        if any(k not in layer for k in BASELINE_KEYS):raise PatchRejected('Every baseline layer is required')
        baseline={k:deepcopy(layer[k]) for k in BASELINE_KEYS};hashes={k:digest(v) for k,v in baseline.items()}
        if spec['baseline_sha256']!=hashes:raise PatchRejected('Specification baseline changed')
        _validate(spec,layer)
        entries.append({'cache_key':spec['cache_key'],'specification':deepcopy(spec),'baseline':baseline,'baseline_sha256':hashes})
    return {'schema_version':1,'kind':'australia_eta_workflow_exact_layers','entries':entries}


def _entry(spec,layer):
    from app.visa_snapshot import verified_overrides as vo,tstation
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    _validate(spec,layer);route=layer['route'];key=vo._key(route['passport_nationality'],'AUS','tourism','ordinary_passport')
    prior=vo._parse_rows(layer['seed_entries'],{}).get(key)
    if not prior:raise PatchRejected('Existing reviewed verdict owner missing')
    original,checked=vo.merge_verified_fields(layer['raw_guidance'],prior['fields'],source_url=prior['source_url'])
    provenance=dict(prior['field_provenance'].get('disposition') or vo._provenance(prior),fields=sorted(checked),field_provenance=prior['field_provenance'])
    if original!=layer['merged_guidance'] or provenance!=layer['source_provenance']:
        raise PatchRejected('Current raw/seed/provenance layers cannot be reconstructed exactly')
    output=deepcopy(prior)
    output.update(route={'nationality':route['passport_nationality'],'destination':'AUS','travel_purpose':'tourism','travel_document_type':'ordinary_passport'},
                  review_id=spec['id'],partial_review=True,field_review_scope='eta_workflow_fields_only')
    for change in spec['changes']:
        f=change['field'];proof=field_provenance(change['proof'],route,f)
        proof['verification_scope']='eta_workflow_fields_only'
        output['fields'][f]=deepcopy(change['new']);output['field_provenance'][f]=vo._provenance(proof)
    parsed=vo._parse_rows([output],{}).get(key);expected=deepcopy(prior)
    expected['fields'].update(deepcopy(VALUES));expected['field_provenance'].update({f:output['field_provenance'][f] for f in FIELDS})
    if parsed!=expected:raise PatchRejected('Serving loader changed a fact/proof owner or review date')
    effective,checked_after=vo.merge_verified_fields(layer['raw_guidance'],parsed['fields'],source_url=parsed['source_url'])
    after_provenance=dict(provenance,fields=sorted(checked_after),field_provenance=parsed['field_provenance'])
    if ({k:v for k,v in original.items() if k not in FIELDS}!={k:v for k,v in effective.items() if k not in FIELDS}
        or set(checked_after)!=set(checked)|FIELDS):
        raise PatchRejected('A non-workflow fact or proof scope changed')
    if serve_time_invariants(original)!=serve_time_invariants(effective):raise PatchRejected('Integrity holds changed')
    before=tstation.records_for_route(route,original,provenance);after=tstation.records_for_route(route,effective,after_provenance)
    if before!=after:raise PatchRejected('Product/QC facts, grade, review dates or source proof changed')
    report={'cache_key':spec['cache_key'],'changed_fields':sorted(FIELDS),'products_unchanged':True,
            'projected_records_unchanged':True,'new_verdict_reviews':0,'new_product_reviews':0,'new_release':False,
            'new_grounded_check':False,'renew_fresh_until':False}
    return output,report,effective,after_provenance


def convert(manifest,current_layers):
    if not isinstance(manifest,dict) or set(manifest)!={'schema_version','kind','entries'} or manifest['schema_version']!=1 or manifest['kind']!='australia_eta_workflow_exact_layers':
        raise PatchRejected('Malformed ETA workflow manifest')
    entries=manifest['entries'];specs=[e['specification'] for e in entries]
    baselines=[dict(deepcopy(e['baseline']),cache_key=e['cache_key']) for e in entries]
    if build_manifest(specs,baselines)!=manifest:raise PatchRejected('Reviewed baseline contract was tampered')
    current=_current_map(current_layers,CASE_KEYS)
    # Check every route/layer before constructing any prepared output.
    for e in entries:
        for field in BASELINE_KEYS:
            if field not in current[e['cache_key']] or digest(current[e['cache_key']][field])!=e['baseline_sha256'][field]:
                raise PatchRejected('Current layer changed: '+e['cache_key']+' '+field)
    result=[_entry(e['specification'],current[e['cache_key']]) for e in entries]
    overlay={'schema_version':1,'kind':'reviewed_overlay_conversion','review_id':'australia-eta-workflow-20260910',
             'entries':[r[0] for r in result],'review_scope':'eta_workflow_fields_only','status':'detached; exact-layer preflight required'}
    return overlay,[r[1] for r in result],{e['cache_key']:{'guidance':r[2],'source_provenance':r[3]} for e,r in zip(entries,result,strict=True)}


def verify_prepared(manifest,current_layers,prepared_overlay):
    result=convert(manifest,current_layers)
    if result[0]!=prepared_overlay:raise PatchRejected('Complete prepared ETA workflow overlay differs from exact rebuild')
    return result
