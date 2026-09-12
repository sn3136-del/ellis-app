"""The adversarial battery of the round two, three, five and seven reviews.

Every sentence a review executed and found proving the wrong verdict is
listed here with the nationality and verdict it leaked for, and must refuse;
the canaries are clean rows that must keep proving. The battery is the
converter's regression floor: a converter that leaks one of these sentences
again has re-opened a closed finding, whatever else its tests say.
"""
import pytest

from scripts.convert_reviewed_general_batch import _decision_supported


D = dict(document_type='ordinary_passport', purpose='tourism', detail='unconditional_visa_free')

# (label, value, quotes, nat, kwargs)
REFUSE = []


def add(label, value, quotes, nat, **kw):
    opts = dict(D)
    opts.update(kw)
    REFUSE.append((label, value, quotes if isinstance(quotes, list) else [quotes], nat, opts))


# --- round 2 ---
add('r2 group carve-out MMR', 'VISA_EXEMPT',
    'Visa is not required for a stay of less than one (1) month for ASEAN nationals except Myanmar.', 'MMR')
for nat in ('BGR', 'ROU'):
    add('r2 EU carve-out ' + nat, 'VISA_EXEMPT',
        'EU citizens do not need a visa, except for Bulgaria and Romania.', nat)
add('r2 dependency BVI', 'VISA_EXEMPT',
    'Holders of British Virgin Islands passports do not require a visa for stays of up to 90 days.', 'GBR')
add('r2 dependency French Polynesia', 'VISA_EXEMPT',
    'Holders of French Polynesia travel documents do not require a visa.', 'FRA')
add('r2 Chinese Taipei', 'VISA_REQUIRED',
    'Chinese Taipei nationals must obtain a visa before arrival.', 'CHN', detail='paper_visa')
add('r2 EAR service offer', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    'Chinese resident of Taiwan satisfying the following criteria can make use of this online service to apply for pre-arrival registration to visit the HKSAR:',
    'TWN', detail='eta_electronic_authorization')
add('r2 EAR spanish esta', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    'Esta informacion es para ciudadanos estadounidenses.', 'USA', detail='eta_electronic_authorization')
add('r2 EAR baggage counter', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    'Indian nationals may use the pre-arrival registration counter for baggage.', 'IND', detail='eta_electronic_authorization')
add('r2 CJK optional online visa', 'VISA_REQUIRED',
    '香港或澳門居民現行可申請網簽或入出境許可證來臺，', 'HKG', detail='evisa')
add('r2 fee row eTA', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    'e usa 5-year multiple entry eTA $185', 'USA', detail='eta_electronic_authorization')
add('r2 fee row visa', 'VISA_REQUIRED', 'USA tourist visa 90 days USD 160', 'USA', detail='paper_visa')
add('r2 fee row e-visa', 'VISA_REQUIRED', 'India e-visa fee: INR 2,000', 'IND', detail='evisa')
add('r2 stem Indiana', 'VISA_EXEMPT', 'Los ciudadanos de Indiana no necesitan visado.', 'IND')
add('r2 stem Francesca', 'VISA_EXEMPT', 'Francesca no necesita visado.', 'FRA')
add('r2 stem Russell', 'VISA_EXEMPT', 'Mr Russell does not need a visa.', 'RUS')

