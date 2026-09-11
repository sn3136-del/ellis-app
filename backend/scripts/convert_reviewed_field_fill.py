"""Detached, exact-layer fill of empty required cells on reviewed routes.

Ellis serves product rows that cite a government page and still leave a
required cell empty (validity, permitted stay, entries, fee, documents or the
application method) although the official page states the value. Each fill
here writes one value into one such cell, on one context, with the value's own
destination-government quote, or records a documented absence when the pages
checked do not publish it. A fill never overwrites a value and never touches
the verdict, the product identities, policy intervals, freshness, raw records
or operator entries. The record can only become more complete, so its grade
may rise from Medium to High and may never fall. The output is an additive
reviewed overlay that a release must preflight against the exact six
production layers it was built from.

Every quote is bound to the cell's own subject before it is believed: the
sentence must name the product, must not speak about another nationality or
another entry type, must use a validity word for a validity and a stay word
for a stay, must put the figure beside its own unit, must carry the currency
marker beside the amount, and must be about the government fee rather than a
service charge. A documented absence names the captured pages it checked and
is refused when any of them states a value for the field. Where a gate cannot
decide, it rejects, because a wrong value reaches travellers.
"""
from copy import deepcopy
from datetime import date
import math
import re

from scripts.convert_reviewed_field_corrections import _mask, _note_normalised, _tidy_notes
from scripts.convert_reviewed_general_batch import (NOT_PUBLISHED_CELLS, _EXTRA_ALIASES, _aliases, _check_proof,
                                                    _monetary_text, _norm, _proof_for, _source_table)
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map
from scripts.prepare_reviewed_product_patch import PatchRejected, digest

KIND = 'reviewed_field_fill'
SCOPE = 'reviewed_field_fill'
MANIFEST_KIND = 'field_fill_exact_layers'
VERIFIED_BY = 'Ellis AI official-source field review'
PRODUCT_FIELDS = frozenset({'validity', 'max_stay_days', 'entry', 'fee', 'required_documents'})
ROUTE_FIELDS = frozenset({'permitted_stay', 'permitted_stay_days', 'government_fee', 'application_channel',
                          'required_documents'})
# The channel vocabulary the projection turns into an application method.
CHANNELS = frozenset({'online_portal', 'embassy_or_consulate', 'embassy_designated_agency', 'authorised_agent',
                      'visa_application_centre', 'on_arrival'})
ENTRIES = frozenset({'single', 'double', 'multiple'})
# The channels a served verdict family can carry. An arrival visa is applied
# for at the border and nowhere else, an advance visa is never issued there,
# an electronic authorisation is lodged online, and an exemption has no
# application at all.
CHANNELS_BY_DISPOSITION = {
    'VISA_ON_ARRIVAL': frozenset({'on_arrival'}),
    'VISA_REQUIRED': CHANNELS - {'on_arrival'},
    'ELECTRONIC_AUTHORIZATION_REQUIRED': frozenset({'online_portal', 'authorised_agent'}),
}
# The record cells each fillable field feeds. The general batch names the
# absence cells and the three fields it lacks are added here.
CELLS = dict(NOT_PUBLISHED_CELLS, permitted_stay=('max_stay_duration', 'max_stay_unit'),
             required_documents=('required_documents',), application_channel=('application_method',))
# Columns of the projected records a fill may change: the filled cells, the
# absence markers and the grade, nothing else.
RECORD_COLUMNS_MAY_CHANGE = frozenset({
    'validity_duration', 'validity_unit', 'validity_text', 'max_stay_duration', 'max_stay_unit', 'max_stay_text',
    '_max_stay_representation_reason', 'special_conditions', 'entries', 'visa_fee_amount', 'visa_fee_currency',
    'visa_fee_qualifier', 'required_documents', 'application_method', 'confidence_level', '_unpublished',
    'completeness', 'field_status'})
_EMPTY_FEE = {'amount': None, 'currency': None}
_REVIEWED_PROOF_KEYS = frozenset({'status', 'verifier', 'verified_at', 'scope_note', 'evidence'})
_ABSENCE_PROOF_KEYS = frozenset({'status', 'verifier', 'reason', 'verified_at', 'source_ids'})
# The store trims a route-level proof note to this many characters, so an
# absence note that would be cut is refused instead of losing its page list.
_NOTE_LIMIT = 400
_SENTENCE_RE = re.compile(r'[\n;；]+|(?<=[.!?。！？])\s+')
# Letter boundaries that work for accented Latin words as well as ASCII.
_L = r'(?<![^\W\d_])'
_R = r'(?![^\W\d_])'


def _words(latin, other=''):
    """An alternation of letter-bounded Latin words and bare CJK or Cyrillic
    stems, for vocabulary the official pages of the served destinations use."""
    parts = [_L + '(?:' + latin + ')' + _R] if latin else []
    if other:
        parts.append('(?:' + other + ')')
    return '|'.join(parts)


_VALIDITY_WORDS = re.compile(_words(
    r'valid(?:ity|ité|ez|ade|o|a|os|as|e|es)?|valable|valables|gültig|geçerli|validità|issued for|granted for|'
    r'hiệu lực|berlaku',
    r'有效|有効|유효|действител|срок действия|صالح|صلاحية|มีอายุ|ใช้ได้'), re.I)
_STAY_WORDS = re.compile(_words(
    r'stay(?:s|ing|ed)?|visit(?:s|ing)?|remain(?:s|ing)?|sojourn|séjour(?:ner)?|estancia|estadía|permanecer|'
    r'permanencia|permanência|aufenthalt|soggiorno|tinggal|kalış|lưu trú|ở lại',
    r'停留|逗留|滞在|체류|пребыван|находиться|إقامة|พำนัก'), re.I)
_PROCESSING_WORDS = re.compile(_words(r'process(?:ing|ed)?|turnaround|working|business', r'处理|處理|审理|審理|処理|처리'), re.I)
_UNIT_WORDS = {
    'Day': (r'days?|jours?|días?|dias?|tage?|giorn[oi]|hari|ngày|gün|dni',
            r'日間|日|天|일|дней|дня|день|วัน|يوم|أيام'),
    'Month': (r'months?|mois|mes|meses|monate?|mes[ei]|bulan|tháng|ay',
              r'个月|個月|ヶ月|か月|カ月|箇月|月|개월|месяц(?:а|ев)?|เดือน|شهر|أشهر'),
    'Year': (r'years?|ans?|années?|años?|anos?|jahre?|ann[oi]|tahun|năm|yıl',
             r'年|년|года?|лет|ปี|سنة|سنوات'),
    'Hour': (r'hours?|heures?|horas?|stunden?|ore|jam|giờ|saat',
             r'小时|小時|時間|시간|час(?:а|ов)?|ชั่วโมง|ساعة|ساعات'),
}
_ANY_UNIT_RE = re.compile('|'.join(_words(*_UNIT_WORDS[u]) for u in _UNIT_WORDS), re.I)
_NUMBER_WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine',
                 10: 'ten', 11: 'eleven', 12: 'twelve', 14: 'fourteen', 15: 'fifteen', 20: 'twenty', 21: 'twenty[- ]one',
                 28: 'twenty[- ]eight', 30: 'thirty', 45: 'forty[- ]five', 60: 'sixty', 90: 'ninety',
                 120: 'one hundred (?:and )?twenty', 180: 'one hundred (?:and )?eighty',
                 365: 'three hundred (?:and )?sixty[- ]five'}
_DURATION_RE = re.compile(r'(?<![\d.,])' + _L + r'(?:\d+(?:\.\d+)?|' + '|'.join(_NUMBER_WORDS.values())
                          + r')\s*(?:\(\s*\d+\s*\)\s*)?[-‐–]?\s*(?:calendar\s+|kalender\s+)?(?:' + _ANY_UNIT_RE.pattern + ')', re.I)
# Two figures joined by one of these, with nothing but a unit between, state
# a range or a choice ("3 to 6 months", "1/3/5 years", "30 or 90 days").
_CONNECTOR_RE = re.compile(
    r'^\s*(?:to|or|and|,|,\s*or|,\s*and|/|-|–|—|~|〜|至|到|或|或者|và|hoặc|đến|hingga|sampai|atau|à|ou|bis|oder|до|или|'
    r'veya|ve|a|o|y|e)\s*$', re.I)
