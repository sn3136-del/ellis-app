"""Deterministic ResearchEvidenceValidator (brief "INDEPENDENT RESEARCH
VALIDATION"): an INDEPENDENT gate over Kimi's grounded extraction.

Kimi already drops fields citing an unknown/absent source (kimi_research). This
validator adds the deterministic, model-free checks the brief requires and
rejects, per material claim:
  - nonofficial source presented as authoritative  -> non_official_source
  - wrong jurisdiction for the exact route          -> jurisdiction_mismatch
  - a citation whose page text does NOT support it  -> citation_unsupported
  - an assumption presented as fact (no citation)   -> uncited_assumption
It also verifies the DISPOSITION against the cited pages' actual text (a route
is "complete" only when an official page states the disposition), and reports
which required fields are still missing so the pipeline can retry automatically.

Never weakens a safeguard and never invents: a claim it cannot verify is
rejected with a precise reason, not passed through.
"""
from __future__ import annotations

import re
import unicodedata

from .authority import hostname, is_government_host, registrable_domain
from .authority_ownership import government_owner

# Shared with detached reviewed-batch validation so a fallback cannot turn an
# explicitly denied exemption back into a positive visa-free decision.
NEGATED_VISA_EXEMPTION = (
    r"\bnot (?:visa[- ]free|visa[- ]exempt|exempt(?:ed)? from (?:a |the )?visa)|"
    r"\b(?:not|never) (?:eligible|entitled|qualified) (?:for|to) visa[- ]free|"
    r"visa[- ](?:free|exempt)(?: entry| access| travel)? (?:is|are) not "
    r"(?:available|permitted|allowed|possible|applicable|offered|granted|provided)\b"
)

# Disposition -> (positive support patterns, negative/contradiction patterns).
# Multilingual (English / Simplified Chinese / Spanish). A page "supports" a
# disposition when a positive pattern matches AND no negative pattern matches.
_DISPOSITION_SUPPORT = {
    "VISA_REQUIRED": (
        r"\bvisa (is |are )?required\b|must (obtain|apply for|hold) (a |an )?"
        r"(?:(?:tourist|visitor|business|transit|entry)(?: \([a-z]\))? )?(e-?visa|visa)|"
        r"need(s|ed)? (a |an )?(e-?visa|visa)|"
        r"需要办理签证|需申请签证|应当申请签证|必须持有签证|需要签证|"
        r"requiere (una )?visa|necesita(n)? (una )?visa|debe(n)? tramitar (una )?visa",
        # Negations (English/Spanish/Chinese) that flip a "visa" mention to
        # visa-free — must NOT be read as a requirement.
        r"visa[- ]free|no visa (is )?required|without a visa|visa exemption|visa on arrival|"
        r"免签|无需签证|免办签证|"
        r"no (se )?requiere(n)?( de)? (una )?visa|no necesita(n)?( de)? (una )?visa|"
        r"do(es)? not (require|need)( a| an)? visa|not require a visa|"
        r"sin( necesidad de)? visa|exent",
    ),
    "VISA_FREE": (
        r"visa[- ]free|no visa (is )?required|do(es)? not (require|need) a visa|"
        r"without a visa|visa exemption|visa[- ]exempt|免签|无需签证|免办签证|"
        r"no (se )?requiere(n)? (de )?visa|sin (necesidad de )?visa|exent[ao]s? de visa|"
        r"no necesita(n)? visa|not require a visa|may enter.*without a visa",
        # NOT tourism-relevant visa-free: transit-only exemptions (e.g. China's
        # visa-free transit) do not make a TOURIST route visa-free.
        r"must (obtain|apply for) a visa|(?<!no )visa is required|需要签证|requiere visa|"
        r"transit|过境|tr[aá]nsito|" + NEGATED_VISA_EXEMPTION,
    ),
    "EVISA_REQUIRED": (
        # Must be an e-VISA specifically — never a mere "electronic form" (e.g.
        # Mexico's electronic FMM entry permit is NOT an e-visa).
        r"\be-?visa\b|electronic visa|电子签证|visa electr[oó]nica",
        # ...and never a mention of a DIFFERENT region's e-visa (e.g. a Hong
        # Kong / Macao e-visa headline on a mainland-China page).
        r"visa[- ]free|no visa (is )?required|do(es)? not (require|need) a visa|免签|"
        r"no (se )?requiere(n)? visa|no necesita(n)? visa|"
        r"hong kong|macao|macau|香港|澳门|澳門",
    ),
    "VISA_ON_ARRIVAL": (
        r"visa[- ]on[- ]arrival|visa.{0,20}(on|upon) arrival|落地签|落地簽",
        r"not available|not eligible|not issued|no visa on arrival|must apply.{0,20}before",
    ),
    "ETA_REQUIRED": (
        r"(?:must|require[ds]?|needs?)[^.!?\n]{0,80}(?:electronic travel authori[sz]|\beta\b|\besta\b)|"
        r"(?:electronic travel authori[sz][a-z ]*|\beta\b|\besta\b)[^.!?\n]{0,50}(?:is required|mandatory)|"
        r"必须.{0,12}电子旅行授权|需要.{0,12}电子旅行授权",
        r"not required|do(es)? not (require|need)|无需|不需要",
    ),
}

