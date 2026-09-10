"""Explicit source-ordered application instructions; no inferred stage order.

A registered workflow supplies complete ordered text and each step's own
source evidence. Provenance is matched to the exact route/product, never lent
from a sibling. An unreviewed legacy list is not an ordered procedure.
"""
from copy import deepcopy
import hashlib
import json

WORKFLOW_ID = "uk-standard-visitor-ordered-20260910"
STEPS = [{'after': [],
  'evidence': [{'quote': 'If you need a Standard Visitor visa, you must apply online before you travel to '
                         'the UK and attend an appointment at a visa application centre.',
                'source_id': 'apply',
                'source_url': 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'}],
  'id': 'apply_online',
  'text': 'Apply for a Standard Visitor visa online before travelling to the UK.'},
 {'after': ['apply_online'],
  'evidence': [{'quote': 'You must apply for your visa before you can make an appointment at a visa '
                         'application centre.',
                'source_id': 'vac',
                'source_url': 'https://www.gov.uk/find-a-visa-application-centre'},
               {'quote': 'As part of your online application, you need to book an appointment at a visa '
                         'application centre. Allow time to attend your appointment, as the visa application '
                         'centre could be in another country.',
                'source_id': 'apply',
                'source_url': 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'}],
  'id': 'book_appointment',
  'text': 'Book a visa application centre appointment after applying online. Allow time to attend, as the '
          'centre could be in another country.'},
 {'after': ['book_appointment'],
  'evidence': [{'quote': 'At your appointment, you’ll need to: prove your identity with your passport or '
                         'travel document have your fingerprints and a photo (biometric information) taken '
                         'provide the required documents that show you’re eligible for a Standard Visitor '
                         'visa',
                'source_id': 'apply',
                'source_url': 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'},
               {'quote': 'You must provide certified translations of any documents that are not in English '
                         'or Welsh.',
                'source_id': 'apply',
                'source_url': 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'}],
  'id': 'attend_appointment',
  'text': 'Attend the appointment to prove your identity with your passport or travel document, provide the '
          'required biometrics and supporting documents. Provide certified translations of documents that '
          'are not in English or Welsh.'},
 {'after': ['attend_appointment'],
  'evidence': [{'quote': 'You’ll get an email when the Home Office has made a decision on your application . '
                         'This will explain what you need to do next.',
                'source_id': 'apply',
                'source_url': 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa'}],
  'id': 'receive_decision',
  'text': 'Check the Home Office decision email and follow its instructions on what to do next.'}]
SUBJECTS = {'AUS': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'AUS',
                   'passport_nationality': 'AUS',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'CAN': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'CAN',
                   'passport_nationality': 'CAN',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'ESP': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'ESP',
                   'passport_nationality': 'ESP',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'FRA': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'FRA',
                   'passport_nationality': 'FRA',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'HKG': {'default': False,
         'products': [],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'HKG',
                   'passport_nationality': 'HKG',
                   'transit_countries': None,
                   'travel_document_type': 'ordinary_passport',
                   'travel_purpose': 'tourism',
                   'visa_category': None}},
 'IDN': {'default': True,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'IDN',
                   'passport_nationality': 'IDN',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'IND': {'default': True,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'IND',
                   'passport_nationality': 'IND',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'JPN': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'JPN',
                   'passport_nationality': 'JPN',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'KOR': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'KOR',
                   'passport_nationality': 'KOR',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'MYS': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'MYS',
                   'passport_nationality': 'MYS',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'PHL': {'default': True,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'PHL',
                   'passport_nationality': 'PHL',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'RUS': {'default': True,
         'products': ['Standard Visitor - short, up to 6 months',
                      'Standard Visitor - long, up to 2 years',
                      'Standard Visitor - long, up to 5 years',
                      'Standard Visitor - long, up to 10 years'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'RUS',
                   'passport_nationality': 'RUS',
                   'transit_countries': None,
                   'travel_document_type': 'ordinary_passport',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'SGP': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'SGP',
                   'passport_nationality': 'SGP',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'THA': {'default': True,
         'products': ['Standard Visitor - short, up to 6 months',
                      'Standard Visitor - long, up to 2 years',
                      'Standard Visitor - long, up to 5 years',
                      'Standard Visitor - long, up to 10 years'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'THA',
                   'passport_nationality': 'THA',
                   'transit_countries': None,
                   'travel_document_type': 'ordinary_passport',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'TWN': {'default': False,
         'products': ['Standard Visitor visa (6 months)',
                      'Long-term Standard Visitor (2 years)',
                      'Long-term Standard Visitor (5 years)',
                      'Long-term Standard Visitor (10 years)'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'TWN',
                   'passport_nationality': 'TWN',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'USA': {'default': False,
         'products': [],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'USA',
                   'passport_nationality': 'USA',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}},
 'VNM': {'default': True,
         'products': ['Standard Visitor - short, up to 6 months',
                      'Standard Visitor - long, up to 2 years',
                      'Standard Visitor - long, up to 5 years',
                      'Standard Visitor - long, up to 10 years'],
         'route': {'arrival_date': None,
                   'consular_jurisdiction': None,
                   'destination_country': 'GBR',
                   'lawful_country_of_residence': 'VNM',
                   'passport_nationality': 'VNM',
                   'transit_countries': None,
                   'travel_document_type': 'ordinary_passport',
                   'travel_purpose': 'tourism',
                   'visa_category': 'tourist_visa'}}}


def _digest(v):
    try:
        return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,
            separators=(',', ':'),allow_nan=False).encode()).hexdigest()
    except (TypeError,ValueError):
        return None


def field_proof(route, product=None):
    """The exact reviewed source contract, used by converter and reader."""
    from scripts.convert_reviewed_product_patch import _subject
    evidence=[e for step in STEPS for e in step['evidence']]
    return {'source_id':'apply','source_url':evidence[0]['source_url'],'quote':evidence[0]['quote'],
        'additional_quotes':[e['quote'] for e in evidence[1:] if e['source_id']=='apply'],
        'supporting_evidence':[deepcopy(e) for e in evidence if e['source_id']!='apply'],
        'verified_at':'2026-09-10','verified_by':'Ellis AI official-source field review','verifier':'ai',
        'status':'reviewed','subject':_subject(route,product),
        'note':'Reviewed order of the Standard Visitor application stages only. This proof does not review eligibility, fees, visa terms, other procedures or freshness.',
        'verification_scope':{'kind':'ordered_application_instructions_v1','workflow_id':WORKFLOW_ID,
            'steps':deepcopy(STEPS)}}


def route_in_scope(route, reviewed):
    """The reviewed sequence was bound to a verbatim copy of the stored route,
    whose None slots (no travel date, no consular district) and purpose-derived
    visa category do not describe the procedure. A live lookup carries a
    travel date, the purpose-derived category and, for some countries, a
    consular district, so those keys are compared by meaning: any travel date
    is fine, the category must be the one the purpose derives (or absent), a
    consular district must be absent or the default, and an empty transit
    list equals none. Every other reviewed key must match exactly."""
    from .kimi_primary import category_for_purpose
    for k,v in reviewed.items():
        got=route.get(k)
        if k=='arrival_date':
            continue
        if k=='consular_jurisdiction':
            if got not in (None,'default'):
                return False
            continue
        if k=='visa_category':
            allowed={None,v,category_for_purpose(route.get('travel_purpose'))}
            if got not in allowed:
                return False
            continue
        if k=='transit_countries':
            if got not in (None,[]):
                return False
            continue
        if got!=v:
            return False
    return True


def ordered_instructions(guidance, route=None, provenance=None, *, product=None):
    """Return a complete reviewed sequence, or [] without inventing one."""
    if not isinstance(guidance,dict) or not isinstance(route,dict):
        return []
    scope=SUBJECTS.get(route.get('passport_nationality'))
    if (not scope or not route_in_scope(route, scope['route'])
            or route.get('travel_document_type') not in (None, 'ordinary_passport')
            or route.get('transit_countries') not in (None, [])):
        return []
    if product is None:
        if (not scope['default'] or guidance.get('disposition')!='VISA_REQUIRED'
                or guidance.get('application_channel')!='online_portal'):
            return []
        if (not isinstance(provenance,dict) or not isinstance(provenance.get('fields'),list)
                or 'submission_process' not in provenance['fields']):
            return []
        fields=provenance.get('field_provenance')
        subject=guidance
    else:
        if (not isinstance(product,dict) or product.get('type') not in scope['products']
                or product.get('application_channel')!='online_portal'):
            return []
        fields=product.get('field_provenance');subject=product
    if not isinstance(fields,dict):
        return []
    proof=fields.get('submission_process')
    if _digest(proof)!=_digest(field_proof(route,product)):
        return []
    text=[s['text'] for s in STEPS]
    if subject.get('submission_process')!=text:
        return []
    return deepcopy(STEPS)


ETA_APP_CONTRACT = {'HKG': {'proofs': {'account_registration_steps': {'additional_quotes': ['To apply for an ETA using the '
                                                                         'Australian ETA app, you need '
                                                                         'to have:\n'
                                                                         'A mobile device with a camera '
                                                                         'which can take your photo and '
                                                                         'an image of the Passport data '
                                                                         'page.\n'
                                                                         'Near-field communication '
                                                                         '(NFC) enabled on your mobile '
                                                                         'device.\n'
                                                                         'Location services enabled on '
                                                                         'your mobile device.\n'
                                                                         'A valid email address.\n'
                                                                         'A valid payment method ready '
                                                                         'to pay the service fee.',
                                                                         'You can get help from a '
                                                                         'friend or family member to '
                                                                         'apply through the app but you '
                                                                         'must be physically with them '
                                                                         'when you apply.'],
                                                   'note': 'HKG ordinary-passport tourism; ETA601 '
                                                           'workflow only. Initial filing requires the '
                                                           'Australian ETA app. ImmiAccount is retained '
                                                           'only for a requested further-information '
                                                           'response or a different visa, never an '
                                                           'initial ETA alternative.',
                                                   'quote': 'Download the Australian ETA app for free '
                                                            'from the Apple Store (Apple) or Google '
                                                            'Play store (Android).',
                                                   'source_id': 'eta_howto_current',
                                                   'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo',
                                                   'status': 'reviewed',
                                                   'subject': {'destination_country': 'AUS',
                                                               'passport_nationality': 'HKG',
                                                               'travel_document_type': 'ordinary_passport',
                                                               'travel_purpose': 'tourism'},
                                                   'verification_scope': 'eta_workflow_fields_only',
                                                   'verified_at': '2026-09-10',
                                                   'verified_by': 'Ellis AI official-source field '
                                                                  'review',
                                                   'verifier': 'ai'},
                    'submission_process': {'additional_quotes': ['In most cases, we will tell you of '
                                                                 'the result of your ETA application '
                                                                 'immediately. Sometimes there will be '
                                                                 'a delay before you receive your '
                                                                 'notification. Check your emails, '
                                                                 'including your junk mail folder for a '
                                                                 'written notification from us.',
                                                                 'Your application might take longer to '
                                                                 'process if:\n'
                                                                 "you don't fill it in correctly\n"
                                                                 'we need more information from you\n'
                                                                 'your information is hard to verify.',
                                                                 'You will receive a letter detailing '
                                                                 'how to provide this information. You '
                                                                 'must:\n'
                                                                 'use the link in the letter to submit '
                                                                 'an online form directly in '
                                                                 'ImmiAccount\n'
                                                                 'answer all questions in the online '
                                                                 'form and attach the required '
                                                                 'supporting documentation\n'
                                                                 'complete and attach Form 1554: ETA '
                                                                 'Request for further processing to the '
                                                                 'online form.',
                                                                 'Do not submit further ETA '
                                                                 'applications through the Australian '
                                                                 'ETA app. You will receive the same '
                                                                 'results.'],
                                           'note': 'HKG ordinary-passport tourism; ETA601 workflow '
                                                   'only. Initial filing requires the Australian ETA '
                                                   'app. ImmiAccount is retained only for a requested '
                                                   'further-information response or a different visa, '
                                                   'never an initial ETA alternative.',
                                           'quote': 'scan your passport to pre-fill your name, date of '
                                                    'birth, passport number and other details\n'
                                                    'take a photo of yourself\n'
                                                    'answer some questions, including whether you have '
                                                    'any criminal convictions, any prior names and '
                                                    'contact details in Australia\n'
                                                    'pay the service fee\n'
                                                    'submit the application.',
                                           'source_id': 'eta_howto_current',
                                           'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo',
                                           'status': 'reviewed',
                                           'subject': {'destination_country': 'AUS',
                                                       'passport_nationality': 'HKG',
                                                       'travel_document_type': 'ordinary_passport',
                                                       'travel_purpose': 'tourism'},
                                           'verification_scope': 'eta_workflow_fields_only',
                                           'verified_at': '2026-09-10',
                                           'verified_by': 'Ellis AI official-source field review',
                                           'verifier': 'ai'}},
         'values': {'account_registration_steps': ['Download and open the official Australian ETA app.',
                                                   'Have your valid eligible passport ready and use a '
                                                   'mobile device with a camera, NFC and location '
                                                   'services enabled.',
                                                   'Have a valid email address and payment method '
                                                   'ready. You must be physically present if another '
                                                   'person helps you apply.'],
                    'submission_process': ['Scan your passport, take your photo, and answer the app '
                                           'questions, including criminal convictions, previous names '
                                           'and contact details in Australia.',
                                           'Check all application details and answers, pay the service '
                                           'fee and submit through the Australian ETA app.',
                                           'Check email, including junk mail, for the written decision. '
                                           'Results are immediate in most cases, but additional '
                                           'information or verification can delay the result.',
                                           'If Home Affairs sends a request for further information, '
                                           "follow the letter's ImmiAccount link, provide the requested "
                                           'documents and Form 1554. Do not lodge another ETA app '
                                           'application for the same request.']}},
 'USA': {'proofs': {'account_registration_steps': {'additional_quotes': ['To apply for an ETA using the '
                                                                         'Australian ETA app, you need '
                                                                         'to have:\n'
                                                                         'A mobile device with a camera '
                                                                         'which can take your photo and '
                                                                         'an image of the Passport data '
                                                                         'page.\n'
                                                                         'Near-field communication '
                                                                         '(NFC) enabled on your mobile '
                                                                         'device.\n'
                                                                         'Location services enabled on '
                                                                         'your mobile device.\n'
                                                                         'A valid email address.\n'
                                                                         'A valid payment method ready '
                                                                         'to pay the service fee.',
                                                                         'You can get help from a '
                                                                         'friend or family member to '
                                                                         'apply through the app but you '
                                                                         'must be physically with them '
                                                                         'when you apply.'],
                                                   'note': 'USA ordinary-passport tourism; ETA601 '
                                                           'workflow only. Initial filing requires the '
                                                           'Australian ETA app. ImmiAccount is retained '
                                                           'only for a requested further-information '
                                                           'response or a different visa, never an '
                                                           'initial ETA alternative.',
                                                   'quote': 'Download the Australian ETA app for free '
                                                            'from the Apple Store (Apple) or Google '
                                                            'Play store (Android).',
                                                   'source_id': 'eta_howto_current',
                                                   'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo',
                                                   'status': 'reviewed',
                                                   'subject': {'destination_country': 'AUS',
                                                               'passport_nationality': 'USA',
                                                               'travel_document_type': 'ordinary_passport',
                                                               'travel_purpose': 'tourism'},
                                                   'verification_scope': 'eta_workflow_fields_only',
                                                   'verified_at': '2026-09-10',
                                                   'verified_by': 'Ellis AI official-source field '
                                                                  'review',
                                                   'verifier': 'ai'},
                    'submission_process': {'additional_quotes': ['In most cases, we will tell you of '
                                                                 'the result of your ETA application '
                                                                 'immediately. Sometimes there will be '
                                                                 'a delay before you receive your '
                                                                 'notification. Check your emails, '
                                                                 'including your junk mail folder for a '
                                                                 'written notification from us.',
                                                                 'Your application might take longer to '
                                                                 'process if:\n'
                                                                 "you don't fill it in correctly\n"
                                                                 'we need more information from you\n'
                                                                 'your information is hard to verify.',
                                                                 'You will receive a letter detailing '
                                                                 'how to provide this information. You '
                                                                 'must:\n'
                                                                 'use the link in the letter to submit '
                                                                 'an online form directly in '
                                                                 'ImmiAccount\n'
                                                                 'answer all questions in the online '
                                                                 'form and attach the required '
                                                                 'supporting documentation\n'
                                                                 'complete and attach Form 1554: ETA '
                                                                 'Request for further processing to the '
                                                                 'online form.',
                                                                 'Do not submit further ETA '
                                                                 'applications through the Australian '
                                                                 'ETA app. You will receive the same '
                                                                 'results.'],
                                           'note': 'USA ordinary-passport tourism; ETA601 workflow '
                                                   'only. Initial filing requires the Australian ETA '
                                                   'app. ImmiAccount is retained only for a requested '
                                                   'further-information response or a different visa, '
                                                   'never an initial ETA alternative.',
                                           'quote': 'scan your passport to pre-fill your name, date of '
                                                    'birth, passport number and other details\n'
                                                    'take a photo of yourself\n'
                                                    'answer some questions, including whether you have '
                                                    'any criminal convictions, any prior names and '
                                                    'contact details in Australia\n'
                                                    'pay the service fee\n'
                                                    'submit the application.',
                                           'source_id': 'eta_howto_current',
                                           'source_url': 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/electronic-travel-authority-601#HowTo',
                                           'status': 'reviewed',
                                           'subject': {'destination_country': 'AUS',
                                                       'passport_nationality': 'USA',
                                                       'travel_document_type': 'ordinary_passport',
                                                       'travel_purpose': 'tourism'},
                                           'verification_scope': 'eta_workflow_fields_only',
                                           'verified_at': '2026-09-10',
                                           'verified_by': 'Ellis AI official-source field review',
                                           'verifier': 'ai'}},
         'values': {'account_registration_steps': ['Download and open the official Australian ETA app.',
                                                   'Have your valid eligible passport ready and use a '
                                                   'mobile device with a camera, NFC and location '
                                                   'services enabled.',
                                                   'Have a valid email address and payment method '
                                                   'ready. You must be physically present if another '
                                                   'person helps you apply.'],
                    'submission_process': ['Scan your passport, take your photo, and answer the app '
                                           'questions, including criminal convictions, previous names '
                                           'and contact details in Australia.',
                                           'Check all application details and answers, pay the service '
                                           'fee and submit through the Australian ETA app.',
                                           'Check email, including junk mail, for the written decision. '
                                           'Results are immediate in most cases, but additional '
                                           'information or verification can delay the result.',
                                           'If Home Affairs sends a request for further information, '
                                           "follow the letter's ImmiAccount link, provide the requested "
                                           'documents and Form 1554. Do not lodge another ETA app '
                                           'application for the same request.']}}}


def reviewed_eta_steps(guidance, route=None, provenance=None):
    """Preserve the previously reviewed two ETA app workflows and qualifiers."""
    if not all(isinstance(x,dict) for x in (guidance,route,provenance)):
        return []
    c=ETA_APP_CONTRACT.get(route.get('passport_nationality'))
    if (not c or route.get('destination_country')!='AUS'
            or route.get('travel_purpose')!='tourism'
            or route.get('travel_document_type') not in (None,'ordinary_passport')
            or route.get('lawful_country_of_residence')!=route.get('passport_nationality')
            or route.get('consular_jurisdiction') not in (None,'')):
        return []
    own=provenance.get('field_provenance');fields=provenance.get('fields')
    if not isinstance(own,dict) or not isinstance(fields,list):return []
    for field,value in c['values'].items():
        if (field not in fields or guidance.get(field)!=value
                or _digest(own.get(field))!=_digest(c['proofs'][field])):
            return []
    return deepcopy(c['values']['account_registration_steps']+c['values']['submission_process'])


def reference_source(provenance):
    """A source link is reference metadata, never proof of a procedure."""
    from .evidence_validator import source_is_official
    if not isinstance(provenance,dict):return None
    fields=provenance.get('field_provenance')
    if not isinstance(fields,dict):return None
    for field in ('application_channel_detail','official_portal_url'):
        own=fields.get(field)
        if not isinstance(own,dict):continue
        url=own.get('source_url')
        if isinstance(url,str) and source_is_official(url):return url
    return None