_ENTRY_STATEMENTS = {
    'single': (r'single[- ]?entry|single entries|one[- ]entry|一次(?:入境|入出境|有效)?|单次|單次|単次|1回|단수|'
               r'một lần|sekali|entrée unique|entrada única|однократн'),
    'double': (r'double[- ]?entry|double entries|two[- ]entr(?:y|ies)|两次|兩次|2回|двукратн|hai lần|dua kali|'
               r'double entrée|doble entrada'),
    'multiple': (r'multiple[- ]?entr(?:y|ies)|multi[- ]?entry|多次|数次|數次|複数回|복수|nhiều lần|beberapa kali|'
                 r'entrées multiples|entradas múltiples|многократн'),
}
_ENTRY_QUALIFIERS = {'single': r'\bsingle\b', 'double': r'\bdouble\b', 'multiple': r'\bmultiple\b|\bmulti\b'}
# Words that make a sentence about the fee the government collects, and the
# words that mark a charge somebody else collects. A sentence carrying the
# second kind is never the government fee, whatever else it says.
_FEE_WORDS = re.compile(_words(r'fees?|charges?|costs?|prices?|tariffs?|duty|tarif|frais|tasa|tarifa|lệ phí|phí|biaya',
                               r'费|費|手数料|料金|수수료|сбор|пошлин|стоимост'), re.I)
_FEE_SUBJECT_WORDS = re.compile(_words(r'consular|government|state|official|e-?visas?|visas?|visto|visado|visé|konsuler|'
                                       r'thị thực|lãnh sự',
                                       r'领事|签证|簽證|査証|ビザ|비자|영사|консульск|виз'), re.I)
_SERVICE_WORDS = re.compile(_words(
    r'service (?:fee|charge)s?|services? charges?|agency|agents?(?:\'s)? fees?|courier|handling|logistics?|delivery|'
    r'postage|postal|vfs|tls ?contact|bls international|visa (?:application )?cent(?:re|er)s?|application cent(?:re|er)s?|'
    r'convenience|bank (?:charge|fee)s?|card (?:fee|surcharge)s?|surcharge|insurance|premium|expedit(?:ed|e)|express|'
    r'priority|urgent|optional|phí dịch vụ|biaya layanan|frais de service',
    r'服务费|服務費|代办|代辦|手续费|手續費|サービス料|대행|сервисн'), re.I)
# Currency markers written on official pages, longest first, and the codes
# each can mean. A set with several codes is ambiguous on its own and needs
# a qualifier in the sentence or the destination's own currency to resolve.
_DOLLARS = frozenset({'USD', 'AUD', 'CAD', 'SGD', 'HKD', 'NZD', 'TWD', 'MXN', 'MOP', 'BRL'})
_CURRENCY_MARKERS = [
    ('australian dollars', {'AUD'}), ('australian dollar', {'AUD'}), ('us dollars', {'USD'}), ('us dollar', {'USD'}),
    ('u.s. dollars', {'USD'}), ('u.s. dollar', {'USD'}), ('american dollars', {'USD'}), ('hong kong dollars', {'HKD'}),
    ('hong kong dollar', {'HKD'}), ('singapore dollars', {'SGD'}), ('singapore dollar', {'SGD'}),
    ('canadian dollars', {'CAD'}), ('canadian dollar', {'CAD'}), ('new zealand dollars', {'NZD'}),
    ('new zealand dollar', {'NZD'}), ('taiwan dollars', {'TWD'}), ('taiwan dollar', {'TWD'}),
    ('mexican pesos', {'MXN'}), ('philippine pesos', {'PHP'}), ('japanese yen', {'JPY'}), ('chinese yuan', {'CNY'}),
    ('pounds sterling', {'GBP'}), ('pound sterling', {'GBP'}), ('egyptian pounds', {'EGP'}),
    ('A$', {'AUD'}), ('AU$', {'AUD'}), ('US$', {'USD'}), ('U.S.$', {'USD'}), ('HK$', {'HKD'}), ('S$', {'SGD'}),
    ('SG$', {'SGD'}), ('NZ$', {'NZD'}), ('C$', {'CAD'}), ('CA$', {'CAD'}), ('CAN$', {'CAD'}), ('NT$', {'TWD'}),
    ('MX$', {'MXN'}), ('MOP$', {'MOP'}), ('R$', {'BRL'}), ('E£', {'EGP'}),
    ('AUD', {'AUD'}), ('USD', {'USD'}), ('HKD', {'HKD'}), ('SGD', {'SGD'}), ('NZD', {'NZD'}), ('CAD', {'CAD'}),
    ('TWD', {'TWD'}), ('MXN', {'MXN'}), ('MOP', {'MOP'}), ('BRL', {'BRL'}), ('EUR', {'EUR'}), ('GBP', {'GBP'}),
    ('JPY', {'JPY'}), ('CNY', {'CNY'}), ('RMB', {'CNY'}), ('KRW', {'KRW'}), ('INR', {'INR'}), ('THB', {'THB'}),
    ('IDR', {'IDR'}), ('MYR', {'MYR'}), ('RUB', {'RUB'}), ('VND', {'VND'}), ('PHP', {'PHP'}), ('CHF', {'CHF'}),
    ('AED', {'AED'}), ('SAR', {'SAR'}), ('TRY', {'TRY'}), ('EGP', {'EGP'}),
    ('dollars', _DOLLARS), ('dollar', _DOLLARS), ('euros', {'EUR'}), ('euro', {'EUR'}), ('yen', {'JPY'}),
    ('yuan', {'CNY'}), ('renminbi', {'CNY'}), ('人民币', {'CNY'}), ('won', {'KRW'}), ('rupees', {'INR'}),
    ('rupee', {'INR'}), ('Rs.', {'INR'}), ('Rs', {'INR'}), ('rupiah', {'IDR'}), ('Rp.', {'IDR'}), ('Rp', {'IDR'}),
    ('baht', {'THB'}), ('ringgit', {'MYR'}), ('RM', {'MYR'}), ('rubles', {'RUB'}), ('roubles', {'RUB'}),
    ('ruble', {'RUB'}), ('rouble', {'RUB'}), ('руб.', {'RUB'}), ('руб', {'RUB'}), ('рублей', {'RUB'}),
    ('dong', {'VND'}), ('VNĐ', {'VND'}), ('pesos', {'PHP', 'MXN'}), ('peso', {'PHP', 'MXN'}),
    ('pounds', {'GBP', 'EGP'}), ('pound', {'GBP', 'EGP'}), ('francs', {'CHF'}), ('franc', {'CHF'}),
    ('dirhams', {'AED'}), ('dirham', {'AED'}), ('riyals', {'SAR'}), ('riyal', {'SAR'}), ('lira', {'TRY'}),
    ('港币', {'HKD'}), ('港幣', {'HKD'}), ('港元', {'HKD'}), ('新台币', {'TWD'}), ('新臺幣', {'TWD'}),
    ('$', _DOLLARS), ('€', {'EUR'}), ('£', {'GBP', 'EGP'}), ('¥', {'JPY', 'CNY'}), ('円', {'JPY'}),
    ('元', {'CNY', 'TWD', 'HKD', 'MOP'}), ('₩', {'KRW'}), ('원', {'KRW'}), ('₹', {'INR'}), ('฿', {'THB'}),
    ('₽', {'RUB'}), ('₫', {'VND'}), ('₱', {'PHP'}), ('₺', {'TRY'}),
]
# The currency a destination prices its own visas in, used only to read an
# ambiguous marker such as a bare dollar sign on that destination's page.
_HOME_CURRENCY = {'USA': 'USD', 'AUS': 'AUD', 'CAN': 'CAD', 'SGP': 'SGD', 'HKG': 'HKD', 'NZL': 'NZD', 'TWN': 'TWD',
                  'MEX': 'MXN', 'BRA': 'BRL', 'MAC': 'MOP', 'JPN': 'JPY', 'CHN': 'CNY', 'KOR': 'KRW', 'IND': 'INR',
                  'THA': 'THB', 'IDN': 'IDR', 'MYS': 'MYR', 'RUS': 'RUB', 'VNM': 'VND', 'PHL': 'PHP', 'GBR': 'GBP',
                  'FRA': 'EUR', 'ESP': 'EUR', 'DEU': 'EUR', 'ITA': 'EUR', 'TUR': 'TRY', 'ARE': 'AED', 'SAU': 'SAR',
                  'EGY': 'EGP', 'CHE': 'CHF'}