# Applicant-nationality references (for nationality-anchored detection). A
# disposition only grounds when the applicant's nationality — or a universal
# marker ("all foreign nationals", "citizens of the following countries") —
# appears near the disposition statement, so a page's rule for OTHER
# nationalities never grounds this route.
_NATIONALITY_NAMES = {
    # Chinese anchors use CITIZEN-forms (美国公民/美国护照/美籍) — a bare 美国 also
    # matches "赴美国" ("traveling TO the US") in news headlines, which is a
    # destination mention, not the applicant's nationality.
    "USA": ("united states", "u.s.", "u.s.a", " us ", "us citizen", "us national",
            "u.s. citizen", "american citizen", "american citizens", "americans", "estados unidos",
            "estadounidense", "美国公民", "美国护照", "美国国民", "美籍"),
    "CHN": ("china", "chinese", "中国"), "MEX": ("mexico", "méxico", "mexican"),
    "GBR": ("united kingdom", "british", "u.k."), "CAN": ("canada", "canadian"),
    "HKG": ("hong kong", "hksar", "香港"),
    "TWN": ("taiwan", "taiwanese", "台湾", "台灣", "臺灣"),
    "JPN": ("japan", "japanese", "日本"), "KOR": ("south korea", "republic of korea", "korean", "韩国", "韓國"),
    "THA": ("thailand", "thai", "泰国", "泰國"), "MYS": ("malaysia", "malaysian", "马来西亚", "馬來西亞"),
    "RUS": ("russia", "russian", "俄罗斯", "俄羅斯"), "AUS": ("australia", "australian", "澳大利亚", "澳大利亞"),
    "IDN": ("indonesia", "indonesian", "印度尼西亚", "印度尼西亞"),
    "PHL": ("philippines", "philippine", "filipino", "菲律宾", "菲律賓"),
    "FRA": ("france", "french", "法国", "法國"), "ESP": ("spain", "spanish", "西班牙"),
    "IND": ("india", "indian"), "VNM": ("vietnam", "vietnamese"),
    "KHM": ("cambodia", "cambodian"), "SGP": ("singapore", "singaporean"),
}
_UNIVERSAL_MARKERS = (
    "all foreign", "all nationalities", "any nationality", "foreign citizens",
    "foreign nationals", "citizens of all countries", "regardless of nationality",
    "todos los", "todas las nacionalidades", "extranjeros", "所有外国", "外国公民",
    "following countries", "siguientes países", "list of countries",
)


# A diplomatic mission's own name ("Embassy of X in the United States", "驻美国
# 使馆") states where the MISSION is, not the applicant's nationality — it must
# never anchor a disposition. Stripped before anchoring.
_MISSION_SELF_RE = re.compile(
    r"(embassy|consulate([- ]general)?)[^.\n]{0,100}?in the united states( of america)?|"
    r"embajada[^.\n]{0,80}?en (los )?estados unidos|驻美国?[使领]馆|驻美使领馆",
    re.I)


def _statement_bounds(text: str, at: int, window: int = 500) -> tuple[int, int]:
    # Preserve abbreviation offsets while recognizing actual sentence/row and
    # contrast boundaries. A nationality in the previous rule is not a subject
    # of this one merely because it is fewer than 500 characters away.
    masked = re.sub(r"\b(?:[a-z]\.){2,}", lambda m: m.group().replace(".", " "), text, flags=re.I)
    breaks = list(re.finditer(r"[.!?。！？;\n]|\b(?:whereas|but|while)\b", masked, re.I))
    left, right = max(0, at - window), min(len(text), at + window)
    for boundary in breaks:
        if boundary.end() <= at:
            left = max(left, boundary.end())
        elif boundary.start() >= at:
            right = min(right, boundary.start())
            break
    return left, right