# --- round 3 ---
for quote, nat in [
    ('EU citizens do not need a visa, with the exception of Bulgarian nationals.', 'BGR'),
    ('EU citizens do not need a visa, apart from Bulgarian nationals.', 'BGR'),
    ('EU citizens do not need a visa, aside from Bulgarian nationals.', 'BGR'),
    ('EU citizens do not need a visa, with the sole exception of Bulgarian nationals.', 'BGR'),
    ("Les ressortissants de l'Union européenne sont dispensés de visa, à l'exception des Bulgares et des Roumains.", 'BGR'),
    ("Les ressortissants de l'Union européenne sont dispensés de visa, à l'exception des Bulgares et des Roumains.", 'ROU'),
    ("Les ressortissants de l'Union européenne sont dispensés de visa, hormis les Roumains.", 'ROU'),
    ('Los ciudadanos de la Unión Europea están exentos de visado, con excepción de los búlgaros.', 'BGR'),
    ('EU-Bürger benötigen kein Visum, mit Ausnahme von Bulgarien und Rumänien.', 'BGR'),
    ('EU-Bürger benötigen kein Visum, mit Ausnahme von Bulgarien und Rumänien.', 'ROU'),
    ('EU-Bürger benötigen kein Visum, ausgenommen Rumänien.', 'ROU'),
    ("I cittadini dell'Unione europea sono esenti dal visto, ad eccezione dei bulgari.", 'BGR'),
    ('Os cidadãos da União Europeia estão isentos de visto, com exceção dos búlgaros.', 'BGR'),
    ('Công dân ASEAN được miễn thị thực, ngoại trừ Bulgaria và Romania.', 'BGR'),
    ('Warga negara Uni Eropa bebas visa, selain Bulgaria.', 'BGR'),
    ('东盟国家公民免签，缅甸除外。', 'MMR'),
    ('東南アジア諸国連合の国民はビザ免除ですが、ミャンマーを除く。', 'MMR'),
    ('아세안 국민은 무비자입니다. 미얀마는 제외합니다.', 'MMR'),
    ('EU citizens do not need a visa, except Bulgarian and Romanian nationals holding non-biometric passports.', 'ROU'),
    ('EU citizens do not need a visa, except Bulgarian and Romanian nationals holding non-biometric passports.', 'BGR'),
]:
    add('r3 exception wording', 'VISA_EXEMPT', quote, nat)
for value, quote, nat in [
    ('VISA_REQUIRED', 'Non-EU citizens must obtain a visa before travelling.', 'FRA'),
    ('VISA_REQUIRED', 'Non-EU nationals are required to hold a visa.', 'FRA'),
    ('VISA_REQUIRED', 'Citizens of non-EU countries need a visa.', 'FRA'),
    ('VISA_REQUIRED', 'Les ressortissants hors Union européenne doivent obtenir un visa.', 'FRA'),
    ('VISA_REQUIRED', 'Non-ASEAN nationals must obtain a visa.', 'THA'),
    ('VISA_EXEMPT', 'Nicht-EU-Bürger benötigen kein Visum.', 'FRA'),
    ('VISA_REQUIRED', 'Warga negara bukan ASEAN wajib memiliki visa.', 'THA'),
    ('VISA_REQUIRED', '非东盟国家公民需要签证。', 'THA'),
]:
    add('r3 negated group', value, quote, nat, detail='paper_visa' if value == 'VISA_REQUIRED' else 'unconditional_visa_free')
for quote, nat in [
    ('Visa-free entry for Indian nationals has been suspended with effect from 1 July 2026.', 'IND'),
    ('The visa exemption agreement with China has been terminated.', 'CHN'),
    ('La exención de visado para los ciudadanos españoles queda suspendida.', 'ESP'),
    ('日本国民に対する査証免除措置は一時停止されています。', 'JPN'),
    ('대한민국 국민에 대한 사증면제 조치가 중단되었습니다.', 'KOR'),
    ('Việc miễn thị thực cho công dân Việt Nam đã bị tạm dừng.', 'VNM'),
    ('Bebas visa bagi warga negara Indonesia telah dihentikan sementara.', 'IDN'),
    ('การยกเว้นวีซ่าสำหรับคนไทยถูกระงับชั่วคราว', 'THA'),
    ('Безвизовый режим для граждан России приостановлен.', 'RUS'),
    ('Die Visumfreiheit für deutsche Staatsangehörige ist ausgesetzt.', 'DEU'),
    ('Indian nationals were exempt from the visa requirement until the agreement was withdrawn.', 'IND'),
    ('Indian nationals will be exempt from the visa requirement.', 'IND'),
]:
    add('r3 suspension', 'VISA_EXEMPT', quote, nat)
