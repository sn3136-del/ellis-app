"""Convert a general official-source review batch into a reviewed serving overlay.

The Schengen, Australia and Japan batches each carried a closed converter with
pinned rows. This converter is generic in shape but not in trust: every value
still needs its own literal quote from a captured government page whose owner
is the destination, a verdict needs a statement about THIS nationality on such a
page, figures must occur in their own evidence, products bind to the exact
current product identity, and the whole batch binds to captured production
layers that a deployer re-checks under maintenance. No database, seed, operator
or issue writes happen here; the output is a detached candidate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import re
import unicodedata

from scripts.prepare_reviewed_product_patch import PatchRejected, digest, route_identity
from scripts.convert_reviewed_product_patch import field_provenance

KIND = 'general_reviewed_batch'
VERIFIED_BY = 'Ellis AI official-source field review'
BASELINE_KEYS = ('route', 'raw_guidance', 'merged_guidance', 'source_provenance',
                 'seed_entries', 'operator_entries')
ROUTE_FIELDS = ('permitted_stay_days', 'permitted_stay', 'government_fee', 'application_channel',
                'application_channel_detail', 'processing_time', 'official_portal_url',
                'required_documents', 'exceptions', 'arrival_card')
# A route-level proof key covers the value keys listed with it.
ROUTE_PROOF_COVERS = {'permitted_stay_days': ('permitted_stay_days', 'permitted_stay'),
                      'government_fee': ('government_fee',),
                      'application_channel': ('application_channel', 'application_channel_detail'),
                      'processing_time': ('processing_time',),
                      'official_portal_url': ('official_portal_url',),
                      'required_documents': ('required_documents',),
                      'exceptions': ('exceptions',),
                      'arrival_card': ('arrival_card',)}
PRODUCT_FIELDS = ('entry', 'validity', 'max_stay_days', 'fee', 'notes')
PRODUCT_PROOF_FIELDS = ('disposition', 'entry', 'validity', 'max_stay_days', 'fee')
# Which 25-field record cells a not-published proof marks.
NOT_PUBLISHED_CELLS = {'permitted_stay_days': ('max_stay_duration', 'max_stay_unit'),
                       'max_stay_days': ('max_stay_duration', 'max_stay_unit'),
                       'validity': ('validity_duration', 'validity_unit'),
                       'fee': ('visa_fee_amount', 'visa_fee_currency'),
                       'government_fee': ('visa_fee_amount', 'visa_fee_currency'),
                       'processing_time': ('processing_min_days', 'processing_unit'),
                       'entry': ('entries',)}
_CONDITION_RE = re.compile(r"hold(?:er|ers|ing)?\b|valid (?:visa|permit|residence)|provided|only if|subject to|"
                           r"regist(?:er|ration)|in transit|transit(?:ing)? through|group|accompan", re.I)
# Wording that makes an exemption conditional: a document the traveller must
# hold beyond the passport, a residence or crew status, a registration, a
# restriction word ("solo Pte. HKSAR", "only"), a passport-type requirement,
# or a nationality named only as an example. A sentence that carries one
# cannot prove an unconditional verdict. "Passport holders" alone is the
# subject, not a condition, so the hold pattern needs its object noun.
_HOLD_OBJECT = (r"\bhold(?:er|ers|ing|s)?\b(?:\s+(?!(?:do|does|not|no|require|requires|need|needs|must|are|is|will|may|can|"
                r"shall|should|obtain|apply|be|been|who|which|that)\b)[a-z.'’-]+){0,5}\s+"
                r"(?:visas?|permits?|residence|residents?|residency|green cards?|identity cards?|id cards?|tickets?|proof)\b")
_CONDITIONAL_CORE = (
    r"|\bwho\s+" + _HOLD_OBJECT[2:] +
    r"|\bwho (?:are|is) (?:a |an |the )?(?:permanent |lawful |legal |ordinary )?resident|\bwho (?:are|is) (?:crew|members?|part of)|"
    r"\bprovided\b|\bonly if\b|\bsubject to\b(?!\s+(?:the\s+)?(?:requirement|obligation|need|visa|entry clearance|eta|k-eta|esta|etias|nzeta|evisitor)\b)|"
    r"\bon condition\b|\bconditional\b|\bunless\b|"
    r"\btour group|\bgroup (?:tour|visa|travel)|\baccompan|"
    r"\bpermanent resident|\bresidence permit|\bresidency\b|\bcrew\b|"
    r"(?<!need )(?<!needs )(?<!require )(?<!requires )\b(?:only|solely|exclusively)\b(?!\s+(?:a |an |the |their |your )?(?:valid |current )?passport)|"
    r"\b(?:solo|sólo|solamente|únicamente|unicamente|seulement|uniquement|nur|apenas|somente|hanya|chỉ)\b|仅限|僅限|限り|のみ|에 한해|에 한함|"
    r"\b(?:biometric|e-?passports?|electronic passports?|machine[- ]readable|chip)\b|biométrique|biométrico|biometrisch|biometrik|生物识别|電子旅券|전자여권|"
    r"\bsi\b[^.;\n]{0,30}\bpasaporte|"
    # "if you hold a passport from one of the following countries" opens a
    # list; "if your passport is biometric" states a condition.
    r"\bif\b(?![^.;\n]{0,40}\bpassports?\s+(?:from|of|issued by)\s+(?:any of |one of )?(?:the following|these|those|one of the)\b)[^.;\n]{0,40}\bpassport")
_CONDITIONAL_MARK_RE = re.compile(
    _HOLD_OBJECT + r"|\bvalid (?:visa|permit|residence|residency)|\bregist(?:er|ered|ration)\b" + _CONDITIONAL_CORE, re.I)
# The same gate for a visa, an arrival visa or an authorisation: there the
# document the traveller must hold is the verdict itself ("must hold a valid
# visa", "must complete pre-arrival registration"), so only a holding that
# is not the requirement, and no registration word, marks a condition.
_HOLD_REQUIREMENT_GUARD = (r"(?<!must )(?<!should )(?<!shall )(?<!need to )(?<!needs to )(?<!have to )(?<!has to )"
                           r"(?<!required to )(?<!obliged to )(?<!able to )(?<!doivent )(?<!deben )(?<!devem )")
_CONDITIONAL_MARK_OTHER_RE = re.compile(_HOLD_REQUIREMENT_GUARD + _HOLD_OBJECT + _CONDITIONAL_CORE, re.I)
# The subcategories under which a conditional sentence may stand. Every
# other subcategory of every disposition is unconditional: a condition,
# footnote or example in its deciding sentence refuses it.
_CONDITIONAL_DETAILS = frozenset({'conditional_visa_free', 'transit_visa_free'})


def _conditional_marker(low, value):
    pattern = _CONDITIONAL_MARK_RE if value == 'VISA_EXEMPT' else _CONDITIONAL_MARK_OTHER_RE
    return bool(pattern.search(low))


_EXAMPLE_BEFORE_RE = re.compile(r"(?:\bfor example|\be\.g\.?|\bsuch as|\bpar exemple|\bpor ejemplo|\bzum beispiel|例如|例えば|예를 들어)\s*[,:]?\s*(?:[a-z]+\s+){0,3}$", re.I)
# A footnote mark right after the nationality's own name ("United States of
# America*", "Japan (1)") or closing the verdict cell ("No necesita Visa (4)").
_FOOTNOTE_AFTER_RE = re.compile(r"\s?(?:[*†‡]+|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\(\d{1,2}\)|\[\d{1,2}\]|\d{1,2}\))")
_FOOTNOTE_END_RE = re.compile(r"(\(\d{1,2}\)|\[\d{1,2}\])\s*[.:;]?\s*$")
# A sentence scoped to transit proves nothing for another purpose. Transit
# beside tourism or business in one purpose list is not a transit scope.
_TRANSIT_RE = re.compile(r"\bin (?:direct |immediate )?transit\b|\btransit(?:ing)? (?:through|via|at)\b|\btransit[- ]without[- ]visa|\btwov\b|"
                         r"\bvisa[- ]free transit|\btransit (?:visa )?(?:exemption|waiver|policy|passengers?|facility|scheme)|"
                         r"\b(?:\d+|twenty[- ]four|seventy[- ]two)[- ]hours? (?:visa[- ]free )?transit|过境免签|過境免簽|过境|過境|通過|乗り継ぎ|환승|통과|quá cảnh|"
                         r"\btr[aâ]nsito\b|\btransitreis|транзит", re.I)
# The purposes an official sentence scopes a rule to, in the languages of
# the captured pages. A sentence that names one or more purposes proves the
# verdict for those purposes only; a sentence naming none, or naming every
# purpose, is general. Entering, leaving and staying are not purposes: the
# bare verbs describe the travel, so they never rescue a transit-only rule.
_PURPOSE_PATTERNS = {key: re.compile(pattern, re.I) for key, pattern in {
    'tourism': r"\btouris\w*|\bleisure\b|\bholidays?\b|\bvacations?\b|\bsightseeing\b|\btourisme\b|\bturismo\b|\bturístic\w*|\bturistic\w*|"
               r"\burlaub\b|\bwisata\b|\bpelancong\w*|\bmelancong\b|\bdu lịch\b|\btoerisme\b|\bvakantie\b|旅游|旅遊|观光|觀光|観光|관광|ท่องเที่ยว|туризм\w*|туристич\w*|отдых\w*",
    'visit': r"\bvisitors?\b|\bvisits?\b(?!\s+(?:the\s+|our\s+)?(?:website|web ?site|webpage|page|portal|link|embassy|consulate|office|mission|centre|center))|"
             r"\bvisiting\b(?!\s+(?:the\s+|our\s+)?(?:website|web ?site|webpage|page|portal|link|embassy|consulate|office|mission|centre|center))|"
             r"\bsocial visit|\bvisite\b|\bvisita\b|\bvisitas?\b|\bbesuch\w*|\bkunjungan\b|\bmelawat\b|\bthăm\b|\bviếng thăm\b|訪問|访问|방문|เยี่ยม|เยือน|посещени\w*|визит\w*",
    'business': r"\bbusiness\b|\bcommerc\w*|\btrade\b|\baffaires\b|\bnegocios?\b|\bnegócios?\b|\bgeschäft\w*|\baffari\b|\bbisnis\b|\bperniagaan\b|\bkinh doanh\b|\bthương mại\b|\bzaken\b|"
                r"商务|商務|经商|經商|ビジネス|商用|사업|비즈니스|상용|ธุรกิจ|деловы\w*|бизнес\w*|коммерческ\w*",
    'family_visit': r"\bfamily (?:visits?|reunion|reunification)\b|\bvisit(?:ing|s)? (?:family|relatives|friends)\b|\brelatives\b|\bfriends\b|\bvisite familiale\b|\bvisita familiar\b|\bfamiliares\b|"
                    r"\bfamilienbesuch\w*|\bverwandt\w*|\bthăm thân\b|\bkunjungan keluarga\b|探亲|探親|访友|訪友|親族訪問|친지 방문|가족 방문|친척|เยี่ยมญาติ|родственник\w*",
    'transit': _TRANSIT_RE.pattern,
    'study': r"\bstud(?:y|ies|ying|ent|ents)\b|\beducation(?:al)?\b|\benrol\w*|\bdegree programmes?\b|\bdegree programs?\b|\bétudes?\b|\bétudiants?\b|\bestudios?\b|\bestudiantes?\b|"
             r"\bstudium\b|\bstudieren\b|\bstudi\b|\bbelajar\b|\bpelajar\b|\bdu học\b|\bhọc tập\b|留学|留學|学习|學習|就学|유학|학업|ศึกษา|เรียน|учеб\w*|обучени\w*|студент\w*",
    'work': r"\b(?:for|to) work\b|\bworking\b(?! (?:holiday|days?|hours?|weeks?))|\bemployment\b|\bemployed\b|\bwork (?:permits?|visas?|purposes?)\b|\btravail\w*|\btrabaj\w*|\btrabalh\w*|"
            r"\barbeit\w*|\blavor\w*|\bbekerja\b|\bpekerjaan\b|\blàm việc\b|\blao động\b|工作|就业|就業|就労|취업|근로|ทำงาน|работ\w*|трудов\w*",
    'medical': r"\bmedical\b|\btreatment\b|\bhospital\w*|\bhealth ?care\b|\bmédical\w*|\bmédic\w*|\bmedizinisch\w*|\bmedis\b|\bpengobatan\b|\by tế\b|\bchữa bệnh\b|\bkhám bệnh\b|"
               r"医疗|醫療|就医|就醫|医療|治療|의료|치료|รักษา|แพทย์|лечени\w*|медицинск\w*",
    'official': r"\bofficial (?:missions?|visits?|duty|duties|business|purposes?|delegations?|trips?)\b|\bgovernment (?:missions?|business|delegations?)\b|\bdiplomatic visits?\b|"
                r"\bmission officielle\b|\bmisión oficial\b|\bmissão oficial\b|\bdienstreise\b|\bcông vụ\b|\btugas (?:resmi|dinas)\b|公务访问|公務訪問|公务活动|公務活動|공무|공식 방문|ราชการ|"
                r"официальн\w* (?:визит\w*|мисси\w*|цел\w*)|служебн\w* (?:поездк\w*|цел\w*)",
    'conference': r"\bconferences?\b|\bseminars?\b|\bconventions?\b(?! travel document)|\bexchanges?\b|\bconférence\b|\bconferencia\b|\bconferência\b|\bkonferenz\b|\bkonferensi\b|"
                  r"\bhội nghị\b|\bhội thảo\b|\btrao đổi\b|会议|會議|交流|会議|학술|회의|교류|ประชุม|конференци\w*|обмен\w*",
    'religious': r"\bpilgrim\w*|\breligious\b|\bhajj\b|\bumrah\b|\bpèlerinage\b|\bperegrin\w*|\bziarah\b|\bhành hương\b|朝圣|朝聖|巡礼|성지순례|แสวงบุญ|паломнич\w*",
    'journalism': r"\bjournalis\w*|\bpress\b|\breporters?\b|\bjournaliste\w*|\bperiodist\w*|\bjornalist\w*|\bjurnalis\w*|\bbáo chí\b|记者|記者|報道|기자|언론|สื่อมวลชน|журналист\w*",
}.items()}
# Wording that covers every purpose, or the short stay every purpose shares.
_GENERAL_PURPOSE_RE = re.compile(
    r"\b(?:all|any|whatever the|every|all types of|irrespective of the|regardless of the) purposes?\b|\bfor any purpose of entry\b|"
    r"\bshort[- ](?:stays?|term stays?|term visits?)\b|\bcourt séjour\b|\bséjour\b|\bestancia\b|\bestadia\b|\bkurzaufenthalt\w*|\bkurzfristig\w*|\bsoggiorno\b|"
    r"\btous (?:les )?motifs\b|\bcualquier (?:motivo|propósito|fin)\b|\btodos os (?:fins|propósitos)\b|\bqualquer (?:motivo|fim)\b|\bjeden zweck\b|\balle zwecke\b|"
    r"\bbất kỳ mục đích\b|\bmọi mục đích\b|\bsemua tujuan\b|\bapa ?pun tujuan\b|任何目的|所有目的|各种目的|一切目的|短期滞在|短期停留|모든 목적|어떤 목적|단기 체류|"
    r"ทุกวัตถุประสงค์|любых целях|всех целей|независимо от цели", re.I)
# A rule stated for entry, exit and transit alike ("when entering, exiting
# and transiting through the territory") is not a transit-only rule.
_ENTRY_EXIT_TRANSIT_RE = re.compile(
    r"(?:\b(?:enter\w*|entry|entrance|einreise|entrée|entrada|ingresso|ulaz\w*|vstup\w*|nhập cảnh|masuk|въезд\w*)\b|入境|入国|입국)[^.;\n]{0,25}"
    r"(?:\b(?:exit\w*|leav\w*|departure|ausreise|sortie|salida|saída|uscita|izlaz\w*|výstup\w*|xuất cảnh|keluar|выезд\w*)\b|出境|出国|출국)[^.;\n]{0,25}"
    r"(?:\btransit\w*|\bdurchreise|\bprelaz\w*|\bquá cảnh|\bтранзит\w*|过境|過境|通過|통과|환승)", re.I)
# The purposes a route purpose is proved by. Visit wording covers a tourist
# and a family visit alike; nothing else covers another purpose.
_ROUTE_PURPOSES = {'tourism': {'tourism', 'visit'}, 'family_visit': {'family_visit', 'visit'}, 'business': {'business'},
                   'transit': {'transit'}, 'study': {'study'}, 'work': {'work'}, 'medical': {'medical'},
                   'official': {'official'}, 'conference': {'conference'}, 'religious': {'religious'},
                   'journalism': {'journalism'}}
# A rule for a longer stay than the exemption covers ("must obtain a visa
# for stays longer than 30 days") does not contradict the exemption.
_LONGER_STAY_RE = re.compile(
    r"\b(?:longer|exceed\w*|more than|beyond|over|in excess of|extension|extend\w*|prolong\w*|above)\b[^.;\n]{0,30}\b(?:days?|months?|weeks?|stays?|period)\b|"
    r"\bstays? (?:longer|exceeding|of more than|beyond|over)\b|\blonger (?:stays?|periods?|visits?)\b|\bwish(?:ing|es)? to stay longer\b|"
    r"超过|超過|以上|逾|plus de \d+ jours|más de \d+ días|mais de \d+ dias|länger als|hơn \d+ ngày|lebih dari \d+ hari|이상|초과|เกิน", re.I)


def _stated_purposes(low):
    stated = {key for key, pattern in _PURPOSE_PATTERNS.items() if pattern.search(low)}
    if 'transit' in stated and _ENTRY_EXIT_TRANSIT_RE.search(low):
        stated.discard('transit')
    return stated


def _purpose_block(low, purpose):
    """Why the purposes the normalized sentence states do not cover the
    route's purpose, or None when the sentence is general or names it."""
    if _GENERAL_PURPOSE_RE.search(low):
        return None
    stated = _stated_purposes(low)
    if not stated:
        return None
    accepted = _ROUTE_PURPOSES.get(purpose, {purpose})
    if stated & accepted:
        return None
    return 'scoped to ' + ', '.join(sorted(stated)) + ', not the route purpose ' + str(purpose)
# Passport classes an official page scopes a rule to. A sentence scoped to a
# class the route is not proves nothing for the route unless it also names
# the route's own class ("national passport (diplomatic, official, or
# ordinary)", "all types of passports").
_DOCUMENT_CLASSES = {
    'diplomatic_passport': r"diplomatic (?:passports?|(?:or|and|/) ?(?:official|service|special)|passport holders?)|passeports? diplomatiques?|"
                           r"pasaportes? diplom[aá]ticos?|passaport[oi] diplomatic[oi]|"
                           r"diplomatenp[aä]ss(?:e|es)?|外交护照|外交護照|外交旅券|hộ chiếu ngoại giao|paspor diplomatik|외교관 ?여권|외교여권|"
                           r"หนังสือเดินทางทูต|дипломатическ\w* паспорт\w*",
    'official_passport': r"official (?:passports?|or service|and service|/ ?service)|service passports?|passeports? (?:officiels?|de service)|"
                         r"pasaportes? (?:oficial(?:es)?|de servicio)|passaport[oi] (?:ufficial[ei]|di servizio)|dienstp[aä]ss(?:e|es)?|"
                         r"公务护照|公務護照|公務旅券|公用旅券|hộ chiếu công vụ|paspor dinas|관용 ?여권|หนังสือเดินทางราชการ|служебн\w* паспорт\w*",
    'special_passport': r"special passports?|laissez[- ]passer|\bbn ?\(?o\)?\b|british national \(?overseas\)?|document of identity|"
                        r"emergency passports?|passeports? spéciaux|pasaportes? especial(?:es)?|特别护照|特別護照|hộ chiếu đặc biệt|paspor khusus",
    # Travel documents that are not a national passport at all: refugee and
    # Convention documents, alien's passports, certificates of identity,
    # seafarer and crew documents, re-entry permits.
    'travel_document': r"refugee(?:s|'s|s'|’s)? travel documents?|convention travel documents?|travel documents? (?:for|issued to) (?:refugees|stateless)|"
                       r"\b(?:stateless|refugees?)\b|alien(?:s|'s|s'|’s)? passports?|passports? for aliens|foreigner(?:s|'s|s'|’s)? passports?|"
                       r"certificates? of identity|identity certificates?|seafarer(?:s|'s|s'|’s)? (?:identity documents?|identity cards?|books?|cards?)|"
                       r"seam[ae]n(?:'s|s'|’s)? (?:books?|cards?|identity documents?)|crew member(?:s|'s|s'|’s)? certificates?|crew cards?|re-?entry permits?|"
                       r"travel documents? in lieu of|(?:emergency|temporary) travel documents?|titres? de voyage|documents? de voyage|"
                       r"passeports? (?:pour )?étrangers?|passeport d[’']étranger|documentos? de viaje|pasaportes? de extranjero|documentos? de viagem|"
                       r"passaport[oi] per stranieri|documento di viaggio|reiseausweis(?:e)?|fremdenp[aä]ss(?:e)?|reisedokument(?:e)?|"
                       r"旅行证|旅行證|难民|難民|无国籍|無国籍|外国人护照|外國人護照|再入国許可|再入國許可|再入境许可|船员证|船員證|海员证|海員證|船員手帳|"
                       r"재입국허가|난민여행증명서|외국인여권|선원신분증명서|선원수첩|giấy thông hành|giấy tờ đi lại|hộ chiếu (?:người )?nước ngoài|thuyền viên|"
                       r"surat perjalanan laksana paspor|dokumen perjalanan|paspor orang asing|buku pelaut|เอกสารเดินทาง|หนังสือเดินทางคนต่างด้าว|หนังสือคนประจำเรือ|"
                       r"проездно\w* документ\w*|паспорт\w* иностранц\w*|удостоверени\w* личности моряка|паспорт\w* моряка",
}
_DOCUMENT_CLASSES['service_passport'] = _DOCUMENT_CLASSES['official_passport']
_DOCUMENT_CLASS_LABELS = {'diplomatic_passport': 'diplomatic passports', 'official_passport': 'official passports',
                          'service_passport': 'service passports', 'special_passport': 'special passports',
                          'travel_document': 'travel documents other than a passport', 'ordinary_passport': 'ordinary passports'}
# "Holders of <some document> ..." scopes the rule to that document. When
# the document is not the route's own passport class the sentence proves
# nothing for the route, whatever the document is called.
_HOLDERS_OF_DOCUMENT_RE = re.compile(
    r"\b(?:holders?|bearers?|titulaires?|titulares|portadores|inhaber|pemegang|người mang|người có) (?:of |de |des |du |d[’']|von |eines? |einer )?"
    r"(?:a |an |the |their |valid |a valid |any |all )?(?:[a-z\-’']+ ){0,3}"
    r"(?:travel documents?|identity documents?|identity cards?|id cards?|certificates?|seaman'?s'? books?|seafarer'?s'? books?)\b", re.I)
_ORDINARY_CLASS_RE = re.compile(r"\b(?:ordinary|regular|normal|common|standard)\b(?: (?:and|or) (?:ordinary|regular|normal|official|service))? passports?|"
                                # The Special Administrative Region passport is Hong Kong's and Macao's ordinary passport.
                                r"\b(?:hong kong |hk|macao |macau )?(?:sar|s\.a\.r\.|special administrative region)(?: \([a-z. ]{1,10}\))? passports?|\bhksar passports?|"
                                r"\bordinary\b|passeports? ordinaires?|pasaportes? ordinarios?|passaport[oi] ordinar[io]|gewöhnlich\w* (?:reise)?p[aä]ss|"
                                r"普通护照|普通護照|一般旅券|普通旅券|hộ chiếu phổ thông|paspor biasa|일반 ?여권|หนังสือเดินทางธรรมดา|общегражданск\w*|"
                                r"all types of passports?|any type of passports?|tous (?:les )?types de passeports?|todos los tipos de pasaporte|"
                                r"所有类型|所有類型|全ての旅券|모든 여권", re.I)