def _marker_pattern(literal):
    text = re.escape(literal).replace(r'\ ', r'\s+')
    return (_L if literal[0].isalpha() else '') + text + (_R if literal[-1].isalpha() else '')


_MARKER_TABLE = [(re.compile(_marker_pattern(m), re.I), codes) for m, codes in _CURRENCY_MARKERS]
_MONEY_RE = re.compile(r'(?:' + '|'.join(_marker_pattern(m) for m, _ in _CURRENCY_MARKERS) + r')\s*\d[\d,.]*|'
                       r'\d[\d,.]*\s*(?:' + '|'.join(_marker_pattern(m) for m, _ in _CURRENCY_MARKERS) + ')', re.I)
_DOCUMENT_WORDS = re.compile(
    r'passport|photo|form|application|ticket|insurance|proof|invitation|itinerary|bank|booking|letter|certificate|'
    r'copy|document|voucher|confirmation|паспорт|фото|анкет|страхов|билет|приглашен|документ|справк|подтвержден|'
    r'ваучер|护照|護照|照片|申请|申請|文件|パスポート|旅券|写真|申請書|書類|여권|사진|신청서|서류|hộ chiếu|ảnh|'
    r'passeport|pasaporte|formulaire|formulario|paspor|surat|dokumen|foto', re.I)
_DOCUMENT_CUES = re.compile(_words(
    r'documents?(?: required)?|required documents?|supporting documents?|checklist|submit|provide|present|enclose|'
    r'attach|accompanied by|together with|pièces|documentos|dokumen|hồ sơ|giấy tờ',
    r'必要书类|必要書類|所需材料|所需文件|提交|提出|서류|подать|документы|申請書類'), re.I)
_APPLY_WORDS = re.compile(_words(r'apply|applying|application|applications|lodge|lodged|submit|submitted|demande|'
                                 r'solicitud|nộp|xin|mengajukan|permohonan',
                                 r'申请|申請|申込|申し込|신청|подать|заявлен|оформ'), re.I)
_CHANNEL_WORDS = re.compile(_words(
    r'online|website|portal|embassy|consulate|mission|visa application cent(?:re|er)|agents?|agency|agencies|'
    r'on arrival|upon arrival|at the border|port of entry|en ligne|ambassade|consulat|en línea|embajada|consulado|'
    r'trực tuyến|đại sứ quán|lãnh sự quán|kedutaan|konsulat',
    r'在线|网上|線上|电子|電子|使馆|使館|领事馆|領事館|大使館|オンライン|온라인|대사관|영사관|онлайн|посольств|консульств|'
    r'по прибытии|落地|到着'), re.I)
_PASSPORT_WORDS = re.compile(_words(r'passports?|travel documents?|passeport|pasaporte|paspor|hộ chiếu',
                                    r'护照|護照|パスポート|旅券|여권|паспорт'), re.I)
_VISA_WORDS = re.compile(_words(r'e-?visas?|visas?|permits?|authori[sz]ations?|visto|visado|thị thực',
                                r'签证|簽證|査証|ビザ|비자|виз|许可|許可'), re.I)


def _sentences(text):
    return [s for s in _SENTENCE_RE.split(str(text or '')) if s and s.strip()]


def _empty(value):
    if isinstance(value, dict) and 'amount' in value:
        return value.get('amount') is None
    return value in (None, '', [], {})


def _label(fill):
    if fill.get('target') == 'product':
        return 'product %s %s' % (fill.get('product_type'), fill.get('field'))
    return 'route %s' % fill.get('field')


def _wants_absence(proof):
    return isinstance(proof, dict) and proof.get('status') == 'not_published'


def _product_named(products, name, label):
    hits = [p for p in (products if isinstance(products, list) else []) if isinstance(p, dict) and p.get('type') == name]
    if not hits:
        raise PatchRejected('%s: no served product is named %s' % (label, name))
    if len(hits) > 1:
        raise PatchRejected('%s: more than one served product is named %s' % (label, name))
    return hits[0]


def _product_index(products, name, label):
    product = _product_named(products, name, label)
    return next(i for i, p in enumerate(products) if p is product)


def _checked_proof(proof, sources, route, field, value, label, product=None):
    """The general batch's proof check, its reasons re-labelled with the fill
    (it names the field alone, which is ambiguous across products)."""
    try:
        return _check_proof(proof, sources, route, field, value, product=product)
    except PatchRejected as exc:
        message = str(exc)
        if message.startswith(field + ': '):
            message = message[len(field) + 2:]
        raise PatchRejected(label + ': ' + message) from exc


def _review_date(proof, label):
    try:
        when = date.fromisoformat(str(proof.get('verified_at')))
    except (TypeError, ValueError) as exc:
        raise PatchRejected(label + ': invalid review date') from exc
    if when > date.today():
        raise PatchRejected(label + ': review date is in the future')
    return when.isoformat()


# Value detectors for documented absences. Each reads a captured page the
# absence names and answers whether that page states a value for the field.
# A field without a detector cannot be declared absent.
def _figure_re(n, unit):
    """The figure n written beside a unit word of this unit, as official
    pages write it: 30 days, thirty (30) days, 30-day, 30日, 6 mois."""
    forms = [re.escape(str(n))]
    whole = float(n) == int(n)
    if whole and str(int(n)) != str(n):
        forms.append(re.escape(str(int(n))))
    word = _NUMBER_WORDS.get(int(n)) if whole else None
    if word:
        digit = re.escape(str(int(n)))
        forms += [word + r'\s*\(\s*' + digit + r'\s*\)', digit + r'\s*\(\s*' + word + r'\s*\)', word]
    units = _words(*_UNIT_WORDS[unit])
    return re.compile(r'(?<![\d.,])' + _L + '(?:' + '|'.join(forms) + r')\s*(?:\(\s*\d+\s*\)\s*)?[-‐–]?\s*'
                      r'(?:calendar\s+|kalender\s+)?(?:' + units + ')', re.I)


def _bound(sentence, n, unit, own, other):
    """Whether some occurrence of the figure has an own word (validity, stay)
    as its nearest subject word before it, with none of the other kind in
    between, or directly after it ("30 days' stay", "30-day validity"). A
    subject word further along the sentence belongs to the next figure."""
    for m in _figure_re(n, unit).finditer(sentence):
        before = sentence[max(0, m.start() - 90):m.start()]
        owned = list(own.finditer(before))
        if owned and not other.search(before[owned[-1].end():]):
            return True
        after = sentence[m.end():m.end() + 16]
        owned = own.search(after)
        if owned and re.fullmatch(r"[\s'’]*(?:of|de|di|d')?\s*", after[:owned.start()]):
            return True
    return False


def _range_or_choice(sentence, n):
    """True when the figure n is one bound of a range or one option of a
    choice in this sentence, which never states one value."""
    numbers = list(re.finditer(r'(?<![\d.,])\d+(?:\.\d+)?(?![\d.,]\d)', sentence))
    for a, b in zip(numbers, numbers[1:]):
        gap = _ANY_UNIT_RE.sub(' ', sentence[a.end():b.start()])
        gap = re.sub(r'[()]', ' ', gap)
        if _CONNECTOR_RE.match(gap) and float(n) in (float(a.group()), float(b.group())):
            return True
    return False


def _entry_types(text, *, qualifiers=False):
    found = {kind for kind, pattern in _ENTRY_STATEMENTS.items() if re.search(pattern, text, re.I)}
    if qualifiers:
        found |= {kind for kind, pattern in _ENTRY_QUALIFIERS.items() if re.search(pattern, text, re.I)}
    return found


def _known_entries(product, products):
    """The entry types this subject states: the product's own cell or its
    name, or every served product's for a route-level fill."""
    known = set()
    for p in ([product] if product is not None else [q for q in (products or []) if isinstance(q, dict)]):
        if p.get('entry') in ENTRIES:
            known.add(p['entry'])
        else:
            known |= _entry_types(str(p.get('type') or ''), qualifiers=True)
    return known