for quote, nat in [
    ('日本国民に対する査証免除は令和6年12月31日まで有効です。', 'JPN'),
    ('การยกเว้นวีซ่าสำหรับคนไทยมีผลจนถึงวันที่ 31 ธันวาคม 2567', 'THA'),
    ('Bebas visa bagi warga negara Indonesia berlaku hingga 31 Desember 2024.', 'IDN'),
    ('Безвизовый режим для граждан России действует до 31 декабря 2024 года.', 'RUS'),
    ('Chính sách miễn thị thực cho công dân Việt Nam có hiệu lực đến ngày 31 tháng 12 năm 2024.', 'VNM'),
    ('The visa exemption for Chinese nationals is valid until 2024/12/31.', 'CHN'),
    ('中方对法国持普通护照人员的免签政策实施期限为2023/12/01至2024/11/30。', 'FRA'),
    ('The visa exemption for United States nationals applied from 1 January 2024 until 31 December 2024.', 'USA'),
]:
    add('r3 expired window', 'VISA_EXEMPT', quote, nat)
add('r3 group unreadable exception', 'VISA_EXEMPT',
    'EU citizens do not need a visa for stays of up to 90 days, except for the nationalities listed below.', 'ESP')
add('r3 group unreadable exception 2', 'VISA_EXEMPT', 'ASEAN nationals do not need a visa except:', 'IDN')
add('r3 group unreadable exception 3', 'VISA_EXEMPT',
    'Visa is not required for a stay of less than one (1) month for ASEAN nationals except the countries listed in the table.', 'IDN')
add('r3 unreadable exception annex', 'VISA_EXEMPT',
    'EU citizens do not need a visa, apart from those listed in the annex.', 'FRA')
add('r3 unreadable exception emergency', 'VISA_EXEMPT',
    'Indian nationals do not need a visa, with the exception of holders of emergency travel documents.', 'IND')
add('r3 unreadable exception fr', 'VISA_EXEMPT',
    'Les ressortissants indiens sont dispensés de visa, hormis les titulaires de documents de voyage provisoires.', 'IND')
add('r3 unreadable exception ja', 'VISA_EXEMPT',
    '東南アジア諸国連合の国民はビザ免除ですが、外交旅券所持者を除く。', 'THA')
add('r3 unless flips the requirement', 'VISA_REQUIRED',
    'All travellers must obtain a visa unless they are Japanese nationals.', 'JPN', detail='paper_visa')
add('r3 document class diplomatic only', 'VISA_EXEMPT',
    'Holders of diplomatic and service passports do not require a visa.', 'IND')
add('r3 transit only', 'VISA_EXEMPT',
    'Indian nationals in direct transit through the airport do not require a visa.', 'IND')

# --- round 5 ---
RULE = 'Nationals of the countries and territories listed below are exempt from the visa requirement for stays of up to 90 days.'
NOTE = 'Note: the visa exemption for Japan has been suspended until further notice.'
PAGE = RULE + '\nJapan\nKorea\nSingapore\n' + NOTE + '\n'
add('r5 suspension adjacent', 'VISA_EXEMPT', [RULE, 'Japan', NOTE], 'JPN')
add('r5 suspension next sentence', 'VISA_EXEMPT',
    ['Japanese nationals are exempt from the visa requirement.', 'The visa exemption has been suspended.'], 'JPN')
add('r5 suspension dated', 'VISA_EXEMPT',
    ['Japanese nationals are exempt from the visa requirement. This measure has been suspended since 1 April 2026.'], 'JPN')
add('r5 suspension before', 'VISA_EXEMPT',
    ['Visa exemption for Japanese nationals is suspended. Japanese nationals are exempt from the visa requirement for stays of up to 90 days.'], 'JPN')
