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
            'sjedinjene američke države', 'sjedinjenih američkih država', 'verenigde staten', 'stany zjednoczone', 'アメリカ合衆国'),
    'THA': ('таиланд', 'thaïlande', 'tailandia', 'tailândia', '태국', 'タイ', '泰国', '泰國', '泰方', 'thái lan', 'ไทย', 'تايلاند', 'tayland'),
    'SGP': ('сингапур', 'singapour', 'singapur', 'singapura', '싱가포르', 'シンガポール', '新加坡', 'สิงคโปร์', 'سنغافورة'),
    'MYS': ('малайзия', 'malaisie', 'malasia', 'malásia', '말레이시아', 'マレーシア', '马来西亚', '馬來西亞', 'มาเลเซีย', 'ماليزيا', 'malezya'),
    'GBR': ('великобритания', 'соединенное королевство', 'royaume-uni', 'reino unido', 'regno unito', 'vereinigtes königreich', 'großbritannien', '영국', 'イギリス', '英国', '英國', 'anh', 'vương quốc anh', 'สหราชอาณาจักร', 'britania raya', 'inggris', 'المملكة المتحدة', 'birleşik krallık', 'great britain', 'britain', 'royaume uni', 'verenigd koninkrijk', 'velika britanija', 'ujedinjeno kraljevstvo', 'spojené kráľovstvo', 'wielka brytania'),
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
}


# Demonym stems whose endings inflect for gender, number or case on official
# pages ("ressortissants indiens", "ciudadanos rusos", "граждане России").
# A stem matches with up to five further letters, so one stem covers
# indien/indienne/indiens/indiennes without listing every form. Stems are
# the adjective root only; a country name that is already an alias is not
# repeated here.
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
    'RUS': ('russe', 'ruso', 'rusa', 'russo', 'russisch', 'russian', 'росси', 'русск'),
    'AUS': ('australien', 'australiano', 'australisch', 'australian', 'австрали'),
    'IDN': ('indonésien', 'indonesien', 'indonesio', 'indonesiano', 'indonesisch', 'indonesian', 'индонези'),
    'PHL': ('philippin', 'filipino', 'filipina', 'filippino', 'philippinisch', 'филиппин'),
    'FRA': ('français', 'francais', 'francés', 'frances', 'francese', 'französisch', 'french', 'francês', 'франци', 'француз'),
    'VNM': ('vietnamien', 'vietnamita', 'vietnamesisch', 'vietnamese', 'вьетнам'),
    'ESP': ('espagnol', 'español', 'espanol', 'spagnolo', 'spanisch', 'spanish', 'espanhol', 'испан'),
    'IND': ('indien', 'indio', 'indiano', 'indisch', 'indian', 'инди'),
    'CAN': ('canadien', 'canadiense', 'canadese', 'kanadisch', 'canadian', 'canadiano', 'канад'),
    'CHN': ('chinois', 'chino', 'cinese', 'chinesisch', 'chinese', 'chinês', 'chines', 'китай', 'китая'),
}
_NAME_PATTERNS = {}


def _aliases(nat):
    from app.visa_snapshot.evidence_validator import _NATIONALITY_NAMES
    return sorted({_norm(a) for a in (*_NATIONALITY_NAMES.get(nat, ()), *_EXTRA_ALIASES.get(nat, ())) if _norm(a)})


def _name_pattern(nat):
    """One compiled pattern for "this nationality is named here": the fixed
    aliases (ASCII-letter boundaries, as the validator reads them) plus the
    inflecting demonym stems (letter boundaries in any script)."""
    pattern = _NAME_PATTERNS.get(nat)
    if pattern is None:
        parts = [r'(?<![a-z])' + re.escape(a) + r'(?![a-z])' for a in _aliases(nat)]
        # A stem inside a hyphenated or regional compound ("sud-américains",
        # "latin american") names a region, not this nationality.
        parts += [_COMPOUND_GUARD + r'(?<![^\W\d_])' + re.escape(_norm(stem)) + r'[^\W\d_]{0,5}(?![^\W\d_])'
                  for stem in _DEMONYM_STEMS.get(nat, ())]
        pattern = _NAME_PATTERNS[nat] = re.compile('|'.join(parts) if parts else r'(?!x)x')
    return pattern


_COMPOUND_GUARD = ''.join(f'(?<!{p})' for p in ('-', 'sud ', 'nord ', 'south ', 'north ', 'latin ', 'latino ',
                                                 'hispano ', 'afro ', 'anglo ', 'central ', 'indo '))