def _nationality_anchored(text: str, nationality: str, at: int, *, window: int = 500) -> bool:
    """Require this applicant in the same statement as the disposition.
    Country lists must name the applicant in that statement. A universal list
    heading or a mention on another row never proves membership in the list."""
    text = _MISSION_SELF_RE.sub(lambda m: " " * len(m.group(0)), text or "")
    low = text.lower()
    lo, hi = _statement_bounds(low, at, window)
    seg = low[lo:hi]
    names = _NATIONALITY_NAMES.get((nationality or "").upper(), ())
    for name in names:
        name = name.strip()
        # Word boundaries avoid India matching an unrelated longer word.
        pattern = r"(?<![a-z])" + re.escape(name) + r"(?![a-z])"
        for match in re.finditer(pattern, seg):
            before = seg[max(0, match.start() - 35):match.start()]
            if re.search(r"(?:travel(?:l?ing)?|travell?ers?|enter(?:ing)?|visits?|flights?)\s+(?:to\s+)?$|\bto\s+(?:the\s+)?$", before):
                continue  # destination mention, not passport nationality
            return True
    return False

# Canonical disposition aliases coming from the pipeline.
_DISP_ALIASES = {
    "VISA_EXEMPT": "VISA_FREE", "VISA_FREE": "VISA_FREE",
    "VISA_ON_ARRIVAL": "VISA_ON_ARRIVAL",
    "VISA_REQUIRED": "VISA_REQUIRED", "EMBASSY_VISA_REQUIRED": "VISA_REQUIRED",
    "AUTHORIZED_VISA_CENTER_REQUIRED": "VISA_REQUIRED",
    "EVISA_REQUIRED": "EVISA_REQUIRED", "ETA_REQUIRED": "ETA_REQUIRED",
    "ELECTRONIC_AUTHORIZATION_REQUIRED": "ETA_REQUIRED",
}


def _page_by_url(pages: list[dict], url: str) -> dict | None:
    for p in pages:
        if p.get("url") == url or p.get("final_url") == url:
            return p
    # tolerant match on hostname+path
    for p in pages:
        if p.get("url") and url and p["url"].split("?")[0] == url.split("?")[0]:
            return p
    return None


def source_is_official(url: str) -> bool:
    return is_government_host(hostname(url))


def jurisdiction_matches(url: str, destination: str) -> bool:
    """Require reviewed government ownership, including official missions.
    Recognition as an official host alone never supplies country ownership.

    A thin wrapper over source_authority.is_competent (guard-20260912 T3),
    so every call site shares the one jurisdiction rule: the destination's
    own government, or Union visa law naming its instrument for a Schengen
    destination. The signature is unchanged."""
    from .source_authority import is_competent
    if not is_government_host(hostname(url)):
        return False
    return is_competent(url, {"destination_country": (destination or "").upper()}, "disposition")


def _supporting_match(text: str, disposition: str, nationality: str = ""):
    canon = _DISP_ALIASES.get((disposition or "").upper())
    spec = _DISPOSITION_SUPPORT.get(canon or "")
    if not spec:
        return None
    pos, neg = spec
    low = (text or "").lower()
    for m in re.finditer(pos, low, re.I):
        lo, hi = _statement_bounds(low, m.start())
        if neg and re.search(neg, low[max(lo, m.start() - 60):min(hi, m.end() + 120)], re.I):
            continue
        if nationality and not _nationality_anchored(text, nationality, m.start()):
            continue
        return m
    return None


def supports_disposition(text: str, disposition: str, *, nationality: str = "") -> bool:
    """Require explicit disposition evidence for this applicant in the same
    statement. A title, program name, unrelated rule or list header is not a
    statement that this applicant needs (or is exempt from) that permission."""
    return _supporting_match(text, disposition, nationality) is not None


def quote_in_text(quote: str, text: str) -> bool:
    """Match the WHOLE quote after Unicode, case and whitespace normalization.
    A true prefix followed by invented words never counts as a quotation."""
    def normalized(value):
        return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())
    needle = normalized(quote)
    return bool(needle) and needle in normalized(text)