add('r5 suspension on the page section', 'VISA_EXEMPT', [RULE, 'Korea'], 'KOR', pages=[('p', PAGE)] * 2)
add('r5 suspension on the page section jpn', 'VISA_EXEMPT', [RULE, 'Japan'], 'JPN', pages=[('p', PAGE)] * 2)
for quotes, nat in [
    (['ASEAN nationals do not require a visa for stays of up to one month.', 'Myanmar nationals are not covered by this arrangement.'], 'MMR'),
    (['EU citizens do not need a visa.', 'Bulgarian nationals remain subject to the visa requirement.'], 'BGR'),
    (['ASEAN nationals are exempt from the visa requirement. This does not apply to Myanmar.'], 'MMR'),
    (['Myanmar is excluded from the arrangement. ASEAN nationals do not require a visa.'], 'MMR'),
]:
    add('r5 prose carve-out', 'VISA_EXEMPT', quotes, nat)
for phrase in ['with the sole exception of', 'with the single exception of', 'but not', 'save in the case of', 'saving',
               'bar', 'less', 'to the exclusion of', 'not including', 'with the exclusion of']:
    add('r5 exception construction ' + phrase, 'VISA_EXEMPT',
        f'EU citizens do not need a visa, {phrase} Bulgarian nationals.', 'BGR')
for quote, nat in [
    ('Japan is not on the visa-free list.', 'JPN'),
    ('Japan has been removed from the visa-free list.', 'JPN'),
    ('The visa exemption does not extend to Japanese nationals.', 'JPN'),
    ('Japanese nationals are excluded from the visa-free scheme.', 'JPN'),
    ('The visa-free arrangement with Japan is under review and does not currently apply.', 'JPN'),
    ('Japan was struck from the visa waiver list.', 'JPN'),
]:
    add('r5 negation', 'VISA_EXEMPT', quote, nat)
add('r5 destination JPN required', 'VISA_REQUIRED',
    'Kenyan citizens travelling to Japan must obtain a visa prior to departure.', 'JPN', detail='paper_visa')
add('r5 destination JPN exempt', 'VISA_EXEMPT', 'Kenyan citizens do not require a visa for Japan.', 'JPN')
add('r5 destination IND', 'VISA_EXEMPT',
    'Our nationals are exempt from the visa requirement when travelling to India.', 'IND')
add('r5 destination KOR', 'VISA_EXEMPT',
    'Holders of our passports do not need a visa to visit the Republic of Korea.', 'KOR')
for quote, nat in [
    ('Indian nationals travelling for business purposes do not require a visa.', 'IND'),
    ('Indian nationals coming for medical treatment do not require a visa.', 'IND'),
    ('Chinese nationals enrolled in a degree programme do not require a visa.', 'CHN'),
    ('Indian nationals travelling on official government missions do not require a visa.', 'IND'),
]:
    add('r5 purpose scoped', 'VISA_EXEMPT', quote, nat)
for quote, nat in [
    ('Chinese citizens are eligible for visa-free entry under the 144-hour transit policy.', 'CHN'),
    ('Indian nationals in transit may enter without a visa for up to 24 hours.', 'IND'),
    ('Japanese nationals qualify for transit without visa entry at the airport.', 'JPN'),
]:
    add('r5 transit scoped', 'VISA_EXEMPT', quote, nat)
add('r5 conditional VOA', 'VISA_ON_ARRIVAL',
    'Indian nationals holding a valid United States visa may obtain a visa on arrival.', 'IND', detail='paper_visa_on_arrival')
add('r5 conditional VOA resident', 'VISA_ON_ARRIVAL',
    'Chinese nationals who are permanent residents of Hong Kong may be issued a visa on arrival.', 'CHN', detail='paper_visa_on_arrival')
add('r5 conditional EAR crew', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    'Indian nationals who are crew members must obtain an ETA.', 'IND', detail='eta_electronic_authorization')
for quote, nat in [
    ('Holders of Indian refugee travel documents do not require a visa.', 'IND'),
    ('Indian seafarers holding a seafarer identity document do not require a visa.', 'IND'),
    ('Holders of Russian alien passports do not require a visa.', 'RUS'),
    ('Holders of a certificate of identity issued by India do not require a visa.', 'IND'),
]:
    add('r5 other document class', 'VISA_EXEMPT', quote, nat)