# A mention right after a negating prefix ("non-US citizens", "other than
# Indian nationals") speaks about everyone but this nationality.
_NEGATED_PREFIX_RE = re.compile(r"(?:\bnon[- ]?|\bother than\s+|\bexcluding\s+|\bexcept(?:ing)?(?:\s+for)?\s+|\bnot\s+|"
                                r"\bsauf\s+|\bhors\s+|\bexcepto\s+|\bsalvo\s+|\bexceto\s+|\baußer\s+|\btranne\s+|"
                                r"\bkecuali\s+|\bкроме\s+|\bне\s+)$")


def _mentions(text, nat):
    """Spans of the nationality's own mentions in the normalized text: fixed
    aliases and inflected demonyms, negated mentions left out."""
    low = _norm(text)
    spans = [m.span() for m in _name_pattern(nat).finditer(low)
             if not _NEGATED_PREFIX_RE.search(low[max(0, m.start() - 24):m.start()])]
    tokens = _CAPITAL_TOKENS.get(nat)
    if tokens:
        nfkc = unicodedata.normalize('NFKC', str(text or ''))
        for m in re.finditer(r'(?<![A-Za-z])(?:' + '|'.join(tokens) + r')(?![A-Za-z])', nfkc):
            if _NEGATED_PREFIX_RE.search(nfkc[max(0, m.start() - 24):m.start()].casefold()):
                continue
            # Locate the same token in the normalized text for a span.
            low_token = re.escape(_norm(m.group(0)))
            at = re.search(r'(?<![a-z])' + low_token + r'(?![a-z])', low)
            spans.append(at.span() if at else (0, 0))
    return spans


# Abbreviations that are the nationality only as capitals: "USA" is the
# country, "usa" is a Spanish verb. Read on the un-casefolded text.
_CAPITAL_TOKENS = {'USA': (r'USA', r'EUA', r'EE\.?UU\.?', r'U\.S\.A\.?')}


def _named(text, nat):
    """The text names the nationality: a fixed alias, an inflected demonym
    (after normalization) or a capitalised abbreviation (as written), and
    not only behind a negating prefix."""
    return bool(_mentions(text, nat))


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
    policy = re.compile(r'\b(?:polic(?:y|ies)|regulations?|rules?|resolutions?|'
                        r'agreements?|decrees?|schemes?|programmes?|programs?|'
                        r'visa[- ]?exemption|visa[- ]?free|exemption)\b|'
                        r'政策|免簽|免签|規定|规定|法規|法规|辦法|办法', re.I)
    if key == 'effective_from':
        relation = (r'(?:effective\s*(?:from|on|as of)?|(?:comes?|enters?)\s+into\s+force\s*(?:on|from)?|'
                    r'takes?\s+effect\s*(?:on|from)?|(?:starts?|begins?|commences?)\s*(?:on|from)?|'
                    r'appl(?:y|ies)\s+from)\s*[:,]?\s*' + dates)
        chinese = r'(?:自|從|从|於|于)\s*' + dates + r'[^。；\n]{0,60}(?:起|生效|實施|实施|至|到)'
    else:
        relation = (r'(?:until|through|ends?\s*(?:on)?|ending\s+on|expires?\s*(?:on)?|'
                    r'ceases?\s+to\s+(?:apply|have\s+effect)\s*(?:on)?)\s*[:,]?\s*'
                    r'(?:and\s+including\s+)?' + dates)
        chinese = r'(?:至|截至|到|有效至)\s*' + dates + r'|' + dates + r'\s*(?:止|屆滿|届满|到期)'
    for sentence in re.split(r'[.!?;。；\n]', str(quote or '')):
        sentence = _norm(sentence)
        if re.search(r'\b(?:holiday|closed|closure|issued|issuance|updated|published)\b|'
                     r'passport.{0,35}(?:valid|expir)|護照|护照|休館|休馆|休假|發證|发证', sentence):
            continue
        if policy.search(sentence) and (re.search(relation, sentence, re.I)
                                        or re.search(chinese, sentence)):
            return True
    return False


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
                 f'{day.year}年{day.month}月{day.day}日')
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


def _check_proof(proof, sources, route, field, value, *, product=None, covered=None):
    """Return the validated proof or None for an explicit unknown/not_published.
    `covered` carries the other values this proof key covers (the prose
    permitted_stay under the permitted_stay_days proof); an unknown or
    unpublished proof leaves every one of them empty."""
    if not isinstance(proof, dict) or proof.get('verifier', 'ai') != 'ai':
        raise PatchRejected(f'{field}: the source review must be attributed to AI')
    status = proof.get('status')
    if status in ('unknown', 'not_published'):
        if not _empty_value(value):
            raise PatchRejected(f'{field}: an unknown or unpublished value must be empty')
        for key, other in (covered or {}).items():
            if not _empty_value(other):
                raise PatchRejected(f'{field}: an unknown or unpublished proof cannot cover a stated {key}')
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
        short_ok = isinstance(quote, str) and _list_line(quote, route['passport_nationality'])
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
        _check_value(field, value, passages, route, product, pages=pages)
    if any(k in proof for k in ('effective_from', 'effective_to', 'policy_interval_evidence')):
        if field != 'disposition':
            raise PatchRejected('Policy bounds belong to the reviewed visa disposition')
        proof.update(_checked_policy_bounds(proof, sources, route, product))
    return proof


