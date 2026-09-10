"""Source-ordered New COVA workflow for the one reviewed USA tourist context.

A changed route/value/own proof yields no sequence; no raw procedure inference.
"""
from copy import deepcopy
import hashlib,json
ROUTE = {'arrival_date': None,
 'consular_jurisdiction': None,
 'destination_country': 'CHN',
 'lawful_country_of_residence': 'USA',
 'passport_nationality': 'USA',
 'transit_countries': None,
 'travel_document_type': 'ordinary_passport',
 'travel_purpose': 'tourism',
 'visa_category': None}
VALUES = {'application_channel': 'online_portal',
 'application_channel_detail': 'Apply through New COVA and select the Chinese embassy or consulate '
                               'responsible for your US residence. After online preliminary '
                               'approval shows “Passport to be submitted”, you or an agent may '
                               'submit the passport, printed barcode information page and required '
                               'originals at that post by walk-in. Routine personal attendance for '
                               'fingerprint collection is not required; a consular officer may '
                               'require an in-person interview or additional documents. The '
                               'Washington Embassy does not accept mailed visa applications.',
 'appointment_required': False,
 'biometrics_required': False,
 'official_portal_url': 'http://consular.mfa.gov.cn/VISA/',
 'route_workflow_type': 'embassy_submission',
 'submission_process': ['Register or sign in to New COVA and select the Chinese embassy or '
                        'consulate responsible for your US residence.',
                        'Complete the online form and upload the required materials for '
                        'preliminary review; follow requests for corrections or additional '
                        'documents.',
                        'When the status is “Passport to be submitted”, you or an agent may bring '
                        'the passport, printed barcode information page and required originals to '
                        'the competent post by walk-in. Attend an interview if the consular '
                        'officer requests one.',
                        'Follow the competent post’s collection and payment instructions. At '
                        'Washington, wait for both the estimated pickup date and “Passport to be '
                        'collected” status; the estimated date is not a guarantee.']}