CN_RULE = '上述国家持普通护照人员来华经商、旅游观光、探亲访友、交流访问、过境不超过30天，可免办签证入境。'
CN_SUNSET = '对其余48国持普通护照人员的免签政策施行至2026年12月31日。'
CN_PAGE = '为进一步便利中外人员往来，中方决定扩大免签国家范围。\n法国、德国、日本、韩国\n' + CN_RULE + '\n' + CN_SUNSET + '\n'
add('r5 unquoted sunset on the page', 'VISA_EXEMPT', [CN_RULE, '法国、德国、日本、韩国'], 'JPN', pages=[('p', CN_PAGE)] * 2)
CN_QA = ('答：目前，中方对文莱持普通护照人员的免签政策未设施行期限，对俄罗斯持普通护照人员的免签政策施行至2027年12月31日，'
         '对其余48国持普通护照人员的免签政策施行至2026年12月31日。')
add('r5 unquoted sunset beside another nationality', 'VISA_EXEMPT', [CN_RULE, '法国、德国、日本、韩国'], 'JPN',
    pages=[('p', CN_PAGE + CN_QA + '\n')] * 2)
FREEDONIA_RULE = 'Nationals of the following countries must obtain a visa before travelling to Freedonia.'
for heading in ('Visa-free countries.', 'Annex II.', 'Visa-free countries|', 'Visa-free countries', 'Visa waiver countries',
                'Visa Waiver Program', 'ETA countries', 'Visa nationals', 'Schedule 2', 'Part II', 'Category B', 'List B',
                'Group II', 'Second Schedule', 'Countries whose nationals may enter freely', 'Freedom of movement list'):
    page = (FREEDONIA_RULE + '\nChina, India, Kenya\n' + heading + '\nAustria, France, Germany, Japan, Spain, United States\n')
    add('r5 heading ' + heading, 'VISA_REQUIRED', [FREEDONIA_RULE, 'Austria, France, Germany, Japan, Spain, United States'],
        'FRA', pages=[('p', page)] * 2, detail='paper_visa')
LIST_LINE = 'Slovakia, Slovenia, Spain, Sweden, Switzerland, Tuvalu*, United Kingdom*, and United States of America*.'
LIST_RULE = 'Nationals of the following countries are eligible for the visa-exemption program, with a duration of stay of up to 90 days:'
add('r5 footnote on our own list line', 'VISA_EXEMPT', [LIST_LINE, LIST_RULE], 'USA',
    pages=[('p', LIST_RULE + '\n' + LIST_LINE + '\n')] * 2)

# --- rounds 3 and 5 sentences the first rebuild left out ---
for quote, nat in [
    ('British National (Overseas) passport holders do not require a visa.', 'GBR'),
    ('Holders of Bermuda passports issued by the United Kingdom do not require a visa.', 'GBR'),
    ('Holders of Dutch Caribbean identity cards do not require a visa.', 'NLD'),
    ('Holders of diplomatic and official passports of India are exempt from the visa requirement.', 'IND'),
    ('Indian nationals holding diplomatic or service passports do not require a visa.', 'IND'),
    ('Les titulaires de passeports diplomatiques français sont dispensés de visa.', 'FRA'),
    ('Người mang hộ chiếu ngoại giao Việt Nam được miễn thị thực.', 'VNM'),
    ('Pemegang paspor diplomatik Indonesia bebas visa.', 'IDN'),
    ('대한민국 외교관 여권 소지자는 무비자입니다.', 'KOR'),
    ('Visa-free entry applies to holders of United States diplomatic passports.', 'USA'),
]:
    add('r3 document class again', 'VISA_EXEMPT', quote, nat)
