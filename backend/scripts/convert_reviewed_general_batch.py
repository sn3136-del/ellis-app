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
    'KOR': ('корея', 'республика корея', 'corée', 'corea', 'coreia', '한국', '대한민국', '韓国', '韩国', '韓國', 'hàn quốc', 'เกาหลี', 'korea selatan', 'كوريا', 'güney kore'),
    'USA': ('сша', 'соединенные штаты', 'états-unis', 'etats-unis', 'estados unidos', 'stati uniti', 'vereinigte staaten', '미국', 'アメリカ', '米国', '美国', '美國', 'hoa kỳ', 'สหรัฐ', 'amerika serikat', 'الولايات المتحدة', 'amerika birleşik devletleri'),
    'THA': ('таиланд', 'thaïlande', 'tailandia', 'tailândia', '태국', 'タイ', '泰国', '泰國', 'thái lan', 'ไทย', 'تايلاند', 'tayland'),
    'SGP': ('сингапур', 'singapour', 'singapur', 'singapura', '싱가포르', 'シンガポール', '新加坡', 'สิงคโปร์', 'سنغافورة'),
    'MYS': ('малайзия', 'malaisie', 'malasia', 'malásia', '말레이시아', 'マレーシア', '马来西亚', '馬來西亞', 'มาเลเซีย', 'ماليزيا', 'malezya'),
    'GBR': ('великобритания', 'соединенное королевство', 'royaume-uni', 'reino unido', 'regno unito', 'vereinigtes königreich', 'großbritannien', '영국', 'イギリス', '英国', '英國', 'anh', 'vương quốc anh', 'สหราชอาณาจักร', 'britania raya', 'inggris', 'المملكة المتحدة', 'birleşik krallık', 'great britain'),
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


def _aliases(nat):
    from app.visa_snapshot.evidence_validator import _NATIONALITY_NAMES
    return sorted({_norm(a) for a in (*_NATIONALITY_NAMES.get(nat, ()), *_EXTRA_ALIASES.get(nat, ())) if _norm(a)})


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


def _check_proof(proof, sources, route, field, value, *, product=None):
    """Return the validated proof or None for an explicit unknown/not_published."""
    if not isinstance(proof, dict) or proof.get('verifier', 'ai') != 'ai':
        raise PatchRejected(f'{field}: the source review must be attributed to AI')
    status = proof.get('status')
    if status in ('unknown', 'not_published'):
        if value not in (None, [], {}, '') and not (isinstance(value, dict) and all(v is None for v in value.values())):
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
        short_ok = isinstance(quote, str) and _list_line(quote, _aliases(route['passport_nationality']))
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
        _check_value(field, value, passages, route, product)
    if any(k in proof for k in ('effective_from', 'effective_to', 'policy_interval_evidence')):
        if field != 'disposition':
            raise PatchRejected('Policy bounds belong to the reviewed visa disposition')
        proof.update(_checked_policy_bounds(proof, sources, route, product))
    return proof


