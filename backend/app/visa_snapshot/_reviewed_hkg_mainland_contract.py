"""Closed, literal-source review contract; no platform or commercial-host allowlist."""
CASE = {'cache_key': 'HKG|HKG|CHN|tourism|default|unknown|v6',
 'route': {'passport_nationality': 'HKG',
           'lawful_country_of_residence': 'HKG',
           'destination_country': 'CHN',
           'visa_category': 'tourist_visa',
           'travel_purpose': 'tourism',
           'arrival_date': None,
           'consular_jurisdiction': None,
           'travel_document_type': 'ordinary_passport',
           'transit_countries': None},
 'changes': {'disposition': {'new': 'CONDITIONAL',
                             'evidence': [{'source_id': 'nia_regulation',
                                           'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                           'quote': '第四条 '
                                                    '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                          {'source_id': 'nia_guide',
                                           'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                           'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                          {'source_id': 'nia_guide',
                                           'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                           'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                          {'source_id': 'hksar_chengdu',
                                           'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                           'quote': 'Q10 I am a Hong Kong resident holding both the Hong '
                                                    'Kong and Macao Residents Entry and Exit Permit '
                                                    '(Mainland Travel Permit for Hong Kong and Macao '
                                                    'Residents) and the HKSAR Passport. If I take an '
                                                    'international direct flight to the Mainland, what kind '
                                                    'of documents should I use for my entry?\n'
                                                    'A10 You should use the Hong Kong and Macao Residents '
                                                    'Entry and Exit Permit (Mainland Travel Permit for Hong '
                                                    'Kong and Macao Residents) to enter and exit the '
                                                    'Mainland.'}],
                             'scope_note': 'AI review for a Chinese national holding an ordinary HKSAR '
                                           'passport, resident in Hong Kong, travelling to mainland China '
                                           'for tourism. Home Return Permit facts are separate from the '
                                           'non-Chinese resident permit and from a foreign-visitor visa.'},
             'requirement_detail': {'new': 'conditional_visa_free',
                                    'evidence': [{'source_id': 'nia_regulation',
                                                  'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                  'quote': '第四条 '
                                                           '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                 {'source_id': 'nia_guide',
                                                  'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                  'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                 {'source_id': 'nia_guide',
                                                  'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                  'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                 {'source_id': 'hksar_chengdu',
                                                  'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                  'quote': 'Q10 I am a Hong Kong resident holding both the '
                                                           'Hong Kong and Macao Residents Entry and Exit '
                                                           'Permit (Mainland Travel Permit for Hong Kong and '
                                                           'Macao Residents) and the HKSAR Passport. If I '
                                                           'take an international direct flight to the '
                                                           'Mainland, what kind of documents should I use '
                                                           'for my entry?\n'
                                                           'A10 You should use the Hong Kong and Macao '
                                                           'Residents Entry and Exit Permit (Mainland Travel '
                                                           'Permit for Hong Kong and Macao Residents) to '
                                                           'enter and exit the Mainland.'}],
                                    'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                  'HKSAR passport, resident in Hong Kong, travelling to '
                                                  'mainland China for tourism. Home Return Permit facts are '
                                                  'separate from the non-Chinese resident permit and from a '
                                                  'foreign-visitor visa.'},
             'visa_category': {'new': 'Mainland Travel Permit for Hong Kong and Macao Residents (Chinese '
                                      'citizens)',
                               'evidence': [{'source_id': 'nia_regulation',
                                             'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                             'quote': '第四条 '
                                                      '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                            {'source_id': 'nia_guide',
                                             'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                             'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                            {'source_id': 'nia_guide',
                                             'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                             'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                            {'source_id': 'hksar_chengdu',
                                             'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                             'quote': 'Q10 I am a Hong Kong resident holding both the Hong '
                                                      'Kong and Macao Residents Entry and Exit Permit '
                                                      '(Mainland Travel Permit for Hong Kong and Macao '
                                                      'Residents) and the HKSAR Passport. If I take an '
                                                      'international direct flight to the Mainland, what '
                                                      'kind of documents should I use for my entry?\n'
                                                      'A10 You should use the Hong Kong and Macao Residents '
                                                      'Entry and Exit Permit (Mainland Travel Permit for '
                                                      'Hong Kong and Macao Residents) to enter and exit the '
                                                      'Mainland.'}],
                               'scope_note': 'AI review for a Chinese national holding an ordinary HKSAR '
                                             'passport, resident in Hong Kong, travelling to mainland China '
                                             'for tourism. Home Return Permit facts are separate from the '
                                             'non-Chinese resident permit and from a foreign-visitor visa.'},
             'permitted_stay': {'new': None,
                                'evidence': [],
                                'unknown_reason': 'The reviewed Chinese-resident permit sources establish '
                                                  'the travel credential and its validity, but do not '
                                                  'substantiate the existing unlimited-stay claim or one '
                                                  'numerical maximum stay. No non-Chinese permit90-day limit '
                                                  'is imported.'},
             'permitted_stay_days': {'new': None,
                                     'evidence': [],
                                     'unknown_reason': 'No numerical maximum stay was established for this '
                                                       'Chinese-resident permit; permit validity is a '
                                                       'different field.'},
             'government_fee': {'new': {'amount': 390, 'currency': 'HKD'},
                                'evidence': [{'source_id': 'nia_guide',
                                              'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                              'quote': '有效期为5年的港澳居民来往内地通行证收费260元港币；有效期为10年的港澳居民来往内地通行证收费390元港币；遗失或者证件严重损坏要求补办的，按规定收费。'},
                                             {'source_id': 'nia_guide',
                                              'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                              'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                'scope_note': 'Hong Kong normal adult10-year permit fee. The separately '
                                              'displayed under18permit is HKD260. Lost/damaged replacement '
                                              'and expedited services have different charges; the headline '
                                              'is not a universal fee.'},
             'processing_time': {'new': '12 working days from application acceptance to permit issuance; '
                                        'special investigation or out-of-area verification time is excluded.',
                                 'evidence': [{'source_id': 'nia_guide',
                                               'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                               'quote': '（一）从受理申请到签发港澳居民来往内地通行证12个工作日；需异地核查材料等特殊情况的调查时间，不计入办证时限。'}],
                                 'scope_note': 'AI review for a Chinese national holding an ordinary HKSAR '
                                               'passport, resident in Hong Kong, travelling to mainland '
                                               'China for tourism. Home Return Permit facts are separate '
                                               'from the non-Chinese resident permit and from a '
                                               'foreign-visitor visa.'},
             'application_channel': {'new': 'in_person',
                                     'evidence': [{'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                     'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                   'HKSAR passport, resident in Hong Kong, travelling to '
                                                   'mainland China for tourism. Home Return Permit facts are '
                                                   'separate from the non-Chinese resident permit and from a '
                                                   'foreign-visitor visa.'},
             'application_channel_detail': {'new': 'Chinese Hong Kong residents must use a Mainland Travel '
                                                   'Permit to enter and exit mainland China. To apply in '
                                                   'Hong Kong, book an appointment and submit the permit '
                                                   'application in person to China Travel Service (Hong '
                                                   'Kong), the delegated reception body; Guangdong '
                                                   'Provincial Public Security Department approves and '
                                                   'issues the permit. Under 18 applicants attend with their '
                                                   'legal guardian.',
                                            'evidence': [{'source_id': 'nia_regulation',
                                                          'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                          'quote': '第四条 '
                                                                   '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                         {'source_id': 'hksar_chengdu',
                                                          'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                          'quote': 'Q10 I am a Hong Kong resident holding '
                                                                   'both the Hong Kong and Macao Residents '
                                                                   'Entry and Exit Permit (Mainland Travel '
                                                                   'Permit for Hong Kong and Macao '
                                                                   'Residents) and the HKSAR Passport. If I '
                                                                   'take an international direct flight to '
                                                                   'the Mainland, what kind of documents '
                                                                   'should I use for my entry?\n'
                                                                   'A10 You should use the Hong Kong and '
                                                                   'Macao Residents Entry and Exit Permit '
                                                                   '(Mainland Travel Permit for Hong Kong '
                                                                   'and Macao Residents) to enter and exit '
                                                                   'the Mainland.'},
                                                         {'source_id': 'nia_guide',
                                                          'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                          'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'},
                                                         {'source_id': 'nia_guide',
                                                          'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                          'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                         {'source_id': 'nia_guide',
                                                          'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                          'quote': '公安部授权广东省公安厅负责港澳居民申请通行证的审批签发工作。'},
                                                         {'source_id': 'nia_appointment',
                                                          'source_url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                                                          'quote': '一、申请人需提前预约，选择 '
                                                                   '“办理证件”预约办证时间和地点，然后依约前往办理。'}],
                                            'scope_note': 'AI review for a Chinese national holding an '
                                                          'ordinary HKSAR passport, resident in Hong Kong, '
                                                          'travelling to mainland China for tourism. Home '
                                                          'Return Permit facts are separate from the '
                                                          'non-Chinese resident permit and from a '
                                                          'foreign-visitor visa.'},
             'official_portal_url': {'new': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                     'evidence': [{'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '港澳居民来往内地通行证签发服务指南'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                  {'source_id': 'hksar_chengdu',
                                                   'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                   'quote': "For details, please refer to CTS's website and "
                                                            'make telephone enquiry on (852) 2998 7888.'}],
                                     'scope_note': 'Official NIA application instructions identifying the '
                                                   'delegated Hong Kong reception body; this link is a '
                                                   'service guide, not a claim of a wholly online '
                                                   'application.'},
             'appointment_required': {'new': True,
                                      'evidence': [{'source_id': 'nia_appointment',
                                                    'source_url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                                                    'quote': '一、申请人需提前预约，选择 “办理证件”预约办证时间和地点，然后依约前往办理。'},
                                                   {'source_id': 'nia_guide',
                                                    'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                    'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                      'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                    'HKSAR passport, resident in Hong Kong, travelling to '
                                                    'mainland China for tourism. Home Return Permit facts '
                                                    'are separate from the non-Chinese resident permit and '
                                                    'from a foreign-visitor visa.'},
             'passport_validity': {'new': 'For a Home Return Permit application lodged in Hong Kong, a Hong '
                                          'Kong travel document submitted with the application (including an '
                                          'HKSAR passport) must have more than six months of validity. This '
                                          'is an application-document rule; use the Mainland Travel Permit '
                                          'to enter and exit mainland China. No passport-validity interval '
                                          'at mainland arrival has been established by this review.',
                                   'evidence': [{'source_id': 'cts_pdf',
                                                 'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                 'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。'},
                                                {'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                {'source_id': 'hksar_chengdu',
                                                 'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                 'quote': 'Q10 I am a Hong Kong resident holding both the '
                                                          'Hong Kong and Macao Residents Entry and Exit '
                                                          'Permit (Mainland Travel Permit for Hong Kong and '
                                                          'Macao Residents) and the HKSAR Passport. If I '
                                                          'take an international direct flight to the '
                                                          'Mainland, what kind of documents should I use for '
                                                          'my entry?\n'
                                                          'A10 You should use the Hong Kong and Macao '
                                                          'Residents Entry and Exit Permit (Mainland Travel '
                                                          'Permit for Hong Kong and Macao Residents) to '
                                                          'enter and exit the Mainland.'}],
                                   'scope_note': 'CTS is the NIA-delegated Hong Kong permit reception body. '
                                                 'Its exact captured PDF is the primary source for the '
                                                 'application-document half-year condition. HKSAR government '
                                                 'corroboration separately establishes use of the permit at '
                                                 'the mainland border. This does not create a six-month '
                                                 'arrival clock.'},
             'passport_validity_requirement': {'new': None,
                                               'evidence': [],
                                               'unknown_reason': 'This schema represents passport validity '
                                                                 'at arrival or departure. The verified CTS '
                                                                 'half-year rule applies to a document '
                                                                 'submitted at permit application; it cannot '
                                                                 'be encoded as a passport arrival rule.'},
             'required_documents': {'new': ['For the Hong Kong permit application: complete the Mainland '
                                            'Travel Permit application information online when booking, then '
                                            'check and sign the form at the service centre.',
                                            'One front-facing, bareheaded colour paper photograph on a white '
                                            'background, taken within the past six months, together with its '
                                            'approved photo-inspection receipt.',
                                            'Hong Kong permanent identity card and the evidence of Chinese '
                                            'nationality required for the applicable first-application or '
                                            'renewal category; submit the originals and one copy of the '
                                            'supporting documents.',
                                            'A Hong Kong travel document submitted with the permit '
                                            'application, including an HKSAR passport, must have more than '
                                            'six months of remaining validity. This is an application rule, '
                                            'not a passport-validity rule measured from mainland arrival.',
                                            'For a first application, the required supporting evidence '
                                            'varies with birthplace and how Chinese nationality or Hong Kong '
                                            'residence was obtained. Follow the applicable category in the '
                                            'official instructions; additional evidence may be requested.',
                                            'For renewal, submit the existing Mainland Travel Permit; for a '
                                            'lost permit, complete the loss declaration. Follow the separate '
                                            'replacement category when identity information has changed.',
                                            'Applicants under 18 must be accompanied by their legal guardian '
                                            'and provide the birth or guardianship document and the '
                                            "guardian's identity document; specific approved exceptions "
                                            'apply to renewal or replacement.'],
                                    'evidence': [{'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '（一）《港澳居民來往內地通行證申請表》：請在網上預約時完整填寫申請資訊，現場受理時申請人核對後在表上簽署中文姓名全名；如有未能表述的特殊情況，受理時填寫聲明書如實表述。'},
                                                 {'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                                           '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 '
                                                           '"。'},
                                                 {'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。'},
                                                 {'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '（五）提交的證件及相關文件須為原件並同時提供複印件一份，如複印的材料超過一頁的，請以 '
                                                           'A４紙雙面複印。'},
                                                 {'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '（二）申請人須親自到受理部門遞交申請。18 '
                                                           '周歲以下的申請人須由其合法監護人陪同提出申請，幷提交關係證明文件（法定有效的出生證明文件或監護文件）和監護人身份證明文件。'},
                                                 {'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '第一類：在香港出生具有中國國籍的香港永久性居民首次申請回鄉證的\n'
                                                           '  1.香港永久性居民身份證。11 周歲以下未領有香港身份證的，提交多次回港證；\n'
                                                           '  2.香港特區護照。未滿 18 '
                                                           '周歲未領有香港特區護照的，需交驗申請人出生時父或母為中國公民的證明文\n'
                                                           '件,如中國護照、香港特區護照、臺灣居民來往大陸通行證等。\n'
                                                           '  第二類：內地居民經批准（持單程證）赴香港定居，取得香港居民身份首次申請回鄉證的\n'
                                                           '  1.香港身份證／香港永久性居民身份證；\n'
                                                           '  2.持香港身份證或 11 歲以下未領香港身份證的香港居民須提交香港特別行政區簽證身份書；11 '
                                                           '歲以下\n'
                                                           '香港永久性居民領有香港永久性居民身份證的須提交特區護照，如未領有香港永久性居民身份證的請提交多\n'
                                                           '次有效回港證；\n'
                                                           '  3. 前往港澳通行證。\n'
                                                           '  第三類：在香港以外出生的中國籍居民，已確立香港永久性居民身份首次申請回鄉證的\n'
                                                           '  1.香港永久性居民身份證；\n'
                                                           '  2.香港特區護照或中國護照（有效期須在半年以上）；\n'
                                                           '  '
                                                           '3.首次抵港登記香港居民身份證時所持用的旅行證件（內貼香港入境處進入許可、延期許可），及申請換\n'
                                                           '領永久性居民身份證所持用旅行證件（內貼“以往規定的逗留條件現已告撤銷”標簽）或核實香港永久性居\n'
                                                           '民身份證資格申請結果通知單；\n'
                                                           '  '
                                                           '4.在內地出生的提交內地戶籍部門出具的戶口注銷證明文件，永久性居民在內地所生子女未入戶的提交\n'
                                                           '內地出境證件、香港入境許可證或外國護照等旅行證件；\n'
                                                           '  '
                                                           '5.在臺灣出生的提交臺灣居民身份證、戶籍謄本、臺灣旅行證件，已領取《臺灣居民來往大陸通行證》\n'
                                                           '的，應提交該證幷填寫放棄使用《臺灣居民來往大陸通行證》聲明。\n'
                                                           '  第四類：香港永久性居民中的外國籍或無國籍人士，經批准加入或者恢復中國國籍首次申請回\n'
                                                           '鄉證的\n'
                                                           '  1.香港永久性居民身份證；\n'
                                                           '  2.香港特區護照；\n'
                                                           '  3.批准加入或恢復中國國籍證書；\n'
                                                           '        客戶服務熱線：\n'
                                                           '              （852）2998 7888   '
                                                           '網頁：visa.ctshk.com   電郵：enquiry_epd@ctg.cn\n'
                                                           '\x0c'
                                                           '  '
                                                           '4、香港入境事務處出具的中文版登記事項證明書（申請回鄉證時三個月內所簽發，內容包括首次抵港至\n'
                                                           '今的完整信息方為有效）。\n'
                                                           '  '
                                                           '5.首次抵港登記香港居民身份證時所持用的旅行證件或加入（恢復）中國國籍前持用的外國旅行證件。\n'
                                                           '  第五類：申請換發、補發回鄉證的\n'
                                                           '  1.香港永久性居民身份證／香港居民身份證。年齡未滿 11 '
                                                           '周歲持有香港永久性居民身份證的需同時提交\n'
                                                           '香港特區護照；未持有香港永久性居民身份證的，請提交香港旅行證件。\n'
                                                           '  '
                                                           '2.申請換發回鄉證的（包括證期滿失效、更改及損壞原證），提交目前持用的回鄉證；如持本式回鄉證的\n'
                                                           '須同時提交香港特區護照。\n'
                                                           '  '
                                                           '3.申請人遺失回鄉證申請補發的，受理時須在遺失聲明中簽署中文姓名全名。如報失的是本式回鄉證，\n'
                                                           '必須提交香港特區護照。\n'
                                                           '  '
                                                           '4.持證人身份資訊變更的（包括姓名、性別及出生資料），須提交具法律效力的相關證明（如改名契、香\n'
                                                           '港入境處出具的中文版登記事項證明書或官方認可機構出具的醫學證明等）     。\n'
                                                           '  5.曾退出中國國籍的，需提交恢復中國國籍證書和香港特區護照。\n'
                                                           '  '
                                                           '6.香港居民已在內地定居，取得內地戶籍後又重返香港定居的，提交香港特區護照、赴港證件和內地戶\n'
                                                           '籍部門出具的戶口注銷證明。\n'
                                                           '  7.年齡未滿 18 '
                                                           '周歲申請換發、遺失補發回鄉證（首次申請除外），倘若合法監護人不能陪同辦理，申請\n'
                                                           '人的合法監護人可委託親友陪同申請人提交申請。申請人按辦證規定提交相關申請材料外，還需提交合法監\n'
                                                           '護人簽署的委託書或公證處委託證明、合法監護人的身份證明文件、與申請人關係證明文件（如出生證明）、\n'
                                                           '受託人之身份證明文件。\n'
                                                           '  '
                                                           '8.因患有嚴重疾病等特殊情況本人不能前來申請換發、補發回鄉證的，提供政府醫院或政府福利部門出\n'
                                                           '具的證明（兩個月內開具的）連同以上各點所述的申請材料，遞函經審批部門同意，可以委託申請，申請人\n'
                                                           '或代辦人須填寫申請表"聲明"欄，並提交代辦人身份證。\n'
                                                           '  （五）審批部門審核需要的其他材料。'},
                                                 {'source_id': 'nia_guide',
                                                  'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                  'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                                    'scope_note': 'Chinese-resident Home Return Permit applications lodged '
                                                  'in Hong Kong. The primary delegated CTS instructions and '
                                                  'their category-specific exceptions are retained '
                                                  'literally; this is not an HKSAR passport application or '
                                                  'the non-Chinese permit.'},
             'photo_requirements': {'new': 'For the Hong Kong permit application, submit one front-facing, '
                                           'bareheaded colour paper photograph with a white background, '
                                           'taken within the past six months, and its approved inspection '
                                           'receipt.',
                                    'evidence': [{'source_id': 'cts_pdf',
                                                  'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                  'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                                           '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 '
                                                           '"。'},
                                                 {'source_id': 'nia_guide',
                                                  'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                  'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                                    'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                  'HKSAR passport, resident in Hong Kong, travelling to '
                                                  'mainland China for tourism. Home Return Permit facts are '
                                                  'separate from the non-Chinese resident permit and from a '
                                                  'foreign-visitor visa.'},
             'biometrics_required': {'new': None,
                                     'evidence': [],
                                     'unknown_reason': 'The current captured permit instructions do not '
                                                       'establish the inherited blanket biometric '
                                                       'requirement; no positive or negative biometric rule '
                                                       'is invented.'},
             'onward_travel_evidence': {'new': None,
                                        'evidence': [],
                                        'unknown_reason': 'No return/onward ticket requirement for this '
                                                          'Chinese-resident permit route was established by '
                                                          'the reviewed sources.'},
             'accommodation_evidence': {'new': None,
                                        'evidence': [],
                                        'unknown_reason': 'No booking or host-address entry requirement for '
                                                          'this Chinese-resident permit route was '
                                                          'established by the reviewed sources.'},
             'financial_evidence': {'new': None,
                                    'evidence': [],
                                    'unknown_reason': 'No funds-evidence entry requirement for this '
                                                      'Chinese-resident permit route was established by the '
                                                      'reviewed sources.'},
             'insurance_required': {'new': None,
                                    'evidence': [],
                                    'unknown_reason': 'No positive or negative insurance entry requirement '
                                                      'was established by this permit review.'},
             'arrival_card': {'new': None,
                              'evidence': [],
                              'unknown_reason': 'The reviewed current permit sources do not establish the '
                                                'inherited no-arrival-card claim; no foreign-arrival-card '
                                                'rule is imported.'}},
 'products': [{'current_name': 'Mainland Travel Permit, 10-year (applicants 18 and over)',
               'changes': {'application_channel': {'new': 'in_person',
                                                   'evidence': [{'source_id': 'nia_guide',
                                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                 'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                                   'scope_note': 'AI review for a Chinese national holding '
                                                                 'an ordinary HKSAR passport, resident in '
                                                                 'Hong Kong, travelling to mainland China '
                                                                 'for tourism. Home Return Permit facts are '
                                                                 'separate from the non-Chinese resident '
                                                                 'permit and from a foreign-visitor visa.'},
                           'application_channel_detail': {'new': 'Chinese Hong Kong residents must use a '
                                                                 'Mainland Travel Permit to enter and exit '
                                                                 'mainland China. To apply in Hong Kong, '
                                                                 'book an appointment and submit the permit '
                                                                 'application in person to China Travel '
                                                                 'Service (Hong Kong), the delegated '
                                                                 'reception body; Guangdong Provincial '
                                                                 'Public Security Department approves and '
                                                                 'issues the permit. Under 18 applicants '
                                                                 'attend with their legal guardian.',
                                                          'evidence': [{'source_id': 'nia_regulation',
                                                                        'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                                        'quote': '第四条 '
                                                                                 '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                                       {'source_id': 'hksar_chengdu',
                                                                        'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                                        'quote': 'Q10 I am a Hong Kong '
                                                                                 'resident holding both the '
                                                                                 'Hong Kong and Macao '
                                                                                 'Residents Entry and Exit '
                                                                                 'Permit (Mainland Travel '
                                                                                 'Permit for Hong Kong and '
                                                                                 'Macao Residents) and the '
                                                                                 'HKSAR Passport. If I take '
                                                                                 'an international direct '
                                                                                 'flight to the Mainland, '
                                                                                 'what kind of documents '
                                                                                 'should I use for my '
                                                                                 'entry?\n'
                                                                                 'A10 You should use the '
                                                                                 'Hong Kong and Macao '
                                                                                 'Residents Entry and Exit '
                                                                                 'Permit (Mainland Travel '
                                                                                 'Permit for Hong Kong and '
                                                                                 'Macao Residents) to enter '
                                                                                 'and exit the Mainland.'},
                                                                       {'source_id': 'nia_guide',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                        'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'},
                                                                       {'source_id': 'nia_guide',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                        'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                                       {'source_id': 'nia_guide',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                        'quote': '公安部授权广东省公安厅负责港澳居民申请通行证的审批签发工作。'},
                                                                       {'source_id': 'nia_appointment',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                                                                        'quote': '一、申请人需提前预约，选择 '
                                                                                 '“办理证件”预约办证时间和地点，然后依约前往办理。'}],
                                                          'scope_note': 'AI review for a Chinese national '
                                                                        'holding an ordinary HKSAR passport, '
                                                                        'resident in Hong Kong, travelling '
                                                                        'to mainland China for tourism. Home '
                                                                        'Return Permit facts are separate '
                                                                        'from the non-Chinese resident '
                                                                        'permit and from a foreign-visitor '
                                                                        'visa.'},
                           'required_documents': {'new': ['For the Hong Kong permit application: complete '
                                                          'the Mainland Travel Permit application '
                                                          'information online when booking, then check and '
                                                          'sign the form at the service centre.',
                                                          'One front-facing, bareheaded colour paper '
                                                          'photograph on a white background, taken within '
                                                          'the past six months, together with its approved '
                                                          'photo-inspection receipt.',
                                                          'Hong Kong permanent identity card and the '
                                                          'evidence of Chinese nationality required for the '
                                                          'applicable first-application or renewal category; '
                                                          'submit the originals and one copy of the '
                                                          'supporting documents.',
                                                          'A Hong Kong travel document submitted with the '
                                                          'permit application, including an HKSAR passport, '
                                                          'must have more than six months of remaining '
                                                          'validity. This is an application rule, not a '
                                                          'passport-validity rule measured from mainland '
                                                          'arrival.',
                                                          'For a first application, the required supporting '
                                                          'evidence varies with birthplace and how Chinese '
                                                          'nationality or Hong Kong residence was obtained. '
                                                          'Follow the applicable category in the official '
                                                          'instructions; additional evidence may be '
                                                          'requested.',
                                                          'For renewal, submit the existing Mainland Travel '
                                                          'Permit; for a lost permit, complete the loss '
                                                          'declaration. Follow the separate replacement '
                                                          'category when identity information has changed.',
                                                          'Applicants under 18 must be accompanied by their '
                                                          'legal guardian and provide the birth or '
                                                          "guardianship document and the guardian's identity "
                                                          'document; specific approved exceptions apply to '
                                                          'renewal or replacement.'],
                                                  'evidence': [{'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（一）《港澳居民來往內地通行證申請表》：請在網上預約時完整填寫申請資訊，現場受理時申請人核對後在表上簽署中文姓名全名；如有未能表述的特殊情況，受理時填寫聲明書如實表述。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                                                         '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 '
                                                                         '"。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（五）提交的證件及相關文件須為原件並同時提供複印件一份，如複印的材料超過一頁的，請以 '
                                                                         'A４紙雙面複印。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（二）申請人須親自到受理部門遞交申請。18 '
                                                                         '周歲以下的申請人須由其合法監護人陪同提出申請，幷提交關係證明文件（法定有效的出生證明文件或監護文件）和監護人身份證明文件。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '第一類：在香港出生具有中國國籍的香港永久性居民首次申請回鄉證的\n'
                                                                         '  1.香港永久性居民身份證。11 '
                                                                         '周歲以下未領有香港身份證的，提交多次回港證；\n'
                                                                         '  2.香港特區護照。未滿 18 '
                                                                         '周歲未領有香港特區護照的，需交驗申請人出生時父或母為中國公民的證明文\n'
                                                                         '件,如中國護照、香港特區護照、臺灣居民來往大陸通行證等。\n'
                                                                         '  '
                                                                         '第二類：內地居民經批准（持單程證）赴香港定居，取得香港居民身份首次申請回鄉證的\n'
                                                                         '  1.香港身份證／香港永久性居民身份證；\n'
                                                                         '  2.持香港身份證或 11 '
                                                                         '歲以下未領香港身份證的香港居民須提交香港特別行政區簽證身份書；11 '
                                                                         '歲以下\n'
                                                                         '香港永久性居民領有香港永久性居民身份證的須提交特區護照，如未領有香港永久性居民身份證的請提交多\n'
                                                                         '次有效回港證；\n'
                                                                         '  3. 前往港澳通行證。\n'
                                                                         '  '
                                                                         '第三類：在香港以外出生的中國籍居民，已確立香港永久性居民身份首次申請回鄉證的\n'
                                                                         '  1.香港永久性居民身份證；\n'
                                                                         '  2.香港特區護照或中國護照（有效期須在半年以上）；\n'
                                                                         '  '
                                                                         '3.首次抵港登記香港居民身份證時所持用的旅行證件（內貼香港入境處進入許可、延期許可），及申請換\n'
                                                                         '領永久性居民身份證所持用旅行證件（內貼“以往規定的逗留條件現已告撤銷”標簽）或核實香港永久性居\n'
                                                                         '民身份證資格申請結果通知單；\n'
                                                                         '  '
                                                                         '4.在內地出生的提交內地戶籍部門出具的戶口注銷證明文件，永久性居民在內地所生子女未入戶的提交\n'
                                                                         '內地出境證件、香港入境許可證或外國護照等旅行證件；\n'
                                                                         '  '
                                                                         '5.在臺灣出生的提交臺灣居民身份證、戶籍謄本、臺灣旅行證件，已領取《臺灣居民來往大陸通行證》\n'
                                                                         '的，應提交該證幷填寫放棄使用《臺灣居民來往大陸通行證》聲明。\n'
                                                                         '  '
                                                                         '第四類：香港永久性居民中的外國籍或無國籍人士，經批准加入或者恢復中國國籍首次申請回\n'
                                                                         '鄉證的\n'
                                                                         '  1.香港永久性居民身份證；\n'
                                                                         '  2.香港特區護照；\n'
                                                                         '  3.批准加入或恢復中國國籍證書；\n'
                                                                         '        客戶服務熱線：\n'
                                                                         '              （852）2998 7888   '
                                                                         '網頁：visa.ctshk.com   '
                                                                         '電郵：enquiry_epd@ctg.cn\n'
                                                                         '\x0c'
                                                                         '  '
                                                                         '4、香港入境事務處出具的中文版登記事項證明書（申請回鄉證時三個月內所簽發，內容包括首次抵港至\n'
                                                                         '今的完整信息方為有效）。\n'
                                                                         '  '
                                                                         '5.首次抵港登記香港居民身份證時所持用的旅行證件或加入（恢復）中國國籍前持用的外國旅行證件。\n'
                                                                         '  第五類：申請換發、補發回鄉證的\n'
                                                                         '  1.香港永久性居民身份證／香港居民身份證。年齡未滿 11 '
                                                                         '周歲持有香港永久性居民身份證的需同時提交\n'
                                                                         '香港特區護照；未持有香港永久性居民身份證的，請提交香港旅行證件。\n'
                                                                         '  '
                                                                         '2.申請換發回鄉證的（包括證期滿失效、更改及損壞原證），提交目前持用的回鄉證；如持本式回鄉證的\n'
                                                                         '須同時提交香港特區護照。\n'
                                                                         '  '
                                                                         '3.申請人遺失回鄉證申請補發的，受理時須在遺失聲明中簽署中文姓名全名。如報失的是本式回鄉證，\n'
                                                                         '必須提交香港特區護照。\n'
                                                                         '  '
                                                                         '4.持證人身份資訊變更的（包括姓名、性別及出生資料），須提交具法律效力的相關證明（如改名契、香\n'
                                                                         '港入境處出具的中文版登記事項證明書或官方認可機構出具的醫學證明等）     '
                                                                         '。\n'
                                                                         '  5.曾退出中國國籍的，需提交恢復中國國籍證書和香港特區護照。\n'
                                                                         '  '
                                                                         '6.香港居民已在內地定居，取得內地戶籍後又重返香港定居的，提交香港特區護照、赴港證件和內地戶\n'
                                                                         '籍部門出具的戶口注銷證明。\n'
                                                                         '  7.年齡未滿 18 '
                                                                         '周歲申請換發、遺失補發回鄉證（首次申請除外），倘若合法監護人不能陪同辦理，申請\n'
                                                                         '人的合法監護人可委託親友陪同申請人提交申請。申請人按辦證規定提交相關申請材料外，還需提交合法監\n'
                                                                         '護人簽署的委託書或公證處委託證明、合法監護人的身份證明文件、與申請人關係證明文件（如出生證明）、\n'
                                                                         '受託人之身份證明文件。\n'
                                                                         '  '
                                                                         '8.因患有嚴重疾病等特殊情況本人不能前來申請換發、補發回鄉證的，提供政府醫院或政府福利部門出\n'
                                                                         '具的證明（兩個月內開具的）連同以上各點所述的申請材料，遞函經審批部門同意，可以委託申請，申請人\n'
                                                                         '或代辦人須填寫申請表"聲明"欄，並提交代辦人身份證。\n'
                                                                         '  （五）審批部門審核需要的其他材料。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                                                  'scope_note': 'Chinese-resident Home Return Permit '
                                                                'applications lodged in Hong Kong. The '
                                                                'primary delegated CTS instructions and '
                                                                'their category-specific exceptions are '
                                                                'retained literally; this is not an HKSAR '
                                                                'passport application or the non-Chinese '
                                                                'permit.'},
                           'passport_validity': {'new': 'For a Home Return Permit application lodged in Hong '
                                                        'Kong, a Hong Kong travel document submitted with '
                                                        'the application (including an HKSAR passport) must '
                                                        'have more than six months of validity. This is an '
                                                        'application-document rule; use the Mainland Travel '
                                                        'Permit to enter and exit mainland China. No '
                                                        'passport-validity interval at mainland arrival has '
                                                        'been established by this review.',
                                                 'evidence': [{'source_id': 'cts_pdf',
                                                               'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                               'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。'},
                                                              {'source_id': 'nia_guide',
                                                               'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                               'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                              {'source_id': 'hksar_chengdu',
                                                               'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                               'quote': 'Q10 I am a Hong Kong resident '
                                                                        'holding both the Hong Kong and '
                                                                        'Macao Residents Entry and Exit '
                                                                        'Permit (Mainland Travel Permit for '
                                                                        'Hong Kong and Macao Residents) and '
                                                                        'the HKSAR Passport. If I take an '
                                                                        'international direct flight to the '
                                                                        'Mainland, what kind of documents '
                                                                        'should I use for my entry?\n'
                                                                        'A10 You should use the Hong Kong '
                                                                        'and Macao Residents Entry and Exit '
                                                                        'Permit (Mainland Travel Permit for '
                                                                        'Hong Kong and Macao Residents) to '
                                                                        'enter and exit the Mainland.'}],
                                                 'scope_note': 'CTS is the NIA-delegated Hong Kong permit '
                                                               'reception body. Its exact captured PDF is '
                                                               'the primary source for the '
                                                               'application-document half-year condition. '
                                                               'HKSAR government corroboration separately '
                                                               'establishes use of the permit at the '
                                                               'mainland border. This does not create a '
                                                               'six-month arrival clock.'},
                           'photo_requirements': {'new': 'For the Hong Kong permit application, submit one '
                                                         'front-facing, bareheaded colour paper photograph '
                                                         'with a white background, taken within the past six '
                                                         'months, and its approved inspection receipt.',
                                                  'evidence': [{'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                                                         '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 '
                                                                         '"。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                                                  'scope_note': 'AI review for a Chinese national holding an '
                                                                'ordinary HKSAR passport, resident in Hong '
                                                                'Kong, travelling to mainland China for '
                                                                'tourism. Home Return Permit facts are '
                                                                'separate from the non-Chinese resident '
                                                                'permit and from a foreign-visitor visa.'},
                           'appointment_required': {'new': True,
                                                    'evidence': [{'source_id': 'nia_appointment',
                                                                  'source_url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                                                                  'quote': '一、申请人需提前预约，选择 '
                                                                           '“办理证件”预约办证时间和地点，然后依约前往办理。'},
                                                                 {'source_id': 'nia_guide',
                                                                  'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                  'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                                    'scope_note': 'AI review for a Chinese national holding '
                                                                  'an ordinary HKSAR passport, resident in '
                                                                  'Hong Kong, travelling to mainland China '
                                                                  'for tourism. Home Return Permit facts are '
                                                                  'separate from the non-Chinese resident '
                                                                  'permit and from a foreign-visitor visa.'},
                           'processing_time': {'new': '12 working days from application acceptance to permit '
                                                      'issuance; special investigation or out-of-area '
                                                      'verification time is excluded.',
                                               'evidence': [{'source_id': 'nia_guide',
                                                             'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                             'quote': '（一）从受理申请到签发港澳居民来往内地通行证12个工作日；需异地核查材料等特殊情况的调查时间，不计入办证时限。'}],
                                               'scope_note': 'AI review for a Chinese national holding an '
                                                             'ordinary HKSAR passport, resident in Hong '
                                                             'Kong, travelling to mainland China for '
                                                             'tourism. Home Return Permit facts are separate '
                                                             'from the non-Chinese resident permit and from '
                                                             'a foreign-visitor visa.'},
                           'disposition': {'new': 'CONDITIONAL',
                                           'evidence': [{'source_id': 'nia_regulation',
                                                         'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                         'quote': '第四条 '
                                                                  '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                        {'source_id': 'nia_guide',
                                                         'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                         'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                        {'source_id': 'nia_guide',
                                                         'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                         'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                        {'source_id': 'hksar_chengdu',
                                                         'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                         'quote': 'Q10 I am a Hong Kong resident holding '
                                                                  'both the Hong Kong and Macao Residents '
                                                                  'Entry and Exit Permit (Mainland Travel '
                                                                  'Permit for Hong Kong and Macao Residents) '
                                                                  'and the HKSAR Passport. If I take an '
                                                                  'international direct flight to the '
                                                                  'Mainland, what kind of documents should I '
                                                                  'use for my entry?\n'
                                                                  'A10 You should use the Hong Kong and '
                                                                  'Macao Residents Entry and Exit Permit '
                                                                  '(Mainland Travel Permit for Hong Kong and '
                                                                  'Macao Residents) to enter and exit the '
                                                                  'Mainland.'},
                                                        {'source_id': 'nia_guide',
                                                         'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                         'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                           'scope_note': 'AI review for a Chinese national holding an '
                                                         'ordinary HKSAR passport, resident in Hong Kong, '
                                                         'travelling to mainland China for tourism. Home '
                                                         'Return Permit facts are separate from the '
                                                         'non-Chinese resident permit and from a '
                                                         'foreign-visitor visa.'},
                           'requirement_detail': {'new': 'conditional_visa_free',
                                                  'evidence': [{'source_id': 'nia_regulation',
                                                                'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                                'quote': '第四条 '
                                                                         '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                               {'source_id': 'hksar_chengdu',
                                                                'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                                'quote': 'Q10 I am a Hong Kong resident '
                                                                         'holding both the Hong Kong and '
                                                                         'Macao Residents Entry and Exit '
                                                                         'Permit (Mainland Travel Permit for '
                                                                         'Hong Kong and Macao Residents) and '
                                                                         'the HKSAR Passport. If I take an '
                                                                         'international direct flight to the '
                                                                         'Mainland, what kind of documents '
                                                                         'should I use for my entry?\n'
                                                                         'A10 You should use the Hong Kong '
                                                                         'and Macao Residents Entry and Exit '
                                                                         'Permit (Mainland Travel Permit for '
                                                                         'Hong Kong and Macao Residents) to '
                                                                         'enter and exit the Mainland.'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                                  'scope_note': 'AI review for a Chinese national holding an '
                                                                'ordinary HKSAR passport, resident in Hong '
                                                                'Kong, travelling to mainland China for '
                                                                'tourism. Home Return Permit facts are '
                                                                'separate from the non-Chinese resident '
                                                                'permit and from a foreign-visitor visa.'},
                           'validity': {'new': '10 years',
                                        'evidence': [{'source_id': 'nia_guide',
                                                      'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                      'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                        'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                      'HKSAR passport, resident in Hong Kong, travelling to '
                                                      'mainland China for tourism. Home Return Permit facts '
                                                      'are separate from the non-Chinese resident permit and '
                                                      'from a foreign-visitor visa.'},
                           'entry': {'new': 'multiple',
                                     'evidence': [{'source_id': 'nia_regulation',
                                                   'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                   'quote': '第二十一条 '
                                                            '港澳同胞回乡证由持证人保存，有效期10年，在有效期内可以多次使用，超过有效期或者查验页用完的，可以换领新证。申请新证按照本办法第十四条规定办理。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                     'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                   'HKSAR passport, resident in Hong Kong, travelling to '
                                                   'mainland China for tourism. Home Return Permit facts are '
                                                   'separate from the non-Chinese resident permit and from a '
                                                   'foreign-visitor visa.'},
                           'fee': {'new': {'amount': 390, 'currency': 'HKD'},
                                   'evidence': [{'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '有效期为5年的港澳居民来往内地通行证收费260元港币；有效期为10年的港澳居民来往内地通行证收费390元港币；遗失或者证件严重损坏要求补办的，按规定收费。'},
                                                {'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                   'scope_note': 'AI review for a Chinese national holding an ordinary HKSAR '
                                                 'passport, resident in Hong Kong, travelling to mainland '
                                                 'China for tourism. Home Return Permit facts are separate '
                                                 'from the non-Chinese resident permit and from a '
                                                 'foreign-visitor visa.'},
                           'max_stay_days': {'new': None,
                                             'evidence': [],
                                             'unknown_reason': 'No numerical maximum stay is established for '
                                                               'the Chinese-resident permit. Do not reuse '
                                                               'the distinct non-Chinese permit90-day rule.'},
                           'notes': {'new': 'For Chinese-national Hong Kong residents aged 18 or over. '
                                            'Normal 10-year permit application fee HKD 390. This is a '
                                            'Mainland Travel Permit, not a foreign-visitor tourist visa. '
                                            'Permit validity is separate from permitted stay. First '
                                            'applications are made in person; renewal and replacement have '
                                            'their own document requirements.',
                                     'evidence': [{'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '有效期为5年的港澳居民来往内地通行证收费260元港币；有效期为10年的港澳居民来往内地通行证收费390元港币；遗失或者证件严重损坏要求补办的，按规定收费。'},
                                                  {'source_id': 'nia_regulation',
                                                   'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                   'quote': '第四条 '
                                                            '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                  {'source_id': 'hksar_chengdu',
                                                   'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                   'quote': 'Q10 I am a Hong Kong resident holding both the '
                                                            'Hong Kong and Macao Residents Entry and Exit '
                                                            'Permit (Mainland Travel Permit for Hong Kong '
                                                            'and Macao Residents) and the HKSAR Passport. If '
                                                            'I take an international direct flight to the '
                                                            'Mainland, what kind of documents should I use '
                                                            'for my entry?\n'
                                                            'A10 You should use the Hong Kong and Macao '
                                                            'Residents Entry and Exit Permit (Mainland '
                                                            'Travel Permit for Hong Kong and Macao '
                                                            'Residents) to enter and exit the Mainland.'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                     'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                   'HKSAR passport, resident in Hong Kong, travelling to '
                                                   'mainland China for tourism. Home Return Permit facts are '
                                                   'separate from the non-Chinese resident permit and from a '
                                                   'foreign-visitor visa.'}}},
              {'current_name': 'Mainland Travel Permit, 5-year (under 18)',
               'changes': {'application_channel': {'new': 'in_person',
                                                   'evidence': [{'source_id': 'nia_guide',
                                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                 'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                                   'scope_note': 'AI review for a Chinese national holding '
                                                                 'an ordinary HKSAR passport, resident in '
                                                                 'Hong Kong, travelling to mainland China '
                                                                 'for tourism. Home Return Permit facts are '
                                                                 'separate from the non-Chinese resident '
                                                                 'permit and from a foreign-visitor visa.'},
                           'application_channel_detail': {'new': 'Chinese Hong Kong residents must use a '
                                                                 'Mainland Travel Permit to enter and exit '
                                                                 'mainland China. To apply in Hong Kong, '
                                                                 'book an appointment and submit the permit '
                                                                 'application in person to China Travel '
                                                                 'Service (Hong Kong), the delegated '
                                                                 'reception body; Guangdong Provincial '
                                                                 'Public Security Department approves and '
                                                                 'issues the permit. Under 18 applicants '
                                                                 'attend with their legal guardian.',
                                                          'evidence': [{'source_id': 'nia_regulation',
                                                                        'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                                        'quote': '第四条 '
                                                                                 '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                                       {'source_id': 'hksar_chengdu',
                                                                        'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                                        'quote': 'Q10 I am a Hong Kong '
                                                                                 'resident holding both the '
                                                                                 'Hong Kong and Macao '
                                                                                 'Residents Entry and Exit '
                                                                                 'Permit (Mainland Travel '
                                                                                 'Permit for Hong Kong and '
                                                                                 'Macao Residents) and the '
                                                                                 'HKSAR Passport. If I take '
                                                                                 'an international direct '
                                                                                 'flight to the Mainland, '
                                                                                 'what kind of documents '
                                                                                 'should I use for my '
                                                                                 'entry?\n'
                                                                                 'A10 You should use the '
                                                                                 'Hong Kong and Macao '
                                                                                 'Residents Entry and Exit '
                                                                                 'Permit (Mainland Travel '
                                                                                 'Permit for Hong Kong and '
                                                                                 'Macao Residents) to enter '
                                                                                 'and exit the Mainland.'},
                                                                       {'source_id': 'nia_guide',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                        'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'},
                                                                       {'source_id': 'nia_guide',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                        'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                                       {'source_id': 'nia_guide',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                        'quote': '公安部授权广东省公安厅负责港澳居民申请通行证的审批签发工作。'},
                                                                       {'source_id': 'nia_appointment',
                                                                        'source_url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                                                                        'quote': '一、申请人需提前预约，选择 '
                                                                                 '“办理证件”预约办证时间和地点，然后依约前往办理。'}],
                                                          'scope_note': 'AI review for a Chinese national '
                                                                        'holding an ordinary HKSAR passport, '
                                                                        'resident in Hong Kong, travelling '
                                                                        'to mainland China for tourism. Home '
                                                                        'Return Permit facts are separate '
                                                                        'from the non-Chinese resident '
                                                                        'permit and from a foreign-visitor '
                                                                        'visa.'},
                           'required_documents': {'new': ['For the Hong Kong permit application: complete '
                                                          'the Mainland Travel Permit application '
                                                          'information online when booking, then check and '
                                                          'sign the form at the service centre.',
                                                          'One front-facing, bareheaded colour paper '
                                                          'photograph on a white background, taken within '
                                                          'the past six months, together with its approved '
                                                          'photo-inspection receipt.',
                                                          'Hong Kong permanent identity card and the '
                                                          'evidence of Chinese nationality required for the '
                                                          'applicable first-application or renewal category; '
                                                          'submit the originals and one copy of the '
                                                          'supporting documents.',
                                                          'A Hong Kong travel document submitted with the '
                                                          'permit application, including an HKSAR passport, '
                                                          'must have more than six months of remaining '
                                                          'validity. This is an application rule, not a '
                                                          'passport-validity rule measured from mainland '
                                                          'arrival.',
                                                          'For a first application, the required supporting '
                                                          'evidence varies with birthplace and how Chinese '
                                                          'nationality or Hong Kong residence was obtained. '
                                                          'Follow the applicable category in the official '
                                                          'instructions; additional evidence may be '
                                                          'requested.',
                                                          'For renewal, submit the existing Mainland Travel '
                                                          'Permit; for a lost permit, complete the loss '
                                                          'declaration. Follow the separate replacement '
                                                          'category when identity information has changed.',
                                                          'Applicants under 18 must be accompanied by their '
                                                          'legal guardian and provide the birth or '
                                                          "guardianship document and the guardian's identity "
                                                          'document; specific approved exceptions apply to '
                                                          'renewal or replacement.'],
                                                  'evidence': [{'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（一）《港澳居民來往內地通行證申請表》：請在網上預約時完整填寫申請資訊，現場受理時申請人核對後在表上簽署中文姓名全名；如有未能表述的特殊情況，受理時填寫聲明書如實表述。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                                                         '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 '
                                                                         '"。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（五）提交的證件及相關文件須為原件並同時提供複印件一份，如複印的材料超過一頁的，請以 '
                                                                         'A４紙雙面複印。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（二）申請人須親自到受理部門遞交申請。18 '
                                                                         '周歲以下的申請人須由其合法監護人陪同提出申請，幷提交關係證明文件（法定有效的出生證明文件或監護文件）和監護人身份證明文件。'},
                                                               {'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '第一類：在香港出生具有中國國籍的香港永久性居民首次申請回鄉證的\n'
                                                                         '  1.香港永久性居民身份證。11 '
                                                                         '周歲以下未領有香港身份證的，提交多次回港證；\n'
                                                                         '  2.香港特區護照。未滿 18 '
                                                                         '周歲未領有香港特區護照的，需交驗申請人出生時父或母為中國公民的證明文\n'
                                                                         '件,如中國護照、香港特區護照、臺灣居民來往大陸通行證等。\n'
                                                                         '  '
                                                                         '第二類：內地居民經批准（持單程證）赴香港定居，取得香港居民身份首次申請回鄉證的\n'
                                                                         '  1.香港身份證／香港永久性居民身份證；\n'
                                                                         '  2.持香港身份證或 11 '
                                                                         '歲以下未領香港身份證的香港居民須提交香港特別行政區簽證身份書；11 '
                                                                         '歲以下\n'
                                                                         '香港永久性居民領有香港永久性居民身份證的須提交特區護照，如未領有香港永久性居民身份證的請提交多\n'
                                                                         '次有效回港證；\n'
                                                                         '  3. 前往港澳通行證。\n'
                                                                         '  '
                                                                         '第三類：在香港以外出生的中國籍居民，已確立香港永久性居民身份首次申請回鄉證的\n'
                                                                         '  1.香港永久性居民身份證；\n'
                                                                         '  2.香港特區護照或中國護照（有效期須在半年以上）；\n'
                                                                         '  '
                                                                         '3.首次抵港登記香港居民身份證時所持用的旅行證件（內貼香港入境處進入許可、延期許可），及申請換\n'
                                                                         '領永久性居民身份證所持用旅行證件（內貼“以往規定的逗留條件現已告撤銷”標簽）或核實香港永久性居\n'
                                                                         '民身份證資格申請結果通知單；\n'
                                                                         '  '
                                                                         '4.在內地出生的提交內地戶籍部門出具的戶口注銷證明文件，永久性居民在內地所生子女未入戶的提交\n'
                                                                         '內地出境證件、香港入境許可證或外國護照等旅行證件；\n'
                                                                         '  '
                                                                         '5.在臺灣出生的提交臺灣居民身份證、戶籍謄本、臺灣旅行證件，已領取《臺灣居民來往大陸通行證》\n'
                                                                         '的，應提交該證幷填寫放棄使用《臺灣居民來往大陸通行證》聲明。\n'
                                                                         '  '
                                                                         '第四類：香港永久性居民中的外國籍或無國籍人士，經批准加入或者恢復中國國籍首次申請回\n'
                                                                         '鄉證的\n'
                                                                         '  1.香港永久性居民身份證；\n'
                                                                         '  2.香港特區護照；\n'
                                                                         '  3.批准加入或恢復中國國籍證書；\n'
                                                                         '        客戶服務熱線：\n'
                                                                         '              （852）2998 7888   '
                                                                         '網頁：visa.ctshk.com   '
                                                                         '電郵：enquiry_epd@ctg.cn\n'
                                                                         '\x0c'
                                                                         '  '
                                                                         '4、香港入境事務處出具的中文版登記事項證明書（申請回鄉證時三個月內所簽發，內容包括首次抵港至\n'
                                                                         '今的完整信息方為有效）。\n'
                                                                         '  '
                                                                         '5.首次抵港登記香港居民身份證時所持用的旅行證件或加入（恢復）中國國籍前持用的外國旅行證件。\n'
                                                                         '  第五類：申請換發、補發回鄉證的\n'
                                                                         '  1.香港永久性居民身份證／香港居民身份證。年齡未滿 11 '
                                                                         '周歲持有香港永久性居民身份證的需同時提交\n'
                                                                         '香港特區護照；未持有香港永久性居民身份證的，請提交香港旅行證件。\n'
                                                                         '  '
                                                                         '2.申請換發回鄉證的（包括證期滿失效、更改及損壞原證），提交目前持用的回鄉證；如持本式回鄉證的\n'
                                                                         '須同時提交香港特區護照。\n'
                                                                         '  '
                                                                         '3.申請人遺失回鄉證申請補發的，受理時須在遺失聲明中簽署中文姓名全名。如報失的是本式回鄉證，\n'
                                                                         '必須提交香港特區護照。\n'
                                                                         '  '
                                                                         '4.持證人身份資訊變更的（包括姓名、性別及出生資料），須提交具法律效力的相關證明（如改名契、香\n'
                                                                         '港入境處出具的中文版登記事項證明書或官方認可機構出具的醫學證明等）     '
                                                                         '。\n'
                                                                         '  5.曾退出中國國籍的，需提交恢復中國國籍證書和香港特區護照。\n'
                                                                         '  '
                                                                         '6.香港居民已在內地定居，取得內地戶籍後又重返香港定居的，提交香港特區護照、赴港證件和內地戶\n'
                                                                         '籍部門出具的戶口注銷證明。\n'
                                                                         '  7.年齡未滿 18 '
                                                                         '周歲申請換發、遺失補發回鄉證（首次申請除外），倘若合法監護人不能陪同辦理，申請\n'
                                                                         '人的合法監護人可委託親友陪同申請人提交申請。申請人按辦證規定提交相關申請材料外，還需提交合法監\n'
                                                                         '護人簽署的委託書或公證處委託證明、合法監護人的身份證明文件、與申請人關係證明文件（如出生證明）、\n'
                                                                         '受託人之身份證明文件。\n'
                                                                         '  '
                                                                         '8.因患有嚴重疾病等特殊情況本人不能前來申請換發、補發回鄉證的，提供政府醫院或政府福利部門出\n'
                                                                         '具的證明（兩個月內開具的）連同以上各點所述的申請材料，遞函經審批部門同意，可以委託申請，申請人\n'
                                                                         '或代辦人須填寫申請表"聲明"欄，並提交代辦人身份證。\n'
                                                                         '  （五）審批部門審核需要的其他材料。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                                                  'scope_note': 'Chinese-resident Home Return Permit '
                                                                'applications lodged in Hong Kong. The '
                                                                'primary delegated CTS instructions and '
                                                                'their category-specific exceptions are '
                                                                'retained literally; this is not an HKSAR '
                                                                'passport application or the non-Chinese '
                                                                'permit.'},
                           'passport_validity': {'new': 'For a Home Return Permit application lodged in Hong '
                                                        'Kong, a Hong Kong travel document submitted with '
                                                        'the application (including an HKSAR passport) must '
                                                        'have more than six months of validity. This is an '
                                                        'application-document rule; use the Mainland Travel '
                                                        'Permit to enter and exit mainland China. No '
                                                        'passport-validity interval at mainland arrival has '
                                                        'been established by this review.',
                                                 'evidence': [{'source_id': 'cts_pdf',
                                                               'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                               'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。'},
                                                              {'source_id': 'nia_guide',
                                                               'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                               'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                                              {'source_id': 'hksar_chengdu',
                                                               'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                               'quote': 'Q10 I am a Hong Kong resident '
                                                                        'holding both the Hong Kong and '
                                                                        'Macao Residents Entry and Exit '
                                                                        'Permit (Mainland Travel Permit for '
                                                                        'Hong Kong and Macao Residents) and '
                                                                        'the HKSAR Passport. If I take an '
                                                                        'international direct flight to the '
                                                                        'Mainland, what kind of documents '
                                                                        'should I use for my entry?\n'
                                                                        'A10 You should use the Hong Kong '
                                                                        'and Macao Residents Entry and Exit '
                                                                        'Permit (Mainland Travel Permit for '
                                                                        'Hong Kong and Macao Residents) to '
                                                                        'enter and exit the Mainland.'}],
                                                 'scope_note': 'CTS is the NIA-delegated Hong Kong permit '
                                                               'reception body. Its exact captured PDF is '
                                                               'the primary source for the '
                                                               'application-document half-year condition. '
                                                               'HKSAR government corroboration separately '
                                                               'establishes use of the permit at the '
                                                               'mainland border. This does not create a '
                                                               'six-month arrival clock.'},
                           'photo_requirements': {'new': 'For the Hong Kong permit application, submit one '
                                                         'front-facing, bareheaded colour paper photograph '
                                                         'with a white background, taken within the past six '
                                                         'months, and its approved inspection receipt.',
                                                  'evidence': [{'source_id': 'cts_pdf',
                                                                'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                                                                'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                                                         '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 '
                                                                         '"。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                                                  'scope_note': 'AI review for a Chinese national holding an '
                                                                'ordinary HKSAR passport, resident in Hong '
                                                                'Kong, travelling to mainland China for '
                                                                'tourism. Home Return Permit facts are '
                                                                'separate from the non-Chinese resident '
                                                                'permit and from a foreign-visitor visa.'},
                           'appointment_required': {'new': True,
                                                    'evidence': [{'source_id': 'nia_appointment',
                                                                  'source_url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                                                                  'quote': '一、申请人需提前预约，选择 '
                                                                           '“办理证件”预约办证时间和地点，然后依约前往办理。'},
                                                                 {'source_id': 'nia_guide',
                                                                  'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                  'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                                    'scope_note': 'AI review for a Chinese national holding '
                                                                  'an ordinary HKSAR passport, resident in '
                                                                  'Hong Kong, travelling to mainland China '
                                                                  'for tourism. Home Return Permit facts are '
                                                                  'separate from the non-Chinese resident '
                                                                  'permit and from a foreign-visitor visa.'},
                           'processing_time': {'new': '12 working days from application acceptance to permit '
                                                      'issuance; special investigation or out-of-area '
                                                      'verification time is excluded.',
                                               'evidence': [{'source_id': 'nia_guide',
                                                             'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                             'quote': '（一）从受理申请到签发港澳居民来往内地通行证12个工作日；需异地核查材料等特殊情况的调查时间，不计入办证时限。'}],
                                               'scope_note': 'AI review for a Chinese national holding an '
                                                             'ordinary HKSAR passport, resident in Hong '
                                                             'Kong, travelling to mainland China for '
                                                             'tourism. Home Return Permit facts are separate '
                                                             'from the non-Chinese resident permit and from '
                                                             'a foreign-visitor visa.'},
                           'disposition': {'new': 'CONDITIONAL',
                                           'evidence': [{'source_id': 'nia_regulation',
                                                         'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                         'quote': '第四条 '
                                                                  '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                        {'source_id': 'nia_guide',
                                                         'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                         'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                        {'source_id': 'nia_guide',
                                                         'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                         'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                        {'source_id': 'hksar_chengdu',
                                                         'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                         'quote': 'Q10 I am a Hong Kong resident holding '
                                                                  'both the Hong Kong and Macao Residents '
                                                                  'Entry and Exit Permit (Mainland Travel '
                                                                  'Permit for Hong Kong and Macao Residents) '
                                                                  'and the HKSAR Passport. If I take an '
                                                                  'international direct flight to the '
                                                                  'Mainland, what kind of documents should I '
                                                                  'use for my entry?\n'
                                                                  'A10 You should use the Hong Kong and '
                                                                  'Macao Residents Entry and Exit Permit '
                                                                  '(Mainland Travel Permit for Hong Kong and '
                                                                  'Macao Residents) to enter and exit the '
                                                                  'Mainland.'},
                                                        {'source_id': 'nia_guide',
                                                         'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                         'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                           'scope_note': 'AI review for a Chinese national holding an '
                                                         'ordinary HKSAR passport, resident in Hong Kong, '
                                                         'travelling to mainland China for tourism. Home '
                                                         'Return Permit facts are separate from the '
                                                         'non-Chinese resident permit and from a '
                                                         'foreign-visitor visa.'},
                           'requirement_detail': {'new': 'conditional_visa_free',
                                                  'evidence': [{'source_id': 'nia_regulation',
                                                                'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                                'quote': '第四条 '
                                                                         '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                               {'source_id': 'hksar_chengdu',
                                                                'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                                'quote': 'Q10 I am a Hong Kong resident '
                                                                         'holding both the Hong Kong and '
                                                                         'Macao Residents Entry and Exit '
                                                                         'Permit (Mainland Travel Permit for '
                                                                         'Hong Kong and Macao Residents) and '
                                                                         'the HKSAR Passport. If I take an '
                                                                         'international direct flight to the '
                                                                         'Mainland, what kind of documents '
                                                                         'should I use for my entry?\n'
                                                                         'A10 You should use the Hong Kong '
                                                                         'and Macao Residents Entry and Exit '
                                                                         'Permit (Mainland Travel Permit for '
                                                                         'Hong Kong and Macao Residents) to '
                                                                         'enter and exit the Mainland.'},
                                                               {'source_id': 'nia_guide',
                                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                                'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                                  'scope_note': 'AI review for a Chinese national holding an '
                                                                'ordinary HKSAR passport, resident in Hong '
                                                                'Kong, travelling to mainland China for '
                                                                'tourism. Home Return Permit facts are '
                                                                'separate from the non-Chinese resident '
                                                                'permit and from a foreign-visitor visa.'},
                           'validity': {'new': '5 years',
                                        'evidence': [{'source_id': 'nia_guide',
                                                      'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                      'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                        'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                      'HKSAR passport, resident in Hong Kong, travelling to '
                                                      'mainland China for tourism. Home Return Permit facts '
                                                      'are separate from the non-Chinese resident permit and '
                                                      'from a foreign-visitor visa.'},
                           'entry': {'new': 'multiple',
                                     'evidence': [{'source_id': 'nia_regulation',
                                                   'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                   'quote': '第二十一条 '
                                                            '港澳同胞回乡证由持证人保存，有效期10年，在有效期内可以多次使用，超过有效期或者查验页用完的，可以换领新证。申请新证按照本办法第十四条规定办理。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                     'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                   'HKSAR passport, resident in Hong Kong, travelling to '
                                                   'mainland China for tourism. Home Return Permit facts are '
                                                   'separate from the non-Chinese resident permit and from a '
                                                   'foreign-visitor visa.'},
                           'fee': {'new': {'amount': 260, 'currency': 'HKD'},
                                   'evidence': [{'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '有效期为5年的港澳居民来往内地通行证收费260元港币；有效期为10年的港澳居民来往内地通行证收费390元港币；遗失或者证件严重损坏要求补办的，按规定收费。'},
                                                {'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'}],
                                   'scope_note': 'AI review for a Chinese national holding an ordinary HKSAR '
                                                 'passport, resident in Hong Kong, travelling to mainland '
                                                 'China for tourism. Home Return Permit facts are separate '
                                                 'from the non-Chinese resident permit and from a '
                                                 'foreign-visitor visa.'},
                           'max_stay_days': {'new': None,
                                             'evidence': [],
                                             'unknown_reason': 'No numerical maximum stay is established for '
                                                               'the Chinese-resident permit. Do not reuse '
                                                               'the distinct non-Chinese permit90-day rule.'},
                           'notes': {'new': 'For Chinese-national Hong Kong residents under 18. Normal '
                                            '5-year permit application fee HKD 260; a legal guardian '
                                            'accompanies the child and provides the required identity and '
                                            'relationship documents. This is a Mainland Travel Permit, not a '
                                            'foreign-visitor tourist visa. Permit validity is separate from '
                                            'permitted stay. First applications are made in person; renewal '
                                            'and replacement have their own document requirements.',
                                     'evidence': [{'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '审批通过后，为未满十八周岁的申请人签发有效期为5年的港澳居民来往内地通行证，为十八周岁以上（含十八周岁）的申请人签发有效期为10年的港澳居民来往内地通行证。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '有效期为5年的港澳居民来往内地通行证收费260元港币；有效期为10年的港澳居民来往内地通行证收费390元港币；遗失或者证件严重损坏要求补办的，按规定收费。'},
                                                  {'source_id': 'nia_regulation',
                                                   'source_url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                                                   'quote': '第四条 '
                                                            '港澳同胞来往于香港、澳门与内地之间，凭我国公安机关签发的港澳同胞回乡证或者入出境通行证，从中国对外开放的口岸通行。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（一）在香港或者澳门出生具有中国国籍的香港或者澳门永久性居民。'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（三）在香港或者澳门以外出生的中国籍居民，已确立香港或者澳门永久性居民身份的。'},
                                                  {'source_id': 'hksar_chengdu',
                                                   'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                   'quote': 'Q10 I am a Hong Kong resident holding both the '
                                                            'Hong Kong and Macao Residents Entry and Exit '
                                                            'Permit (Mainland Travel Permit for Hong Kong '
                                                            'and Macao Residents) and the HKSAR Passport. If '
                                                            'I take an international direct flight to the '
                                                            'Mainland, what kind of documents should I use '
                                                            'for my entry?\n'
                                                            'A10 You should use the Hong Kong and Macao '
                                                            'Residents Entry and Exit Permit (Mainland '
                                                            'Travel Permit for Hong Kong and Macao '
                                                            'Residents) to enter and exit the Mainland.'},
                                                  {'source_id': 'nia_guide',
                                                   'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                   'quote': '（一）港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请，未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件。'}],
                                     'scope_note': 'AI review for a Chinese national holding an ordinary '
                                                   'HKSAR passport, resident in Hong Kong, travelling to '
                                                   'mainland China for tourism. Home Return Permit facts are '
                                                   'separate from the non-Chinese resident permit and from a '
                                                   'foreign-visitor visa.'}}}]}

SOURCES = {'nia_guide': {'url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
               'sha256': '5c5a6de353b5dc94a49687e9f4cdef80a78fb60c351113dcd83d0dc6d06512ee',
               'checked_at': '2026-09-10',
               'http_body_sha256': '9a83d697c30bf6f216b75dcf81198a764fd72141dde185ea22c65de777c05ce8'},
 'cts_booking': {'url': 'https://www.ctshk.com/mep/zh/apply-chinapermit-2/',
                 'sha256': '4a9c5e8fbb81d1a5b841c8a8eb43c0f0ec03c8656dd2f2cb6d7f46792dc7dc79',
                 'checked_at': '2026-09-10',
                 'http_body_sha256': '9fc7a53ee0541bb5fe2f97a0908b54a009e11f0bf278a5b2c7fe9f3f10388c96'},
 'cts_pdf': {'url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
             'sha256': '065ab045ca2dbace2352358074d177cb9e30970027305864b318a852165665de',
             'checked_at': '2026-09-10',
             'http_body_sha256': '824e98f09e8cd75fc2e1c937ed56606e790641d43c4a4b6fd2ab32b59b0cfb79'},
 'nia_regulation': {'url': 'https://www.nia.gov.cn/n741440/n741547/c757562/content.html',
                    'sha256': '13711ac6ba5cf1e0c4ad766904a289230f049485321d7a9aece26e9596ee3850',
                    'checked_at': '2026-09-10',
                    'http_body_sha256': None},
 'nia_appointment': {'url': 'https://s.nia.gov.cn/mps/views/hxz/hxz-ydxy.html',
                     'sha256': '49662cbf1e0f4c84be7d80051eb1359e61ec7634e98d6b82a71b2bedc06a1d74',
                     'checked_at': '2026-09-10',
                     'http_body_sha256': None},
 'hksar_chengdu': {'url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                   'sha256': '38e5f9020d8afdcbbbe9e4bf03bf073410b2b57ae4552e990c3f70ea5eb17239',
                   'checked_at': '2026-09-10',
                   'http_body_sha256': None},
 'hksar_passport': {'url': 'https://www.immd.gov.hk/eng/service/travel_document/apply_for_hksar_passport.html',
                    'sha256': '2a704e863be693f5d7854901f2949652b03f3a9062022b176144bba0ab58bd62',
                    'checked_at': '2026-09-10',
                    'http_body_sha256': None}}

AUTHORITY_LINKS = {'captured_at': '2026-09-10',
 'government_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
 'link_label': "CTS's website",
 'target': 'https://www.ctshk.com',
 'capture_kind': 'Fresh web reader link1',
 'delegation_source': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
 'delegation_quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。',
 'limitation': 'Delegation covers permit reception; it does not make all CTS commercial content a government '
               'source.'}

BASELINES = {'route': 'b8f37c0d05aeb1406f57441d8d67fb6b4349dc2015c09f792a89de6fd69cec2f',
 'raw_guidance': '28e13e1e19d4f37369aa6b41666a8ed28512f27e79697d552af88c2741e382a8',
 'merged_guidance': 'afc77411a82eeb664abb89c06837e993a7843ea32f5adf5107cf0c400f89fad1',
 'source_provenance': 'd14c68ec9e4b9112722c30d87399aeeaffd94f1897e0aec32f7cde183bd13dc3',
 'seed_entries': '2e9fe45622491729394bf23d9d9636fa05c13a60e19e681b428cef46cfb104cf',
 'operator_entries': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'}

RESOLVED_WARNINGS = [{'field': 'government_fee', 'reason': 'Current Home Return Permit fee not verified; set to null.'},
 {'field': 'official_portal_url/source_url',
  'reason': 'Exact official NIA service page URL not verified; no URL invented.'}]

DELEGATED_ID = 'hkg-mainland-cts-application-20260910'

DELEGATED_HASH = '3c8eede668f3b9784a56424287298b67fce0c1076be19ebf0fff3caea2119a64'

DELEGATED_PROOFS = {'passport_validity': {'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                       'verified_at': '2026-09-10',
                       'verified_by': 'Ellis AI official-source field review',
                       'verifier': 'ai',
                       'note': 'CTS is the NIA-delegated Hong Kong permit reception body. Its exact captured '
                               'PDF is the primary source for the application-document half-year condition. '
                               'HKSAR government corroboration separately establishes use of the permit at '
                               'the mainland border. This does not create a six-month arrival clock.',
                       'source_id': 'cts_pdf',
                       'supporting_evidence': [{'source_id': 'nia_guide',
                                                'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'},
                                               {'source_id': 'hksar_chengdu',
                                                'source_url': 'https://www.cdeto.gov.hk/en/mainland/apply_passport.html',
                                                'quote': 'Q10 I am a Hong Kong resident holding both the '
                                                         'Hong Kong and Macao Residents Entry and Exit '
                                                         'Permit (Mainland Travel Permit for Hong Kong and '
                                                         'Macao Residents) and the HKSAR Passport. If I take '
                                                         'an international direct flight to the Mainland, '
                                                         'what kind of documents should I use for my entry?\n'
                                                         'A10 You should use the Hong Kong and Macao '
                                                         'Residents Entry and Exit Permit (Mainland Travel '
                                                         'Permit for Hong Kong and Macao Residents) to enter '
                                                         'and exit the Mainland.'}],
                       'status': 'reviewed',
                       'quote': '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。',
                       'authority_binding_id': 'hkg-mainland-cts-application-20260910',
                       'authority_binding_sha256': '3c8eede668f3b9784a56424287298b67fce0c1076be19ebf0fff3caea2119a64'},
 'required_documents': {'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                        'verified_at': '2026-09-10',
                        'verified_by': 'Ellis AI official-source field review',
                        'verifier': 'ai',
                        'note': 'Chinese-resident Home Return Permit applications lodged in Hong Kong. The '
                                'primary delegated CTS instructions and their category-specific exceptions '
                                'are retained literally; this is not an HKSAR passport application or the '
                                'non-Chinese permit.',
                        'source_id': 'cts_pdf',
                        'supporting_evidence': [{'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                        'additional_quotes': ['（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                              '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 "。',
                                              '（四）申請人提交的香港旅行證件（包括香港特區護照、簽證身份書及回港證）有效期須在半年以上，簽證身份書國籍應爲中國，回港證需爲多次有效。',
                                              '（五）提交的證件及相關文件須為原件並同時提供複印件一份，如複印的材料超過一頁的，請以 A４紙雙面複印。',
                                              '（二）申請人須親自到受理部門遞交申請。18 '
                                              '周歲以下的申請人須由其合法監護人陪同提出申請，幷提交關係證明文件（法定有效的出生證明文件或監護文件）和監護人身份證明文件。',
                                              '第一類：在香港出生具有中國國籍的香港永久性居民首次申請回鄉證的\n'
                                              '  1.香港永久性居民身份證。11 周歲以下未領有香港身份證的，提交多次回港證；\n'
                                              '  2.香港特區護照。未滿 18 周歲未領有香港特區護照的，需交驗申請人出生時父或母為中國公民的證明文\n'
                                              '件,如中國護照、香港特區護照、臺灣居民來往大陸通行證等。\n'
                                              '  第二類：內地居民經批准（持單程證）赴香港定居，取得香港居民身份首次申請回鄉證的\n'
                                              '  1.香港身份證／香港永久性居民身份證；\n'
                                              '  2.持香港身份證或 11 歲以下未領香港身份證的香港居民須提交香港特別行政區簽證身份書；11 歲以下\n'
                                              '香港永久性居民領有香港永久性居民身份證的須提交特區護照，如未領有香港永久性居民身份證的請提交多\n'
                                              '次有效回港證；\n'
                                              '  3. 前往港澳通行證。\n'
                                              '  第三類：在香港以外出生的中國籍居民，已確立香港永久性居民身份首次申請回鄉證的\n'
                                              '  1.香港永久性居民身份證；\n'
                                              '  2.香港特區護照或中國護照（有效期須在半年以上）；\n'
                                              '  3.首次抵港登記香港居民身份證時所持用的旅行證件（內貼香港入境處進入許可、延期許可），及申請換\n'
                                              '領永久性居民身份證所持用旅行證件（內貼“以往規定的逗留條件現已告撤銷”標簽）或核實香港永久性居\n'
                                              '民身份證資格申請結果通知單；\n'
                                              '  4.在內地出生的提交內地戶籍部門出具的戶口注銷證明文件，永久性居民在內地所生子女未入戶的提交\n'
                                              '內地出境證件、香港入境許可證或外國護照等旅行證件；\n'
                                              '  5.在臺灣出生的提交臺灣居民身份證、戶籍謄本、臺灣旅行證件，已領取《臺灣居民來往大陸通行證》\n'
                                              '的，應提交該證幷填寫放棄使用《臺灣居民來往大陸通行證》聲明。\n'
                                              '  第四類：香港永久性居民中的外國籍或無國籍人士，經批准加入或者恢復中國國籍首次申請回\n'
                                              '鄉證的\n'
                                              '  1.香港永久性居民身份證；\n'
                                              '  2.香港特區護照；\n'
                                              '  3.批准加入或恢復中國國籍證書；\n'
                                              '        客戶服務熱線：\n'
                                              '              （852）2998 7888   網頁：visa.ctshk.com   '
                                              '電郵：enquiry_epd@ctg.cn\n'
                                              '\x0c'
                                              '  4、香港入境事務處出具的中文版登記事項證明書（申請回鄉證時三個月內所簽發，內容包括首次抵港至\n'
                                              '今的完整信息方為有效）。\n'
                                              '  5.首次抵港登記香港居民身份證時所持用的旅行證件或加入（恢復）中國國籍前持用的外國旅行證件。\n'
                                              '  第五類：申請換發、補發回鄉證的\n'
                                              '  1.香港永久性居民身份證／香港居民身份證。年齡未滿 11 周歲持有香港永久性居民身份證的需同時提交\n'
                                              '香港特區護照；未持有香港永久性居民身份證的，請提交香港旅行證件。\n'
                                              '  2.申請換發回鄉證的（包括證期滿失效、更改及損壞原證），提交目前持用的回鄉證；如持本式回鄉證的\n'
                                              '須同時提交香港特區護照。\n'
                                              '  3.申請人遺失回鄉證申請補發的，受理時須在遺失聲明中簽署中文姓名全名。如報失的是本式回鄉證，\n'
                                              '必須提交香港特區護照。\n'
                                              '  4.持證人身份資訊變更的（包括姓名、性別及出生資料），須提交具法律效力的相關證明（如改名契、香\n'
                                              '港入境處出具的中文版登記事項證明書或官方認可機構出具的醫學證明等）     。\n'
                                              '  5.曾退出中國國籍的，需提交恢復中國國籍證書和香港特區護照。\n'
                                              '  6.香港居民已在內地定居，取得內地戶籍後又重返香港定居的，提交香港特區護照、赴港證件和內地戶\n'
                                              '籍部門出具的戶口注銷證明。\n'
                                              '  7.年齡未滿 18 周歲申請換發、遺失補發回鄉證（首次申請除外），倘若合法監護人不能陪同辦理，申請\n'
                                              '人的合法監護人可委託親友陪同申請人提交申請。申請人按辦證規定提交相關申請材料外，還需提交合法監\n'
                                              '護人簽署的委託書或公證處委託證明、合法監護人的身份證明文件、與申請人關係證明文件（如出生證明）、\n'
                                              '受託人之身份證明文件。\n'
                                              '  8.因患有嚴重疾病等特殊情況本人不能前來申請換發、補發回鄉證的，提供政府醫院或政府福利部門出\n'
                                              '具的證明（兩個月內開具的）連同以上各點所述的申請材料，遞函經審批部門同意，可以委託申請，申請人\n'
                                              '或代辦人須填寫申請表"聲明"欄，並提交代辦人身份證。\n'
                                              '  （五）審批部門審核需要的其他材料。'],
                        'status': 'reviewed',
                        'quote': '（一）《港澳居民來往內地通行證申請表》：請在網上預約時完整填寫申請資訊，現場受理時申請人核對後在表上簽署中文姓名全名；如有未能表述的特殊情況，受理時填寫聲明書如實表述。',
                        'authority_binding_id': 'hkg-mainland-cts-application-20260910',
                        'authority_binding_sha256': '3c8eede668f3b9784a56424287298b67fce0c1076be19ebf0fff3caea2119a64'},
 'photo_requirements': {'source_url': 'https://www.ctshk.com/mep/wp-content/uploads/2025/01/APN001_%E5%9B%9E%E9%84%89%E8%AD%89%E5%8F%97%E7%90%86%E9%A0%88%E7%9F%A5_2024.03.28.pdf',
                        'verified_at': '2026-09-10',
                        'verified_by': 'Ellis AI official-source field review',
                        'verifier': 'ai',
                        'note': 'AI review for a Chinese national holding an ordinary HKSAR passport, '
                                'resident in Hong Kong, travelling to mainland China for tourism. Home '
                                'Return Permit facts are separate from the non-Chinese resident permit and '
                                'from a foreign-visitor visa.',
                        'source_id': 'cts_pdf',
                        'supporting_evidence': [{'source_id': 'nia_guide',
                                                 'source_url': 'https://s.nia.gov.cn/mps/bszy/wldl/ndtxz/202008/t20200828_1260.html',
                                                 'quote': '（一）公安部委托香港中旅集团负责香港居民申请通行证的受理工作。'}],
                        'status': 'reviewed',
                        'quote': '（二）相片：近半年拍攝的正面免冠白底彩色紙質相片 1 '
                                 '張，幷提交該相片的合格檢測回執；相片的詳細規格及提交辦法，請閱"《港澳居民來往內地通行證》相片規範 "。',
                        'authority_binding_id': 'hkg-mainland-cts-application-20260910',
                        'authority_binding_sha256': '3c8eede668f3b9784a56424287298b67fce0c1076be19ebf0fff3caea2119a64'}}