for quote, nat in [
    ('Indian nationals holding a valid United States visa do not require a visa.', 'IND'),
    ('Chinese nationals who are permanent residents of Hong Kong do not require a visa.', 'CHN'),
    ('Indian nationals who are crew members of an aircraft do not require a visa.', 'IND'),
    ('Nationals who hold a residence permit of a Schengen State (for example Indian nationals resident in Germany) do not require a visa.', 'IND'),
]:
    add('r3 conditional again', 'VISA_EXEMPT', quote, nat)
for quote, nat in [
    ('Indian nationals in direct transit through the international airport do not require a visa.', 'IND'),
    ('Chinese citizens are eligible for the 144-hour visa-free transit policy.', 'CHN'),
    ('日本国民は通過の場合、ビザ免除となります。', 'JPN'),
]:
    add('r3 transit again', 'VISA_EXEMPT', quote, nat)
for quote, nat in [
    ('From 1 January 2027, Indian nationals will be exempt from the visa requirement.', 'IND'),
    ('From 1 January 2024 to 31 December 2024, United States nationals are exempt from the visa requirement.', 'USA'),
    ('中方决定对法国持普通护照人员试行免签政策，实施期限为2023年12月1日至2024年11月30日。', 'FRA'),
    ('2020년 4월 13일부터 대한민국 국민에 대한 무비자 입국이 일시 중지되었습니다.', 'KOR'),
]:
    add('r3 window again', 'VISA_EXEMPT', quote, nat)
add('r3 EAR not eligible', 'ELECTRONIC_AUTHORIZATION_REQUIRED', 'India is not eligible for K-ETA.', 'IND',
    detail='eta_electronic_authorization')
add('r3 EAR recommended only', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    '한국 국민은 K-ETA 없이도 입국할 수 있으나 필요 시 사전등록을 권장합니다.', 'KOR', detail='eta_electronic_authorization')
add('r3 EAR portal reading', 'ELECTRONIC_AUTHORIZATION_REQUIRED',
    'Indian nationals: the ETA portal is necessary reading before you travel.', 'IND', detail='eta_electronic_authorization')
add('r3 currency mention', 'VISA_REQUIRED',
    'Fees are payable in cash (Indian rupees are not accepted). A visa is required for all visitors.', 'IND', detail='paper_visa')
add('r5 stay only', 'VISA_EXEMPT', 'Indian nationals may stay for 30 days.', 'IND')
add('r5 apply at the embassy', 'VISA_EXEMPT', 'Nationals of India may apply at the embassy.', 'IND')

# --- round 7 review, the five blocking and two major findings ---
add('r7 B1 annex sibling item', 'VISA_EXEMPT',
    ['Visa-free entry for stays of up to 30 days applies to ASEAN nationals, except Myanmar nationals '
     'and persons from the countries listed in Annex III.'], 'LAO')
ASEAN = 'Visa is not required for a stay of less than one (1) month for ASEAN nationals.'
for extra in ('The arrangement covers all ASEAN members except Myanmar.',
              'Participation is limited to the ASEAN members other than Myanmar.'):
    add('r7 B2 carve-out without verdict words', 'VISA_EXEMPT', [ASEAN], 'MMR',
        pages=[('p', ASEAN + chr(10) + extra)])
INTRO = 'Nationals of the following countries do not require a visa for stays of up to 45 days:'
BIO = '(1) The exemption applies only to holders of biometric passports.'
for cell in ('Japan and Thailand (1)', 'Japan, Korea, Singapore (1)', 'Japan / Thailand (1)', 'Japan,' + chr(10) + 'Thailand (1)'):
    add('r7 B3 closing mark on a multi-entry cell', 'VISA_EXEMPT', [INTRO, cell], 'JPN',
        pages=[('p', INTRO + chr(10) + cell + chr(10) + BIO + chr(10))] * 2)
SUNSET = 'The visa exemption scheme ends on 31 December 2025.'
for row in ('Japan', '1) Japan', '* Japan'):
    page = INTRO + chr(10) + row + chr(10) + row.replace('Japan', 'Poland') + chr(10) + SUNSET + chr(10)
    add('r7 B4 list row shaped like a footnote', 'VISA_EXEMPT', [INTRO, row], 'JPN', pages=[('p', page)] * 2)