def route_supporting_excerpt(text: str, disposition: str, route: dict, *, policy_date: str = "") -> str:
    """An applicant, document, purpose and effective date belong to one rule.
    A different passport category or a future announcement is not today's rule.
    Generic ordinary visitor language is allowed; exceptional documents and
    non-visitor purposes require their own explicit scope."""
    from datetime import date, datetime
    try:
        on = date.fromisoformat(policy_date[:10]) if policy_date else date.today()
    except (ValueError, TypeError):
        on = date.today()
    nationality = route.get("passport_nationality") or ""
    if not nationality:
        return ""
    document = route.get("travel_document_type") or "ordinary_passport"
    purpose = route.get("travel_purpose") or "tourism"
    doc_words = {"diplomatic_passport": r"diplomatic|外交", "service_passport": r"service|official|公务|公務",
                 "official_passport": r"official|service|公务|公務", "prc_travel_document": r"travel document|旅行证|旅行證"}
    purpose_words = {"tourism": r"touris[mt]|holiday|visitor|旅游|旅遊", "business": r"business|商务|商務",
                     "work": r"work|employment|工作", "study": r"stud[ye]|student|education|留学|留學",
                     "transit": r"transit|过境|過境"}
    canon = _DISP_ALIASES.get(str(disposition or "").upper())
    if canon not in _DISPOSITION_SUPPORT:
        return ""
    for match in re.finditer(_DISPOSITION_SUPPORT[canon][0], text or "", re.I):
        lo, hi = _statement_bounds(text, match.start())
        statement = text[lo:hi]
        if not _supporting_match(statement, disposition, nationality):
            continue
        if document == "ordinary_passport":
            if re.search(r"diplomatic|service passport|official passport|travel document|外交|公务|公務|旅行证|旅行證", statement, re.I):
                continue
        elif document not in doc_words or not re.search(doc_words[document], statement, re.I):
            continue
        own = purpose_words.get(purpose)
        if not own:
            continue
        other_scope = any(re.search(pattern, statement, re.I) for key, pattern in purpose_words.items() if key != purpose)
        if (other_scope or purpose != "tourism") and not re.search(own, statement, re.I):
            continue
        # Includes a immediately preceding dated heading, but does not scan
        # arbitrary future dates elsewhere on a long policy page.
        context = text[max(0, lo - 100):hi]
        future = False
        date_pattern = r"(?:from|effective(?:\s+from)?|starting|as of|beginning)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})"
        for dated in re.finditer(date_pattern, context, re.I):
            value = dated.group(1).replace(",", "")
            for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
                try:
                    future |= datetime.strptime(value, fmt).date() > on
                    break
                except ValueError:
                    pass
        if not future:
            return statement.strip()
    return ""


