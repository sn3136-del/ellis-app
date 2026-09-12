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
sentence must bind to the product, must not speak about another nationality
or another entry type, must use a validity word for a validity and a stay
word for a stay, must put the figure beside its own unit, must carry the
currency marker beside the amount, and must be about the government fee
rather than a service charge. A documented absence names the captured pages
it checked and is refused when any of them states a value for the field.
Where a gate cannot decide, it rejects, because a wrong value reaches
travellers.

A served product is named with a synthesised label ("Visitor visa (subclass
600) — Tourist stream (apply outside Australia)") that no government page
prints, so a sentence binds to a product through anchors derived from the
product: its class codes (subclass 600, B-1/B-2, K-ETA, VLS-TS), its permit
family (visa on arrival, electronic visa, travel authorisation, tourist
visa), its stream (tourist, business, family), its entry type and a duration
in its name. A sentence binds to the target when it carries an anchor of the
target and no anchor of a sibling product that the target lacks; against a
sibling that shares every anchor the sentence carries, it must carry a word
of the target's name that sibling's name lacks; a sentence with no anchor at
all binds only when the route serves exactly one product.
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
# The letters of the scripts that separate words with spaces: Latin with
# its accented and Vietnamese forms, Greek and Cyrillic. A word boundary
# means something only in those scripts. Chinese, Japanese, Korean, Thai
# and Arabic glue a word to its neighbours, so a term in those scripts
# matches bare wherever it stands.
_LETTER = r'A-Za-zÀ-ɏḀ-ỿͰ-ϿЀ-ӿ'
_L = r'(?<![' + _LETTER + '])'
_R = r'(?![' + _LETTER + '])'
# A vocabulary stem may run on into a Cyrillic ending (транзит matches
# Транзитная, служебн matches служебных). Money markers and country
# aliases keep the strict boundary.
_R_STEM = r'(?![A-Za-zÀ-ɏḀ-ỿͰ-Ͽ])'
# The left boundary of a figure. Chinese, Japanese, Korean and Thai pages
# glue the figure to the preceding character (有効期間は90日, 有效期为90天),
# so only a Latin, Greek or Cyrillic letter, a digit or a decimal mark may
# not precede it.
_FIGURE_L = r'(?<![\d.,])' + _L


def _words(latin, other=''):
    """An alternation of letter-bounded Latin words and bare CJK or Cyrillic
    stems, for vocabulary the official pages of the served destinations use."""
    parts = [_L + '(?:' + latin + ')' + _R_STEM] if latin else []
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
_FIGURE_FORMS = r'(?:\d+(?:\.\d+)?|' + '|'.join(_NUMBER_WORDS.values()) + r')'
_FIGURE_TAIL = r'\s*(?:\(\s*\d+\s*\)\s*)?[-‐–]?\s*(?:calendar\s+|kalender\s+)?'
_DURATION_RE = re.compile(_FIGURE_L + _FIGURE_FORMS + _FIGURE_TAIL + '(?:' + _ANY_UNIT_RE.pattern + ')', re.I)
# A figure beside a word of one unit, for counting the figures a sentence
# states in the unit of the value.
_UNIT_FIGURE_RES = {u: re.compile(_FIGURE_L + _FIGURE_FORMS + _FIGURE_TAIL + '(?:' + _words(*_UNIT_WORDS[u]) + ')', re.I)
                    for u in _UNIT_WORDS}
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
# The fee words of every served language. German writes the fee as one
# compound (Visumgebühr), so the compound forms stand in front of the stem.
_FEE_WORDS = re.compile(_words(
    r'fees?|charges?|costs?|prices?|pricing|tariffs?|duty|duties|tarifs?|tarifas?|frais|droits?|montant|tasas?|'
    r'derechos?|precios?|importe|costes?|costos?|arancel(?:es)?|(?:visum|visa|konsular|bearbeitungs|antrags)?gebühr(?:en)?|'
    r'kosten|entgelt|tassa|tasse|costi|tariffa|taxas?|emolumentos?|ücret(?:i|leri)?|harç|harcı|lệ phí|phí|biaya',
    r'费用|費用|签证费|簽證費|费|費|手数料|料金|수수료|비용|요금|사증료|сбор|пошлин|стоимост|ค่าธรรมเนียม|ค่าวีซ่า|'
    r'ค่าใช้จ่าย|رسوم|رسم'), re.I)