PROOFS = {'application_channel': {'additional_quotes': ['II. Filling out the Form and Uploading Materials. Please '
                                               'refer to the consular jurisdiction of Chinese Embassy and '
                                               'Consulates-General in the United States ( click to view ) '
                                               'and select corresponding Embassy/Consulate-General for your '
                                               'submission.',
                                               'The Chinese Embassy and Consulates-General in the U.S. '
                                               'provide walk-in visa application services. After online '
                                               'preliminary review approved (with the application status of '
                                               '“Passport to be submitted” ), the applicant or an agent can '
                                               'go to the corresponding Embassy or Consulate-General to '
                                               'submit the passport, a printed copy of the application '
                                               'information page with the barcode, and certain original '
                                               'documents requiring submitted on-site.'],
                         'note': 'Initial New COVA filing is online; physical passport/original submission '
                                 'follows preliminary approval at the competent Chinese embassy/consulate in '
                                 'the US.',
                         'quote': 'I. Signing up and Logging into Your Account. Go to '
                                  'http://consular.mfa.gov.cn/VISA/ (click to redirect)',
                         'source_id': 'faq2025',
                         'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                         'status': 'reviewed',
                         'subject': {'destination_country': 'CHN',
                                     'passport_nationality': 'USA',
                                     'travel_document_type': 'ordinary_passport',
                                     'travel_purpose': 'tourism'},
                         'supporting_evidence': [],
                         'verification_scope': {'evidence': [{'quote': 'I. Signing up and Logging into Your '
                                                                       'Account. Go to '
                                                                       'http://consular.mfa.gov.cn/VISA/ '
                                                                       '(click to redirect)',
                                                              'source_id': 'faq2025',
                                                              'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                             {'quote': 'II. Filling out the Form and '
                                                                       'Uploading Materials. Please refer to '
                                                                       'the consular jurisdiction of Chinese '
                                                                       'Embassy and Consulates-General in '
                                                                       'the United States ( click to view ) '
                                                                       'and select corresponding '
                                                                       'Embassy/Consulate-General for your '
                                                                       'submission.',
                                                              'source_id': 'faq2025',
                                                              'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                             {'quote': 'The Chinese Embassy and '
                                                                       'Consulates-General in the U.S. '
                                                                       'provide walk-in visa application '
                                                                       'services. After online preliminary '
                                                                       'review approved (with the '
                                                                       'application status of “Passport to '
                                                                       'be submitted” ), the applicant or an '
                                                                       'agent can go to the corresponding '
                                                                       'Embassy or Consulate-General to '
                                                                       'submit the passport, a printed copy '
                                                                       'of the application information page '
                                                                       'with the barcode, and certain '
                                                                       'original documents requiring '
                                                                       'submitted on-site.',
                                                              'source_id': 'faq2025',
                                                              'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'}],
                                                'field': 'application_channel',
                                                'kind': 'usa_china_tourism_exact_fields_20260910'},
                         'verified_at': '2026-09-10',
                         'verified_by': 'Ellis AI official-source field review',
                         'verifier': 'ai'},
 'application_channel_detail': {'additional_quotes': ['II. Filling out the Form and Uploading Materials. '
                                                      'Please refer to the consular jurisdiction of Chinese '
                                                      'Embassy and Consulates-General in the United States ( '
                                                      'click to view ) and select corresponding '
                                                      'Embassy/Consulate-General for your submission.',
                                                      'The Chinese Embassy and Consulates-General in the '
                                                      'U.S. provide walk-in visa application services. After '
                                                      'online preliminary review approved (with the '
                                                      'application status of “Passport to be submitted” ), '
                                                      'the applicant or an agent can go to the corresponding '
                                                      'Embassy or Consulate-General to submit the passport, '
                                                      'a printed copy of the application information page '
                                                      'with the barcode, and certain original documents '
                                                      'requiring submitted on-site.',
                                                      'According to relevant regulations, applicants are not '
                                                      'required to visit in person for fingerprint '
                                                      'collection. However, based on the specifics of each '
                                                      'case, consular officers may require the applicant to '
                                                      'appear in person for an interview. Please follow the '
                                                      'instructions of the consular officers.',
                                                      'The Chinese Embassy does not provide mailing services '
                                                      'for visa application.'],
                                'note': 'US mission filing sequence; no mandatory agency or CVASC-only '
                                        'requirement. No-mail statement is explicitly Washington-only. '
                                        'Interview and further documents remain conditional.',
                                'quote': 'I. Signing up and Logging into Your Account. Go to '
                                         'http://consular.mfa.gov.cn/VISA/ (click to redirect)',
                                'source_id': 'faq2025',
                                'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                                'status': 'reviewed',
                                'subject': {'destination_country': 'CHN',
                                            'passport_nationality': 'USA',
                                            'travel_document_type': 'ordinary_passport',
                                            'travel_purpose': 'tourism'},
                                'supporting_evidence': [],
                                'verification_scope': {'evidence': [{'quote': 'I. Signing up and Logging '
                                                                              'into Your Account. Go to '
                                                                              'http://consular.mfa.gov.cn/VISA/ '
                                                                              '(click to redirect)',
                                                                     'source_id': 'faq2025',
                                                                     'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                                    {'quote': 'II. Filling out the Form and '
                                                                              'Uploading Materials. Please '
                                                                              'refer to the consular '
                                                                              'jurisdiction of Chinese '
                                                                              'Embassy and '
                                                                              'Consulates-General in the '
                                                                              'United States ( click to view '
                                                                              ') and select corresponding '
                                                                              'Embassy/Consulate-General for '
                                                                              'your submission.',
                                                                     'source_id': 'faq2025',
                                                                     'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                                    {'quote': 'The Chinese Embassy and '
                                                                              'Consulates-General in the '
                                                                              'U.S. provide walk-in visa '
                                                                              'application services. After '
                                                                              'online preliminary review '
                                                                              'approved (with the '
                                                                              'application status of '
                                                                              '“Passport to be submitted” ), '
                                                                              'the applicant or an agent can '
                                                                              'go to the corresponding '
                                                                              'Embassy or Consulate-General '
                                                                              'to submit the passport, a '
                                                                              'printed copy of the '
                                                                              'application information page '
                                                                              'with the barcode, and certain '
                                                                              'original documents requiring '
                                                                              'submitted on-site.',
                                                                     'source_id': 'faq2025',
                                                                     'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                                    {'quote': 'According to relevant '
                                                                              'regulations, applicants are '
                                                                              'not required to visit in '
                                                                              'person for fingerprint '
                                                                              'collection. However, based on '
                                                                              'the specifics of each case, '
                                                                              'consular officers may require '
                                                                              'the applicant to appear in '
                                                                              'person for an interview. '
                                                                              'Please follow the '
                                                                              'instructions of the consular '
                                                                              'officers.',
                                                                     'source_id': 'faq2025',
                                                                     'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                                    {'quote': 'The Chinese Embassy does not '
                                                                              'provide mailing services for '
                                                                              'visa application.',
                                                                     'source_id': 'faq2025',
                                                                     'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'}],
                                                       'field': 'application_channel_detail',
                                                       'kind': 'usa_china_tourism_exact_fields_20260910'},
                                'verified_at': '2026-09-10',
                                'verified_by': 'Ellis AI official-source field review',
                                'verifier': 'ai'},
 'appointment_required': {'additional_quotes': [],
                          'note': 'US embassy/consulates walk-in passport submission after online '
                                  'preliminary approval. Does not remove a case-specific interview request.',
                          'quote': 'The Chinese Embassy and Consulates-General in the U.S. provide walk-in '
                                   'visa application services. After online preliminary review approved '
                                   '(with the application status of “Passport to be submitted” ), the '
                                   'applicant or an agent can go to the corresponding Embassy or '
                                   'Consulate-General to submit the passport, a printed copy of the '
                                   'application information page with the barcode, and certain original '
                                   'documents requiring submitted on-site.',
                          'source_id': 'faq2025',
                          'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                          'status': 'reviewed',
                          'subject': {'destination_country': 'CHN',
                                      'passport_nationality': 'USA',
                                      'travel_document_type': 'ordinary_passport',
                                      'travel_purpose': 'tourism'},
                          'supporting_evidence': [],
                          'verification_scope': {'evidence': [{'quote': 'The Chinese Embassy and '
                                                                        'Consulates-General in the U.S. '
                                                                        'provide walk-in visa application '
                                                                        'services. After online preliminary '
                                                                        'review approved (with the '
                                                                        'application status of “Passport to '
                                                                        'be submitted” ), the applicant or '
                                                                        'an agent can go to the '
                                                                        'corresponding Embassy or '
                                                                        'Consulate-General to submit the '
                                                                        'passport, a printed copy of the '
                                                                        'application information page with '
                                                                        'the barcode, and certain original '
                                                                        'documents requiring submitted '
                                                                        'on-site.',
                                                               'source_id': 'faq2025',
                                                               'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'}],
                                                 'field': 'appointment_required',
                                                 'kind': 'usa_china_tourism_exact_fields_20260910'},
                          'verified_at': '2026-09-10',
                          'verified_by': 'Ellis AI official-source field review',
                          'verifier': 'ai'},
 'biometrics_required': {'additional_quotes': [],
                         'note': 'Routine personal attendance for visa-application fingerprint collection is '
                                 'not required under the US mission FAQ. This says nothing about border '
                                 'biometrics.',
                         'quote': 'According to relevant regulations, applicants are not required to visit '
                                  'in person for fingerprint collection. However, based on the specifics of '
                                  'each case, consular officers may require the applicant to appear in '
                                  'person for an interview. Please follow the instructions of the consular '
                                  'officers.',
                         'source_id': 'faq2025',
                         'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                         'status': 'reviewed',
                         'subject': {'destination_country': 'CHN',
                                     'passport_nationality': 'USA',
                                     'travel_document_type': 'ordinary_passport',
                                     'travel_purpose': 'tourism'},
                         'supporting_evidence': [],
                         'verification_scope': {'evidence': [{'quote': 'According to relevant regulations, '
                                                                       'applicants are not required to visit '
                                                                       'in person for fingerprint '
                                                                       'collection. However, based on the '
                                                                       'specifics of each case, consular '
                                                                       'officers may require the applicant '
                                                                       'to appear in person for an '
                                                                       'interview. Please follow the '
                                                                       'instructions of the consular '
                                                                       'officers.',
                                                              'source_id': 'faq2025',
                                                              'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'}],
                                                'field': 'biometrics_required',
                                                'kind': 'usa_china_tourism_exact_fields_20260910'},
                         'verified_at': '2026-09-10',
                         'verified_by': 'Ellis AI official-source field review',
                         'verifier': 'ai'},
 'official_portal_url': {'additional_quotes': [],
                         'note': 'Exact New COVA address linked by current Chinese mission instructions; a '
                                 'filing portal, not an electronic visa guarantee.',
                         'quote': 'I. Signing up and Logging into Your Account. Go to '
                                  'http://consular.mfa.gov.cn/VISA/ (click to redirect)',
                         'source_id': 'faq2025',
                         'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                         'status': 'reviewed',
                         'subject': {'destination_country': 'CHN',
                                     'passport_nationality': 'USA',
                                     'travel_document_type': 'ordinary_passport',
                                     'travel_purpose': 'tourism'},
                         'supporting_evidence': [],
                         'verification_scope': {'evidence': [{'quote': 'I. Signing up and Logging into Your '
                                                                       'Account. Go to '
                                                                       'http://consular.mfa.gov.cn/VISA/ '
                                                                       '(click to redirect)',
                                                              'source_id': 'faq2025',
                                                              'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'}],
                                                'field': 'official_portal_url',
                                                'kind': 'usa_china_tourism_exact_fields_20260910'},
                         'verified_at': '2026-09-10',
                         'verified_by': 'Ellis AI official-source field review',
                         'verifier': 'ai'},
 'route_workflow_type': {'additional_quotes': [],
                         'note': 'Online preliminary filing followed by competent embassy/consulate passport '
                                 'submission; avoids the obsolete CVASC workflow label.',
                         'quote': 'The Chinese Embassy and Consulates-General in the U.S. provide walk-in '
                                  'visa application services. After online preliminary review approved (with '
                                  'the application status of “Passport to be submitted” ), the applicant or '
                                  'an agent can go to the corresponding Embassy or Consulate-General to '
                                  'submit the passport, a printed copy of the application information page '
                                  'with the barcode, and certain original documents requiring submitted '
                                  'on-site.',
                         'source_id': 'faq2025',
                         'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                         'status': 'reviewed',
                         'subject': {'destination_country': 'CHN',
                                     'passport_nationality': 'USA',
                                     'travel_document_type': 'ordinary_passport',
                                     'travel_purpose': 'tourism'},
                         'supporting_evidence': [],
                         'verification_scope': {'evidence': [{'quote': 'The Chinese Embassy and '
                                                                       'Consulates-General in the U.S. '
                                                                       'provide walk-in visa application '
                                                                       'services. After online preliminary '
                                                                       'review approved (with the '
                                                                       'application status of “Passport to '
                                                                       'be submitted” ), the applicant or an '
                                                                       'agent can go to the corresponding '
                                                                       'Embassy or Consulate-General to '
                                                                       'submit the passport, a printed copy '
                                                                       'of the application information page '
                                                                       'with the barcode, and certain '
                                                                       'original documents requiring '
                                                                       'submitted on-site.',
                                                              'source_id': 'faq2025',
                                                              'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'}],
                                                'field': 'route_workflow_type',
                                                'kind': 'usa_china_tourism_exact_fields_20260910'},
                         'verified_at': '2026-09-10',
                         'verified_by': 'Ellis AI official-source field review',
                         'verifier': 'ai'},
 'submission_process': {'additional_quotes': ['II. Filling out the Form and Uploading Materials. Please '
                                              'refer to the consular jurisdiction of Chinese Embassy and '
                                              'Consulates-General in the United States ( click to view ) and '
                                              'select corresponding Embassy/Consulate-General for your '
                                              'submission.',
                                              'The Chinese Embassy and Consulates-General in the U.S. '
                                              'provide walk-in visa application services. After online '
                                              'preliminary review approved (with the application status of '
                                              '“Passport to be submitted” ), the applicant or an agent can '
                                              'go to the corresponding Embassy or Consulate-General to '
                                              'submit the passport, a printed copy of the application '
                                              'information page with the barcode, and certain original '
                                              'documents requiring submitted on-site.',
                                              'According to relevant regulations, applicants are not '
                                              'required to visit in person for fingerprint collection. '
                                              'However, based on the specifics of each case, consular '
                                              'officers may require the applicant to appear in person for an '
                                              'interview. Please follow the instructions of the consular '
                                              'officers.'],
                        'note': 'Reviewed ordered application stages. Washington-only collection qualifier '
                                'does not prescribe other consulates’ collection methods.',
                        'quote': 'I. Signing up and Logging into Your Account. Go to '
                                 'http://consular.mfa.gov.cn/VISA/ (click to redirect)',
                        'source_id': 'faq2025',
                        'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm',
                        'status': 'reviewed',
                        'subject': {'destination_country': 'CHN',
                                    'passport_nationality': 'USA',
                                    'travel_document_type': 'ordinary_passport',
                                    'travel_purpose': 'tourism'},
                        'supporting_evidence': [{'quote': 'Please modify and supplement your application '
                                                          "materials according to the Embassy or Consulate's "
                                                          'feedback to avoid delays in the review process '
                                                          'due to failure to provide supplementary materials '
                                                          'in a timely manner.',
                                                 'source_id': 'online2025',
                                                 'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202509/t20250920_11712388.htm'},
                                                {'quote': 'The pick-up form issued by the staff of the '
                                                          'Embassy will indicate the earliest estimated date '
                                                          'for pick-up. Please check your online application '
                                                          'status on or after that date. When the status '
                                                          'shows “Passport to be collected”',
                                                 'source_id': 'application2025',
                                                 'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202509/t20250920_11712385.htm'}],
                        'verification_scope': {'evidence': [{'quote': 'I. Signing up and Logging into Your '
                                                                      'Account. Go to '
                                                                      'http://consular.mfa.gov.cn/VISA/ '
                                                                      '(click to redirect)',
                                                             'source_id': 'faq2025',
                                                             'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                            {'quote': 'II. Filling out the Form and '
                                                                      'Uploading Materials. Please refer to '
                                                                      'the consular jurisdiction of Chinese '
                                                                      'Embassy and Consulates-General in the '
                                                                      'United States ( click to view ) and '
                                                                      'select corresponding '
                                                                      'Embassy/Consulate-General for your '
                                                                      'submission.',
                                                             'source_id': 'faq2025',
                                                             'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                            {'quote': 'Please modify and supplement your '
                                                                      'application materials according to '
                                                                      "the Embassy or Consulate's feedback "
                                                                      'to avoid delays in the review process '
                                                                      'due to failure to provide '
                                                                      'supplementary materials in a timely '
                                                                      'manner.',
                                                             'source_id': 'online2025',
                                                             'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202509/t20250920_11712388.htm'},
                                                            {'quote': 'The Chinese Embassy and '
                                                                      'Consulates-General in the U.S. '
                                                                      'provide walk-in visa application '
                                                                      'services. After online preliminary '
                                                                      'review approved (with the application '
                                                                      'status of “Passport to be submitted” '
                                                                      '), the applicant or an agent can go '
                                                                      'to the corresponding Embassy or '
                                                                      'Consulate-General to submit the '
                                                                      'passport, a printed copy of the '
                                                                      'application information page with the '
                                                                      'barcode, and certain original '
                                                                      'documents requiring submitted '
                                                                      'on-site.',
                                                             'source_id': 'faq2025',
                                                             'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                            {'quote': 'According to relevant regulations, '
                                                                      'applicants are not required to visit '
                                                                      'in person for fingerprint collection. '
                                                                      'However, based on the specifics of '
                                                                      'each case, consular officers may '
                                                                      'require the applicant to appear in '
                                                                      'person for an interview. Please '
                                                                      'follow the instructions of the '
                                                                      'consular officers.',
                                                             'source_id': 'faq2025',
                                                             'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202309/t20230919_11144908.htm'},
                                                            {'quote': 'The pick-up form issued by the staff '
                                                                      'of the Embassy will indicate the '
                                                                      'earliest estimated date for pick-up. '
                                                                      'Please check your online application '
                                                                      'status on or after that date. When '
                                                                      'the status shows “Passport to be '
                                                                      'collected”',
                                                             'source_id': 'application2025',
                                                             'source_url': 'https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/202509/t20250920_11712385.htm'}],
                                               'field': 'submission_process',
                                               'kind': 'usa_china_tourism_exact_fields_20260910'},
                        'verified_at': '2026-09-10',
                        'verified_by': 'Ellis AI official-source field review',
                        'verifier': 'ai'}}

def _digest(value):
 try:return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
 except (TypeError,ValueError):return None

def steps(guidance,route,provenance):
 if not all(isinstance(x,dict) for x in (guidance,route,provenance)):return []
 if any(route.get(k)!=v for k,v in ROUTE.items()):return []
 if guidance.get('disposition')!='VISA_REQUIRED' or guidance.get('requirement_detail')!='paper_visa':return []
 if not isinstance(provenance.get('fields'),list) or not set(VALUES)<=set(provenance['fields']):return []
 own=provenance.get('field_provenance')
 if not isinstance(own,dict):return []
 from .verified_overrides import _provenance
 for field,value in VALUES.items():
  if guidance.get(field)!=value or _digest(own.get(field))!=_digest(_provenance(PROOFS[field])):return []
 evidence=deepcopy(PROOFS['submission_process']['verification_scope']['evidence'])
 return [dict(id=f'new_cova_{i+1}',text=text,after=[] if i==0 else [f'new_cova_{i}'],evidence=deepcopy(evidence)) for i,text in enumerate(VALUES['submission_process'])]