def _known_nationalities():
    from app.visa_snapshot.evidence_validator import _NATIONALITY_NAMES
    return sorted(set(_NATIONALITY_NAMES) | set(_EXTRA_ALIASES))


def _alias_pattern(alias):
    """Letter boundaries for scripts that separate words with spaces. CJK and
    Thai text runs without spaces, so those aliases match bare."""
    if re.search(r'[A-Za-zÀ-ɏЀ-ӿ]', alias):
        return _L + re.escape(alias) + _R
    return re.escape(alias)


def _names_nationality(normalised, code):
    """Whether the sentence names this nationality as a subject. Short ASCII
    aliases ("us", "anh") are too common as ordinary words to count, and a
    country after "to", "in" or "enter" is a place, not a passport."""
    for alias in _aliases(code):
        if len(alias) < 4 and alias.isascii():
            continue
        for m in re.finditer(_alias_pattern(alias), normalised):
            before = normalised[max(0, m.start() - 35):m.start()]
            if re.search(r'(?:travel(?:l?ing)?|travell?ers?|enter(?:ing)?|visits?|flights?|entry)\s+(?:to\s+|into\s+)?$|'
                         r'(?<![^\W\d_])(?:to|into|in)\s+(?:the\s+)?$', before):
                continue
            return True
    return False


def _foreign_subject(sentence, route):
    """The other nationalities a sentence is about, when it is not also about
    this route's nationality. The destination is a place, never a subject."""
    normalised = _norm(sentence)
    named = {code for code in _known_nationalities() if _names_nationality(normalised, code)}
    named.discard(route['destination_country'])
    if named and route['passport_nationality'] not in named:
        return sorted(named)
    return []


def _amount_re(amount):
    if float(amount) == int(amount):
        n = int(amount)
        forms = {str(n)}
        if n >= 1000:
            grouped = f'{n:,}'
            forms |= {grouped, grouped.replace(',', '.'), grouped.replace(',', ' ')}
        forms = {re.escape(f) for f in forms}
        forms |= {f + r'[.,]00' for f in list(forms)}
    else:
        text = repr(float(amount))
        forms = {re.escape(text), re.escape(text.replace('.', ','))}
    return re.compile(r'(?<![\d.,])(?:' + '|'.join(sorted(forms, key=len, reverse=True)) + r')(?!\d|[.,]\d)')


def _adjacent_marker(before, after):
    """The longest currency marker touching the amount, and its codes."""
    best = None
    for pattern, codes in _MARKER_TABLE:
        hit = re.search(r'(?:' + pattern.pattern + r')\s*$', before, re.I)
        if hit and (best is None or len(hit.group()) > best[0]):
            best = (len(hit.group()), codes)
        hit = re.match(r'\s*(?:' + pattern.pattern + ')', after, re.I)
        if hit and (best is None or len(hit.group()) > best[0]):
            best = (len(hit.group()), codes)
    return best[1] if best else None


def _currency_beside(sentence, amount, code, destination):
    """The reason this sentence does not price the amount in the value's
    currency, or None when the marker beside the amount is that currency.

    The marker must touch the amount. An ambiguous marker (a bare dollar
    sign, a bare pound sign) resolves only through a qualifier in the same
    sentence or the destination's own currency, never through the value.
    """
    seen = []
    for m in _amount_re(amount).finditer(sentence):
        before = sentence[max(0, m.start() - 24):m.start()]
        after = sentence[m.end():m.end() + 32]
        codes = _adjacent_marker(before, after)
        if codes is None:
            seen.append('no currency marker stands beside the amount')
            continue
        if len(codes) > 1:
            qualified = set()
            for pattern, named in _MARKER_TABLE:
                if len(named) == 1 and pattern.search(sentence) and named <= codes:
                    qualified |= named
            home = _HOME_CURRENCY.get(destination)
            if len(qualified) == 1:
                codes = qualified
            elif home in codes and not qualified:
                codes = {home}
            else:
                seen.append('the currency marker beside the amount is ambiguous and the sentence does not name it')
                continue
        if code in codes:
            return None
        seen.append('the quote prices in %s, not %s' % ('/'.join(sorted(codes)), code))
    return seen[0] if seen else 'the amount is not in this sentence'


def _fee_sentence_problem(sentence):
    if _SERVICE_WORDS.search(sentence):
        return 'the sentence prices a service, agency, centre or other non-government charge'
    if not (_FEE_WORDS.search(sentence) and _FEE_SUBJECT_WORDS.search(sentence)):
        return 'the sentence does not name a consular, government or visa fee'
    return None


def _states_value(field, text):
    """The first sentence of a captured page that states a value for this
    field, or None. Conservative in the rejecting direction: any stated
    value, for any product, refuses an absence claimed over this page."""
    for s in _sentences(text):
        if field in ('fee', 'government_fee'):
            if _FEE_WORDS.search(s) and _MONEY_RE.search(s):
                return s
        elif field == 'validity':
            if (_VALIDITY_WORDS.search(s) and _DURATION_RE.search(s)
                    and not (_PASSPORT_WORDS.search(s) and not _VISA_WORDS.search(s))):
                return s
        elif field in ('max_stay_days', 'permitted_stay_days', 'permitted_stay'):
            if _STAY_WORDS.search(s) and _DURATION_RE.search(s):
                return s
        elif field == 'entry':
            if _entry_types(s):
                return s
        elif field == 'required_documents':
            if _DOCUMENT_CUES.search(s) and _DOCUMENT_WORDS.search(s):
                return s
        elif field == 'application_channel':
            if _APPLY_WORDS.search(s) and _CHANNEL_WORDS.search(s):
                return s
        else:
            raise PatchRejected('no value detector exists for ' + str(field) + ', so it cannot be declared absent')
    return None


def _check_absence(proof, sources, route, field, value, label, product=None):
    """A documented absence names the captured destination pages it checked,
    carries its review date and a reason, and is refused when any named
    page states a value for the field."""
    if set(proof) - _ABSENCE_PROOF_KEYS:
        raise PatchRejected(label + ': extra instruction on the absence proof')
    if value is not None and value != _EMPTY_FEE:
        raise PatchRejected(label + ': a documented absence carries no value')
    # The general batch's own absence rule: an empty value and a reason.
    _checked_proof(proof, sources, route, field, value, label)
    _review_date(proof, label)
    from app.visa_snapshot.evidence_validator import jurisdiction_matches, quote_in_text
    ids = proof.get('source_ids')
    if (not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
            or any(not isinstance(i, str) or i not in sources for i in ids)):
        raise PatchRejected(label + ': the absence must list the captured page ids it checked')
    pages = [sources[i] for i in ids]
    if any(not jurisdiction_matches(page['url'], route['destination_country']) for page in pages):
        raise PatchRejected(label + ': every page an absence checked must be a captured destination-government page')
    if product is not None and not any(quote_in_text(product['type'], page['text']) for page in pages):
        raise PatchRejected(label + ': none of the pages checked mentions the product ' + str(product['type']))
    for page in pages:
        stated = _states_value(field, page['text'])
        if stated:
            raise PatchRejected('%s: the absence names %s, which states a value: "%s"' % (label, page['url'], stated.strip()[:160]))
    if len(_absence_reason(proof, sources)) > _NOTE_LIMIT:
        raise PatchRejected(label + ': the absence reason and its page list exceed the %d characters the store keeps' % _NOTE_LIMIT)


def _absence_reason(proof, sources):
    """The stored reason: the reviewer's words, the review date and the
    pages checked, so a stored absence says what was read and when."""
    urls = [sources[i]['url'] for i in proof['source_ids']]
    reason = str(proof.get('reason') or '').strip()
    return '%s Checked on %s: %s.' % (reason, proof['verified_at'], ', '.join(urls))


def _sentence_supports(field, value, sentence):
    from app.visa_snapshot.evidence_validator import field_value_supported
    if field in ('fee', 'government_fee'):
        return field_value_supported(field, value, _monetary_text(sentence, value['currency']))
    if field == 'entry':
        return bool(re.search(_ENTRY_STATEMENTS[value], sentence, re.I))
    if field == 'application_channel':
        legacy = {'online_portal': 'online', 'embassy_or_consulate': 'embassy', 'embassy_designated_agency': 'authorised_agent',
                  'authorised_agent': 'authorised_agent', 'on_arrival': 'on_arrival'}.get(value, value)
        return field_value_supported('application_channel', legacy, sentence)
    return field_value_supported(field, value, sentence)