TWN = 'Nationals of Brunei, the Philippines and Thailand (effective until July 31, 2022) are eligible for visa-free entry.'
for nat in ('BRN', 'PHL', 'THA'):
    add('r7 B5 bound after a conjunction run', 'VISA_EXEMPT', [TWN], nat)
for relation in ('ceases to apply on', 'shall cease on', 'lapses on', 'applies for arrivals before', 'runs to'):
    add('r7 M1 end relation ' + relation, 'VISA_EXEMPT',
        ['Nationals of Japan do not require a visa. The visa exemption scheme ' + relation + ' 31 December 2025.'], 'JPN')
for extra in ('Myanmar is not a party to the arrangement.',
              'The list of beneficiary countries does not include Myanmar.',
              'Myanmar was left out of the 2026 arrangement.',
              'Myanmar joins the arrangement at a later date.'):
    add('r7 M2 removal outside the enumeration', 'VISA_EXEMPT', [ASEAN], 'MMR',
        pages=[('p', ASEAN + chr(10) + extra)])

PROVE = [
    ('canary south korean', 'VISA_EXEMPT', ['South Korean nationals do not need a visa.'], 'KOR', dict(D)),
    ('canary french curly apostrophe', 'VISA_REQUIRED',
     ['L’entrée des ressortissants français est soumise à l’obtention d’un visa.'], 'FRA', dict(D, detail='paper_visa')),
    ('canary readable exception other member', 'VISA_EXEMPT',
     ['EU citizens do not need a visa, with the exception of Bulgarian nationals.'], 'ESP', dict(D)),
    ('canary asean other member', 'VISA_EXEMPT', ['아세안 국민은 무비자입니다. 미얀마는 제외합니다.'], 'THA', dict(D)),
    ('canary plain list', 'VISA_EXEMPT', [RULE, 'Japan'], 'JPN',
     dict(D, pages=[('p', RULE + '\nJapan\nKorea\nSingapore\n')] * 2)),
    ('canary footnote on another entry', 'VISA_EXEMPT', [LIST_LINE, LIST_RULE], 'ESP',
     dict(D, pages=[('p', LIST_RULE + '\n' + LIST_LINE + '\n')] * 2)),
    # auswaertiges-amt.de, the kept FRA to DEU row: the group path with no
    # exception clause, and Germany named only as the destination.
    ('canary eu group into germany', 'VISA_EXEMPT',
     ['EU-Bürger benötigen generell kein Visum für die Einreise nach Deutschland.'], 'FRA', dict(D)),
    ('canary r7 B3 control, the footnote body names the neighbour', 'VISA_EXEMPT',
     [INTRO, 'Japan and Thailand (1)'], 'JPN',
     dict(D, pages=[('p', INTRO + '\nJapan and Thailand (1)\n(1) Thailand nationals must hold a biometric passport.\n')] * 2)),
]


@pytest.mark.parametrize('label,value,quotes,nat,opts', REFUSE, ids=[f'{i}-{r[0]}-{r[3]}' for i, r in enumerate(REFUSE)])
def test_every_reviewed_failure_sentence_still_refuses(label, value, quotes, nat, opts):
    explain = []
    assert not _decision_supported(value, quotes, nat, explain=explain, **opts), (label, explain)


@pytest.mark.parametrize('label,value,quotes,nat,opts', PROVE, ids=[r[0] for r in PROVE])
def test_every_canary_still_proves(label, value, quotes, nat, opts):
    explain = []
    assert _decision_supported(value, quotes, nat, explain=explain, **opts), (label, explain)


def test_the_battery_holds_the_rounds_it_was_built_from():
    """The rounds two, three and five sentences (143 of them) plus the round
    seven findings; a battery that shrinks has lost a closed finding."""
    assert len(REFUSE) == 186 and len(PROVE) == 8