def field_value_supported(name: str, value, text: str) -> bool:
    """Conservative claim matching, never a shared currency/token shortcut.
    All numbers and substantive value tokens must occur in the cited text;
    booleans require an explicit statement about that specific requirement."""
    if value is None or value == "" or value == [] or value == {}:
        return False
    low = " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())
    if isinstance(value, bool):
        topic = {"biometrics_required": r"biometric|fingerprint", "appointment_required": r"appointment",
                 "interview_required": r"interview"}.get(name)
        if not topic:
            return False
        negative = rf"(?:no|not|without)[^.!?]{{0,35}}(?:{topic})|(?:{topic})[^.!?]{{0,35}}(?:not required|not needed|waived)"
        positive = rf"(?:{topic})[^.!?]{{0,35}}(?:required|mandatory)|(?:must|require)[^.!?]{{0,35}}(?:{topic})"
        return bool(re.search(positive if value else negative, low)) and not (value and re.search(negative, low))
    if name == "disposition":
        return supports_disposition(text, value)
    if name == "application_channel":
        # The serving schema uses these names; the original matcher only
        # recognized legacy online/visa_application_centre/none. A current
        # channel therefore could never be reconfirmed from a fresh quote.
        # Require an actual application instruction, not a payment/tracking
        # page or the mere availability of a visa product.
        if value == "in_person":
            # A general filing claim needs an unqualified application rule.
            # Attendance, eligibility conditions and other public services do
            # not prove that this visa/permit may be applied for in person.
            if re.search(
                    r"\b(?:not|never|cannot|can't|ineligible|prohibited|unavailable|if|unless|except|only|"
                    r"renew|renewal|replacement|driving|driver|licen[cs]e|biometrics?|fingerprints?|"
                    r"agents?|agencies|representatives?)\b|\bprovided (?:that|you)\b|\bsubject to\b|"
                    r"不应当|不應當|无需|無需|毋须|毋須|不得|不能|不可以|不接受|仅限|僅限|"
                    r"如果|倘若|若是|只限|限于|限於", low):
                return False
            # Exact mainland-permit application syntax from the current NIA
            # instruction; its following under-18 guardian rule is preserved.
            if re.fullmatch(
                    r"(?:\(一\)\s*)?港澳居民申请港澳居民来往内地通行证应当本人前往受理机构提出申请"
                    r"(?:,未满十八周岁的申请人须由法定监护人陪同申请并提供法定监护人的身份证件)?[。.]?", low):
                return True
            # A standalone general visa/permit filing sentence cannot carry
            # an unreviewed conditional or service-specific tail.
            return bool(re.fullmatch(
                r"(?:(?:applicants |you )(?:must|should|may|can) )?"
                r"(?:submit|lodge|make) (?:an? |the |your )?(?:visa |permit )application(?: form)? in person[.!]?|"
                r"(?:visa|permit) applications? (?:must|may|can|should) be (?:submitted|lodged|made) in person[.!]?", low))
        canonical = {
            "online_portal": r"\bapply(?:ing)?\s+online\b|"
                             r"\bapply(?:ing)?\s+for\s+(?:an? |the )?(?:[a-z-]+\s+){0,3}visa\s+online\b|"
                             r"\bapplications?\s+(?:(?:must|may|can|should)\s+be|are|is)\s+(?:submitted|lodged|made)\s+online\b|"
                             r"\b(?:submit|lodge)\s+(?:an? |the |your )?(?:visa |e-visa )?application(?: form)?\s+(?:online|electronically)\b|"
                             r"\b(?:website|portal)\b[^.!?;\n]{0,60}\b(?:to apply|for applying|to submit|to lodge)\b|"
                             r"\bsubmit\s+(?:an? |the |your )?application\s+(?:from|through|via|on)\b[^.!?;,\n]{0,115}\b(?:website|portal)\b",
            "visa_center": r"\bapply\b[^.!?;\n]{0,70}\bvisa application cent(?:re|er)\b|"
                           r"\b(?:submit|lodge|submission of|lodgement of)\s+(?:an? |the |your )?(?:visa )?applications?\b[^.!?;\n]{0,70}\bvisa application cent(?:re|er)\b|"
                           r"\bvisa application cent(?:re|er)\b[^.!?;\n]{0,60}\baccepts? (?:visa )?applications?\b",
            "not_required": r"\bno (?:advance )?application (?:is )?(?:required|necessary|needed)\b|"
                            r"\b(?:do|does) not need to (?:submit|make|lodge) (?:an? )?application\b",
        }
        if isinstance(value, str) and value in canonical:
            if value in {'online_portal', 'visa_center'} and re.search(
                    r"\b(?:cannot|can't|do not|does not|must not|may not|not permitted|not allowed|not eligible|not accepted|not available|unavailable|ineligible|prohibited)\b|"
                    r"\b(?:cannot|can't|may not|must not|do not|does not|not permitted to)\s+(?:apply|submit|lodge)|"
                    r"\b(?:not eligible|ineligible|not allowed|not permitted|prohibited|do not|must not|cannot|can't)\b[^.!?;\n]{0,100}\b(?:apply|submit|lodge)\b|"
                    r"\b(?:online applications?|applications? online)\b[^.!?;\n]{0,25}\b(?:not accepted|unavailable|not available)\b|"
                    r"\b(?:only|must)\b[^.!?;\n]{0,45}\b(?:through|via|by)\b[^.!?;\n]{0,30}\b(?:agent|agency)\b|"
                    r"\b(?:agents?|agencies|representatives?)\s+(?:(?:must|may|can)\s+)?(?:apply|submit|lodge)\b", low):
                return False
            if value in {'online_portal', 'visa_center'} and re.search(
                    r"\b(?:appointments?|appointment[- ]only|fingerprints?|biometrics?|if|unless|except)\b|"
                    r"\bonly when\b|\bprovided (?:that|you)\b|\bsubject to\b", low):
                # Booking a visit or submitting biometrics does not establish
                # where the visa application itself may be lodged.
                return False
            if value == 'online_portal' and re.search(
                    r"\b(?:print|printed|in person|by post|embassy|consulate|visa application cent(?:re|er))\b", low):
                return False
            if value == 'not_required' and re.search(
                    r"\b(?:unless|except|if|children|child|under|residents?)\b|"
                    r"\b(?:must|shall|need to|required to)\b[^.!?;\n]{0,30}\b(?:apply|submit|lodge)\b", low):
                return False
            if value == 'not_required':
                # A cancelled application, fee payment or other limited
                # procedure cannot prove a general no-application route.
                return bool(re.fullmatch(r"(?:" + canonical[value] + r")[.!]?", low))
            return bool(re.search(canonical[value], low))
        pattern = {"authorised_agent": r"accredited (?:travel )?agen|authori[sz]ed agen",
                   "embassy": r"embassy|consulate|mission", "visa_application_centre": r"visa application cent",
                   "evisa": r"e-?visa|electronic visa", "online": r"online|electronic|e-?visa",
                   "none": r"no application|visa[- ]free|without a visa", "on_arrival": r"on arrival|upon arrival"}.get(str(value))
        return bool(pattern and re.search(pattern, low))
    if name in {"permitted_stay", "permitted_stay_days", "max_stay_days"} and not re.search(r"stay|visit|remain|停留|逗留", low):
        return False
    if name == "processing_time" and not re.search(r"process|processing|处理|處理|审理|審理", low):
        return False
    if name == 'visa_products':
        if isinstance(value,list):
            return bool(value) and all(field_value_supported(name,product,text) for product in value)
        if not isinstance(value,dict) or not isinstance(value.get('type'),str) or not value['type'].strip():
            return False
        # Composite product fields cannot borrow a different product's fee
        # elsewhere on a page. Unstructured proof must bind every claim to
        # the same literal named-product statement; richer tables need their
        # own reviewed structured evidence and remain unverified here.
        metadata={'source_url','source_quote','verified_at','verifier','corroborating_sources'}
        for statement in re.split(r'(?<=[.!?])\s+|\n+',str(text)):
            if not quote_in_text(value['type'],statement):
                continue
            fee=value.get('fee')
            if isinstance(fee,dict) and fee.get('amount') is not None:
                if (not re.search(r'fee|cost|pay|price',statement,re.I)
                        or re.search(r'\bnot\b|\bno\b|funds|balance|income|insurance|deposit',statement,re.I)):
                    continue
                currency=re.escape(str(fee.get('currency','')))
                amounts=re.findall(r'(?<![\d.])([\d,]+(?:\.\d+)?)\s*'+currency+r'\b|\b'+currency+r'\s*([\d,]+(?:\.\d+)?)',statement,re.I)
                if len({(a or b).replace(',','') for a,b in amounts})>1:
                    continue
            if all(field_value_supported(k,v,statement) for k,v in value.items()
                   if k not in metadata|{'type'} and v not in (None,'',[],{})):
                return True
        return False
    if name == 'passport_validity_requirement':
        from app.passport_validity import passport_validity_rule_errors, normalize_passport_validity_rule
        value = normalize_passport_validity_rule(value)
        if passport_validity_rule_errors(value) or not isinstance(value, dict):
            return False
        if re.search(r'\b(?:not|no|if|unless|except)\b|provided that|only when|residen',low):
            return False
        if not re.search(r'passport|travel document', low):
            return False
        kind = value.get('kind')
        if kind == 'valid_through_departure':
            return bool(re.search(r'valid.{0,50}(?:entire|full|duration of|period of|throughout).{0,25}stay|valid.{0,45}(?:until|through).{0,20}(?:departure|end of.{0,10}stay)', low)) and not re.search(r'\bmonths?\b|application',low)
        if kind == 'valid_on_arrival':
            return bool(re.search(r'valid.{0,30}(?:on|at).{0,10}(?:arrival|entry)',low)) and not re.search(r'\bmonths?\b|application',low)
        anchor = 'arrival|entry' if kind == 'months_after_arrival' else 'departure|end of.{0,10}stay'
        return bool(re.search(r'(?<!\d)' + str(value['months']) + r'\s+months?.{0,35}(?:after|from|beyond).{0,25}(?:' + anchor + ')',low)) and 'application' not in low
    if isinstance(value, dict):
        if "amount" in value and "currency" in value:
            from decimal import Decimal, InvalidOperation
            currency = str(value['currency']).casefold()
            monetary = re.sub(r'\bCAN\s*\$|\$\s*CAN\b|\bCanadian dollars?\b', 'CAD ', low, flags=re.I) if currency == 'cad' else low
            monetary = re.sub(r'(?<![\d,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])', lambda m:m.group().replace(',', ''), monetary)
            number = r'(?<![\d.,-])(\d+(?:\.\d+)?)(?!\d|[.,]\d)'
            matches = re.finditer(r'(?:' + number + r'\s*' + re.escape(currency) + r'\b|\b' + re.escape(currency) + r'\s*' + number + r')', monetary, re.I)
            try:
                expected = Decimal(str(value['amount']))
                supported = expected.is_finite() and any(Decimal(m.group(1) or m.group(2)) == expected for m in matches)
            except (InvalidOperation, ValueError, TypeError):
                supported = False
            if not supported:
                return False
            return all(field_value_supported(k, v, text) for k, v in value.items()
                       if k not in {'amount', 'currency'} and v not in (None, '', [], {}))
        return all(field_value_supported(k, v, text) for k, v in value.items() if v not in (None, "", [], {})) and any(v not in (None, "", [], {}) for v in value.values())
    if isinstance(value, list):
        return bool(value) and all(field_value_supported(name, item, text) for item in value)
    # Decimal numbers must match in full: 7 never proves 70 or 999; a currency
    # mention alone never proves an amount. Keep all numbers, including <100.
    raw = str(value).replace("_", " ").casefold()
    numbers = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", raw)
    if any(not re.search(r"(?<![\w.])" + re.escape(n) + r"(?![\w.])", low) for n in numbers):
        return False
    if isinstance(value, (int, float)):
        return bool(numbers)
    if quote_in_text(raw, low):
        return True
    ignored = {"the", "and", "for", "with", "are", "was", "were", "can", "may", "must", "as", "at", "by", "of", "or", "to", "a", "an", "is", "in", "be", "up", "per"}
    tokens = [t for t in re.findall(r"[a-z]+", raw) if t not in ignored]
    return bool(tokens or numbers) and all(re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low) for t in tokens)