def _sentence_problem(field, value, sentence, route, product, products):
    """Why this supporting sentence cannot prove the value for this cell, or
    None when every gate passes."""
    from app.visa_snapshot.tstation import _num_unit, _validity_num_unit
    foreign = _foreign_subject(sentence, route)
    if foreign:
        return 'the sentence is about %s, not %s' % ('/'.join(foreign), route['passport_nationality'])
    if field != 'entry':
        qualified = _entry_types(sentence, qualifiers=True)
        if qualified:
            known = _known_entries(product, products)
            if not known:
                return 'the sentence is qualified by a %s entry type the subject does not state' % '/'.join(sorted(qualified))
            if (product is not None and not known <= qualified) or (product is None and not (known & qualified)):
                return 'the sentence is qualified by a %s entry, not the subject\'s %s' % (
                    '/'.join(sorted(qualified)), '/'.join(sorted(known)))
    if field == 'validity':
        n, unit = _validity_num_unit(value)
        if _range_or_choice(sentence, n):
            return 'the sentence states a range or a choice of validities, not this one value'
        if not _bound(sentence, n, unit, _VALIDITY_WORDS, _STAY_WORDS):
            return 'the figure is not bound to a validity word in the sentence (a stay is not a validity)'
    elif field in ('max_stay_days', 'permitted_stay_days'):
        if not _figure_re(value, 'Day').search(sentence):
            return 'the figure does not stand beside a day word in the sentence (months or years are never converted)'
        if _range_or_choice(sentence, value):
            return 'the sentence states a range or a choice of stays, not this one value'
        if not _bound(sentence, value, 'Day', _STAY_WORDS, _VALIDITY_WORDS):
            return 'the figure is not bound to a stay word in the sentence (a validity is not a stay)'
    elif field == 'permitted_stay':
        n, unit = _num_unit(value)
        if n:
            if not _figure_re(n, unit).search(sentence):
                return 'the stay figure does not stand beside its unit word in the sentence'
            if _range_or_choice(sentence, n):
                return 'the sentence states a range or a choice of stays, not this one value'
            if not _bound(sentence, n, unit, _STAY_WORDS, _VALIDITY_WORDS):
                return 'the figure is not bound to a stay word in the sentence (a validity is not a stay)'
        elif _VALIDITY_WORDS.search(sentence):
            return 'the sentence states a validity, and the stay wording cannot be told apart from it'
    elif field in ('fee', 'government_fee'):
        problem = _fee_sentence_problem(sentence) or _currency_beside(sentence, value['amount'], value['currency'],
                                                                      route['destination_country'])
        if problem:
            return problem
    elif field == 'entry':
        stated = _entry_types(sentence)
        if stated != {value}:
            return 'the sentence states %s entries, not only %s' % ('/'.join(sorted(stated)) or 'no', value)
    return None


def _check_fill_binding(field, value, proof, route, merged, product, label):
    """Bind the quote to the cell's own subject. The general batch already
    proved the quote is literal and the figure occurs somewhere in it. This
    proves it is this product's, this nationality's and this field's value."""
    from app.visa_snapshot.evidence_validator import field_value_supported, quote_in_text
    passages = '\n'.join(item['quote'] for item in proof['evidence'])
    products = merged.get('visa_products') if isinstance(merged.get('visa_products'), list) else []
    product_type = product.get('type') if product is not None else None
    if field == 'application_channel':
        allowed = CHANNELS_BY_DISPOSITION.get(merged.get('disposition'), frozenset())
        if value not in allowed:
            raise PatchRejected('%s: the channel %s contradicts the served %s verdict' % (
                label, value, merged.get('disposition') or 'unknown'))
    if product is not None:
        bound = {'type': product_type, field: value}
        text = _monetary_text(passages, value['currency']) if field == 'fee' else passages
        if not field_value_supported('visa_products', bound, text):
            raise PatchRejected(label + ': no quoted sentence names the product and states this value')
    items = value if field == 'required_documents' else [value]
    sentences = [s for s in _sentences(passages) if product_type is None or quote_in_text(product_type, s)]
    for item in items:
        candidates = [s for s in sentences if _sentence_supports(field, item, s)]
        if not candidates:
            what = 'the document "%s"' % item if field == 'required_documents' else 'this value'
            raise PatchRejected('%s: no quoted sentence %sstates %s' % (
                label, 'names the product and ' if product_type else '', what))
        problems = [_sentence_problem(field, item, s, route, product, products) for s in candidates]
        if all(problems):
            raise PatchRejected(label + ': ' + problems[0])


def _check_fill_value(field, value, proof, label):
    """Shape and projection checks the general batch does not make. The quote
    and figure checks already ran inside _check_proof, the binding runs next."""
    from app.visa_snapshot.verified_overrides import _reads_like_review
    passages = '\n'.join(item['quote'] for item in proof['evidence'])
    if field == 'validity':
        from app.visa_snapshot.tstation import _validity_num_unit
        n, unit = _validity_num_unit(value) if isinstance(value, str) else (None, None)
        if not n or unit not in _UNIT_WORDS or len(value.strip()) > 60:
            raise PatchRejected(label + ': the value must be one unambiguous duration such as "30 days"')
    elif field in ('max_stay_days', 'permitted_stay_days'):
        if type(value) is not int or value <= 0:
            raise PatchRejected(label + ': a positive whole number of days is required')
    elif field == 'entry':
        if value not in ENTRIES:
            raise PatchRejected(label + ': expected single, double or multiple')
    elif field in ('fee', 'government_fee'):
        if (not isinstance(value, dict) or set(value) != {'amount', 'currency'} or isinstance(value['amount'], bool)
                or not isinstance(value['amount'], (int, float)) or not math.isfinite(value['amount'])
                or value['amount'] <= 0 or not isinstance(value['currency'], str)
                or not re.fullmatch(r'[A-Z]{3}', value['currency'])):
            raise PatchRejected(label + ': expected a positive amount with an ISO 4217 currency code')
    elif field == 'required_documents':
        if (not isinstance(value, list) or not value or len(value) > 25
                or any(not isinstance(d, str) or not d.strip() or len(d) > 160 for d in value)):
            raise PatchRejected(label + ': expected a short list of document names')
        if _reads_like_review(value):
            raise PatchRejected(label + ': a document name reads like the reviewer talking')
        if not _DOCUMENT_WORDS.search(passages):
            raise PatchRejected(label + ': the quotes do not describe documents')
    elif field == 'permitted_stay':
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise PatchRejected(label + ': expected a short stay statement')
        if _reads_like_review(value):
            raise PatchRejected(label + ': the stay statement reads like the reviewer talking')
        digits = re.findall(r'\d+', value)
        if digits and not all(d in passages for d in digits):
            raise PatchRejected(label + ': the stay figure is not in its evidence')
    elif field == 'application_channel':
        if value not in CHANNELS:
            raise PatchRejected(label + ': the channel is outside the projection vocabulary')


def _prior(layer):
    from app.visa_snapshot import verified_overrides as vo
    route = layer['route']
    doc = route.get('travel_document_type') or 'ordinary_passport'
    identity = vo._key(route['passport_nationality'], route['destination_country'], route['travel_purpose'], doc)
    prior = vo._parse_rows(layer['seed_entries'], {}).get(identity)
    if not prior:
        raise PatchRejected('Existing seed ownership missing: ' + str(layer.get('cache_key')))
    return identity, prior