_CURRENCY_SYMBOLS = {
    'USD': (r'US\$', r'U\.S\.\$', r'\$'), 'EUR': (r'€', r'\beuros?\b'), 'GBP': (r'£',),
    'JPY': (r'¥', r'円', r'\byen\b'), 'CNY': (r'¥', r'元', r'\bRMB\b', r'人民币'), 'KRW': (r'₩', r'원', r'\bwon\b'),
    'INR': (r'₹', r'\bRs\.?', r'\brupees?\b'), 'THB': (r'฿', r'\bbaht\b'), 'IDR': (r'\bRp\.?', r'\brupiah\b'),
    'MYR': (r'\bRM\b', r'\bringgit\b'), 'SGD': (r'S\$', r'SG\$', r'\$'), 'HKD': (r'HK\$', r'\$', r'元?港币', r'元?港幣'), 'AUD': (r'A\$', r'AU\$', r'\$'),
    'CAD': (r'C\$', r'CA\$', r'CAN\$', r'\$'), 'TWD': (r'NT\$', r'\$'), 'RUB': (r'₽', r'руб\.?', r'\brub\b'),
    'VND': (r'₫', r'đ', r'VNĐ', r'\bdong\b'), 'PHP': (r'₱', r'\bpesos?\b'), 'NZD': (r'NZ\$', r'\$'), 'MXN': (r'MX\$', r'\$'),
    'MOP': (r'MOP\$',), 'CHF': (r'\bfrancs?\b', r'\bFr\.'), 'AED': (r'\bdirhams?\b', r'\bDhs?\b'),
    'SAR': (r'\briyals?\b', r'\bSR\b'), 'TRY': (r'₺', r'\blira\b'), 'EGP': (r'\bE£', r'\bLE\b'), 'BRL': (r'R\$'),
}


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