def _norm(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def quote_literal(quote, text):
    """The whole quote must occur in the captured text. Whitespace is the
    only thing a capture may change (a list line "9. Australia" renders as
    "9.Australia" in another reader), so a second comparison ignores it."""
    from app.visa_snapshot.evidence_validator import quote_in_text
    if quote_in_text(quote, text):
        return True
    q = re.sub(r'\s+', '', _norm(quote)); t = re.sub(r'\s+', '', _norm(text))
    return bool(q) and len(q) >= 8 and q in t


# Names of the station nationalities in the languages of the destinations
# whose official pages are read most (Russian, French, Spanish, Portuguese,
# German, Italian, Vietnamese, Thai, Indonesian/Malay, Japanese, Korean,
# Chinese, Arabic, Turkish). They extend, never replace, the validator's own
# English and Chinese anchors.
_EXTRA_ALIASES = {
    'HKG': ('гонконг', 'hong-kong', 'hongkong', '홍콩', '香港', 'hồng kông', 'ฮ่องกง', 'هونغ كونغ'),
    'TWN': ('тайвань', 'taïwan', 'taiwán', '대만', '台灣', '台湾', 'đài loan', 'ไต้หวัน', 'تايوان'),
    'JPN': ('япония', 'japon', 'japón', 'japão', 'giappone', '일본', '日本', 'nhật bản', 'ญี่ปุ่น', 'jepang', 'اليابان', 'japonya'),
    'KOR': ('корея', 'республика корея', 'corée', 'corea', 'coreia', '한국', '대한민국', '韓国', '韩国', '韓國', 'hàn quốc', 'đại hàn dân quốc', 'đại hàn', 'เกาหลี', 'korea selatan', 'كوريا', 'güney kore'),
    'USA': ('сша', 'соединенные штаты', 'états-unis', 'etats-unis', 'estados unidos', 'stati uniti', 'vereinigte staaten', '미국', 'アメリカ', '米国', '美国', '美國', 'hoa kỳ', 'สหรัฐ', 'amerika serikat', 'الولايات المتحدة', 'amerika birleşik devletleri',
            'usa nationals', 'usa national', 'usa citizens', 'usa citizen', 'usa passport', 'the usa', 'ee.uu', 'eeuu', 'spojené štáty americké', 'spojené státy americké',
            'sjedinjene američke države', 'sjedinjenih američkih država', 'verenigde staten', 'stany zjednoczone', 'アメリカ合衆国',
            'united states of america', 'estados unidos de américa', 'estados unidos de america', 'estados unidos da américa', 'estados unidos da america',
            "états-unis d'amérique", 'états-unis d’amérique', "etats-unis d'amerique", 'etats-unis d’amerique', "stati uniti d'america", 'stati uniti d’america',
            'vereinigte staaten von amerika', 'соединенные штаты америки'),
    'THA': ('таиланд', 'thaïlande', 'tailandia', 'tailândia', '태국', 'タイ', '泰国', '泰國', '泰方', 'thái lan', 'ไทย', 'تايلاند', 'tayland'),
    'SGP': ('сингапур', 'singapour', 'singapur', 'singapura', '싱가포르', 'シンガポール', '新加坡', 'สิงคโปร์', 'سنغافورة'),
    'MYS': ('малайзия', 'malaisie', 'malasia', 'malásia', '말레이시아', 'マレーシア', '马来西亚', '馬來西亞', 'มาเลเซีย', 'ماليزيا', 'malezya'),
    'GBR': ('великобритания', 'соединенное королевство', 'royaume-uni', 'reino unido', 'regno unito', 'vereinigtes königreich', 'großbritannien', '영국', 'イギリス', '英国', '英國', 'anh', 'vương quốc anh', 'สหราชอาณาจักร', 'britania raya', 'inggris', 'المملكة المتحدة', 'birleşik krallık', 'great britain', 'britain', 'royaume uni', 'verenigd koninkrijk', 'velika britanija', 'ujedinjeno kraljevstvo', 'spojené kráľovstvo', 'wielka brytania', 'uk'),
    'RUS': ('россия', 'russie', 'rusia', 'rússia', '러시아', 'ロシア', '俄罗斯', '俄羅斯', 'nga', 'liên bang nga', 'รัสเซีย', 'روسيا', 'rusya'),
    'AUS': ('австралия', 'australie', 'australien', '호주', 'オーストラリア', '澳大利亚', '澳大利亞', '澳洲', 'úc', 'ออสเตรเลีย', 'أستراليا', 'avustralya'),
    'IDN': ('индонезия', 'indonésie', 'indonesien', '인도네시아', 'インドネシア', '印度尼西亚', '印尼', 'อินโดนีเซีย', 'إندونيسيا', 'endonezya'),
    'PHL': ('филиппины', 'philippines', 'filipinas', 'philippinen', 'filippine', '필리핀', 'フィリピン', '菲律宾', '菲律賓', 'ฟิลิปปินส์', 'filipina', 'الفلبين', 'filipinler'),
    'FRA': ('франция', 'francia', 'frança', 'frankreich', '프랑스', 'フランス', '法国', '法國', 'pháp', 'ฝรั่งเศส', 'perancis', 'prancis', 'فرنسا', 'fransa'),
    'VNM': ('вьетнам', 'viet nam', 'việt nam', 'vietnã', '베트남', 'ベトナム', '越南', 'เวียดนาม', 'فيتنام'),
    'ESP': ('испания', 'espagne', 'españa', 'espanha', 'spanien', 'spagna', '스페인', 'スペイン', '西班牙', 'tây ban nha', 'สเปน', 'spanyol', 'إسبانيا', 'ispanya'),
    'IND': ('индия', 'inde', 'índia', 'indien', '인도', 'インド', '印度', 'ấn độ', 'อินเดีย', 'الهند', 'hindistan'),
    'CAN': ('канада', 'canadá', 'kanada', '캐나다', 'カナダ', '加拿大', 'كندا'),
    'CHN': ('китай', 'кнр', 'chine', 'china', '중국', '中国', '中國', 'trung quốc', 'จีน', 'tiongkok', 'الصين', 'çin'),
    # The other members of the groups in _GROUPS. A group sentence can only
    # be read when every carve-out in it can be resolved by name, so each
    # member needs its own names here even when it is not a station
    # nationality. Names, not inflecting demonyms. Those sit in the stems.
    'AUT': ('austria', 'autriche', 'österreich', 'oesterreich', 'áustria'),
    'BEL': ('belgium', 'belgique', 'belgië', 'belgien', 'bélgica', 'belgio'),
    'BGR': ('bulgaria', 'bulgarie', 'bulgarien', 'bulgária'),
    'HRV': ('croatia', 'croatie', 'kroatien', 'croacia', 'croácia', 'croazia', 'hrvatska'),
    'CYP': ('cyprus', 'chypre', 'zypern', 'chipre', 'cipro'),
    'CZE': ('czech republic', 'czechia', 'république tchèque', 'tchéquie', 'tschechien', 'tschechische republik',
            'república checa', 'chequia', 'repubblica ceca', 'česko', 'česká republika'),
    'DNK': ('denmark', 'danemark', 'dänemark', 'dinamarca', 'danimarca', 'danmark'),
    'EST': ('estonia', 'estonie', 'estland', 'estónia', 'estônia', 'eesti'),
    'FIN': ('finland', 'finlande', 'finnland', 'finlandia', 'finlândia', 'suomi'),
    'DEU': ('germany', 'german', 'germans', 'allemagne', 'deutschland', 'alemania', 'alemanha', 'germania'),
    'GRC': ('greece', 'grèce', 'griechenland', 'grecia', 'grécia', 'hellas', 'hellenic republic'),
    'HUN': ('hungary', 'hongrie', 'ungarn', 'hungría', 'hungria', 'ungheria', 'magyarország'),
    'IRL': ('ireland', 'irlande', 'irland', 'irlanda', 'éire'),
    'ITA': ('italy', 'italie', 'italien', 'italia', 'itália'),
    'LVA': ('latvia', 'lettonie', 'lettland', 'letonia', 'letónia', 'lettonia', 'latvija'),
    'LTU': ('lithuania', 'lituanie', 'litauen', 'lituania', 'lituânia', 'lietuva'),
    'LUX': ('luxembourg', 'luxemburg', 'luxemburgo', 'lussemburgo'),
    'MLT': ('malta', 'malte'),
    'NLD': ('netherlands', 'the netherlands', 'holland', 'pays-bas', 'niederlande', 'países bajos', 'paises bajos',
            'países baixos', 'paesi bassi', 'nederland'),
    'POL': ('poland', 'polish', 'pologne', 'polen', 'polonia', 'polónia', 'polônia', 'polska'),
    'PRT': ('portugal',),
    'ROU': ('romania', 'romanian', 'romanians', 'roumanie', 'rumänien', 'rumanía', 'rumania', 'roménia', 'romênia', 'românia'),
    'SVK': ('slovakia', 'slovak republic', 'slovaquie', 'slowakei', 'eslovaquia', 'eslováquia', 'slovacchia', 'slovensko'),
    'SVN': ('slovenia', 'slovénie', 'slowenien', 'eslovenia', 'eslovénia', 'slovenija'),
    'SWE': ('sweden', 'suède', 'schweden', 'suecia', 'suécia', 'svezia', 'sverige'),
    'BRN': ('brunei', 'brunei darussalam', 'brunéi', 'ブルネイ', '文莱', '汶萊', '브루나이'),
    'KHM': ('cambodge', 'camboya', 'camboja', 'kambodscha', 'cambogia', 'kamboja', 'campuchia', 'カンボジア', '柬埔寨', '캄보디아'),
    'LAO': ('laos', 'lao pdr', "lao people's democratic republic", 'lao', 'lào', 'ラオス', '老挝', '寮國', '라오스'),
    'MMR': ('myanmar', 'burma', 'birmanie', 'birmania', 'mianmar', 'ミャンマー', '缅甸', '緬甸', '미얀마'),
    'TLS': ('timor-leste', 'timor leste', 'east timor', 'timor oriental', 'osttimor', 'timor est', 'timor-est',
            '東ティモール', '东帝汶', '東帝汶', '동티모르'),
    # Names the captured pages carve out beside the station nationalities
    # ("except those nationals from the FSM, RMI and USA", "except New
    # Zealand citizens"): a carve-out is read only through a known name.
    'FSM': ('micronesia', 'federated states of micronesia', 'fsm', 'micronésie', 'mikronesien', 'ミクロネシア', '密克罗尼西亚', '密克羅尼西亞', '미크로네시아'),
    'MHL': ('marshall islands', 'republic of the marshall islands', 'rmi', 'îles marshall', 'islas marshall', 'marshallinseln',
            'マーシャル諸島', '马绍尔群岛', '馬紹爾群島', '마셜 제도'),
    'NZL': ('new zealand', 'nz', 'nouvelle-zélande', 'nouvelle zélande', 'nueva zelanda', 'nova zelândia', 'neuseeland', 'nuova zelanda',
            'selandia baru', 'ニュージーランド', '新西兰', '紐西蘭', '뉴질랜드', 'นิวซีแลนด์', 'новая зеландия', 'новой зеландии'),
}


# Demonym stems whose endings inflect for gender, number or case on official
# pages ("ressortissants indiens", "ciudadanos rusos", "граждане России").
# A stem matches with up to five further letters, so one stem covers
# indien/indienne/indiens/indiennes without listing every form. Stems are
# the adjective root only; a country name that is already an alias is not
# repeated here. A stem that is itself a complete word and swallows other
# words with that allowance ("indian" in Indiana, "frances" in Francesca,
# "russe" in Russell) is listed as fixed aliases instead.
_DEMONYM_STEMS = {
    'HKG': ('hongkongais', 'hongkon', 'hongkonger', 'гонконг'),
    'TWN': ('taïwanais', 'taiwanais', 'taiwan', 'taiwanisch', 'тайван'),
    'JPN': ('japonais', 'japon', 'giapponese', 'japanisch', 'japanese', 'япон'),
    'KOR': ('coréen', 'coreen', 'coreano', 'koreanisch', 'korean', 'корей', 'кореи'),
    'USA': ('américain', 'americain', 'estadounidense', 'norteamericano', 'amerikanisch', 'американ', 'états-unien', 'etats-unien'),
    'THA': ('thaïlandais', 'thailandais', 'tailandés', 'tailandes', 'tailandese', 'thailändisch', 'таиланд', 'тайск'),
    'SGP': ('singapourien', 'singapurense', 'singaporean', 'singapurisch', 'сингапур'),
    'MYS': ('malaisien', 'malasio', 'malese', 'malaysisch', 'malaysian', 'малайзи'),
    'GBR': ('britannique', 'británico', 'britanico', 'britannico', 'britisch', 'british', 'britânico', 'британ', 'великобритани'),
    'RUS': ('ruso', 'rusa', 'russo', 'russisch', 'russian', 'росси', 'русск'),
    'AUS': ('australien', 'australiano', 'australisch', 'australian', 'австрали'),
    'IDN': ('indonésien', 'indonesien', 'indonesio', 'indonesiano', 'indonesisch', 'indonesian', 'индонези'),
    'PHL': ('philippin', 'filipino', 'filipina', 'filippino', 'philippinisch', 'филиппин'),
    'FRA': ('français', 'francais', 'francés', 'französisch', 'french', 'francês', 'франци', 'француз'),
    'VNM': ('vietnamien', 'vietnamita', 'vietnamesisch', 'vietnamese', 'вьетнам'),
    'ESP': ('espagnol', 'español', 'espanol', 'spagnolo', 'spanisch', 'spanish', 'espanhol', 'испан'),
    'IND': ('indien', 'indio', 'indiano', 'indisch', 'инди'),
    'CAN': ('canadien', 'canadiense', 'canadese', 'kanadisch', 'canadian', 'canadiano', 'канад'),
    'CHN': ('chinois', 'chino', 'cinese', 'chinesisch', 'chinese', 'chinês', 'chines', 'китай', 'китая'),
    'AUT': ('austrian', 'autrichien', 'austriac', 'österreichisch', 'austríac'),
    'BEL': ('belgian', 'belge', 'belgisch', 'belga'),
    'BGR': ('bulgarian', 'bulgare', 'bulgarisch', 'búlgar', 'bulgar'),
    'HRV': ('croatian', 'croate', 'kroatisch', 'croat'),
    'CYP': ('cypriot', 'chypriote', 'zypriotisch', 'chipriot', 'cipriot'),
    'CZE': ('czech', 'tchèque', 'tschechisch', 'checo', 'ceco', 'cechi'),
    'DNK': ('danish', 'danois', 'dänisch', 'danés', 'danes', 'danese'),
    'EST': ('estonian', 'estonien', 'estnisch', 'estonio', 'estone'),
    'FIN': ('finnish', 'finlandais', 'finnisch', 'finlandés', 'finlandes', 'finlandese'),
    'DEU': ('allemand', 'deutsch', 'alemán', 'aleman', 'alemã', 'tedesc'),
    'GRC': ('greek', 'grec', 'griechisch', 'griego', 'grego'),
    'HUN': ('hungarian', 'hongrois', 'ungarisch', 'húngar', 'hungar', 'ungheres', 'magyar'),
    'IRL': ('irish', 'irlandais', 'irisch', 'irlandés', 'irlandes', 'irlandese'),
    'ITA': ('italian', 'italien', 'italienisch', 'italiano'),
    'LVA': ('latvian', 'letton', 'lettisch', 'letón', 'leton', 'lettone'),
    'LTU': ('lithuanian', 'lituanien', 'litauisch', 'lituano'),
    'LUX': ('luxembourgish', 'luxembourgeois', 'luxemburgisch', 'luxemburgués', 'luxemburgues', 'lussemburghes'),
    'MLT': ('maltese', 'maltais', 'maltesisch', 'maltés', 'maltes'),
    'NLD': ('dutch', 'néerlandais', 'neerlandais', 'niederländisch', 'neerlandés', 'neerlandes', 'holandés', 'holandes', 'olandes', 'nederlands'),
    'POL': ('polonais', 'polnisch', 'polac'),
    'PRT': ('portuguese', 'portugais', 'portugiesisch', 'portugués', 'portugues', 'português', 'portoghes'),
    'ROU': ('roumain', 'rumänisch', 'rumano', 'romen'),
    'SVK': ('slovak', 'slovaque', 'slowakisch', 'eslovac', 'slovacc'),
    'SVN': ('slovenian', 'slovene', 'slovène', 'slowenisch', 'esloven'),
    'SWE': ('swedish', 'suédois', 'suedois', 'schwedisch', 'sueco', 'svedes'),
    'BRN': ('bruneian', 'brunéien', 'bruneien'),
    'KHM': ('cambodgien', 'camboyan', 'cambojan', 'kambodschan', 'cambogian', 'khmer'),
    'LAO': ('laotian', 'laotien', 'laosian', 'laotisch'),
    'MMR': ('burmese', 'birman', 'myanmarese'),
    'TLS': ('timorese', 'timorais', 'timorens'),
    'FSM': ('micronesian',),
    'MHL': ('marshallese',),
    'NZL': ('new zealander', 'néo-zélandais', 'neo-zelandais', 'neozelandés', 'neozelandes', 'neuseeländisch', 'neozelandese', 'новозеланд'),
}
# Inflected forms of the stems that were retired for colliding with other
# words. They are fixed aliases: whole words only.
_INFLECTED_ALIASES = {
    'IND': ('indians',),
    'FRA': ('frances', 'francesa', 'francesas', 'franceses', 'francese', 'francesi'),
    'RUS': ('russe', 'russes'),
}
_NAME_PATTERNS = {}
_KNOWN_NATIONALITIES = []


def _aliases(nat):
    from app.visa_snapshot.evidence_validator import _NATIONALITY_NAMES
    return sorted({_norm(a) for a in (*_NATIONALITY_NAMES.get(nat, ()), *_EXTRA_ALIASES.get(nat, ()),
                                       *_INFLECTED_ALIASES.get(nat, ())) if _norm(a)})


def _known_nationalities():
    """Every nationality the converter can name: the validator's table plus
    the converter's own alias and stem tables."""
    if not _KNOWN_NATIONALITIES:
        from app.visa_snapshot.evidence_validator import _NATIONALITY_NAMES
        _KNOWN_NATIONALITIES.extend(sorted(set(_NATIONALITY_NAMES) | set(_EXTRA_ALIASES) | set(_DEMONYM_STEMS)))
    return _KNOWN_NATIONALITIES


def _name_pattern(nat):
    """One compiled pattern for "this nationality is named here": the fixed
    aliases (ASCII-letter boundaries, as the validator reads them) plus the
    inflecting demonym stems (letter boundaries in any script)."""
    pattern = _NAME_PATTERNS.get(nat)
    if pattern is None:
        # An alias or a stem inside a hyphenated or regional compound
        # ("sud-américains", "Latin American citizens") names a region, not
        # this nationality. The longest alias is tried first so "US national"
        # is read whole rather than as the bare "US".
        pattern = _NAME_PATTERNS[nat] = _build_name_pattern(nat)
    return pattern


_COMPOUND_PREFIXES = ('-', 'sud ', 'nord ', 'south ', 'north ', 'latin ', 'latino ',
                      'hispano ', 'afro ', 'anglo ', 'central ', 'indo ')


def _compound_guard(aliases):
    """Lookbehinds that refuse a regional compound before the name. A prefix
    that opens one of the nationality's own names ("south " for "south
    korea") is part of the name, so "South Korean" still names Korea while
    "North Korean" does not."""
    return ''.join(f'(?<!{p})' for p in _COMPOUND_PREFIXES if not any(a.startswith(p) for a in aliases))
# A mention right after a negating prefix ("non-US citizens", "other than
# Indian nationals") speaks about everyone but this nationality.
_NEGATED_PREFIX_RE = re.compile(
    r"(?:\bnon[- ]?|\bnicht[- ]|非|"
    r"\b(?:other than|excluding|except(?:ing)?(?: for)?|not|no|apart from|aside from|with the (?:\w+ )?exception of|to the exclusion of|"
    r"not including|but not|barring|unless|sauf|hors|hormis|excepté|[àa] l['’]exception d(?:e|es|u)|excepto|salvo|con excepci[oó]n de|"
    r"a excepci[oó]n de|exceto|com exce[cç][aã]o d[eoa]s?|außer|ausser|ausgenommen|mit ausnahme von|tranne|eccetto|ad eccezione d\w+|"
    r"kecuali|selain|bukan|ngoại trừ|không phải|кроме|не|за исключением|ยกเว้น)"
    r"\s+(?:the\s+|les\s+|des\s+|los\s+|las\s+|die\s+|der\s+|den\s+|dem\s+|of\s+|de\s+|du\s+|del\s+|dei\s+|degli\s+|delle\s+|von\s+)?)$")


# Aliases that are also everyday words: the English pronoun and currency
# prefix "us", the Vietnamese pronoun "anh", "pháp" (method), "nga", "úc".
# They name the nationality only in a nationality-shaped mention: beside a
# nationality noun ("US citizens", "the US", "công dân Anh"), never before a
# currency sign, a figure or an institution ("US$50", "the US Embassy").
_AMBIGUOUS_ALIASES = {'us', 'u.s.', 'u.s', 'uk', 'anh', 'nga', 'pháp', 'úc', 'lao'}
_SHAPED_AFTER_RE = re.compile(r'^\s?(?:citizens?|nationals?|passports?|holders?|residents?|travell?ers?|visitors?|nationality)\b')
_SHAPED_BEFORE_RE = re.compile(r'(?:(?<![a-z])the|công dân|quốc tịch|nước|người|hộ chiếu|vương quốc|liên bang|cộng hòa)\s$')
# A name followed by a currency, product or institution noun ("Indian
# rupees", "Korean won", "Thai cuisine", "the US Embassy") names a thing,
# not a traveller, wherever it occurs.
_UNSHAPED_AFTER_RE = re.compile(r'^\s?(?:dollars?|embassy|embassies|consulate|consulates|consular|mission|department|'
                                r'government|state|customs|border|immigration|authorit\w*|visas?|market|law|army|military|'
                                r'rupees?|rupiah|yuan|renminbi|won|yen|baht|ringgit|dong|đồng|pesos?|reais|real|rand|'
                                r'cuisine|restaurants?|food|dishes|tea|coffee|beer|whisky|wine)\b')
# A currency sign or figure right after a short alias or abbreviation
# ("US$50", "USA 5-year") prices something. A full name before a count
# ("英国50国", the fiftieth country of a list) still names the country.
_UNSHAPED_SYMBOL_RE = re.compile(r'^\s?[$€£\d]')


def _nationality_shaped(low, start, end):
    after = low[end:end + 24]
    if _UNSHAPED_SYMBOL_RE.match(after) or _UNSHAPED_AFTER_RE.match(after):
        return False
    return bool(_SHAPED_AFTER_RE.match(after) or _SHAPED_BEFORE_RE.search(low[max(0, start - 16):start]))


def _mentions(text, nat):
    """Spans of the nationality's own mentions in the normalized text: fixed
    aliases and inflected demonyms, negated mentions left out, a mention
    that names a currency, product or institution left out, an ambiguous
    short alias only when the mention is nationality-shaped, and never a
    name that its neighbours turn into a dependency or another jurisdiction
    ("British Virgin Islands passports", "Chinese Taipei nationals")."""
    low = _norm(text)
    spans = [m.span() for m in _name_pattern(nat).finditer(low)
             if not _NEGATED_PREFIX_RE.search(low[max(0, m.start() - 24):m.start()])
             and not _UNSHAPED_AFTER_RE.match(low[m.end():m.end() + 24])
             and (m.group(0) not in _AMBIGUOUS_ALIASES or _nationality_shaped(low, m.start(), m.end()))
             and not _other_jurisdiction(low, m.start(), m.end())]
    tokens = _CAPITAL_TOKENS.get(nat)
    if tokens:
        nfkc = unicodedata.normalize('NFKC', str(text or ''))
        for m in re.finditer(r'(?<![A-Za-z])(?:' + '|'.join(tokens) + r')(?![A-Za-z])', nfkc):
            if _NEGATED_PREFIX_RE.search(nfkc[max(0, m.start() - 24):m.start()].casefold()):
                continue
            # Locate the same token in the normalized text for a span.
            low_token = re.escape(_norm(m.group(0)))
            at = re.search(r'(?<![a-z])' + low_token + r'(?![a-z])', low)
            if at is None:
                spans.append((0, 0))
            elif (not _other_jurisdiction(low, at.start(), at.end())
                  and not _UNSHAPED_SYMBOL_RE.match(low[at.end():at.end() + 24])
                  and not _UNSHAPED_AFTER_RE.match(low[at.end():at.end() + 24])):
                spans.append(at.span())
    return spans


# The name's own item ends at a list separator or a sentence terminator, and
# the compound name ends at a prose word: "British Virgin Islands passports
# do not ..." qualifies "British" with "Virgin Islands passports", while
# "Indian nationals who are part of a tour group" stops at "who".
_QUALIFIER_BOUNDARY_RE = re.compile(r"[.!?。！？;:,，、/|()\[\]\n]|\s(?:and|or|und|et|y|e|o|dan|và|и|или|或|及|和)\s")
_QUALIFIER_STOP_RE = re.compile(
    r"^(?:as|who|whose|which|that|do|does|did|not|is|are|was|were|be|must|may|can|shall|should|will|need|needs|"
    r"require|requires|required|requiring|with|without|for|to|in|into|from|at|on|by|if|when|unless|hold|holders|"
    r"holding|residing|travel|travelling|traveling|enter|entering|entry|visit|visiting|stay|apply|applying|"
    r"pour|para|por|con|sin|avec|dans|für|mit|bei|nach|qui|que)$")
# The ISO and UN naming of the special administrative regions puts the
# parent after a comma: "Hong Kong SAR, China", "China, Hong Kong SAR". An
# enumeration ("Hong Kong, Macau and China") carries no SAR qualifier.
_SAR_BEFORE_RE = re.compile(r"(?:hong ?kong|macao|macau)\s*\(?\s*(?:sar|s\.a\.r\.?|special administrative region)\s*\)?\s*[,，]\s*$")
_SAR_AFTER_RE = re.compile(r"^\s*[,，(]\s*(?:hong ?kong|macao|macau)\s*\(?\s*(?:sar|s\.a\.r\.?|special administrative region)")


def _qualifier_words(text, *, before):
    """Up to four words of the name's own item next to the name, in text
    order, cut at the nearest boundary and at the first prose word."""
    if before:
        cuts = list(_QUALIFIER_BOUNDARY_RE.finditer(text))
        words = text[cuts[-1].end():].split() if cuts else text.split()
        kept = []
        for word in reversed(words):
            if _QUALIFIER_STOP_RE.match(word) or len(kept) == 4:
                break
            kept.insert(0, word)
        return kept
    cut = _QUALIFIER_BOUNDARY_RE.search(text)
    words = text[:cut.start()].split() if cut else text.split()
    kept = []
    for word in words:
        if _QUALIFIER_STOP_RE.match(word) or len(kept) == 4:
            break
        kept.append(word)
    return kept


def _other_jurisdiction(low, start, end):
    """The neighbours of the name make it a dependency or another
    jurisdiction, the same test a list entry gets in _entry_shaped: a
    territory word beside the name, a territory word anywhere in the
    document phrase before the name ("Bermuda passports issued by the
    United Kingdom"), or a sibling jurisdiction beside it that the text
    does not say it includes. A bracket right after the name is part of
    its qualifier ("British National (Overseas)"), not the end of it."""
    before, after = low[max(0, start - 40):start], low[end:end + 40]
    after_open = re.sub(r'[()\[\]（）]', ' ', after)
    qualifier = ' '.join(_qualifier_words(before, before=True) + _qualifier_words(after_open, before=False))
    if _JURISDICTION_RE.search(qualifier):
        return True
    # The clause before the name, back to the nearest list or clause
    # boundary, whatever prose words it holds.
    clause = low[max(0, start - 80):start]
    cuts = list(_QUALIFIER_BOUNDARY_RE.finditer(clause))
    clause = clause[cuts[-1].end():] if cuts else clause
    if _JURISDICTION_RE.search(clause):
        return True
    if _INCLUSION_RE.search(before + ' ' + after):
        return False
    return bool(_SIBLING_RE.search(qualifier) or _SAR_BEFORE_RE.search(before) or _SAR_AFTER_RE.match(after))


# Abbreviations that are the nationality only as capitals: "USA" is the
# country, "usa" is a Spanish verb. Read on the un-casefolded text.
_CAPITAL_TOKENS = {'USA': (r'USA', r'EUA', r'EE\.?UU\.?', r'U\.S\.A\.?')}


def _named(text, nat):
    """The text names the nationality: a fixed alias, an inflected demonym
    (after normalization) or a capitalised abbreviation (as written), and
    not only behind a negating prefix."""
    return bool(_mentions(text, nat))


# A mention in a traveller role: beside a traveller noun ("Japanese
# nationals", "citizens of Japan", "holders of passports issued by Japan"),
# or the subject of the sentence. A mention governed by a destination frame
# ("travelling to Japan", "a visa for Japan", "in Japan", "赴日本", "日本へ")
# names where the traveller goes, never who the traveller is.
_STATE_PREFIX = (r"(?:(?:the|la|le|les|l['’]|el|los|las|o|os|as|die|der|das|den|dem|il|lo|gli|i)\s+)?"
                 r"(?:(?:federal |people'?s |socialist |united |democratic |islamic |arab |hashemite )?"
                 r"(?:republic|kingdom|state|states|federation|commonwealth|union|sultanate|emirates|principality|grand duchy) of |"
                 r"république (?:de |du |des |d['’])|royaume (?:de |du |d['’])|reino (?:de |da |do |de la )|república (?:de |da |do |de la |dos |das )|"
                 r"federación de |federação da |repubblica (?:di |del |della |dei )|regno (?:di |del )|bundesrepublik |königreich |republik |"
                 r"республик[аи] |федерац\w* |королевств\w* |nước |cộng hòa |vương quốc |liên bang |negara |kerajaan |สาธารณรัฐ|ราชอาณาจักร)?")
_TRAVELLER_AFTER_RE = re.compile(
    r"^\s?(?:(?:sar|s\.a\.r\.|special administrative region|ordinary|regular|national|biometric|valid|diplomatic|official|service)\s+)?"
    r"(?:citizens?|nationals?|passports?|passport[- ]holders?|holders?|residents?|travell?ers?|visitors?|nationality|subjects?|people|persons?|"
    r"applicants?|tourists?|students?|workers?|visa applicants?|citoyens?|ressortissants?|nationaux|titulaires|ciudadan[oa]s?|nacionales|titulares|"
    r"cidadãos?|nacionais|portadores|cittadin[oi]|staatsangehörige\w*|staatsbürger\w*|bürger\w*|inhaber|граждан\w*|подданн\w*|"
    r"công dân|người|hộ chiếu|quốc tịch|warga ?negara|warganegara|paspor|pemegang|พลเมือง|คน|ผู้ถือ|สัญชาติ)\b|"
    r"^(?:国民|公民|人|人员|人員|人士|籍|国籍|旅券|护照|護照|方|の国民|の方|の旅券|여권|국민|인|국적|人民|持)")
_TRAVELLER_BEFORE_RE = re.compile(
    r"\b(?:citizens?|nationals?|holders?|passports?|residents?|subjects?|people|persons?|travell?ers?|visitors?|natives?|applicants?|tourists?|"
    r"citoyens?|ressortissants?|nationaux|titulaires|ciudadan[oa]s?|nacionales|titulares|cidadãos?|nacionais|portadores|cittadin[oi]|titolari|"
    r"staatsangehörige\w*|staatsbürger\w*|bürger\w*|inhaber|angehörige|граждан\w*|подданн\w*|владельц\w*|уроженц\w*|"
    r"công dân|người|hộ chiếu|quốc tịch|mang quốc tịch|warga ?negara|warganegara|pemegang paspor|paspor|พลเมือง|คนสัญชาติ|สัญชาติ|ผู้ถือหนังสือเดินทาง|บุคคลสัญชาติ)"
    r"\s+(?:(?:of|de|du|des|d['’]|da|do|dos|das|di|dell['’]|della|dei|degli|del|von|der|aus|from|từ|dari|mang|nước|of the)\s+)?"
    r"(?:(?:the|a|an|la|le|les|el|los|las|o|os|as|die|der|den|dem|il|lo|gli|i)\s+)?" + _STATE_PREFIX + r"$|"
    r"\bpassports?\s+(?:issued|delivered|délivrés?|expedidos?|emitidos?|ausgestellt)\s+(?:by|in|par|por|von|from)\s+(?:the\s+)?"
    r"(?:government of |authorities of |immigration authorities of )?" + _STATE_PREFIX + r"$|"
    r"(?:国籍|籍|持|来自|來自|居民|公民|国民|人员|人員|旅券を所持する|の国籍|여권을 소지한|국적의|국적|국민|출신|người mang hộ chiếu|hộ chiếu|quốc tịch|công dân|dân|"
    r"dari|ke ?warganegaraan|berpaspor|berkewarganegaraan|สัญชาติ|ชาว|ผู้ถือหนังสือเดินทาง)\s*$", re.I)
_DESTINATION_BEFORE_RE = re.compile(
    r"(?:\b(?:to|into|towards?|onto|throughout|across|within|inside|in|at)\s+|"
    r"\b(?:visit|visiting|visits|enter|entering|enters|entry to|entry into|entering into|travel(?:ling|ing)? to|travels? to|trips? to|journey to|"
    r"arriv(?:e|al|ing) (?:in|at)|stay(?:ing|s)? in|leaving|departing|departure from|exit(?:ing)? from|resid(?:e|ing|ent|ents) in|located in|based in|born in|"
    r"admission to|admitted to|going to|come to|coming to|fly(?:ing)? to|flights? to)\s+|"
    r"\b(?:visas?|e-?visas?|visado|visto|visum|permits?|admission|entry)\s+(?:for|to|pour|para|per|für|untuk|cho)\s+|"
    r"\b(?:pour (?:entrer|se rendre|voyager|séjourner) (?:en|au|aux|à|dans)|à destination d(?:e|u|es)|vers|en|au|aux|dans|sur le territoire (?:de|du|des|d['’]))\s+|"
    r"\b(?:para (?:entrar|viajar|ingresar|visitar|llegar) (?:a|en|al|en el|en la)|hacia|rumbo a|en|en el|en la|en los|en las|ao|à|na|nas|nos|em|para o|para a|"
    r"per (?:entrare|recarsi|viaggiare) (?:in|a|nel|nella)|nel|nella|negli|nelle|nach|in die|in den|in das|ins|im|in der|в|во|на|до|"
    r"đến|tới|vào|sang|tại|ở|nhập cảnh|ke|di|menuju|masuk ke|ไป|ยัง|เข้า|สู่|เดินทางไป)\s+)"
    r"(?:(?:the|la|le|les|l['’]|el|los|las|o|os|as|die|der|das|den|dem|il|lo|gli|i)\s+)?" + _STATE_PREFIX + r"$", re.I)
_DESTINATION_CJK_BEFORE_RE = re.compile(r"(?:赴|前往|前赴|到|去|来|來|入境|進入|进入|访问|訪問|访|飞往|飛往|抵达|抵達|進出|进出|渡航先|行き先|목적지)\s*$")
_DESTINATION_AFTER_RE = re.compile(r"^\s*(?:へ|への|に入国|へ入国|に渡航|へ渡航|に行く|に来る|に滞在|に入る|に|에 입국|에 방문|으로|로 입국|을 방문|를 방문|에 가|에 체류|에|境内|境內|国内|國內|入境|을 여행|를 여행)")


def _traveller_mentions(text, nat):
    low = _norm(text)
    spans = []
    for start, end in _mentions(text, nat):
        before, after = low[max(0, start - 48):start], low[end:end + 24]
        if start == end == 0 or _TRAVELLER_AFTER_RE.match(after) or _TRAVELLER_BEFORE_RE.search(before):
            spans.append((start, end))
        elif (_DESTINATION_BEFORE_RE.search(before) or _DESTINATION_CJK_BEFORE_RE.search(before)
              or _DESTINATION_AFTER_RE.match(after)):
            continue
        else:
            spans.append((start, end))
    return spans


def _named_as_traveller(text, nat):
    """The text names the nationality in a traveller role, not only as a
    destination of travel."""
    return bool(_traveller_mentions(text, nat))


def _today():
    return date.today()


def _policy_bound_statement(quote, forms, key):
    """A dated office notice or visa issue date does not bound a policy.

    Require explicit policy-effect language in the same sentence as the
    date. Ambiguous or unsupported language needs a better captured passage;
    the reviewer's label alone cannot turn a date into a rule's expiry.
    """
    dates = '(?:' + '|'.join(re.escape(_norm(form)).replace(r'\ ', r'\s*')
                            for form in forms) + ')'
    # A clock time may sit between the relation and the date ("to 24:00 on
    # December 31, 2026").
    clock = r'(?:\d{1,2}[:.]\d{2}\s*(?:on|hrs?|h|時|时)?\s*)?'
    if key == 'effective_from':
        relation = (r'(?:effective\s*(?:from|on|as of)?|(?:comes?|enters?)\s+into\s+force\s*(?:on|from)?|'
                    r'takes?\s+effect\s*(?:on|from)?|(?:starts?|begins?|commences?)\s*(?:on|from)?|'
                    r'appl(?:y|ies)\s+from|with\s+effect\s+from|as\s+(?:of|from)|on\s+or\s+after|from|since|'
                    r'à\s+(?:partir|compter)\s+d[ue]|a\s+partir\s+de|desde(?:\s+el)?|ab(?:\s+dem)?|seit|dal|с|начиная\s+с|'
                    r'kể\s+từ(?:\s+ngày)?|từ(?:\s+ngày)?|mulai(?:\s+dari)?|sejak|ตั้งแต่(?:วันที่)?)\s*[:,]?\s*' + clock + dates)
        chinese = (r'(?:自|從|从|於|于|实施期限为|實施期限為|有效期为|有效期為)\s*' + dates + r'[^。；\n]{0,60}(?:起|生效|實施|实施|至|到|止)|'
                   + dates + r'\s*(?:부터|以降|이후|よ り|より)')
    else:
        relation = (r'(?:until|till|through|thru|to|ends?\s*(?:on)?|ending\s+on|expires?\s*(?:on)?|expiring\s+on|'
                    r'valid\s+(?:until|through|to)|extended\s+(?:until|to|through)|prolonged\s+(?:until|to)|in\s+force\s+until|'
                    r'ceases?\s+to\s+(?:apply|have\s+effect|be\s+(?:valid|in\s+force))\s*(?:on|from)?|'
                    # The same end relations the window reader knows, so a
                    # bound one reader expires the verdict on is a bound the
                    # other lets the reviewer record.
                    r'(?:shall\s+|will\s+|to\s+|would\s+)?ceases?(?:\s+on)?|ceasing(?:\s+on)?|lapses?(?:\s+on)?|lapsing(?:\s+on)?|'
                    r'terminates?(?:\s+on)?|terminating(?:\s+on)?|runs?\s+(?:to|until|through)|running\s+(?:to|until|through)|'
                    r'(?:arrivals?|entries|entry|stays?|travel|visits?|applications?|departures?)\s+(?:before|up\s+to|until)|'
                    r'no\s+later\s+than|not\s+later\s+than|at\s+the\s+latest(?:\s+on)?|jusqu[\'’](?:au|à)|hasta(?:\s+el)?|até|bis(?:\s+zum)?|fino\s+al|до|по|'
                    r'đến(?:\s+hết)?(?:\s+ngày)?|tới(?:\s+ngày)?|hingga|sampai(?:\s+dengan)?|(?:จน)?ถึง(?:วันที่)?)\s*[:,]?\s*'
                    r'(?:and\s+including\s+)?' + clock + dates)
        chinese = (r'(?:至|截至|到|有效至|延期至|延長至|延长至|施行至|有效期至|截止)\s*' + dates + r'|' + dates
                   + r'\s*(?:止|屆滿|届满|到期|까지|まで)')
    for sentence in re.split(r'(?<=[.!?])\s+|[;。；！？\n]+', str(quote or '')):
        sentence = _norm(sentence)
        if _WINDOW_SKIP_RE.search(sentence):
            continue
        if _POLICY_WORD_RE.search(sentence) and (re.search(relation, sentence, re.I)
                                                 or re.search(chinese, sentence)):
            return True
    return False


# Sentences whose date is not a rule's bound: office closures, issue and
# update dates, passport validity.
_WINDOW_SKIP_RE = re.compile(r'\b(?:holiday|closed|closure|issued|issuance|updated|published|last reviewed|printed|revised)\b|'
                             r'\b(?:official journal|journal officiel|official gazette|gazzetta ufficiale|amtsblatt|diario oficial|'
                             r'boletín oficial|diário oficial)\b|'
                             r'passport[^.;\n]{0,35}(?:valid|expir)|(?:護照|护照|旅券|여권)[^。；\n]{0,12}(?:有效|到期|效期|残存|유효)|'
                             r'休館|休馆|休假|發證|发证|更新日|最終更新')
# A sentence about a rule: policy language, or a verdict statement of its own.
_POLICY_WORD_RE = re.compile(r'\b(?:polic(?:y|ies)|regulations?|rules?|resolutions?|agreements?|decrees?|schemes?|programmes?|programs?|'
                             r'arrangements?|measures?|visa[- ]?exemption|visa[- ]?free|exemption|exempt\w*|waive\w*|visas?|entry|'
                             r'visado|visto|visum|dispens\w*|exent\w*|kebijakan|peraturan|ketentuan|dasar|bebas\s+visa|chính\s+sách|quy\s+định|miễn\s+thị\s+thực)\b|'
                             r'政策|免簽|免签|規定|规定|法規|法规|辦法|办法|措置|措施|签证|簽證|査証|ビザ|비자|무비자|사증|thị thực|виз|'
                             r'วีซ่า|ตรวจลงตรา|ยกเว้น|นโยบาย|ระเบียบ|มาตรการ', re.I)
_MONTH_NUMBERS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 'july': 7, 'august': 8, 'september': 9,
    'october': 10, 'november': 11, 'december': 12, 'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    'janvier': 1, 'février': 2, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'août': 8, 'aout': 8,
    'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12, 'decembre': 12,
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8, 'septiembre': 9,
    'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
    'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3, 'maio': 5, 'junho': 6, 'julho': 7, 'setembro': 9, 'outubro': 10,
    'novembro': 11, 'dezembro': 12,
    'januar': 1, 'februar': 2, 'märz': 3, 'maerz': 3, 'juni': 6, 'juli': 7, 'oktober': 10, 'dezember': 12,
    'gennaio': 1, 'febbraio': 2, 'aprile': 4, 'maggio': 5, 'giugno': 6, 'luglio': 7, 'settembre': 9, 'ottobre': 10, 'dicembre': 12,
    # Indonesian, Malay and Dutch
    'januari': 1, 'februari': 2, 'maret': 3, 'maart': 3, 'mac': 3, 'mei': 5, 'julai': 7, 'agustus': 8, 'ogos': 8, 'augustus': 8,
    'desember': 12, 'disember': 12,
    # Turkish
    'ocak': 1, 'şubat': 2, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayıs': 5, 'mayis': 5, 'haziran': 6, 'temmuz': 7, 'ağustos': 8,
    'agustos': 8, 'eylül': 9, 'eylul': 9, 'ekim': 10, 'kasım': 11, 'kasim': 11, 'aralık': 12, 'aralik': 12,
    # Russian, genitive as dates are written and nominative
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10,
    'ноября': 11, 'декабря': 12, 'январь': 1, 'февраль': 2, 'март': 3, 'апрель': 4, 'май': 5, 'июнь': 6, 'июль': 7, 'август': 8,
    'сентябрь': 9, 'октябрь': 10, 'ноябрь': 11, 'декабрь': 12,
    # Thai, full and abbreviated; the year beside them is the Buddhist era
    'มกราคม': 1, 'กุมภาพันธ์': 2, 'มีนาคม': 3, 'เมษายน': 4, 'พฤษภาคม': 5, 'มิถุนายน': 6, 'กรกฎาคม': 7, 'สิงหาคม': 8, 'กันยายน': 9,
    'ตุลาคม': 10, 'พฤศจิกายน': 11, 'ธันวาคม': 12, 'ม.ค.': 1, 'ก.พ.': 2, 'มี.ค.': 3, 'เม.ย.': 4, 'พ.ค.': 5, 'มิ.ย.': 6, 'ก.ค.': 7,
    'ส.ค.': 8, 'ก.ย.': 9, 'ต.ค.': 10, 'พ.ย.': 11, 'ธ.ค.': 12,
}
_MONTH_ALT = '(?:' + '|'.join(sorted(map(re.escape, _MONTH_NUMBERS), key=len, reverse=True)) + ')'
# Japanese era years: 令和6年 is 2024.
_ERA_BASE = {'令和': 2018, '平成': 1988, '昭和': 1925, '大正': 1911}
# Every date shape the captured pages write, with the order of its groups.
_DATE_RES = (
    (re.compile(r'(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)'), 'ymd'),
    (re.compile(r'(?<![\d:])(\d{1,2})(?:st|nd|rd|th|er|º|°)?\.?\s*(?:of\s+|de\s+|del\s+)?(' + _MONTH_ALT + r')\.?,?\s*(?:de\s+|del\s+|พ\.?ศ\.?\s*|г\.?\s*)?(\d{4})(?!\d)'), 'dmy'),
    (re.compile(r'(?<![a-z])(' + _MONTH_ALT + r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)'), 'mdy'),
    (re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'), 'ymd'),
    (re.compile(r'(令和|平成|昭和|大正)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'), 'era'),
    (re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일'), 'ymd'),
    (re.compile(r'(?:ngày\s*)?(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})'), 'dmy'),
    (re.compile(r'(?<![\d/.])(\d{4})\s*[/.]\s*(\d{1,2})\s*[/.]\s*(\d{1,2})(?!\d)(?![/.]\d)'), 'ymd'),
    (re.compile(r'(?<![\d:/.])(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)(?![/.]\d)'), 'dmy'),
)
# A date expression by shape, whatever the parser makes of it: an era or
# calendar year, a numeric date, a month name with a day or a year, a
# Vietnamese day-month, or a bare year governed by a time relation. Every
# such expression in a rule sentence must parse, or the sentence cannot be
# dated and proves nothing.
_YEAR = r'(?:1[89]\d{2}|20\d{2}|2[45]\d{2})'
_DATE_LIKE_RE = re.compile(
    r'(?:令和|平成|昭和|大正)\s*(?:\d{1,2}|元)\s*年|'
    r'(?<!\d)' + _YEAR + r'\s*(?:年|년)|'
    r'(?:พ\.?ศ\.?|ค\.?ศ\.?)\s*\d{4}|'
    r'(?<![\d/.:-])\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})(?!\d)(?![/.-]\d)|'
    r'(?<![\d/.:-])\d{4}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{1,2}(?!\d)(?![/.-]\d)|'
    r'(?<![a-z])' + _MONTH_ALT + r'\.?\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?(?:de\s+|del\s+)?' + _YEAR + r'(?!\d)|'
    r'\bngày\s*\d{1,2}\s*tháng\s*\d{1,2}|\btháng\s*\d{1,2}\s*(?:năm|/)\s*' + _YEAR + r'|'
    r'(?:\b(?:until|till|through|thru|from|since|by|before|after|as of|effective|expires?|expiring|to|hasta|jusqu[\'’]?(?:au|à|en)?|'
    r'desde|bis|ab|seit|до|по|с|начиная с|đến|tới|từ|hingga|sampai|sejak|ถึง|จนถึง|ตั้งแต่)\s+(?:the\s+)?'
    r'(?:end of\s+|start of\s+|beginning of\s+|early\s+|late\s+|mid-?\s*)?|(?:至|截至|到|自|从|從|於|于|まで|から|까지|부터)\s*)' + _YEAR + r'(?!\d)(?!\s*[/-]\d)',
    re.I)
_RANGE_BETWEEN_RE = re.compile(r'\s*(?:to|till|until|through|thru|and|-|–|—|~|至|到|bis|à|au|al|a|hasta|até|e|по|до|부터|까지|から|'
                               r'đến(?:\s+hết)?(?:\s+ngày)?|tới|hingga|sampai(?:\s+dengan)?|s/d|(?:จน)?ถึง(?:วันที่)?)\s*'
                               r'(?:\d{1,2}[:.]\d{2}\s*(?:on|hrs?|h|時|时)?\s*)?(?:the\s+)?$', re.I)
# The start of a window, in the same relation words _policy_bound_statement
# reads. The two readers have to agree in both directions: a start one of
# them knows and the other does not is a rule in force for the recorder and
# an unreadable bound for the window gate.
_START_BEFORE_RE = re.compile(r'(?:\bfrom|\bas\s+of|\bas\s+from|\bon\s+or\s+after|\bwith\s+effect\s+from|\beffective(?:\s+from|\s+on|\s+as\s+of)?|\bstarting(?:\s+from|\s+on)?|'
                              r'\bbeginning(?:\s+from|\s+on)?|\bcommencing(?:\s+from|\s+on)?|\bsince|\bw\.e\.f\.?|\bà\s+(?:partir|compter)\s+d[ue]|'
                              r'\b(?:com(?:es?|ing)|came|enters?|entering|entered)\s+into\s+(?:force|effect)(?:\s+on|\s+from)?|'
                              r'\b(?:takes?|taking|took)\s+effect(?:\s+on|\s+from)?|\bappl(?:y|ies)\s+from|\bapplicable\s+from|\bvalid\s+from|'
                              r'\b(?:starts?|began|begins?|commences?|commenced)(?:\s+on|\s+from)?|\bin\s+(?:force|effect)\s+(?:from|since)|'
                              r'\ba\s+partir\s+d[eu]l?|\bdesde(?:\s+el)?|\bab(?:\s+dem)?|\bseit|\bdal|\bс|\bначиная\s+с|\bkể\s+từ(?:\s+ngày)?|\btừ(?:\s+ngày)?|'
                              r'\bmulai(?:\s+dari)?|\bsejak|ตั้งแต่(?:วันที่)?|自|从|從|於|于|实施期限为|實施期限為|有效期为|有效期為)'
                              r'\s*[:,]?\s*(?:\d{1,2}[:.]\d{2}\s*(?:on|hrs?|h|時|时)?\s*)?(?:the\s+)?$', re.I)
_START_AFTER_RE = re.compile(r'^\s*(?:起|부터|以降|이후|より|onwards?|onward)')
# The end of a window, in the same relation words _policy_bound_statement
# reads. The two readers have to agree: a relation one of them knows and
# the other does not is a bound that expires the verdict for the recorder
# and no bound at all for the window gate.
_END_BEFORE_RE = re.compile(r'(?:\buntil|\btill|\bthrough|\bthru|\bup\s+to|\bends?(?:\s+on)?|\bending(?:\s+on)?|\bexpires?(?:\s+on)?|\bexpiring(?:\s+on)?|'
                            r'\bvalid\s+(?:until|through|to)|\bextended\s+(?:until|to|through)|\bprolonged\s+(?:until|to)|\bin\s+force\s+until|'
                            r'\bceases?\s+to\s+(?:apply|have\s+effect|be\s+(?:valid|in\s+force))(?:\s+on|\s+from)?|'
                            r'\b(?:shall\s+|will\s+|to\s+|would\s+)?ceases?(?:\s+on)?|\bceasing(?:\s+on)?|\blapses?(?:\s+on)?|\blapsing(?:\s+on)?|'
                            r'\bterminates?(?:\s+on)?|\bterminating(?:\s+on)?|\bruns?\s+(?:to|until|through)|\brunning\s+(?:to|until|through)|'
                            r'\b(?:arrivals?|entries|entry|stays?|travel|visits?|applications?|departures?)\s+(?:before|up\s+to|until)|'
                            r'\bno\s+later\s+than|\bnot\s+later\s+than|\bat\s+the\s+latest(?:\s+on)?|'
                            r'\bjusqu[\'’](?:au|à)|\bhasta(?:\s+el)?|\baté|\bbis(?:\s+zum)?|\bfino\s+al|\bдо|\bпо|\bđến(?:\s+hết)?(?:\s+ngày)?|\btới(?:\s+ngày)?|'
                            r'\bhingga|\bsampai(?:\s+dengan)?|(?:จน)?ถึง(?:วันที่)?|至|截至|到|有效至|延期至|延長至|延长至|施行至|有效期至|截止)'
                            r'\s*[:,]?\s*(?:\d{1,2}[:.]\d{2}\s*(?:on|hrs?|h|時|时)?\s*)?(?:the\s+)?$', re.I)
_END_AFTER_RE = re.compile(r'^\s*(?:止|屆滿|届满|到期|까지|まで)')
# The date of the instrument that states a rule is not a bound of the rule:
# "Resolution No. 44/NQ-CP of the Government dated 07 March 2025", "nghị
# quyết số 44/NQ-CP ngày 7/3/2025", "нота от 16.06.2014", "Regulation (EU)
# 2018/1806 of the European Parliament and of the Council of 14 November
# 2018", "2003年6月30日付緊急政令第196号", the Official Journal issue that
# printed it ("L 32, 1, 3.2.2023"), or the day it was signed, adopted or
# promulgated. The designation has to stand before the date, or the dating
# word itself ("dated", "en date du", "vom", "z dnia"), so a bare "on <date>"
# or "of <date>" after no instrument stays an unreadable bound, and so does
# "the end of <date>" however near an instrument noun it stands.
_INSTRUMENT_NOUN = (
    r"(?:resolutions?|decrees?|decisions?|regulations?|directives?|laws?|acts?|orders?|ordinances?|circulars?|notifications?|"
    r"notices?|notes?|memorand(?:um|a)|protocols?|agreements?|treat(?:y|ies)|conventions?|gazettes?|instructions?|announcements?|"
    r"proclamations?|statutes?|amendments?|letters?|communiqu[ée]s?|"
    r"notas?|resoluci[oó]n(?:es)?|decretos?|ley(?:es)?|[oó]rden(?:es)?|acuerdos?|reglamentos?|"
    r"d[ée]crets?|arr[êe]t[ée]s?|lois?|ordonnances?|circulaires?|r[èe]glements?|accords?|"
    r"verordnung(?:en)?|gesetz(?:es)?|erlass(?:es)?|beschl(?:uss|üsse)|bekanntmachung(?:en)?|verf[üu]gung(?:en)?|abkommen|"
    r"deliber[ae]|decreto-legge|legg[ei]|regolament[oi]|accord[oi]|portarias?|leis?|despachos?|"
    r"нот[аы]|постановлени[еяю]|приказа?|указа?|распоряжени[еяю]|закона?|решени[еяю]|соглашени[еяю]|письм[оа]|протокола?|"
    r"nghị quyết|nghị định|quyết định|thông tư|công văn|luật|hiệp định|thỏa thuận|"
    r"peraturan|keputusan|undang-undang|surat(?: edaran)?|perjanjian|kanun|karar(?:name)?|genelge|yönetmelik|tebliğ|anlaşma|"
    r"พระราชบัญญัติ|กฎกระทรวง|ประกาศ|คำสั่ง|ระเบียบ|ความตกลง)")
_INSTRUMENT_DATE_BEFORE_RE = re.compile(
    r"(?:\b" + _INSTRUMENT_NOUN + r"\b[^;\n]{0,80}?(?<!\bend)(?<!\bstart)(?<!\bclose)(?<!\bmiddle)(?<!\bbeginning)"
    r"\s(?:of|dated|du|de|del|vom|von|от|ngày|của ngày|d\.d\.|dd\.|z dnia|dnia|din|van|af|dari|tanggal|tertanggal)|"
    r"\b(?:dated|signed|adopted|approved|promulgated|enacted|ratified|gazetted|notified|announced|concluded|amended|passed|issued|published|"
    r"en date du|datée? du|de fecha|con fecha|datado de|com data de|vom|d\.d\.|dd\.|z dnia|"
    r"подписан\w*|принят\w*|утвержд[её]н\w*|издан\w*|опубликован\w*|ban hành|ký ngày|ký kết|ditandatangani|ditetapkan|diterbitkan|disahkan)"
    r"(?:\s+(?:on|at|le|el|am|в|du|de|ngày|tanggal|pada|pada tanggal))?|"
    r"\b(?:signed|adopted|approved|promulgated|enacted|ratified|gazetted|notified|announced|concluded|amended|passed)\b[^;\n]{0,80}?\s(?:on|le|el|am|в)|"
    r"\b(?:oj|jo|abl|gu|guue|do|dou|official journal|journal officiel)\.?\s*[lc]?\s*\d+[,\s]*(?:p\.?\s*\d+[,\s]*)?(?:of|du|vom|de|del)?|"
    r"\bl\s+(?:\d{1,4}\s+){1,4})"
    r"\s*[:,]?\s*(?:«|\"|“)?\s*$", re.I)
_INSTRUMENT_DATE_AFTER_RE = re.compile(
    r"^\s*(?:付(?:け)?|자\s*(?:고시|공고|법률|시행령|시행규칙|훈령|예규|지침|결정|공문|령)|"
    r"に?\s*(?:发布|發布|公布|颁布|頒布|签署|簽署|签订|簽訂|通过|通過|制定|印发|印發|批准|署名|採択|改正|発表)|"
    r"(?:에\s*)?(?:발표|공포|제정|개정|서명|채택|공표))")


def _instrument_date(low, start, end, lead=''):
    """The date at this span of the normalized sentence dates the instrument
    that states the rule, or the journal that printed it, and bounds nothing.
    The designation may stand in the fragment before this one when the
    sentence splitter cut it off the date ("Regulation (EC) No." / "810/2009
    ... of the Council of 13 July 2009")."""
    before = low[:start]
    context = (lead[-120:] + ' ' + before) if lead and len(before) <= 90 else before
    return bool(_INSTRUMENT_DATE_BEFORE_RE.search(context[-200:]) or _INSTRUMENT_DATE_AFTER_RE.match(low[end:end + 16]))


def _dates_in(low):
    """Every calendar date in the normalized sentence with its span, in
    text order. Day-month-year forms read day first, as the bound forms
    do; a Thai year beyond 2400 is the Buddhist era; a Japanese era year
    is counted from the era's base."""
    found = {}
    for pattern, order in _DATE_RES:
        for m in pattern.finditer(low):
            groups = m.groups()
            try:
                if order == 'era':
                    era, number, month, day_of_month = groups
                    year = _ERA_BASE[era] + (1 if number == '元' else int(number))
                    day = date(year, int(month), int(day_of_month))
                else:
                    if order == 'ymd':
                        year, month, day_of_month = groups
                    elif order == 'dmy':
                        day_of_month, month, year = groups
                    else:
                        month, day_of_month, year = groups
                    year = int(year)
                    if year > 2400:
                        year -= 543
                    month = _MONTH_NUMBERS[month] if month in _MONTH_NUMBERS else int(month)
                    day = date(year, month, int(day_of_month))
            except (ValueError, KeyError):
                continue
            if not any(s <= m.start() < e or s < m.end() <= e for s, e in found):
                found[m.span()] = day
    return sorted((s, e, d) for (s, e), d in found.items())


def _unreadable_date(low):
    """The first date expression of the normalized sentence the parser
    cannot read, or None. A rule sentence carrying one cannot be dated."""
    dates = _dates_in(low)
    for m in _DATE_LIKE_RE.finditer(low):
        covered = any(s <= m.start() < e or s < m.end() <= e or (m.start() <= s and e <= m.end()) for s, e, _ in dates)
        if not covered:
            return m.group(0).strip()
    return None


def _dated_rule_sentence(low):
    """A sentence whose date would be a rule's date: about a rule and not
    an office, issue, update or passport-validity notice."""
    return bool(low) and not _WINDOW_SKIP_RE.search(low) and bool(
        _POLICY_WORD_RE.search(low) or any(re.search(p, low, re.I) for p, _ in _VERDICT_RULES.values()))


def _label_lead(piece):
    """The piece before a colon is a label and not a clause of its own: a
    short name-shaped head ("India** citizen", "Hong Kong SAR passport")
    with no verdict word in it."""
    text = ' '.join(str(piece or '').split()).strip(' *†‡|')
    return bool(text and len(text) <= 60 and len(text.split()) <= 8 and not _RULE_WORDS.search(text)
                and any(_named(text, nat) for nat in _known_nationalities()))


def _run_continues(gap):
    """The text between a comma and the span behind it is the middle of one
    conjunction-joined run of list entries ("Brunei, the Philippines and
    Thailand (effective until July 31, 2022)"), so that comma separates two
    items of a run and opens no clause of its own. Whatever a parenthesis
    holds is left out of the reading: the bound inside it is the span the run
    is being read for."""
    text = re.split(r'[(（]', gap, maxsplit=1)[0]
    if not re.search(r'\b(?:and|or|und|et|y|e|dan|và|hoặc|atau|и|или)\b', text, re.I):
        return False
    items = [item for item in re.split(r'\s*(?:[,;，、；]|\s(?:and|or|und|et|y|e|dan|và|hoặc|atau|и|или)\s)\s*', text)
             if item.strip()]
    return bool(items) and all(_entry_line(item) for item in items)


def _clause(low, start, end):
    """The comma-delimited clause of the normalized sentence that holds the
    span: a sentence that states one window per clause ("... for Russian
    passport holders until 2027-12-31, for the other 48 countries until
    2026-12-31") is read clause by clause. A leading label stays part of its
    clause, the way _label keeps a quote's label, so "India** citizen: visa
    exempts until 31st December 2026" still names the traveller the bound is
    about, and a comma inside one run of list entries keeps the whole run,
    so a bound the closing item carries is read against every item."""
    lo = 0
    for mark in re.finditer(r'[,，;；:：]', low[:start]):
        if mark.group(0) in ':：' and _label_lead(low[lo:mark.start()]):
            continue
        if _run_continues(low[mark.end():start]):
            continue
        lo = mark.end()
    hi = next((m.start() for m in re.finditer(r'[,，;；:：]', low[end:])), None)
    return low[lo:end + hi] if hi is not None else low[lo:]


# A bound in a parenthesis attached to one entry of a list run ("...,
# North Macedonia*(effective until March 31, 2030), Norway, ...") bounds
# that entry alone. The parenthesis may hold nothing but the bound: a
# relation word and its date, or a date range. Any other word in it, an
# entry that is not a bare list entry, an entry the sentence does not
# reach through an item separator, or an entry with a quantifier or
# anaphora ("all of the above (until ...)") leaves the window on the
# whole sentence, where it must be current and recorded.
_BOUND_ONLY_RE = re.compile(
    r"^[\s,:;]*(?:(?:effective|valid|in force|in effect|applicable|applies|applied|valable|válido|válida|vigente|gültig|有効|有效|유효)\s*)?"
    r"(?:(?:until|till|through|thru|up to|to|from|since|as of|as from|starting|beginning|commencing|ending|expires?|expiring|"
    r"jusqu[’'](?:au|à)|hasta|desde|à partir d[ue]|a partir de[l]?|até|bis|ab|seit|dal|fino al|до|по|с|đến|từ|tới|hingga|sampai|sejak|"
    r"ถึง|จนถึง|ตั้งแต่|至|到|自|从|從|まで|から|까지|부터|起|止|and|-|–|—|~)[\s,:;]*)*$", re.I)
_ITEM_BEFORE_RE = re.compile(r"(?:[,;:，、；：]|\b(?:and|or|und|et|y|e|dan|và|и)\b)\s*$", re.I)
_ENTRY_SCOPE_RE = re.compile(
    r"\b(?:all|every|each|any|other|others|remaining|rest|listed|above|following|these|those|such|everyone|"
    r"countries|nationals|citizens|holders|persons|people|members?)\b|上述|以上|其他|其它|以下|各国|모든|기타|すべて|その他|以下の", re.I)
# A clause that names another nationality but speaks of everyone ("the
# programme agreed with Japan applies to all listed nationals until ...")
# is not that nationality's alone.
_CLAUSE_GENERAL_RE = re.compile(
    r"\b(?:all|every|each|any|other|others|remaining|rest|listed|above|following|these|those|such|everyone|everybody)\b|"
    r"上述|以上|其他|其它|以下|各国|모든|기타|すべて|その他|以下の", re.I)
# A longer passage (a footnote, a page section, a whole sentence) speaks of
# every traveller, not of one nationality's entry, when a quantifier lands
# on a traveller noun ("all listed nationals", "the following countries",
# "any visitor") or on a bare universal. A quantifier on a document or a
# condition ("other passport types", "the following conditions") narrows
# neither the nationality nor the list, so it leaves the passage scoped to
# whatever nationality it names. _CLAUSE_GENERAL_RE stays the looser test
# for a single comma clause, where one quantifier word is enough.
_GENERAL_TRAVELLER_RE = re.compile(
    r"\b(?:all|any|every|each|other|others|remaining|rest|listed|above|below|following|these|those|such|both|no)\s+"
    r"(?:[\w\-’']+\s+){0,3}?"
    r"(?:countries|territories|nationalit(?:y|ies)|nationals?|citizens?|passport ?holders?|holders?|"
    r"travell?ers?|visitors?|foreigners?|aliens?|persons?|people|applicants?|tourists?|passengers?|"
    # A scheme names its travellers by their state as often as by their
    # nationality: "all member states", "every signatory", "the parties".
    r"states?|jurisdictions?|part(?:y|ies)|members?|signator(?:y|ies)|participants?|beneficiar(?:y|ies))\b|"
    r"\b(?:everyone|everybody|anyone|anybody)\b|"
    r"\bforeign (?:nationals?|citizens?|visitors?|travell?ers?|passport ?holders?|passengers?)\b|"
    r"\btous (?:les )?(?:ressortissants|étrangers|voyageurs|pays)\b|\btodos los (?:ciudadanos|extranjeros|viajeros|países)\b|"
    r"\balle (?:staatsangehörigen|ausländer|reisenden|länder)\b|\bsemua (?:warga|orang asing|negara)\b|"
    r"\bmọi (?:công dân|người|quốc gia)\b|\bвсе (?:граждане|иностранц\w*|стран\w*)\b|"
    r"上述|以上|其他国|所有国|各国|全ての国|その他の国|모든 국가|기타 국가", re.I)
# A line that may open with a footnote mark, after an optional table pipe:
# the mark and the rest of the line. Matching this shape is not enough to
# make the line a footnote, because an ordinary numbered or starred list row
# ("1) Japan", "* Poland") has the same shape.
_FOOTNOTE_LINE_RE = re.compile(r"^\s*(?:\|\s*)?([*†‡]{1,3}|\(\d{1,2}\)|\[\d{1,2}\]|\d{1,2}\))\s*(\S.*)$")


def _defines_mark(before, mark, rest):
    """The line opens a footnote body and not a list row. A footnote defines
    a mark the page has already used, so the same mark must occur earlier
    attached to an entry or a cell ("Japan*", "Japan, Thailand (1)"), and the
    line itself must say something: a mark followed by a bare country name is
    a numbered list row, whatever shape it shares with a footnote."""
    if not re.search(r'\S\s?' + re.escape(mark), before):
        return False
    return not (_entry_line(rest) and any(_named(rest, nat) for nat in _known_nationalities()))


def _scope_blocks(text):
    """The (window text, subject text) pairs a captured page is read in.

    A line that defines a reference mark opens a footnote body, and the lines
    under it continue it, so a window stated inside that footnote is read
    with the nationalities the footnote itself names and with no others. A
    sentence that sits in no footnote body is read against the whole page,
    because a sunset the page states beside its list bounds what the page
    lists. A page that defines no mark is one pair, read whole as before.
    """
    lines = str(text or '').split('\n')
    marks = []
    for i, line in enumerate(lines):
        found = _FOOTNOTE_LINE_RE.match(line)
        if found and _defines_mark('\n'.join(lines[:i]), found.group(1), found.group(2)):
            marks.append(i)
    if not marks:
        yield text, text
        return
    bounds = marks + [len(lines)]
    for lo, hi in zip(bounds, bounds[1:]):
        body = '\n'.join(lines[lo:hi])
        if body.strip():
            yield body, body
    outside = '\n'.join(lines[:marks[0]])
    if outside.strip():
        yield outside, text


def _window_elsewhere(scope, nat):
    """The text the window was read from is another nationality's: it names
    other nationalities as travellers, names neither this one nor a group it
    belongs to, and does not speak of every traveller. A footnote that lists
    the nationalities its scheme covers bounds those and no others."""
    low = _norm(scope)
    if not low or _named(scope, nat) or _group_named(scope, nat) or _GENERAL_TRAVELLER_RE.search(low):
        return False
    return any(_named_as_traveller(scope, other) for other in _known_nationalities() if other != nat)


def _window_entry(low, start, end):
    """The list entry a parenthesised bound is attached to, or None when
    the bound is not in a parenthesis of its own after an entry of a run.

    The closing item of a run carries the bound for the whole run
    ("Brunei, the Philippines and Thailand (effective until July 31, 2022)"),
    so the entry has to be opened by a comma or a semicolon and the run has
    to go on after the parenthesis. Anything else leaves the window on the
    whole sentence, where it must be current and recorded.
    """
    open_at = max(low.rfind('(', 0, start), low.rfind('（', 0, start))
    if open_at < 0 or ')' in low[open_at:start] or '）' in low[open_at:start]:
        return None
    close_candidates = [i for i in (low.find(')', end), low.find('）', end)) if i >= 0]
    if not close_candidates:
        return None
    close_at = min(close_candidates)
    if '(' in low[end:close_at] or '（' in low[end:close_at]:
        return None
    inside = low[open_at + 1:close_at]
    for s, e, _ in reversed(_dates_in(inside)):
        inside = inside[:s] + ' ' + inside[e:]
    if not _BOUND_ONLY_RE.fullmatch(inside):
        return None
    if not re.match(r'\s*[,;，、；]\s*\S', low[close_at + 1:]):
        # Nothing follows the parenthesis as another item of the run, so this
        # entry closes it and the bound may be the whole run's.
        return None
    lead = low[:open_at]
    separators = list(re.finditer(r'[,;:，、；：]|\b(?:and|or|und|et|y|e|dan|và|и|hoặc|atau|или)\b', lead))
    if not separators:
        return None
    last = separators[-1]
    if not re.fullmatch(r'[,;，、；]', last.group(0)) or not _ITEM_BEFORE_RE.search(lead[:last.end()]):
        # A conjunction or a colon before the entry joins it to the items
        # before it instead of opening an item of its own.
        return None
    entry = lead[last.end():].strip(' *†‡')
    if not entry or not _entry_line(entry) or _ENTRY_SCOPE_RE.search(entry):
        return None
    return entry


def _policy_windows(text):
    """Every dated bound a rule sentence of the text states: (start, end,
    sentence, clause, entry, unreadable) with either side None when the bound
    gives only one, `entry` the list entry a parenthesised bound is attached
    to, or None, and `unreadable` the date the converter read but could tie
    to no relation it knows. A sentence stating several bounds yields each of
    them. Only a sentence about a rule counts, and never an office, issue,
    update or passport-validity date.

    A date in a rule sentence is always a bound of something. When no start
    or end relation in the vocabulary reaches it, that is a bound the
    converter cannot read and not the absence of one, so it fails closed.
    The one date that is no bound is the instrument's own: the day the
    resolution, decree or regulation stating the rule was dated, signed or
    printed, which the converter reads as such and passes over.
    """
    lead = ''
    for sentence in re.split(r'(?<=[.!?])\s+|[;。；！？\n]+', str(text or '')):
        low = _norm(sentence)
        previous, lead = lead, low
        if not low or _WINDOW_SKIP_RE.search(low):
            continue
        if not (_POLICY_WORD_RE.search(low) or any(re.search(p, low, re.I) for p, _ in _VERDICT_RULES.values())):
            continue
        dates = _dates_in(low)
        if not dates:
            continue
        used = set()
        for (s1, e1, d1), (s2, e2, d2) in zip(dates, dates[1:]):
            if s1 not in used and d1 <= d2 and _RANGE_BETWEEN_RE.fullmatch(low[e1:s2]):
                used.update((s1, s2))
                yield d1, d2, low, _clause(low, s1, e2), _window_entry(low, s1, e2), None
        for s, e, d in dates:
            if s in used:
                continue
            before, after = low[max(0, s - 40):s], low[e:e + 12]
            if _START_BEFORE_RE.search(before) or _START_AFTER_RE.match(after):
                yield d, None, low, _clause(low, s, e), _window_entry(low, s, e), None
            elif _END_BEFORE_RE.search(before) or _END_AFTER_RE.match(after):
                yield None, d, low, _clause(low, s, e), _window_entry(low, s, e), None
            elif _instrument_date(low, s, e, previous):
                continue
            else:
                yield None, None, low, _clause(low, s, e), _window_entry(low, s, e), low[s:e]


def _window_violation(quotes, bounds=None, where='the quoted policy window', value=None, nat=None, scope=None):
    """Why the dated policy window in the evidence cannot serve today's
    verdict: it has ended, it has not started, or the reviewer did not
    record the bound the page states. An end date must always be recorded
    (it is what expires the served verdict), and so must the start of a
    full interval. A start alone that has already passed says the rule is
    in force and needs no record. None when every window is current and
    recorded. `where` names the text the window was read from. `nat`
    lets a window that another entry, clause or footnote scopes to another
    nationality pass this one by; without it every window of the text bounds
    the verdict. Each quote is read on its own, so the passage a window sits
    in is the passage its subject is looked for in, unless `scope` names a
    wider passage the subject is read from: a sentence on a captured page is
    read against the whole page, a sentence in a footnote body against that
    footnote."""
    bounds = bounds or {}
    today = _today()
    for quote in quotes:
        elsewhere = None
        for start, end, low, clause, entry, unreadable in _policy_windows(quote):
            if value is not None and not _concerns_verdict(low, value):
                # A window of another verdict's rule on the captured page (a
                # K-ETA waiver under a visa requirement) does not bound this
                # verdict; the reviewer's own quotes are always read.
                continue
            if nat is not None and entry is not None and not _named(entry, nat) and not _group_named(entry, nat):
                # A bound in a parenthesis on one entry of the list ("North
                # Macedonia*(effective until March 31, 2030)") is that entry's
                # alone; on this nationality's own entry, or on a group it
                # belongs to, it bounds this verdict.
                continue
            if (nat is not None and not _named(clause, nat) and not _CLAUSE_GENERAL_RE.search(clause)
                    and any(_named(clause, other) for other in _known_nationalities() if other != nat)):
                # A window whose clause names another nationality ("the policy
                # for Russian passport holders runs until ...") is theirs; a
                # window whose clause names no one, or speaks of everyone,
                # covers this nationality too.
                continue
            if (nat is not None and entry is None and not _named(clause, nat)
                    and not _CLAUSE_GENERAL_RE.search(clause)):
                # The passage the window sits in may scope it the same way a
                # clause does: a footnote that names the nationalities of its
                # own scheme bounds those and no others.
                if elsewhere is None:
                    elsewhere = _window_elsewhere(quote if scope is None else scope, nat)
                if elsewhere:
                    continue
            if unreadable is not None:
                return f'{where} states a date no relation reaches, so the bound cannot be read: {unreadable}'
            if end is not None and end < today:
                return f'{where} ended on {end.isoformat()}: {low[:100]}'
            if start is not None and start > today:
                return f'{where} starts on {start.isoformat()}: {low[:100]}'
            if end is not None and bounds.get('effective_to') != end.isoformat():
                return f'{where} until {end.isoformat()} is not recorded as effective_to: {low[:100]}'
            if end is not None and start is not None and bounds.get('effective_from') != start.isoformat():
                return f'{where} from {start.isoformat()} is not recorded as effective_from: {low[:100]}'
    return None


def _checked_policy_bounds(proof, sources, route, product=None):
    """Keep policy dates tied to their own captured notice and subject.

    A check date does not renew a rule. Explicit interval evidence uses the
    serving provenance shape; absent separate evidence, the bound must occur
    in this review's own destination-government passages.
    """
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    from scripts.convert_reviewed_product_patch import _subject
    supplied = proof.get('policy_interval_evidence') or {}
    if not isinstance(supplied, dict):
        raise PatchRejected('policy interval evidence must be an object')
    bounds, checked = {}, {}
    for key in ('effective_from', 'effective_to'):
        value = proof.get(key)
        if value is None:
            if key in supplied:
                raise PatchRejected('policy interval evidence has no explicit bound: ' + key)
            continue
        try:
            day = date.fromisoformat(value)
            if day.isoformat() != value:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise PatchRejected('Invalid policy interval date: ' + key) from exc
        forms = (value, f'{day.day} {day.strftime("%B")} {day.year}',
                 f'{day.strftime("%B")} {day.day}, {day.year}',
                 f'{day.day:02d} {day.strftime("%B")} {day.year}',
                 f'{day.day}/{day.month}/{day.year}',
                 f'{day.day:02d}/{day.month:02d}/{day.year}',
                 f'{day.day:02d}.{day.month:02d}.{day.year}',
                 f'{day.year}年{day.month}月{day.day}日',
                 f'{day.year}년 {day.month}월 {day.day}일')
        evidence = supplied.get(key)
        if evidence is None:
            item = next((e for e in proof.get('evidence') or []
                         if jurisdiction_matches(str(e.get('source_url') or ''), route['destination_country'])
                         and any(quote_literal(form, e.get('quote') or '') for form in forms)
                         and _policy_bound_statement(e.get('quote'), forms, key)), None)
            if item is None:
                raise PatchRejected('Policy bound has no literal destination-source date: ' + key)
            evidence = dict(deepcopy(item), status='reviewed', verifier='ai',
                            verified_at=proof['verified_at'], subject=_subject(route, product),
                            note=proof['scope_note'], **{key: value})
        if (not isinstance(evidence, dict) or evidence.get('status') != 'reviewed'
                or evidence.get('verifier') != 'ai'
                or evidence.get('subject') != _subject(route, product)
                or evidence.get(key) not in (None, value)):
            raise PatchRejected('Policy bound evidence has the wrong review or subject: ' + key)
        source = sources.get(evidence.get('source_id'))
        quote = evidence.get('quote')
        if (source is None or source['url'] != evidence.get('source_url')
                or not jurisdiction_matches(source['url'], route['destination_country'])
                or not isinstance(quote, str) or '...' in quote or '…' in quote
                or not quote_literal(quote, source['text'])
                or not any(quote_literal(form, quote) for form in forms)
                or not _policy_bound_statement(quote, forms, key)):
            raise PatchRejected('Policy bound lacks captured literal date evidence: ' + key)
        try:
            reviewed = date.fromisoformat(str(evidence.get('verified_at') or '')[:10])
            if reviewed > _today():
                raise ValueError
        except ValueError as exc:
            raise PatchRejected('Invalid policy bound review date: ' + key) from exc
        bounds[key] = value
        checked[key] = deepcopy(evidence)
        checked[key]['subject'] = _subject(route, product)
    if bounds.get('effective_from') and bounds.get('effective_to') and bounds['effective_to'] < bounds['effective_from']:
        raise PatchRejected('Policy interval ends before it starts')
    if checked:
        bounds['policy_interval_evidence'] = checked
    return bounds


def _source_table(batch):
    sources = {}
    for source in batch.get('sources') or []:
        sid = source.get('id')
        if not sid or sid in sources:
            raise PatchRejected('Missing or duplicate source id')
        text = source.get('text')
        if not isinstance(text, str) or not text.strip():
            raise PatchRejected('Empty source capture: ' + str(source.get('url')))
        if hashlib.sha256(text.encode()).hexdigest() != source.get('sha256'):
            raise PatchRejected('Source capture hash mismatch: ' + str(source.get('url')))
        from app.visa_snapshot.authority import hostname, is_government_host
        if not is_government_host(hostname(str(source.get('url') or ''))):
            raise PatchRejected('Unofficial source: ' + str(source.get('url')))
        try:
            day = date.fromisoformat(str(source.get('checked_at', ''))[:10])
        except ValueError as exc:
            raise PatchRejected('Invalid source-read date') from exc
        if day > _today():
            raise PatchRejected('Future source-read date')
        sources[sid] = source
    return sources


def _empty_value(value):
    return value in (None, [], {}, '') or (isinstance(value, dict) and all(v is None for v in value.values()))


def _check_proof(proof, sources, route, field, value, *, product=None, detail=None, page_gates=False):
    """Return the validated proof or None for an explicit unknown/not_published.
    The prose a proof key covers (permitted_stay under permitted_stay_days)
    is emptied by _validate_row before this check when the proof is unknown
    or unpublished. That pop is the single mechanism. `detail` is the
    requirement subcategory a disposition proof must also support.
    `page_gates` reads the captured page sections and windows around the
    quotes as well (the general batch path); the pinned converters that
    reuse this reading were reviewed on their quotes and keep that."""
    if not isinstance(proof, dict) or proof.get('verifier', 'ai') != 'ai':
        raise PatchRejected(f'{field}: the source review must be attributed to AI')
    status = proof.get('status')
    if status in ('unknown', 'not_published'):
        if not _empty_value(value):
            raise PatchRejected(f'{field}: an unknown or unpublished value must be empty')
        if not str(proof.get('reason') or '').strip():
            raise PatchRejected(f'{field}: an unknown or unpublished value needs a reason')
        return None
    if status != 'reviewed':
        raise PatchRejected(f'{field}: unknown proof status')
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise PatchRejected(f'{field}: missing evidence')
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    destination = route['destination_country']
    for item in evidence:
        source = sources.get(item.get('source_id'))
        if not source or source['url'] != item.get('source_url'):
            raise PatchRejected(f'{field}: evidence cites an uncaptured page')
        quote = item.get('quote')
        short_ok = isinstance(quote, str) and _list_line(quote, route['passport_nationality'],
                                                         route.get('travel_document_type') or 'ordinary_passport')
        if not isinstance(quote, str) or (len(quote.strip()) < 8 and not short_ok) or '...' in quote or '…' in quote:
            raise PatchRejected(f'{field}: a quote must be a literal passage without ellipsis')
        if not quote_literal(quote, source['text']):
            raise PatchRejected(f'{field}: quote is not on its captured page')
    if not any(jurisdiction_matches(item['source_url'], destination) for item in evidence):
        raise PatchRejected(f'{field}: no destination-government page in the evidence')
    if not str(proof.get('scope_note') or '').strip():
        raise PatchRejected(f'{field}: the proof must state its scope')
    if not str(proof.get('verified_at') or '').strip():
        raise PatchRejected(f'{field}: the proof needs a verification date')
    passages = '\n'.join(item['quote'] for item in evidence)
    if proof.get('verification_scope') == 'consular_product_eligibility':
        if field != 'disposition' or not _consular_product_eligibility_supported(
                value, evidence, sources, route, product):
            raise PatchRejected('disposition: no scoped consular-product eligibility evidence')
    else:
        # One page per quoted line: a quote with a line break inside keeps its
        # page pairing line by line instead of losing the page binding.
        pages = [(item['source_id'], sources[item['source_id']]['text'])
                 for item in evidence for line in item['quote'].split('\n') if line.strip()]
        bounds = {k: proof.get(k) for k in ('effective_from', 'effective_to') if proof.get(k) is not None}
        urls = [item['source_url'] for item in evidence if jurisdiction_matches(item['source_url'], destination)]
        _check_value(field, value, passages, route, product, pages=pages, detail=detail, bounds=bounds, evidence_urls=urls,
                     page_gates=page_gates)
    if any(k in proof for k in ('effective_from', 'effective_to', 'policy_interval_evidence')):
        if field != 'disposition':
            raise PatchRejected('Policy bounds belong to the reviewed visa disposition')
        proof.update(_checked_policy_bounds(proof, sources, route, product))
    return proof


# A bare dollar sign is the claimed dollar currency only when no letter
# qualifies it: "HK$500" is Hong Kong dollars whatever currency is claimed.
_BARE_DOLLAR = r'(?<![A-Za-z])\$'
_CURRENCY_SYMBOLS = {
    'USD': (r'US\$', r'U\.S\.\$', _BARE_DOLLAR), 'EUR': (r'€', r'\beuros?\b'), 'GBP': (r'£',),
    'JPY': (r'¥', r'円', r'\byen\b'), 'CNY': (r'¥', r'元', r'\bRMB\b', r'人民币'), 'KRW': (r'₩', r'원', r'\bwon\b'),
    'INR': (r'₹', r'\bRs\.?', r'\brupees?\b'), 'THB': (r'฿', r'\bbaht\b'), 'IDR': (r'\bRp\.?', r'\brupiah\b'),
    'MYR': (r'\bRM\b', r'\bringgit\b'), 'SGD': (r'S\$', r'SG\$', _BARE_DOLLAR), 'HKD': (r'HK\$', _BARE_DOLLAR, r'元?港币', r'元?港幣'),
    'AUD': (r'A\$', r'AU\$', _BARE_DOLLAR),
    'CAD': (r'C\$', r'CA\$', r'CAN\$', _BARE_DOLLAR), 'TWD': (r'NT\$', _BARE_DOLLAR), 'RUB': (r'₽', r'руб\.?', r'\brub\b'),
    'VND': (r'₫', r'đ', r'VNĐ', r'\bdong\b'), 'PHP': (r'₱', r'\bpesos?\b'), 'NZD': (r'NZ\$', _BARE_DOLLAR), 'MXN': (r'MX\$', _BARE_DOLLAR),
    'MOP': (r'MOP\$',), 'CHF': (r'\bfrancs?\b', r'\bFr\.'), 'AED': (r'\bdirhams?\b', r'\bDhs?\b'),
    'SAR': (r'\briyals?\b', r'\bSR\b'), 'TRY': (r'₺', r'\blira\b'), 'EGP': (r'\bE£', r'\bLE\b'), 'BRL': (r'R\$'),
}
# The dollar currency a qualified dollar sign names.
_DOLLAR_PREFIXES = {'us': 'USD', 'u.s.': 'USD', 's': 'SGD', 'sg': 'SGD', 'hk': 'HKD', 'a': 'AUD', 'au': 'AUD', 'c': 'CAD', 'ca': 'CAD',
                    'can': 'CAD', 'nt': 'TWD', 'nz': 'NZD', 'mx': 'MXN', 'r': 'BRL', 'mop': 'MOP', 'b': 'BND', 'bz': 'BZD', 'fj': 'FJD',
                    'j': 'JMD', 'tt': 'TTD', 'bds': 'BBD', 'ec': 'XCD', 'na': 'NAD', 'z': 'ZWL'}


def _foreign_dollar_amount(passages, code, amount):
    """The qualified dollar figure of another currency that equals the
    claimed amount ("HK$500" under a claimed USD 500), or None."""
    try:
        claimed = float(amount)
    except (TypeError, ValueError):
        return None
    low = _norm(passages)
    for m in re.finditer(r'(?<![a-z])([a-z.]{1,3})\$\s*(\d[\d,]*(?:\.\d+)?)', low):
        currency = _DOLLAR_PREFIXES.get(m.group(1))
        try:
            figure = float(m.group(2).replace(',', ''))
        except ValueError:
            continue
        if currency and currency != code and figure == claimed:
            return m.group(0)
    return None


def _monetary_text(passages, code):
    """Official pages write $25, Rp1.650.000 or 715元; the validator needs the
    ISO code beside the number. Only the value's own currency is rewritten."""
    text = passages
    for symbol in _CURRENCY_SYMBOLS.get(code, ()):
        text = re.sub(symbol, f' {code} ', text, flags=re.I)
    # Official tariffs also write "SAR (300)". Remove only parentheses that
    # enclose the complete amount immediately after this currency; dates,
    # references and unrelated parenthesized figures remain untouched.
    text = re.sub(r'\b' + re.escape(code) + r'\s*\((\d[\d,]*(?:\.\d+)?)\)',
                  lambda m: f'{code} {m.group(1)}', text, flags=re.I)
    # European and Indonesian figures: 1.650.000 or 500.000,00 mean 1650000
    # and 500000.00. Only groups of exactly three digits are thousands.
    text = re.sub(r'(?<!\d)(\d{1,3}(?:\.\d{3})+),(\d{2})(?!\d)', lambda m: m.group(1).replace('.', '') + '.' + m.group(2), text)
    text = re.sub(r'(?<!\d)(\d{1,3}(?:\.\d{3})+)(?![\d,])', lambda m: m.group(1).replace('.', ''), text)
    # The validator consumes "1 IDR" out of "B1 IDR 500000" and then cannot see
    # the code beside the amount; repeat the code after the amount so either
    # reading finds the same literal number.
    text = re.sub(r'\b' + code + r'\s*(\d[\d,]*(?:\.\d+)?)', lambda m: f'{code} {m.group(1)} {code}', text)
    return text


# An electronic travel authorisation by name. The bare token "esta" is also
# the Spanish demonstrative, so it counts only in an English frame ("an
# ESTA", "ESTA approval"), never on its own.
_EAR_TOKEN = (r"\b(?:eta|etas|k-eta|evisitor|etias|nzeta|e-?ta)\b|"
              r"\b(?:an|the|valid|approved|your|their|with|without|via|through|obtain|obtaining|apply for|denied|approve)\s+esta\b|"
              r"\besta\s+(?:approval|applications?|authori[sz]ation|registration|fee|website|is|are|will|before|prior|or|and|for|to)\b|"
              r"electronic travel authori[sz]ation|electronic travel authority|electronic system for travel authori[sz]ation|"
              r"travel authori[sz]ation|pre-arrival registration|电子旅行授权|電子旅行許可|전자여행허가|케이이티에이")
# A word that states a requirement, in the languages of the captured pages.
_REQUIREMENT_WORD = (r"\b(?:required|require|requires|requiring|requirement|must|need|needs|needed|mandatory|compulsory|obligatory|"
                     r"obligation|necessary|obligatoire|obligatoirement|nécessaire|doivent|doit|devez|obligatori[oa]s?|necesari[oa]s?|"
                     r"deben|debe|necesita|necesitan|erforderlich|benötigen|müssen|verpflichtend|obbligatori[oa]|devono|deve|"
                     r"necessário|obrigatório|devem|precisam|phải|cần|bắt buộc|wajib|harus|diperlukan|требуется|необходим\w*|"
                     r"должны|обязательн\w*)\b|必要|必須|必须|需要|需|须|義務|义务|필요|필수|의무|ต้อง|จำเป็น|يجب|ضروري|إلزامي")
# Up to 80 characters that stay inside one clause: no sentence end, comma,
# colon, or coordinating conjunction.
_SAME_CLAUSE = (r"(?:(?!\s(?:and|or|but|nor|et|ou|mais|y|o|pero|und|oder|aber|dan|atau|tetapi|và|hoặc|nhưng|и|или|но)\s)[^.;,:\n])"
                r"{0,80}")

# A rule that is suspended, terminated, withdrawn, expired, or stated in
# the past or the future is not today's rule, in the languages of the
# captured pages. "No longer" flips an exemption only in its suspension
# sense ("no longer visa-free"), while "no longer need a visa" is one.
_SUSPENDED = (r"\bsuspend\w*|\bsuspens\w*|\bterminat\w*|\bdiscontinu\w*|\brevoked|\brevocation|\bwithdrawn|\babolished|\bcancell?ed|\bhalted|\bceased|"
              r"\brescinded|\bno longer (?:visa|exempt|appl|valid|in (?:force|effect)|available|eligible|entitled|offered|granted|accept)|"
              r"\b(?:has|have|had|is|are) expired\b|\bexpired on\b|\bsuspendu\w*|\bsuspendid[oa]s?\b|\bsuspensión|\bsuspensão|\bsospes\w*|"
              r"\bsospensione|\bausgesetzt|\baufgehoben|\bbeendet|\bwiderrufen|\bdihentikan|\bditangguhkan|\bdibatalkan|\bdicabut|"
              r"đình chỉ|tạm dừng|tạm ngừng|chấm dứt|bãi bỏ|thu hồi|停止|中止|中断|中斷|暂停|暫停|终止|終止|取消|廃止|廢止|停用|终了|終了|失効|"
              r"중단|중지|정지|폐지|종료|취소|ระงับ|ยกเลิก|สิ้นสุด|приостановлен\w*|отменен\w*|прекращ\w*|аннулирован\w*|отозван\w*|"
              # A temporary measure or one in force until further notice is
              # not a rule that can be served without its own dated window.
              r"\buntil further notice\b|\btill further notice\b|\bpending further notice\b|"
              r"\btemporar(?:y|ily)\b\s+(?:suspend\w*|halt\w*|stopp?\w*|lift\w*|waiv\w*|exempt\w*|extend\w*|allow\w*|permit\w*|grant\w*|implement\w*|"
              r"introduc\w*|ceas\w*|discontinu\w*|reintroduc\w*|imposed?|in (?:force|effect)|applicable|available|unavailable|admit\w*|restrict\w*|"
              r"clos\w*|open\w*|relax\w*|relief|arrangement|measure|policy|exemption|waiver|scheme|visa[- ]free|visa[- ]exempt\w*)|"
              r"\btemporary (?:suspension|measures?|polic(?:y|ies)|exemptions?|waivers?|arrangements?|schemes?|visa[- ]free|visa exemptions?|lifting|halt|ban|"
              r"closure|restrictions?|relief|entry (?:permit|arrangement))|"
              r"\btemporairement\b|\bà titre temporaire\b|\bjusqu[’']à nouvel ordre\b|\bprovisoirement\b|\btemporalmente\b|\bhasta nuevo aviso\b|"
              r"\bprovisionalmente\b|\btemporariamente\b|\baté novo aviso\b|\bvorübergehend\b|\bbis auf weiteres\b|\bвременно\b|\bдо дальнейшего уведомления\b|"
              r"\bдо особого распоряжения\b|\buntuk sementara\b|\bsementara waktu\b|\bhingga pemberitahuan lebih lanjut\b|\bsampai pemberitahuan lebih lanjut\b|"
              r"\btạm thời (?:miễn|dừng|ngừng|đình chỉ|áp dụng|cho phép|không)|\bcho đến khi có thông báo mới\b|"
              r"(?:ระงับ|ยกเว้น|ยกเลิก|มีผล|บังคับใช้|อนุญาต|ผ่อนผัน)[^.\n]{0,12}ชั่วคราว|จนกว่าจะมีประกาศ|一時的|当面の間|追って通知|"
              r"暂时|暫時|另行通知|另有通知|일시적|추후 공지|별도 공지")
_PAST_OR_FUTURE = (r"\b(?:was|were|had been|used to be)\s+(?:visa[- ]?free|visa[- ]?exempt|exempt(?:ed)?)\b|"
                   r"\bwill\s+(?:be|become)\s+(?:visa[- ]?free|visa[- ]?exempt|exempt(?:ed)?|eligible|able|entitled)\b|\bwill no longer\b|"
                   r"\b(?:applied|applies|applicable)\s+(?:until|through|up to|till)\b|"
                   r"\b(?:formerly|previously)\s+(?:visa[- ]?free|visa[- ]?exempt|exempt(?:ed)?|eligible|entitled|required|applicable|in force|available|allowed|permitted)\b|"
                   r"即将|將於|将于|将自|將自|予定|예정|sẽ được miễn|akan dibebaskan")
_NOT_TODAYS_RULE = _SUSPENDED + '|' + _PAST_OR_FUTURE

_VERDICT_RULES = {
    # A sentence that states the rule, and the words that flip it.
    'VISA_REQUIRED': (r"(?:e-?visa|visa)s?\b[^.;\n]{0,60}\b(?:is |are )?(?:required|mandatory|needed|necessary|obligatoire|obligatorio|necesario|bắt buộc)|"
                      r"\b(?:need|needs|require|requires|must have|must hold|must obtain|are required to hold|are required to obtain|is subject to|are subject to|"
                      r"remains? subject to|stays? subject to|continues? to be subject to|still subject to|still (?:need|needs|require|requires))\b (?:a |an |the )?(?:valid |prior |entry |tourist |schengen |visitor |short[- ]stay )*(?:e-?visa|visa)s?\b|"
                      r"\b(?:needs?|requir(?:es|ing|ed))\b (?:an? )?entry clearance|entry clearance \(a visa\)|"
                      r"\bnecesita(?:n|r[áa]n?)? (?:de )?(?:un |el )?visado|\brequiere(?:n)? (?:de )?(?:un |el )?visado|\bont besoin d['’]un visa|\bdoivent (?:obtenir|demander|solliciter) un visa|\bvisa (?:est |sera )?(?:requis|nécessaire|exigé)|"
                      r"\b(?:soumis|subordonné)e?s? à l['’]obtention d['’]un visa|\bmunie?s? d['’]un visa|"
                      r"\bnecessitano (?:di )?un visto|\bvisto (?:è )?(?:richiesto|necessario|obbligatorio)|\bbenötigen ein visum|\bvisumpflichtig\b|\bprecisam de visto|\bvisto (?:é )?(?:obrigatório|necessário)|"
                      r"\b(?:need|needs|require|requires|must|shall|should|have to|has to|required to|doivent|doit|deben|debe|phải|cần)\b[^.;\n]{0,40}"
                      r"\b(?:obtain|hold|have|apply for|possess|be in possession of|get|obtenir|être munis?|obtener|xin|có)\b[^.;\n]{0,40}\b(?:e-?visa|visa|thị thực)\b|"
                      r"\b(?:can|may|eligible to|entitled to) apply (?:for )?(?:an? |the )?(?:\w+ ){0,2}(?:e-?visa|electronic visa)\b|"
                      # "may apply for a visa" proves a requirement only beside a requirement word;
                      # alone it also describes an optional longer-stay visa.
                      r"\b(?:required|must|need|needs|mandatory|compulsory|obligatory)\b[^.;\n]{0,80}\b(?:can|may|eligible to|entitled to) apply (?:for )?(?:an? |the )?(?:\w+ ){0,2}visa\b|"
                      r"\b(?:can|may|eligible to|entitled to) apply (?:for )?(?:an? |the )?(?:\w+ ){0,2}visa\b[^.;\n]{0,80}\b(?:required|must|need|needs|mandatory|compulsory|obligatory)\b|"
                      r"\bmay be (?:granted|issued) (?:with )?(?:an? )?e-?visa|"
                      r"\beligible for (?:the |an? )?(?:\w+ ){0,2}e-?visa|"
                      r"\b(?:grant|granted|issue|issued)\b[^.;\n]{0,30}\b(?:visit|tourist|entry|e-?)visas?\b[^.;\n]{0,80}\b(?:to|for) (?:foreigners|nationals|citizens|holders)|"
                      r"виз[аы] по всем|требуется виза|необходима виза|нужна виза|оформить визу|получить визу|должны иметь[^.;\n]{0,30}визу|"
                      # An online visa (網簽) is a requirement only beside a requirement marker.
                      # "可申請網簽" offers the option.
                      r"需要办理签证|需申请签证|应当申请签证|必须持有签证|需要签证|事前に査証|ビザが必要|签证申请|"
                      r"(?:需要|必須|必须|應|应|須|须|需)[^。；;\n]{0,20}(?:網簽|网签)|(?:網簽|网签)[^。；;\n]{0,20}(?:需要|必須|必须|應|应|須|须|需)|"
                      r"비자.{0,6}필요|ต้องขอวีซ่า|wajib memiliki visa|harus memiliki visa|يجب الحصول على تأشيرة",
                      r"visa[- ]free|no visa|without (?:a )?visa|exempt|not required|do(?:es)? not (?:require|need)|\bnot (?:need|require)\b|\bneed not\b|\bnot needed\b|"
                      r"\b(?:visa )?(?:on|upon) arrival\b|без виз|sans visa|sin visa|miễn thị thực|không cần|không phải xin|không yêu cầu|"
                      r"免签|无需签证|免办签证|査証免除|ビザ免除|무비자|면제|bebas visa|ยกเว้นวีซ่า|不需|無需|无需|毋須|毋须|免辦|免办|"
                      r"\bno longer\b|\blifted\b|\babolished\b|\bwaived\b|\bscrapped\b|\bunless\b|\bsauf si\b|\ba menos que\b|\bes sei denn\b|\btrừ khi\b|\bkecuali jika\b"),
    'VISA_EXEMPT': (r"visa[- ]free|visa[- ]exempt|exempt(?:ed|ion)? from (?:the |a |an )?(?:(?:short[- ]stay|short[- ]term|entry|tourist|visitor|schengen|port of entry) )?(?:visa|obtaining a visa|visas?(?: requirements?)?)|"
                    r"exempt(?:ed)? from (?:the )?(?:requirement|obligation|need) (?:to obtain|to hold|to get|of obtaining|of holding) (?:a |an )?visa|"
                    r"do(?:es)? not (?:require|need) (?:a |an |any )?(?:entry |tourist |visitor )?visa|do(?:es)? not (?:require|need) to (?:apply for|obtain|hold|have) (?:a |an )?visa|"
                    r"\b(?:will|shall|would) (?:therefore |also |thus |then |generally )?not (?:require|need) (?:a |an |any )?(?:schengen |short[- ]stay |entry |tourist |visitor )*visa|"
                    r"\bneed not (?:obtain|apply for|hold|have) (?:a |an )?visa|"
                    r"do(?:es)?n['’]t (?:require|need) (?:a |an |any |to (?:apply for|obtain|hold|have) (?:a |an )?)?(?:entry |tourist |visitor )?visa|"
                    r"without (?:a |an |the need for a )?(?:entry |tourist )?visa|no visa (?:is )?(?:required|needed|necessary)|not required to (?:obtain|hold|apply for) (?:a |an )?visa|"
                    r"visas? (?:is |are )?(?:generally |normally |usually )?not required|not requir(?:ing|ed to (?:have|hold|obtain)) (?:a |an )?(?:visitor |tourist |entry )?visa|"
                    r"no longer (?:need|needs|require|requires) (?:to (?:obtain|apply for|hold|have) )?(?:a |an |any )?(?:entry |tourist |visitor )?visa|"
                    r"visa(?: requirements?)? (?:is |are )?waived|need only (?:a )?valid passport|"
                    r"без виз|безвизов|sans visa|dispensée?s? de visa|exemptée?s? de visa|n['’](?:ont|avez|a|avons) pas besoin (?:d['’]un |de )?visa|"
                    r"sin visa(?:do)?|exent[oa]s? de visa(?:do)?|exención de visa(?:do)?|exonerad[oa]s? de visa|no (?:se )?requiere(?:n)?(?: de)? (?:un |una |el |la )?visa(?:do)?|no necesita(?:n)?(?: de)? (?:un |una |el |la )?visa(?:do)?|"
                    r"isent[oa]s? de vistos?|isenção de visto|sem visto|não precisam? de visto|visumfrei|visumsfrei|ohne visum|kein visum|senza visto|esent[ie] dal visto|non hanno bisogno di visto|"
                    r"visumvrij|geen visum|nepodliehajú vízovej povinnosti|nepodléhají vízové povinnosti|bez víz|izuzet[ia]? (?:su |je )?od vizn(?:og|e)|bez vize|ne trebaju vizu|oslobođeni (?:su )?(?:od )?viz|"
                    r"miễn thị thực|không cần (?:xin )?(?:visa|thị thực)|ยกเว้นวีซ่า|bebas visa|visa tidak diperlukan|tidak (?:memerlukan|perlu) visa|免签|无需签证|免办签证|查証免除|査証免除|ビザ免除|ビザなし|무비자|사증면제|معفى|إعفاء من التأشيرة|vizeden muaf",
                    r"\bnot (?:visa[- ]free|exempt|eligible)|do(?:es)? not (?:qualify|benefit)|unless|except(?:ion)? (?:for|of)?\s*(?:holders|nationals|citizens) of|"
                    r"(?<!no )(?<!sin )(?<!sans )(?:visa|e-?visa) (?:is |are )?(?:required|mandatory)|must (?:obtain|hold|apply)|"
                    r"\bremains? subject to\b|\bstill (?:require|requires|need|needs)\b|\bcontinues? to (?:require|need)\b|\bineligible\b|" + _NOT_TODAYS_RULE),
    # The authorisation token must sit in the same clause as a requirement
    # word, with no comma, colon or coordinating conjunction between them:
    # a sentence that offers the online service, names a product or a
    # portal, or prices it states no requirement.
    'ELECTRONIC_AUTHORIZATION_REQUIRED': (r"(?:" + _REQUIREMENT_WORD + r")" + _SAME_CLAUSE + r"(?:" + _EAR_TOKEN + r")|(?:" + _EAR_TOKEN + r")" + _SAME_CLAUSE + r"(?:" + _REQUIREMENT_WORD + r")",
                                          r"not required|not need|no need|n['’]t need|exempt(?:ed)? from|\bwithout\b|\bsans\b|\bohne\b|\btanpa\b|do(?:es)? not need|不需|無需|无需|免除|필요 없|없이|không cần|"
                                          r"(?:" + _EAR_TOKEN + r")\s+(?:portal|website|web ?site|webpage|page|link|guide|information|form|system|service|counter|fee|logo|app)\b|" + _NOT_TODAYS_RULE),
    # The arrival wording must sit beside a visa noun in the same clause; a
    # stamp, card or check "on entry" carries no visa meaning of its own.
    'VISA_ON_ARRIVAL': (r"visa[- ]on[- ]arrival|visas?\b[^.;,\n]{0,60}\b(?:on|upon) (?:arrival|arriving|entry|entering)\b|\b(?:on|upon) (?:arrival|entry)\b[^.;,\n]{0,25}\bvisas?\b|"
                        r"visas?\b[^.;,\n]{0,60}\bat the (?:airport|border|port of entry)\b|"
                        r"(?:visa|visto|visado|виз\w*)\b[^.;,\n]{0,40}(?:saat kedatangan|à l['’]arrivée|a la llegada|à chegada|по прибытии)|"
                        r"(?:saat kedatangan|à l['’]arrivée|a la llegada|à chegada|по прибытии)[^.;,\n]{0,40}(?:visa|visto|visado|виз)|"
                        r"落地签|落地簽|到着ビザ|도착비자|e-?voa",
                        r"not (?:available|eligible|issued)|no visa on arrival|cannot obtain|do(?:es)? not (?:need|require)|visa[- ]free|without (?:a |an )?visa|exempt|" + _NOT_TODAYS_RULE),
}

# A statement that removes the nationality from a rule without an
# exception word: "not covered by this arrangement", "excluded from the
# scheme", "removed from the visa-free list", "does not extend to", "not
# eligible for". Read with the verdict noun it excludes from.
_EXCLUSION_RE = re.compile(
    r"\b(?:does|do|did|will|would|shall|can|could)\s+not\s+(?:currently\s+|yet\s+|presently\s+)?(?:apply|extend|cover|include|benefit|qualify|concern|pertain)\b|"
    r"\bnot\s+(?:be\s+)?(?:covered|included|listed|applicable|eligible|entitled|concerned|admitted|accepted|qualified)\b|"
    r"\b(?:is|are|was|were|has been|have been|remains?|stays?|be)\s+(?:excluded|removed|struck|deleted|withdrawn|omitted|dropped|delisted|barred)\b|"
    r"\b(?:excluded|removed|struck|deleted|withdrawn|omitted|delisted|barred)\s+from\b|\bexclusion\s+of\b|"
    r"\bnot\s+(?:on|in|among|within)\s+(?:the\s+|this\s+|that\s+|our\s+)?(?:[a-z\-]+\s+){0,3}(?:lists?|annex|schedule|table|scheme|programme|program|arrangement|agreement|category|categories|countries|nationalities)\b|"
    r"\b(?:ineligible|not eligible|no longer eligible|not entitled|no longer entitled)\b|"
    r"\bremains?\s+subject\s+to\b|\bstill\s+(?:require|requires|need|needs|subject)\b|\bcontinues?\s+to\s+(?:require|need)\b|"
    r"\bunder\s+review\b|\bno\s+longer\s+(?:appl\w+|cover\w*|includ\w*|extend\w*|available|valid|in force|in effect|eligible|entitled|benefit\w*)\b|"
    r"\bne\s+s['’]applique(?:nt)?\s+pas\b|\bne\s+(?:sont|est)\s+pas\s+(?:concerné|couvert|inclus|admis|éligible)|\bexclu[es]?\s+(?:de|du|des)\b|\bretiré[es]?\s+(?:de|du|des)\b|"
    r"\bne\s+bénéficie(?:nt)?\s+pas\b|\bne\s+figure(?:nt)?\s+pas\b|"
    r"\bno\s+(?:se\s+)?aplica(?:n)?\b|\bno\s+(?:está|están|es|son)\s+(?:incluid|cubiert|exent|comprendid)|\bexcluid[oa]s?\s+(?:de|del)\b|\bretirad[oa]s?\s+(?:de|del)\b|"
    r"\bno\s+figura(?:n)?\b|\bno\s+se\s+benefician?\b|"
    r"\bnão\s+se\s+aplica(?:m)?\b|\bnão\s+(?:está|estão|é|são)\s+(?:incluíd|abrangid|contemplad)|\bexcluíd[oa]s?\s+(?:de|do|da|dos|das)\b|\bretirad[oa]s?\s+(?:de|do|da)\b|\bnão\s+constam?\b|"
    r"\bnon\s+si\s+applica(?:no)?\b|\besclus[oiae]\s+(?:da|dal|dalla|dai|dagli|dalle)\b|\brimoss[oiae]\b|\bnon\s+(?:sono|è)\s+(?:inclus|compres)|"
    r"\bgilt\s+nicht\b|\bgelten\s+nicht\b|\bnicht\s+(?:erfasst|einbezogen|enthalten|aufgeführt|berechtigt|umfasst)\b|\bausgeschlossen\b|\bgestrichen\b|\bnicht\s+mehr\s+(?:gilt|gelten|aufgeführt)|"
    r"\bне\s+распространяется\b|\bне\s+применяется\b|\bне\s+применяются\b|\bисключен\w*\b|\bне\s+входит\b|\bне\s+входят\b|\bне\s+включен\w*\b|\bне\s+имеют?\s+права\b|\bне\s+подпадают?\b|"
    r"\btidak\s+berlaku\b|\bdikecualikan\b|\btidak\s+termasuk\b|\bdihapus(?:kan)?\b|\bdicoret\b|\btidak\s+berhak\b|\btidak\s+mencakup\b|"
    r"\bkhông\s+áp\s+dụng\b|\bbị\s+loại\b|\bloại\s+trừ\b|\bkhông\s+bao\s+gồm\b|\bkhông\s+thuộc\b|\bkhông\s+được\s+hưởng\b|\bbị\s+đưa\s+ra\s+khỏi\b|\bbị\s+xóa\b|"
    r"ไม่รวม|ไม่ใช้บังคับ|ถูกถอด|ไม่มีสิทธิ|ไม่อยู่ในรายชื่อ|ไม่ครอบคลุม|ถูกตัดออก|"
    r"適用されません|適用されない|適用外|対象外|対象となりません|対象とならない|除外|含まれません|含まれない|削除|除かれ|"
    r"不适用|不適用|不包括|不包含|不含|不在.{0,12}(?:之列|名单|名單|范围|範圍)|已被移除|已移除|已删除|已刪除|不享受|不享有|不属于|不屬於|"
    r"적용되지\s*않|적용\s*대상이\s*아니|제외|포함되지\s*않|삭제|해당되지\s*않|해당\s*없|대상이\s*아닙니다", re.I)
# The noun each verdict is known by, so an exclusion reads as an exclusion
# from this verdict, not from another.
_VERDICT_NOUNS = {
    'VISA_EXEMPT': r"visa[- ]?free|\bexempt\w*|waiver|免签|免簽|査証免除|ビザ免除|무비자|사증면제|miễn thị thực|bebas visa|ยกเว้นวีซ่า|"
                   r"без виз|безвиз\w*|sans visa|exención|isenção|visumfrei\w*|dispense",
    'VISA_ON_ARRIVAL': r"visa[- ]on[- ]arrival|(?:on|upon) arrival|落地签|落地簽|到着ビザ|도착비자|\bvoa\b",
    'ELECTRONIC_AUTHORIZATION_REQUIRED': _EAR_TOKEN,
    'VISA_REQUIRED': r"(?<!no )(?<!without )(?<!sans )(?<!sin )\bvisas?\b(?![- ]?(?:free|exempt|waiver|on[- ]arrival))|visado|visto|visum|签证|簽證|査証|ビザ|비자|thị thực|วีซ่า|виз\w*",
}
# A sentence whose subject is the rule stated before it.
_ANAPHOR_RE = re.compile(r"^\s*(?:this|these|that|those|it|they|the (?:above|foregoing|said|same)|such)\b|"
                         r"\b(?:this|these|that|those|such|the (?:above|foregoing|said|same))\s+(?:arrangement|scheme|programme|program|policy|measure|facility|"
                         r"list|rule|provision|regime|agreement|treatment|category|benefit|exemption|waiver|requirement|facilit\w+)s?\b|"
                         r"^\s*(?:ceci|cela|celui-ci|esto|esta|ello|isto|isso|dies|dieses|это|này|ini|นี้|これ|この|上記|上述|이는|이것)", re.I)


def _states_opposite(low, value):
    """The normalized sentence states another verdict in rule words and
    not this one: a carve-out or an exclusion in it is from that other
    rule, which is consistent with this verdict."""
    positive, _ = _VERDICT_RULES.get(value, (None, None))
    if positive and re.search(positive, low, re.I):
        return False
    for key, (other, negative) in _VERDICT_RULES.items():
        if key != value and re.search(other, low, re.I) and not (negative and re.search(negative, low, re.I)):
            return True
    return False


def _concerns_verdict(low, value):
    """The normalized sentence speaks of this verdict: it names this
    verdict's noun, names no verdict noun but speaks of a rule (a policy
    word, or the rule before it by anaphora), or refers to the rule by
    anaphora. A suspension of another verdict's benefit ("visa on arrival
    is suspended" under an exemption) or of something that is no rule
    ("the reciprocity fee has been suspended") is not a suspension of this
    verdict."""
    present = {key for key, noun in _VERDICT_NOUNS.items() if re.search(noun, low, re.I)}
    if present:
        return value in present or bool(_ANAPHOR_RE.search(low))
    return bool(_POLICY_WORD_RE.search(low) or _ANAPHOR_RE.search(low))


# The scheme a verdict rests on, by the nouns a page calls it. An exclusion
# from the scheme is an exclusion from the verdict the scheme carries.
_SCHEME_NOUN_RE = re.compile(
    r"\b(?:arrangements?|schemes?|programmes?|programs?|agreements?|treat(?:y|ies)|conventions?|protocols?|memorandum|memoranda|"
    r"lists?|annexe?s?|schedules?|tables?|waivers?|exemptions?|polic(?:y|ies)|regimes?|facilit(?:y|ies)|frameworks?|"
    r"measures?|initiatives?|mechanisms?|arrangement)\b|"
    r"协定|协议|協定|協議|安排|名单|名單|附件|制度|措置|措施|협정|목록|제도|thỏa thuận|danh sách|perjanjian|daftar", re.I)
# Being in the scheme, and being out of it. A page writes membership as many
# ways as it writes a verdict, so the predicate is read in the clause that
# holds it and not matched against a list of sentences.
_MEMBERSHIP_WORD_RE = re.compile(
    r"\b(?:part(?:y|ies)|members?|signator(?:y|ies)|participants?|participates?|participate|participating|participation|"
    r"beneficiar(?:y|ies)|covered|coverage|included|includes?|include|inclusion|listed|eligible|eligibility|entitled|"
    r"joins?|joined|joining|accedes?|acceded|acceding|accession|added)\b", re.I)
# Removal from the scheme written without an exception word and without a
# membership predicate of its own.
_SCHEME_REMOVED_RE = re.compile(
    r"\b(?:left|kept|shut|locked)\s+out\s+(?:of|from)\b|\bleft\s+off\b|\bfell\s+off\b|\bdropped\s+from\b|"
    r"\bnot\s+(?:among|part\s+of|one\s+of|a\s+party\s+to)\b|\bomitted\s+from\b|\boutside\s+the\s+scope\b", re.I)
_MEMBERSHIP_NEGATOR_RE = re.compile(
    r"\b(?:not|no|never|neither|nor|cannot|can[’']?t|don[’']?t|doesn[’']?t|didn[’']?t|isn[’']?t|aren[’']?t|wasn[’']?t|weren[’']?t|"
    r"without|outside|lacks?|lacking|fails?|failed|unless|ceased|excluded|omitted|dropped|barred|removed|struck|withdrawn)\b|"
    r"\bno\s+longer\b|\bleft\s+out\b|\bkept\s+out\b|\byet\s+to\b", re.I)
# A membership that has not started is not a membership today, however
# affirmative its wording.
_MEMBERSHIP_DEFERRED_RE = re.compile(
    r"\b(?:will|shall|is\s+to|are\s+to|expected\s+to|due\s+to|once|when|pending|upon\s+ratification|after\s+ratification)\b|"
    r"\bat\s+a\s+later\s+(?:date|stage)\b|\bin\s+due\s+course\b|\bnot\s+yet\b|\bfrom\s+a\s+later\b", re.I)
# How far back of a membership word its own predicate reaches. A negator
# further back than this belongs to another predicate of the same clause
# ("nationals who do not hold a machine readable passport are members of
# the scheme").
_MEMBERSHIP_REACH = 48
# The words that may stand between a negator and the membership word it
# negates ("is not YET A party", "will not BE added", "are not, AT PRESENT,
# members"). Any other word between them is a predicate of its own, so the
# negator is that predicate's ("nationals who do not HOLD A MACHINE-READABLE
# PASSPORT ARE members of the scheme").
_MEMBERSHIP_GAP_WORDS = frozenset(
    'a an the be been being yet currently presently present moment time for longer still among one of to part in on any all as at '
    'this that these those such its their formally officially fully full now then also even ever considered deemed regarded '
    'listed counted included covered eligible entitled'.split())


def _membership_started(low):
    """The membership the normalized sentence defers has a stated start that
    has passed ("starting from 1 January 2021 the United Kingdom will be
    added to the list"), so it is a membership today. A deferral with no
    start, or with a start still to come, stays a deferral."""
    today = _today()
    return any(day <= today and _START_BEFORE_RE.search(low[max(0, s - 40):s])
               for s, _, day in _dates_in(low))


def _membership_negated(clause, at):
    """The membership word at this offset of the clause is the one the clause
    negates: a negator stands between the subject and the word, inside the
    clause, within the reach of the predicate, and with nothing but function
    words between it and the membership word."""
    window = clause[max(0, at - _MEMBERSHIP_REACH):at]
    for negator in _MEMBERSHIP_NEGATOR_RE.finditer(window):
        between = re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", window[negator.end():])
        if all(word in _MEMBERSHIP_GAP_WORDS for word in between):
            return True
    return False


def _membership_denied(low):
    """The normalized sentence says the traveller it names is out of the
    scheme the verdict rests on.

    The sentence has to name a scheme. Then a removal written as prose ("was
    left out of the 2026 arrangement"), a membership word its own predicate
    negates ("is not a party to the arrangement", "the list of beneficiary
    countries does not include Myanmar"), and a membership that has not
    started yet ("joins the arrangement at a later date") all read as out.
    A membership word with a negator in front of it that the converter cannot
    read as anything else reads as out too, because an unclassified predicate
    proves the scheme covers nobody.
    """
    if not _SCHEME_NOUN_RE.search(low):
        return False
    if _SCHEME_REMOVED_RE.search(low):
        return True
    for mark in _MEMBERSHIP_WORD_RE.finditer(low):
        clause = _clause(low, mark.start(), mark.end())
        if _MEMBERSHIP_DEFERRED_RE.search(clause) and not _membership_started(low):
            return True
        # The negator is looked for in the sentence, not the comma clause: a
        # parenthetical between them ("are not, at present, parties") is
        # crossed, while any content word in between belongs to another
        # predicate and stops the reading.
        if _membership_negated(low, mark.start()):
            return True
    return False


def _excluded_by_statement(sentence, nat, value):
    """The sentence names the nationality and removes it from this verdict:
    an exclusion predicate applied to this verdict's noun, to a rule
    referred to by anaphora, or to nothing named at all, or a denial of
    membership in the scheme the verdict rests on."""
    if not _named(sentence, nat):
        return False
    low = _norm(sentence)
    if _states_opposite(low, value):
        return False
    denied = _membership_denied(low)
    if not _EXCLUSION_RE.search(low) and not denied:
        return False
    # A denial of membership names its own subject, the scheme, so it does
    # not need this verdict's noun beside it to be about this verdict.
    return _concerns_verdict(low, value) or denied


# Words that carry a verdict. A list entry (a country's own line in a list
# or table) must not contain one; the rule sentence does.
_RULE_WORDS = re.compile(r"visa|visado|visto|visum|víz|viz|виз|签证|簽證|査証|查証|ビザ|비자|thị thực|วีซ่า|تأشيرة|"
                         r"\b(?:eta|etas|esta|etias|k-eta|nzeta|evisitor)\b|exempt|arrival|免签|免簽|entry clearance|travel authori", re.I)
# A short line that states a verdict on its own ("Visa-free countries",
# "Visa required", "ETA nationals") is a section heading whatever follows.
_VERDICT_LINE_RE = re.compile('|'.join([positive for positive, _ in _VERDICT_RULES.values()]
                                       + [_VERDICT_RULES['VISA_REQUIRED'][1], _EAR_TOKEN,
                                          r"visa nationals|visa waiver|visa[- ]required|visa[- ]needed"]), re.I)
# A price or fee row: an amount of money and no word that states a rule.
# "USA 5-year multiple entry eTA $185" prices a product and carries no
# verdict for anyone.
_MONEY_RE = re.compile(r"[$€£¥₩₹₺₽₫₱฿]\s*\d|\d[\d,.]*\s*(?:[$€£¥₩₹元円원]|usd|eur|gbp|aud|cad|nzd|sgd|hkd|twd|jpy|cny|rmb|krw|inr|thb|idr|myr|php|"
                       r"vnd|kes|zar|aed|sar|try|rub|brl|mxn|chf|dollars?|euros?|pounds?|yen|won|baht|rupees?|ringgit|pesos?|rupiah|"
                       r"shillings?|dong|đồng|dirhams?|riyals?|lira|rands?|francs?)\b|"
                       r"\b(?:usd|eur|gbp|kes|aud|cad|nzd|sgd|hkd|twd|jpy|cny|krw|inr|thb|idr|myr|php|vnd|zar|aed|sar|rub|brl|mxn|chf)\s*\d")
_STATEMENT_VERB_RE = re.compile(
    r"\b(?:must|need|needs|needed|require|requires|required|requiring|mandatory|compulsory|obligatory|exempt|exempted|exemption|"
    r"eligible|entitled|may|can|shall|should|have to|has to|issued|granted|available|obtain|obtains|apply|applies|do not|does not|"
    r"don['’]t|doesn['’]t|without|necessary|waived|allowed|permitted|doivent|doit|peuvent|peut|deben|debe|pueden|puede|necesita|"
    r"necesitan|besoin|exent[oa]s?|dispens\w*|wajib|harus|phải|cần)\b|需要|必須|必须|免签|免簽|免除|需|须|須|필요|ต้อง")


def _fee_row(low):
    """The normalized sentence is a fee or price row, not a rule."""
    return bool(_MONEY_RE.search(low)) and not _STATEMENT_VERB_RE.search(low)
# A sentence that opens a list of nationalities: only such a sentence can be
# proved by a nationality's own list line. A universal statement ("all
# foreigners") opens no list and names no one.
_LIST_INTRO_RE = re.compile(
    r"\b(?:following countries|following states|listed below|listed above|eligible countries|countries/territories|"
    r"(?:set out|criteria|countries|list|shown|mentioned|specified|named|indicated|table) (?:below|above)|"
    r"the following|list of|lists? [a-z]\b|countries (?:that|which|who|whose|not|requiring|exempt|with|and regions|or regions|and territories)|"
    r"\d+ countries|these countries|nationalities|in the table|table below|schedule|countries whose (?:citizens|nationals)|"
    r"liste des pays|pays suivants|pays ci-après|ci-dessous|ci-après|ci-dessus|ressortissants des pays|"
    r"lista de (?:los )?pa[ií]ses|siguientes pa[ií]ses|pa[ií]ses siguientes|seguintes pa[ií]ses|pa[ií]ses cujos|"
    r"staatenliste|folgenden? (?:staaten|länder)|daftar|negara(?:-negara)? berikut|krajiny, ktorých|список|следующих|danh sách|các nước)\b|"
    r"以下|下列|上述|次の|下記|国持|\d+国|国・地域", re.I)
# A sentence that refers back to the list before it.
_LIST_ABOVE_RE = re.compile(r"\b(?:above|foregoing|aforementioned|ci-dessus|précédente?s?|anteriores?|acima|oben|di atas|выше)\b|上述|上記", re.I)
# Words that are prose, not part of a list entry, before or after the name.
_ENTRY_PROSE_RE = re.compile(
    r"\b(?:to|for|is|are|was|were|be|been|being|must|may|can|could|will|shall|should|would|need|needs|require|requires|"
    r"requiring|required|proof|mission|embassy|consulate|apply|applying|application|applications|contact|issued|issue|"
    r"issues|by|with|from|at|in|into|on|non|other|except|excluding|than|unless|your|you|we|our|please|if|when|who|"
    r"which|that|this|these|those|residing|travel|travelling|traveling|enter|entering|entry|stay|visit|visiting|"
    r"au|aux|pour|para|por|con|sin|avec|dans|für|mit|von|bei|nach)\b", re.I)
# A sentence that introduces its own subject cannot borrow the previous
# sentence's nationality.
_OWN_SUBJECT_RE = re.compile(r"\b(?:nationals|citizens|holders|residents|ressortissants|ciudadanos|nacionales|cidadãos|citoyens|"
                             r"staatsbürger|bürger|граждане) (?:of|de|du|des|d'|von|der)\b", re.I)
# A qualifier that turns the name into a dependency or another jurisdiction
# ("British Virgin Islands", "Îles mineures éloignées des Etats-Unis",
# "French Polynesia", "Chinese Taipei", "Hong Kong SAR, China").
_JURISDICTION_RE = re.compile(
    r"\b(?:islands?|isles?|territory|territories|overseas|polynesia|guiana|guyana|taipei|minor|outlying|virgin|samoa|"
    r"antarctic|ocean|province|part|dependenc(?:y|ies)|bermuda|caribbean|bno|bn ?\(?o\)?|national \(?overseas\)?|"
    r"îles?|iles?|territoire|outre-mer|polynésie|polynesie|guyane|vierges|mineures|éloignées|eloignees|antarctiques|australes|partie|"
    r"islas?|territorio|ultramar|polinesia|guayana|vírgenes|virgenes|menores|alejadas|parte|"
    r"ilhas?|território|ultramarinas|polinésia|virgens|distantes|"
    r"inseln?|gebiet|übersee|jungferninseln|острова|территори\w*|заморск\w*|полинези\w*|виргинск\w*)\b", re.I)
# A sibling jurisdiction beside the name ("Hong Kong SAR, China", "Taiwan,
# Province of China") makes the entry that jurisdiction's line, unless the
# entry says it includes the sibling ("China (including Hong Kong and Macau)").
_SIBLING_RE = re.compile(r"\b(?:hong ?kong|macao|macau|taiwan)\b|香港|澳門|澳门|台灣|台湾", re.I)
_INCLUSION_RE = re.compile(r"\b(?:includes?|included|including|incl|inclusive|y compris|einschließlich|incluyendo|incluid[oa]s?|"
                           r"incluindo|termasuk|bao gồm)\b|包括|含", re.I)
_TERMINATOR_RE = re.compile(r"[.!?。！？;\n|]")
# Sentence boundaries; "U.S.", " J.", "e.g.", "i.e.", "etc.", "No." and "cf."
# are abbreviations, not ends.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])(?<![A-Z]\.[A-Z]\.)(?<!\s[A-Z]\.)(?<![eE]\.[gG]\.)(?<![iI]\.[eE]\.)(?<!\betc\.)(?<!\b[Nn]o\.)(?<!\b[Cc]f\.)\s+|\n+")


# An ampersand is part of a name ("Trinidad & Tobago", "St Vincent &
# Grenadines"), never a list separator: a run that needs it to split is
# not a list the converter can read.
_ITEM_SPLIT_RE = re.compile(r'\s*(?:,|;|、|，|/|\||\s[-–—]\s|\s(?:and|und|et|y|e|dan|và|и)\s)\s*', re.I)


def _document_class_restriction(low, document_type):
    """The passport class the normalized text is scoped to when that class
    is not the route's, or None. A text that names the route's own class
    beside another ("diplomatic, official, or ordinary") is not scoped away
    from the route."""
    route_class = document_type or 'ordinary_passport'
    if route_class == 'ordinary_passport':
        own = _ORDINARY_CLASS_RE
    else:
        own = re.compile(_DOCUMENT_CLASSES.get(route_class, r'(?!x)x'), re.I)
    if own.search(low):
        return None
    for name, pattern in _DOCUMENT_CLASSES.items():
        if name != route_class and re.search(pattern, low, re.I):
            return _DOCUMENT_CLASS_LABELS[name]
    if route_class != 'ordinary_passport' and _ORDINARY_CLASS_RE.search(low):
        return _DOCUMENT_CLASS_LABELS['ordinary_passport']
    if _HOLDERS_OF_DOCUMENT_RE.search(low):
        return _DOCUMENT_CLASS_LABELS['travel_document']
    return None


def _entry_shaped(item, nat, document_type='ordinary_passport'):
    """The item is the nationality's name with at most a short qualifier: no
    prose verbs or prepositions before or after the name, no negation, no
    passport class the route is not."""
    low = _norm(item)
    if not low.strip(' ()*') or len(low.strip(' ()*')) > 60:
        return False
    spans = _mentions(item, nat)
    if not spans:
        return False
    start, end = spans[0]
    lead, trail = low[:start].strip(' ()*'), low[end:].strip(' ()*.')
    qualifier = lead + ' ' + trail
    return (len(lead) <= 30 and len(trail) <= 40
            and not _ENTRY_PROSE_RE.search(lead) and not _ENTRY_PROSE_RE.search(trail)
            and not _JURISDICTION_RE.search(qualifier)
            and _document_class_restriction(qualifier, document_type) is None
            and not (_SIBLING_RE.search(qualifier) and not _INCLUSION_RE.search(qualifier)))


def _list_line(quote, nat, document_type='ordinary_passport'):
    """A quote that is essentially the nationality's own line in a list: a
    short entry-shaped line, or a run of names separated by commas, dashes
    or CJK enumerators whose item is entry-shaped. A verdict word, a prose
    fragment that mentions the nationality, or a negated mention is not a
    list line."""
    raw = re.sub(r'^\s*(?:\d+[.)]|[-*•])\s*', '', str(quote or ''))
    if not _named(raw, nat) or _RULE_WORDS.search(_norm(raw)):
        return False
    if _entry_shaped(raw, nat, document_type):
        return True
    items = _ITEM_SPLIT_RE.split(raw)
    return (len(items) >= 3 and all(len(_norm(item)) <= 45 for item in items)
            and any(_entry_shaped(item, nat, document_type) for item in items))


def _page_index(text):
    """Normalized page text with newlines kept, plus a whitespace-free copy
    and the map from its offsets back to the kept text."""
    kept = unicodedata.normalize('NFKC', str(text or '')).casefold().replace('\r', '\n')
    kept = re.sub(r'[ \t\f\v]+', ' ', kept)
    kept = re.sub(r'\s*\n\s*', '\n', kept)
    stripped, back = [], []
    for i, ch in enumerate(kept):
        if not ch.isspace():
            stripped.append(ch); back.append(i)
    return kept, ''.join(stripped), back, _headings(kept)


def _entry_line(piece):
    """A line that is a list entry: short, no verdict word, no prose."""
    text = re.sub(r'^\s*(?:\d+[.)]|[-*•])\s*', '', piece).strip(' *()')
    return bool(0 < len(text) <= 60 and len(text.split()) <= 8 and re.search(r'[^\W\d_]', text)
                and not _RULE_WORDS.search(text) and not _ENTRY_PROSE_RE.search(text))


def _entry_run(piece):
    """A line that is a run of three or more comma-separated list entries:
    one line that holds a whole list ("Albania, Andorra, ..., Uruguay")."""
    items = [item for item in _ITEM_SPLIT_RE.split(piece) if item.strip()]
    return len(items) >= 3 and all(_entry_line(item) for item in items)


# A heading that names a schedule, annex, part, list or category, in the
# languages of the captured pages. Between two country runs such a line is
# a section boundary whatever verdict it leaves unsaid.
_HEADING_VOCAB_RE = re.compile(
    r"\b(?:annex|annexe|anexo|anhang|allegato|appendix|appendice|apéndice|apêndice|schedule|part|parte|partie|teil|list|liste|lista|"
    r"elenco|category|catégorie|categoría|categoria|kategorie|group|groupe|grupo|gruppo|gruppe|section|sección|seção|sezione|abschnitt|"
    r"table|tableau|tabla|tabela|tabella|tabelle|chapter|chapitre|capítulo|capitolo|kapitel|tier|class|classe|clase|klasse|column|zone|"
    r"first|second|third|fourth|premier|première|deuxième|troisième|primera|segunda|erste|zweite)\b|"
    r"附件|附录|附錄|附表|别表|別表|附属書|付表|第.{1,3}[表部類类组組]|부록|별표|제\s*\d+\s*[표부군]|phụ lục|bảng|nhóm|lampiran|daftar|kelompok|"
    r"приложение|список|таблица|группа|часть|категория|ภาคผนวก|ตาราง|กลุ่ม", re.I)
# A continent or region sub-heading inside one list is not a boundary.
_REGION_LINE_RE = re.compile(
    r"^\s*(?:[a-z\d]{1,3}[.)]\s*)?(?:europe|europa|asia|asie|africa|afrique|áfrica|america|américa|americas|amérique|the americas|"
    r"north america|south america|central america|latin america|oceania|océanie|caribbean|caraïbes|middle east|pacific|"
    r"欧洲|歐洲|亚洲|亞洲|非洲|美洲|大洋洲|ヨーロッパ|アジア|アフリカ|アメリカ|オセアニア|유럽|아시아|아프리카|아메리카|오세아니아|"
    r"châu âu|châu á|châu phi|châu mỹ|eropa|afrika|amerika|европа|азия|африка|америка)\s*[:：]?\s*$", re.I)
# Heading vocabulary that states a verdict, mapped to the verdict it states.
_HEADING_VERDICTS = (
    ('VISA_EXEMPT', r"visa[- ]?waiver|visa[- ]?free|visa[- ]?exempt|\bexempt|no visa|without (?:a )?visa|免签|免簽|査証免除|ビザ免除|무비자|사증면제|"
                    r"sans visa|sin visa(?:do)?|miễn thị thực|bebas visa|ยกเว้นวีซ่า|без виз|безвиз"),
    ('VISA_REQUIRED', r"visa[- ]?nationals?|visa[- ]?required|visa[- ]?needed|visa[- ]?countries|requir\w* (?:a |an )?visa|entry clearance|"
                      r"need(?:s|ing)? (?:a |an )?visa|签证国家|需要签证|需签证|비자 필요|visa obligatoire|visado obligatorio|visa requerida|виза требуется|требуется виза"),
    ('ELECTRONIC_AUTHORIZATION_REQUIRED', _EAR_TOKEN),
    ('VISA_ON_ARRIVAL', r"visa[- ]on[- ]arrival|落地签|落地簽|到着ビザ|도착비자|e-?voa"),
)


def _headings(kept):
    """Offsets of the sentences that open a list of nationalities under a
    verdict: a sentence with list-intro wording ("Countries whose citizens
    must have a visa:"), a short line that ends its own line and states a
    verdict ("Visa-free countries") whatever follows it, a short line with
    a verdict word that ends its own line and is followed by entry lines or
    by one line holding a comma-separated list, or a short line without a
    verdict word that opens such a run by shape: a heading-shaped line
    before one comma run, or a schedule, annex, part or list title before
    entry lines ("Annex II", "Schedule 2", "List B"). They cut the page into
    sections. A list line proves only the heading of its own section."""
    parts = re.split(r'([.!?。！？;\n|])', kept)
    pieces_all = parts[0::2]
    seps = parts[1::2] + ['']
    count = len(pieces_all)
    offsets, at = [], 0
    for piece, sep in zip(pieces_all, seps):
        offsets.append(at)
        at += len(piece) + len(sep)
    # A heading is decided by the shape of its line, not by the terminator
    # that follows it: a piece that starts its own line and ends it (with
    # the line break, or with a period, semicolon or pipe and nothing but
    # blanks before the break) stands alone in its block; a piece that
    # starts its line and is closed by a pipe is a table's first cell.
    starts_line = [False] * count
    for i in range(count):
        starts_line[i] = i == 0 or seps[i - 1] == '\n' or (not pieces_all[i - 1].strip() and starts_line[i - 1])
    ends_line = [False] * count
    for i in range(count - 1, -1, -1):
        ends_line[i] = seps[i] in ('\n', '') or (seps[i] in '.;|' and i + 1 < count
                                                and not pieces_all[i + 1].strip() and ends_line[i + 1])
    content = [j for j in range(count) if pieces_all[j].strip()]
    position = {j: k for k, j in enumerate(content)}
    starts, pieces = [], []
    for i, piece in enumerate(pieces_all):
        short = len(piece.strip()) <= 100 and starts_line[i] and (ends_line[i] or seps[i] == '|')
        following, following_ends = [], []
        if short:
            j = i + 1
            while j < count and len(following) < 2:
                if pieces_all[j].strip():
                    following.append(pieces_all[j]); following_ends.append(ends_line[j])
                elif seps[j] == '|' or (j and seps[j - 1] == '|'):
                    # A blank piece at a cell boundary: the next cell is not
                    # a line under this heading.
                    break
                j += 1
        before_run = short and len(following) >= 1 and following_ends[0] and _entry_run(following[0])
        before_lines = (short and len(following) == 2 and all(_entry_line(p) for p in following) and all(following_ends))
        text = piece.strip()
        # A line standing alone in its own block that is neither a list
        # entry, a comma run of entries nor a region sub-heading is a
        # section boundary whatever it says: a title, a rule sentence or a
        # note between two lists ends the reach of the list before it.
        standalone = (short and ends_line[i] and 0 < len(text) and bool(re.search(r'[^\W\d_]', text))
                      and not _entry_line(piece) and not _entry_run(piece) and not _REGION_LINE_RE.match(text))
        if _RULE_WORDS.search(piece):
            opens = (bool(_LIST_INTRO_RE.search(piece)) or (short and bool(_VERDICT_LINE_RE.search(piece)))
                     or before_run or before_lines or standalone)
        else:
            titled = (0 < len(text) <= 60 and len(text.split()) <= 8 and re.search(r'[^\W\d_]', text)
                      and not _REGION_LINE_RE.match(text))
            opens = standalone or (titled and (before_run or ((before_lines or before_run)
                                                              and bool(_HEADING_VOCAB_RE.search(text) or _LIST_INTRO_RE.search(text)))))
        if opens:
            starts.append(offsets[i])
            pieces.append(piece)
    return starts, pieces


def _states_other_verdict(text, value):
    """The text states a verdict other than this one (its own positive
    wording without its flip, or heading vocabulary that names another
    verdict: "Visa waiver countries", "Visa nationals", "ETA countries"),
    or flips this one."""
    low = _norm(text)
    for key, (positive, negative) in _VERDICT_RULES.items():
        if key == value:
            if negative and re.search(negative, low, re.I):
                return True
        elif re.search(positive, low, re.I) and not (negative and re.search(negative, low, re.I)):
            return True
    for key, vocabulary in _HEADING_VERDICTS:
        negative = _VERDICT_RULES[key][1]
        if key != value and re.search(vocabulary, low, re.I) and not (negative and re.search(negative, low, re.I)):
            return True
    return False


def _boundary(piece, value):
    """A heading between a rule sentence and a list line ends the rule
    sentence's reach unless it restates this verdict in rule words ("Visa
    required (Asia)" under a visa-required rule). A heading that states
    another verdict or flips this one, a heading in list vocabulary only
    ("Visa nationals", "Visa waiver countries", "ETA countries"), and a
    boundary by shape alone ("Annex II", "Schedule 2") all cut the list."""
    low = _norm(piece)
    positive, negative = _VERDICT_RULES[value]
    restates = bool(re.search(positive, low, re.I)) and not (negative and re.search(negative, low, re.I))
    if not restates and _dated_start_heading(low):
        return False
    return not restates or _states_other_verdict(piece, value)


def _dated_start_heading(low):
    """A sub-heading that only dates the start of the rule above it ("(c)
    for travel to the UK on or after 8 January 2025:") continues that
    rule's list when its date has passed; a future date, any other date
    relation, or any verdict word of its own makes it a boundary."""
    if _RULE_WORDS.search(low) or any(re.search(p, low, re.I) for p, _ in _VERDICT_RULES.values()):
        return False
    dates = _dates_in(low)
    if len(dates) != 1:
        return False
    start, end, day = dates[0]
    before = low[max(0, start - 40):start]
    return bool(_START_BEFORE_RE.search(before)) and not _END_BEFORE_RE.search(before) and day <= _today()


def _section(headings, pos):
    """Index of the section (between two headings) that holds the offset."""
    from bisect import bisect_right
    return None if pos is None else bisect_right(headings, pos)


def _in_navigation(kept, start, end):
    """The span sits in site navigation boilerplate: among the three cells
    (lines or table cells) on each side, the same digit-free phrase that
    states nothing recurs on both sides ("информация о стране" around
    every country of a site index). A verdict or stay cell that repeats
    down a table column carries a rule word, a verb or a figure and is
    not boilerplate."""
    before = [c.strip() for c in re.split(r'[\n|]', kept[max(0, start - 600):start])][:-1][-3:]
    after = [c.strip() for c in re.split(r'[\n|]', kept[end:end + 600])][1:][:3]

    def phrase(cell):
        return (len(cell) >= 8 and not re.search(r'\d', cell) and not _RULE_WORDS.search(cell)
                and not _STATEMENT_VERB_RE.search(cell))
    return bool({c for c in before if phrase(c)} & {c for c in after if phrase(c)})


def _passages(evidence_quotes, pages, nat=None):
    """Group the quotes into passages. Without page texts each quote is its
    own passage. With them, quotes are located on their captured page and two
    quotes that the page shows inside one sentence or table row (no sentence
    terminator or line break between them, at most 400 characters apart), or
    as consecutive sentences (nothing but the terminator between them), are
    read as one passage in page order, since the reviewer only split what the
    page says in one breath. Each passage carries its page id and offset."""
    if pages is None:
        return [{'text': q, 'page': None, 'pos': None, 'section': None, 'ambiguous': False, 'boilerplate': False,
                 'parts': [{'text': q, 'page': None, 'pos': None, 'section': None, 'ambiguous': False, 'boilerplate': False}]}
                for q in evidence_quotes], {}
    located, indexes = [], {}
    for order, (quote, (page_id, page_text)) in enumerate(zip(evidence_quotes, pages, strict=True)):
        if page_id not in indexes:
            indexes[page_id] = _page_index(page_text)
        kept, stripped, back, headings = indexes[page_id]
        needle = re.sub(r'\s+', '', _norm(quote))
        hits = [m.start() for m in re.finditer(re.escape(needle), stripped)][:200] if needle else []
        located.append({'text': quote, 'page': page_id, 'order': order, 'ambiguous': False, 'boilerplate': False,
                        'hits': [(back[at], back[at + len(needle) - 1] + 1) for at in hits]})
    # A repeated cell ("No visa required.") is read at the occurrence that
    # continues the line or sentence of a quote occurring once on that page
    # (the country's own line), else at the nearest occurrence to one. A
    # quote that names the nationality without a verdict word (its own
    # country line) and occurs under two different verdict headings is
    # ambiguous: the page states two rules for it and the reviewer's other
    # quote must not pick one.
    def rank(hit, anchors, kept):
        best = None
        for start, end in anchors:
            if hit[0] >= end and hit[0] - end <= 400 and not _TERMINATOR_RE.search(kept[end:hit[0]]):
                score = (0, hit[0] - end)
            else:
                score = (1, min(abs(hit[0] - start), abs(hit[0] - end)))
            best = score if best is None or score < best else best
        return best
    for unit in located:
        anchors = [other['hits'][0] for other in located
                   if other is not unit and other['page'] == unit['page'] and len(other['hits']) == 1]
        hits = unit['hits']
        headings = indexes[unit['page']][3][0]
        if not hits:
            unit['pos'] = unit['end'] = None
        elif (len(hits) > 1 and not _RULE_WORDS.search(_norm(unit['text']))
              and (nat is None or _named(unit['text'], nat))
              and len({_section(headings, h[0]) for h in hits}) > 1):
            unit['pos'] = unit['end'] = None
            unit['ambiguous'] = True
        elif len(hits) == 1 or not anchors:
            unit['pos'], unit['end'] = hits[0]
        else:
            unit['pos'], unit['end'] = min(hits, key=lambda h: rank(h, anchors, indexes[unit['page']][0]))
        # A country name inside a site navigation index (the same sibling
        # phrase repeated on both sides of it) is no list entry.
        if (unit['pos'] is not None and nat is not None and not _RULE_WORDS.search(_norm(unit['text']))
                and _named(unit['text'], nat) and _in_navigation(indexes[unit['page']][0], unit['pos'], unit['end'])):
            unit['boilerplate'] = True
            unit['pos'] = unit['end'] = None
    for unit in located:
        del unit['hits']
        unit['section'] = _section(indexes[unit['page']][3][0], unit['pos'])
        unit['parts'] = [{'text': unit['text'], 'page': unit['page'], 'pos': unit['pos'],
                          'section': unit['section'], 'ambiguous': unit['ambiguous'], 'boilerplate': unit['boilerplate']}]
    located.sort(key=lambda u: (str(u['page']), u['pos'] if u['pos'] is not None else 10 ** 9, u['order']))
    merged = []
    for unit in located:
        last = merged[-1] if merged else None
        gap = (indexes[unit['page']][0][last['end']:unit['pos']]
               if last and last['page'] == unit['page'] and last['pos'] is not None
               and unit['pos'] is not None and unit['pos'] >= last['end'] else None)
        if gap is not None and ((len(gap) <= 400 and not _TERMINATOR_RE.search(gap))
                                or (len(gap) <= 20 and not gap.strip(' .!?。！？;:|\n'))):
            # Across a line break the parts stay separate sentences, so a
            # table cell can borrow the country line before it, never after.
            joiner = '\n' if '\n' in gap else ' '
            last['text'] = last['text'].rstrip() + joiner + unit['text'].lstrip()
            last['end'] = unit['end']
            last['parts'].extend(unit['parts'])
            continue
        merged.append(dict(unit))
    return merged, indexes


def _sentence_span(index, sentence, unit):
    """Page span of this sentence of the unit: the occurrence inside the
    unit's own span when the sentence sits there, else the first occurrence
    on the page; None when the sentence is not on the page."""
    kept, stripped, back, _ = index
    needle = re.sub(r'\s+', '', _norm(sentence))
    if not needle:
        return None
    hits = [(back[m.start()], back[m.end() - 1] + 1) for m in re.finditer(re.escape(needle), stripped)]
    if not hits:
        return None
    inside = [h for h in hits if unit.get('pos') is not None and unit['pos'] <= h[0] < unit.get('end', unit['pos'])]
    return (inside or hits)[0]


def _same_list(listed, page, span, backwards, index, value):
    """The list line that proves the sentence opening its list, or None:
    the line sits on the same captured page after the sentence (before it
    when the sentence refers back to the list above), and no heading
    between them states another verdict or is a boundary by shape. The
    reach of a heading ends where the next heading begins, not at a fixed
    distance."""
    if page is None:
        return listed[0] if listed else None
    if span is None:
        return None
    starts, pieces = index[3]
    for entry in listed:
        if entry['page'] != page or entry['pos'] is None:
            continue
        if backwards:
            lo, hi = entry['pos'], span[0]
        else:
            lo, hi = span[1], entry['pos']
        if lo > hi:
            continue
        between = [piece for start, piece in zip(starts, pieces) if lo <= start < hi]
        if not any(_boundary(piece, value) for piece in between):
            return entry
    return None


# Named groups an official page may use instead of the country, with the
# membership checked against the group's own published list (europa.eu EU
# member countries; asean.org member states, Timor-Leste admitted 2025).
_GROUPS = {
    'EU': (r"(?<![a-z])(?:eu|e\.u\.)[- ](?:bürger|citizens?|nationals?|member states?|countries|passports?|passport holders?)(?![a-z])|"
           r"european union|union européenne|unión europea|união europeia|"
           r"europäischen? union|unione europea|европейского союза|uni eropa|liên minh châu âu",
           {'AUT', 'BEL', 'BGR', 'HRV', 'CYP', 'CZE', 'DNK', 'EST', 'FIN', 'FRA', 'DEU', 'GRC', 'HUN', 'IRL', 'ITA', 'LVA', 'LTU',
            'LUX', 'MLT', 'NLD', 'POL', 'PRT', 'ROU', 'SVK', 'SVN', 'ESP', 'SWE'}),
    'ASEAN': (r"\basean\b|东盟|東盟|東南アジア諸国連合|아세안",
              {'BRN', 'KHM', 'IDN', 'LAO', 'MYS', 'MMR', 'PHL', 'SGP', 'THA', 'VNM', 'TLS'}),
}


def _check_group_names():
    """Every member of every group must be named by the converter's own
    compiled pattern: a carve-out ("except Myanmar") is read through those
    names, and a member whose pattern never matches could never be carved
    out. An explicit raise, so the check also runs under python -O, and the
    compiled pattern is tested rather than the table entry, so an alias
    tuple holding only an empty string fails too."""
    for _, members in _GROUPS.values():
        for member in sorted(members):
            if _build_name_pattern(member).pattern == r'(?!x)x':
                raise PatchRejected('group member without a name pattern: ' + member)


def _build_name_pattern(nat):
    """The compiled name pattern for the nationality, uncached."""
    aliases = sorted(_aliases(nat), key=len, reverse=True)
    guard = _compound_guard(aliases)
    parts = [guard + r'(?<![a-z])' + re.escape(a) + r'(?![a-z])' for a in aliases]
    parts += [guard + r'(?<![^\W\d_])' + re.escape(_norm(stem)) + r'[^\W\d_]{0,5}(?![^\W\d_])'
              for stem in _DEMONYM_STEMS.get(nat, ())]
    return re.compile('|'.join(parts) if parts else r'(?!x)x')


_check_group_names()

# An exception clause, in the languages of the captured pages: the
# construction that opens it and the item it carves out. The openers are
# read as a construction (any "exception of", "exclusion of", "but not",
# "unless", "save ...", "bar" shape), not as a closed list, so a wording
# outside the list still opens a clause whose item must resolve or the
# sentence proves nothing. Preposed forms capture the item after the word
# ("except Myanmar", "à l'exception des Bulgares", "除了缅甸之外"),
# postposed CJK forms capture it before ("缅甸除外", "ミャンマーを除く",
# "미얀마는 제외"), and ", less X" needs its comma. Thai ยกเว้น is also the
# exemption noun before วีซ่า.
_EXCEPTION_RE = re.compile(
    r"(?:(?<![^\W\d_])(?:except(?:ing)?|excluding|exclusive of|other than|save for|save in the case of|save that|save where|saving|"
    r"apart from|aside from|with the (?:\w+ ){0,2}exceptions? (?:of|for|being)|with the (?:\w+ ){0,2}exclusion of|to the exclusion of|"
    r"exceptions? (?:being|for|of|:)|exceptions?(?= ?:)|but not|not including|not counting|barring|bar(?! ?codes?)|unless|"
    r"sauf|[àa] l['’]exception d(?:e|es|u)|[àa] l['’]exclusion d(?:e|es|u)|hormis|excepté|exceptés|exception faite d(?:e|es|u)|en dehors d(?:e|es|u)|mis [àa] part|[àa] moins que|"
    r"excepto|salvo|con excepci[oó]n de|a excepci[oó]n de|exceptuando|excluyendo|con exclusi[oó]n de|a menos que|salvo que|"
    r"exceto|com exce[cç][aã]o d(?:e|os|as|o|a)|[àa] exce[cç][aã]o d(?:e|os|as|o|a)|excluindo|salvo se|"
    r"tranne|ad eccezione d(?:i|ei|elle|egli|el|ella)|eccetto|escluso|esclusi|esclusa|escluse|fatta eccezione per|a meno che|"
    r"außer|ausser|mit ausnahme (?:von|der|des|dem)|ausgenommen|abgesehen von|bis auf|unter ausschluss (?:von|der)|es sei denn|"
    r"kecuali|selain|terkecuali|tidak termasuk|di luar|ngoại trừ|(?<!miễn )trừ|trừ khi|loại trừ|không bao gồm|osim|kromě|okrem|za isključenjem|"
    r"за исключением|кроме|помимо|исключая|не считая|если не)(?![^\W\d_])|"
    r"ยกเว้น(?!วีซ่า|การตรวจลงตรา)|เว้นแต่|นอกจาก|ไม่รวม)"
    r"\s*(?:for|of|de|des|du|del|dei|degli|delle|den|der|die|das|pour|para|bagi|untuk|les|los|las|the)?\s*([^.;:\n]{0,80}[^\s.;:\n,，、()（）]*)"
    r"|(?<![免解削排控驱驅])除了?\s*([^.;:\n。；，、,()（）]{1,40}?)\s*(?:之外|以外|外(?![国國交人籍币幣汇匯出]))"
    r"|([^\s.;:\n。；，、,()（）]{1,40}?)\s*(?:(?:는|은|이|가|을|를|도|만)\s*)?(?:除外|は除く|を除く|を除き|を除いて|以外|제외|이외)"
    r"|(?<=,)\s*less(?!\s+than)\s*(?:the\s+)?([^.;:\n]{0,80})", re.I)
# An exception-only sentence that follows a rule sentence binds to it
# ("아세안 국민은 무비자입니다. 미얀마는 제외합니다.").
_EXCEPTION_LEAD_RE = re.compile(
    r"^\s*(?:except|excluding|other than|save for|save in the case of|apart from|aside from|with the (?:\w+ ){0,2}exceptions?|exceptions?\b|barring|"
    r"but not|not including|unless|sauf|à l['’]exception|à l['’]exclusion|hormis|excepté|exception faite|"
    r"excepto|salvo|con excepci[oó]n|a excepci[oó]n|exceptuando|excluyendo|exceto|com exce[cç][aã]o|excluindo|tranne|ad eccezione|eccetto|escluso|außer|ausser|"
    r"mit ausnahme|ausgenommen|abgesehen von|kecuali|selain|terkecuali|tidak termasuk|ngoại trừ|trừ|loại trừ|osim|kromě|okrem|за исключением|кроме|"
    r"ยกเว้น(?!วีซ่า)|เว้นแต่|除了|但|ただし|단,)", re.I)
_EXCEPTION_TAIL_RE = re.compile(r"(?:除外|を除く|を除き|を除いて|제외)(?:합니다|됩니다|된다|함|입니다|됨|です|とする|する)?\s*[.。]?\s*$")
_ITEM_WORDS = re.compile(r"\b(?:nationals?|citizens?|holders?|passport holders?|passports?|those|persons?|people|from|of|the|"
                         r"ressortissants?|citoyens|titulaires|ciudadanos|nacionales|titulares|cidadãos|portadores|cittadini|titolari|"
                         r"staatsangehörige|staatsbürger|bürger|inhaber|warganegara|warga negara|negara|công dân|người)\b|"
                         r"国民|公民|人员|人員|国籍|旅券|护照|護照|여권|국민|시민|공민")


# Where the sentence's own predicate resumes after an exception clause:
# "except those holding diplomatic passports, are eligible for the
# programme" excepts the passports, not the eligibility.
_CLAUSE_RESUMES_RE = re.compile(
    r"^\s*(?:and\s+|but\s+|they\s+|he\s+|she\s+|it\s+|who\s+|which\s+|that\s+)?"
    r"(?:are|is|was|were|do|does|did|shall|will|would|may|can|must|should|have|has|had|remain|remains|need|needs|"
    r"require|requires|receive|receives|enjoy|enjoys|sont|est|ont|doivent|son|es|están|deben|sind|ist|müssen)\b", re.I)


def _clause_text(clause):
    """The captured text of one exception clause, cut where the sentence's own
    predicate resumes. A run of names ("except for Brunei, Singapore and
    Malaysia") crosses its commas untouched, because no comma of it opens a
    predicate; a clause in the middle of a sentence stops at the comma that
    closes it, so the rest of the rule is not read as an excepted item."""
    text = next((g for g in clause.groups() if g is not None), '')
    depth = 0
    for i, ch in enumerate(text):
        if ch in '(（':
            depth += 1
        elif ch in ')）':
            if depth == 0:
                # The clause was opened inside a parenthesis ("(not including
                # Iceland) within 6 months ...") and closes with it.
                text = text[:i]
                break
            depth -= 1
    for mark in re.finditer(r'[,，;；]', text):
        if _CLAUSE_RESUMES_RE.match(text[mark.end():]):
            return text[:mark.start()]
    return text


_ITEM_SEPARATOR_RE = re.compile(r"\s*(?:,|;|/|、|，|\s(?:and|or|und|et|y|e|dan|và|hoặc|atau|и|или)\s)\s*", re.I)


def _names_any_nationality(text):
    """The text names some nationality the converter knows."""
    return any(_named(text, nat) for nat in _known_nationalities())


def _item_fragment(piece, bare):
    """The item is a fragment of the phrase before it and no subject of its
    own. An enumeration cuts a modifier away from its head noun ("diplomatic
    or official/service passports" leaves "official" and "service passports"
    hanging off "those holding diplomatic"), and read alone such a fragment
    names neither a traveller nor a country, so it says nothing."""
    return not (_ITEM_TRAVELLER_RE.search(_norm(piece)) or _resolves_to_nationality(bare)
                or _names_any_nationality(piece))


def _clause_parts(text):
    """The items of one exception clause's captured text, each as the piece the
    clause wrote, the bare name left once the traveller words are taken out of
    it, and the phrase the piece heads.

    The written piece is what a document class is read from, because the words
    that name the class ("passports", "travel documents") are among the ones
    the bare item drops. The phrase is the piece with the fragments the
    enumeration cut off its head noun put back, exactly as the clause wrote
    them, so "those holding diplomatic or official/service passports" reads as
    one document class and not as three subjects. Resolving a nationality
    stays the piece's own business: a phrase would let a name the converter
    reads carry a neighbour it cannot.
    """
    text = str(text or '')
    spans, at = [], 0
    for separator in _ITEM_SEPARATOR_RE.finditer(text):
        spans.append((at, separator.start()))
        at = separator.end()
    spans.append((at, len(text)))
    # Capitals are kept: "USA" is the country, "usa" a Spanish verb.
    items = [(start, end, ' '.join(unicodedata.normalize('NFKC', text[start:end]).split())) for start, end in spans]
    items = [(start, end, piece, _ITEM_WORDS.sub(' ', piece).strip(' ()（）')) for start, end, piece in items]
    for i, (start, end, piece, bare) in enumerate(items):
        stop = end
        for other_start, other_end, other_piece, other_bare in items[i + 1:]:
            if not _item_fragment(other_piece, other_bare):
                break
            stop = other_end
        yield piece, bare, ' '.join(unicodedata.normalize('NFKC', text[start:stop]).split())


def _clause_items(text):
    """The bare items of one exception clause's captured text."""
    for _, bare, _phrase in _clause_parts(text):
        yield bare


def _exception_items(sentence, following=None):
    """The bare items of every exception clause in the sentence, plus those
    of the adjacent following sentence when that sentence is nothing but an
    exception clause."""
    for clause in _EXCEPTION_RE.finditer(str(sentence or '')):
        yield from _clause_items(_clause_text(clause))
    low = _norm(following)
    if low and (_EXCEPTION_LEAD_RE.match(low) or _EXCEPTION_TAIL_RE.search(low)):
        yield from _exception_items(following)


def _resolves_to_nationality(bare):
    """The bare exception item is a nationality the converter can name and
    little else: once every known name is removed, at most three short
    words remain, none of them prose, a figure or a verdict word."""
    if not bare:
        return False
    if bare.islower() and re.fullmatch(r'[a-z.]{2,5}', bare):
        # A captured page is read casefolded, so "USA" and "FSM" arrive as
        # "usa" and "fsm"; as a whole exception item a short abbreviation is
        # the country, never the Spanish verb.
        bare = bare.upper()
    rest, matched = bare, False
    for nat in _known_nationalities():
        spans = _mentions(bare, nat)
        if spans:
            matched = True
            for start, end in sorted(spans, reverse=True):
                rest = rest[:start] + ' ' + rest[end:]
    if not matched:
        return False
    leftover = ' '.join(rest.split()).strip(' ()（）.,')
    return (not leftover or (len(leftover.split()) <= 3 and len(leftover) <= 24 and not re.search(r'\d', leftover)
                             and not _ENTRY_PROSE_RE.search(leftover) and not _RULE_WORDS.search(leftover)))


def _carved_out(sentence, nat, following=None):
    """The sentence (or the exception-only sentence after it) excludes this
    nationality by name in an exception item: "except Myanmar", "except for
    Brunei and Singapore nationals", "except Bulgarian and Romanian
    nationals holding non-biometric passports"."""
    return any(bare and _named(bare, nat) for bare in _exception_items(sentence, following))


def _group_named(sentence, nat):
    """The sentence names a group the nationality belongs to, and not only
    behind a negating prefix ("Non-EU citizens", "hors Union européenne")."""
    low = _norm(sentence)
    for pattern, members in _GROUPS.values():
        if nat not in members:
            continue
        for m in re.finditer(pattern, low, re.I):
            if not _NEGATED_PREFIX_RE.search(low[max(0, m.start() - 24):m.start()]):
                return True
    return False


# An exception item that speaks of travellers the converter cannot name
# ("those listed in the annex", "persons from the countries in Annex III")
# is never dismissed: it may be naming this row's traveller in words the
# converter cannot read. An item that names no traveller at all ("excluding
# the day of submission") excepts nobody.
_ITEM_TRAVELLER_RE = re.compile(
    r"\b(?:nationals?|citizens?|holders?|bearers?|persons?|people|those|anyone|everyone|travell?ers?|visitors?|passengers?|"
    r"applicants?|tourists?|residents?|foreigners?|aliens?|subjects?|countr(?:y|ies)|nationalit(?:y|ies)|states?|territories|"
    r"crews?|seafarers?|seam[ae]n|sailors?|staff|personnel|employees?|workers?|students?|spouses?|child(?:ren)?|famil(?:y|ies)|"
    r"dependants?|dependents?|members?|individuals?|he|she|they|them|their|his|her|who|whoever|"
    r"awak|pelaut|penumpang|orang|thuyền viên|"
    r"ressortissants?|citoyens?|titulaires?|ciudadanos?|nacionales|titulares|cidadãos?|portadores|cittadin[oi]|pa[ií]ses|pays|"
    r"staatsangehörige\w*|staatsb[üu]rger\w*|b[üu]rger\w*|inhaber|l[äa]nder|warga ?negara|warganegara|negara|c[ôo]ng d[âa]n|ng[ưu][ờo]i|"
    r"qu[ốo]c gia|граждан\w*|стран\w*|พลเมือง|ผู้ถือ|ประเทศ)\b|"
    r"国民|公民|人员|人員|国籍|国家|國家|여권|국민|시민|공민|국가|旅券|护照|護照|船员|船員|乗客|乘客|승무원|선원|승객", re.I)
# What an exception item is when it excepts no traveller: a day, a date, a
# period, a fee or an office matter ("excluding the day of submission",
# "except weekends and public holidays", "excluding VAT"). The item has to be
# READ as one of these; an item that is none of them and names no traveller
# either ("unless any of the following applies", "other than where VN 2.3
# applies", "unless otherwise exempt") is a condition on travellers the
# converter cannot read, and refuses.
_ITEM_NON_TRAVELLER_RE = re.compile(
    r"\b(?:days?|dates?|periods?|hours?|minutes?|weeks?|weekends?|months?|years?|holidays?|time|times|working days|business days|"
    r"public holidays|seasons?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"fees?|charges?|costs?|amounts?|tax|taxes|vat|duty|duties|prices?|surcharges?|deposits?|payments?|"
    r"jours?|d[ée]lais?|frais|taxes?|d[ií]as?|fechas?|plazos?|tasas?|dias?|prazos?|taxas?|tage?|datum|geb[üu]hren?|"
    r"giorn[oi]|data|tass[ae]|hari|tanggal|biaya|ngày|phí|lệ phí|thời hạn|дн[яеи]й?|дней|дня|дата|даты|срок\w*|сбор\w*|"
    r"วัน|ค่าธรรมเนียม)\b|"
    r"日以内|日間|営業日|平日|休日|祝日|天内|工作日|节假日|節假日|费用|費用|手数料|料金|일 이내|영업일|공휴일|수수료|요금", re.I)
# A day or a fee that belongs to a document, an entry or a stay ("the
# passport has at least 6 months remaining", "stays exceeding 90 days") is a
# condition on the traveller, not a calendar matter.
_ITEM_CONDITION_RE = re.compile(
    r"\b(?:passports?|permits?|documents?|entry|entries|stays?|arrivals?|purposes?|validity|valid|remaining|exceed\w*)\b|"
    r"paspor|passeport|pasaporte|hộ chiếu|여권|护照|護照|旅券", re.I)


def _exception_elsewhere(piece, bare, phrase, nat, document_type):
    """This one exception item is resolved to a subject that is not this
    row's: it is scoped to a document class the route is not ("except those
    holding diplomatic or official/service passports"), or it is read as a
    day, a period, a fee or an office matter and so excepts no traveller at
    all ("excluding the day of submission"). An item that speaks of
    travellers without naming them keeps refusing, and so does an item the
    converter can read as nothing in particular ("unless any of the following
    applies"), and so does every item when there is no nationality to compare
    against, because nothing about it is resolved."""
    if nat is None:
        return False
    low = _norm(piece)
    if not low:
        return False
    if document_type and _document_class_restriction(_norm(phrase), document_type):
        return True
    if _named(piece, nat) or _group_named(piece, nat) or _GENERAL_TRAVELLER_RE.search(low):
        return False
    if _ITEM_TRAVELLER_RE.search(low) or _ITEM_TRAVELLER_RE.search(_norm(bare)):
        return False
    return bool(_ITEM_NON_TRAVELLER_RE.search(low)) and not _ITEM_CONDITION_RE.search(low) and not _RULE_WORDS.search(low)


def _unreadable_exception(sentence, following=None, *, nat=None, document_type=None):
    """The first exception item the converter cannot resolve to a nationality,
    or None. A sentence with such an item may except this nationality in
    words the converter cannot read, so it proves nothing for anyone.

    Every ITEM is resolved on its own, never the clause as a whole. A clause
    holds as many subjects as it holds items, so "except Myanmar nationals
    and persons from the countries listed in Annex III" answers for Myanmar
    and leaves the annex unanswered. Given `nat` and `document_type` an item
    that names another nationality, or a document class the route is not, or
    no traveller at all, is not this row's to answer for, and every other
    unreadable item refuses.
    """
    for clause in _EXCEPTION_RE.finditer(str(sentence or '')):
        for piece, bare, phrase in _clause_parts(_clause_text(clause)):
            if _resolves_to_nationality(bare) or _exception_elsewhere(piece, bare, phrase, nat, document_type):
                continue
            return bare or '(empty)'
    low = _norm(following)
    if low and (_EXCEPTION_LEAD_RE.match(low) or _EXCEPTION_TAIL_RE.search(low)):
        return _unreadable_exception(following, nat=nat, document_type=document_type)
    return None


def _exception_opens_list(sentence, positive, negative):
    """The exception clause is itself the rule for the list it opens
    ("EXCEPT for the following countries which do not require a visa for
    any purpose of entry:"): its item introduces a list and states this
    verdict without flipping it. Such a sentence proves nothing by name or
    group, but a list line under it on the same page proves the verdict
    stated inside the clause."""
    for clause in _EXCEPTION_RE.finditer(str(sentence or '')):
        item = _norm(next((g for g in clause.groups() if g is not None), ''))
        if (_LIST_INTRO_RE.search(item) and re.search(positive, item, re.I)
                and not (negative and re.search(negative, item, re.I))):
            return True
    return False


def _group_member(sentence, nat, following=None, document_type=None):
    """The sentence names a group the nationality belongs to, every carve-out
    in it resolves to a nationality by name or to another document class, and
    none of them is this one."""
    return (_group_named(sentence, nat)
            and _unreadable_exception(sentence, following, nat=nat, document_type=document_type) is None
            and not _carved_out(sentence, nat, following))


def _label(quote, nat):
    """A quote that opens with the nationality as a label ("Canada: ...",
    "Hong Kong SAR passport . ...") applies that label to every sentence;
    returns the quote without its label, or None."""
    quote = str(quote or '').strip()
    parts = re.split(r'(?<=[.:：])(?<![A-Z]\.[A-Z]\.)(?<!\s[A-Z]\.)\s+|\n+', quote, maxsplit=1)
    head = parts[0]
    text = _norm(head).rstrip('.:： ')
    if len(parts) < 2 or len(text) > 80 or not _named_as_traveller(head, nat) or _RULE_WORDS.search(text):
        return None
    if head.rstrip().endswith((':', '：')) or (len(text.split()) >= 2 and (head.rstrip().endswith('.') or '\n' in quote)):
        return parts[1]
    return None


def _footnote_body(page, mark):
    """The body of the footnote this mark defines on the captured page, or
    None when the page defines none. Only the defining line is the body: a
    line under it may be the next footnote or anything else, and reading more
    than the page says would dismiss a mark that is this row's."""
    if not page or not mark:
        return None
    lines = str(page).split('\n')
    for i, line in enumerate(lines):
        found = _FOOTNOTE_LINE_RE.match(line)
        if found and found.group(1) == mark and _defines_mark('\n'.join(lines[:i]), mark, found.group(2)):
            return found.group(2)
    return None


def _closing_mark_elsewhere(text, nat, page=None):
    """The mark that closes the text belongs to another entry of the same
    cell, so it says nothing about this one.

    Position alone decides nothing: a cell that holds several entries
    ("Japan and Thailand (1)") wears one mark for all of them until the page
    says otherwise. The footnote the mark points to is what says otherwise,
    and only when it resolves to a neighbour: the body names another entry of
    the cell and names neither this nationality, a group it belongs to, nor
    every traveller. With no body on the page to read, the mark marks every
    entry of the cell, this one included.
    """
    spans = _mentions(text, nat)
    if not spans:
        return False
    low = _norm(text)
    closing = _FOOTNOTE_END_RE.search(low)
    if not closing:
        return False
    others = [other for other in _known_nationalities() if other != nat and _named(low, other)]
    if not others:
        return False
    body = _footnote_body(page, closing.group(1))
    if body is None:
        return False
    if _named(body, nat) or _group_named(body, nat) or _GENERAL_TRAVELLER_RE.search(_norm(body)):
        return False
    return any(_named(body, other) for other in others)


def _footnote_marked(text, nat, page=None):
    """A footnote mark sits on the nationality's own name ("United States of
    America*", "Japan (1)") or closes the cell ("No necesita Visa (4)"). The
    page says more about this entry than the cell states. A mark that the
    captured page's own footnote resolves to another entry of the same cell
    ("Japan, Korea (1)" under "(1) Korean nationals ...") is that entry's."""
    low = ' '.join(unicodedata.normalize('NFC', str(text or '')).casefold().split())
    if _FOOTNOTE_END_RE.search(_norm(text)) and not _closing_mark_elsewhere(text, nat, page):
        return True
    # The mark may follow a continuation of the name that is not itself an
    # alias ("United States of America*", "République de Corée*").
    tail = re.compile(r"(?:\s+(?:of|de|da|des|du|d['’])\s*[^\s,;.*()\[\]]+)*")
    return any(_FOOTNOTE_AFTER_RE.match(low[tail.match(low, m.end()).end():][:6]) for m in _name_pattern(nat).finditer(low))


# The example marker alone, and the run of items it opens: names separated
# by commas, slashes or a conjunction, with no terminator or closing
# parenthesis between the marker and the mention. "(e.g. UK citizens,
# Americans, Australians, etc.)" names every one of them as an example, not
# only the first.
_EXAMPLE_MARK_RE = re.compile(r"(?:\bfor example|\be\.g\.?|\bsuch as|\bpar exemple|\bpor ejemplo|\bzum beispiel|例如|例えば|예를 들어)", re.I)
_EXAMPLE_RUN_RE = re.compile(r"\s*[,:]?\s*(?:[^.;:!?()\n,/、，]{0,60}(?:,|/|、|，|\s(?:and|or|und|et|y|e|o|ou|oder)\s)\s*)*(?:[^\W\d_][^\s.;:!?()\n,/、，]*\s+){0,3}", re.I)


def _in_example_run(low, start):
    marks = list(_EXAMPLE_MARK_RE.finditer(low, 0, start))
    return bool(marks) and bool(_EXAMPLE_RUN_RE.fullmatch(low[marks[-1].end():start]))


def _example_only(sentence, nat):
    """Every mention of the nationality follows an example marker ("for
    example Indian nationals resident in Germany"), as the first item after
    it or as a later item of the run it opens."""
    low = _norm(sentence)
    spans = _mentions(sentence, nat)
    return bool(spans) and all(_EXAMPLE_BEFORE_RE.search(low[max(0, start - 40):start]) or _in_example_run(low, start)
                               for start, _ in spans)


def _sentence_block(sentence, following, value, nat, negative, document_type, purpose, conditional_ok, page=None):
    """Why this sentence cannot decide the verdict for this route, or None.
    Every path that reads a verdict out of a sentence (the validator's
    anchored statement, a named or carried rule sentence, a group sentence,
    a list heading) passes through this gate."""
    sl = _norm(sentence)
    if negative and re.search(negative, sl, re.I):
        return 'flipped in the same sentence'
    if _excluded_by_statement(sentence, nat, value):
        return 'negated or removed in the same sentence'
    if _fee_row(sl):
        return 'fee or price row, not a rule sentence'
    if _carved_out(sentence, nat, following):
        return 'carved out by name'
    unreadable = _unreadable_exception(sentence, following, nat=nat, document_type=document_type)
    if unreadable is not None:
        return ('group named but an exception cannot be read: ' if _group_named(sentence, nat)
                else 'exception clause cannot be read: ') + unreadable[:80]
    restricted = _document_class_restriction(sl, document_type)
    if restricted:
        return 'scoped to ' + restricted + ', not the route document'
    scoped = _purpose_block(sl, purpose)
    if scoped:
        return scoped
    if _dated_rule_sentence(sl):
        undated = _unreadable_date(sl)
        if undated:
            return 'a date in the sentence cannot be read: ' + undated
    if not conditional_ok and (_conditional_marker(sl, value) or _footnote_marked(sentence, nat, page)):
        return 'conditional wording under an unconditional verdict'
    if not conditional_ok and _example_only(sentence, nat):
        return 'nationality named only as an example'
    return None


def _about_rule(low):
    """The normalized sentence speaks of a rule: policy or rule words, or a
    subject that refers back to the rule before it."""
    return bool(_POLICY_WORD_RE.search(low) or _RULE_WORDS.search(low) or _ANAPHOR_RE.search(low))


def _about_verdict(low, value, negative):
    """The normalized sentence speaks of this verdict's own rule: it states
    the rule, flips it, states another verdict in its place, or excludes
    from it. A sentence about something else (a processing time, a
    conversion of status, a document the traveller carries) decides nothing
    about this verdict."""
    positive, _ = _VERDICT_RULES.get(value, (None, None))
    return bool((positive and re.search(positive, low, re.I))
                or (negative and re.search(negative, low, re.I))
                or _states_opposite(low, value)
                or (_EXCLUSION_RE.search(low) and _concerns_verdict(low, value)))


# A predicate about a matter that is not the verdict: converting or
# extending a stay, processing an application, the validity or entries of
# the document issued, how days of stay are counted, the papers and fees an
# application needs, whom to contact. An exception hanging off such a
# predicate modifies that matter ("visa-exempt entry cannot be converted to
# visa-based stay, unless any of the following applies") and not the rule
# the verdict rests on.
_OTHER_MATTER_RE = re.compile(
    r"\b(?:cannot|can(?:not)?|could|may|might|must|shall|will|would|should|is|are|was|were|be|been|being|have|has|had)\s+"
    r"(?:not\s+|be\s+|not\s+be\s+|also\s+)?"
    r"(?:converted|convertible|extended|extendable|changed|renewed|prolonged|switched|transferred|processed|successful|refunded|"
    r"refundable|deducted|counted|reckoned|calculated|computed|paid|payable|charged|submitted|uploaded|attached|enclosed)\b|"
    r"\b(?:conversion|extension|renewal|prolongation)\s+of\s+(?:status|stay|the\s+(?:stay|visa|permit))\b|\bchange\s+of\s+status\b|"
    r"\b(?:processed|issued|delivered|sent|answered)\s+(?:within|in)\b|\bprocessing\s+(?:time|times|period|takes)\b|\b(?:working|business)\s+days\b|"
    r"\bvalid\s+for\s+(?:a\s+)?(?:\d+|one|single|multiple|two|three|several)\b|\b(?:multiple|single|double)[- ]entr(?:y|ies)\b|"
    r"\bvalidity\s+(?:of|is|shall|period)\b|"
    r"\b(?:period|duration|length)\s+of\s+(?:the\s+)?(?:stay|visit|residence)\s+(?:is|shall|will|starts|begins|ends)\b|"
    r"\b(?:standard|maximum|permitted)\s+period\s+(?:is|of)\b|"
    r"\b(?:return|onward|round[- ]trip)\s+tickets?\b|\btickets?\s+(?:to|for)\b|\btiket\b|\bbillets?\b|\bboletos?\b|\bproof\s+of\b|"
    r"\bhotel\s+(?:booking|reservation)\b|\baccommodation\b|\bsufficient\s+funds\b|\btravel\s+insurance\b|\bphotographs?\b|"
    r"\bpassport\s+(?:must|should|has|have|needs|is|be)\s+(?:be\s+)?(?:valid|at\s+least|remaining)\b|\bpassport\s+(?:validity|with\s+at\s+least)\b|"
    r"\b(?:fees?|payments?)\s+(?:are|is|must|shall|may|can)\b|\bpay(?:able|ment)?\s+(?:in|by|at|online)\b|\bcontact\b|\bopening\s+hours\b|\be-?mail\b", re.I)
# A verdict adjective on a noun ("visa-exempt entry", "visa-free stay") names
# the traveller's status, not the sentence's rule; the predicate is what
# states a rule.
_ATTRIBUTIVE_VERDICT_RE = re.compile(
    r"\bvisa[- ](?:free|exempt(?:ion)?|waiver)\s+(?:entry|entries|stay|stays|status|period|periods|visit|visits|visitors?|travell?ers?|"
    r"arrivals?|admission|regime|scheme|program(?:me)?|arrangement|policy|list|countries|nationals?|citizens?|holders?|passport holders?|"
    r"treatment|access|days|basis|entrants?|foreigners?|category|categories)\b", re.I)


def _host_text(sentence):
    """The normalized sentence without its exception clauses: the rule the
    clauses hang off."""
    low = _norm(sentence)
    for clause in reversed(list(_EXCEPTION_RE.finditer(low))):
        item = _clause_text(clause)
        full = clause.group(0)
        cut = full.find(item) + len(item) if item and item in full else len(full)
        low = low[:clause.start()] + ' ' + low[clause.start() + cut:]
    return ' '.join(low.split())


def _host_states_rule(host, value, negative):
    """The host states, flips or contradicts this verdict, excludes from it,
    or speaks of membership in the scheme it rests on."""
    stripped = _ATTRIBUTIVE_VERDICT_RE.sub(' ', host)
    positive, _ = _VERDICT_RULES.get(value, (None, None))
    return bool((positive and re.search(positive, stripped, re.I)) or (negative and re.search(negative, stripped, re.I))
                or _states_opposite(stripped, value) or (_EXCLUSION_RE.search(stripped) and _concerns_verdict(stripped, value))
                or (_SCHEME_NOUN_RE.search(stripped) and _MEMBERSHIP_WORD_RE.search(stripped)))


def _exception_on_another_rule(sentence, nat, value, negative, document_type=None):
    """The rule this sentence's exception clauses hang off is not this row's:
    the sentence is scoped to a document class the route is not, its
    traveller subject is another nationality alone, or its predicate is about
    another matter and states no rule of this verdict."""
    low = _norm(sentence)
    if _document_class_restriction(low, document_type) is not None:
        return True
    if (not _named(sentence, nat) and not _group_named(sentence, nat) and not _GENERAL_TRAVELLER_RE.search(low)
            and any(_named_as_traveller(sentence, other) for other in _known_nationalities() if other != nat)):
        return True
    host = _host_text(sentence)
    return bool(host and _OTHER_MATTER_RE.search(host) and not _host_states_rule(host, value, negative))


def _exception_binds(sentence, nat, value, negative, document_type=None, following=None):
    """An exception clause or a carve-out in this sentence can refuse this row.

    WHO the clause excepts decides this, never the words the sentence spends
    on a verdict. "The arrangement covers all ASEAN members except Myanmar"
    takes Myanmar out of the rule beside it without saying visa once, so an
    item that names this nationality binds, and so does an item the converter
    can resolve to nobody.

    What the clause excepts FROM decides the rest. A clause whose items all
    resolve elsewhere, to other nationalities, to a document class the route
    is not, or to a day or a fee ("processed within three days, excluding the
    day of submission"), binds nothing. A clause the converter cannot read
    still binds nothing when the rule it hangs off is not this row's: a
    sentence scoped to another document class, a sentence whose traveller is
    another nationality alone, or a predicate about converting a stay,
    processing an application, the validity of the document issued, how days
    are counted, or the papers an application needs. Such a clause modifies
    that rule and not the verdict; on a sentence that states, flips or
    contradicts this verdict, or speaks of the scheme's membership, it
    refuses.
    """
    if _carved_out(sentence, nat, following):
        return True
    if _unreadable_exception(sentence, following, nat=nat, document_type=document_type) is not None:
        return not _exception_on_another_rule(sentence, nat, value, negative, document_type)
    low = _norm(sentence)
    return bool(_about_verdict(low, value, negative)
                and (_named(sentence, nat) or _group_named(sentence, nat) or _GENERAL_TRAVELLER_RE.search(low)))


def _passage_block(text, value, nat, positive, negative, document_type, purpose, where, quoted):
    """Why the sentences of this text refuse the verdict for this route, or
    None. Every sentence is read, not only the deciding one: a suspension,
    withdrawal or dated-out rule anywhere in it, a carve-out or an
    exclusion that names this nationality, an exception clause that
    cannot be resolved to nationalities, a flip that names this
    nationality, or a date the parser cannot read in a rule sentence.
    `quoted` says the text is the reviewer's evidence, where every
    sentence counts; on a captured page section only sentences about a
    rule or naming the nationality are read. A carve-out and an exception
    bind only where the sentence is a rule this row is under, so a clause
    that belongs to another nationality's entry or to no traveller at all
    refuses nothing here."""
    for sentence in re.split(r'(?<=[.!?])\s+|[;。；！？\n]+', str(text or '')):
        low = _norm(sentence)
        if not low:
            continue
        names = _named(sentence, nat)
        # A carve-out names its nationality behind a negating prefix ("other
        # than Myanmar"), which is not a mention _named counts, so the
        # captured page's own filter has to ask for it by itself. Otherwise a
        # sentence that spends no rule word on the carve-out is never read.
        if not quoted and not (_about_rule(low) or names or _carved_out(sentence, nat)):
            continue
        if (not _WINDOW_SKIP_RE.search(low) and (_about_rule(low) or names)
                and re.search(_NOT_TODAYS_RULE, low, re.I) and _concerns_verdict(low, value)):
            return f'suspended, withdrawn or dated out in {where}: {low[:100]}'
        opposite = _states_opposite(low, value)
        # A question ("Do US citizens need a visa?") states nothing.
        question = bool(re.search(r'[?？]\s*$', sentence.rstrip()))
        if not opposite and _carved_out(sentence, nat) and _exception_binds(sentence, nat, value, negative, document_type):
            return f'carved out by name in {where}: {low[:100]}'
        if _excluded_by_statement(sentence, nat, value):
            return f'excluded from the rule in {where}: {low[:100]}'
        if _exception_binds(sentence, nat, value, negative, document_type):
            unreadable = _unreadable_exception(sentence, nat=nat, document_type=document_type)
            if unreadable is not None and not (positive and _exception_opens_list(sentence, positive, negative)):
                return f'an exception clause in {where} cannot be read: {unreadable[:80]}'
        # A sentence that states the opposite verdict for this nationality
        # as a traveller, for the route's own document and purpose and not
        # for a longer stay, contradicts the verdict.
        if (names and opposite and not question and _named_as_traveller(sentence, nat) and not _carved_out(sentence, nat)
                and not _LONGER_STAY_RE.search(low) and _document_class_restriction(low, document_type) is None
                and _purpose_block(low, purpose) is None):
            return f'the opposite verdict is stated for this nationality in {where}: {low[:100]}'
        if _dated_rule_sentence(low):
            undated = _unreadable_date(low)
            if undated:
                return f'a date in {where} cannot be read: {undated}'
    return None


def _section_text(index, part):
    """The captured page's section (between two headings) that holds the
    located quote part, or None when the part is not located."""
    kept, _, _, (starts, pieces) = index
    section = part.get('section')
    if part.get('pos') is None or section is None:
        return None
    lo = starts[section - 1] if section else 0
    # The heading that closes the section is read with it: a note standing
    # alone after a list ("Note: the exemption for Japan is suspended")
    # bounds the list and speaks about it.
    hi = starts[section] + len(pieces[section]) if section < len(starts) else len(kept)
    return kept[lo:hi]


def _sections_block(units, indexes, value, nat, positive, negative, document_type, purpose, bounds):
    """Why the captured page sections that hold the quotes refuse the
    verdict, or None: the same reading as the evidence, plus a dated
    policy window on the page that has ended, has not started, or is not
    recorded by the reviewer."""
    seen = set()
    for unit in units:
        for part in unit['parts']:
            if part.get('page') is None or part.get('pos') is None:
                continue
            index = indexes[part['page']]
            text = _section_text(index, part)
            key = (part['page'], part.get('section'))
            if text is None or key in seen:
                continue
            seen.add(key)
            reason = _passage_block(text, value, nat, positive, negative, document_type, purpose, 'the same page section', quoted=False)
            if reason:
                return reason
    # A dated window of this verdict's rule anywhere on a captured page the
    # quotes come from must be current and recorded: the sunset a page
    # states two sections below the quoted list still expires the verdict.
    # The page is read footnote body by footnote body, so a window stated
    # under one scheme's mark is read with the nationalities that mark names,
    # while a window that sits in no footnote is read against the whole page.
    for page in {part['page'] for unit in units for part in unit['parts'] if part.get('page') is not None}:
        for block, scope in _scope_blocks(indexes[page][0]):
            window = _window_violation([block], bounds, where='a policy window on the captured page', value=value,
                                       nat=nat, scope=scope)
            if window:
                return window
    return None


def _decision_supported(value, evidence_quotes, nat, pages=None, explain=None, *,
                        document_type='ordinary_passport', purpose='tourism', detail=None, bounds=None, page_gates=True):
    """Strict but shaped for real official pages: the nationality must be named
    in the evidence (in the rule sentence, as a label or antecedent of it, or
    as its own list line beside the rule sentence on the same page) and a
    sentence of the evidence must state the rule without flipping it in the
    same sentence, for the route's own passport class and purpose, for
    today, and without a condition when the subcategory is unconditional.
    A group the nationality belongs to (EU, ASEAN) counts only through the
    explicit membership table. `pages`, when given, is one (page id, page
    text) pair per quote; `explain`, when given, is a list that receives
    the path that decided; `bounds` are the policy dates the reviewer
    recorded."""
    def decide(path):
        if explain is not None:
            explain.append(path)
        return True

    def note(message):
        if explain is not None:
            explain.append(message)
    from app.visa_snapshot.evidence_validator import supports_disposition, NEGATED_VISA_EXEMPTION
    passages = '\n'.join(evidence_quotes)
    named = _named(passages, nat)
    if value == 'CONDITIONAL':
        return named and bool(_CONDITION_RE.search(passages))
    positive, negative = _VERDICT_RULES.get(value, (None, None))
    if positive and value == 'VISA_EXEMPT':
        negative = '(?:' + negative + ')|(?:' + NEGATED_VISA_EXEMPTION + ')'
    window = _window_violation(evidence_quotes, bounds, nat=nat)
    if window:
        note('rejected: ' + window)
        return False
    # The batch path always names the subcategory, so every unconditional
    # one is gated; a caller that names none (the pinned converters that
    # reuse this reading) asserts no subcategory and keeps its contract.
    conditional_ok = detail is None or detail in _CONDITIONAL_DETAILS
    # The whole evidence is read before any sentence decides: a suspension,
    # a carve-out, an exclusion, an unreadable exception, a flip or an
    # unreadable date anywhere in the quotes refuses the row.
    blocked_all = _passage_block(passages, value, nat, positive, negative, document_type, purpose, 'the evidence', quoted=True)
    if blocked_all:
        note('rejected: ' + blocked_all)
        return False
    units, indexes = _passages(evidence_quotes, pages, nat)
    if pages is not None and page_gates:
        # The captured page sections the quotes sit in are read the same
        # way, and a dated policy window there must be current and recorded.
        blocked_page = _sections_block(units, indexes, value, nat, positive, negative, document_type, purpose, bounds)
        if blocked_page:
            note('rejected: ' + blocked_page)
            return False

    def block(sentence, following, page=None):
        return _sentence_block(sentence, following, value, nat, negative, document_type, purpose, conditional_ok, page)
    # The validator's anchored statement still needs a sentence of its own
    # that states the rule, names the nationality as a traveller and passes
    # the gate.
    if positive:
        sentences = _SENTENCE_SPLIT.split(passages)
        for i, sentence in enumerate(sentences):
            following = sentences[i + 1] if i + 1 < len(sentences) else None
            if (re.search(positive, _norm(sentence), re.I) and _named_as_traveller(sentence, nat)
                    and block(sentence, following) is None
                    and supports_disposition(sentence, value, nationality=nat)):
                return decide('validator: anchored statement')
    elif named and supports_disposition(passages, value, nationality=nat):
        return decide('validator: anchored statement')
    if not positive:
        return False
    listed = [part for unit in units for part in unit['parts']
              if not part['ambiguous'] and not part['boilerplate'] and _list_line(part['text'], nat, document_type)]
    for unit in units:
        if unit['ambiguous']:
            note('list line occurs under different verdict headings: ' + _norm(unit['text'])[:120])
        if unit['boilerplate']:
            note('list line sits in navigation boilerplate: ' + _norm(unit['text'])[:120])
    others = [n for n in _EXTRA_ALIASES if n != nat]
    seen_positive = False
    for unit in units:
        index = indexes.get(unit['page']) if unit['page'] is not None else None
        page = index[0] if index else None
        body = _label(unit['text'], nat)
        labelled = body is not None
        previous_named = False
        sentences = _SENTENCE_SPLIT.split(body if labelled else unit['text'])
        for i, sentence in enumerate(sentences):
            sl = _norm(sentence)
            following = sentences[i + 1] if i + 1 < len(sentences) else None
            blocked = block(sentence, following, page)
            # The nationality must be named as the traveller: a country named
            # as the destination of travel never proves a verdict for its own
            # nationals.
            sentence_named = _named_as_traveller(sentence, nat)
            # A label or the previous sentence lends its nationality only to a
            # sentence that brings no subject of its own.
            borrowable = (not _named(sentence, nat) and not _OWN_SUBJECT_RE.search(sl)
                          and not any(_named(sentence, other) for other in others))
            carried = borrowable and (labelled or previous_named)
            # A blocked sentence cannot lend its nationality to the next one:
            # "do not need a visa" followed by an optional "may apply for a
            # visa" is still an exemption, a price row names a product's
            # buyer, a carve-out or a class restriction names no traveller
            # of this route.
            previous_named = (sentence_named or carried) and blocked is None
            if not re.search(positive, sl, re.I):
                continue
            seen_positive = True
            # An exception clause that opens a list and states the verdict
            # inside it is the rule of that list: only a list line under it
            # can prove the verdict.
            list_only = (blocked is not None and 'cannot be read' in blocked
                         and _exception_opens_list(sentence, positive, negative))
            if blocked and not list_only:
                note(blocked + ': ' + sl[:120])
                continue
            if sentence_named and not list_only:
                return decide('named in the rule sentence: ' + sl[:120])
            if carried and not list_only:
                return decide(('label' if labelled else 'previous sentence') + ' names the nationality: ' + sl[:120])
            if not list_only and _group_member(sentence, nat, following, document_type):
                return decide('group membership: ' + sl[:120])
            if listed and _LIST_INTRO_RE.search(sl):
                span = _sentence_span(index, sentence, unit) if index else None
                entry = _same_list(listed, unit['page'], span, bool(_LIST_ABOVE_RE.search(sl)), index, value)
                if entry is not None and not conditional_ok and _footnote_marked(entry['text'], nat, page):
                    note('footnote on the list line under an unconditional verdict: ' + _norm(entry['text'])[:120])
                elif entry is not None:
                    return decide('list line beside the rule sentence: ' + sl[:120])
                else:
                    note('list line on another page or under another heading: ' + sl[:120])
            elif listed:
                note('list line but the rule sentence opens no list: ' + sl[:120])
    note('rejected: ' + ('nationality never named' if not named else
                         'no rule sentence in the evidence' if not seen_positive else
                         'rule sentence does not name the nationality'))
    return False


def _consular_product_eligibility_supported(value, evidence, sources, route, product):
    """Positive consular acceptance proves an optional product, not a route.

    This deliberately narrow evidence mode accepts national-passport tourist
    applications with prior immigration approval. The destination embassy
    must actually invite physical applications, and its exact eligibility
    sentence must remain visible in the product notes. A form, embassy name,
    eVisa offer or default visa exemption alone cannot establish this lane.
    """
    if (not isinstance(product, dict) or value != 'VISA_REQUIRED'
            or product.get('disposition') != value
            or product.get('requirement_detail') != 'paper_visa'
            or route.get('travel_purpose') != 'tourism'
            or (route.get('travel_document_type') or 'ordinary_passport') != 'ordinary_passport'
            or not re.search(r'\btourist\b', _norm(product.get('type')))):
        return False
    from app.visa_snapshot.authority import hostname
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    relevant = [item for item in evidence
                if jurisdiction_matches(item['source_url'], route['destination_country'])]
    # The traveller's nationality must be explicit on a destination page.
    # Origin-country advice cannot supply the only identity anchor.
    if not any(_list_line(item['quote'], route['passport_nationality']) for item in relevant):
        return False
    acceptance = re.compile(
        r'\b(?:the )?embassy (?:only )?(?:accepts|processes) '
        r'(?:tourist )?visa applications for holders of (?:valid )?national passports '
        r'with approval letters issued by the immigration department of [^.!?\n]+[.!?]?', re.I)
    invitation = re.compile(r'\binvited to submit your (?:tourist )?visa application '
                            r'directly to the embassy(?:[’\']s)? (?:office|consular section)\b', re.I)
    tourist_heading = re.compile(r'^(?:[ivx\d]+[.)]?\s*)?tourist visa\s*:?$', re.I)
    for item in relevant:
        host = hostname(item['source_url'])
        local = [e for e in relevant if hostname(e['source_url']) == host]
        for sentence in acceptance.findall(item['quote']):
            # An immigration approval letter is a real prerequisite. Do not
            # let a review quietly remove it from the traveller's product.
            approval_country = _norm(re.split(r'immigration department of ', sentence,
                                             flags=re.I)[-1]).rstrip('.!?')
            if approval_country not in _aliases(route['destination_country']):
                continue
            notes = str(product.get('notes') or '')
            if (not quote_literal(sentence, notes)
                    or re.search(r'approval(?: letters?)?[^.!?\n]{0,25}'
                                 r'\b(?:not required|optional|unnecessary|can be omitted)\b', notes, re.I)):
                continue
            if not any(tourist_heading.fullmatch(_norm(e['quote'])) for e in local):
                continue
            if not any(invitation.search(_norm(e['quote'])) for e in local):
                continue
            # Look at whole supplied captures too: quoting an old invitation
            # cannot ignore an explicit suspension on that captured page.
            pages = '\n'.join(sources[e['source_id']]['text'] for e in local)
            statements = [_norm(s) for s in re.split(r'(?<=[.!?])\s+|\n+', pages)]
            adverse = False
            for statement in statements:
                if (re.search(r'\b(?:no longer|not|unavailable|suspend\w*|stop\w*|clos\w*|halt\w*|ceas\w*)\b', statement)
                        and re.search(r'\b(?:accept\w*|process\w*|issu\w*|applications?|services?|eligible)\b', statement)
                        and re.search(r'\b(?:tourist|visa|passport)\b', statement)
                        and not re.fullmatch(
                            r'any other travel documents such as documents issued for refugees '
                            r'or national passports without approval letters are not acceptable[.!?]?',
                            statement)
                        and not (re.search(r'\bby email\b', statement)
                                 and not re.search(r'\b(?:or|in person|post)\b', statement))):
                    adverse = True
                    break
            if not adverse:
                return True
    return False


# An explicit statement that nothing is charged, or a zero standing in the
# fee column of the quoted row: a pipe-separated "| 0 |" cell, a zero
# beside a currency, or a tariff row (a name followed by figure cells
# only) with a zero cell ("70 Indonesia 00 00 00 00", "Malaysia 00 00 40
# 200"). A bare "0" inside "30 days" or a tariff row without a zero cell
# ("Malaysia 10 25 40 200") proves nothing.
_ZERO_FEE_RE = re.compile(
    r"\bfree\b|\bfree of charge\b|\bno fee\b|\bno fees\b|\bno charge\b|\bgratis\b|\bgratuit\w*|\bkostenlos\b|\bwaived\b|"
    r"\bexempt(?:ed)? from (?:the |any |all )?(?:visa )?fees?\b|\bno visa\b|\bvisa[- ]free\b|\bwithout (?:a )?visa\b|\bexempt\b|"
    r"免费|免費|無料|무료|miễn phí|percuma|ฟรี|бесплатно|"
    r"(?:^|\|)\s*(?:0|0[.,]00)\s*(?:\||$)|(?<![\d.,])(?:usd|eur|gbp|aud|cad|sgd|nzd|\$|€|£)\s*0(?:[.,]00)?(?!\d)(?![.,]\d)|"
    r"(?<![\d.,])0(?:[.,]00)?\s*(?:usd|eur|gbp|aud|cad|sgd|nzd|dollars?|euros?|pounds?)\b|"
    r"^\s*(?:\d{1,3}[.)]?\s+)?[^\d\n|]{2,60}?(?:[\s|]+\d+(?:[.,]\d+)?)*[\s|]+0+(?:[.,]0+)?(?:[\s|]+\d+(?:[.,]\d+)?)*[\s|]*$", re.I | re.M)
_NUMBER_WORDS_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|same[- ]day|immediate\w*|instant\w*|"
    r"varies|variable|weeks?|months?|days?|hours?|minutes?)\b", re.I)


def _url_key(url):
    """The address without scheme, leading www and trailing slash."""
    key = _norm(url).split('#')[0].strip()
    key = re.sub(r'^https?://', '', key)
    key = re.sub(r'^www\.', '', key)
    return key.rstrip('/')


def _portal_url_in_evidence(value, passages, pages, evidence_urls):
    """The portal address occurs literally in the quote or on a captured
    destination page of the evidence, or is itself the address of such a
    page (or a parent path of it on the same host)."""
    key = _url_key(value)
    if not key or '.' not in key.split('/')[0]:
        return False
    haystacks = [_norm(passages)] + [_norm(text) for _, text in (pages or [])]
    if any(key in re.sub(r'\s+', '', h) for h in haystacks):
        return True
    for url in evidence_urls or []:
        other = _url_key(url)
        if other == key or other.startswith(key + '/'):
            return True
    return False


def _check_value(field, value, passages, route, product, pages=None, detail=None, bounds=None, evidence_urls=None, page_gates=False):
    from app.visa_snapshot.evidence_validator import field_value_supported
    if field == 'disposition':
        quotes = [q for q in passages.split('\n') if q.strip()]
        if pages is not None and len(pages) != len(quotes):
            # A quote with a line break inside cannot keep its page pairing.
            pages = None
        window = _window_violation(quotes, bounds, nat=route['passport_nationality'])
        if window:
            raise PatchRejected('disposition: ' + window)
        explain = []
        if not _decision_supported(value, quotes, route['passport_nationality'], pages=pages, explain=explain,
                                   document_type=route.get('travel_document_type') or 'ordinary_passport',
                                   purpose=route.get('travel_purpose') or 'tourism', detail=detail, bounds=bounds,
                                   page_gates=page_gates):
            # The refusal names the gate that closed, so a reviewer can act on it.
            reason = next((e[len('rejected: '):] for e in reversed(explain) if e.startswith('rejected: ')), '')
            raise PatchRejected('disposition: evidence does not state this verdict for this nationality' + (f' ({reason[:160]})' if reason else ''))
        return
    if field in ('permitted_stay_days', 'max_stay_days'):
        if value is not None and not field_value_supported(field, value, passages):
            raise PatchRejected(f'{field}: the figure is not in its evidence')
    elif field in ('government_fee', 'fee'):
        if isinstance(value, dict) and value.get('amount') not in (None, 0):
            code = str(value.get('currency') or '').upper()
            foreign = _foreign_dollar_amount(passages, code, value.get('amount'))
            if foreign:
                raise PatchRejected(f'{field}: the amount is stated in another dollar currency ({foreign})')
            text = _monetary_text(passages, code)
            if not field_value_supported(field, value, text):
                raise PatchRejected(f'{field}: the fee amount is not in its evidence')
        elif isinstance(value, dict) and value.get('amount') == 0:
            if not _ZERO_FEE_RE.search(passages):
                raise PatchRejected(f'{field}: a zero fee needs an explicit free, waived or no-visa statement')
    elif field == 'processing_time':
        if value and not field_value_supported(field, value, passages):
            # Working-day and calendar figures must appear in the evidence;
            # a value without a figure must occur in the quote in its own
            # words, so "same day" or "varies" is never served unsupported.
            digits = re.findall(r'\d+', str(value))
            low_value, low_passages = _norm(value), _norm(passages)
            if digits:
                if not all(d in passages for d in digits):
                    raise PatchRejected('processing_time: the figure is not in its evidence')
            else:
                words = _NUMBER_WORDS_RE.findall(low_value)
                if not words or not all(re.search(r'\b' + re.escape(w) + r'\b', low_passages) for w in words):
                    if low_value not in low_passages:
                        raise PatchRejected('processing_time: a value without a figure must occur in its evidence')
    elif field == 'application_channel':
        if value and not field_value_supported(field, {'online_portal': 'online', 'embassy_or_consulate': 'embassy',
                                                       'embassy_designated_agency': 'authorised_agent',
                                                       'authorised_agent': 'authorised_agent', 'on_arrival': 'on_arrival',
                                                       'not_required': 'none'}.get(value, value), passages):
            raise PatchRejected('application_channel: the channel is not described in its evidence')
    elif field == 'official_portal_url':
        # The address itself must be on the captured destination page or in
        # the quote; a quote that merely describes an application proves no
        # address.
        if value and not _portal_url_in_evidence(value, passages, pages, evidence_urls):
            raise PatchRejected('official_portal_url: the portal address is not in its quote or on its captured page')
    elif field == 'entry':
        if value and not re.search({'single': r'single|one entry|一次', 'double': r'double|two entries|二次|兩次|两次',
                                    'multiple': r'multiple|multi|数次|多次'}[value], passages, re.I):
            raise PatchRejected('entry: the entry count is not in its evidence')
    elif field == 'validity':
        digits = re.findall(r'\d+', str(value or ''))
        if value and digits and not all(d in passages for d in digits):
            raise PatchRejected('validity: the validity figure is not in its evidence')


def validate_batch(batch, *, strict=True):
    """Validate every row. Strict mode raises on the first defect; otherwise
    the defective rows are returned separately with their reason, so one
    unprovable route never blocks the publishable ones. A rejected row is
    never published either way."""
    if batch.get('kind') != KIND or batch.get('schema_version') != 1 or not batch.get('id'):
        raise PatchRejected('Not a general reviewed batch')
    sources = _source_table(batch)
    rows = batch.get('rows') or []
    if not rows or len({r.get('cache_key') for r in rows}) != len(rows):
        raise PatchRejected('Empty batch or duplicate route rows')
    accepted, rejected = [], []
    for row in rows:
        try:
            _validate_row(row, sources)
            accepted.append(row)
        except PatchRejected as exc:
            if strict:
                raise
            rejected.append({'cache_key': row.get('cache_key'), 'reason': str(exc)})
    if strict:
        return sources
    return sources, accepted, rejected


def _dropped_proof(reason):
    """The record of a field proof this review could not accept. It asserts
    nothing: the conversion leaves the prior layer's value and provenance
    untouched and reports the drop. Only a proof the reviewer marked
    unknown or not_published blanks a field."""
    return {'status': 'unknown', 'verifier': 'ai', 'dropped': True,
            'reason': 'Not asserted by this review: ' + reason}


def _record_drop(dropped, message):
    """Record a dropped value once per row. The batch is validated again at
    conversion, and product names are unique within a row, so a repeated
    message can only be the same drop seen on a later pass."""
    if message not in dropped:
        dropped.append(message)


def _validate_row(row, sources):
    from app.visa_snapshot.kimi_primary import DISPOSITIONS
    from app.visa_snapshot.verified_overrides import _DETAIL_FAMILY
    if True:
        route = row.get('route') or {}
        if not all(route.get(k) for k in ('passport_nationality', 'destination_country', 'travel_purpose')):
            raise PatchRejected('Incomplete route identity')
        route.setdefault('travel_document_type', 'ordinary_passport')
        verdict = row.get('verdict') or {}
        disp, detail = verdict.get('disposition'), verdict.get('requirement_detail')
        if disp not in DISPOSITIONS or detail not in _DETAIL_FAMILY.get(disp, ()):
            raise PatchRejected('Verdict outside the disposition and subcategory vocabulary')
        if _check_proof(verdict.get('proof'), sources, route, 'disposition', disp, detail=detail, page_gates=True) is None:
            raise PatchRejected('A published verdict cannot be unknown')
        proofs = row.get('route_field_proofs')
        if not isinstance(proofs, dict):
            # A dropped value is recorded here as an explicit unknown, so the
            # table must be the row's own.
            proofs = row['route_field_proofs'] = {}
        values = row.get('route_fields') or {}
        dropped = row.setdefault('dropped', [])
        for key, covered in ROUTE_PROOF_COVERS.items():
            if key in proofs:
                if isinstance(proofs[key], dict) and proofs[key].get('status') in ('unknown', 'not_published'):
                    # The proof says the figure is unknown or not published:
                    # the prose it covers is unproved too and is not asserted.
                    for c in covered:
                        if c != key and not _empty_value(values.get(c)):
                            _record_drop(dropped, f'{c}: a value under an unknown or unpublished {key} proof')
                            values.pop(c, None)
                try:
                    _check_proof(proofs[key], sources, route, key, values.get(key), page_gates=True)
                except PatchRejected as exc:
                    # An ancillary value that cannot be proved is not asserted;
                    # the verdict is the only field that decides the row. The
                    # field becomes an explicit unknown carrying the reason, so
                    # the served entry cannot keep an earlier value for it.
                    _record_drop(dropped, str(exc))
                    proofs[key] = _dropped_proof(str(exc))
                    for c in covered:
                        values.pop(c, None)
            elif any(values.get(c) not in (None, [], '') for c in covered):
                reason = f'{key}: a value without a proof'
                _record_drop(dropped, reason)
                proofs[key] = _dropped_proof(reason)
                for c in covered:
                    values.pop(c, None)
        for product in row.get('products') or []:
            if product.get('action') not in ('keep', 'patch', 'remove', 'add'):
                raise PatchRejected('Unknown product action')
            if product['action'] == 'remove':
                if not str(product.get('remove_reason') or '').strip():
                    raise PatchRejected('A removed product needs a reason')
                continue
            spec = product.get('product') or {}
            if not str(spec.get('type') or '').strip():
                raise PatchRejected('A product needs a name')
            pd, pdet = spec.get('disposition'), spec.get('requirement_detail')
            if pd not in DISPOSITIONS or pdet not in _DETAIL_FAMILY.get(pd, ()):
                raise PatchRejected('Product verdict outside the vocabulary: ' + spec['type'])
            pproofs = product.get('proofs') or {}
            for field in PRODUCT_PROOF_FIELDS:
                value = spec.get(field)
                try:
                    if field in pproofs:
                        _check_proof(pproofs[field], sources, route, field, value, product=spec, detail=pdet, page_gates=True)
                    elif field == 'disposition' and product.get('verdict_unproved'):
                        # Recorded on an earlier pass over this row: the batch
                        # is validated again at conversion and must not report
                        # the same unproved verdict twice.
                        continue
                    elif value not in (None, '', {}) and not (isinstance(value, dict) and value.get('amount') is None):
                        raise PatchRejected(f'{field}: has a value but no proof')
                except PatchRejected as exc:
                    # Reported under the product's name, so two products that
                    # drop the same field stay distinguishable in the report.
                    _record_drop(dropped, f'product {spec["type"]}: {exc}')
                    if field == 'disposition' and field in pproofs:
                        # A product verdict whose own proof fails is unproved:
                        # the product is not served with an asserted verdict.
                        product['verdict_unproved'] = str(exc)
                    pproofs.pop(field, None)
                    if field != 'disposition':
                        spec[field] = {'amount': None, 'currency': None} if field == 'fee' else None
                        pproofs[field] = {'status': 'unknown', 'verifier': 'ai', 'reason': 'No literal official quote supports this value'}


def build_manifest(batch, layers):
    """Bind the accepted rows to exact captured serving layers; no writes.
    Rejected rows are listed with their reason and bound to nothing."""
    _, accepted, rejected = validate_batch(batch, strict=False)
    if not accepted:
        raise PatchRejected('No row in the batch is publishable: ' + '; '.join(r['reason'] for r in rejected)[:400])
    by_key = {r['cache_key']: r for r in accepted}
    layers = [l for l in (layers or []) if l.get('cache_key') in by_key]
    if not isinstance(layers, list) or {l.get('cache_key') for l in layers} != set(by_key) or len(layers) != len(by_key):
        raise PatchRejected('Missing, extra or duplicate baseline routes')
    entries = []
    for layer in layers:
        row = by_key[layer['cache_key']]
        if any(k not in layer for k in BASELINE_KEYS):
            raise PatchRejected('Missing raw/merged/ordered source layer capture')
        if route_identity(layer['route']) != route_identity(row['route']):
            raise PatchRejected('Stored route differs from the reviewed subject: ' + layer['cache_key'])
        products = layer['merged_guidance'].get('visa_products') or []
        matches = []
        for product in row.get('products') or []:
            name = product.get('current_name')
            if product['action'] == 'add':
                if any(p.get('type') == (product.get('product') or {}).get('type') for p in products):
                    raise PatchRejected('Added product already exists: ' + str((product.get('product') or {}).get('type')))
                matches.append({'current_name': None, 'product_sha256': None})
                continue
            found = [p for p in products if p.get('type') == name]
            if len(found) != 1:
                raise PatchRejected('Missing or ambiguous current product identity: ' + str(name))
            matches.append({'current_name': name, 'product_sha256': digest(found[0])})
        baseline = {k: deepcopy(layer[k]) for k in BASELINE_KEYS}
        entries.append({'cache_key': layer['cache_key'], 'route': deepcopy(layer['route']), 'matches': matches,
                        'baseline': baseline, 'baseline_sha256': {k: digest(v) for k, v in baseline.items()}})
    kept = dict(deepcopy(batch), rows=[r for r in batch['rows'] if r['cache_key'] in by_key])
    return {'schema_version': 1, 'kind': 'general_reviewed_exact_layers', 'id': batch['id'],
            'batch': kept, 'routes': entries, 'rejected': rejected,
            'status': 'detached candidate; no registration or activation'}


def _prior(layer, route):
    from app.visa_snapshot import verified_overrides as vo
    table = vo._parse_rows(layer['seed_entries'], {})
    if layer['operator_entries']:
        raise PatchRejected('Operator edits on this route need separate review: ' + layer['cache_key'])
    return table.get(vo._key(route['passport_nationality'], route['destination_country'],
                            route['travel_purpose'], route.get('travel_document_type') or 'ordinary_passport')) or {}


def _proof_for(proof, route, field, product=None):
    if proof.get('status') == 'not_published':
        # The store records an explicit null with its reason; the record
        # cell reads "not published" through unpublished_fields.
        proof = {'status': 'unknown', 'verifier': 'ai',
                 'reason': 'Not published by the destination: ' + str(proof.get('reason') or '').strip()}
    converted = field_provenance(proof, route, field, product)
    for key in ('effective_from', 'effective_to', 'policy_interval_evidence', 'verification_scope'):
        if key in proof:
            converted[key] = deepcopy(proof[key])
    if product is not None:
        from scripts.convert_reviewed_product_patch import _subject
        for evidence in (converted.get('policy_interval_evidence') or {}).values():
            if evidence.get('subject') == _subject(route):
                evidence['subject'] = _subject(route, product)
    return converted


def _reviewed_optional_products(products, specs, verdict, sources, route):
    """An exemption can coexist with a separately reviewed paid visa option.

    Do not silently delete optional products to make a route pass integrity
    checks. A paid option needs its own current review (not the route's free
    verdict), and a real reviewed exemption product must remain beside it.
    Previously stored or failed proofs cannot establish either lane.
    """
    def paid(product):
        amount = (product.get('fee') or {}).get('amount')
        return isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0

    if not any(paid(p) for p in products):
        return
    reviewed = {s['product']['type']: s for s in specs
                if s.get('action') != 'remove' and s.get('product')}

    def check(product, *, exemption=False):
        spec = reviewed.get(product.get('type'))
        if spec is None:
            raise PatchRejected('Optional paid products and their exemption lane need an explicit current product review')
        proofs = spec.get('proofs') or {}
        decision = proofs.get('disposition')
        if exemption and decision is None:
            decision = deepcopy(verdict['proof'])
            from scripts.convert_reviewed_product_patch import _subject
            for evidence in (decision.get('policy_interval_evidence') or {}).values():
                if evidence.get('subject') == _subject(route):
                    evidence['subject'] = _subject(route, product)
        if _check_proof(decision, sources, route, 'disposition', product.get('disposition'), product=product,
                        detail=product.get('requirement_detail'), page_gates=True) is None:
            raise PatchRejected('An optional product needs its own reviewed verdict')
        for field in PRODUCT_PROOF_FIELDS[1:]:
            value = product.get(field)
            empty = value in (None, '', {}) or (isinstance(value, dict) and all(v is None for v in value.values()))
            if not empty and _check_proof(proofs.get(field), sources, route, field, value, product=product, page_gates=True) is None:
                raise PatchRejected('An optional product value needs its own reviewed proof: ' + field)

    free_lanes = [p for p in products
                  if p.get('disposition') == 'VISA_EXEMPT'
                  and p.get('requirement_detail') == verdict['requirement_detail']
                  and not paid(p)]
    if not free_lanes:
        raise PatchRejected('Optional paid visa products require an explicit reviewed exemption lane')
    # A named zero-fee model product is not evidence for the free lane.
    for product in free_lanes:
        check(product, exemption=True)
    for product in products:
        if paid(product):
            if product.get('disposition') == 'VISA_EXEMPT':
                raise PatchRejected('A paid product cannot itself claim visa exemption')
            check(product)


def convert(manifest, current_layers):
    """Return the overlay candidate and a per-route report."""
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if manifest.get('kind') != 'general_reviewed_exact_layers' or manifest.get('schema_version') != 1:
        raise PatchRejected('Malformed general integration manifest')
    batch = manifest['batch']
    sources = validate_batch(batch)
    rebuilt = build_manifest(batch, [dict(deepcopy(e['baseline']), cache_key=e['cache_key']) for e in manifest['routes']])
    if rebuilt['rejected']:
        raise PatchRejected('A bound row no longer validates: ' + rebuilt['rejected'][0]['reason'])
    for actual, checked in zip(manifest['routes'], rebuilt['routes'], strict=True):
        if actual != checked:
            raise PatchRejected('Match or baseline contract was altered')
    rows = {r['cache_key']: r for r in batch['rows']}
    wanted = {e['cache_key'] for e in manifest['routes']}
    current = {l['cache_key']: l for l in current_layers if l.get('cache_key') in wanted}
    if set(current) != wanted:
        raise PatchRejected('Current route set differs from baseline')
    today = _today().isoformat()
    entries, reports = [], []
    for entry in manifest['routes']:
        layer = current[entry['cache_key']]; route = entry['route']; row = rows[entry['cache_key']]
        for key in BASELINE_KEYS:
            if digest(layer.get(key)) != entry['baseline_sha256'][key]:
                raise PatchRejected('Layer changed since the reviewed baseline: ' + key + ' ' + entry['cache_key'])
        old = _prior(layer, route)
        fields = deepcopy(old.get('fields') or {})
        proofs = deepcopy(old.get('field_provenance') or {})
        unpublished = set(fields.get('unpublished_fields') or [])
        verdict = row['verdict']
        same_permission = (fields.get('disposition') == verdict['disposition']
                           and fields.get('requirement_detail') == verdict['requirement_detail'])
        fields['disposition'] = verdict['disposition']
        fields['requirement_detail'] = verdict['requirement_detail']
        from app.visa_snapshot.policy_intervals import inherit_bounds
        vproof = _proof_for(verdict['proof'], route, 'disposition')
        previous_verdict = proofs.get('disposition') or {}
        if same_permission and previous_verdict.get('subject') == vproof.get('subject'):
            vproof = inherit_bounds(previous_verdict, vproof)
        proofs['disposition'] = vproof
        proofs['requirement_detail'] = dict(vproof, note='The subcategory is read from the same verified verdict passage.')
        values = row.get('route_fields') or {}
        for key, covered in ROUTE_PROOF_COVERS.items():
            proof = (row.get('route_field_proofs') or {}).get(key)
            if proof is None:
                continue
            if proof.get('dropped'):
                # This review could not prove the field, so it asserts
                # nothing about it: the prior layer's value, provenance and
                # not-published state stand, and the report lists the drop.
                continue
            unpublished.difference_update(NOT_PUBLISHED_CELLS.get(key, ()))
            if proof.get('status') in ('unknown', 'not_published'):
                for c in covered:
                    fields[c] = None
                    proofs[c] = _proof_for(proof, route, c)
                if proof.get('status') == 'not_published':
                    unpublished.update(NOT_PUBLISHED_CELLS.get(key, ()))
                continue
            for c in covered:
                if c in values and values[c] not in (None, '', []):
                    fields[c] = deepcopy(values[c])
                    proofs[c] = _proof_for(proof, route, c)
        # Products: the current merged list is the starting point.
        products = deepcopy(layer['merged_guidance'].get('visa_products') or [])
        by_name = {p.get('type'): p for p in products}
        final, unsupported, removed, inherited = [], [], [], []
        seen = set()
        for spec in row.get('products') or []:
            action = spec['action']
            if action == 'remove':
                removed.append({'type': spec.get('current_name'), 'reason': spec.get('remove_reason')})
                seen.add(spec.get('current_name'))
                continue
            seen.add(spec.get('current_name'))
            if spec.get('verdict_unproved'):
                same_verdict = (spec['product'].get('disposition') == verdict['disposition']
                                and spec['product'].get('requirement_detail') == verdict['requirement_detail'])
                if same_verdict:
                    # The product's own quote does not state the verdict, but
                    # the product asserts nothing beyond the route's proved
                    # verdict: it inherits the route proof below, and the
                    # failure is reported rather than the product erased.
                    inherited.append({'type': spec['product'].get('type'), 'reason': spec['verdict_unproved']})
                else:
                    # The reviewer asserted a product verdict that differs from
                    # the route's and its evidence does not state it; neither
                    # that verdict nor the current product is served. On an
                    # exemption lane a paid option without its own reviewed
                    # verdict stays a row-level defect, as before.
                    amount = ((spec['product'].get('fee') or {}).get('amount')
                              if isinstance(spec['product'].get('fee'), dict) else None)
                    if verdict['disposition'] == 'VISA_EXEMPT' and isinstance(amount, (int, float)) and amount > 0:
                        raise PatchRejected('An optional product needs its own reviewed verdict')
                    removed.append({'type': spec['product'].get('type'), 'reason': spec['verdict_unproved']})
                    continue
            base = deepcopy(by_name.get(spec.get('current_name')) or {}) if action != 'add' else {}
            product = dict(base)
            pspec = spec['product']
            product['type'] = pspec['type']
            product['disposition'] = pspec['disposition']
            product['requirement_detail'] = pspec['requirement_detail']
            pproofs = deepcopy(product.get('field_provenance') or {})
            product_unpublished = set(product.get('unpublished_fields') or [])
            for field in PRODUCT_FIELDS:
                proof = (spec.get('proofs') or {}).get(field)
                unbound = isinstance(proof, dict) and str(proof.get('reason') or '').startswith('No literal official quote')
                if unbound and action != 'add':
                    # The reviewer's quote could not be captured: the current
                    # value stays as it was, unsupported and untouched.
                    continue
                if field in pspec and (proof is not None or action == 'add'):
                    product_unpublished.difference_update(NOT_PUBLISHED_CELLS.get(field, ()))
                    if proof is not None and proof.get('status') in ('unknown', 'not_published'):
                        product[field] = None if field != 'fee' else {'amount': None, 'currency': None}
                        pproofs[field] = _proof_for(proof, route, field, product)
                        if proof.get('status') == 'not_published':
                            product_unpublished.update(NOT_PUBLISHED_CELLS.get(field, ()))
                    elif proof is not None:
                        product[field] = deepcopy(pspec[field])
                        pproofs[field] = _proof_for(proof, route, field, product)
            product['unpublished_fields'] = sorted(product_unpublished)
            decision = (spec.get('proofs') or {}).get('disposition')
            if (decision is None or decision.get('status') != 'reviewed') \
                    and product['disposition'] == verdict['disposition'] \
                    and product['requirement_detail'] == verdict['requirement_detail'] \
                    and action != 'add' or (action == 'add' and decision is None
                                            and product['disposition'] == verdict['disposition']
                                            and product['requirement_detail'] == verdict['requirement_detail']
                                            and any(p.get('status') == 'reviewed' for p in pproofs.values())):
                # A product that carries the route's own verdict inherits the
                # route's nationality-anchored verdict proof: the rule is
                # proved once for the traveller, the product's own quotes bind
                # its identity, fee, stay and validity. A product whose verdict
                # differs from the route's must prove its own.
                decision = verdict['proof']
            if decision is None or decision.get('status') != 'reviewed':
                if (product['disposition'] != verdict['disposition']
                        or product['requirement_detail'] != verdict['requirement_detail']):
                    # A reviewed product whose verdict differs from the
                    # route's must prove its own; without a reviewed proof
                    # its verdict is not served. On an exemption lane a paid
                    # option without its own reviewed verdict stays a
                    # row-level defect, as before.
                    amount = (product.get('fee') or {}).get('amount') if isinstance(product.get('fee'), dict) else None
                    if verdict['disposition'] == 'VISA_EXEMPT' and isinstance(amount, (int, float)) and amount > 0:
                        raise PatchRejected('An optional product needs its own reviewed verdict')
                    removed.append({'type': product['type'],
                                    'reason': 'disposition: the product verdict differs from the route verdict and has no reviewed proof'})
                    continue
                unsupported.append(product['type'])
                product['field_provenance'] = pproofs
                final.append(product)
                continue
            dproof = _proof_for(decision, route, 'disposition', product)
            previous_decision = pproofs.get('disposition') or {}
            if (action != 'add' and previous_decision.get('subject') == dproof.get('subject')
                    and previous_decision.get('subject')):
                # Rechecking this exact product cannot erase its own known
                # interval. A renamed product or different permission/detail
                # must not inherit the previous program's policy notice.
                dproof = inherit_bounds(previous_decision, dproof)
            pproofs['disposition'] = dproof
            pproofs['requirement_detail'] = dict(dproof, note='The subcategory is read from the same verified verdict passage.')
            product['field_provenance'] = pproofs
            product['source_url'] = dproof['source_url']
            product['source_quote'] = dproof['quote']
            product['verified_at'] = dproof['verified_at']
            product['verifier'] = 'ai'
            product['corroborating_sources'] = [
                {'url': e['source_url'], 'quote': e['quote'], 'checked_at': dproof['verified_at'],
                 'authority': 'Official source; AI field review'}
                for e in decision['evidence'][1:]]
            final.append(product)
        for name, product in by_name.items():
            if name not in seen:
                unsupported.append(name)
                final.append(deepcopy(product))
        if fields['disposition'] == 'VISA_EXEMPT':
            _reviewed_optional_products(final, row.get('products') or [], verdict, sources, route)
        fields['visa_products'] = final
        fields['unpublished_fields'] = sorted(unpublished)
        fields['source_url'] = vproof['source_url']
        proofs['visa_products'] = dict(vproof, note='Product rows carry their own reviewed verdict proofs.')
        errors = vo._field_errors(fields)
        if errors:
            raise PatchRejected(entry['cache_key'] + ': ' + '; '.join(errors))
        # Preview exactly what the store loader will serve, including the
        # exemption and application leftover clean-up, before accepting.
        parsed = vo._parse_rows([{'route': {'nationality': route['passport_nationality'], 'destination': route['destination_country'],
                                            'travel_purpose': route['travel_purpose'],
                                            'travel_document_type': route.get('travel_document_type') or 'ordinary_passport'},
                                  'verified_at': today, 'verified_by': VERIFIED_BY, 'verifier': 'ai',
                                  'source_url': vproof['source_url'], 'note': 'preview', 'fields': fields,
                                  'field_provenance': proofs}], {})
        served_fields = next(iter(parsed.values()))['fields'] if parsed else {}
        merged, _ = vo.merge_verified_fields(deepcopy(layer['raw_guidance']), served_fields, source_url=vproof['source_url'])
        problems = serve_time_invariants(merged)
        if problems:
            raise PatchRejected(entry['cache_key'] + ': the reviewed answer would contradict itself: ' + '; '.join(problems))
        entries.append({'route': {'nationality': route['passport_nationality'], 'destination': route['destination_country'],
                                  'travel_purpose': route['travel_purpose'],
                                  'travel_document_type': route.get('travel_document_type') or 'ordinary_passport'},
                        'verified_at': today, 'verified_by': VERIFIED_BY, 'verifier': 'ai',
                        'source_url': vproof['source_url'],
                        'note': str(verdict['proof'].get('scope_note') or '')[:400],
                        'fields': fields, 'field_provenance': proofs,
                        'review_id': batch['id'], 'cache_key': entry['cache_key']})
        reports.append({'cache_key': entry['cache_key'], 'disposition': fields['disposition'],
                        'products': [p['type'] for p in final], 'unsupported_products': unsupported,
                        'removed_products': removed, 'route_verdict_products': inherited,
                        'unpublished': sorted(unpublished),
                        # Every field or product value the review asserted but
                        # could not prove, with the reason it was not served.
                        'dropped': list(row.get('dropped') or [])})
    overlay = {'schema_version': 1, 'kind': 'reviewed_overlay_conversion', 'review_id': batch['id'],
               'reviewed_at': today, 'status': 'candidate; registered only after preflight under maintenance',
               'contract': 'Every value carries its own literal official-page quote; verdicts name the nationality; '
                           'products bind to the exact current product; baseline layers are hash-pinned.',
               'entries': entries}
    return overlay, reports


if __name__ == '__main__':
    import sys
    batch = json.loads(open(sys.argv[1]).read())
    layers = json.loads(open(sys.argv[2]).read())
    layers = layers.get('layers', layers)
    manifest = build_manifest(batch, layers)
    overlay, reports = convert(manifest, layers)
    json.dump(overlay, open(sys.argv[3], 'w'), ensure_ascii=False, indent=1)
    json.dump({'manifest_routes': [{k: e[k] for k in ('cache_key', 'matches', 'baseline_sha256')} for e in manifest['routes']],
               'rejected': manifest['rejected'], 'reports': reports}, open(sys.argv[4], 'w'), ensure_ascii=False, indent=1)
    if len(sys.argv) > 5:
        json.dump(manifest, open(sys.argv[5], 'w'), ensure_ascii=False)
    print(json.dumps({'entries': len(overlay['entries']), 'rejected': len(manifest['rejected']),
                      'unsupported_products': sum(len(r['unsupported_products']) for r in reports),
                      'dropped_values': sum(len(r['dropped']) for r in reports)}))