def _validate_fill(fill, layer, sources, prior):
    """Every check one fill must pass against its current layer. Returns
    'fill' for a quoted value and 'absence' for a documented absence."""
    if not isinstance(fill, dict):
        raise PatchRejected('Malformed fill')
    route, merged = layer['route'], layer['merged_guidance']
    label = _label(fill)
    target = fill.get('target')
    if target == 'product':
        expected_keys, allowed = {'target', 'product_type', 'field', 'value', 'proof'}, PRODUCT_FIELDS
    elif target == 'route':
        expected_keys, allowed = {'target', 'field', 'value', 'proof'}, ROUTE_FIELDS
    else:
        raise PatchRejected('A fill targets a route or a product')
    if set(fill) != expected_keys:
        raise PatchRejected(label + ': extra instruction on a fill')
    field = fill['field']
    if field not in allowed:
        raise PatchRejected(label + ': only empty required cells may be filled')
    product = None
    if target == 'product':
        if 'visa_products' not in prior['fields'] or digest(prior['fields']['visa_products']) != digest(merged.get('visa_products')):
            raise PatchRejected(label + ': the served product table is not owned by the reviewed seed, product fills need a product review')
        product = _product_named(merged.get('visa_products'), fill['product_type'], label)
        current = product.get(field)
    else:
        current = merged.get(field)
    if not _empty(current):
        raise PatchRejected(label + ': the cell already holds a value')
    proof = fill['proof']
    if not isinstance(proof, dict) or proof.get('status') not in ('reviewed', 'not_published'):
        raise PatchRejected(label + ': a fill is a quoted value or a documented absence, nothing else')
    owner = product if product is not None else merged
    if _wants_absence(proof):
        _check_absence(proof, sources, route, field, fill['value'], label, product=product)
        if set(CELLS[field]) <= set(owner.get('unpublished_fields') or []):
            raise PatchRejected(label + ': already documented as not published')
        return 'absence'
    if set(proof) - _REVIEWED_PROOF_KEYS:
        raise PatchRejected(label + ': extra instruction on the proof')
    if _checked_proof(deepcopy(proof), sources, route, field, fill['value'], label, product=product) is None:
        raise PatchRejected(label + ': a fill needs a reviewed proof')
    _review_date(proof, label)
    _check_fill_value(field, fill['value'], proof, label)
    _check_fill_binding(field, fill['value'], proof, route, merged, product, label)
    return 'fill'


def _route_checks(row, layer):
    """The context-level gates: exact shape, exact six-layer baseline, no
    operator authorship, existing seed ownership."""
    if not isinstance(row, dict) or set(row) != {'cache_key', 'route', 'baseline_sha256', 'fills'}:
        raise PatchRejected('Extra instruction')
    if any(k not in layer for k in BASELINE_KEYS) or layer['operator_entries']:
        raise PatchRejected('Operator-authored or incomplete layer')
    if digest(row['route']) != digest(layer['route']):
        raise PatchRejected('Route identity changed')
    if row['baseline_sha256'] != {k: digest(layer[k]) for k in BASELINE_KEYS}:
        raise PatchRejected('Exact six-layer baseline changed')
    if not isinstance(row['fills'], list) or not row['fills']:
        raise PatchRejected('Each context needs at least one fill')
    return _prior(layer)[1]


def _fill_key(fill):
    return (fill.get('target'), fill.get('product_type'), fill.get('field')) if isinstance(fill, dict) else None


def _validate_route(row, layer, sources):
    prior = _route_checks(row, layer)
    keys = [_fill_key(f) for f in row['fills']]
    if len(set(keys)) != len(keys):
        raise PatchRejected('Duplicate fill on one cell')
    return [_validate_fill(f, layer, sources, prior) for f in row['fills']]


def _check_spec_shape(spec):
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'kind', 'id', 'sources', 'routes'} or
            type(spec['schema_version']) is not int or spec['schema_version'] != 1 or
            spec['kind'] != KIND or not isinstance(spec['id'], str) or not spec['id'].strip()):
        raise PatchRejected('Unexpected field-fill contract')


def validate(spec, layers):
    _check_spec_shape(spec)
    sources = _source_table(spec)
    rows = spec['routes']
    if not isinstance(rows, list) or not rows or len({r.get('cache_key') for r in rows if isinstance(r, dict)}) != len(rows):
        raise PatchRejected('Missing or duplicate fill routes')
    current = _current_map(layers, {r['cache_key'] for r in rows})
    if len(layers) != len(rows):
        raise PatchRejected('Exactly the filled contexts must be supplied')
    for row in rows:
        _validate_route(row, current[row['cache_key']], sources)
    return current


def build_manifest(spec, layers):
    current = validate(spec, layers)
    return dict(schema_version=1, kind=MANIFEST_KIND, specification=deepcopy(spec),
                routes=[dict(cache_key=r['cache_key'], baseline={f: deepcopy(current[r['cache_key']][f]) for f in BASELINE_KEYS})
                        for r in sorted(spec['routes'], key=lambda r: r['cache_key'])])


def _mask_product(product, fields):
    """A product without the cells a fill may write, their proofs and its
    absence markers, so everything else can be compared exactly."""
    out = deepcopy(product)
    for field in fields:
        out.pop(field, None)
    proofs = out.get('field_provenance')
    if isinstance(proofs, dict):
        for field in fields:
            proofs.pop(field, None)
        if not proofs:
            out.pop('field_provenance')
    elif proofs is None:
        out.pop('field_provenance', None)
    out.pop('unpublished_fields', None)
    return out


def _apply_fills(out, fills, route, sources):
    """Write the fills into the seed entry copy. Returns the applied fills
    and the route-level fields whose value changed."""
    from app.visa_snapshot import verified_overrides as vo
    fields, proofs = out['fields'], out['field_provenance']
    applied, touched = [], set()
    for fill in fills:
        field, absence, label = fill['field'], _wants_absence(fill['proof']), _label(fill)
        record = dict(target=fill['target'], product_type=fill.get('product_type'), product_index=None, field=field,
                      kind='absence' if absence else 'fill', value=deepcopy(fill['value']), cells=list(CELLS[field]))
        if absence:
            reason = _absence_reason(fill['proof'], sources)
            record['checked_source_urls'] = [sources[i]['url'] for i in fill['proof']['source_ids']]
        if fill['target'] == 'product':
            index = _product_index(fields['visa_products'], fill['product_type'], label)
            product = fields['visa_products'][index]
            record['product_index'] = index
            unpublished = set(product.get('unpublished_fields') or [])
            if absence:
                product[field] = deepcopy(_EMPTY_FEE) if field == 'fee' else None
                unpublished.update(CELLS[field])
                proof = _proof_for({'status': 'not_published', 'reason': reason}, route, field, product)
                proof['verified_at'] = fill['proof']['verified_at']
                proof['checked_source_urls'] = list(record['checked_source_urls'])
            else:
                product[field] = deepcopy(fill['value'])
                unpublished.difference_update(CELLS[field])
                proof = _proof_for(fill['proof'], route, field, product)
            proof['verification_scope'] = SCOPE
            if not isinstance(product.get('field_provenance'), dict):
                product['field_provenance'] = {}
            product['field_provenance'][field] = proof
            if unpublished or 'unpublished_fields' in product:
                product['unpublished_fields'] = sorted(unpublished)
            touched.add('visa_products')
        else:
            unpublished = set(fields.get('unpublished_fields') or [])
            if absence:
                fields[field] = None
                unpublished.update(CELLS[field])
                # The loader's own shape for a deliberate null, so reloading
                # the stored entry changes nothing. The loader keeps no date
                # on a null, so the review date travels in the note.
                proofs[field] = {'status': 'unknown', 'verifier': 'ai', 'source_url': '', 'verified_at': None,
                                 'verified_by': '', 'note': 'Not published by the destination: ' + reason}
            else:
                fields[field] = deepcopy(fill['value'])
                unpublished.difference_update(CELLS[field])
                proof = _proof_for(fill['proof'], route, field)
                proof['verification_scope'] = SCOPE
                proofs[field] = vo._provenance(proof)
            touched.add(field)
            if sorted(unpublished) != sorted(fields.get('unpublished_fields') or []):
                fields['unpublished_fields'] = sorted(unpublished)
                touched.add('unpublished_fields')
                if 'unpublished_fields' not in proofs:
                    proofs['unpublished_fields'] = vo._provenance(out)
        applied.append(record)
    return applied, touched