_FEE_SUBJECT_WORDS = re.compile(_words(r'consular(?:es)?|consulaires?|konsular(?:gebühr(?:en)?)?|government|state|official|'
                                       r'e-?visas?|visas?|visum(?:s|gebühr(?:en)?)?|visa(?:gebühr(?:en)?)?|visto|visados?|'
                                       r'visés?|konsuler|thị thực|lãnh sự|วีซ่า|กงสุล',
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
    ('A$', {'AUD'}), ('AU$', {'AUD'}), ('US$', {'USD'}), ('US $', {'USD'}), ('U.S.$', {'USD'}), ('HK$', {'HKD'}), ('S$', {'SGD'}),
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
    ('baht', {'THB'}), ('บาท', {'THB'}), ('ringgit', {'MYR'}), ('RM', {'MYR'}), ('rubles', {'RUB'}), ('roubles', {'RUB'}),
    ('đồng', {'VND'}), ('đ', {'VND'}), ('ريال', {'SAR'}), ('درهم', {'AED'}), ('جنيه', {'EGP'}),
    ('ruble', {'RUB'}), ('rouble', {'RUB'}), ('руб.', {'RUB'}), ('руб', {'RUB'}), ('рублей', {'RUB'}),
    ('dong', {'VND'}), ('VNĐ', {'VND'}), ('pesos', {'PHP', 'MXN'}), ('peso', {'PHP', 'MXN'}),
    ('pounds', {'GBP', 'EGP'}), ('pound', {'GBP', 'EGP'}), ('francs', {'CHF'}), ('franc', {'CHF'}),
    ('dirhams', {'AED'}), ('dirham', {'AED'}), ('riyals', {'SAR'}), ('riyal', {'SAR'}), ('lira', {'TRY'}),
    ('港币', {'HKD'}), ('港幣', {'HKD'}), ('港元', {'HKD'}), ('新台币', {'TWD'}), ('新臺幣', {'TWD'}),
    # The currency words of the Chinese, Japanese, Korean, Russian, Thai,
    # Vietnamese, Indonesian and Arabic pages Ellis reads. The languages
    # that glue the word to the figure (3,000円です, 40,000원입니다) match
    # bare, see _marker_pattern.
    ('美元', {'USD'}), ('美金', {'USD'}), ('欧元', {'EUR'}), ('歐元', {'EUR'}), ('日元', {'JPY'}), ('日圆', {'JPY'}),
    ('日圓', {'JPY'}), ('韩元', {'KRW'}), ('韓元', {'KRW'}), ('英镑', {'GBP'}), ('英鎊', {'GBP'}), ('澳元', {'AUD'}),
    ('加元', {'CAD'}), ('新加坡元', {'SGD'}), ('新元', {'SGD'}), ('泰铢', {'THB'}), ('泰銖', {'THB'}), ('卢布', {'RUB'}),
    ('盧布', {'RUB'}), ('越南盾', {'VND'}), ('印尼盾', {'IDR'}), ('卢比', {'INR'}), ('盧比', {'INR'}), ('林吉特', {'MYR'}),
    ('澳门元', {'MOP'}), ('澳門元', {'MOP'}), ('人民元', {'CNY'}),
    ('米ドル', {'USD'}), ('ユーロ', {'EUR'}), ('ポンド', {'GBP'}), ('ウォン', {'KRW'}), ('バーツ', {'THB'}),
    ('ルピア', {'IDR'}), ('ルーブル', {'RUB'}), ('ルピー', {'INR'}), ('リンギット', {'MYR'}), ('ドル', _DOLLARS),
    ('달러', _DOLLARS), ('유로', {'EUR'}), ('파운드', {'GBP'}), ('위안', {'CNY'}), ('바트', {'THB'}), ('루피아', {'IDR'}),
    ('루블', {'RUB'}), ('링깃', {'MYR'}), ('엔', {'JPY'}),
    ('долларов', {'USD'}), ('доллара', {'USD'}), ('доллар', {'USD'}), ('долл.', {'USD'}), ('долл', {'USD'}),
    ('евро', {'EUR'}), ('фунтов', {'GBP'}), ('юаней', {'CNY'}), ('иен', {'JPY'}), ('бат', {'THB'}),
    ('ดอลลาร์', _DOLLARS), ('ยูโร', {'EUR'}), ('เหรียญ', _DOLLARS), ('đô la Mỹ', {'USD'}), ('đô la', _DOLLARS),
    ('dolar AS', {'USD'}), ('dolar', _DOLLARS), ('دولار', {'USD'}), ('يورو', {'EUR'}),
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


def _spaced_script(text):
    """Whether this text is written in a script that separates words with
    spaces (Latin, Greek, Cyrillic), so a letter boundary means something.
    Chinese, Japanese, Korean, Thai and Arabic glue the word to its
    neighbours (3,000円です, 40,000원입니다), so those match bare."""
    return bool(re.search('[' + _LETTER + ']', text))


def _marker_pattern(literal):
    text = re.escape(literal).replace(r'\ ', r'\s+')
    if not _spaced_script(literal):
        return text
    return (_L if literal[0].isalpha() else '') + text + (_R if literal[-1].isalpha() else '')


_MARKER_TABLE = [(re.compile(_marker_pattern(m), re.I), codes) for m, codes in _CURRENCY_MARKERS]
_MARKER_ALT = '|'.join(_marker_pattern(m) for m, _ in _CURRENCY_MARKERS)
_MONEY_RE = re.compile(r'(?:' + _MARKER_ALT + r')\s*\d[\d,.]*|\d[\d,.]*\s*(?:' + _MARKER_ALT + ')', re.I)
# Money written across table cells: the marker in one cell and the amount
# in the next ("Tourist visa | USD | 160"), or a bare marker in a header or
# a cell ("Fee (in US $)", "| A$ |") with number cells further down.
_CELL_MONEY_RE = re.compile(r'(?:' + _MARKER_ALT + r')\s*[|\t]+\s*\d[\d,.]*(?![\d,.]*\s*%)|'
                            r'\d[\d,.]*\s*[|\t]+\s*(?:' + _MARKER_ALT + ')', re.I)
_BARE_MARKER_RE = re.compile(r'(?:(?<=[|\t\n(])|^)\s*(?:in\s+)?(?:' + _MARKER_ALT + r')\s*(?=[|\t\n)]|$)', re.I | re.M)
_NUMBER_CELL_RE = re.compile(r'(?:(?<=[|\t\n])|^)\s*\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{1,2})?\s*(?=[|\t\n]|$)', re.M)
_NUMBER_TOKEN_RE = re.compile(r'(?<![\d.,])\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{1,2})?(?![\d.,])')
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

# Subject scope. A sentence about another passport class, a bloc the
# nationality does not belong to, or residents of another place is not
# about this route, whatever product it names.
_PASSPORT_CLASSES = [
    ('diplomatic', re.compile(_words(r'diplomatic|diplomatique|diplomátic[oa]s?|diplomatik|diplomaten(?:pass|pässe)?|diplomatico|'
                                     r'ngoại giao|ทูต', r'外交|외교|дипломатическ'), re.I)),
    ('service', re.compile(_words(r'service|servicio|dienst(?:pass|pässe|passes)?|servizio|serviço|công vụ|dinas', r'公务|公務|공무|служебн'), re.I)),
    ('official', re.compile(_words(r'official|officiel(?:le)?s?|oficial(?:es)?|amtlich|ufficiale|ราชการ',
                                   r'公用|官员|官員|관용|официальн'), re.I)),
    ('laissez-passer', re.compile(_words(r'laissez[- ]passer', ''), re.I)),
    ('refugee', re.compile(_words(r'refugees?|réfugiée?s?|refugiad[oa]s?|flüchtling[es]?|rifugiat[oi]|tị nạn|pengungsi|ผู้ลี้ภัย',
                                  r'難民|难民|난민|беженц'), re.I)),
    ('stateless', re.compile(_words(r'stateless|apatrides?|apátridas?|staatenlos(?:e|en)?|không quốc tịch|tanpa kewarganegaraan|ไร้สัญชาติ',
                                    r'無国籍|无国籍|무국적|без гражданства'), re.I)),
]
# Classes that name a passport class on their own. The others (diplomatic,
# service, official) are ordinary words until a passport word stands beside them.
_STANDALONE_CLASSES = frozenset({'laissez-passer', 'refugee', 'stateless'})
_PASSPORT_WORDS_WIDE = re.compile(_words(
    r'passports?|travel documents?|passeports?|pasaportes?|paspor|pasport|reisep[aä]ss(?:e|es)?|pässe|passaport[oi]|passaporte|'
    r'hộ chiếu|หนังสือเดินทาง|جواز', r'护照|護照|パスポート|旅券|여권|паспорт'), re.I)
_EU = frozenset('AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE'.split())
_BLOCS = {
    'European Union': (re.compile(_words(r'european union|unión europea|union européenne|europäischen? union|unione europea|'
                                         r'união europeia|liên minh châu âu|uni eropa|евросоюз|европейск(?:ий|ого|ому|им) союз',
                                         r'欧盟|歐盟|欧州連合|유럽\s*연합|สหภาพยุโรป'), re.I),
                       re.compile(r'(?<![A-Za-zА-я])(?:EU|UE|ЕС)(?![A-Za-zА-я])'), _EU),
    'EEA': (re.compile(_words(r'european economic area|espacio económico europeo|espace économique européen|'
                              r'europäischer wirtschaftsraum|europäischen wirtschaftsraum', r'欧洲经济区|歐洲經濟區|欧州経済領域|유럽\s*경제\s*지역'), re.I),
            re.compile(r'(?<![A-Za-zА-я])(?:EEA|EEE|EWR|ЕЭЗ)(?![A-Za-zА-я])'), _EU | {'ISL', 'LIE', 'NOR'}),
    'Schengen': (re.compile(_words(r'schengen|шенген', r'申根|シェンゲン|셍겐|쉥겐|เชงเก้น|เชงเกน'), re.I), None,
                 (_EU - {'IRL', 'CYP'}) | {'ISL', 'LIE', 'NOR', 'CHE'}),
    'GCC': (re.compile(_words(r'gulf cooperation council|conseil de coopération du golfe|consejo de cooperación del golfo',
                              r'海合会|海灣合作|海湾合作|걸프\s*협력'), re.I),
            re.compile(r'(?<![A-Za-z])(?:GCC|CCG)(?![A-Za-z])'), frozenset('BHR KWT OMN QAT SAU ARE'.split())),
    'ASEAN': (re.compile(_words(r'asean|асеан', r'东盟|東盟|アセアン|아세안|อาเซียน'), re.I), None,
              frozenset('BRN KHM IDN LAO MYS MMR PHL SGP THA VNM TLS'.split())),
    'Mercosur': (re.compile(_words(r'mercosur|mercosul', ''), re.I), None, frozenset('ARG BRA PRY URY BOL'.split())),
    'Commonwealth': (re.compile(r'(?<![^\W\d_])commonwealth(?!\s+of\s+(?!nations)(?:the\s+)?[A-Z])(?![^\W\d_])', re.I), None,
                     frozenset('ATG AUS BHS BGD BRB BLZ BWA BRN CMR CAN CYP DMA SWZ FJI GAB GMB GHA GRD GUY IND JAM KEN KIR '
                               'LSO MWI MYS MDV MLT MUS MOZ NAM NRU NZL NGA PAK PNG RWA KNA LCA VCT WSM SYC SLE SGP SLB ZAF '
                               'LKA TZA TGO TON TTO TUV UGA GBR VUT ZMB'.split())),
    'CIS': (re.compile(_words(r'commonwealth of independent states|содружеств[оа] независимых государств', ''), re.I),
            re.compile(r'(?<![A-Za-zА-я])(?:CIS|СНГ)(?![A-Za-zА-я])'), frozenset('ARM AZE BLR KAZ KGZ MDA RUS TJK UZB'.split())),
    'African Union': (re.compile(_words(r'african union|union africaine|unión africana|união africana', r'非洲联盟|非盟|アフリカ連合|아프리카\s*연합'), re.I),
                      None, frozenset('DZA AGO BEN BWA BFA BDI CMR CPV CAF TCD COM COD COG CIV DJI EGY GNQ ERI SWZ ETH GAB GMB '
                                      'GHA GIN GNB KEN LSO LBR LBY MDG MWI MLI MRT MUS MAR MOZ NAM NER NGA RWA ESH STP SEN SYC '
                                      'SLE SOM ZAF SSD SDN TZA TGO TUN UGA ZMB ZWE'.split())),
}
# A bloc is a subject when a nationals, citizens or passport word stands
# right beside it ("nationals of European Union member states", "EU
# citizens", "欧盟公民"). A bare bloc name is a place (a Schengen visa, the
# EU border), and "Schengen visa for nationals of Australia" binds the
# nationals to Australia, not to Schengen.
_BLOC_SUBJECT_WORDS = re.compile(_words(
    r'nationals?|citizens?|citizenship|nationality|passports?|passport holders?|holders?|ciudadan[oa]s?|nacionales?|'
    r'ressortissants?|citoyen(?:ne)?s?|bürger(?:innen)?|staatsangehörige[nr]?|staatsbürger|cittadin[oi]|cidadã[os]s?|'
    r'công dân|quốc tịch|warga negara|kewarganegaraan|พลเมือง|สัญชาติ',
    r'公民|国民|國民|国籍|國籍|护照|護照|旅券|パスポート|여권|국민|시민|граждан|гражданств'), re.I)
# The words that may stand between a bloc and its subject word.
_BLOC_LINK_WORDS = re.compile(_words(
    r'member|members|state|states|countr(?:y|ies)|estados?|miembros?|países|états?|membres?|pays|mitglied(?:s)?|'
    r'staaten|länder|stati|membri|paesi|thành viên|nước|anggota|negara|สมาชิก|ประเทศ|стран(?:ы|ах)?|государств(?:а)?|'
    r'член(?:ов|ы)?',
    r'成员国|成員國|加盟国|회원국'), re.I)
_RESIDENCE_RE = re.compile(_words(
    r'resid(?:ing|ent|ents|ence|ency)\s+(?:in|of)|(?:lawfully|legally|ordinarily|permanently|habitually)\s+resid(?:ing|ent)|'
    r'domiciled\s+in|résid(?:ant|ent|ents)\s+(?:en|au|aux|à|dans)|résidents?\s+(?:de|du|des)|domicilié(?:e|s|es)?\s+(?:en|au|aux|à)|'
    r'residentes?\s+(?:en|de)|con\s+residencia\s+en|residen\s+en|domiciliad[oa]s?\s+en|wohnhaft\s+in|ansässig\s+in|'
    r'mit\s+wohnsitz\s+in|residenti\s+(?:in|a|nel|nella)|residentes?\s+(?:em|no|na)|cư trú tại|thường trú tại|'
    r'sinh sống tại|berdomisili di|bertempat tinggal di|penduduk|มีถิ่นที่อยู่ใน|พำนักอยู่ใน|อาศัยอยู่ใน|ผู้พำนักใน',
    r'居住在|居住于|居住於|常住|定居|在住|居住|거주|проживающ|проживает|проживания в|постоянно проживающ'), re.I)

# Visa classes. A route-level fill may borrow only a sentence that names
# the route's own class or none at all. Each family lists its forms in the
# served languages, and a family is served when the route's purpose is of
# that family or a served product type or the visa category names it.
_VISA_CLASSES = {
    'tourist': re.compile(_words(
        r'tourists?|tourism|touristic|visitors?|visits?|visiting|holiday|vacation|leisure|touristique|tourisme|touristes?|'
        r'turismo|turistas?|turístic[oa]s?|tourismus|touristen(?:vis(?:um|a|en))?|touristisch|besuch(?:ervis(?:um|a|en))?|'
        r'besucher|du lịch|tham quan|wisata|turis|'
        r'kunjungan|ท่องเที่ยว|เยี่ยม|туристическ|турист|гостев',
        r'旅游|旅遊|观光|觀光|観光|관광|방문|短期滞在|访问|訪問'), re.I),
    'business': re.compile(_words(
        r'business|negocios|affaires|geschäft(?:svis(?:um|a|en)|s|lich)?|affari|negócios|thương mại|công tác|kinh doanh|'
        r'bisnis|usaha|'
        r'ธุรกิจ|делов|бизнес|коммерческ', r'商务|商務|商业|商業|商用|상용|비즈니스|사업'), re.I),
    'student': re.compile(_words(
        r'students?|study|studies|studying|estudiantes?|estudios|étudiant(?:e)?s?|études|student(?:en)?vis(?:um|a|en)|'
        r'studienvis(?:um|a|en)|studenten|studium|studierende|'
        r'student[ei]|studio|du học|học tập|sinh viên|học sinh|pelajar|mahasiswa|belajar|studi|นักเรียน|นักศึกษา|ศึกษา|'
        r'студенческ|учебн|обучени', r'学生|學生|留学|留學|学习|學習|就学|유학|학생|연수'), re.I),
    'family': re.compile(_words(
        r'family|families|relatives|spouses?|dependants?|dependents?|familiar(?:es)?|familia|famille|familial(?:e)?|'
        r'familien(?:besuch)?|thăm thân|gia đình|keluarga|ครอบครัว|เยี่ยมญาติ|частн|родственник',
        r'探亲|探親|家族|親族|가족|친척'), re.I),
    'work': re.compile(_words(
        r'work|working|employment|employees?|workers?|labou?r|trabajo|travail|arbeit(?:svis(?:um|a|en)|s)?|beschäftigung|'
        r'lavoro|trabalho|'
        r'lao động|làm việc|kerja|bekerja|ทำงาน|рабоч|работ|трудов', r'工作|就労|就業|취업|근로|노동'), re.I),
    'transit': re.compile(_words(r'transit(?:ing|reisende[nr]?|vis(?:um|a|en))?|flughafentransit|durchreise|stopover|layover|connecting flights|'
                                 r'passagers en correspondance|escala|tránsito|trânsito|transito|quá cảnh|ผ่านแดน|транзит',
                                 r'过境|過境|通過|トランジット|통과|환승'), re.I),
    'crew': re.compile(_words(
        r'crews?|seafarers?|seam[ae]n|aircrew|tripulantes?|tripulación|équipage|besatzung|thuyền viên|phi hành đoàn|awak|'
        r'ลูกเรือ|экипаж|моряк', r'乘务|乘務|船员|船員|机组|機組|乗員|乗務員|승무원|선원'), re.I),
    'diplomatic': re.compile(_words(
        r'diplomatic|diplomatique|diplomátic[oa]s?|diplomatik|diplomaten|courtesy|(?:service|official)\s+(?:visas?|passports?)|'
        r'passeport de service|pasaporte (?:de servicio|oficial)|công vụ|ngoại giao|dinas|ทูต|ราชการ|дипломатическ|служебн',
        r'外交|公务|公務|公用|官员|官員|외교|관용|공무'), re.I),
    'journalist': re.compile(_words(
        r'journalists?|media|press|periodistas?|journalistes?|journalisten|nhà báo|phóng viên|jurnalis|wartawan|นักข่าว|журналист',
        r'记者|記者|報道|기자|취재'), re.I),
    'medical': re.compile(_words(
        r'medical|treatment|médic[oa]|médical(?:e)?|medizinisch(?:e|en)?|behandlung|y tế|chữa bệnh|medis|pengobatan|แพทย์|รักษา|'
        r'медицинск|лечени', r'医疗|醫療|就医|就醫|医療|의료|치료'), re.I),
    'investor': re.compile(_words(
        r'investors?|investment|inversor(?:es)?|inversión|investisseurs?|investissement|nhà đầu tư|đầu tư|penanam modal|'
        r'นักลงทุน|ลงทุน|инвестор|инвестиц', r'投资|投資|투자'), re.I),
    'retirement': re.compile(_words(
        r'retirement|retirees?|retired|jubilad[oa]s?|retraite|retraité(?:e)?s?|ruhestand|rentner|hưu trí|pensiun|เกษียณ|пенсионер',
        r'退休|退職|은퇴'), re.I),
    'religious': re.compile(_words(
        r'religious|pilgrims?|pilgrimage|hajj|umrah|missionar(?:y|ies)|religios[oa]|religieux|pèlerins?|religiös|pilger|tôn giáo|'
        r'hành hương|keagamaan|ziarah|haji|umroh|ศาสนา|แสวงบุญ|религиозн|паломни', r'宗教|朝圣|朝聖|巡礼|종교|성지순례'), re.I),
    'residence': re.compile(_words(
        r'immigrants?|immigration|settlement|permanent residence|residence|residents?|humanitarian|research(?:ers?)?|'
        r'conference|training|trainees?|interns?|internship|working holiday|investigador(?:es)?|investigación|formación|'
        r'prácticas|humanitario|inmigrantes?|residencia|chercheurs?|recherche|formation|stagiaires?|vacances-travail|'
        r'humanitaire|résidence|forschung|forscher|ausbildung|praktikant(?:en)?|praktikum|niederlassung|nghiên cứu|đào tạo|'
        r'thực tập|nhân đạo|định cư|penelitian|pelatihan|magang|kemanusiaan|วิจัย|ฝึกอบรม|ฝึกงาน|มนุษยธรรม|ถิ่นที่อยู่|'
        r'исследоват|стажир|гуманитарн|иммиграц|вид на жительство',
        r'研究|研修|实习|實習|培训|培訓|人道|移民|定居|居留|연구|인턴|인도적|이민|거주|영주'), re.I),
}
_PURPOSE_FAMILIES = {'tourism': ('tourist',), 'business': ('business',), 'study': ('student',),
                     'family_visit': ('family', 'tourist'), 'work': ('work',), 'transit': ('transit',),
                     'medical': ('medical',)}
_CLASS_VISA_WORDS = re.compile(_words(
    r'e-?visas?|visas?|visum(?:s)?|visados?|vistos?|visés?|permits?|permis|permisos?|autorisations?|autorizaci(?:ón|ones)|'
    r'authori[sz]ations?|thị thực|วีซ่า|การตรวจลงตรา|giấy phép|izin|ใบอนุญาต',
    r'签证|簽證|査証|ビザ|비자|사증|виз|разрешени|许可|許可|허가'), re.I)

# Fee scope. A concession is a government fee for somebody else.
_CONCESSION_RE = re.compile(_words(
    r'reduced|reduction|discount(?:ed|s)?|concession(?:ary|s)?|child|children|minors?|infants?|under\s+(?:the\s+age\s+of\s+)?\d+|'
    r'below\s+(?:the\s+age\s+of\s+)?\d+|aged\s+under|students?|pupils?|pensioners?|seniors?|elderly|groups?|waived|waiver|'
    r'free\s+of\s+charge|no\s+fee|gratis|exempt(?:ed|ion)?\s+from|reducid[oa]s?|reducción|descuento|gratuit[oa]s?|menores|'
    r'niñ[oa]s|estudiantes?|jubilad[oa]s|grup[oa]s?|exent[oa]s?|réduits?|réduites?|réduction|gratuit(?:e|s|es)?|enfants?|'
    r'mineurs?|étudiant(?:e)?s?|groupes?|exonér(?:é|és|ée|ées|ation)|ermäßigt(?:e|en|er)?|ermäßigung|kostenlos|gebührenfrei|'
    r'kostenfrei|kinder|minderjährige|studenten|studierende|gruppen?|befreit|miễn phí|miễn lệ phí|giảm|trẻ em|học sinh|'
    r'sinh viên|đoàn|diskon|potongan|anak(?:-anak)?|pelajar|mahasiswa|rombongan|dibebaskan|bebas biaya|ฟรี|'
    r'ยกเว้นค่าธรรมเนียม|ลดหย่อน|ส่วนลด|เด็ก|นักเรียน|นักศึกษา|กลุ่ม|คณะ|бесплатн|льготн|скидк|дет(?:и|ей|ям|ский|ского)|'
    r'несовершеннолетн|студент|пенсионер|групп|освобожд',
    r'免费|免費|减免|減免|优惠|優惠|减收|減收|儿童|兒童|未成年|学生|學生|团体|團體|团队|團隊|未满|未滿|無料|減額|免除|割引|'
    r'子供|子ども|児童|団体|未満|무료|감면|할인|면제|어린이|아동|미성년|학생|단체|미만'), re.I)
# "Citizens of X": the languages that put the country after the subject
# word, and the ones that put it before (澳大利亚公民, 호주 국민). A fee for
# foreign citizens or all nationals names nobody in particular.
_CITIZENS_THEN_COUNTRY_RE = re.compile(_words(
    r'(?:citizens?|nationals?|passport holders?|holders? of (?:an? |the )?[\w-]+ passports?)\s+(?:of|from)|ciudadan[oa]s? de|'
    r'nacionales de|ressortissants? (?:de|du|des)|citoyen(?:ne)?s? (?:de|du|des)|staatsangehörige (?:von|aus|der|des)|'
    r'công dân|warga negara|граждан(?:е|ам|ин|ина|ами)?', ''), re.I)
_COUNTRY_THEN_CITIZENS_RE = re.compile(_words('', r'公民|国民|國民|국민'), re.I)
_ANY_CITIZENS_RE = re.compile(_words(
    r'foreign(?:ers?)?|extranjer[oa]s?|étrangers?|étrangères?|ausländ(?:er|ische[nr]?)|stranier[oi]|estrangeir[oa]s?|'
    r'nước ngoài|asing|ต่างชาติ|ต่างด้าว|all|todos|tous|alle|tất cả|semua|ทุก|всех|иностранн(?:ых|ые|ым|ыми)?|'
    r'third[- ]country|terceros países|pays tiers|drittstaat(?:en|s)?',
    r'外国|外國|外籍|第三国|第三國|외국|제3국'), re.I)
_TOTAL_RE = re.compile(_words(
    r'in total|total(?:ling|led|s)?|altogether|combined|for a family|family of|per family|'
    r'for (?:two|three|four|five|\d+) (?:persons?|people|applicants?|adults?|travell?ers?|passengers?|members?)|'
    r'en total|por familia|familia de|para (?:dos|tres|cuatro|\d+) (?:personas|solicitantes)|au total|par famille|'
    r'famille de|pour (?:deux|trois|quatre|\d+) personnes|insgesamt|gesamt(?:betrag|gebühr)?|pro familie|familie von|'
    r'für (?:zwei|drei|vier|\d+) personen|tổng cộng|gia đình|cho \d+ người|keluarga|untuk \d+ orang|ครอบครัว|'
    r'สำหรับ \d+ คน|итого|всего|в сумме|за семью|семья из|за \d+ человек',
    r'合计|合計|共计|共計|总计|總計|一家|家庭|\d+\s*人分|\d+\s*名分|합계|총액|가족|รวมทั้งสิ้น'), re.I)
_MULTIPLIED_RE = re.compile(r'\d\s*[x×]\s*\d|\d\s*[x×]\s*(?:' + _MARKER_ALT + r')', re.I)
# An age limit is the definition of a child or minor product, not a
# second concession, so it is allowed on a product row that is itself one.
_AGE_PHRASE_RE = re.compile(r'(?:under|below|aged under|unter|moins de|menores de|dưới|di bawah|ต่ำกว่า|младше)\s*(?:the\s+age\s+of\s+)?\d+|'
                            r'\d+\s*(?:未满|未滿|未満|미만)', re.I)


def _concession_stem(word):
    """The family of a concession word across its inflections: the first
    four letters of a spaced-script word (child, children, menor, menores),
    the word itself in a glued script."""
    word = _norm(word)
    return word[:4] if _spaced_script(word) else word

# Temporal scope. An exception, a closed period or a suspension is not the
# current rule, whatever figure the sentence carries.
# A discretion leaves the value to the officer: "at the discretion of",
# "determined by consular officials", "on a case by case basis". The forms
# stand in the exception vocabulary of the fill path and, on their own, in
# the absence path, where a discretion sentence that names no one value
# publishes no value.
_DISCRETION_FORMS = (
    r'at (?:the |its |their |his |her |our )?(?:sole |absolute )?discretion|discretion(?:ary)?|'
    r'(?:determined|decided|assessed|set|fixed|established) (?:solely |only )?(?:by|at) (?:the )?(?:consular|visa|immigration|'
    r'border|competent) (?:officials?|officers?|section|post|authorit(?:y|ies)|department)|'
    r'(?:on an? |on a )?case[- ]by[- ]case(?: basis)?|a discreción|discrecional(?:mente)?|caso por caso|'
    r'à la discrétion|discrétionnaire|au cas par cas|nach ermessen|im ermessen|einzelfall(?:prüfung)?|'
    r'по усмотрению|на усмотрение|в индивидуальном порядке|tùy theo|tùy thuộc vào quyết định|kebijaksanaan|'
    r'ดุลยพินิจ|เป็นรายกรณี',
    r'酌情|裁量|个案|個案|재량|사안별|個別に判断|ケースバイケース')
_DISCRETION_RE = re.compile(_words(*_DISCRETION_FORMS), re.I)
_EXCEPTION_RE = re.compile(_words(
    r'exceptional(?:ly)?|exceptions?|in special cases|special cases|' + _DISCRETION_FORMS[0] + '|'
    r'(?:may|can|could|might|shall) be (?:extended|prolonged|renewed)|extendable|extensions?|'
    r'excepcional(?:mente|es)?|casos especiales|a discreción|discrecional|podrá (?:ser )?prorrogar|prorrogable|prórroga|'
    r'exceptionnel(?:le|les|lement)?|cas particuliers|cas exceptionnels|à la discrétion|discrétionnaire|peut être prolongé|'
    r'prolongeable|prolongation|ausnahmsweise|in ausnahmefällen|ausnahmefall|im ermessen|nach ermessen|kann verlängert|'
    r'verlängerbar|verlängerung|ngoại lệ|trường hợp đặc biệt|tùy theo|có thể (?:được )?gia hạn|gia hạn|pengecualian|'
    r'kasus khusus|kebijaksanaan|dapat diperpanjang|perpanjangan|กรณีพิเศษ|ดุลยพินิจ|สามารถขยาย|ขยายได้|ต่ออายุ|'
    r'исключительн|в особых случаях|в исключительных|по усмотрению|может быть продл|продлеваем|продлени',
    r'例外|特殊情况|特殊情況|特别情况|特別情況|酌情|可延长|可延長|可以延长|可以延長|延期|特別な場合|特別の場合|裁量|'
    r'延長可能|延長することができ|延長できる|延長|예외|특별한 경우|재량|연장 가능|연장할 수|연장'), re.I)
_NORMALLY_RE = re.compile(_words(
    r'normally|usually|generally|typically|as a rule|in general|ordinarily|normalmente|generalmente|habitualmente|'
    r'por lo general|généralement|normalement|en général|habituellement|in der regel|normalerweise|üblicherweise|'
    r'thông thường|biasanya|umumnya|โดยปกติ|โดยทั่วไป|обычно|как правило',
    r'通常|一般|原則|原则|일반적으로|보통|원칙적으로'), re.I)
_PAST_RE = re.compile(_words(
    r'was valid|were valid|previously|no longer|prior to\s+(?:the\s+)?(?:\d|[A-Z][a-z]+\s+\d{4})|formerly|'
    r'used to (?:be|have|cost|require|allow|issue)|has expired|have expired|ceased|discontinued|abolished|repealed|'
    r'anteriormente|ya no|antes del? \d|dejó de|derogad[oa]|auparavant|ne (?:sont|est) plus|n[’\']est plus|avant le \d|'
    r'abrogé|früher|nicht mehr|vor dem \d|aufgehoben|außer kraft|trước đây|không còn|trước ngày|hết hiệu lực|bãi bỏ|'
    r'sebelumnya|tidak lagi|sebelum tanggal|dicabut|ก่อนหน้านี้|ก่อนวันที่|ยกเลิก|ранее|больше не|прежде|утратил силу|'
    r'отменен',
    r'此前|不再|曾经|曾經|已废止|已廢止|已取消|以前は|もはや|廃止|이전에|더 이상|종전|폐지') + '|' +
    r'(?<![^\W\d_])until\s+(?:the\s+)?(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-zÀ-ɏ]+\s+\d{4}|[A-Za-zÀ-ɏ]+\s+\d{1,2},?\s+\d{4}|'
    r'\d{4}(?![\d.,])|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})|hasta\s+el\s+\d|jusqu[’\']au\s+\d|bis\s+(?:zum\s+)?\d{1,2}\.|'
    r'đến\s+(?:ngày\s+)?\d{1,2}/\d|hingga\s+\d{1,2}\s+\w+\s+\d{4}|до\s+\d{1,2}\s+[а-яё]+\s+\d{4}|'
    r'\d{4}年\d{1,2}月\d{1,2}日\s*(?:まで|止|前|以前)|\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*까지', re.I)
_SUSPENSION_RE = re.compile(_words(
    r'suspended|suspension|temporarily unavailable|temporarily suspended|not currently (?:issued|available|accepted|offered)|'
    r'currently not (?:issued|available|accepted)|until further notice|(?:when|once|after|upon)\s+(?:it\s+)?(?:is\s+)?resum(?:ed|es|ption)|'
    r'resumption|halted|paused|suspendid[oa]s?|suspensión|temporalmente no|hasta nuevo aviso|suspendu(?:e|s|es)?|'
    r'temporairement indisponible|jusqu[’\']à nouvel ordre|ausgesetzt|aussetzung|vorübergehend nicht|bis auf weiteres|'
    r'tạm dừng|tạm ngừng|đình chỉ|tạm thời không|cho đến khi có thông báo mới|ditangguhkan|dihentikan sementara|'
    r'sementara tidak|hingga pemberitahuan lebih lanjut|ระงับ|ปิดชั่วคราว|งดชั่วคราว|งดให้บริการชั่วคราว|'
    r'จนกว่าจะมีประกาศ|приостановлен[аоы]?|приостановк|'
    r'временно не|до особого распоряжения|до дальнейшего уведомления',
    r'暂停|暫停|暂缓|暫緩|中止|停止受理|暂不|暫不|另行通知|一時停止|停止中|当面の間|再開|중단|일시 중지|잠정 중단|추후 공지|재개'), re.I)
# A cap or a hedge governs a figure. "Generally, a visitor visa may be
# valid for up to a maximum of 10 years, or until the expiry of your
# passport, whichever comes first" states a ceiling on a discretionary
# grant, not the validity a traveller receives, so the figure is refused as
# a value and, on the absence path, is not a stated value. The forms are
# the ones the captured corpus prints. A bare "up to" is how a fixed
# validity is commonly worded ("valid for up to 30 days") and is not a cap.
# The whole maximum family is a ceiling, whatever stands between the word
# and the figure: "a maximum period of 90 days", "the maximum validity of
# a tourist visa is 10 years", "issued for a maximum validity of", "valid
# for maximum 5 years", "90 days maximum", "max. 90 days", and the postfix
# "90 days or less". A ceiling is not the visa's validity.
_CAP_RE = re.compile(_words(
    r'up to a maximum of|a maximum of|maximum of|maximum|max\.|max(?=\s*\d)|at (?:the )?most|no more than|not more than|not (?:to )?exceed(?:ing|s)?|'
    r'or (?:less|fewer|shorter|under|below)|o menos|ou moins|oder weniger|atau kurang|hoặc ít hơn|'
    r'máxim[oa]s?|maximale?s?|massim[oa]|'
    # "as long as one year", "as much as 10 years" cap a figure. The same
    # words with no figure after them are a condition ("valid for 30 days
    # as long as your passport stays valid") and cap nothing.
    r'as (?:much|long|many) as(?=\s+(?:\d|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)(?![^\W_])))|'
    r'(?:shall|must|may|can|will|does|do|should) (?:not|never|in no case) exceed|cannot exceed|can not exceed|'
    r'exceed|surpass|go beyond|greater than|more than|longer than|less than|limited to|capped at|upper limit|at the longest|'
    r'no longer than|not longer than|'
    r'whichever (?:comes|is|occurs|happens) (?:first|earlier|sooner|shorter|the (?:earlier|sooner|shorter))|'
    r'au maximum|un maximum de|jusqu[’\']à un maximum|ne (?:peut|pourra|doit|devra) (?:pas )?(?:excéder|dépasser)|n[’\']excédant pas|'
    r'hasta un máximo de|un máximo de|como máximo|máximo de|no (?:será|sera|podrá|podra|puede|deberá|debera) (?:ser )?superior a|'
    r'no (?:podrá|podra|puede|deberá|debera) exceder|höchstens|maximal|nicht länger als|nicht mehr als|'
    r'paling lama|maksimal|maksimum|tidak lebih dari|tidak melebihi|tối đa|không quá|không vượt quá|'
    r'ไม่เกิน|สูงสุด|не более|не свыше|не дольше|не может превышать|не превыша|максимум|максимальн',
    r'最长|最長|最多|不超过|不得超过|不超過|不得超過|最大|を超えない|上限|최대|최장|초과하지'), re.I)
# A bare "up to" before a figure is a ceiling the destination may grant
# less than ("the visa may be valid for up to 10 years"). A cell may carry
# the ceiling as its own wording ("Up to 30 days") and a traveller then
# reads what the page says, so the fill is refused only when the stored
# value drops the wording and serves the figure flat. A stay is a maximum
# by definition, so this reads a validity alone.
_BARE_CAP_RE = re.compile('(?:' + _words(
    r'up to|up until|jusqu[’\']à|jusqu[’\']au|hasta|bis zu|fino a|até|sampai|hingga|lên đến|lên tới|ถึง|до',
    r'最多|最长|最長|까지') + r')\s*(?:a|an|the)?\s*(?=' + _FIGURE_FORMS + ')', re.I)
# "Non-extendable", "cannot be extended" state a firm rule, not an
# extension, so they are blanked before the exception vocabulary is read.
_NOT_EXTENDABLE_RE = re.compile(_words(
    r'non[- ]?(?:extendable|extendible|renewable|convertible|prolongeable|prorrogable|verlängerbar)|not (?:extendable|renewable|convertible)|'
    r'can ?not be (?:extended|prolonged|renewed)|may not be (?:extended|prolonged|renewed)|no extensions?|no extension is|'
    r'no (?:se )?(?:podrá|puede) prorrogar|no prorrogable|non prolongeable|nicht verlängerbar|không (?:được |thể )?gia hạn|'
    r'tidak dapat diperpanjang|tidak bisa diperpanjang',
    r'不可延长|不可延長|不得延长|不得延長|不能延长|不能延長|延長不可|延長できません|연장 불가|연장할 수 없'), re.I)
# A sentence whose subject is a pronoun or a pointer ("They must carry",
# "Such applicants") takes its subject from the sentence before it.
_ANAPHORA_RE = re.compile(
    r'^\W*(?:they|these|those|such|the (?:latter|former|above|aforementioned|same)|said|ils|elles|ceux-ci|celles-ci|'
    r'ces derni(?:ers|ères)|ellos|ellas|estos|estas|éstos|éstas|dichos|dichas|они|эти|такие|указанные|последние|'
    r'mereka|họ|những người này|diese|solche|letztere)(?![^\W_])', re.I)
# A stream named only after unless, except or other than is carved out of
# the rule, not the rule's subject.
_CARVED_RE = re.compile(_words(
    r'unless|except(?:ing)?|except for|other than|save for|but not|excluding|apart from|à moins (?:que|de)|sauf|hormis|'
    r'excepté|salvo|excepto|a menos que|a excepción de|außer|ausgenommen|mit ausnahme|kecuali|selain|trừ|ngoại trừ|'
    r'ยกเว้น|за исключением|кроме|исключая',
    r'除非|除了|除外|を除き|を除く|以外|제외|를 제외'), re.I)
# An explicit age threshold on a fee: "a partir de los 12 años", "aged 12
# and over". A threshold at or under the age of majority is the standard
# adult fee and travels into the note. Any other age scope is a concession.
_AGE_THRESHOLD_RE = re.compile(
    r'a partir de (?:los |las )?(\d{1,2}) años|à partir de (\d{1,2}) ans|from (?:the age of )?(\d{1,2})(?: years)?(?: of age)?(?: (?:and|or) (?:over|above|older|up(?:wards)?))?|'
    r'aged (\d{1,2}) (?:and|or) (?:over|above|older)|(\d{1,2}) years (?:of age )?(?:and|or) (?:over|above|older)|(?:over|above) (?:the age of )?(\d{1,2})|'
    r'mayores de (\d{1,2})|de más de (\d{1,2}) años|plus de (\d{1,2}) ans|über (\d{1,2}) jahren?|'
    r'ab (\d{1,2}) jahren|dari usia (\d{1,2})|berusia (\d{1,2}) tahun (?:ke atas|atau lebih)|từ (\d{1,2}) tuổi|старше (\d{1,2})|от (\d{1,2}) лет|'
    r'(\d{1,2})\s*(?:岁|歲|周岁)(?:及)?以上|(\d{1,2})\s*歳以上|(\d{1,2})\s*세 이상|อายุ\s*(\d{1,2})\s*ปีขึ้นไป', re.I)
_ADULT_AGE = 18
# The documents whose own duration a page states beside a visa's: a
# passport's remaining validity, a certificate's shelf life, an insurance
# policy's cover. A figure that follows one of these, with no visa word
# between, is that document's duration and never the visa's.
_DOCUMENT_SUBJECT_RE = re.compile(_words(
    r'passports?|travel documents?|passeports?|pasaportes?|paspor|hộ chiếu|certificates?|certificats?|certificados?|sertifikat|'
    r'chứng nhận|giấy chứng nhận|insurance|assurance|seguro|asuransi|bảo hiểm|polic(?:y|ies)|tickets?|billets?|billetes?|tiket|'
    r'photos?|photographs?|bank statements?|statements?|invitations?|letters?|forms?|receipts?|biometrics|tests?|résultats?|'
    # A residence or work permit, an identity card and a licence carry a
    # validity of their own that is never the served visa's.
    r'(?:residence|residency|work|employment|stay) permits?|(?:identity|id|residence|resident|green|credit|debit) cards?|cards?|'
    r'(?:driving|driver[’\']?s?) licen[cs]es?|licen[cs]es?|permis de séjour|permis de travail|cartes? de séjour|'
    r'permiso de residencia|permiso de trabajo|tarjeta de residencia|aufenthaltstitel|aufenthaltserlaubnis|arbeitserlaubnis|'
    r'результат|справк|сертификат|страхов|полис|билет|паспорт|приглашени|анкет|вид на жительство',
    r'护照|護照|パスポート|旅券|여권|证明|證明|証明|保险|保險|保険|보험|증명서|ประกัน|หนังสือเดินทาง|ใบรับรอง|居留证|居留證|在留カード|'
    r'외국인등록증|身份证|身份證'), re.I)
# A header cell that names the visa's validity column must not name a stay
# or a document: "Validity of Stay", "Stay Validity", "Validity (Duration
# of Stay)", "Passport Validity", "Residence Permit Validity" and "ID Card
# Validity" all carry a validity word and none of them is the visa's own.
_HEADER_NOT_VISA_RE = re.compile(_words(
    r'permits?|cards?|licen[cs]es?|passes?|residence|residency|work|employment|sojourn|admission|admitted|'
    r'entry stamp|stamp'), re.I)

# Requirement position. A document a traveller must present stands as the
# object of a requirement cue (present, submit, need, must carry, a
# "documents required" heading, a list marker) at the head of an
# enumeration piece. Anything inside a clause describing another party
# ("from a hosting agency or a hotel, which is registered with the
# Ministry and has a valid reference number") is that party's, never the
# applicant's document.
_REQUIREMENT_CUE_RE = re.compile(_words(
    r'documents? (?:required|needed|necessary|to (?:be )?(?:submit(?:ted)?|present(?:ed)?|provided?|upload(?:ed)?|attach(?:ed)?))|'
    r'(?:required|necessary|supporting|following) documents?|list of (?:required )?documents|checklist|documents? checklist|'
    r'(?:must|should|shall|need to|needs to|have to|has to|are required to|is required to|required to|will need to|will have to|'
    r'are (?:also )?requested to|is (?:also )?requested to|are expected to|are asked to)\s+(?:(?:also|then|always|still|first|only|either)\s+)?'
    r'(?:present|submit|upload|provide|carry|bring|hold|have|attach|enclose|produce|show|supply|furnish|possess|be in possession of|'
    r'be accompanied by|include|obtain)|'
    r'(?:you|applicants?|travell?ers?|visitors?|holders?|passengers?|persons?|nationals?|citizens?) (?:will |may |can |also )?(?:need|require)|'
    r'will need|needed|need|requires?d?|required|(?:please|kindly) (?:present|submit|upload|provide|attach|bring|enclose)|'
    r'to (?:hold|present|submit|upload|provide|carry|bring|show|produce|possess|have|attach|enclose|supply|furnish)|'
    r'present(?:ed|ing)?|submit(?:ted|ting)?|upload(?:ed|ing)?|attach(?:ed|ing)?|enclose[d]?|enclosing|'
    r'accompanied by|together with|along with|in possession of|'
    r'presentar|aportar|adjuntar|entregar|acompañar|disponer de|estar en posesión de|en posesión de|se requiere[n]?|'
    r'deberá(?:n)? (?:presentar|aportar|adjuntar|entregar|acompañar)|necesita(?:rá|rán|n)?|documentos? (?:necesarios|requeridos|exigidos|a presentar)|'
    r'présenter|fournir|joindre|munir|munis? de|produire|être en possession de|il faut|pièces (?:justificatives|à fournir|requises)|'
    r'vorlegen|einreichen|beifügen|mitbringen|benötigen|benötigt|erforderlich(?:e)? (?:unterlagen|dokumente)|unterlagen|'
    r'menyerahkan|melampirkan|membawa|menunjukkan|menyertakan|wajib|harus|diperlukan|dibutuhkan|persyaratan(?: dokumen)?|'
    r'syarat|dokumen yang (?:diperlukan|dibutuhkan|harus)|'
    r'nộp|xuất trình|mang theo|cần (?:có|nộp|xuất trình)|phải (?:có|nộp|xuất trình)|hồ sơ (?:gồm|bao gồm)|giấy tờ (?:cần|bao gồm)|'
    r'предостав(?:ить|ляет|ляются|ляемые|ляются)|представ(?:ить|ляет|ляются|ляемые)|прилож(?:ить|ены|ена)|подать|необходимо|'
    r'требуется|требуются|перечень документов|необходимые документы|документы,? необходимые',
    r'提交|提供|出示|携带|攜帶|需要|须|須|必须|必須|所需材料|所需文件|材料|提出|提示|必要書類|必要な書類|持参|'
    r'제출|제시|지참|필요|구비서류|ยื่น|แสดง|ต้องมี|เอกสารที่ต้อง|เอกสารประกอบ'), re.I)
# "Prove your identity with a valid passport", "acreditar la identidad con
# un documento de viaje": the document is the instrument of a proving verb.
_INSTRUMENT_CUE_RE = re.compile(
    r'(?<![^\W_])(?:prove|proving|proof of|demonstrate|demonstrating|justify|justifying|evidence|attest|establish|'
    r'acreditar|justificar|probar|demostrar|acreditando|prouver|justifier|attester|nachweisen|belegen|membuktikan|chứng minh)'
    r'(?![^\W_])[^.;:!?]{0,60}?(?<![^\W_])(?:with|by means of|by way of|using|through|con|mediante|avec|au moyen de|par|mit|durch|'
    r'dengan|bằng)(?![^\W_])', re.I)
# The separators between the pieces of an enumeration, and the words that
# open a clause about another party inside a piece.
_PIECE_SEP_RE = re.compile(
    r'[,;:，、；：]|(?<![^\W_])(?:and|or|et|ou|und|oder|или|dan|atau|serta|và|hoặc|hay)(?![^\W_])|(?<=\s)(?:y|e|o|u|и)(?=\s)|及|和|或|또는|및|และ|หรือ|'
    r'(?<=\s)(?:0\d|\d{1,2}[.)]|\(\d{1,2}\)|[a-z][.)])\s+(?=[^\W\d_])|(?<=\s)[-–—•*·▪■●○◦]\s+', re.I)
_TAIL_OPENER_RE = re.compile(_words(
    r'which|that|who|whom|whose|where|wherein|from|by|issued|registered|obtained|provided|certified|approved|recognised|recognized|'
    r'qui|que|dont|lequel|laquelle|lesquels|lesquelles|délivrée?s?|émise?s?|reconnue?s?|par|cual|cuales|cuyo|cuya|quien|quienes|'
    r'expedid[oa]s?|emitid[oa]s?|reconocid[oa]s?|por|welche[rs]?|ausgestellt|anerkannt|yang|dari|oleh|diterbitkan|dikeluarkan|'
    r'mà|do|từ|được cấp|котор|выданн|зарегистрирован|от|со стороны',
    r'由|所|签发|發給|颁发|に登録|が発行|から|에서 발급|ที่|ซึ่ง|จาก|โดย'), re.I)
_RELATIVE_START_RE = re.compile(
    r'^\W*(?:which|that|who|whom|whose|where|when|if|unless|provided|except|for those|for applicants|for persons|for visitors|'
    r'for residents|for holders|for citizens|for nationals|qui|que|dont|lequel|laquelle|lorsque|si|pour les|pour ceux|cual|cuales|'
    r'cuyo|cuya|quien|quienes|cuando|para los|para las|para quienes|welche[rs]?|der|die|das|wenn|falls|für|yang|jika|apabila|bagi|'
    r'untuk|mà|nếu|đối với|котор\w*|если|когда|для|ซึ่ง|ที่|หาก|สำหรับ)(?![^\W_])', re.I)
# A bare line that carries a finite or modal verb explains an item. It is
# not an item of the list itself ("Application form must be signed").
_BARE_VERB_RE = re.compile(_words(
    r'must|should|shall|may|can|will|would|is|are|was|were|be|has|have|had|do|does|need|needs|please|make sure|note|'
    r'deberá|deberán|debe|deben|puede|pueden|es|son|doit|doivent|peut|peuvent|est|sont|muss|müssen|kann|können|ist|sind|'
    r'harus|wajib|dapat|adalah|phải|có thể|là|должен|должны|может|могут|является',
    r'必须|必須|应当|應當|需要|可以|是|なければ|してください|해야|필요합니다|ต้อง'), re.I)
_LIST_MARKER_RE = re.compile(
    r'^\s*(?:[-–—•*·▪■●○◦]+|\(?\d{1,2}[.)]|\d{1,2}(?=\s\D)|\(?[a-z][.)]|[ivx]{1,4}[.)]|<[^>]{0,40}>|[①-⑳]|[•·]|[\[（(]\s*\d{1,2}\s*[\]）)])\s*', re.I)
_DETERMINER_RE = re.compile(
    r'(?:a|an|the|one|two|three|your|their|his|her|its|my|our|each|every|any|all|both|un|una|unos|unas|el|la|los|las|su|sus|'
    r'le|les|des|du|votre|vos|ein|eine|einen|einem|einer|der|die|das|ihr|ihre|ihren|một|các|những|sebuah|satu|dua|ваш|ваша|'
    r'ваше|ваши|один|одна|одну|две|два|copie|copia)(?![^\W_])\W*', re.I)
_ITEM_STOPWORDS = frozenset(
    'the and for with are was were can may must as at by of or to a an is in be up per your their its de del la el los las un una '
    'y o e du des le les et ou und der die das và của dan yang atau serta и или в на с для от из để cho'.split())
# An aside that opens with an inclusion cue names the documents the
# requirement itself asks for ("upload the required documents (including a
# valid passport)"), so its words stand in requirement position. Any other
# aside is a description and opens no head.
_INCLUSION_RE = re.compile(
    r'\s*(?:including|includes?|such as|e\.?g\.?|i\.?e\.?|namely|in particular|notably|inter alia|'
    r'incluyendo|incluid[oa]s?|por ejemplo|en particular|en concreto|y compris|notamment|par exemple|'
    r'einschließlich|insbesondere|z\.? ?b\.?|termasuk|antara lain|misalnya|bao gồm|ví dụ|trong đó có|'
    r'включая|в том числе|например|เช่น|รวมถึง|包括|包含|例如|など|포함|예를 들어)\s*[,:]?\s*', re.I)

# A delimited table row binds to its header. A reciprocity schedule prints
# "Visa Classification | Fee | Number of Entries | Validity Period" above
# "B-1/B-2 | None | Multiple | 120 Months": the entries cell is the entry
# statement of the class the first cell names, and the validity cell is
# its validity, whatever the row's words alone would say.
# The validity column has to name the visa's own validity. A "Duration of
# Stay" column states the stay a traveller is admitted for and a "Passport
# Validity" column states a document's remaining validity, so neither may
# take the validity slot.
_HEADER_KINDS = {
    'class': re.compile(r'classification|visa type|type of visa|\bclass\b|category|\bvisa\b', re.I),
    'entries': re.compile(r'number of entries|no\.? of entries|entries|entry', re.I),
    'validity': re.compile(r'validity|valid for|period of validity', re.I),
    'fee': re.compile(r'\bfees?\b', re.I),
}
# Class is the widest kind (a bare "visa" names it), so a header cell reads
# as the class column only when no narrower kind claims it first.
_HEADER_ORDER = ('entries', 'validity', 'fee', 'class')
# A reciprocity row carries its footnote as a marker after the class code or
# after the value, and the footnote is where the page qualifies the row.
# The extractor renders the marker however the page's markup falls: spaced
# ("60 Months 3"), glued ("60 Months3"), bracketed ("60 Months [3]",
# "60 Months (3)"), as a superscript glyph ("60 Months³") or as one or
# more symbols ("60 Months▲", "60 Months ▲◼"). Every rendering is a marker.
_MARKER_SYMBOLS = r'[▲△◼■□●○◆◇†‡*※§¶⁰¹²³⁴⁵⁶⁷⁸⁹]'
_ROW_MARKER_RE = re.compile(
    r'(?:(?P<spaced>[^\W_]\s+\d{1,2})|(?P<glued>[^\W\d_]\d{1,2})|(?P<bracket>\s*[\[(]\s*\d{1,2}\s*[\])])|'
    r'(?P<symbol>\s*' + _MARKER_SYMBOLS + r'+))\s*$')
_CELL_ENTRIES = {'multiple': 'multiple', 'multi': 'multiple', 'm': 'multiple', 'single': 'single', 's': 'single',
                 'one': 'single', '1': 'single', 'double': 'double', 'd': 'double', 'two': 'double', '2': 'double'}

# A restrictive stream of the product family (a group tour, a cruise, the
# electronic lane of a paper visa) is not the plain product. The families
# are allowed only when the served product's own type string carries them.
_RESTRICTIVE_FAMILIES = {
    'group': re.compile(_words(r'groups?|tour groups?|organi[sz]ed tours?|grupos?|groupes?|gruppen?|đoàn|theo đoàn|rombongan|กลุ่ม|คณะ|групп',
                               r'团队|團隊|团体|團體|団体|단체'), re.I),
    'cruise': re.compile(_words(r'cruises?|cruceros?|croisières?|kreuzfahrt(?:en)?|du thuyền|kapal pesiar|เรือสำราญ|круиз',
                                r'邮轮|郵輪|游轮|遊輪|クルーズ|크루즈'), re.I),
    'transit': _VISA_CLASSES['transit'],
    'electronic': re.compile(_words(r'e-?visas?|evisas?|electronic|électronique|electrónic[oa]s?|elektronisch(?:e|es|en)?|elektronik|'
                                    r'điện tử|อิเล็กทรอนิกส์|электронн|e-?ta', r'电子|電子|전자'), re.I),
    'paper': re.compile(_words(r'paper|sticker|regular visas?|ordinary visas?|conventional|non-?electronic|nonelektronik|giấy|kertas|'
                               r'бумажн|наклейк', r'纸质|紙質|纸本|紙本|贴纸|貼紙|贴付|貼付|シール|종이|스티커'), re.I),
}
_ELECTRONIC_DETAILS = frozenset({'evisa', 'evisa_on_arrival', 'eta_electronic_authorization'})
_RESTRICTIVE_PHRASES = re.compile(_words(
    r'issued to|for participants|participants? (?:of|in)|for holders of|to holders of|in the case of|on the invitation|'
    r'by invitation|invitation-based|délivrés? aux|expedid[oa]s? a|ausgestellt an|cấp cho|diterbitkan untuk|dành cho|ออกให้|'
    r'выда(?:ется|ются|ваемые) (?:только )?(?:для|лицам|участникам)|участник',
    r'发给|發給|签发给|簽發給|参加者|參加者|참가자|대상으로 발급'), re.I)

# The cue vocabulary of each field, for reading whether a page talks about
# the field at all, and the words that make a page's address or heading
# about the field.
_ENTRY_SUBJECT_WORDS = re.compile(_words(
    r'entr(?:y|ies)|number of entries|entrées?|entradas?|einreisen?|nhập cảnh|lần nhập|masuk|въезд|เข้าเมือง',
    r'入境次数|入境|出入境|入国回数|入国|입국|횟수'), re.I)
_BARE_ENTRY_WORDS = re.compile(_words(r'single|double|multiple|multi', r'一次|两次|兩次|多次|単次|数次|複数|단수|복수'), re.I)
_FIELD_CUES = {
    'fee': _FEE_WORDS, 'government_fee': _FEE_WORDS, 'validity': _VALIDITY_WORDS, 'max_stay_days': _STAY_WORDS,
    'permitted_stay_days': _STAY_WORDS, 'permitted_stay': _STAY_WORDS, 'entry': _ENTRY_SUBJECT_WORDS,
    'required_documents': _DOCUMENT_CUES, 'application_channel': _APPLY_WORDS,
}
_FIELD_URL_WORDS = {
    'fee': r'fees?|costs?|prices?|pricing|tarifs?|tarifas?|tasas?|derechos|precios?|gebuehr|gebühr|gebuehren|frais|droits|'
           r'le-?phi|lephi|phi|biaya|charges|费用|料金|手数料|수수료|ค่าธรรมเนียม',
    'validity': r'validity|valid|duration|period|有效|有効|유효',
    'max_stay_days': r'stay|duration|period|停留|滞在|체류|luu-tru|tinggal',
    'entry': r'entries|entry-type|number-of-entries|入境次数',
    'required_documents': r'documents?|requirements?|checklist|requisitos|documentos|pieces|dokumen|ho-so|giay-to|書類|材料|서류|เอกสาร',
    'application_channel': r'apply|application|how-to|申请|申請|신청|nop-ho-so|solicitud|demande|antrag',
}
_FIELD_URL_WORDS.update(government_fee=_FIELD_URL_WORDS['fee'], permitted_stay=_FIELD_URL_WORDS['max_stay_days'],
                        permitted_stay_days=_FIELD_URL_WORDS['max_stay_days'])
_FIELD_NOUN = {'fee': 'fee', 'government_fee': 'fee', 'validity': 'validity', 'max_stay_days': 'stay',
               'permitted_stay_days': 'stay', 'permitted_stay': 'stay', 'entry': 'entries',
               'required_documents': 'documents', 'application_channel': 'application'}


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
    """The served row index of the product: tstation projects only the
    products that carry a type, so the index is counted over those."""
    product = _product_named(products, name, label)
    typed = [p for p in products if isinstance(p, dict) and p.get('type')]
    return next(i for i, p in enumerate(typed) if p is product)


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
    return re.compile(_FIGURE_L + '(?:' + '|'.join(forms) + ')' + _FIGURE_TAIL + '(?:' + units + ')', re.I)


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


def _figure_of(text):
    """The number a figure-with-unit match writes: digits when present,
    else the number word."""
    digits = re.search(r'\d+(?:\.\d+)?', text)
    if digits:
        return float(digits.group())
    low = text.lower()
    for n, word in _NUMBER_WORDS.items():
        if re.match(word, low):
            return float(n)
    return None


def _served_names(products, merged, product_type=None):
    """The names a sentence can bind a figure to: the served product types
    and the route's visa category, the fill's own product first."""
    names = [product_type] if product_type else []
    names += [p.get('type') for p in (products or []) if isinstance(p, dict) and p.get('type')]
    category = merged.get('visa_category') if isinstance(merged, dict) else None
    if isinstance(category, str) and category.strip():
        names.append(category)
    seen, out = set(), []
    for name in names:
        key = _norm(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _name_spans(name, sentence):
    pattern = r'\s+'.join(re.escape(w) for w in str(name).split())
    return [(m.start(), m.end()) for m in re.finditer(pattern, sentence, re.I)]


# Product anchors. Ellis names a served product with a synthesised label
# that no government page prints, so a quote binds to a product through
# the anchors its label carries. Each family pairs the pattern that finds
# it in a label with the forms the official pages of the served
# destinations print. A family word alone ("tourist") is a stream anchor;
# the family noun ("tourist visa") is a second, sharper anchor.
_ANCHOR_FAMILIES = {
    'visa on arrival': (
        r'visas? on arrival|visa-on-arrival|arrival visas?|\bvoa\b|\be-?voa\b',
        re.compile(_words(r'visas? (?:on|upon) arrival|visa-on-arrival|arrival visas?|e-?voa|voa|visas? à l[’\']arrivée|'
                          r'visados? a la llegada|visa al arribo|visum bei (?:der )?ankunft|thị thực tại cửa khẩu|visa kedatangan',
                          r'落地签|落地簽|到着ビザ|到着査証|도착비자|도착 비자|виз\w* по прибыти|วีซ่าหน้าด่าน|วีซ่า ณ ช่องทาง'), re.I)),
    'unified electronic visa': (
        r'unified (?:electronic |e-?)visas?',
        re.compile(_words(r'unified (?:electronic |e-?)visas?|unified e-?visas?',
                          r'един(?:ая|ой|ую) электронн(?:ая|ой|ую) виз\w*|ЕЭВ|统一电子签证|統一電子簽證'), re.I)),
    'electronic visa': (
        r'e-?visas?|evisas?|electronic visas?|electronic (?:[a-z]+ )?visas?|e-[a-z]+ visas?|\be-?voa\b',
        re.compile(_words(r'e-?visas?|evisas?|electronic visas?|electronic (?:[a-z]+ )?visas?|online visas?|e-?voa|e-[a-z]+ visas?|'
                          r'visas? électroniques?|visados? electrónicos?|visa elektronik|thị thực điện tử',
                          r'电子签证|電子簽證|电子签|電子簽|電子ビザ|電子査証|전자비자|전자 비자|электронн\w* виз\w*|วีซ่าอิเล็กทรอนิกส์'), re.I)),
    'travel authorisation': (
        r'travel authori[sz]ation|travel authority|electronic travel|\be-?tas?\b|\bk-?eta\b|\bnz ?eta\b|\besta\b',
        re.compile(_words(r'electronic travel authori[sz]ations?|electronic travel authority|travel authori[sz]ations?|'
                          r'electronic authori[sz]ations?|autorisations? de voyage électroniques?|'
                          r'autorizaci(?:ón|ones) (?:electrónica )?de viaje|otorisasi perjalanan',
                          r'电子旅行授权|電子旅行授權|电子旅行许可|電子旅行許可|电子旅行认证|電子旅行認證|전자여행허가|'
                          r'электронн\w* разрешени\w* на (?:въезд|поездку)'), re.I)),
    'tourist visa': (
        r'tourist visas?|tourism visas?|touristic visas?|tourist e-?visas?|e-?tourist visas?|visas? for tourism|旅游签证|旅遊簽證|観光ビザ|観光査証|관광비자',
        re.compile(_words(r'tourist visas?|tourism visas?|touristic visas?|visas? for tourism|visas? for tourists?|tourist e-?visas?|'
                          r'e-?tourist visas?|visados? de turismo|visados? de turista|visados? turísticos?|visas? de turismo|'
                          r'visas? (?:de )?tourisme|visas? touristiques?|touristenvis(?:um|a|en)|visa turis|visa wisata|thị thực du lịch|'
                          r'visa du lịch|วีซ่าท่องเที่ยว',
                          r'旅游签证|旅遊簽證|观光签证|觀光簽證|観光ビザ|観光査証|관광비자|관광 비자|туристическ\w* виз\w*|виз\w* для туризма'), re.I)),
    'visitor visa': (
        r'visitor visas?|visit visas?|visitor \(subclass|visitor subclass|temporary visitor|temporary resident visa|visitor\'?s visa',
        re.compile(_words(r'visitor visas?|visit visas?|visitor \(subclass \d+\)|visitor subclass \d+|temporary visitor(?:[’\']s)? visas?|'
                          r'temporary visitor|temporary resident visas?|visas? de visiteur|visados? de visitante|visitor[’\']s visas?',
                          r'访问签证|訪問簽證|探访签证|短期滞在|단기방문|방문비자|방문 비자|гостев\w* виз\w*'), re.I)),
    'student visa': (
        r'student visas?|study visas?|\bstudents?\b|\bstudy\b',
        re.compile(_words(r'student visas?|study visas?|visas? (?:de |pour )?étudiants?|visas? (?:de )?études|visados? de estudiantes?|'
                          r'visados? de estudios|studentenvis(?:um|a|en)|studienvis(?:um|a|en)|visa pelajar|thị thực du học',
                          r'留学签证|留學簽證|学生签证|學生簽證|留学ビザ|留学査証|유학비자|학생비자|студенческ\w* виз\w*|учебн\w* виз\w*'), re.I)),
    'business visa': (
        r'business visas?',
        re.compile(_words(r'business visas?|visas? d[’\']affaires|visados? de negocios|geschäftsvis(?:um|a|en)|visa bisnis|'
                          r'thị thực công tác|thị thực thương mại',
                          r'商务签证|商務簽證|商用ビザ|商用査証|비즈니스 비자|상용비자|делов\w* виз\w*|бизнес-виз\w*'), re.I)),
    'schengen': (r'schengen', re.compile(_words(r'schengen', r'шенген\w*|申根|シェンゲン|셰겐|쉥겐|เชงเก้น|เชงเกน'), re.I)),
    'travel permit': (
        r'travel permits?|\bpermits?\b',
        re.compile(_words(r'travel permits?|permits?', r'通行证|通行證|回乡证|回鄉證|허가증|разрешени\w* на въезд'), re.I)),
    'visa-free entry': (
        r'visa[- ]free|no visa|exempt',
        re.compile(_words(r'visa[- ]free|visa exemption|visa[- ]exempt|exempt(?:ed)? from (?:the )?visa|without a visa|'
                          r'no visa (?:is )?(?:required|needed)|sin visado|sans visa|visumfrei|miễn thị thực|bebas visa',
                          r'免签|免簽|ビザ免除|査証免除|무비자|비자 면제|безвизов\w*|без визы|ฟรีวีซ่า|ยกเว้นวีซ่า'), re.I)),
    'paper': (r'\bpaper\b|\bsticker\b|\bregular\b|\bordinary\b|\bconventional\b', _RESTRICTIVE_FAMILIES['paper']),
    'long-stay': (r'long[- ]stay|long[- ]term|\bvls\b',
                  re.compile(_words(r'long[- ]stay|long[- ]term|long séjour|larga duración|larga estancia|dài hạn',
                                    r'长期|長期|장기|долгосрочн\w*'), re.I)),
    'short-stay': (r'short[- ]stay|short[- ]term',
                   re.compile(_words(r'short[- ]stay|short[- ]term|court séjour|corta duración|estancia corta|ngắn hạn',
                                     r'短期|단기|краткосрочн\w*'), re.I)),
    'transit': (r'\btransit\b', _VISA_CLASSES['transit']),
    'medical': (r'\bmedical\b', _VISA_CLASSES['medical']),
    'group': (r'\bgroups?\b|团队|團隊|団体', _RESTRICTIVE_FAMILIES['group']),
    'individual': (r'\bindividual\b|个人|個人',
                   re.compile(_words(r'individuals?|individuel(?:le)?s?|individual(?:es)?', r'个人|個人|개인|индивидуальн\w*'), re.I)),
    'frequent traveller': (r'frequent travell?er', re.compile(_words(r'frequent travell?ers?', ''), re.I)),
    'approved destination status': (r'approved destination status|\bads\b',
                                    re.compile(_words(r'approved destination status|ADS', ''), re.I)),
    'high income': (r'high[- ]income|高收入', re.compile(_words(r'high[- ]income', r'高收入|高所得'), re.I)),
    'minor': (r'under (?:the age of )?\d+|\bchild(?:ren)?\b|\bminors?\b|未满|未滿|未満|미만',
              re.compile(_words(r'under (?:the age of )?\d+|below (?:the age of )?\d+|minors?|child(?:ren)?|aged under',
                                r'未满|未滿|未満|미만|младше|несовершеннолетн\w*'), re.I)),
    'adult': (r'\d+ (?:and|or) (?:over|above|older)|\badults?\b|aged \d+|\d+周岁(?:及)?以上|\d+세 이상',
              re.compile(_words(r'\d+ (?:years )?(?:and|or) (?:over|above|older)|adults?|aged \d+ (?:and|or) (?:over|above)',
                                r'成年|成人|\d+周岁(?:及)?以上|\d+세 이상|старше \d+|совершеннолетн\w*'), re.I)),
}
# Stream words: the family word on its own, in the served languages.
_ANCHOR_STREAMS = {
    'tourist': (r'tourists?|tourism|touristic|旅游|旅遊|观光|觀光|観光|관광',
                re.compile(_words(r'tourists?|tourism|touristic|turismo|turistas?|turístic[oa]s?|tourisme|touristiques?|touristen|'
                                  r'touristisch|du lịch|wisata|turis|ท่องเที่ยว',
                                  r'турист\w*|туристическ\w*|旅游|旅遊|观光|觀光|観光|관광'), re.I)),
    'business': (r'\bbusiness\b|商务|商務|商用', _VISA_CLASSES['business']),
    'family': (r'\bfamily\b|\bfamilies\b|\brelatives\b|探亲|探親|家族', _VISA_CLASSES['family']),
}
# The families a served requirement detail implies whatever the label says.
_DETAIL_FAMILIES = {
    'evisa': ('electronic visa',), 'evisa_on_arrival': ('electronic visa', 'visa on arrival'),
    'paper_visa_on_arrival': ('visa on arrival',), 'eta_electronic_authorization': ('travel authorisation',),
    'paper_visa': ('paper',), 'conditional_visa_free': ('visa-free entry',), 'unconditional_visa_free': ('visa-free entry',),
}
_ANCHOR_TYPE_RES = {name: re.compile(pattern, re.I) for name, (pattern, _) in {**_ANCHOR_FAMILIES, **_ANCHOR_STREAMS}.items()}
_ANCHOR_PAGE_RES = {name: page for name, (_, page) in {**_ANCHOR_FAMILIES, **_ANCHOR_STREAMS}.items()}
_SUBCLASS_RE = re.compile(r'(?:sub)?class\s*(\d{2,4})', re.I)
_DURATION_TOKEN_RE = re.compile(r'^\d+[-‐–]?(?:days?|months?|years?|weeks?|hours?)$', re.I)
_VISA_TOKEN_RE = re.compile(r'^e?-?visas?$|^evisas?$', re.I)
_CODE_JOIN = r'[-‐–\s/]?'
# The words of a product label that name nothing on their own: the
# vocabulary the anchors already read, and the little words of a label.
_LABEL_STOPWORDS = frozenset(
    'visa visas visado visto visum stream the and for with apply applied applying application applications outside inside '
    'from via per entry entries single double multiple multi tourist tourism business family electronic travel authorization '
    'authorisation authority visitor visitors visit student students study permit permits subclass class type day days month '
    'months year years week weeks hour hours arrival paper sticker ordinary regular short long stay term applicants applicant '
    'holders holder over under required not non available eligible eligibility only all any other than more less through '
    'without free needed need country countries national nationals citizen citizens'.split())


def _label_tokens(label):
    """The tokens of a product label, without the brackets and list marks."""
    return [t.strip('.,;:/-‐–') for t in re.split(r'[\s(),;:—–…]+', str(label or '')) if t.strip('.,;:/-‐–')]


def _country_words():
    """Every alias of every known nationality, so a label's country word
    (JAPAN eVISA) is never mistaken for a class code."""
    if not hasattr(_country_words, 'cache'):
        _country_words.cache = frozenset(a for code in _known_nationalities() for a in _aliases(code))
    return _country_words.cache


def _is_code(token):
    """Whether a label token is a class code: letters beside digits (B-2,
    e-T2V) or an acronym (ETA, eTAS, K-ETA, VLS-TS), never a duration, a
    visa word or a country name."""
    if len(token) > 12 or _DURATION_TOKEN_RE.match(token) or _VISA_TOKEN_RE.match(token) or token.isdigit():
        return False
    if _norm(token) in _country_words():
        return False
    letters = [ch for ch in token if ch.isalpha()]
    if not letters:
        return False
    if any(ch.isdigit() for ch in token) or re.match(r'^e-?[A-Z][A-Za-z]{2,}$', token):
        return True
    upper = sum(1 for ch in letters if ch.isupper())
    lower = len(letters) - upper
    return upper >= 2 and lower <= 1 and len(token) <= 8


def _code_key(code):
    return re.sub(r'[^0-9a-z]', '', code.lower())


def _code_pattern(code):
    """The code as pages print it: its runs of letters and digits, joined
    with an optional hyphen, space or slash, as written or all in capitals.
    Codes keep their case: ESTA is not the Spanish word esta."""
    runs = re.findall(r'[A-Za-z]+|\d+', code)
    written = _CODE_JOIN.join(re.escape(r) for r in runs)
    capitals = _CODE_JOIN.join(re.escape(r.upper()) for r in runs)
    return re.compile(r'(?<![A-Za-z0-9])(?:' + written + '|' + capitals + r')(?![A-Za-z0-9])')


def _letter_code_pattern(letter):
    """A one-letter class (China's L visa) counts only beside a visa word or
    in brackets: "L visa", "visa (L)", "L字签证", "(L)"."""
    return re.compile(r'(?<![A-Za-z])' + letter + r'(?![A-Za-z])(?=[\s\-]?(?:[Vv]isa|签证|簽證|字签证|字簽證|类|類|型))|'
                      r'(?<=[Vv]isa[\s(（])' + letter + r'(?![A-Za-z])|(?<=[(（])' + letter + r'(?=[)）])')


def _product_anchors(product):
    """The anchors of a served product, keyed so two products share an
    anchor exactly when they share its meaning: (label, page pattern)."""
    label = str(product.get('type') or '')
    anchors = {}
    for m in _SUBCLASS_RE.finditer(label):
        anchors[('subclass', m.group(1))] = ('subclass ' + m.group(1),
                                            re.compile(r'(?:sub)?class\s*' + m.group(1) + r'(?!\d)', re.I))
    tokens = _label_tokens(label)
    skip = False
    for i, token in enumerate(tokens):
        if skip:
            skip = False
            continue
        # A code split at a space ("e-T2 V") is the code written together.
        if _is_code(token) and i + 1 < len(tokens) and len(tokens[i + 1]) == 1 and tokens[i + 1].isupper():
            token, skip = token + tokens[i + 1], True
        parts = [token] + ([p for p in token.split('/') if p] if '/' in token else [])
        for part in parts:
            if len(part) == 1 and part.isupper() and part not in 'AI':
                anchors[('code', part.lower())] = (part, _letter_code_pattern(part))
            elif _is_code(part):
                anchors[('code', _code_key(part))] = (part, _code_pattern(part))
    families = {name for name, pattern in _ANCHOR_TYPE_RES.items() if pattern.search(label)}
    families.update(_DETAIL_FAMILIES.get(str(product.get('requirement_detail') or ''), ()))
    for name in sorted(families):
        anchors[('family', name)] = (name, _ANCHOR_PAGE_RES[name])
    entries = _entry_types(label, qualifiers=True)
    cell = str(product.get('entry') or '').strip().lower()
    if cell in ENTRIES:
        entries.add(cell)
    for kind in sorted(entries):
        anchors[('entry', kind)] = (kind + ' entry', re.compile(_ENTRY_STATEMENTS[kind] + '|' + _ENTRY_QUALIFIERS[kind], re.I))
    for unit in _UNIT_WORDS:
        for m in _UNIT_FIGURE_RES[unit].finditer(label):
            n = _figure_of(m.group())
            if n is not None:
                shown = int(n) if n == int(n) else n
                anchors[('duration', n, unit)] = ('%s %s' % (shown, unit.lower() + ('s' if shown != 1 else '')), _figure_re(n, unit))
    return anchors


def _anchors_in(text, anchors):
    """The keys of the anchors that stand in this text."""
    # US tables abbreviate a shared class prefix, e.g. B-1/2. Read both
    # codes for conflict detection without treating the slash as prose.
    expanded = re.sub(r'\b([A-Z]{1,3})-?([1-9])((?:/[1-9])+)(?![A-Za-z0-9])',
                      lambda m: m[1] + '-' + m[2] + ''.join('/' + m[1] + '-' + n for n in m[3].split('/')[1:]), text)
    return {key for key, (_, pattern) in anchors.items() if pattern.search(expanded if key[0] == 'code' else text)}


def _anchor_spans(text, anchors):
    return [(m.start(), m.end()) for _, pattern in anchors.values() for m in pattern.finditer(text)]


def _anchor_labels(anchors, keys=None):
    return ', '.join(anchors[k][0] for k in anchors if keys is None or k in keys)


def _siblings(product, products):
    return [p for p in (products or []) if isinstance(p, dict) and p.get('type') and p is not product
            and p.get('type') != product.get('type')]


def _label_words(label):
    """The words of a label that can tell it from a sibling's."""
    words = set()
    for token in re.findall(r'[^\W_]+', _norm(label)):
        if token in _LABEL_STOPWORDS or token.isdigit():
            continue
        if len(token) >= 3 or not _spaced_script(token):
            words.add(token)
    return words


def _word_in(word, sentence):
    return bool(re.search(_alias_pattern(word), _norm(sentence)))


def _free_spans(label, sentence, labels):
    """Where this label stands in the sentence outside any longer label of
    the same route."""
    covering = [span for other in labels if len(other) > len(label) for span in _name_spans(other, sentence)]
    return [(a, b) for a, b in _name_spans(label, sentence) if not any(x <= a and b <= y for x, y in covering)]


def _mentions(text, product):
    """Whether a page mentions the product, by its label or any anchor."""
    from app.visa_snapshot.evidence_validator import quote_in_text
    return quote_in_text(product['type'], text) or bool(_anchors_in(text, _product_anchors(product)))


def _binding_problem(sentence, product, products):
    """Why this sentence does not bind to the product among the route's
    served products, or None when it does. The sentence must carry an
    anchor of the target and no anchor a sibling has that the target lacks;
    against a sibling sharing every anchor the sentence carries, it must
    carry a word of the target's label that sibling's label lacks; and a
    sentence with no anchor at all binds only on a one-product route."""
    target = _product_anchors(product)
    siblings = [(p, _product_anchors(p)) for p in _siblings(product, products)]
    # A label printed whole names its product, unless it stands inside a
    # longer sibling label ("Visa on Arrival (B1)" inside "Electronic Visa
    # on Arrival (B1)").
    labels = [product['type']] + [p['type'] for p, _ in siblings]
    mine = _free_spans(product['type'], sentence, labels)
    theirs = [p['type'] for p, _ in siblings if _free_spans(p['type'], sentence, labels)]
    if mine and theirs:
        return 'the sentence names several products (%s) and binds to none of them alone' % ', '.join([product['type']] + theirs)
    if mine:
        return None
    if theirs:
        return 'the sentence is about the sibling product %s (named), not %s' % (theirs[0], product['type'])
    every = dict(target)
    for _, anchors in siblings:
        every.update(anchors)
    found = _anchors_in(sentence, every)
    own = found & set(target)
    for sibling, anchors in siblings:
        foreign = found & (set(anchors) - set(target))
        if not foreign:
            continue
        if own - set(anchors):
            return 'the sentence names several products (%s, %s) and binds to none of them alone' % (product['type'], sibling['type'])
        return 'the sentence is about the sibling product %s (%s), not %s' % (
            sibling['type'], _anchor_labels(anchors, foreign), product['type'])
    if not found:
        if siblings:
            return 'the sentence carries no anchor of the product (%s) and the route serves %d products' % (
                _anchor_labels(target) or 'none', len(siblings) + 1)
        return None
    for sibling, anchors in siblings:
        if own - set(anchors):
            continue
        distinct = set(target) - set(anchors)
        if distinct:
            return 'the sentence carries only anchors the product shares with its sibling %s (%s) and none of its own (%s)' % (
                sibling['type'], _anchor_labels(target, own), _anchor_labels(target, distinct))
        words = _label_words(product['type']) - _label_words(sibling['type'])
        if not words:
            return 'the product shares every anchor with its sibling %s and no word of its name tells them apart' % sibling['type']
        if not any(_word_in(w, sentence) for w in words):
            return 'the sentence carries only anchors the product shares with its sibling %s (%s) and none of the words that tell them apart (%s)' % (
                sibling['type'], _anchor_labels(target, own), ', '.join(sorted(words)))
    return None


def _subjects(product, products):
    """The served products a sentence may be about: the fill's own product
    and its siblings, or every served product for a route-level fill."""
    typed = [p for p in (products or []) if isinstance(p, dict) and p.get('type')]
    if product is None:
        return typed
    return [product] + _siblings(product, products)


def _meaningful_anchors(product):
    """The anchors of a product that name it on their own. A bare stream
    word ("tourist" in "a tourist hotel reservation") carries no product
    meaning by itself and is left out."""
    return {k: v for k, v in _product_anchors(product).items() if not (k[0] == 'family' and k[1] in _ANCHOR_STREAMS)}


def _unanchored(sentence, product, products):
    """Whether nothing in the sentence says which product it is about: it
    prints no served product's label and carries no anchor of any of them
    beyond a bare stream word. On a one-product route such a sentence
    passes _binding_problem, and a route-level sentence binds to nothing
    at all, so its subject has to come from the section of the page it
    stands in."""
    subjects = _subjects(product, products)
    labels = [p['type'] for p in subjects]
    if any(_free_spans(label, sentence, labels) for label in labels):
        return False
    every = {}
    for p in subjects:
        every.update(_meaningful_anchors(p))
    return not _anchors_in(sentence, every)


# How far above a sentence the captured page is read for the subject of its
# section. A page that says nothing about its subject in this much text
# above a sentence has no section for it.
_SECTION_WINDOW = 6000
# A line the extractor marks as a heading (markdown or a tag it kept).
_HEADING_MARK_RE = re.compile(r'^\s*(?:#{1,6}\s+|<h[1-6][^>]*>)', re.I)
# A numbered line: "4. Fees", "(2) Documents", "II. Transit", "§ 3 Visa".
_NUMBERED_LINE_RE = re.compile(r'^\s*(?:\(?\d{1,2}(?:\.\d{1,2})*[.):]?|[IVX]{1,4}[.)]|[A-Za-z][.)]|[§#]\s*\d+)\s+\S')
# The phrases in which a class word names people, a time or a topic rather
# than a visa class ("GCC Residents", "working days", "business hours",
# "Treatment of personal data", "Press releases", "Diplomatic relations").
# A class word inside one of them does not make a heading that class's
# section.
_HEADING_SCOPE_RE = re.compile(_words(
    r'(?:GCC|EU|EEA|Schengen) residents?|working (?:days?|hours)|business (?:days?|hours)|treatment of|press (?:releases?|office|room)|'
    r'media (?:cent(?:re|er)|contacts?|enquiries|inquiries|releases?)|diplomatic (?:relations|missions?|corps|list|notes?)'), re.I)
# A sentence that mentions the served product to point away from it, so it
# says nothing about whose section it stands in: "Holders of a valid
# tourist visa are exempt", "unless you hold a tourist visa".
_CROSS_REFERENCE_RE = re.compile(_words(
    r'holders? of|holding|who holds?|if you (?:hold|have|already)|already (?:hold|have)|with a valid|exempt(?:ed|ion)?|'
    r'not required|no longer|instead of|rather than|unlike|as opposed to|except|unless|other than|see also|refer to|'
    r'can ?not|cannot|does not|do not|is not|are not|not (?:be )?(?:eligible|permitted|allowed)|'
    r'titulaires? d|sauf|à moins|dispensés?|exemptés?|titulares? de|salvo|a menos que|exentos?|inhaber|außer|befreit|'
    r'holders?|see above|back to|compare with|apply for|information desk|не требуется|no se necesita',
    r'除非|除了|持有|持旅游|免办|免除|제외|소지자|免除|を除き'), re.I)
_HEADING_PREDICATE_RE = re.compile(r'\b(?:must|shall|should|need|needs|are|is|have|has|will|can|may|costs?|issued|admitted|leaving|hold)\b', re.I)
_NEUTRAL_HEADING_RE = re.compile(r'(?:(?:required|supporting|application|visa) )?(?:documents?|requirements?|fees?|processing(?: time)?|how to apply|application procedure)', re.I)


def _heading_text(line):
    text = _HEADING_MARK_RE.sub('', line.strip())
    text = re.sub(r'</?h[1-6][^>]*>', '', text, flags=re.I)
    prefix = _NUMBERED_LINE_RE.match(text)
    if prefix:
        text = text[prefix.end() - 1:]
    return text.strip().rstrip(':：').strip()


def _reads_as_heading(line):
    """A heading has structural markup or a short noun phrase, never a
    short sentence merely because it contains six words or fewer."""
    text = line.strip()
    if not text:
        return False
    plain = _heading_text(text)
    if re.search(r'[.;,!?。；，！？]', plain) or _HEADING_PREDICATE_RE.search(plain):
        return False
    return bool(plain and (len(plain.split()) <= 12 or _HEADING_MARK_RE.match(text)))


def _sibling_named(piece, product, products):
    """Why this piece names a sibling of the product rather than the
    product, or None: it prints a sibling's label the product's does not
    cover, or carries a sibling anchor the product lacks."""
    if product is None:
        return None
    siblings = _siblings(product, products)
    if not siblings:
        return None
    labels = [product['type']] + [p['type'] for p in siblings]
    printed = [p['type'] for p in siblings if _free_spans(p['type'], piece, labels)]
    target = _product_anchors(product)
    for sibling in siblings:
        foreign = _anchors_in(piece, {k: v for k, v in _product_anchors(sibling).items() if k not in target})
        if foreign and sibling['type'] not in printed:
            printed.append(sibling['type'])
    if printed:
        return 'names the product %s the route serves beside %s' % (', '.join(printed), product['type'])
    return None


def _bare_class_problem(heading, route, product, products, merged):
    """Why this heading names a visa class the route does not serve by the
    class word alone: a section headed Transit, Student, Work, Business
    travellers or Crew and transit is that class's section whether or not
    the word visa stands beside it."""
    served = _families_served(_VISA_CLASSES, route, product, products, merged)
    for family, pattern in _VISA_CLASSES.items():
        if family in served:
            continue
        scoped = [(m.start(), m.end()) for m in _HEADING_SCOPE_RE.finditer(heading)]
        for m in pattern.finditer(heading):
            if any(a <= m.start() and m.end() <= b for a, b in scoped):
                continue
            return 'the section heading names a %s class the route does not serve' % family
    return None


def _names_served(piece, route, product, products, merged, heading):
    """Whether this piece says the text under it is about a served
    subject: it prints a served product's label, carries one of its
    anchors, or, in a heading, names a served class by the class word. A
    piece that mentions the product only to point away from it (a
    cross-reference: "Holders of a valid tourist visa are exempt", however
    short the line) says nothing about whose section it stands in."""
    subjects = _subjects(product, products)
    labels = [p['type'] for p in subjects]
    every = {}
    for p in subjects:
        every.update(_product_anchors(p) if heading else _meaningful_anchors(p))
    served = _families_served(_VISA_CLASSES, route, product, products, merged) if heading else ()
    for sentence in ([piece] if heading else _sentences(piece)):
        if _CROSS_REFERENCE_RE.search(sentence):
            continue
        if heading:
            title = _heading_text(sentence)
            if any(re.fullmatch(re.escape(label) + r'(?:\s+(?:requirements|documents|fees|processing))?', title, re.I) for label in labels):
                return True
            # A family word alone does not make "Private visit" or
            # "Tourist information desk" the tourist visa's section.
            continue
        if any(_free_spans(label, sentence, labels) for label in labels) or _anchors_in(sentence, every):
            return True
        if any(_VISA_CLASSES[family].search(sentence) for family in served):
            return True
    return False


def _piece_subject(piece, route, product, products, merged, heading):
    """What this piece of the page says about the subject of the text
    under it: ('foreign', reason) when it names a class or a product the
    fill is not about, ('served', None) when it names the fill's subject,
    or None when it says nothing either way."""
    problem = _class_problem(piece, route, product, products, merged) or _sibling_named(piece, product, products)
    if not problem and (heading or re.match(r'^(?:the )?(?:transit|passengers?|travell?ers?|students?|workers?|residents?|tourists?|journalists?)\b', piece, re.I)):
        problem = _bare_class_problem(piece, route, product, products, merged)
    if problem:
        return 'foreign', problem
    if _names_served(piece, route, product, products, merged, heading):
        return 'served', None
    return None


def _sentence_start(sentence, item, sources):
    """The captured page and the offset of this sentence in it, or (text,
    None) when the page does not print the quote."""
    page = (sources or {}).get(item.get('source_id')) if isinstance(sources, dict) else None
    text = str((page or {}).get('text') or '')
    quote = str(item.get('quote') or '')
    if not text or not quote:
        return text, None
    span = quote if quote in text else _page_span(quote, text)
    start = text.find(span) if span else -1
    if start < 0:
        return text, None
    inner = text.find(sentence.strip(), start, start + len(span) + 2)
    return text, (inner if inner >= 0 else start)


def _section_problem(sentence, item, sources, route, product, products, merged, cells=None):
    """Why the section of the page a sentence with no anchor stands in is
    not this fill's. The captured page is read upward from the sentence,
    line by line. The nearest heading that names a subject decides: a
    heading naming a class or a product the fill is not about refuses the
    fill, whether the heading carries a visa word ("Transit visa") or the
    class word alone ("Transit", "Student", "Work"), and a heading naming
    the fill's subject binds it. Any sentence between the fill's sentence
    and that heading that names a foreign class refuses the fill, so a
    cross-reference to the served product standing under a foreign heading
    never reopens the section. With no subject heading above it, the
    nearest sentence that names the served subject (and does not merely
    point away from it) binds the fill. A sentence whose section the page
    does not state binds only when the page as a whole is about the served
    subject, which it is when it names no other class at all. The gate
    runs for a route-level fill exactly as for a product fill, with every
    served product as its subject.

    A row read through its header says which class it is about in its own
    class cell, and _row_binding_problem reads that cell, so a row needs no
    section above it."""
    if cells is not None or not _unanchored(sentence, product, products):
        return None
    whose = product['type'] if product is not None else 'the served products'
    text, start = _sentence_start(sentence, item, sources)
    if start is not None:
        candidate = None
        above = text[max(0, start - _SECTION_WINDOW):start]
        for line in reversed(above.split('\n')):
            line = line.strip()
            if not line:
                continue
            heading = _reads_as_heading(line)
            found = _piece_subject(line, route, product, products, merged, heading)
            if found is None:
                if candidate is None and heading and not _NEUTRAL_HEADING_RE.fullmatch(_heading_text(line)) and not _CROSS_REFERENCE_RE.search(line):
                    return 'the sentence carries no anchor of %s and its page section (%s) is not an allowed neutral heading' % (whose, line[:80])
                continue
            kind, reason = found
            if kind == 'foreign':
                return 'the sentence carries no anchor of %s and its page section (%s) %s' % (whose, line[:80], reason)
            if heading or candidate is None:
                candidate = line
            # A plain text mention cannot reopen an earlier foreign section.
            # Explicit extractor/numbered headings preserve genuine siblings.
            if heading and (_HEADING_MARK_RE.match(line) or _NUMBERED_LINE_RE.match(line)):
                break
        if candidate is not None:
            return None
    title, lead = _page_title_and_lead(text)
    if any(_heading_names(title + '\n' + lead, p, products) for p in _subjects(product, products)):
        return None
    # Nothing above the sentence says whose section it is, so the whole page
    # has to be about the served subject. A page that names another visa
    # class anywhere may be stating that class's rule here.
    for piece in _sentences(text):
        problem = _class_problem(piece, route, product, products, merged)
        if problem:
            return ('the sentence carries no anchor of %s, the page states no section subject above it and '
                    'the page is not about %s alone (%s)' % (whose, whose, problem))
    return None


def _several_figures_problem(sentence, n, unit, product, products, merged):
    """The fee branch's several-amounts guard for durations. A statement
    that names several products, or several figures of the value's unit
    with the value not beside the product's own anchor, binds nothing."""
    from app.visa_snapshot.evidence_validator import quote_in_text
    typed = [p for p in (products or []) if isinstance(p, dict) and p.get('type')]
    anchors = {p['type']: _product_anchors(p) for p in typed}
    if product is not None:
        spans = _name_spans(product['type'], sentence) + _anchor_spans(sentence, anchors.get(product['type'], {}))
        anchor = product['type']
    else:
        named = []
        for p in typed:
            shared = set().union(*(set(a) for t, a in anchors.items() if t != p['type'])) if len(typed) > 1 else set()
            own = {k: v for k, v in anchors[p['type']].items() if k not in shared}
            if quote_in_text(p['type'], sentence) or _anchors_in(sentence, own):
                named.append(p)
        category = merged.get('visa_category') if isinstance(merged, dict) else None
        names = [p['type'] for p in named]
        if isinstance(category, str) and category.strip() and quote_in_text(category, sentence) and _norm(category) not in {_norm(x) for x in names}:
            names.append(category)
        if len(names) > 1:
            return 'the sentence names several products (%s), so its figure cannot be bound to one' % ', '.join(names)
        anchor = names[0] if names else None
        spans = _name_spans(anchor, sentence) if anchor else []
        if named:
            spans += _anchor_spans(sentence, anchors[named[0]['type']])
    figures = [(_figure_of(m.group()), m.start(), m.end()) for m in _UNIT_FIGURE_RES[unit].finditer(sentence)]
    distinct = {f[0] for f in figures if f[0] is not None}
    if len(distinct) <= 1:
        return None
    if not spans:
        return 'the sentence states several %s figures and names no product to bind this one to' % unit.lower()

    def distance(figure):
        _, start, end = figure
        return min(max(0, start - b, a - end) for a, b in spans)
    nearest = min(figures, key=distance)
    if nearest[0] != float(n):
        return 'the sentence states several %s figures and the one beside %s is %s, not %s' % (
            unit.lower(), anchor, int(nearest[0]) if nearest[0] == int(nearest[0]) else nearest[0], n)
    return None


# The little words that may stand between a qualifier and the noun it
# qualifies ("visa for students", "visa de estudiante", "citizens of the
# European Union", "holder of a diplomatic passport"), in the served
# languages, and the punctuation a list may put between them.
_LINK_WORDS = (r'(?:d[’\']|l[’\']|dell[’\']|(?:of|for|to|the|an?|or|and|de|des|du|del|della|dei|degli|di|per|para|pour|'
               r'à|au|aux|für|zum|zur|zu|von|der|die|das|den|dem|ein|eine|einen|einem|la|le|les|el|los|las|un|une|una|'
               r'uno|ou|et|y|o|u|e|und|oder|của|cho|và|hoặc|với|untuk|bagi|dan|atau|เพื่อ|สำหรับ|และ|หรือ|для|на|или|и|'
               r'с|type|types|category|categories|catégorie|categoría|kategorie|loại|jenis|ประเภท|тип|категори[яи]|'
               r'holders?|titulaires?|titulares?|inhaber|người|pemegang|ผู้ถือ|владельц(?:ы|ев|ам))' + _R + ')')
_LINK_GAP = r'[\s,/()（）、，\-‐–]*'


def _qualifies(sentence, qualifier, noun, extra=None, links=3, span=45):
    """Whether some match of `qualifier` stands right beside some match of
    `noun`, in either order, with nothing between them but link words,
    list punctuation and (when given) words of `extra`. A qualifier further
    away belongs to something else in the sentence."""
    between_re = re.compile('^' + _LINK_GAP + '(?:(?:' + _LINK_WORDS + ('|' + extra if extra else '') + ')' + _LINK_GAP
                            + '){0,%d}$' % links, re.I)
    nouns = [_whole_word(sentence, m) for m in noun.finditer(sentence)]
    for q in qualifier.finditer(sentence):
        q_start, q_end = _whole_word(sentence, q)
        for start, end in nouns:
            if q_end <= start:
                between = sentence[q_end:start]
            elif end <= q_start:
                between = sentence[end:q_start]
            else:
                continue
            if len(between) <= span and between_re.match(between):
                return True
    return False


def _whole_word(sentence, match):
    """The match widened to the whole word it sits in, so a stem written
    for an inflected language (служебн, транзит) covers its ending."""
    start, end = match.start(), match.end()
    while end < len(sentence) and re.match('[' + _LETTER + ']', sentence[end]):
        end += 1
    while start > 0 and re.match('[' + _LETTER + ']', sentence[start - 1]):
        start -= 1
    return start, end


def _names_place(window, code):
    """Whether a window names this country by any alias long enough to be
    a name rather than an ordinary word."""
    for alias in _aliases(code):
        if len(alias) < 4 and alias.isascii():
            continue
        if re.search(_alias_pattern(alias), _norm(window)):
            return True
    return False


_CLASS_WORDS_ALT = '|'.join('(?:%s)' % pattern.pattern for _, pattern in _PASSPORT_CLASSES)


def _scope_problem(sentence, route):
    """Why this sentence is scoped to another passport class, another bloc
    of nationalities or another country of residence, or None."""
    doc = route.get('travel_document_type') or 'ordinary_passport'
    for family, pattern in _PASSPORT_CLASSES:
        if family in doc:
            continue
        matches = list(pattern.finditer(sentence))
        if not matches:
            continue
        standalone = family in _STANDALONE_CLASSES or any('pass' in m.group().lower() for m in matches)
        # Diplomatic, service and official are ordinary words until a
        # passport word stands beside them ("diplomatic, official or
        # service passports", "passeport de service", "外交护照").
        if standalone or _qualifies(sentence, pattern, _PASSPORT_WORDS_WIDE, extra=_CLASS_WORDS_ALT, links=6):
            return 'the sentence is about holders of %s passports or documents, not the route\'s %s' % (family, doc)
    nationality = route.get('passport_nationality')
    for bloc, (words, tokens, members) in _BLOCS.items():
        if nationality in members:
            continue
        for pattern in (words, tokens):
            if pattern is None:
                continue
            if _qualifies(sentence, pattern, _BLOC_SUBJECT_WORDS, extra=_BLOC_LINK_WORDS.pattern, links=4):
                return 'the sentence is about nationals of the %s, and %s is not a member' % (bloc, nationality)
    home = route.get('lawful_country_of_residence')
    for m in _RESIDENCE_RE.finditer(sentence):
        window = sentence[max(0, m.start() - 30):m.start()] + ' ' + sentence[m.end():m.end() + 50]
        if home and _names_place(window, home):
            continue
        return 'the sentence is scoped to residents of another place, not %s' % (home or 'the route\'s own residence')
    return None


def _families_served(pattern_map, route, product, products, merged):
    """The families of pattern_map the route serves: named by a served
    product type or the visa category, or implied by the travel purpose."""
    names = _served_names(products, merged, product.get('type') if product is not None else None)
    served = set(_PURPOSE_FAMILIES.get(route.get('travel_purpose'), ()))
    for family, pattern in pattern_map.items():
        if any(pattern.search(name) for name in names):
            served.add(family)
    return served


_VISA_CLASS_ALT = '|'.join('(?:%s)' % pattern.pattern for pattern in _VISA_CLASSES.values())
_VISA_CLASS_LINKS = _VISA_CLASS_ALT + '|' + _words(
    r'entry|entries|single|double|multiple|multi|short|long|stay|term|purpose|purposes|corta|larga|duración|court|'
    r'long|séjour|kurz|lang|aufenthalt|ngắn hạn|dài hạn|jangka|ระยะสั้น|ระยะยาว|краткосрочн|долгосрочн',
    r'短期|长期|長期|단기|장기')
# German writes the class into the visa word (Geschäftsvisum, Arbeitsvisum).
_COMPOUND_VISA_RE = re.compile(r'vis(?:um|a|en)$', re.I)


def _class_problem(sentence, route, product, products, merged):
    """Why this sentence names a visa class the route does not serve. A
    class word counts when it stands beside a visa word ("business visa",
    "visa de negocios", "商务签证"), not anywhere in the sentence."""
    served = _families_served(_VISA_CLASSES, route, product, products, merged)
    for family, pattern in _VISA_CLASSES.items():
        if family in served:
            continue
        compound = any(_COMPOUND_VISA_RE.search(m.group()) for m in pattern.finditer(sentence))
        if compound or _qualifies(sentence, pattern, _CLASS_VISA_WORDS, extra=_VISA_CLASS_LINKS, links=5):
            return 'the sentence is about a %s visa class the route does not serve' % family
    return None


def _temporal_problem(sentence, unit=None):
    """Why this sentence states an exception, a closed period or a
    suspension rather than the current rule."""
    if _SUSPENSION_RE.search(sentence):
        return 'the sentence describes a suspended or resumed issuance, not the current rule'
    if _PAST_RE.search(sentence):
        return 'the sentence describes a past or closed period, not the current rule'
    # "Non-extendable" is a firm rule, not an extension.
    if _EXCEPTION_RE.search(_NOT_EXTENDABLE_RE.sub(' ', sentence)):
        return 'the sentence states an exception, a discretion or an extension beside the value'
    figures = {_figure_of(m.group()) for m in _DURATION_RE.finditer(sentence)}
    if _NORMALLY_RE.search(sentence) and len(figures - {None}) > 1:
        return 'the sentence states a normal figure beside a second figure, not one value'
    return None


def _governed_problem(sentence):
    """Why a duration in this sentence is governed by a hedge or a cap
    rather than stated: "Generally ... up to a maximum of 10 years ...
    whichever comes first" is a ceiling on a discretionary grant."""
    hedge = _NORMALLY_RE.search(sentence)
    cap = _CAP_RE.search(sentence)
    if hedge and cap:
        return 'the figure is governed by a hedge (%s) and a cap (%s), not stated as the value' % (hedge.group().strip(), cap.group().strip())
    if hedge:
        return 'the figure is governed by a hedge (%s), not stated as the value' % hedge.group().strip()
    if cap:
        return 'the figure is governed by a cap (%s), not stated as the value' % cap.group().strip()
    return None


def _bare_cap_problem(text, stated):
    """Why a bare "up to N" leaves this figure a ceiling rather than a
    validity. The ceiling may be served when the stored value carries the
    wording itself ("Up to 30 days"), never as a flat figure."""
    cap = _BARE_CAP_RE.search(text)
    if not cap or (isinstance(stated, str) and (_BARE_CAP_RE.search(stated) or _CAP_RE.search(stated))):
        return None
    return ('the figure is a ceiling the page states with "%s" and the stored value drops the wording, so it would be '
            'served as a flat validity' % cap.group().strip())


def _carved(sentence, match):
    """Whether this stream word is carved out of the rule by an unless,
    except or other-than connective standing before it in the same clause."""
    before = sentence[max(0, match.start() - 45):match.start()]
    before = re.split(r'[.;!?]', before)[-1]
    return bool(_CARVED_RE.search(before))


def _electronic(product):
    return bool(product) and (str(product.get('requirement_detail') or '') in _ELECTRONIC_DETAILS
                              or bool(_RESTRICTIVE_FAMILIES['electronic'].search(str(product.get('type') or ''))))


def _restrictive_problem(sentence, product, products):
    """Why this sentence's product mention carries a restrictive qualifier
    the served product type does not carry."""
    if _RESTRICTIVE_PHRASES.search(sentence):
        return 'the sentence restricts the product to a class of holders or participants'
    subjects = [product] if product is not None else [p for p in (products or []) if isinstance(p, dict) and p.get('type')]
    types = [str(p.get('type') or '') for p in subjects]
    for family, pattern in _RESTRICTIVE_FAMILIES.items():
        # A stream named only after unless or except is carved out of the
        # rule ("unless in transit to the Mainland"), not its subject.
        if not any(not _carved(sentence, m) for m in pattern.finditer(sentence)):
            continue
        if family == 'electronic':
            if any(_electronic(p) for p in subjects):
                continue
        elif family == 'paper':
            if subjects and not all(_electronic(p) for p in subjects):
                continue
        elif any(pattern.search(t) for t in types):
            continue
        return 'the sentence is about a %s stream the served product is not' % family
    return None


def _money_anywhere(text):
    """A money amount in running text or across table cells, or None."""
    for pattern in (_MONEY_RE, _CELL_MONEY_RE):
        m = pattern.search(text)
        if m:
            return m.group()
    for m in _BARE_MARKER_RE.finditer(text):
        after = text[m.end():m.end() + 600]
        cell = _NUMBER_CELL_RE.search(after)
        if cell:
            return m.group().strip() + ' ... ' + cell.group().strip()
        for line in after.splitlines():
            if len(_NUMBER_TOKEN_RE.findall(line)) >= 2:
                return m.group().strip() + ' ... ' + line.strip()
    return None


def _page_title_and_lead(text):
    """The first line of a capture and the first three prose lines of its
    opening: lines of fewer than four words are navigation crumbs, not
    headings. A fee question deep in a FAQ is not the page's subject."""
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    title = lines[0] if lines else ''
    prose = [line for line in lines[1:] if len(line.split()) >= 4]
    lead = ' '.join(prose[:3])[:300]
    return title, lead


def _about_field(page, field):
    """Whether this page's address, title or leading prose is about the field."""
    from urllib.parse import unquote, urlsplit
    parts = urlsplit(str(page.get('url') or ''))
    path = unquote(parts.path + ' ' + parts.query + ' ' + parts.fragment).lower()
    if re.search(r'(?<![a-z])(?:' + _FIELD_URL_WORDS[field] + r')(?![a-z])', path):
        return True
    title, lead = _page_title_and_lead(page.get('text'))
    cue = _FIELD_CUES[field]
    return bool(cue.search(title) or cue.search(lead))


def _heading_names(heading, product, products):
    """Whether a page heading is about the product: it names the product
    or carries one of its anchors, and no anchor of a sibling the product
    lacks."""
    from app.visa_snapshot.evidence_validator import quote_in_text
    if quote_in_text(product['type'], heading):
        return True
    target = _product_anchors(product)
    if not _anchors_in(heading, target):
        return False
    for sibling in _siblings(product, products):
        anchors = _product_anchors(sibling)
        if _anchors_in(heading, {k: v for k, v in anchors.items() if k not in target}):
            return False
    return True


def _field_page_id(proof, sources, field, product, products=None):
    """The first checked page that is the destination's page about this
    field for the product, or None. For a validity, stay or entry count the
    product's own page counts when its heading is about the product and the
    page talks about the field."""
    for sid in proof.get('source_ids') or []:
        page = sources.get(sid)
        if not page:
            continue
        text = page['text']
        if product is not None and not _mentions(text, product):
            continue
        if _about_field(page, field):
            return sid
        if field in ('validity', 'max_stay_days', 'permitted_stay_days', 'permitted_stay', 'entry') and product is not None:
            title, lead = _page_title_and_lead(text)
            if _heading_names(title + '\n' + lead, product, products) and _FIELD_CUES[field].search(text):
                return sid
    return None


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
    if _spaced_script(alias):
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


def _destination_as_subject(normalised, code):
    """Whether the destination's own people are the subject: a citizens,
    nationals or passport-holders word stands beside its name ("Canadian
    citizens, including dual citizens, need a valid Canadian passport"). A
    bare destination name is a place (a visa to Canada)."""
    for alias in _aliases(code):
        if len(alias) < 4 and alias.isascii():
            continue
        pattern = re.compile(_alias_pattern(alias), re.I)
        if pattern.search(normalised) and _qualifies(normalised, pattern, _BLOC_SUBJECT_WORDS, extra=_BLOC_LINK_WORDS.pattern, links=4):
            return True
    return False


def _foreign_subject(sentence, route):
    """The other nationalities a sentence is about, when it is not also about
    this route's nationality. The destination is a place, never a subject,
    until its own citizens, nationals or passport holders are named."""
    normalised = _norm(sentence)
    named = {code for code in _known_nationalities() if _names_nationality(normalised, code)}
    destination = route['destination_country']
    if destination in named and not _destination_as_subject(normalised, destination):
        named.discard(destination)
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
        codes = _resolved_codes(codes, sentence, destination)
        if codes is None:
            seen.append('the currency marker beside the amount is ambiguous and the sentence does not name it')
            continue
        if code in codes:
            # Two amounts in the value's own currency are a table row or a
            # choice (a standard and an express fee), never one fee.
            others = _amounts_priced_in(sentence, code, destination) - {_amount_key(m.group())}
            if others:
                return 'the sentence prices several %s amounts (%s), not one fee' % (code, ', '.join(sorted(others)))
            return None
        seen.append('the quote prices in %s, not %s' % ('/'.join(sorted(codes)), code))
    return seen[0] if seen else 'the amount is not in this sentence'


def _resolved_codes(codes, sentence, destination):
    """An ambiguous marker (a bare dollar sign) resolves only through a
    qualifier in the same sentence or the destination's own currency."""
    if len(codes) == 1:
        return codes
    qualified = set()
    for pattern, named in _MARKER_TABLE:
        if len(named) == 1 and pattern.search(sentence) and named <= codes:
            qualified |= named
    home = _HOME_CURRENCY.get(destination)
    if len(qualified) == 1:
        return qualified
    if home in codes and not qualified:
        return {home}
    return None


def _amount_key(token):
    return re.sub(r'[,.]00$', '', token).replace(',', '').replace('.', '').replace(' ', '')


def _amounts_priced_in(sentence, code, destination):
    """The distinct amounts the sentence writes beside a marker of this
    currency."""
    amounts = set()
    for m in _NUMBER_TOKEN_RE.finditer(sentence):
        codes = _adjacent_marker(sentence[max(0, m.start() - 24):m.start()], sentence[m.end():m.end() + 32])
        codes = _resolved_codes(codes, sentence, destination) if codes else None
        if codes and code in codes:
            amounts.add(_amount_key(m.group()))
    return amounts


# A sentence about the money an applicant must hold prices nothing.
_FUNDS_WORDS = re.compile(_words(
    r'funds|balance|income|deposit|savings|bank statements?|financial means|means of subsistence|sufficient means|'
    r'medios económicos|solvencia|ressources|moyens de subsistance|fondos|finanzielle mittel|tài chính|dana|'
    r'ทุนทรัพย์|средств к существованию|финансов\w* средств',
    r'收入|存款|资金|資金|預金|잔고|소득|재정'), re.I)


def _fee_sentence_problem(sentence, route, product, products, merged):
    if _SERVICE_WORDS.search(sentence):
        return 'the sentence prices a service, agency, centre or other non-government charge'
    if not (_FEE_WORDS.search(sentence) and _FEE_SUBJECT_WORDS.search(sentence)):
        return 'the sentence does not name a consular, government or visa fee'
    # A concession is somebody else's fee unless the product row is that
    # concession itself (a child visa, a group visa).
    names = _served_names(products, merged, product.get('type') if product is not None else None)
    own = {_concession_stem(m.group()) for name in names for m in _CONCESSION_RE.finditer(name)}
    for m in _CONCESSION_RE.finditer(sentence):
        word = m.group()
        if _concession_stem(word) in own or (own and _AGE_PHRASE_RE.fullmatch(word.strip())):
            continue
        return 'the sentence prices a concession or an eligibility class (%s), not the product\'s own fee' % word.strip()
    for pattern, after in ((_CITIZENS_THEN_COUNTRY_RE, True), (_COUNTRY_THEN_CITIZENS_RE, False)):
        for m in pattern.finditer(sentence):
            window = sentence[m.end():m.end() + 45] if after else sentence[max(0, m.start() - 24):m.start()]
            around = sentence[max(0, m.start() - 24):m.end() + 12]
            if _names_place(window, route['passport_nationality']) or _ANY_CITIZENS_RE.search(around):
                continue
            return 'the fee is stated for citizens of another country, not %s' % route['passport_nationality']
    if _TOTAL_RE.search(sentence) or _MULTIPLIED_RE.search(sentence):
        return 'the amount is a computed total, not the fee per application'
    if _FUNDS_WORDS.search(sentence):
        return 'the sentence is about the applicant\'s funds or income, not the fee'
    return _age_threshold_problem(sentence, names)


def _age_thresholds(sentence):
    return [int(g) for m in _AGE_THRESHOLD_RE.finditer(sentence) for g in m.groups() if g]


def _age_threshold_problem(sentence, names):
    """An explicit age threshold scopes the fee. At or under the age of
    majority ("a partir de los 12 años") it is the standard adult fee and
    is recorded in the note. Above it, or on a row that is itself an age
    class, the fee belongs to another applicant."""
    ages = _age_thresholds(sentence)
    if not ages:
        return None
    if any(_ANCHOR_TYPE_RES['minor'].search(n) or _ANCHOR_TYPE_RES['adult'].search(n) for n in names):
        return None
    if max(ages) > _ADULT_AGE:
        return 'the fee is stated for applicants aged %d and over, not the product\'s own fee' % max(ages)
    return None


def _age_scope_note(passages):
    """The age scope a fee quote states, for the stored note."""
    ages = _age_thresholds(passages)
    if not ages:
        return None
    return 'Fee stated for applicants aged %d and over.' % max(ages)


def _document_owns(before):
    """Whether the text before a figure makes it a document's duration: a
    passport, certificate or insurance word stands there with no visa word
    after it."""
    docs = list(_DOCUMENT_SUBJECT_RE.finditer(before))
    if not docs:
        return False
    visas = list(_VISA_WORDS.finditer(before))
    return not visas or visas[-1].start() < docs[-1].start()


def _figure_unit(match_text):
    for unit, pattern in _UNIT_FIGURE_RES.items():
        if pattern.fullmatch(match_text):
            return unit
    return None


def _owned_by_document(sentence, n, unit):
    """Whether every occurrence of the figure in this sentence is a
    document's duration (a passport valid for six months, a certificate
    valid for three months), never the visa's."""
    hits = list(_figure_re(n, unit).finditer(sentence))
    return bool(hits) and all(_document_owns(sentence[max(0, m.start() - 90):m.start()]) for m in hits)


def _duration_states(field, sentence):
    """Whether this sentence states a validity or a stay: some duration
    figure is bound to the field's own word, is not a document's duration,
    and is not governed by a hedge or a cap."""
    own, other = (_VALIDITY_WORDS, _STAY_WORDS) if field == 'validity' else (_STAY_WORDS, _VALIDITY_WORDS)
    if _governed_problem(sentence):
        return False
    for m in _DURATION_RE.finditer(sentence):
        n, unit = _figure_of(m.group()), _figure_unit(m.group())
        if n is None or unit is None:
            continue
        before = sentence[max(0, m.start() - 90):m.start()]
        owned = list(own.finditer(before))
        bound = bool(owned) and not other.search(before[owned[-1].end():])
        if not bound:
            after = sentence[m.end():m.end() + 16]
            owned_after = own.search(after)
            bound = bool(owned_after) and bool(re.fullmatch(r"[\s'’]*(?:of|de|di|d')?\s*", after[:owned_after.start()]))
        if bound and not _document_owns(before):
            return True
    return False


def _entry_states(sentence):
    """Whether this sentence states an entry type. A discretion sentence
    that names two types or none ("the officer has discretion to issue a
    single-entry or multiple entry visa") publishes no value."""
    stated = _entry_types(sentence)
    if not stated:
        return False
    return not (_DISCRETION_RE.search(sentence) and len(stated) != 1)


def _sentence_states(field, s):
    """Whether one sentence states a value for the field. A validity or a
    stay needs a figure bound to its own word that is no document's
    duration and is not governed by a hedge or a cap. An entry type needs
    a definite statement, never a discretion between two."""
    if field in ('fee', 'government_fee'):
        return bool(_FEE_WORDS.search(s) and _money_anywhere(s))
    if field == 'validity':
        return _duration_states('validity', s)
    if field in ('max_stay_days', 'permitted_stay_days', 'permitted_stay'):
        return _duration_states('stay', s)
    if field == 'entry':
        return _entry_states(s)
    if field == 'required_documents':
        return bool(_DOCUMENT_CUES.search(s) and _DOCUMENT_WORDS.search(s) and not _question(s))
    if field == 'application_channel':
        return bool(_APPLY_WORDS.search(s) and _CHANNEL_WORDS.search(s) and not _question(s))
    raise PatchRejected('no value detector exists for ' + str(field) + ', so it cannot be declared absent')


def _page_lines(text):
    return [line.strip() for line in str(text or '').splitlines() if line.strip()]


def _lines_state(lines, subject, states, span=1):
    """A value a page splits across adjacent lines: the field's subject word
    on one line and the figure on the same line or the next ("Validity |
    Entries" above "30 days | Single"). The lines are reported as they
    stand, never a splice of two distant sentences."""
    for i, line in enumerate(lines):
        if not subject.search(line):
            continue
        window = '\n'.join(lines[i:i + 1 + span])
        if states(window):
            return window.replace('\n', ' / ')
    return None


def _amount_cells(line):
    """The amounts a table line carries. A figure bound to a duration word
    is a column label ("30 days e-TV", "01 year e-TV"), never an amount, so
    a fee table's own header line is not mistaken for its first row."""
    return _NUMBER_TOKEN_RE.findall(_DURATION_RE.sub(' ', line))


def _fee_table_state(lines):
    """A fee table: a heading names the fee, a bare currency marker stands
    on the heading or within two lines of it ("(in US $)"), and rows of
    amounts follow within a dozen lines."""
    for i, line in enumerate(lines):
        if not _FEE_WORDS.search(line):
            continue
        for j in range(i, min(i + 3, len(lines))):
            if not _BARE_MARKER_RE.search(lines[j]):
                continue
            rows = lines[j + 1:j + 14]
            row = next((r for r in rows if _NUMBER_CELL_RE.search(r) or len(_amount_cells(r)) >= 3), None)
            if row is None:
                row = next((r for r in rows if len(_amount_cells(r)) >= 2), None)
            if row is not None:
                return ' / '.join(dict.fromkeys([line, lines[j], row]))
    return None


def _states_value(field, text):
    """The passage of a captured page that states a value for this field,
    or None. Conservative in the rejecting direction: any stated value,
    for any product, refuses an absence claimed over this page. A value a
    table splits across a header line and a row line still counts, so
    after the sentences the page is read line by line: the field's subject
    word with its figure on the same line or the next, or a fee heading
    with its currency marker and its rows of amounts."""
    text = str(text or '')
    for s in _sentences(text):
        if _sentence_states(field, s):
            return s
    lines = _page_lines(text)
    if field in ('fee', 'government_fee'):
        return (_lines_state(lines, _FEE_WORDS, lambda w: bool(_money_anywhere(w)))
                or _fee_table_state(lines))
    elif field == 'validity':
        return _lines_state(lines, _VALIDITY_WORDS, lambda w: _duration_states('validity', w))
    elif field in ('max_stay_days', 'permitted_stay_days', 'permitted_stay'):
        return _lines_state(lines, _STAY_WORDS, lambda w: _duration_states('stay', w))
    elif field == 'entry':
        return _lines_state(lines, _ENTRY_SUBJECT_WORDS,
                            lambda w: _entry_states(w) or bool(_BARE_ENTRY_WORDS.search(w) and not _DISCRETION_RE.search(w)))
    elif field == 'required_documents':
        return _list_states(text, _DOCUMENT_CUES, _DOCUMENT_WORDS)
    elif field == 'application_channel':
        return _list_states(text, _APPLY_WORDS, _CHANNEL_WORDS)
    return None


def _question(line):
    return line.rstrip().endswith(('?', '？'))


def _list_states(text, cue, words, span=5):
    """A value a page writes as a list: a line carrying the field's cue
    ("Required documents:") followed within a few lines by a line carrying
    a value word ("Passport valid for six months"). No single sentence
    states anything, and the page still publishes the value. A cue line
    that is a question ("What are the required documents?") is an index
    entry, not a list, and a follower that is itself a question is another
    entry."""
    lines = _page_lines(text)
    for i, line in enumerate(lines):
        if not cue.search(line) or _question(line):
            continue
        for follower in lines[i + 1:i + 1 + span]:
            if words.search(follower) and not _question(follower):
                return line[:80] + ' ... ' + follower[:80]
    return None


def _check_absence(proof, sources, route, field, value, label, product=None, products=None):
    """A documented absence names the captured destination pages it checked,
    carries its review date, its verifier and a reason, is refused when any
    named page states a value for the field, and must include the
    destination's own page about the field for the product, so an absence
    is never recorded without the page where the value would be published
    having been read."""
    if set(proof) - _ABSENCE_PROOF_KEYS:
        raise PatchRejected(label + ': extra instruction on the absence proof')
    if 'verifier' not in proof:
        raise PatchRejected(label + ': the absence must name its verifier')
    if value is not None and value != _EMPTY_FEE:
        raise PatchRejected(label + ': a documented absence carries no value')
    # The general batch's own absence rule: an empty value and a reason.
    _checked_proof(proof, sources, route, field, value, label)
    _review_date(proof, label)
    from app.visa_snapshot.evidence_validator import jurisdiction_matches
    ids = proof.get('source_ids')
    if (not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
            or any(not isinstance(i, str) or i not in sources for i in ids)):
        raise PatchRejected(label + ': the absence must list the captured page ids it checked')
    pages = [sources[i] for i in ids]
    if any(not jurisdiction_matches(page['url'], route['destination_country']) for page in pages):
        raise PatchRejected(label + ': every page an absence checked must be a captured destination-government page')
    if product is not None and not any(_mentions(page['text'], product) for page in pages):
        raise PatchRejected('%s: none of the pages checked mentions the product %s (by name or by an anchor: %s)' % (
            label, product['type'], _anchor_labels(_product_anchors(product)) or 'none'))
    for page in pages:
        stated = _states_value(field, page['text'])
        if stated:
            raise PatchRejected('%s: the absence names %s, which states a value: "%s"' % (label, page['url'], stated.strip()[:160]))
    noun = _FIELD_NOUN[field]
    if _field_page_id(proof, sources, field, product, products) is None:
        cued = [page['url'] for page in pages if _FIELD_CUES[field].search(page['text'])]
        if cued:
            raise PatchRejected('%s: %s carries the %s cue without a value, and none of the pages checked is the '
                                'destination\'s %s page for %s (by URL, title or leading text)' % (
                                    label, cued[0], noun, noun, product['type'] if product else 'the route'))
        raise PatchRejected('%s: none of the pages checked is the destination\'s %s page for %s (by URL, title or '
                            'leading text), so the absence has not read where the value would be published' % (
                                label, noun, product['type'] if product else 'the route'))
    if len(_absence_reason(proof, sources, field, product, products)) > _NOTE_LIMIT:
        raise PatchRejected(label + ': the absence reason and its page list exceed the %d characters the store keeps' % _NOTE_LIMIT)


def _absence_reason(proof, sources, field, product, products=None):
    """The stored reason: the reviewer's words, the review date, the pages
    checked and the id of the page about the field, so a stored absence
    says what was read, when, and where the value would have been."""
    urls = [sources[i]['url'] for i in proof['source_ids']]
    reason = str(proof.get('reason') or '').strip()
    return '%s Checked on %s: %s. %s page checked: %s.' % (
        reason, proof['verified_at'], ', '.join(urls), _FIELD_NOUN[field].capitalize(),
        _field_page_id(proof, sources, field, product, products))


def _cell_entry(cell):
    """The entry type a bare table cell states ("Multiple", "M", "Single")."""
    return _CELL_ENTRIES.get(_norm(cell))


def _row_key(line):
    """A row line as a lookup key, so the page's own spacing and the quote's
    find each other."""
    return re.sub(r'\s+', ' ', str(line or '')).strip()


def _row_context(proof, sources):
    """Each quote with the lines the captured page prints above it and the
    rest of its own last line, so a table row quoted on its own is still
    read against the column names the page gives it. The line is the page's,
    not the quote's, so a quote that stops before a footnote marker never
    reads as the row. A quote the page does not print stands alone."""
    out = []
    for item in proof['evidence']:
        quote = item['quote']
        page = (sources or {}).get(item.get('source_id')) or {}
        text = str(page.get('text') or '')
        span = quote if quote and quote in text else (_page_span(quote, text) if text else None)
        if not span:
            out.append(quote)
            continue
        start = text.find(span)
        head = max(0, start - 1200)
        stop = text.find('\n', start + len(span))
        if stop >= 0:
            # Include every following row of this same contiguous table.
            # A reviewer quoting its first row cannot hide conflicting rows
            # later in the table, while a separate section remains separate.
            while stop < len(text):
                end = text.find('\n', stop + 1)
                end = len(text) if end < 0 else end
                following = text[stop + 1:end]
                if following.count('|') < 2:
                    break
                stop = end
        window = text[head:len(text) if stop < 0 else stop]
        out.append(window[window.find('\n') + 1:] if head and '\n' in window else window)
    return '\n'.join(out)


def _delimited_rows(passages):
    """The rows of a delimited table in the quoted passages, keyed by the
    row line as _sentences yields it, each mapped to its header-named
    cells. A header may run over several physical lines ("Visa" /
    "Classification | Fee | Number" / "of Entries | Validity" / "Period");
    the lines above the first row are joined and read as one header. A run
    of rows counts only when a header names an entries or a validity
    column and has exactly the rows' number of cells. A header that names
    one kind in two of its cells says nothing about which of them holds
    the value, so its whole table is refused rather than read by
    position."""
    lines = str(passages or '').split('\n')
    rows = {}
    is_row = [line.count('|') >= 2 and not any(k.search(line) for k in (_HEADER_KINDS['entries'], _HEADER_KINDS['validity'])) for line in lines]
    i = 0
    while i < len(lines):
        if not is_row[i]:
            i += 1
            continue
        start = i
        while i < len(lines) and is_row[i]:
            i += 1
        run = lines[start:i]
        cells = [c.strip() for c in run[0].split('|')]
        head = []
        for j in range(start - 1, max(-1, start - 7), -1):
            if not lines[j].strip():
                break
            head.insert(0, lines[j].strip())
            joined = ' '.join(head)
            if joined.count('|') == len(cells) - 1:
                break
        joined = ' '.join(head)
        if joined.count('|') != len(cells) - 1:
            continue
        found = {}
        for index, cell in enumerate(c.strip() for c in joined.split('|')):
            for kind in _HEADER_ORDER:
                if not _HEADER_KINDS[kind].search(cell):
                    continue
                # A document's own validity is never the visa's, and a stay
                # column is a stay whatever else its name says, so a
                # "Passport Validity", "Validity of Stay" or "Residence
                # Permit Validity" cell takes no column at all.
                tokens = re.findall(r'\w+', cell.lower())
                if kind != 'validity' or ('validity' in tokens and set(tokens) <= {'visa', 'validity', 'of', 'the', 'duration', 'period'}):
                    found.setdefault(kind, []).append(index)
                break
        if any(len(indexes) > 1 for indexes in found.values()):
            continue
        columns = {kind: indexes[0] for kind, indexes in found.items()}
        if 'class' not in columns or not ({'entries', 'validity'} & set(columns)):
            continue
        for line in run:
            parts = [c.strip() for c in line.split('|')]
            if len(parts) != len(cells):
                continue
            rows[_row_key(line)] = {kind: parts[index] for kind, index in columns.items()}
    return rows


def _row_binding_problem(cells, product):
    """A header-bound row states values for the class its first cell
    names: that cell must carry a class-code anchor of the product."""
    codes = {k: v for k, v in _product_anchors(product).items() if k[0] in ('code', 'subclass')}
    if not codes or not _anchors_in(cells.get('class') or '', codes):
        return 'the row\'s class cell (%s) carries no class code of the product' % (cells.get('class') or '')
    return None


def _row_marker(cell, codes=None):
    """The footnote marker a bound cell ends in, or None. A trailing number
    that belongs to a money amount ("USD 10") is the amount itself and not
    a marker, so a priced cell is read as the page writes it. A digit glued
    to a letter is a marker after a unit word ("60 Months3"), and in a
    class cell when the cell without it ends in a code of the product
    while the cell itself does not ("B-1/B-23"): a code of its own ("B2",
    "H-1B") is read as the page writes it."""
    cell = str(cell or '').strip()
    digits = re.search(r'\d+\s*$', cell)
    if digits and any(m.start() <= digits.start() < m.end() for m in _MONEY_RE.finditer(cell)):
        return None
    marker = _ROW_MARKER_RE.search(cell)
    if marker is not None and marker.group('glued'):
        word = re.search(r'[^\W\d_]+$', cell[:marker.start() + 1])
        marker = marker if word and _ANY_UNIT_RE.fullmatch(word.group()) else None
    if marker is not None:
        return marker.group().strip()
    # A code cell whose trailing digits run past the product's own code
    # ("B-1/B-23", "B-212") carries a glued marker after the code.
    if codes and not _ends_in_code(cell, codes):
        for width in (1, 2):
            if re.search(r'\d$', cell[:-width] if len(cell) > width else '') and _ends_in_code(cell[:-width], codes) and cell[-width:].isdigit():
                return cell[-width:]
    return None


def _ends_in_code(text, codes):
    text = text.rstrip()
    return any(m.end() == len(text) for _, pattern in codes.values() for m in pattern.finditer(text))


def _row_marker_problem(cells, product=None):
    """Why a header-bound row cannot be read flat: one of its cells ends in
    a footnote marker, so the page qualifies the row somewhere else and the
    row alone does not state the value."""
    codes = {k: v for k, v in _product_anchors(product).items() if k[0] in ('code', 'subclass')} if product is not None else None
    for kind in sorted(cells):
        cell = str(cells.get(kind) or '')
        if _row_marker(cell, codes if kind == 'class' else None):
            return ('the row\'s %s cell (%s) ends in a footnote marker, so the page qualifies the row elsewhere and the '
                    'row alone does not state the value' % (kind, cell))
        if not _table_cell_supported(kind, cell):
            return ("the row's %s cell (%s) does not fully match its column grammar, so an extra qualifier or footnote "
                    "cannot be discarded" % (kind, cell))
    return None


_TABLE_CODE_RE = r'[A-Z]{1,3}-?[1-9][A-Z]?'
_TABLE_CLASS_RE = re.compile(_TABLE_CODE_RE + r'(?:(?:\s*[/,&]\s*|\s+and\s+)(?:' + _TABLE_CODE_RE + r'|[1-9]))*')
_TABLE_DURATION_RE = re.compile(_FIGURE_FORMS + r'\s*(?:' + _ANY_UNIT_RE.pattern + r')'
                                + r'(?:\s+from\s+(?:the\s+)?(?:date\s+of\s+)?issu(?:e|ance))?', re.I)


def _table_cell_supported(kind, cell):
    """Accept the entire known column grammar, never strip unknown suffixes
    that may be footnotes, stay restrictions or another permission's terms."""
    if kind == 'class':
        return bool(_TABLE_CLASS_RE.fullmatch(cell))
    if kind == 'validity':
        return bool(_TABLE_DURATION_RE.fullmatch(cell))
    if kind == 'entries':
        return _cell_entry(cell) is not None
    if kind == 'fee':
        return bool(re.fullmatch(r'None|Free|\d+(?:\.\d+)?', cell, re.I) or _MONEY_RE.fullmatch(cell))
    return False


def _row_conflict_problem(rows, cells, product, field):
    """Why a product cannot take this row: the table states more than one
    value for the rows the product's class codes match, so which of them
    the product is served under is the page's choice and not the
    reviewer's. A product with one code matches its own row and any
    combined row that carries the code ("B-2" and "B-1/B-2"), so the
    conflict is read from the matched rows, never from the count of the
    product's codes."""
    kind = {'validity': 'validity', 'entry': 'entries'}.get(field)
    codes = {k: v for k, v in _product_anchors(product).items() if k[0] in ('code', 'subclass')}
    if not cells or kind is None or kind not in cells or not codes:
        return None
    stated = {}
    for row in rows.values():
        if kind not in row or not _anchors_in(row.get('class') or '', codes):
            continue
        stated.setdefault(_norm(row[kind]), set()).add(_row_key(row.get('class') or ''))
    if len(stated) > 1:
        return ('the table states %d different %s values for the product\'s own class codes (%s), so this row does not '
                'state the product\'s value' % (len(stated), kind,
                                                '; '.join('%s for %s' % (value or 'nothing', ', '.join(sorted(classes)))
                                                          for value, classes in sorted(stated.items()))))
    return None


def _plain_heads(sentence):
    """The sentence in NFKC form with its parentheticals blanked to spaces,
    so a document named only in an aside ("(fax copy, click here to view
    the sample)") never stands at a head and positions stay aligned, and
    the spans of an inclusion aside that names the very documents the
    requirement asks for ("upload the required documents (including a
    valid passport)"). Those words stay and open a head of their own, and
    the span ends where the aside closes, so the text after the aside is
    read as the sentence's own again."""
    import unicodedata
    text = unicodedata.normalize('NFKC', str(sentence))
    out = list(text)
    depth, keeping, opened = 0, [], []
    for i, ch in enumerate(text):
        if ch in '(（[［':
            cue = _INCLUSION_RE.match(text, i + 1) if depth == 0 else None
            depth += 1
            keeping.append(bool(cue))
            if cue:
                opened.append([cue.end(), len(text)])
            out[i] = ' '
        elif ch in ')）]］':
            if keeping and keeping.pop() and opened:
                opened[-1][1] = i
            depth = max(0, depth - 1)
            out[i] = ' '
        elif depth and not (len(keeping) == 1 and keeping[0]):
            out[i] = ' '
    return ''.join(out), [(start, close) for start, close in opened]


def _plain(sentence):
    return _plain_heads(sentence)[0]


def _piece_heads(text, start, end):
    """The positions where enumeration pieces begin between start and end.
    A comma or a conjunction starts a new piece unless the piece so far
    has opened a clause about another party (from, by, which, issued) and
    no comma list stands before it in this region: "a confirmation from
    an agency or a hotel, which is registered ..." is one piece, "a
    passport, one photo, a confirmation from an operator and an insurance
    policy" is four. A piece that opens with a relative or conditional
    word always continues the piece before it."""
    heads = [start]
    piece_start, listed, open_tail, in_clause = start, 0, False, False
    for m in _PIECE_SEP_RE.finditer(text, start, end):
        if m.start() < piece_start:
            continue
        # A separator that follows another separator (", and") is the
        # same break, not an empty piece.
        if not re.search(r'[^\W_]', text[piece_start:m.start()]):
            if heads and heads[-1] == piece_start:
                heads[-1] = m.end()
                piece_start = m.end()
            continue
        open_tail = open_tail or bool(_TAIL_OPENER_RE.search(text[piece_start:m.start()]))
        follower = text[m.end():end]
        hard = m.group()[0] in ';；' or m.group().strip()[:1].isdigit() or m.group().strip()[:1] in '-–—•*·▪■●○◦'
        if not hard and _RELATIVE_START_RE.match(follower):
            # A relative or conditional clause runs on through its own
            # commas and conjunctions ("unless in transit to the Mainland
            # or the Macao Special Administrative Region").
            in_clause = True
            continue
        # After a clause about another party, a conjunction or a comma
        # resumes the list only when a comma list stands before it ("a
        # passport, one photo, a confirmation from an operator and an
        # insurance policy"), while "a confirmation from an agency or a hotel"
        # stays one piece.
        if not hard and (in_clause or (open_tail and listed == 0)):
            continue
        heads.append(m.end())
        if hard or m.group()[0] in ',，、':
            listed += 1
        piece_start, open_tail, in_clause = m.end(), False, False
    # A head with no word after it (a trailing "and", a heading's colon)
    # opens no piece.
    return [h for i, h in enumerate(heads)
            if re.search(r'[^\W_]', _PIECE_SEP_RE.sub(' ', text[h:heads[i + 1] if i + 1 < len(heads) else end]))]


# What the page offers the applicant rather than asks of them: a sample or
# a template, a page or a link, a service or an office, a fee. A piece of an
# inclusion aside naming one of these is not a document to present.
_OFFERED_RE = re.compile(_words(
    r'samples?|examples?|specimens?|templates?|models?|pages?|websites?|sites?|links?|help|guides?|guidance|instructions?|'
    r'faqs?|services?|couriers?|agenc(?:y|ies)|agents?|cent(?:re|er)s?|offices?|counters?|desks?|fees?|charges?|payments?|'
    r'muestras?|ejemplos?|plantillas?|modelos?|páginas?|paginas?|enlaces?|servicios?|mensajer[íi]a|agencias?|oficinas?|tasas?|'
    r'exemples?|modèles?|gabarits?|liens?|services?|agences?|bureaux?|frais|beispiele?|muster|vorlagen?|seiten?|links?|'
    r'dienste?|gebühren?|contoh|halaman|tautan|layanan|kantor|biaya|mẫu|trang|liên kết|dịch vụ|lệ phí|'
    r'образц|шаблон|страниц|ссылк|услуг|сбор',
    r'样本|樣本|范例|範例|示例|模板|页面|頁面|链接|連結|服务|服務|手数料|見本|샘플|예시|페이지|링크|서비스'), re.I)


# Document nouns the shared document vocabulary does not carry, because it
# reads whole requirement sentences and these words stand alone only as the
# name of a thing an applicant hands over.
_DOCUMENT_NOUNS_EXTRA = re.compile(_words(
    r'reservations?|contracts?|receipts?|statements?|polic(?:y|ies)|licen[cs]es?|permits?|registrations?|declarations?|'
    r'payslips?|cards?|residenc(?:y|e)|records?|reports?|transcripts?|deeds?|affidavits?|'
    r'reservas?|contratos?|recibos?|pólizas?|polizas?|r[ée]servations?|contrats?|re[çc]us?|'
    r'reservasi|kontrak|kuitansi|đặt chỗ|hợp đồng|бронирован|договор|квитанц|полис',
    r'预订|預訂|合同|合約|收据|收據|予約|契約|領収|예약|계약|영수증'), re.I)


def _reads_as_document(piece):
    """Whether a piece of an inclusion aside reads as a document the
    applicant presents: it carries a document word and names no sample,
    page, service or other thing the page merely offers."""
    if _OFFERED_RE.search(piece):
        return False
    return bool(_DOCUMENT_WORDS.search(piece) or _DOCUMENT_NOUNS_EXTRA.search(piece))


def _sentence_heads(sentence, bare_ok=False):
    """The NFKC form of a sentence and the positions where a document may
    stand in requirement position: the pieces after each requirement cue,
    after an inclusion aside's cue, after an opening list marker, or the
    head of a bare list line when the passage is a list."""
    import unicodedata
    plain, included = _plain_heads(sentence)
    text = unicodedata.normalize('NFKC', str(sentence))
    cues = sorted({m.end() for m in _REQUIREMENT_CUE_RE.finditer(plain)} | {m.end() for m in _INSTRUMENT_CUE_RE.finditer(plain)})
    marker = _LIST_MARKER_RE.match(plain)
    starts = []
    if marker and marker.end() > 0:
        starts.append(marker.end())
    elif not cues and bare_ok and len(plain.split()) <= 12 and not _BARE_VERB_RE.search(plain):
        starts.append(0)
    # An inclusion aside only names documents the requirement already asks
    # for, so it opens a head where a requirement cue stands before it.
    asides = {start: close for start, close in included if any(cue <= start for cue in cues)}
    starts = sorted(set(starts) | set(asides) | set(cues))
    heads = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(plain)
        pieces = _piece_heads(plain, start, end)
        close = asides.get(start)
        if close is not None:
            # The aside confirms the documents the requirement asks for and
            # does not enumerate whatever else the page prints inside it, so
            # a piece of it that does not read as a document opens no head.
            bounds = pieces + [end]
            pieces = [head for i, head in enumerate(pieces)
                      if head >= close or _reads_as_document(plain[head:min(bounds[i + 1], close)])]
        heads += pieces
    # The heads are read on the blanked text. An item is matched on the
    # page's own text, so a parenthetical description of a document counts
    # towards its name while never opening a head of its own.
    return text, heads


def _item_tokens(item):
    import unicodedata
    norm = unicodedata.normalize('NFKC', str(item)).casefold()
    if not _spaced_script(norm):
        return None
    return [t for t in re.findall(r'[^\W_]+', norm) if t not in _ITEM_STOPWORDS]


def _token_pattern(token):
    base = token[:-1] if token.endswith('s') and len(token) > 3 and token[:-1].isalpha() else token
    return re.escape(base) + (r'(?:s|es)?' if base.isalpha() else '') + r'(?![^\W_])'


def _head_span(item, plain, head):
    """The span of the item when it stands at this head: its first content
    token opens the piece (after at most three determiners) and each
    further token follows within four words, in order. None otherwise."""
    import unicodedata
    tokens = _item_tokens(item)
    text = plain.casefold()
    pos = head
    if tokens is None:
        needle = unicodedata.normalize('NFKC', str(item)).casefold().strip()
        stripped = text[pos:].lstrip(' \t-–—•*·▪:：、，,')
        pos += len(text[pos:]) - len(stripped)
        return (pos, pos + len(needle)) if needle and text.startswith(needle, pos) else None
    if not tokens:
        return None
    first = re.compile(r'\W*' + _token_pattern(tokens[0]), re.I)
    for _ in range(4):
        m = first.match(text, pos)
        if m:
            break
        skip = _DETERMINER_RE.match(text, pos) if not text[pos:pos + 1].isspace() else re.compile(r'\W+').match(text, pos)
        if not skip or skip.end() == pos:
            return None
        pos = skip.end()
    else:
        return None
    end = m.end()
    for token in tokens[1:]:
        m = re.compile(r'(?:\W+[^\W_]+){0,4}?\W+' + _token_pattern(token), re.I).match(text, end)
        if not m:
            return None
        end = m.end()
    return head, end


def _document_span(item, sentence, bare_ok=False):
    """The span of the item at a head of this sentence, or None when the
    item stands nowhere in requirement position."""
    plain, heads = _sentence_heads(sentence, bare_ok)
    for head in heads:
        span = _head_span(item, plain, head)
        if span:
            return span
    return None


def _list_passage(passages):
    """Whether the quoted passages read as a document list, so a bare line
    ("Hotel reservation") is an item of it: a documents cue somewhere, or
    three or more short lines."""
    if _DOCUMENT_CUES.search(passages) or _REQUIREMENT_CUE_RE.search(passages):
        return True
    short = [s for s in _sentences(passages) if len(s.split()) <= 12]
    return len(short) >= 3


def _enumeration_coverage(value, sentences, bare_ok):
    """How many enumeration pieces the quoted enumeration carries and how
    many of them a stored item opens or covers. The enumeration is every
    sentence where a stored item stands at a head, every sentence that
    states documents of its own, and, in a list passage, every list line
    (a marker line or a bare line) beside them. A whole sentence of the
    quote the stored list never touches counts against it, so a list
    cannot drop one and still read as complete. A line of a list the counter
    cannot read, because it runs longer than a bare item may or reads as
    prose, is a piece of the list all the same and counts as one the stored
    list does not cover, so the list is forced to partial rather than read
    as complete past a line nobody judged."""
    counted, previous = [], None
    for sentence in sentences:
        plain, heads = _sentence_heads(sentence, bare_ok)
        # A sub-bullet under a line that ends with a colon ("Proof of
        # funds:" / "* tax return" / "* bank statement") is part of that
        # item, not an item of its own.
        marker = _LIST_MARKER_RE.match(_plain(sentence))
        above = _LIST_MARKER_RE.match(_plain(previous)) if previous is not None else None
        sub_item = bool(marker and above and previous.rstrip().endswith((':', '：'))
                        and marker.group().strip()[:1] != above.group().strip()[:1])
        if not sub_item:
            previous = sentence
        if sub_item:
            continue
        if not heads:
            # The line is held until it is known whether the passage is a
            # list at all, because only a list makes a bare line an item.
            counted.append(None)
            continue
        spans = [span for item in value for span in [_head_span(item, plain, head) for head in heads] if span]
        listed = bare_ok and (bool(_LIST_MARKER_RE.match(plain)) or not _REQUIREMENT_CUE_RE.search(_plain(sentence)))
        if not spans and not listed and not _sentence_states('required_documents', sentence):
            counted.append(None)
            continue
        counted.append((len(heads), sum(1 for head in heads if any(start <= head < end for start, end in spans))))
    items = [i for i, entry in enumerate(counted) if entry is not None]
    total = sum(entry[0] for entry in counted if entry is not None)
    covered = sum(entry[1] for entry in counted if entry is not None)
    if bare_ok and items:
        # Every line of the list the counter could not read is a piece the
        # stored list does not cover, wherever in the list it stands.
        total += sum(1 for entry in counted if entry is None)
    return total, covered


def _sentence_supports(field, value, sentence, cells=None):
    from app.visa_snapshot.evidence_validator import field_value_supported
    if cells:
        if field == 'entry':
            return _cell_entry(cells.get('entries') or '') == value
        if field == 'validity':
            return field_value_supported(field, value, cells.get('validity') or '')
    if field in ('fee', 'government_fee'):
        return field_value_supported(field, value, _monetary_text(sentence, value['currency']))
    if field == 'entry':
        return bool(re.search(_ENTRY_STATEMENTS[value], sentence, re.I))
    if field == 'application_channel':
        legacy = {'online_portal': 'online', 'embassy_or_consulate': 'embassy', 'embassy_designated_agency': 'authorised_agent',
                  'authorised_agent': 'authorised_agent', 'on_arrival': 'on_arrival'}.get(value, value)
        return field_value_supported('application_channel', legacy, sentence)
    return field_value_supported(field, value, sentence)


def _duration_problem(field, sentence, n, unit, own, other, product, products, merged, cells=None, stated=None):
    """The gates a validity or stay figure passes in its sentence, or in
    its header-named table cell. A cell passes the same hedge, cap and
    document-duration gates as a sentence, because the header names the
    column and says nothing about the words printed inside the cell."""
    noun = 'validity' if field == 'validity' else 'stay'
    own = own or (_VALIDITY_WORDS if field == 'validity' else _STAY_WORDS)
    other = other or (_STAY_WORDS if field == 'validity' else _VALIDITY_WORDS)
    if cells is not None:
        cell = cells.get('validity') or ''
        if field != 'validity' or not _figure_re(n, unit).search(cell):
            return 'the row\'s validity cell (%s) does not state this figure' % cell
        # The header names the column and says nothing about the words in
        # the cell, so a cell that states a stay ("30 Days stay", "Stay of
        # 30 Days") fails the validity-versus-stay gate a sentence fails.
        if other.search(cell) or not _bound(noun + ' ' + cell, n, unit, own, other):
            return 'the row\'s validity cell (%s) states a stay, not the visa\'s validity (a stay is not a validity)' % cell
        if not _table_cell_supported('validity', cell):
            return 'the row\'s validity cell (%s) does not fully match a duration from issue' % cell
        if _range_or_choice(cell, n):
            return 'the row\'s validity cell states a range or a choice, not this one value'
        if _owned_by_document(cell, n, unit):
            return 'the row\'s validity cell states a document\'s own duration, not the visa\'s'
        return (_governed_problem(cell) or _bare_cap_problem(cell, stated)
                or _several_figures_problem(cell, n, unit, product, products, merged))
    if _range_or_choice(sentence, n):
        return 'the sentence states a range or a choice of %s, not this one value' % ('validities' if noun == 'validity' else 'stays')
    if not _bound(sentence, n, unit, own, other):
        return 'the figure is not bound to a %s word in the sentence (%s)' % (
            noun, 'a stay is not a validity' if noun == 'validity' else 'a validity is not a stay')
    if _owned_by_document(sentence, n, unit):
        return 'the figure is a passport\'s, certificate\'s or other document\'s duration, not the visa\'s'
    return (_governed_problem(sentence) or (_bare_cap_problem(sentence, stated) if field == 'validity' else None)
            or _several_figures_problem(sentence, n, unit, product, products, merged))


def _sentence_problem(field, value, sentence, route, product, products, merged=None, cells=None):
    """Why this supporting sentence cannot prove the value for this cell, or
    None when every gate passes. The gates run from the sentence's subject
    outwards: whose sentence it is, when it applies, which stream of the
    product it restricts, which entry type it qualifies, then the field's
    own binding, and last whether it names a visa class the route serves.
    A header-bound table row is read through its cells: the entries cell
    is the entry statement and never a qualifier on the other columns."""
    from app.visa_snapshot.tstation import _num_unit, _validity_num_unit
    merged = merged if isinstance(merged, dict) else {}
    foreign = _foreign_subject(sentence, route)
    if foreign:
        return 'the sentence is about %s, not %s' % ('/'.join(foreign), route['passport_nationality'])
    problem = _scope_problem(sentence, route) or _temporal_problem(sentence) or _restrictive_problem(sentence, product, products)
    if problem:
        return problem
    if cells is not None:
        problem = _row_marker_problem(cells, product) or (_row_binding_problem(cells, product) if product is not None else None)
        if problem:
            return problem
    if field != 'entry':
        qualified_in = sentence
        if cells is not None and cells.get('entries'):
            qualified_in = sentence.replace(cells['entries'], ' ', 1)
        qualified = _entry_types(qualified_in, qualifiers=True)
        if qualified:
            known = _known_entries(product, products)
            if not known:
                return 'the sentence is qualified by a %s entry type the subject does not state' % '/'.join(sorted(qualified))
            if (product is not None and not known <= qualified) or (product is None and not (known & qualified)):
                return 'the sentence is qualified by a %s entry, not the subject\'s %s' % (
                    '/'.join(sorted(qualified)), '/'.join(sorted(known)))
    if field == 'validity':
        n, unit = _validity_num_unit(value)
        problem = _duration_problem(field, sentence, n, unit, _VALIDITY_WORDS, _STAY_WORDS, product, products, merged, cells,
                                    stated=value)
        if problem:
            return problem
    elif field in ('max_stay_days', 'permitted_stay_days'):
        if not _figure_re(value, 'Day').search(sentence):
            return 'the figure does not stand beside a day word in the sentence (months or years are never converted)'
        problem = _duration_problem(field, sentence, value, 'Day', _STAY_WORDS, _VALIDITY_WORDS, product, products, merged, cells)
        if problem:
            return problem
    elif field == 'permitted_stay':
        n, unit = _num_unit(value)
        if n:
            if not _figure_re(n, unit).search(sentence):
                return 'the stay figure does not stand beside its unit word in the sentence'
            problem = _duration_problem(field, sentence, n, unit, _STAY_WORDS, _VALIDITY_WORDS, product, products, merged, cells)
            if problem:
                return problem
        elif _VALIDITY_WORDS.search(sentence):
            return 'the sentence states a validity, and the stay wording cannot be told apart from it'
        elif _governed_problem(sentence):
            return _governed_problem(sentence)
    elif field in ('fee', 'government_fee'):
        problem = (_fee_sentence_problem(sentence, route, product, products, merged)
                   or _currency_beside(sentence, value['amount'], value['currency'], route['destination_country']))
        if problem:
            return problem
    elif field == 'entry':
        stated = {_cell_entry(cells.get('entries') or '')} - {None} if cells is not None else _entry_types(sentence)
        if stated != {value}:
            return 'the sentence states %s entries, not only %s' % ('/'.join(sorted(stated)) or 'no', value)
    return _class_problem(sentence, route, product, products, merged)


_MARKER_ONLY_RE = re.compile(r'^\s*(?:\(?\d{1,2}[.)]|\(?[a-z][.)]|[ivx]{1,4}[.)])\s*$', re.I)


def _list_sentences(text):
    """The sentences of a quote, with a numbered marker the splitter cut
    off ("2." before "One photo") joined back to its item."""
    out = []
    for sentence in _sentences(text):
        if out and _MARKER_ONLY_RE.match(out[-1]):
            out[-1] = out[-1].rstrip() + ' ' + sentence.lstrip()
        else:
            out.append(sentence)
    return out


def _quote_sentences(proof):
    """The sentences of each quote in order, each with the sentences of the
    same quote that stand before it (empty for a quote's first sentence).
    A pronoun subject is read against all of them, not only the nearest,
    because the splitter cuts an abbreviation into a piece of its own
    ("... valid status in the U.S." / "are exempt from the requirement.")
    and the subject then sits two pieces back."""
    out = []
    for item in proof['evidence']:
        quoted = _list_sentences(item['quote'])
        for index, sentence in enumerate(quoted):
            out.append((sentence, tuple(quoted[:index])))
    return out


def _anaphora_problem(sentence, before, route, product, products, binding):
    """Why a sentence whose subject points back to the sentence before it
    cannot stand alone: the quote must include that sentence, and it must
    pass the subject gates and bind to the product itself."""
    if not _ANAPHORA_RE.match(sentence):
        return None
    if not before:
        return 'the sentence\'s subject refers to the sentence before it, which the quote does not include'
    text = ' '.join(s.strip() for s in before)
    foreign = _foreign_subject(text, route)
    unbound = [binding.get(s) for s in before]
    problem = ('the sentence before it is about %s, not %s' % ('/'.join(foreign), route['passport_nationality']) if foreign else
               _scope_problem(text, route) or _restrictive_problem(text, product, products)
               # The subject's own sentence must bind to the product too,
               # and any of the quote's earlier sentences may carry it.
               or (next((p for p in unbound if p), None) if unbound and all(unbound) else None))
    if problem:
        return 'the sentence\'s subject refers to the sentence before it, and ' + problem
    return None


def _check_fill_binding(field, value, proof, route, merged, product, label, sources=None):
    """Bind the quote to the cell's own subject. The general batch already
    proved the quote is literal and the figure occurs somewhere in it. This
    proves it is this product's, this nationality's and this field's value.
    A product fill is read sentence by sentence: the sentence that states
    the value must itself bind to the product through its anchors. A
    document must stand in the requirement position of its sentence, and
    a table row is read through the header it stands under, which the
    captured page supplies when the quote is the row alone."""
    passages = '\n'.join(item['quote'] for item in proof['evidence'])
    products = merged.get('visa_products') if isinstance(merged.get('visa_products'), list) else []
    product_type = product.get('type') if product is not None else None
    if field == 'application_channel':
        allowed = CHANNELS_BY_DISPOSITION.get(merged.get('disposition'), frozenset())
        if value not in allowed:
            raise PatchRejected('%s: the channel %s contradicts the served %s verdict' % (
                label, value, merged.get('disposition') or 'unknown'))
    items = value if field == 'required_documents' else [value]
    rows = _delimited_rows(_row_context(proof, sources))
    ordered = _quote_sentences(proof)
    sentences = [s for s, _ in ordered]
    before = dict(ordered)
    # The quote each sentence came from, so a sentence with no anchor of
    # its own can be read against the section of the page it stands in.
    quoted = {}
    for evidence in proof['evidence']:
        for s in _list_sentences(evidence['quote']):
            quoted.setdefault(s, evidence)
    # A route-level sentence has no product to bind to, so it runs the
    # section gate alone, with every served product as its subject.
    binding = {s: ((_binding_problem(s, product, products) if product is not None else None)
                   or _section_problem(s, quoted.get(s) or {}, sources, route, product, products, merged,
                                       rows.get(_row_key(s)))) for s in sentences}
    bound = [s for s in sentences if binding[s] is None]
    bare_ok = _list_passage(passages)
    for item in items:
        candidates = [s for s in bound if _sentence_supports(field, item, s, rows.get(_row_key(s)))]
        if not candidates:
            what = 'the document "%s"' % item if field == 'required_documents' else 'this value'
            stating = [s for s in sentences if binding[s] is not None and _sentence_supports(field, item, s, rows.get(_row_key(s)))]
            if stating:
                raise PatchRejected('%s: the sentence that states %s does not bind to %s: %s' % (
                    label, what, 'the product' if product_type else 'the served products', binding[stating[0]]))
            raise PatchRejected('%s: no quoted sentence %sstates %s' % (
                label, 'bound to the product ' if product_type else '', what))
        if field == 'required_documents':
            positioned = [s for s in candidates if _document_span(item, s, bare_ok)]
            if not positioned:
                raise PatchRejected('%s: the document "%s" does not stand in the requirement position of its sentence (inside a '
                                    'clause describing another party, an aside, or beside no requirement cue)' % (label, item))
            candidates = positioned
        problems = [_anaphora_problem(s, before[s], route, product, products, binding)
                    or (_row_conflict_problem(rows, rows.get(_row_key(s)), product, field) if product is not None else None)
                    or _sentence_problem(field, item, s, route, product, products, merged, rows.get(_row_key(s)))
                    for s in candidates]
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
        _check_absence(proof, sources, route, field, fill['value'], label, product=product,
                       products=merged.get('visa_products'))
        if set(CELLS[field]) <= set(owner.get('unpublished_fields') or []):
            raise PatchRejected(label + ': already documented as not published')
        return 'absence'
    if set(proof) - _REVIEWED_PROOF_KEYS:
        raise PatchRejected(label + ': extra instruction on the proof')
    if _checked_proof(deepcopy(proof), sources, route, field, fill['value'], label, product=product) is None:
        raise PatchRejected(label + ': a fill needs a reviewed proof')
    _review_date(proof, label)
    _check_fill_value(field, fill['value'], proof, label)
    _check_fill_binding(field, fill['value'], proof, route, merged, product, label, sources)
    return 'fill'


_ROW_KEYS = frozenset({'cache_key', 'route', 'baseline_sha256', 'fills'})


def _row_shape_problem(row):
    """The shape reason for a route row that is not exactly the contract."""
    if not isinstance(row, dict):
        return 'Route row is not an object'
    if set(row) != _ROW_KEYS:
        missing, extra = sorted(_ROW_KEYS - set(row)), sorted(set(row) - _ROW_KEYS)
        return 'Extra instruction: a route row carries exactly cache_key, route, baseline_sha256 and fills' + (
            ' (missing %s)' % ', '.join(missing) if missing else '') + (' (extra %s)' % ', '.join(extra) if extra else '')
    return None


def _route_checks(row, layer):
    """The context-level gates: exact shape, exact six-layer baseline, no
    operator authorship, existing seed ownership."""
    shape = _row_shape_problem(row)
    if shape:
        raise PatchRejected(shape)
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


def _page_span(quote, text):
    """The captured page's own spelling of a quote that quote_literal
    accepted through its whitespace-free fallback: the first span of the
    page whose non-blank characters are the quote's, or None."""
    compact = re.sub(r'\s+', '', str(quote or ''))
    if not compact or len(compact) > 4000:
        return None
    pattern = r'\s*'.join(re.escape(ch) for ch in compact)
    m = re.search(pattern, text, re.I)
    return m.group() if m else None


def _aligned_proof(proof, sources):
    """The proof with each quote rewritten to the span as the captured page
    prints it, so the served citation is searchable on the page. A quote
    the page prints verbatim stays as it is."""
    from scripts.convert_reviewed_general_batch import quote_literal
    proof = deepcopy(proof)
    for item in proof.get('evidence') or []:
        page = sources.get(item.get('source_id'))
        if not page or item['quote'] in page['text']:
            continue
        span = _page_span(item['quote'], page['text'])
        if span and quote_literal(span, page['text']):
            item['quote'] = span.strip()
    return proof


def _partial_reason(fill, sources):
    """Why a document list is stored partial: one quoted document is not
    the destination's list, and a list shorter than the enumeration it
    quotes is known to be incomplete. None for a complete list."""
    value = fill['value']
    if fill['field'] != 'required_documents' or not isinstance(value, list):
        return None
    if len(value) == 1:
        return 'a single quoted document is stored as a partial list and is never credited'
    passages = '\n'.join(item['quote'] for item in fill['proof']['evidence'])
    total, covered = _enumeration_coverage(value, [s for item in fill['proof']['evidence'] for s in _list_sentences(item['quote'])],
                                           _list_passage(passages))
    if total and covered < total:
        return 'the list covers %d of the %d items of the enumeration it quotes and is stored as a partial list, never credited' % (covered, total)
    return None


def _quoted_elements(fill):
    """The items a partial document proof did verify, for the store's own
    reader. They travel under the retained key and never under
    verified_elements, because the grader credits a partial proof whose
    verified elements cover the stored value, and a list known to be
    shorter than the page's may never raise a record's grade."""
    value = fill.get('value')
    return [deepcopy(v) for v in value] if isinstance(value, list) else []


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
        product = _product_named(fields['visa_products'], fill['product_type'], label) if fill['target'] == 'product' else None
        if absence:
            reason = _absence_reason(fill['proof'], sources, field, product, fields.get('visa_products'))
            record['checked_source_urls'] = [sources[i]['url'] for i in fill['proof']['source_ids']]
            record['field_page_id'] = _field_page_id(fill['proof'], sources, field, product, fields.get('visa_products'))
            reviewed = None
        else:
            # The stored quote is the page's own text, and a fee quote
            # carries its age scope into the note.
            reviewed = _aligned_proof(fill['proof'], sources)
            if field in ('fee', 'government_fee'):
                scope = _age_scope_note('\n'.join(item['quote'] for item in reviewed['evidence']))
                if scope:
                    reviewed['scope_note'] = (str(reviewed.get('scope_note') or '').strip() + ' ' + scope).strip()
                    # The served visa_fee_qualifier column holds "from" or
                    # nothing, and this amount is the top age band rather
                    # than a floor, so writing "from" there would misstate
                    # it. The band travels in the proof note and on the
                    # applied record, where a release reads it beside the
                    # amount instead of only inside the note.
                    record['age_scope'] = scope
        # A single quoted document is not the destination's list, and a list
        # shorter than the enumeration it quotes is known to be incomplete.
        # Either proof is stored as partial with no verified element, so the
        # grader never credits the cell and the record's grade cannot rise.
        partial_reason = None if absence else _partial_reason(fill, sources)
        partial = partial_reason is not None
        record['partial'] = partial
        record['partial_reason'] = partial_reason
        if fill['target'] == 'product':
            index = _product_index(fields['visa_products'], fill['product_type'], label)
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
                proof = _proof_for(reviewed, route, field, product)
                if partial:
                    proof.update(status='partial', verified_elements=[], retained_unverified_elements=_quoted_elements(fill))
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
                proof = _proof_for(reviewed, route, field)
                proof['verification_scope'] = SCOPE
                if partial:
                    proof.update(status='partial', verified_elements=[], retained_unverified_elements=_quoted_elements(fill))
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
    reached = {}
    for index, a in enumerate(applied):
        cells = a['cells']
        expected = ('not-published',) if a['kind'] == 'absence' else ('filled', 'not-applicable')
        rows = []
        for row, (x, y) in enumerate(zip(before, after, strict=True)):
            if a['target'] == 'product' and y.get('_product_index') != a['product_index']:
                continue
            sx, sy = tstation.field_status(x), tstation.field_status(y)
            now = all(sy.get(c) in expected for c in cells) and sy.get(cells[0]) == expected[0]
            was = all(sx.get(c) in expected for c in cells) and sx.get(cells[0]) == expected[0]
            if now and not was:
                rows.append(row)
        if not rows:
            raise PatchRejected('%s: the fill does not reach any served cell on %s' % (_label(a), key))
        reached[index] = rows
    return reached


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
    if a.get('partial'):
        return False, a.get('partial_reason') or 'a single quoted document is stored as a partial list and is never credited'
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
        changed_columns, moved_rows = set(), set()
        for row, (a, b) in enumerate(zip(before, after, strict=True)):
            if a.get('visa_type_name') != b.get('visa_type_name') or a.get('visa_requirement') != b.get('visa_requirement'):
                raise PatchRejected('Product identity or requirement changed: ' + key)
            grade = (a.get('confidence_level'), b.get('confidence_level'))
            if grade[0] != grade[1]:
                if grade != ('Medium', 'High'):
                    raise PatchRejected('Grade changed other than Medium to High on %s: %s' % (key, b.get('visa_type_name')))
                grade_changes.append(dict(cache_key=key, visa_type_name=b.get('visa_type_name'), before=grade[0], after=grade[1]))
                moved_rows.add(row)
            diff = {c for c in set(a) | set(b) if a.get(c) != b.get(c)}
            if diff - RECORD_COLUMNS_MAY_CHANGE:
                raise PatchRejected('Unexpected record columns changed on %s: %s' % (key, sorted(diff - RECORD_COLUMNS_MAY_CHANGE)))
            changed_columns |= diff
        reached = _reaches(applied, before, after, key)
        for index, a in enumerate(applied):
            a['grade_credited'], a['grade_reason'] = _grade_verdict(a, g, prov, route)
            # Whether the grade of a row this fill reaches actually moved,
            # beside the single gate grade_credited reports. A grade may
            # rise only on a fill the grader itself credits, or on a
            # documented absence, which the owner's completion policy counts.
            a['grade_moved'] = bool(moved_rows & set(reached[index]))
            if a['grade_moved'] and a['kind'] == 'fill' and not a['grade_credited']:
                raise PatchRejected('%s: the grade would rise on %s although the grader did not credit the fill (%s)' % (
                    _label(a), key, a['grade_reason']))
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
        # The shape is judged before the layer is looked up, so a shapeless
        # row is refused for its shape and never reads as a missing layer.
        shape = _row_shape_problem(row)
        if shape:
            for fill in (row_fills if isinstance(row_fills, list) and row_fills else [None]):
                refuse(key, fill, shape)
            continue
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
    # The counts below share the absence name with triage's list, so the
    # list travels under its own key and is never overwritten by a count.
    report = dict(triage_report, documented_absences=triage_report['absences'], fills=0, absences=0, routes=[])
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