_VERDICT_RULES = {
    # A sentence that states the rule, and the words that flip it.
    'VISA_REQUIRED': (r"(?:e-?visa|visa)s?\b[^.;\n]{0,60}\b(?:is |are )?(?:required|mandatory|needed|necessary|obligatoire|obligatorio|necesario|bắt buộc)|"
                      r"\b(?:need|needs|require|requires|must have|must hold|must obtain|are required to hold|are required to obtain|is subject to|are subject to)\b (?:a |an |the )?(?:valid |prior |entry |tourist |schengen |visitor |short[- ]stay )*(?:e-?visa|visa)s?\b|"
                      r"\b(?:needs?|requir(?:es|ing|ed))\b (?:an? )?entry clearance|entry clearance \(a visa\)|"
                      r"\bnecesita(?:n|r[áa]n?)? (?:de )?(?:un |el )?visado|\brequiere(?:n)? (?:de )?(?:un |el )?visado|\bont besoin d'un visa|\bdoivent (?:obtenir|demander|solliciter) un visa|\bvisa (?:est |sera )?(?:requis|nécessaire|exigé)|"
                      r"\b(?:soumis|subordonné)e?s? à l'obtention d'un visa|\bmunie?s? d'un visa|"
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
                      r"需要办理签证|需申请签证|应当申请签证|必须持有签证|需要签证|事前に査証|ビザが必要|签证申请|網簽|网签|비자.{0,6}필요|ต้องขอวีซ่า|wajib memiliki visa|harus memiliki visa|يجب الحصول على تأشيرة",
                      r"visa[- ]free|no visa|without (?:a )?visa|exempt|not required|do(?:es)? not (?:require|need)|\b(?:visa )?(?:on|upon) arrival\b|без виз|sans visa|sin visa|miễn thị thực|không cần|không phải xin|không yêu cầu|"
                      r"免签|无需签证|免办签证|査証免除|ビザ免除|무비자|면제|bebas visa|ยกเว้นวีซ่า"),
    'VISA_EXEMPT': (r"visa[- ]free|visa[- ]exempt|exempt(?:ed|ion)? from (?:the |a |an )?(?:(?:short[- ]stay|short[- ]term|entry|tourist|visitor|schengen|port of entry) )?(?:visa|obtaining a visa|visas?(?: requirements?)?)|"
                    r"exempt(?:ed)? from (?:the )?(?:requirement|obligation|need) (?:to obtain|to hold|to get|of obtaining|of holding) (?:a |an )?visa|"
                    r"do(?:es)? not (?:require|need) (?:a |an |any )?(?:entry |tourist |visitor )?visa|do(?:es)? not (?:require|need) to (?:apply for|obtain|hold|have) (?:a |an )?visa|"
                    r"do(?:es)?n['’]t (?:require|need) (?:a |an |any |to (?:apply for|obtain|hold|have) (?:a |an )?)?(?:entry |tourist |visitor )?visa|"
                    r"without (?:a |an |the need for a )?(?:entry |tourist )?visa|no visa (?:is )?(?:required|needed|necessary)|not required to (?:obtain|hold|apply for) (?:a |an )?visa|"
                    r"visa (?:is |are )?(?:generally |normally |usually )?not required|not requir(?:ing|ed to (?:have|hold|obtain)) (?:a |an )?(?:visitor |tourist |entry )?visa|"
                    r"visa(?: requirements?)? (?:is |are )?waived|need only (?:a )?valid passport|"
                    r"без виз|безвизов|sans visa|dispensée?s? de visa|exemptée?s? de visa|n['’](?:ont|avez|a|avons) pas besoin (?:d['’]un |de )?visa|"
                    r"sin visa(?:do)?|exent[oa]s? de visa(?:do)?|exención de visa(?:do)?|exonerad[oa]s? de visa|no (?:se )?requiere(?:n)?(?: de)? (?:un |una |el |la )?visa(?:do)?|no necesita(?:n)?(?: de)? (?:un |una |el |la )?visa(?:do)?|"
                    r"isent[oa]s? de vistos?|isenção de visto|sem visto|não precisam? de visto|visumfrei|visumsfrei|ohne visum|kein visum|senza visto|esent[ie] dal visto|non hanno bisogno di visto|"
                    r"visumvrij|geen visum|nepodliehajú vízovej povinnosti|nepodléhají vízové povinnosti|bez víz|izuzet[ia]? (?:su |je )?od vizn(?:og|e)|bez vize|ne trebaju vizu|oslobođeni (?:su )?(?:od )?viz|"
                    r"miễn thị thực|không cần (?:xin )?(?:visa|thị thực)|ยกเว้นวีซ่า|bebas visa|visa tidak diperlukan|tidak (?:memerlukan|perlu) visa|免签|无需签证|免办签证|查証免除|査証免除|ビザ免除|ビザなし|무비자|사증면제|معفى|إعفاء من التأشيرة|vizeden muaf",
                    r"\bnot (?:visa[- ]free|exempt|eligible)|do(?:es)? not (?:qualify|benefit)|unless|except(?:ion)? (?:for|of)?\s*(?:holders|nationals|citizens) of|"
                    r"(?<!no )(?<!sin )(?<!sans )(?:visa|e-?visa) (?:is |are )?(?:required|mandatory)|must (?:obtain|hold|apply)"),
    'ELECTRONIC_AUTHORIZATION_REQUIRED': (r"\b(?:eta|etas|esta|k-eta|evisitor|etias|nzeta|e-?ta)\b|electronic travel authori[sz]ation|electronic travel authority|travel authori[sz]ation|pre-arrival registration|电子旅行授权|電子旅行許可|전자여행허가",
                                          r"not required|exempt(?:ed)? from (?:the )?(?:k-eta|eta|esta)|without (?:an? )?(?:k-eta|eta|esta)|do(?:es)? not need"),
    # The arrival wording must sit beside a visa noun in the same clause; a
    # stamp, card or check "on entry" carries no visa meaning of its own.
    'VISA_ON_ARRIVAL': (r"visa[- ]on[- ]arrival|visas?\b[^.;,\n]{0,60}\b(?:on|upon) (?:arrival|arriving|entry|entering)\b|\b(?:on|upon) (?:arrival|entry)\b[^.;,\n]{0,25}\bvisas?\b|"
                        r"visas?\b[^.;,\n]{0,60}\bat the (?:airport|border|port of entry)\b|"
                        r"(?:visa|visto|visado|виз\w*)\b[^.;,\n]{0,40}(?:saat kedatangan|à l['’]arrivée|a la llegada|à chegada|по прибытии)|"
                        r"(?:saat kedatangan|à l['’]arrivée|a la llegada|à chegada|по прибытии)[^.;,\n]{0,40}(?:visa|visto|visado|виз)|"
                        r"落地签|落地簽|到着ビザ|도착비자|e-?voa",
                        r"not (?:available|eligible|issued)|no visa on arrival|cannot obtain|do(?:es)? not (?:need|require)|visa[- ]free|without (?:a |an )?visa|exempt"),
}