_CURRENCY_SYMBOLS = {
    'USD': (r'US\$', r'U\.S\.\$', r'\$'), 'EUR': (r'€', r'\beuros?\b'), 'GBP': (r'£',),
    'JPY': (r'¥', r'円', r'\byen\b'), 'CNY': (r'¥', r'元', r'\bRMB\b', r'人民币'), 'KRW': (r'₩', r'원', r'\bwon\b'),
    'INR': (r'₹', r'\bRs\.?', r'\brupees?\b'), 'THB': (r'฿', r'\bbaht\b'), 'IDR': (r'\bRp\.?', r'\brupiah\b'),
    'MYR': (r'\bRM\b', r'\bringgit\b'), 'SGD': (r'S\$', r'SG\$', r'\$'), 'HKD': (r'HK\$', r'\$'), 'AUD': (r'A\$', r'AU\$', r'\$'),
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
                      r"\b(?:need|needs|require|requires|must|shall|should|have to|has to|required to|doivent|doit|deben|debe|phải|cần)\b[^.;\n]{0,40}"
                      r"\b(?:obtain|hold|have|apply for|possess|be in possession of|get|obtenir|être munis?|obtener|xin|có)\b[^.;\n]{0,40}\b(?:e-?visa|visa|thị thực)\b|"
                      r"\b(?:can|may|eligible to|entitled to) apply for (?:an? )?(?:e-?visa|electronic visa)|"
                      r"\beligible for (?:the |an? )?(?:unified |electronic )?e-?visa|"
                      r"виз[аы] по всем|требуется виза|необходима виза|нужна виза|оформить визу|получить визу|"
                      r"需要办理签证|需申请签证|应当申请签证|必须持有签证|需要签证|事前に査証|ビザが必要|签证申请|비자.{0,6}필요|ต้องขอวีซ่า|wajib memiliki visa|harus memiliki visa|يجب الحصول على تأشيرة",
                      r"visa[- ]free|no visa|without (?:a )?visa|exempt|not required|do(?:es)? not (?:require|need)|без виз|sans visa|sin visa|miễn thị thực|"
                      r"免签|无需签证|免办签证|査証免除|ビザ免除|무비자|면제|bebas visa|ยกเว้นวีซ่า"),
    'VISA_EXEMPT': (r"visa[- ]free|visa[- ]exempt|exempt(?:ed|ion)? from (?:the )?(?:visa|obtaining a visa|visa requirement)|do(?:es)? not (?:require|need) (?:a |an |any )?(?:entry |tourist )?visa|"
                    r"without (?:a |an |the need for a )?(?:entry |tourist )?visa|no visa (?:is )?(?:required|needed|necessary)|not required to (?:obtain|hold) (?:a |an )?visa|"
                    r"без виз|безвизов|sans visa|dispensés? de visa|exemptés? de visa|sin visa|exento|exención de visa|isento|isenção de visto|visumfrei|ohne visum|senza visto|"
                    r"miễn thị thực|ยกเว้นวีซ่า|bebas visa|免签|无需签证|免办签证|查証免除|査証免除|ビザ免除|ビザなし|무비자|사증면제|معفى|إعفاء من التأشيرة|vizeden muaf",
                    r"\bnot (?:visa[- ]free|exempt|eligible)|do(?:es)? not (?:qualify|benefit)|unless|except(?:ion)? (?:for|of)?\s*(?:holders|nationals|citizens) of|"
                    r"(?:visa|e-?visa) (?:is |are )?(?:required|mandatory)|must (?:obtain|hold|apply)"),
    'ELECTRONIC_AUTHORIZATION_REQUIRED': (r"\b(?:eta|etas|esta|k-eta|evisitor|etias|nzeta|e-?ta)\b|electronic travel authori[sz]ation|travel authori[sz]ation|电子旅行授权|電子旅行許可|전자여행허가",
                                          r"not required|exempt(?:ed)? from (?:the )?(?:k-eta|eta|esta)|without (?:an? )?(?:k-eta|eta|esta)|do(?:es)? not need"),
    'VISA_ON_ARRIVAL': (r"visa[- ]on[- ]arrival|on arrival|upon arrival|at the port of entry|落地签|到着ビザ|도착비자|e-?voa",
                        r"not (?:available|eligible|issued)|no visa on arrival|cannot obtain"),
}


def _list_line(quote, aliases):
    """A quote that is essentially the nationality's own line in a list."""
    text = _norm(re.sub(r'^\s*(?:\d+[.)]|[-*•])\s*', '', quote))
    return len(text) <= 60 and any(re.search(r'(?<![a-z])' + re.escape(_norm(a)) + r'(?![a-z])', text) for a in aliases)


def _decision_supported(value, evidence_quotes, nat):
    """Strict but shaped for real official pages: the nationality must be named
    in the evidence (in the rule sentence, or as its own list line beside the
    rule sentence) and a sentence of the evidence must state the rule without
    flipping it in the same sentence."""
    from app.visa_snapshot.evidence_validator import supports_disposition, NEGATED_VISA_EXEMPTION
    passages = '\n'.join(evidence_quotes)
    aliases = _aliases(nat)
    low = _norm(passages)
    named = any(re.search(r'(?<![a-z])' + re.escape(a) + r'(?![a-z])', low) for a in aliases)
    if value == 'CONDITIONAL':
        return named and bool(_CONDITION_RE.search(passages))
    if supports_disposition(passages, value, nationality=nat):
        return True
    if not named:
        return False
    positive, negative = _VERDICT_RULES.get(value, (None, None))
    if not positive:
        return False
    if value == 'VISA_EXEMPT':
        negative = '(?:' + negative + ')|(?:' + NEGATED_VISA_EXEMPTION + ')'
    listed = any(_list_line(q, aliases) for q in evidence_quotes)
    for sentence in re.split(r'(?<=[.;!?])\s+|\n+', passages):
        sl = _norm(sentence)
        if not re.search(positive, sl, re.I):
            continue
        if negative and re.search(negative, sl, re.I):
            continue
        sentence_named = any(re.search(r'(?<![a-z])' + re.escape(a) + r'(?![a-z])', sl) for a in aliases)
        general = bool(re.search(r'\b(?:all|any|every|foreign nationals|foreigners|following countries|following states|listed below|eligible countries|countries/territories)\b', sl))
        if sentence_named or (listed and general) or (listed and value != 'VISA_EXEMPT'):
            return True
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
    aliases = _aliases(route['passport_nationality'])
    if not any(_list_line(item['quote'], aliases) for item in relevant):
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


def _check_value(field, value, passages, route, product):
    from app.visa_snapshot.evidence_validator import field_value_supported
    if field == 'disposition':
        quotes = [q for q in passages.split('\n') if q.strip()]
        if not _decision_supported(value, quotes, route['passport_nationality']):
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
                try:
                    _check_proof(proofs[key], sources, route, key, values.get(key))
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
            base = deepcopy(by_name.get(spec.get('current_name')) or {}) if action != 'add' else {}
            seen.add(spec.get('current_name'))
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