def find_supporting_excerpt(text: str, disposition: str, *, window: int = 240,
                            nationality: str = "") -> str:
    # The quote must come from the SAME match that passed, not a rejected
    # earlier headline or another nationality's rule on this page.
    m = _supporting_match(text, disposition, nationality)
    if m is None:
        return ""
    lo, hi = _statement_bounds(text, m.start())
    if hi - lo <= window:
        return text[lo:hi].strip()
    a = max(lo, m.start() - window // 2)
    return text[a:min(hi, a + window)].strip()


def validate_disposition(route: dict, disposition: str, source_urls: list[str],
                         pages: list[dict]) -> dict:
    """Independently verify a disposition. Returns
    {ok, reasons[], supporting_url, supporting_excerpt}."""
    dest = (route or {}).get("destination_country", "")
    nat = (route or {}).get("passport_nationality", "")
    reasons: list[str] = []
    if not disposition:
        return {"ok": False, "reasons": ["no disposition proposed"],
                "supporting_url": "", "supporting_excerpt": ""}
    if not source_urls:
        return {"ok": False, "reasons": ["uncited_assumption: disposition has no source"],
                "supporting_url": "", "supporting_excerpt": ""}
    supporting_url, supporting_excerpt = "", ""
    official = [u for u in source_urls if source_is_official(u)]
    if not official:
        reasons.append("non_official_source: no cited source is a government domain")
    for url in official:
        if not jurisdiction_matches(url, dest):
            reasons.append(f"jurisdiction_mismatch: {hostname(url)} is not a {dest} official domain")
            continue
        page = _page_by_url(pages, url)
        text = (page or {}).get("text", "")
        if page is None:
            reasons.append(f"citation_unfetched: {url}")
            continue
        excerpt = route_supporting_excerpt(text, disposition, route)
        if excerpt:
            supporting_url = url
            supporting_excerpt = excerpt
            break
        reasons.append(f"citation_unsupported: {hostname(url)} text does not state {disposition}")
    ok = bool(supporting_url)
    if ok:
        reasons = []   # a valid supporting source overrides the per-source notes
    return {"ok": ok, "reasons": reasons[:8],
            "supporting_url": supporting_url, "supporting_excerpt": supporting_excerpt}


def validate_field(route: dict, name: str, value, source_urls: list[str],
                   pages: list[dict]) -> dict:
    """Generic per-field validation: at least one official, jurisdiction-matched
    source whose page text mentions the value (or a value token)."""
    dest = (route or {}).get("destination_country", "")
    if not source_urls:
        return {"ok": False, "reason": "uncited_assumption"}
    official = [u for u in source_urls if source_is_official(u)]
    if not official:
        return {"ok": False, "reason": "non_official_source"}
    for url in official:
        if not jurisdiction_matches(url, dest):
            continue
        page = _page_by_url(pages, url)
        if page is None:
            continue
        low = (page.get("text", "") or "").lower()
        if field_value_supported(name, value, low):
            return {"ok": True, "reason": "", "url": url}
    return {"ok": False, "reason": "citation_unsupported"}


# Pipeline-canonical dispositions, in specificity order (electronic programs win
# over the generic required/free classification). A generic "visa required"
# resolves to EMBASSY_VISA_REQUIRED (the consular application channel).
_DISP_SPECIFICITY = ("EVISA_REQUIRED", "ETA_REQUIRED", "VISA_ON_ARRIVAL", "VISA_FREE", "EMBASSY_VISA_REQUIRED")


def detect_disposition_from_pages(route: dict, pages: list[dict]) -> dict:
    """Deterministic, EVIDENCE-GROUNDED disposition detection: scan the fetched
    OFFICIAL, jurisdiction-matched pages and report the disposition their text
    actually states (with the supporting url + excerpt). This confirms from real
    page text — it never invents. Returns
    {disposition, supporting_url, supporting_excerpt, attested[], conflict}.
    Missing/ambiguous evidence yields disposition=None (never visa-free by
    default)."""
    dest = (route or {}).get("destination_country", "")
    nat = (route or {}).get("passport_nationality", "")
    attested: dict[str, list[str]] = {}
    excerpt_for: dict[str, tuple] = {}
    for p in pages:
        url = p.get("url") or p.get("final_url") or ""
        if not source_is_official(url) or not jurisdiction_matches(url, dest):
            continue
        text = p.get("text", "") or ""
        # Per-page attestation. A page that attests MULTIPLE contradictory
        # dispositions (e.g. a headline list mentioning both "e-visa" and
        # "visa-free") is NOISE — it is skipped, never allowed to ground or to
        # manufacture a conflict. Only a page with one clear disposition counts.
        page_disps = [d for d in _DISP_SPECIFICITY
                      if route_supporting_excerpt(text, d, route)]
        if len(page_disps) != 1:
            continue
        disp = page_disps[0]
        attested.setdefault(disp, []).append(url)
        excerpt_for.setdefault(disp, (url, route_supporting_excerpt(text, disp, route)))
    if not attested:
        return {"disposition": None, "supporting_url": "", "supporting_excerpt": "",
                "attested": {}, "conflict": False}
    # VISA_FREE and EMBASSY_VISA_REQUIRED directly contradict; treat
    # co-attestation as a conflict to resolve, never silently pick one.
    conflict = ("VISA_FREE" in attested and bool({"EMBASSY_VISA_REQUIRED", "VISA_ON_ARRIVAL"} & set(attested)))
    chosen = next((d for d in _DISP_SPECIFICITY if d in attested), None)
    url, exc = excerpt_for.get(chosen, ("", ""))
    return {"disposition": chosen, "supporting_url": url, "supporting_excerpt": exc,
            "attested": {k: v[:3] for k, v in attested.items()}, "conflict": conflict}


def validate_extraction(route: dict, extraction: dict, pages: list[dict], *,
                        disposition: str = "", disposition_sources: list[str] | None = None,
                        required_fields: tuple = ()) -> dict:
    """Full independent validation pass. Returns accepted/rejected field notes,
    the disposition verdict, and the still-missing required fields (for retry)."""
    fields = (extraction or {}).get("fields", {}) or {}
    rejected: list[dict] = []
    accepted: list[str] = []
    for name, payload in fields.items():
        v = validate_field(route, name, (payload or {}).get("value"),
                           (payload or {}).get("sources") or [], pages)
        if v["ok"]:
            accepted.append(name)
        else:
            rejected.append({"field": name, "reason": v["reason"]})
    disp_verdict = validate_disposition(route, disposition,
                                        disposition_sources or [], pages)
    present = set(fields.keys())
    missing = [f for f in required_fields if f not in present]
    if not disp_verdict["ok"]:
        missing = ["disposition"] + [m for m in missing if m != "disposition"]
    return {"accepted": accepted, "rejected": rejected,
            "disposition": disp_verdict, "missing_fields": missing,
            "ok": disp_verdict["ok"]}