# Words that carry a verdict. A list entry (a country's own line in a list
# or table) must not contain one; the rule sentence does.
_RULE_WORDS = re.compile(r"visa|visado|visto|visum|víz|viz|виз|签证|簽證|査証|查証|ビザ|비자|thị thực|วีซ่า|تأشيرة|"
                         r"\b(?:eta|etas|esta|etias|k-eta|nzeta|evisitor)\b|exempt|arrival|免签|免簽|entry clearance|travel authori", re.I)
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
_TERMINATOR_RE = re.compile(r"[.!?。！？;\n|]")
# Sentence boundaries; "U.S." and " J." are abbreviations, not ends.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])(?<![A-Z]\.[A-Z]\.)(?<!\s[A-Z]\.)\s+|\n+")


_ITEM_SPLIT_RE = re.compile(r'\s*(?:,|;|、|，|/|\||&|\s[-–—]\s|\s(?:and|und|et|y|e|dan|và|и)\s)\s*', re.I)


def _entry_shaped(item, nat):
    """The item is the nationality's name with at most a short qualifier: no
    prose verbs or prepositions before or after the name, no negation."""
    low = _norm(item)
    if not low.strip(' ()*') or len(low.strip(' ()*')) > 60:
        return False
    spans = _mentions(item, nat)
    if not spans:
        return False
    start, end = spans[0]
    lead, trail = low[:start].strip(' ()*'), low[end:].strip(' ()*.')
    return (len(lead) <= 30 and len(trail) <= 40
            and not _ENTRY_PROSE_RE.search(lead) and not _ENTRY_PROSE_RE.search(trail))


def _list_line(quote, nat):
    """A quote that is essentially the nationality's own line in a list: a
    short entry-shaped line, or a run of names separated by commas, dashes
    or CJK enumerators whose item is entry-shaped. A verdict word, a prose
    fragment that mentions the nationality, or a negated mention is not a
    list line."""
    raw = re.sub(r'^\s*(?:\d+[.)]|[-*•])\s*', '', str(quote or ''))
    if not _named(raw, nat) or _RULE_WORDS.search(_norm(raw)):
        return False
    if _entry_shaped(raw, nat):
        return True
    items = _ITEM_SPLIT_RE.split(raw)
    return (len(items) >= 3 and all(len(_norm(item)) <= 45 for item in items)
            and any(_entry_shaped(item, nat) for item in items))


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


def _headings(kept):
    """Offsets of the sentences that open a list of nationalities under a
    verdict ("Countries whose citizens must have a visa:"). They cut the
    page into sections; a list line proves only the heading of its own
    section."""
    starts, pieces, at = [], [], 0
    for piece in _TERMINATOR_RE.split(kept):
        if _RULE_WORDS.search(piece) and _LIST_INTRO_RE.search(piece):
            starts.append(at)
            pieces.append(piece)
        at += len(piece) + 1
    return starts, pieces


def _states_other_verdict(text, value):
    """The text states a verdict other than this one (its own positive
    wording without its flip), or flips this one."""
    low = _norm(text)
    for key, (positive, negative) in _VERDICT_RULES.items():
        if key == value:
            if negative and re.search(negative, low, re.I):
                return True
        elif re.search(positive, low, re.I) and not (negative and re.search(negative, low, re.I)):
            return True
    return False


def _section(headings, pos):
    """Index of the section (between two headings) that holds the offset."""
    from bisect import bisect_right
    return None if pos is None else bisect_right(headings, pos)