def _served_product_cells(applied, old, new, key):
    """Product by product: identity and order unchanged, only the filled
    cells, their proofs and the absence markers may differ."""
    old_products = old.get('visa_products') or []
    new_products = new.get('visa_products') or []
    if len(old_products) != len(new_products):
        raise PatchRejected('Product inventory changed: ' + key)
    by_product = {}
    for a in applied:
        if a['target'] == 'product':
            by_product.setdefault(a['product_type'], []).append(a)
    for before, after in zip(old_products, new_products, strict=True):
        if before.get('type') != after.get('type'):
            raise PatchRejected('Product identity changed: ' + key)
        fills = by_product.get(before.get('type'), [])
        names = {a['field'] for a in fills}
        if digest(_mask_product(before, names)) != digest(_mask_product(after, names)):
            raise PatchRejected('Unreviewed product facts changed on %s: %s' % (key, before.get('type')))
        expected = set(before.get('unpublished_fields') or [])
        for a in fills:
            served = after.get(a['field'])
            if a['kind'] == 'absence':
                expected.update(a['cells'])
                if not _empty(served):
                    raise PatchRejected('%s: a documented absence left a value behind on %s' % (key, a['field']))
            else:
                expected.difference_update(a['cells'])
                if digest(served) != digest(a['value']):
                    raise PatchRejected('Served product value differs from the reviewed value: ' + key)
        if set(after.get('unpublished_fields') or []) != expected:
            raise PatchRejected('Absence markers changed outside the reviewed fills: ' + key)


def _served_route_cells(applied, old, new, key):
    expected = set(old.get('unpublished_fields') or [])
    for a in applied:
        if a['target'] != 'route':
            continue
        if a['kind'] == 'absence':
            expected.update(a['cells'])
            if new.get(a['field']) is not None:
                raise PatchRejected('%s: a documented absence left a value behind on %s' % (key, a['field']))
        else:
            expected.difference_update(a['cells'])
            if digest(new.get(a['field'])) != digest(a['value']):
                raise PatchRejected('Served value differs from the reviewed value: ' + key)
    if set(new.get('unpublished_fields') or []) != expected:
        raise PatchRejected('Absence markers changed outside the reviewed fills: ' + key)


def _reaches(applied, before, after, key):
    """Every fill must surface on at least one served record as a filled
    cell (or a not-published cell for an absence) that did not read so
    before. A fill nobody can see is not a fill. A product fill is judged
    on the served row of its own product index, because two product names
    can collapse to one served name."""
    from app.visa_snapshot import tstation
    for a in applied:
        cells = a['cells']
        expected = ('not-published',) if a['kind'] == 'absence' else ('filled', 'not-applicable')
        hit = False
        for x, y in zip(before, after, strict=True):
            if a['target'] == 'product' and y.get('_product_index') != a['product_index']:
                continue
            sx, sy = tstation.field_status(x), tstation.field_status(y)
            now = all(sy.get(c) in expected for c in cells) and sy.get(cells[0]) == expected[0]
            was = all(sx.get(c) in expected for c in cells) and sx.get(cells[0]) == expected[0]
            if now and not was:
                hit = True
                break
        if not hit:
            raise PatchRejected('%s: the fill does not reach any served cell on %s' % (_label(a), key))


def _grade_verdict(a, guidance, provenance, route):
    """Whether the grader will count this fill as its own proved value, and
    when it will not, the gate that refused it, so a reviewer can tell a
    rejected proof from a field the grader simply does not score."""
    from app.visa_snapshot import tstation
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    from app.visa_snapshot.grade_evidence import _proof_supported, _value_supported
    if a['kind'] == 'absence':
        return None, None
    try:
        if a['target'] == 'product':
            product = _product_named(guidance.get('visa_products'), a['product_type'], _label(a))
            proof = (product.get('field_provenance') or {}).get(a['field'])
            value = product.get(a['field'])
        else:
            product = None
            proof = (provenance.get('field_provenance') or {}).get(a['field'])
            value = guidance.get(a['field'])
        if _proof_supported(proof, route, product, a['field'], value):
            return True, None
    except (PatchRejected, KeyError, TypeError, AttributeError) as exc:
        return False, 'the grader raised ' + exc.__class__.__name__
    if not isinstance(proof, dict):
        return False, 'no stored proof for the field'
    if proof.get('status') not in (None, 'reviewed', 'verified'):
        return False, 'the stored proof status %r is not a review' % proof.get('status')
    if not tstation.verdict_provenance_supported(dict(proof, fields=['disposition'])):
        return False, 'the grader needs a government source_url, a note and a past review date on the proof'
    if not jurisdiction_matches(proof.get('source_url') or '', route.get('destination_country', '')):
        return False, 'the proof source is not a destination-government page'
    if not _value_supported(a['field'], value, proof):
        if a['field'] in ('fee', 'government_fee'):
            return False, 'the grader reads the quote literally and needs the ISO code beside the amount, not a currency symbol'
        return False, 'the grader cannot match the value against the quoted text'
    return False, 'the grader refused the proof for its subject or policy interval'


def convert(manifest, layers):
    from app.visa_snapshot import verified_overrides as vo, tstation
    from app.visa_snapshot.authority import hostname, is_government_host
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    from app.visa_snapshot.kimi_primary import serve_time_invariants
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'kind', 'specification', 'routes'}:
        raise PatchRejected('Malformed field-fill manifest')
    baselines = [dict(deepcopy(r['baseline']), cache_key=r['cache_key']) for r in manifest['routes']]
    if digest(build_manifest(manifest['specification'], baselines)) != digest(manifest):
        raise PatchRejected('Prepared manifest changed')
    spec = manifest['specification']
    current = validate(spec, layers)
    sources = _source_table(spec)
    entries, previews, grade_changes = [], [], []
    fills = absences = 0
    for row in sorted(spec['routes'], key=lambda r: r['cache_key']):
        key = row['cache_key']; layer = current[key]; route = layer['route']
        doc = route.get('travel_document_type') or 'ordinary_passport'
        identity, prior = _prior(layer)
        old, checked = vo.merge_verified_fields(layer['raw_guidance'], prior['fields'], source_url=prior['source_url'])
        old_prov = dict(prior['field_provenance'].get('disposition') or vo._provenance(prior), fields=sorted(checked),
                        field_provenance=prior['field_provenance'])
        if digest(old) != digest(layer['merged_guidance']) or digest(old_prov) != digest(layer['source_provenance']):
            raise PatchRejected('Original canonical source reconstruction changed: ' + key)
        out = deepcopy(prior)
        out.update(route=dict(nationality=route['passport_nationality'], destination=route['destination_country'],
                              travel_purpose=route['travel_purpose'], travel_document_type=doc),
                   partial_review=True, review_id=spec['id'], field_review_scope=SCOPE)
        # The reviewed-overlay store drops a whole release file when one
        # entry cites a page of another government, so the seed's own
        # source_url must already be a destination-government page.
        source_url = str(out.get('source_url') or '')
        if not is_government_host(hostname(source_url)) or not jurisdiction_matches(source_url, route['destination_country']):
            raise PatchRejected('Entry source_url is not a %s government page: %s' % (route['destination_country'], key))
        applied, touched = _apply_fills(out, row['fills'], route, sources)
        _tidy_notes(out)
        lint = vo._field_errors(out['fields'])
        if lint:
            raise PatchRejected('Entry fields fail the store lint on %s: %s' % (key, '; '.join(lint)))
        parsed = vo._parse_rows([out], {}).get(identity)
        if (not parsed or digest(parsed['fields']) != digest(out['fields']) or
                digest(parsed['field_provenance']) != digest(out['field_provenance'])):
            raise PatchRejected('Loader altered reviewed fields/proofs: ' + key)
        g, now_checked = vo.merge_verified_fields(layer['raw_guidance'], parsed['fields'], source_url=parsed['source_url'])
        if set(now_checked) != set(checked) | touched:
            raise PatchRejected('Fill altered checked fields outside its scope: ' + key)
        prov = dict(parsed['field_provenance'].get('disposition') or vo._provenance(parsed), fields=sorted(now_checked),
                    field_provenance=parsed['field_provenance'])
        if g.get('disposition') != old.get('disposition') or g.get('requirement_detail') != old.get('requirement_detail'):
            raise PatchRejected('Fill changed the verdict: ' + key)
        if digest(_mask(g, touched)) != digest(_mask(old, touched)):
            raise PatchRejected('Unreviewed facts changed: ' + key)
        _served_product_cells(applied, old, g, key)
        _served_route_cells(applied, old, g, key)
        op, np_ = deepcopy(old_prov), deepcopy(prov)
        for field in touched:
            op['field_provenance'].pop(field, None); np_['field_provenance'].pop(field, None)
        op.pop('fields'); np_.pop('fields')
        if digest(_note_normalised(op)) != digest(_note_normalised(np_)):
            raise PatchRejected('Unreviewed route provenance changed: ' + key)
        if set(serve_time_invariants(g)) - set(serve_time_invariants(old)):
            raise PatchRejected('New serving contradiction: ' + key)
        before = tstation.records_for_route(route, old, old_prov)
        after = tstation.records_for_route(route, g, prov)
        if len(before) != len(after):
            raise PatchRejected('Product inventory changed: ' + key)
        changed_columns = set()
        for a, b in zip(before, after, strict=True):
            if a.get('visa_type_name') != b.get('visa_type_name') or a.get('visa_requirement') != b.get('visa_requirement'):
                raise PatchRejected('Product identity or requirement changed: ' + key)
            grade = (a.get('confidence_level'), b.get('confidence_level'))
            if grade[0] != grade[1]:
                if grade != ('Medium', 'High'):
                    raise PatchRejected('Grade changed other than Medium to High on %s: %s' % (key, b.get('visa_type_name')))
                grade_changes.append(dict(cache_key=key, visa_type_name=b.get('visa_type_name'), before=grade[0], after=grade[1]))
            diff = {c for c in set(a) | set(b) if a.get(c) != b.get(c)}
            if diff - RECORD_COLUMNS_MAY_CHANGE:
                raise PatchRejected('Unexpected record columns changed on %s: %s' % (key, sorted(diff - RECORD_COLUMNS_MAY_CHANGE)))
            changed_columns |= diff
        _reaches(applied, before, after, key)
        for a in applied:
            a['grade_credited'], a['grade_reason'] = _grade_verdict(a, g, prov, route)
        fills += sum(1 for a in applied if a['kind'] == 'fill')
        absences += sum(1 for a in applied if a['kind'] == 'absence')
        entries.append(out)
        previews.append(dict(cache_key=key, guidance=g, source_provenance=prov, records=after, fills=applied,
                             filled_fields=sorted(a['field'] for a in applied if a['kind'] == 'fill'),
                             documented_absences=sorted(a['field'] for a in applied if a['kind'] == 'absence'),
                             changed_fields=sorted(touched), changed_record_columns=sorted(changed_columns)))
    return (dict(schema_version=1, kind='reviewed_overlay_conversion', review_id=spec['id'], entries=entries,
                 status='detached; exact-layer preflight required'),
            dict(routes=previews, fills=fills, absences=absences, rejected=[], grade_changes=grade_changes,
                 raw_writes=False, operator_writes=False, issue_changes=False, renew_fresh_until=False,
                 confidence_changed=bool(grade_changes), new_release=False))