def _passages(evidence_quotes, pages, nat=None):
    """Group the quotes into passages. Without page texts each quote is its
    own passage. With them, quotes are located on their captured page and two
    quotes that the page shows inside one sentence or table row (no sentence
    terminator or line break between them, at most 400 characters apart), or
    as consecutive sentences (nothing but the terminator between them), are
    read as one passage in page order, since the reviewer only split what the
    page says in one breath. Each passage carries its page id and offset."""
    if pages is None:
        return [{'text': q, 'page': None, 'pos': None, 'section': None, 'ambiguous': False,
                 'parts': [{'text': q, 'page': None, 'pos': None, 'section': None, 'ambiguous': False}]}
                for q in evidence_quotes], {}
    located, indexes = [], {}
    for order, (quote, (page_id, page_text)) in enumerate(zip(evidence_quotes, pages, strict=True)):
        if page_id not in indexes:
            indexes[page_id] = _page_index(page_text)
        kept, stripped, back, headings = indexes[page_id]
        needle = re.sub(r'\s+', '', _norm(quote))
        hits = [m.start() for m in re.finditer(re.escape(needle), stripped)][:200] if needle else []
        located.append({'text': quote, 'page': page_id, 'order': order, 'ambiguous': False,
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
    for unit in located:
        del unit['hits']
        unit['section'] = _section(indexes[unit['page']][3][0], unit['pos'])
        unit['parts'] = [{'text': unit['text'], 'page': unit['page'], 'pos': unit['pos'],
                          'section': unit['section'], 'ambiguous': unit['ambiguous']}]
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
    """A list line proves the sentence that opens its list: the line sits on
    the same captured page after the sentence (before it when the sentence
    refers back to the list above), and no heading between them states
    another verdict. The reach of a heading ends where the next verdict
    heading begins, not at a fixed distance."""
    if page is None:
        return bool(listed)
    if span is None:
        return False
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
        if not any(_states_other_verdict(piece, value) for piece in between):
            return True
    return False


# Named groups an official page may use instead of the country, with the
# membership checked against the group's own published list (europa.eu EU
# member countries; asean.org member states, Timor-Leste admitted 2025).
_GROUPS = {
    'EU': (r"\b(?:eu|e\.u\.)(?:-bürger|-citizens| citizens| nationals| member states?| countries)?\b|european union|union européenne|unión europea|união europeia|"
           r"europäischen? union|unione europea|европейского союза|uni eropa|liên minh châu âu",
           {'AUT', 'BEL', 'BGR', 'HRV', 'CYP', 'CZE', 'DNK', 'EST', 'FIN', 'FRA', 'DEU', 'GRC', 'HUN', 'IRL', 'ITA', 'LVA', 'LTU',
            'LUX', 'MLT', 'NLD', 'POL', 'PRT', 'ROU', 'SVK', 'SVN', 'ESP', 'SWE'}),
    'ASEAN': (r"\basean\b|东盟|東盟|東南アジア諸国連合|아세안",
              {'BRN', 'KHM', 'IDN', 'LAO', 'MYS', 'MMR', 'PHL', 'SGP', 'THA', 'VNM', 'TLS'}),
}
_EXCEPTION_RE = re.compile(r"\b(?:except(?:ing)?|excluding|other than|save for|sauf|excepto|salvo|kecuali|außer|tranne|exceto|osim|kromě|okrem)\b"
                           r"\s*(?:for|of|de|des|pour|para|bagi|untuk)?\s*([^.;:\n]{0,80})", re.I)
_ITEM_WORDS = re.compile(r"\b(?:nationals?|citizens?|holders?|passport holders?|passports?|the|of|ressortissants?|ciudadanos|nacionales|"
                         r"cidadãos|warganegara|warga negara|negara|staatsangehörige)\b")


def _carved_out(sentence, nat):
    """The sentence excludes this nationality by name: a bare item right after
    an exception word ("except Myanmar", "except for Brunei and Singapore
    nationals"). A qualified subset ("except those Canadians who ...") is not
    a carve-out of the nationality."""
    for clause in _EXCEPTION_RE.finditer(str(sentence or '')):
        for item in re.split(r"\s*(?:,|;|/|\s(?:and|or|und|et|y|e|dan|và|и)\s)\s*", clause.group(1)):
            bare = _ITEM_WORDS.sub(' ', _norm(item)).strip(' ()')
            if bare and len(bare) <= 30 and _named(bare, nat):
                return True
    return False


def _group_member(sentence, nat):
    """The sentence names a group the nationality belongs to and does not
    carve the nationality out of it in the same sentence."""
    low = _norm(sentence)
    return any(nat in members and re.search(pattern, low, re.I) for pattern, members in _GROUPS.values()) \
        and not _carved_out(sentence, nat)


def _label(quote, nat):
    """A quote that opens with the nationality as a label ("Canada: ...",
    "Hong Kong SAR passport . ...") applies that label to every sentence;
    returns the quote without its label, or None."""
    quote = str(quote or '').strip()
    parts = re.split(r'(?<=[.:：])(?<![A-Z]\.[A-Z]\.)(?<!\s[A-Z]\.)\s+|\n+', quote, maxsplit=1)
    head = parts[0]
    text = _norm(head).rstrip('.:： ')
    if len(parts) < 2 or len(text) > 80 or not _named(head, nat) or _RULE_WORDS.search(text):
        return None
    if head.rstrip().endswith((':', '：')) or (len(text.split()) >= 2 and (head.rstrip().endswith('.') or '\n' in quote)):
        return parts[1]
    return None


def _decision_supported(value, evidence_quotes, nat, pages=None, explain=None):
    """Strict but shaped for real official pages: the nationality must be named
    in the evidence (in the rule sentence, as a label or antecedent of it, or
    as its own list line beside the rule sentence on the same page) and a
    sentence of the evidence must state the rule without flipping it in the
    same sentence. A group the nationality belongs to (EU, ASEAN) counts
    only through the explicit membership table. `pages`, when given, is one
    (page id, page text) pair per quote; `explain`, when given, is a list
    that receives the path that decided."""
    def decide(path):
        if explain is not None:
            explain.append(path)
        return True
    from app.visa_snapshot.evidence_validator import supports_disposition, NEGATED_VISA_EXEMPTION
    passages = '\n'.join(evidence_quotes)
    named = _named(passages, nat)
    if value == 'CONDITIONAL':
        return named and bool(_CONDITION_RE.search(passages))
    positive, negative = _VERDICT_RULES.get(value, (None, None))
    if positive and value == 'VISA_EXEMPT':
        negative = '(?:' + negative + ')|(?:' + NEGATED_VISA_EXEMPTION + ')'
    # The validator's anchored statement still needs a non-negated mention of
    # the nationality and a sentence that states the rule without flipping it.
    unflipped = (not positive or any(re.search(positive, _norm(s), re.I) and not (negative and re.search(negative, _norm(s), re.I))
                                     for s in _SENTENCE_SPLIT.split(passages)))
    if named and unflipped and supports_disposition(passages, value, nationality=nat):
        return decide('validator: anchored statement')
    if not positive:
        return False
    units, indexes = _passages(evidence_quotes, pages, nat)
    listed = [part for unit in units for part in unit['parts']
              if not part['ambiguous'] and _list_line(part['text'], nat)]
    if explain is not None:
        for unit in units:
            if unit['ambiguous']:
                explain.append('list line occurs under different verdict headings: ' + _norm(unit['text'])[:120])
    others = [n for n in _EXTRA_ALIASES if n != nat]
    seen_positive = False
    for unit in units:
        body = _label(unit['text'], nat)
        labelled = body is not None
        previous_named = False
        for sentence in _SENTENCE_SPLIT.split(body if labelled else unit['text']):
            sl = _norm(sentence)
            sentence_named = _named(sentence, nat) and not _carved_out(sentence, nat)
            if _carved_out(sentence, nat):
                if explain is not None:
                    explain.append('carved out by name: ' + sl[:120])
                previous_named = False
                continue
            # A label or the previous sentence lends its nationality only to a
            # sentence that brings no subject of its own.
            borrowable = (not sentence_named and not _OWN_SUBJECT_RE.search(sl)
                          and not any(_named(sentence, other) for other in others))
            carried = borrowable and (labelled or previous_named)
            flipped = bool(negative and re.search(negative, sl, re.I))
            # A sentence that flips this verdict cannot lend its nationality
            # to the next one: "do not need a visa" followed by an optional
            # "may apply for a visa" is still an exemption.
            previous_named = (sentence_named or carried) and not flipped
            if not re.search(positive, sl, re.I):
                continue
            seen_positive = True
            if flipped:
                if explain is not None:
                    explain.append('flipped in the same sentence: ' + sl[:120])
                continue
            if sentence_named:
                return decide('named in the rule sentence: ' + sl[:120])
            if carried:
                return decide(('label' if labelled else 'previous sentence') + ' names the nationality: ' + sl[:120])
            if _group_member(sentence, nat):
                return decide('group membership: ' + sl[:120])
            if listed and _LIST_INTRO_RE.search(sl):
                index = indexes.get(unit['page']) if unit['page'] is not None else None
                span = _sentence_span(index, sentence, unit) if index else None
                if _same_list(listed, unit['page'], span, bool(_LIST_ABOVE_RE.search(sl)), index, value):
                    return decide('list line beside the rule sentence: ' + sl[:120])
                if explain is not None:
                    explain.append('list line on another page or under another heading: ' + sl[:120])
            elif listed and explain is not None:
                explain.append('list line but the rule sentence opens no list: ' + sl[:120])
    if explain is not None:
        explain.append('rejected: ' + ('nationality never named' if not named else
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


def _check_value(field, value, passages, route, product, pages=None):
    from app.visa_snapshot.evidence_validator import field_value_supported
    if field == 'disposition':
        quotes = [q for q in passages.split('\n') if q.strip()]
        if pages is not None and len(pages) != len(quotes):
            # A quote with a line break inside cannot keep its page pairing.
            pages = None
        if not _decision_supported(value, quotes, route['passport_nationality'], pages=pages):
            raise PatchRejected('disposition: evidence does not state this verdict for this nationality')
        return
    if field in ('permitted_stay_days', 'max_stay_days'):
        if value is not None and not field_value_supported(field, value, passages):
            raise PatchRejected(f'{field}: the figure is not in its evidence')
    elif field in ('government_fee', 'fee'):
        if isinstance(value, dict) and value.get('amount') not in (None, 0):
            text = _monetary_text(passages, str(value.get('currency') or '').upper())
            if not field_value_supported(field, value, text):
                raise PatchRejected(f'{field}: the fee amount is not in its evidence')
        elif isinstance(value, dict) and value.get('amount') == 0:
            if not re.search(r'free|no fee|no charge|gratis|waived|exempt|0\b|no visa', passages, re.I):
                raise PatchRejected(f'{field}: a zero fee needs an explicit free, waived or no-visa statement')
    elif field == 'processing_time':
        if value and not field_value_supported(field, value, passages):
            # Working-day and calendar figures must appear in the evidence.
            digits = re.findall(r'\d+', str(value))
            if digits and not all(d in passages for d in digits):
                raise PatchRejected('processing_time: the figure is not in its evidence')
    elif field == 'application_channel':
        if value and not field_value_supported(field, {'online_portal': 'online', 'embassy_or_consulate': 'embassy',
                                                       'embassy_designated_agency': 'authorised_agent',
                                                       'authorised_agent': 'authorised_agent', 'on_arrival': 'on_arrival',
                                                       'not_required': 'none'}.get(value, value), passages):
            raise PatchRejected('application_channel: the channel is not described in its evidence')
    elif field == 'official_portal_url':
        if value and _norm(value) not in _norm(passages) and not re.search(r'apply|portal|online|website|e-?visa', passages, re.I):
            raise PatchRejected('official_portal_url: the portal is not described in its evidence')
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
        if _check_proof(verdict.get('proof'), sources, route, 'disposition', disp) is None:
            raise PatchRejected('A published verdict cannot be unknown')
        proofs = row.get('route_field_proofs') or {}
        values = row.get('route_fields') or {}
        dropped = row.setdefault('dropped', [])
        for key, covered in ROUTE_PROOF_COVERS.items():
            if key in proofs:
                if isinstance(proofs[key], dict) and proofs[key].get('status') in ('unknown', 'not_published'):
                    # The proof says the figure is unknown or not published:
                    # the prose it covers is unproved too and is not asserted.
                    for c in covered:
                        if c != key and not _empty_value(values.get(c)):
                            dropped.append(f'{c}: a value under an unknown or unpublished {key} proof')
                            values.pop(c, None)
                try:
                    _check_proof(proofs[key], sources, route, key, values.get(key),
                                 covered={c: values.get(c) for c in covered if c != key})
                except PatchRejected as exc:
                    # An ancillary value that cannot be proved is not asserted;
                    # the verdict is the only field that decides the row.
                    dropped.append(str(exc))
                    proofs.pop(key, None)
                    for c in covered:
                        values.pop(c, None)
            elif any(values.get(c) not in (None, [], '') for c in covered):
                dropped.append(f'{key}: a value without a proof')
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
                        _check_proof(pproofs[field], sources, route, field, value, product=spec)
                    elif value not in (None, '', {}) and not (isinstance(value, dict) and value.get('amount') is None):
                        raise PatchRejected(f'{spec["type"]}: {field} has a value but no proof')
                except PatchRejected as exc:
                    dropped.append(str(exc))
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
        if _check_proof(decision, sources, route, 'disposition', product.get('disposition'), product=product) is None:
            raise PatchRejected('An optional product needs its own reviewed verdict')
        for field in PRODUCT_PROOF_FIELDS[1:]:
            value = product.get(field)
            empty = value in (None, '', {}) or (isinstance(value, dict) and all(v is None for v in value.values()))
            if not empty and _check_proof(proofs.get(field), sources, route, field, value, product=product) is None:
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
        final, unsupported, removed = [], [], []
        seen = set()
        for spec in row.get('products') or []:
            action = spec['action']
            if action == 'remove':
                removed.append({'type': spec.get('current_name'), 'reason': spec.get('remove_reason')})
                seen.add(spec.get('current_name'))
                continue
            seen.add(spec.get('current_name'))
            if spec.get('verdict_unproved'):
                # The reviewer asserted a product verdict its evidence does not
                # state; neither that verdict nor the current product is served.
                # On an exemption lane a paid option without its own reviewed
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
                        'removed_products': removed, 'unpublished': sorted(unpublished)})
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
                      'unsupported_products': sum(len(r['unsupported_products']) for r in reports)}))