def verify_prepared(spec, layers, prepared_manifest, prepared_overlay):
    manifest = build_manifest(spec, layers)
    overlay, report = convert(manifest, layers)
    if digest(manifest) != digest(prepared_manifest) or digest(overlay) != digest(prepared_overlay):
        raise PatchRejected('Prepared artifacts differ from full rebuild')
    return report


def triage(spec, layers):
    """Sort a research sweep's fills into the installable ones and the refused.

    Strict conversion refuses a whole specification on any doubt, which is
    right for a release and useless for reading a sweep. This runs the same
    checks fill by fill, keeps what passes, and returns the trimmed
    specification, the layers it needs and a report naming every accepted
    fill, every documented absence and every rejection with its reason. A
    row that cannot be read is itself a named rejection, never a silent drop.
    """
    _check_spec_shape(spec)
    sources = _source_table(spec)
    by_key = {l.get('cache_key'): l for l in layers if isinstance(l, dict)}
    kept_routes, kept_layers, accepted, absences, rejected = [], [], [], [], []

    def refuse(key, fill, reason):
        rejected.append(dict(cache_key=key, target=fill.get('target') if isinstance(fill, dict) else None,
                             product_type=fill.get('product_type') if isinstance(fill, dict) else None,
                             field=fill.get('field') if isinstance(fill, dict) else None, reason=reason))

    rows = spec['routes']
    if not isinstance(rows, list):
        refuse(None, None, 'routes must be a list')
        rows = []
    seen_keys = set()
    for row in rows:
        if not isinstance(row, dict):
            refuse(None, None, 'Route row is not an object')
            continue
        key = row.get('cache_key')
        row_fills = row.get('fills')
        if not isinstance(row_fills, list):
            refuse(key, None, 'fills must be a list')
            continue
        if not row_fills:
            refuse(key, None, 'Each context needs at least one fill')
            continue
        if key in seen_keys:
            for fill in row_fills:
                refuse(key, fill, 'Duplicate route row: ' + str(key))
            continue
        seen_keys.add(key)
        layer = by_key.get(key)
        if layer is None:
            for fill in row_fills:
                refuse(key, fill, 'Current layer missing')
            continue
        try:
            prior = _route_checks(row, layer)
        except PatchRejected as exc:
            for fill in row_fills:
                refuse(key, fill, str(exc))
            continue
        fills, seen = [], set()
        for fill in row_fills:
            fill_key = _fill_key(fill)
            if fill_key in seen:
                refuse(key, fill, 'Duplicate fill on one cell')
                continue
            seen.add(fill_key)
            try:
                _validate_fill(fill, layer, sources, prior)
                fills.append(fill)
            except PatchRejected as exc:
                refuse(key, fill, str(exc))
        if not fills:
            continue

        def converts(subset):
            one = dict(spec, routes=[dict(row, fills=subset)])
            try:
                convert(build_manifest(one, [layer]), [layer])
            except PatchRejected as exc:
                return str(exc)
            return None

        problem = converts(fills)
        if problem:
            # Conversion judges a whole context. Find the fills that fail
            # alone, then confirm the survivors convert together.
            keep = []
            for fill in fills:
                reason = converts([fill])
                if reason:
                    refuse(key, fill, reason)
                else:
                    keep.append(fill)
            fills = keep
            problem = converts(fills) if fills else None
            if problem:
                for fill in fills:
                    refuse(key, fill, problem)
                continue
        if not fills:
            continue
        kept_routes.append(dict(row, fills=fills))
        kept_layers.append(layer)
        for fill in fills:
            (absences if _wants_absence(fill['proof']) else accepted).append(
                dict(cache_key=key, target=fill['target'], product_type=fill.get('product_type'), field=fill['field'],
                     value=deepcopy(fill['value'])))
    return dict(spec, routes=kept_routes), kept_layers, dict(accepted=accepted, absences=absences, rejected=rejected)


def main(argv):
    """Triage, build and convert, writing report.json whatever happens after
    triage so a failed conversion still leaves its reason on disk."""
    import json
    from pathlib import Path
    if len(argv) != 3:
        raise SystemExit('usage: convert_reviewed_field_fill.py specification.json current-layers.json output-dir')
    spec = json.loads(Path(argv[0]).read_text())
    layers = json.loads(Path(argv[1]).read_text())
    layers = layers.get('layers', layers)
    out_dir = Path(argv[2]); out_dir.mkdir(parents=True, exist_ok=True)
    kept, kept_layers, triage_report = triage(spec, layers)
    report = dict(triage_report, fills=0, absences=0, routes=[])
    failure = None
    if kept['routes']:
        try:
            manifest = build_manifest(kept, kept_layers)
            overlay, conversion = convert(manifest, kept_layers)
        except PatchRejected as exc:
            failure = str(exc)
            report['conversion_error'] = failure
        else:
            (out_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
            (out_dir / 'overlay.json').write_text(json.dumps(overlay, ensure_ascii=False, indent=1) + '\n')
            (out_dir / 'specification.json').write_text(json.dumps(kept, ensure_ascii=False, indent=1) + '\n')
            report.update(fills=conversion['fills'], absences=conversion['absences'], grade_changes=conversion['grade_changes'],
                          routes=[dict(cache_key=r['cache_key'], fills=r['fills'], changed_record_columns=r['changed_record_columns'])
                                  for r in conversion['routes']])
    (out_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(dict(routes=len(kept['routes']), fills=report['fills'], absences=report['absences'],
                          rejected=len(report['rejected']), conversion_error=failure)))
    if failure:
        raise SystemExit('conversion failed, report written: ' + failure)
    return report


if __name__ == '__main__':
    import sys
    main(sys.argv[1:])
