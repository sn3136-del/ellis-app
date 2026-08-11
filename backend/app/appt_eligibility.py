"""Appointment eligibility triage — does an in-person act have to happen at all?

This is the first station of the appointment cockpit (docs/APPOINTMENTS_DESIGN.md
§"What Ellis builds", item 1). Everything downstream — the pre-stage, the group
roster, the availability surface — is cheaper or unnecessary depending on the
answer, and the answer is a matter of published rule, not judgement. So it is
CURATED DATA with sources and an ``as_of`` date, in the style of
``h1b/guidance.py``: no model is ever in this loop.

Two bodies of rule, one entry point:

  US.  The nonimmigrant interview waiver ("dropbox") was narrowed sharply in
  2025 and now covers a short, closed list. Anything outside that list needs an
  in-person interview — H-1B included, permanently, which is why
  ``us_appointment_needed`` can answer for H-1B without knowing the post. EVUS
  is the other US-side question and is NOT an appointment: it is an online
  enrolment a PRC national with a 10-year B1/B2 refreshes before travel.

  Schengen.  Visa Code Art. 13(3): fingerprints already in VIS from an
  application made LESS THAN 59 months ago are copied into the new application.
  That single fact decides the whole route. Inside 59 months, and where the
  member state permits submission without appearance, an Art. 45 accredited
  intermediary can carry the filing end to end with no applicant act. First-time
  or lapsed, personal appearance is NON-DELEGABLE — Art. 45(2) bars the
  intermediary from collecting fingerprints and Art. 43 bars external-service
  access to VIS. No amount of product work moves that gate.

Three disciplines this module does not bend:

  1. UNKNOWN IS AN ANSWER. Every verdict is ``True``, ``False`` or
     ``"unknown"``. A fact that is missing, a category not on the curated list,
     a member state whose appearance policy has not been verified by a human —
     all return ``"unknown"`` plus the official source (or the exact document)
     that would settle it. Nothing is defaulted, because a wrong "no interview
     needed" costs the applicant a trip and a season.
  2. THE CONSERVATIVE DIRECTION IS "SHOW UP". Where curated rules overlap, the
     verdict that requires the human act wins. Telling someone to attend an
     interview they did not need wastes a morning; telling them to skip one
     they needed loses the case.
  3. NEAR THE BOUNDARY, A PROXY IS NOT A DATE. The 59-month clock runs from the
     VIS ENROLMENT date (the prior APPLICATION), which precedes the prior visa
     sticker's issue date. So the sticker is accepted as a proxy, but a proxy
     that lands within ``_PROXY_MARGIN_DAYS`` of the boundary on the "reuse"
     side returns ``"unknown"`` — the true enrolment date is always EARLIER,
     and could be over the line.

Nothing here books, searches, or scrapes anything: it is pure data plus
arithmetic over facts the case already holds. Every calendar value is
normalized through ``app.dates`` before it is compared.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from functools import lru_cache

from . import dates
from .visa_snapshot import registry

# Curated on this date against the official pages cited in SOURCES. Anything
# older than _FRESH_DAYS must be re-verified by a human before it is shown next
# to an appointment instruction (see `freshness`).
AS_OF = "2026-08-11"
_FRESH_DAYS = 180

UNKNOWN = "unknown"

# The 59-month VIS fingerprint reuse window (Visa Code Art. 13(3)).
VIS_REUSE_MONTHS = 59
# A prior-visa ISSUE date is a proxy for the VIS enrolment date and always
# LATER than it. Within this margin of the boundary the proxy cannot prove
# reuse, so the verdict degrades to "unknown" rather than guessing in the
# applicant's favour.
_PROXY_MARGIN_DAYS = 60


# ---------------------------------------------------------------------------
# Sources. Every curated row names one of these ids; `_cite` resolves them so a
# payload can never carry a rule without the page it came from.

SOURCES = {
    "dos_interview_waiver_2025": {
        "authority": "U.S. Department of State, Bureau of Consular Affairs",
        "title": "Interview Waiver Update (Sept 18, 2025)",
        "url": "https://travel.state.gov/content/travel/en/News/visas-news/"
               "interview-waiver-update-sept-18-2025.html",
    },
    "dos_visa_appointment_wait_times": {
        "authority": "U.S. Department of State",
        "title": "Global Visa Wait Times / appointment information",
        "url": "https://travel.state.gov/content/travel/en/us-visas/"
               "visa-information-resources/wait-times.html",
    },
    "mission_china_visas": {
        "authority": "U.S. Mission China",
        "title": "Visas — U.S. Embassy & Consulates in China",
        "url": "https://china.usembassy-china.org.cn/visas/",
    },
    "usvisascheduling": {
        "authority": "CGI Federal (Atlas360), U.S. Mission China appointment system",
        "title": "U.S. visa appointment service",
        "url": "https://www.usvisascheduling.com/",
    },
    "cbp_evus": {
        "authority": "U.S. Customs and Border Protection",
        "title": "Electronic Visa Update System (EVUS)",
        "url": "https://www.evus.gov/",
    },
    "visa_code_art13": {
        "authority": "European Union",
        "title": "Regulation (EC) No 810/2009 (Visa Code), Art. 13 — biometric "
                 "identifiers, incl. Art. 13(3) 59-month reuse and Art. 13(7) "
                 "exemptions",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32009R0810",
    },
    "visa_code_art43_45": {
        "authority": "European Union",
        "title": "Regulation (EC) No 810/2009 (Visa Code), Art. 43 (external "
                 "service providers) and Art. 45 (accredited commercial "
                 "intermediaries)",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32009R0810",
    },
    "vis_recast_2021_1134": {
        "authority": "European Union",
        "title": "Regulation (EU) 2021/1134 (VIS recast) — lowers the visa "
                 "fingerprinting age from 12 to 6 as of the recast VIS entering "
                 "operation",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32021R1134",
    },
}


def _cite(*source_ids: str) -> list[dict]:
    """Resolve source ids to full citations, each stamped with this module's
    review date. An unknown id is a programming error, not a soft failure."""
    out = []
    for sid in source_ids:
        src = SOURCES[sid]
        out.append({"id": sid, **src, "as_of": AS_OF})
    return out


# ---------------------------------------------------------------------------
# US: the interview-waiver rules as curated data.
#
# The 2025 narrowing, in the Department's own shape: effective 2025-09-02 the
# waiver was eliminated for most categories, and from 2025-10-01 it survives
# only for the rows below. Every row also carries the Department's standing
# conditions (apply in the country of nationality or residence; no prior
# refusal that has not been overcome or waived; no apparent ineligibility) and
# every row is subject to consular-officer discretion to require an interview
# anyway. A category absent from this table is NOT waiver-eligible.

WAIVER_EFFECTIVE_FROM = "2025-10-01"

# Diplomatic and official categories — the one class that kept the waiver
# outright, with no renewal or age condition of its own.
DIPLOMATIC_CATEGORIES = (
    "A-1", "A-2", "C-3", "G-1", "G-2", "G-3", "G-4",
    "NATO-1", "NATO-2", "NATO-3", "NATO-4", "NATO-5", "NATO-6", "TECRO E-1",
)

WAIVER_RULES = (
    {
        "rule": "diplomatic_official",
        "categories": DIPLOMATIC_CATEGORIES,
        "conditions": (),
        "note": "Diplomatic and official categories retained the interview "
                "waiver when it was withdrawn from everything else. C-3 "
                "excludes attendants, servants and personal employees.",
        "sources": ("dos_interview_waiver_2025",),
    },
    {
        "rule": "b_visa_renewal",
        "categories": ("B-1/B-2", "BCC"),
        # Each condition is a fact the caller must supply; a missing one yields
        # "unknown", never a pass.
        "conditions": ("prior_visa_same_category", "prior_visa_full_validity",
                       "renewal_within_12_months", "age_18_or_over_at_prior_issuance",
                       "applying_in_country_of_nationality_or_residence",
                       "no_unwaived_prior_refusal"),
        "note": "B-1/B-2 (and Border Crossing Card) renewals only: a "
                "full-validity prior visa, renewed within 12 months of its "
                "expiration, by an applicant who was 18 or older when that "
                "prior visa was issued.",
        "sources": ("dos_interview_waiver_2025",),
    },
    {
        "rule": "h2a_renewal",
        "categories": ("H-2A",),
        "conditions": ("is_renewal", "applying_in_country_of_nationality_or_residence",
                       "no_unwaived_prior_refusal"),
        "note": "H-2A renewal applicants remained waiver-eligible. The "
                "Department's per-post conditions for this row are narrower "
                "than the B row's and must be confirmed with the post.",
        "sources": ("dos_interview_waiver_2025",),
    },
)

# Standing caveat attached to every "no interview needed" verdict. The waiver is
# never a right.
OFFICER_DISCRETION = ("A consular officer may require an in-person interview in "
                      "any case, at any time, regardless of waiver eligibility.")

# Post-level policy that overrides category eligibility. Mission China ended
# interview-waiver (dropbox) processing: every applicant in the categories Ellis
# serves is interviewed in person at Beijing, Shanghai, Guangzhou, Shenyang and
# Wuhan.
POST_POLICY = {
    "CN": {
        "post_country": "China",
        "all_applicants_interview": True,
        "posts": ("Beijing", "Shanghai", "Guangzhou", "Shenyang", "Wuhan"),
        "scheduling_system": "usvisascheduling.com (CGI Federal Atlas360)",
        "note": "Mission China does not run interview-waiver (dropbox) "
                "processing: applicants appear in person. Diplomatic and "
                "official categories are handled directly by the post and are "
                "outside this curated rule.",
        "sources": ("mission_china_visas", "usvisascheduling"),
    },
}

# Categories Ellis recognizes. A category outside this set is not judged at all
# ("unknown") — the waiver table's closed-list logic is only sound over
# categories we know are real.
KNOWN_CATEGORIES = tuple(sorted(set(DIPLOMATIC_CATEGORIES) | {
    "B-1/B-2", "BCC", "C-1", "C-1/D", "D", "E-1", "E-2", "E-3",
    "F-1", "F-2", "H-1B", "H-1B1", "H-2A", "H-2B", "H-3", "H-4",
    "I", "J-1", "J-2", "K-1", "L-1", "L-2", "M-1", "O-1", "O-3",
    "P-1", "P-3", "Q-1", "R-1", "TN", "TD",
}))

_CATEGORY_CANON = {
    "B1": "B-1/B-2", "B2": "B-1/B-2", "B1B2": "B-1/B-2", "B1B2BCC": "B-1/B-2",
    "BCC": "BCC",
    "H1B": "H-1B", "H1B1": "H-1B1", "H2A": "H-2A", "H2B": "H-2B", "H3": "H-3",
    "H4": "H-4", "F1": "F-1", "F2": "F-2", "J1": "J-1", "J2": "J-2",
    "L1": "L-1", "L1A": "L-1", "L1B": "L-1", "L2": "L-2", "M1": "M-1",
    "O1": "O-1", "O3": "O-3", "P1": "P-1", "P3": "P-3", "Q1": "Q-1",
    "R1": "R-1", "K1": "K-1", "E1": "E-1", "E2": "E-2", "E3": "E-3",
    "C1": "C-1", "C1D": "C-1/D", "D": "D", "I": "I", "TN": "TN", "TD": "TD",
    "A1": "A-1", "A2": "A-2", "C3": "C-3",
    "G1": "G-1", "G2": "G-2", "G3": "G-3", "G4": "G-4",
    "NATO1": "NATO-1", "NATO2": "NATO-2", "NATO3": "NATO-3",
    "NATO4": "NATO-4", "NATO5": "NATO-5", "NATO6": "NATO-6",
    "TECROE1": "TECRO E-1",
}

# EVUS. Not an appointment and not a visa: an online enrolment CBP requires of
# PRC-passport holders travelling on a 10-year B1/B2, refreshed every two years.
EVUS = {
    "url": "https://www.evus.gov/",
    "applies_to_nationality": "China (PRC passport holders)",
    "applies_to_visa": "10-year B-1/B-2 (B-1, B-2 or B-1/B-2) visa",
    "validity": "Two years from enrolment, or until the passport or the visa "
                "expires — whichever comes first.",
    "when": "Before travel; a valid enrolment must exist at each entry.",
    "note": "EVUS is an online enrolment, not an appointment and not a "
            "biometric act. It creates no in-person obligation, and the "
            "traveller is responsible for the answers it contains.",
    "sources": ("cbp_evus",),
}


# ---------------------------------------------------------------------------
# Schengen: the VIS 59-month rule and its exemptions.

SCHENGEN_STATES = (
    "Austria", "Belgium", "Bulgaria", "Croatia", "Czechia", "Denmark",
    "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Iceland",
    "Italy", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta",
    "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia",
    "Slovenia", "Spain", "Sweden", "Switzerland",
)

VIS_RULES = {
    "reuse": {
        "rule": "Art. 13(3)",
        "text": "Fingerprints entered in VIS as part of an application made "
                "less than 59 months before the date of the new application are "
                "copied into the subsequent application.",
        "sources": ("visa_code_art13",),
    },
    "non_delegable": {
        "rule": "Art. 43 / Art. 45(2)",
        "text": "An accredited commercial intermediary may not collect biometric "
                "identifiers, and an external service provider's access to VIS "
                "is bounded by Art. 43. First-time and lapsed applicants must "
                "appear in person.",
        "sources": ("visa_code_art43_45",),
    },
    "child_exemption": {
        "rule": "Art. 13(7)(a)",
        "text": "Children under 12 are exempt from the fingerprint requirement. "
                "Regulation (EU) 2021/1134 lowers that threshold to 6 as of the "
                "recast VIS entering operation, so ages 6-11 are decided by the "
                "consulate's rule on the application date.",
        "sources": ("visa_code_art13", "vis_recast_2021_1134"),
    },
    "other_exemptions": {
        "rule": "Art. 13(7)(b)-(d)",
        "text": "Physical impossibility of fingerprinting (permanent — a "
                "temporary impossibility still requires appearance at the next "
                "application), heads of state and government with their "
                "delegations, and sovereigns on official visits.",
        "sources": ("visa_code_art13",),
    },
}

# The document that settles an unknown prior-visa date. Ellis can read one.
PRIOR_VISA_EVIDENCE = (
    {"evidence": "prior Schengen visa sticker",
     "settles": "the prior visa's issue date, usable as a proxy for the VIS "
                "enrolment date",
     "how": "photograph the sticker page; Ellis's Regula document reader "
            "extracts visa_valid_from / visa_valid_until "
            "(app/providers/document_read.py, read_prior_visa)"},
    {"evidence": "the prior application's VAC receipt or appointment "
                 "confirmation",
     "settles": "the exact date fingerprints were enrolled in VIS — the date "
                "the 59-month clock actually runs from",
     "how": "applicant supplies the receipt; enter it as "
            "fingerprints_enrolled_on"},
)


# ---------------------------------------------------------------------------
# Ellis-authored user-visible strings (en + zh-CN + zh-Hant parity contract).
# Government wording and legal citations stay in English by design.

STRINGS = {
    "verdict.interview_required": {
        "en": "An in-person interview appointment is required.",
        "zh-CN": "需要本人到场的面谈预约。",
        "zh-Hant": "需要本人到場的面談預約。"},
    "verdict.waiver_eligible": {
        "en": "This case qualifies for an interview waiver — no interview "
              "appointment is required.",
        "zh-CN": "本案符合免面谈条件，无需面谈预约。",
        "zh-Hant": "本案符合免面談條件，無需面談預約。"},
    "verdict.unknown": {
        "en": "Ellis cannot decide this from the facts on record.",
        "zh-CN": "根据现有信息，Ellis 无法作出判断。",
        "zh-Hant": "根據現有資訊，Ellis 無法作出判斷。"},
    "verdict.appearance_required": {
        "en": "The applicant must appear in person for fingerprints. This "
              "cannot be delegated.",
        "zh-CN": "申请人须本人到场录入指纹，不可代办。",
        "zh-Hant": "申請人須本人到場錄入指紋，不可代辦。"},
    "verdict.agent_end_to_end": {
        "en": "Fingerprints can be reused, so an accredited agent can carry "
              "the whole filing.",
        "zh-CN": "指纹可复用，全部申请流程可由认可代理完成。",
        "zh-Hant": "指紋可複用，全部申請流程可由認可代理完成。"},
    "verdict.appearance_for_lodging": {
        "en": "No fingerprints are needed, but this member state requires the "
              "applicant to appear to lodge the application.",
        "zh-CN": "无需录入指纹，但该成员国要求申请人本人到场递交申请。",
        "zh-Hant": "無需錄入指紋，但該成員國要求申請人本人到場遞交申請。"},
    "verdict.biometrics_reused_state_unverified": {
        "en": "No biometric appearance is required. Whether this member state "
              "lets an agent lodge without the applicant has not been verified.",
        "zh-CN": "无需为采集生物信息而到场。该成员国是否允许代理在申请人不到场"
                 "的情况下递交，尚未核实。",
        "zh-Hant": "無需為採集生物資訊而到場。該成員國是否允許代理在申請人不到場"
                   "的情況下遞交，尚未核實。"},
    "act.ds160_sign_submit": {
        "en": "Sign and submit your own DS-160 at ceac.state.gov.",
        "zh-CN": "在 ceac.state.gov 亲自签署并提交 DS-160。",
        "zh-Hant": "在 ceac.state.gov 親自簽署並提交 DS-160。"},
    "act.pay_mrv": {
        "en": "Pay the MRV visa fee through the bank's own channel.",
        "zh-CN": "通过银行自有渠道缴纳 MRV 签证费。",
        "zh-Hant": "透過銀行自有渠道繳納 MRV 簽證費。"},
    "act.book_interview_slot": {
        "en": "Choose and confirm the interview slot yourself in the official "
              "system.",
        "zh-CN": "在官方系统中自行选择并确认面谈时段。",
        "zh-Hant": "在官方系統中自行選擇並確認面談時段。"},
    "act.group_request_submit": {
        "en": "The group coordinator submits the group appointment request and "
              "books each member.",
        "zh-CN": "由团队协调人提交团体预约申请并为每位成员预约。",
        "zh-Hant": "由團隊協調人提交團體預約申請並為每位成員預約。"},
    "act.attend_interview": {
        "en": "Attend the interview and give biometrics in person.",
        "zh-CN": "本人到场参加面谈并采集生物信息。",
        "zh-Hant": "本人到場參加面談並採集生物資訊。"},
    "act.submit_documents": {
        "en": "Deliver the passport and documents to the designated drop-off "
              "location.",
        "zh-CN": "将护照和申请材料送交指定的代传递地点。",
        "zh-Hant": "將護照與申請文件送交指定的代傳遞地點。"},
    "act.evus_enrol": {
        "en": "Complete the EVUS enrolment before travelling.",
        "zh-CN": "出行前完成 EVUS 登记更新。",
        "zh-Hant": "出行前完成 EVUS 登記更新。"},
    "act.sign_mandate": {
        "en": "Sign the mandate authorizing the accredited intermediary to act "
              "for you.",
        "zh-CN": "签署委托书，授权认可中介代为办理。",
        "zh-Hant": "簽署委託書，授權認可中介代為辦理。"},
    "act.appear_biometrics": {
        "en": "Appear in person to give fingerprints.",
        "zh-CN": "本人到场录入指纹。",
        "zh-Hant": "本人到場錄入指紋。"},
    "act.appear_to_lodge": {
        "en": "Appear in person to lodge the application.",
        "zh-CN": "本人到场递交申请材料。",
        "zh-Hant": "本人到場遞交申請文件。"},
    "act.agent_lodges_file": {
        "en": "The accredited agent books through the ESP agent channel and "
              "lodges the file.",
        "zh-CN": "由认可代理通过签证中心代理渠道预约并递交材料。",
        "zh-Hant": "由認可代理透過簽證中心代理渠道預約並遞交文件。"},
    "act.collect_passport": {
        "en": "Collect the passport — a courier or the agent may do this for "
              "you.",
        "zh-CN": "领取护照，可由快递或代理代取。",
        "zh-Hant": "領取護照，可由快遞或代理代取。"},
}


def label(key: str, lang: str = "en") -> str:
    """One Ellis-authored string in the caller's language, falling back to en."""
    entry = STRINGS.get(key) or {}
    return entry.get(lang) or entry.get("en") or ""


def _labels(key: str) -> dict:
    return dict(STRINGS[key])


# ---------------------------------------------------------------------------
# Date helpers. Every incoming value is normalized through app.dates first;
# calendar-month arithmetic is the one operation app.dates does not expose, so
# it lives here, private and clamped at month ends.

def _as_date(value, *, kind: str = "expiry", today: date | None = None) -> date | None:
    if isinstance(value, date):
        return value
    iso = dates.normalize_any(value, kind=kind, today=today)
    return dates.parse_printed_date(iso) if iso else None


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _months_elapsed(start: date, on: date) -> int | None:
    """Whole calendar months from ``start`` to ``on``; None if ``on`` precedes
    ``start`` (a future enrolment date is bad data, not a small number)."""
    if on < start:
        return None
    months = (on.year - start.year) * 12 + (on.month - start.month)
    if _add_months(start, months) > on:
        months -= 1
    return months


def _today(today: date | None) -> date:
    return today or date.today()


def freshness(today: date | None = None) -> dict:
    """How old this curated data is. Past ``_FRESH_DAYS`` a human re-verifies
    the cited pages before any of it is shown beside an appointment
    instruction."""
    on = _today(today)
    as_of = _as_date(AS_OF, kind="issue", today=on)
    age = (on - as_of).days if as_of else None
    return {"as_of": AS_OF, "age_days": age,
            "stale": bool(age is not None and age > _FRESH_DAYS),
            "review_after_days": _FRESH_DAYS}


# ---------------------------------------------------------------------------
# Normalization of the few free-text facts we key rules on.

def _norm_category(value) -> str:
    raw = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if not raw:
        return ""
    return _CATEGORY_CANON.get(raw, str(value or "").strip().upper())


# Spellings the shared registry does not carry. This SUPPLEMENTS
# visa_snapshot.registry.iso3 — it never replaces it. (That module documents
# what happens when a call site writes its own country index instead.)
_EXTRA_COUNTRY_ALIASES = {
    "PRC": "CHN", "P.R. CHINA": "CHN", "PR CHINA": "CHN",
    "PEOPLES REPUBLIC OF CHINA": "CHN", "PEOPLE'S REPUBLIC OF CHINA": "CHN",
    "MAINLAND CHINA": "CHN", "中国": "CHN", "中國": "CHN",
    "CZECH REPUBLIC": "CZE",
    "U.S.": "USA", "U.S.A.": "USA", "AMERICA": "USA", "美国": "USA", "美國": "USA",
}


def _norm_country(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def _country_code(value) -> str:
    """ISO alpha-3 for any country identifier, or '' when it is not a country
    Ellis knows. Empty is meaningful: it means "cannot compare", which the
    callers turn into "unknown" rather than into "different"."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    return registry.iso3(raw) or _EXTRA_COUNTRY_ALIASES.get(_norm_country(raw), "")


def _is_china(value) -> bool:
    return _country_code(value) == "CHN"


def _is_us(value) -> bool:
    return _country_code(value) == "USA"


@lru_cache(maxsize=1)
def _schengen_by_code() -> dict:
    return {registry.iso3(s) or s.upper(): s for s in SCHENGEN_STATES}


def _schengen_state(value) -> str:
    """The Schengen member state a destination names, or '' if it is not one.
    Ireland and Cyprus are EU but not Schengen, and answering for them would be
    the wrong regime entirely."""
    return _schengen_by_code().get(_country_code(value), "")


# Answers arrive from JSON forms as often as from Python, so "no" and "false"
# have to mean False. Plain truthiness would read BOTH of them as True — and on
# `prior_visa_full_validity` that mistake hands out a waiver the applicant does
# not have. A word Ellis cannot read is not an answer: it returns None.
_TRUE_WORDS = {"true", "yes", "y", "t", "1", "是", "有", "已"}
_FALSE_WORDS = {"false", "no", "n", "f", "0", "否", "没有", "沒有", "无", "無"}


def _tri(value):
    """A caller-supplied boolean fact as True/False, or None when it was not
    supplied or cannot be read. An empty string is 'not supplied', not False."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    word = str(value).strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return None


# ---------------------------------------------------------------------------
# US — is an in-person interview appointment required at all?

def us_appointment_needed(case_facts: dict, *, today: date | None = None) -> dict:
    """Does this applicant need an in-person US visa interview appointment?

    ``needed`` is ``True``, ``False`` or ``"unknown"``. ``False`` means the case
    matches a curated interview-waiver row — the passport and documents still go
    to the post, but no interview slot is booked. ``"unknown"`` means the
    curated rules do not cover these facts, or a fact they need is missing; the
    payload then names what would settle it. There is no fourth outcome and no
    default.

    Facts read (all optional; absence is handled, never assumed):
      visa_category, post_country (or interview_country), nationality,
      residence_country, prior_visa_category, prior_visa_expires_on,
      prior_visa_issued_on, date_of_birth or age_at_prior_issuance,
      prior_visa_full_validity, applying_in_country_of_nationality_or_residence,
      prior_refusal, is_renewal.
    """
    facts = dict(case_facts or {})
    on = _today(today)
    category = _norm_category(facts.get("visa_category") or facts.get("visa_type"))
    post = facts.get("post_country") or facts.get("interview_country") or ""

    base = {"as_of": AS_OF, "visa_category": category or None,
            "post_country": post or None, "waiver_rule": None,
            "missing_facts": [], "caveats": [],
            "waiver_effective_from": WAIVER_EFFECTIVE_FROM}

    if not category:
        return {**base, "needed": UNKNOWN,
                "reason": "No visa category on record, and interview-waiver "
                          "eligibility is decided category by category.",
                "missing_facts": ["visa_category"],
                "citations": _cite("dos_interview_waiver_2025")}

    if category not in KNOWN_CATEGORIES:
        return {**base, "needed": UNKNOWN,
                "reason": f"Visa category {category!r} is not on Ellis's curated "
                          "list, so the closed-list waiver rules cannot be "
                          "applied to it. Confirm with the Department of State.",
                "missing_facts": ["visa_category (unrecognized)"],
                "citations": _cite("dos_interview_waiver_2025")}

    rule = _waiver_rule_for(category)

    # A category outside the waiver table needs an interview everywhere. H-1B is
    # this row, and it does not depend on knowing the post.
    if rule is None:
        return {**base, "needed": True,
                "reason": f"The interview waiver was withdrawn from {category} "
                          f"as of {WAIVER_EFFECTIVE_FROM}; it survives only for "
                          "diplomatic/official categories, B-1/B-2 (and BCC) "
                          "renewals, and H-2A renewals.",
                "citations": _cite("dos_interview_waiver_2025")}

    # Post-level policy outranks category eligibility. Mission China interviews
    # everyone in the categories Ellis serves.
    if _is_china(post):
        policy = POST_POLICY["CN"]
        if rule["rule"] == "diplomatic_official":
            return {**base, "needed": UNKNOWN, "waiver_rule": rule["rule"],
                    "reason": "Mission China interviews all applicants in the "
                              "categories Ellis serves, while diplomatic and "
                              "official cases are arranged directly with the "
                              "post. Ellis will not guess between the two — "
                              "confirm with the post.",
                    "missing_facts": ["post confirmation for a diplomatic/"
                                      "official category"],
                    "citations": _cite("mission_china_visas",
                                       "dos_interview_waiver_2025")}
        return {**base, "needed": True, "waiver_rule": rule["rule"],
                "post_policy": policy["note"],
                "reason": "Mission China does not run interview-waiver "
                          "(dropbox) processing: every applicant in this "
                          "category is interviewed in person.",
                "citations": _cite("mission_china_visas", "usvisascheduling")}

    if not post:
        return {**base, "needed": UNKNOWN, "waiver_rule": rule["rule"],
                "reason": "This category can be waiver-eligible, but posts "
                          "apply their own policy (Mission China, for one, "
                          "interviews everyone). Ellis needs the post before it "
                          "can answer.",
                "missing_facts": ["post_country"],
                "citations": _cite("dos_interview_waiver_2025",
                                   "mission_china_visas")}

    unmet, missing = _check_waiver_conditions(rule, facts, on)
    if missing:
        return {**base, "needed": UNKNOWN, "waiver_rule": rule["rule"],
                "reason": "Ellis cannot confirm every condition of the "
                          f"{rule['rule']} waiver row from the facts on record.",
                "missing_facts": missing,
                "conditions": list(rule["conditions"]),
                "citations": _cite(*rule["sources"])}
    if unmet:
        return {**base, "needed": True, "waiver_rule": rule["rule"],
                "reason": "The case fails a condition of the only waiver row it "
                          f"could use ({rule['rule']}): {', '.join(unmet)}.",
                "unmet_conditions": unmet,
                "citations": _cite(*rule["sources"])}

    return {**base, "needed": False, "waiver_rule": rule["rule"],
            "reason": f"The case matches the {rule['rule']} interview-waiver "
                      f"row in force since {WAIVER_EFFECTIVE_FROM}.",
            "rule_note": rule["note"],
            "caveats": [OFFICER_DISCRETION],
            "citations": _cite(*rule["sources"])}


def _waiver_rule_for(category: str) -> dict | None:
    for rule in WAIVER_RULES:
        if category in rule["categories"]:
            return rule
    return None


def _check_waiver_conditions(rule: dict, facts: dict, on: date) -> tuple[list, list]:
    """Returns (unmet, missing). A condition Ellis cannot evaluate goes to
    ``missing`` — it never quietly passes."""
    unmet, missing = [], []

    def record(name: str, value):
        if value is None:
            missing.append(name)
        elif value is False:
            unmet.append(name)

    for condition in rule["conditions"]:
        if condition == "prior_visa_same_category":
            prior = _norm_category(facts.get("prior_visa_category"))
            record(condition, None if not prior else prior in rule["categories"])
        elif condition == "prior_visa_full_validity":
            record(condition, _tri(facts.get("prior_visa_full_validity")))
        elif condition == "renewal_within_12_months":
            record(condition, _renewal_within_12_months(facts, on))
        elif condition == "age_18_or_over_at_prior_issuance":
            record(condition, _age_18_at_prior_issuance(facts))
        elif condition == "applying_in_country_of_nationality_or_residence":
            record(condition, _applying_at_home_post(facts))
        elif condition == "no_unwaived_prior_refusal":
            refused = _tri(facts.get("prior_refusal"))
            record(condition, None if refused is None else not refused)
        elif condition == "is_renewal":
            renewal = _tri(facts.get("is_renewal"))
            if renewal is None and _norm_category(facts.get("prior_visa_category")):
                renewal = _norm_category(facts["prior_visa_category"]) in rule["categories"]
            record(condition, renewal)
    return unmet, missing


def _renewal_within_12_months(facts: dict, on: date) -> bool | None:
    """The prior visa is still valid, or expired less than 12 months ago."""
    expiry = _as_date(facts.get("prior_visa_expires_on")
                      or facts.get("prior_visa_expired_on"), kind="expiry", today=on)
    if not expiry:
        return None
    return on <= _add_months(expiry, 12)


def _age_18_at_prior_issuance(facts: dict) -> bool | None:
    stated = facts.get("age_at_prior_issuance")
    if stated not in (None, ""):
        try:
            return int(stated) >= 18
        except (TypeError, ValueError):
            return None
    birth = _as_date(facts.get("date_of_birth"), kind="birth")
    issued = _as_date(facts.get("prior_visa_issued_on"), kind="issue")
    if not birth or not issued:
        return None
    age = issued.year - birth.year - ((issued.month, issued.day) < (birth.month, birth.day))
    return age >= 18


def _applying_at_home_post(facts: dict) -> bool | None:
    """Is the applicant applying in their own country of nationality or
    residence? Compared as ISO codes, so 'CA' and 'Canada' are the same country
    — and a value neither the registry nor the alias table recognizes returns
    None (unknown), never False. A spelling Ellis cannot resolve must not read
    as 'applying somewhere else' and cost the applicant the waiver."""
    stated = _tri(facts.get("applying_in_country_of_nationality_or_residence"))
    if stated is not None:
        return stated
    post_raw = facts.get("post_country") or facts.get("interview_country")
    post = _country_code(post_raw)
    if not post:
        return None
    homes, unresolved = [], False
    for key in ("nationality", "residence_country", "country_of_residence"):
        value = facts.get(key)
        if not value:
            continue
        code = _country_code(value)
        if code:
            homes.append(code)
        else:
            unresolved = True
    if post in homes:
        return True
    if not homes or unresolved:
        return None
    return False


# ---------------------------------------------------------------------------
# US — EVUS.

def evus_status(case_facts: dict) -> dict:
    """EVUS enrolment status for the traveller. ``required`` is True, False or
    "unknown". EVUS is an online enrolment, never an appointment, so a True here
    adds a task, not a trip."""
    facts = dict(case_facts or {})
    out = {"url": EVUS["url"], "as_of": AS_OF,
           "citations": _cite(*EVUS["sources"]),
           "validity": EVUS["validity"], "missing_facts": []}
    nationality = facts.get("nationality") or facts.get("passport_country")
    if not nationality:
        return {**out, "required": UNKNOWN,
                "note": "EVUS applies to PRC passport holders travelling on a "
                        "10-year B-1/B-2 visa. Ellis has no nationality on "
                        "record for this traveller."}
    if not _is_china(nationality):
        return {**out, "required": False,
                "note": "EVUS applies only to PRC passport holders; this "
                        "traveller is not one."}

    category = _norm_category(facts.get("us_visa_category")
                              or facts.get("visa_category")
                              or facts.get("visa_type"))
    if category and category != "B-1/B-2":
        return {**out, "required": False,
                "note": f"EVUS covers 10-year B-1/B-2 visas; a {category} visa "
                        "is outside it. If this traveller ALSO holds a 10-year "
                        "B-1/B-2 and will enter on it, re-run this check "
                        "against that visa."}

    years = facts.get("us_visa_validity_years") or facts.get("visa_validity_years")
    ten_year = _tri(facts.get("holds_ten_year_b1b2"))
    if ten_year is None and years not in (None, ""):
        try:
            ten_year = int(years) >= 10
        except (TypeError, ValueError):
            ten_year = None
    if ten_year is None:
        return {**out, "required": UNKNOWN,
                "note": "PRC passport holder, but Ellis does not know whether "
                        "the B-1/B-2 visa is a 10-year one. Check the visa "
                        "sticker's validity dates, then re-run.",
                "missing_facts": ["us_visa_validity_years or holds_ten_year_b1b2"]}
    if not ten_year:
        return {**out, "required": False,
                "note": "The B-1/B-2 visa is not a 10-year visa, so EVUS does "
                        "not apply."}
    return {**out, "required": True,
            "note": EVUS["note"] + " " + EVUS["when"],
            "when": EVUS["when"]}


# ---------------------------------------------------------------------------
# Schengen — the VIS 59-month fingerprint reuse rule.

def schengen_biometrics_required(prior_visa_facts: dict, *,
                                 today: date | None = None) -> dict:
    """Must this applicant appear in person to give fingerprints?

    ``required`` is ``True``, ``False`` or ``"unknown"``; ``within_59_months`` is
    ``True``, ``False`` or ``None``. ``required=False`` is the one condition that
    lets an Art. 45 accredited intermediary carry the whole filing — so it is
    only ever returned from a DATED fact, never from an absence of one.

    Facts read: fingerprints_enrolled_on (authoritative — the date of the prior
    application), prior_visa_issued_on / prior_visa_valid_from (proxy),
    prior_schengen_visa, first_time_applicant, fingerprints_in_vis,
    date_of_birth or age, application_date, fingerprint_exemption.
    """
    facts = dict(prior_visa_facts or {})
    on = _as_date(facts.get("application_date"), kind="expiry", today=today) or _today(today)

    out = {"as_of": AS_OF, "within_59_months": None, "reuse_window_months": VIS_REUSE_MONTHS,
           "evidence_needed": [], "caveats": [], "basis": None,
           "non_delegable": None, "months_since_enrolment": None,
           "application_date": on.isoformat()}

    # Age exemptions first: a child below the threshold has no fingerprints to
    # reuse and no fingerprints to give.
    age = _applicant_age(facts, on)
    if age is not None and age < 6:
        return {**out, "required": False, "basis": "Art. 13(7)(a)",
                "reason": f"The applicant is {age}; children below the "
                          "fingerprinting age are exempt under both the current "
                          "Art. 13(7)(a) threshold (12) and the recast "
                          "threshold (6).",
                "caveats": ["The member state may still require the child's "
                            "presence for reasons other than biometrics."],
                "citations": _cite("visa_code_art13", "vis_recast_2021_1134")}
    if age is not None and age < 12:
        return {**out, "required": UNKNOWN, "basis": "Art. 13(7)(a)",
                "reason": f"The applicant is {age}. Art. 13(7)(a) exempts "
                          "children under 12, but Regulation (EU) 2021/1134 "
                          "lowers the threshold to 6 once the recast VIS is in "
                          "operation. Ellis will not guess which rule the "
                          "consulate applies on the application date.",
                "evidence_needed": [{"evidence": "the consulate's or ESP's "
                                                 "published rule on the "
                                                 "fingerprinting age",
                                     "settles": "whether a 6-11 year old is "
                                                "fingerprinted",
                                     "how": "check the member state's visa page "
                                            "for the application date"}],
                "citations": _cite("visa_code_art13", "vis_recast_2021_1134")}

    exemption = str(facts.get("fingerprint_exemption") or "").strip()
    if exemption:
        return {**out, "required": UNKNOWN, "basis": "Art. 13(7)(b)-(d)",
                "reason": f"A claimed fingerprint exemption ({exemption!r}) is "
                          "decided by the consulate, not by Ellis. A TEMPORARY "
                          "physical impossibility does not exempt the applicant "
                          "from appearing.",
                "citations": _cite("visa_code_art13")}

    first_time = _tri(facts.get("first_time_applicant"))
    has_prior = _tri(facts.get("prior_schengen_visa"))
    in_vis = _tri(facts.get("fingerprints_in_vis"))

    if first_time is True or has_prior is False or in_vis is False:
        return {**out, "required": True, "within_59_months": False,
                "basis": VIS_RULES["non_delegable"]["rule"],
                "non_delegable": True,
                "reason": "No fingerprints exist in VIS to reuse, so the "
                          "applicant must appear in person. An accredited "
                          "intermediary may not collect biometrics on their "
                          "behalf.",
                "citations": _cite("visa_code_art13", "visa_code_art43_45")}

    enrolled = _as_date(facts.get("fingerprints_enrolled_on"), kind="issue", today=on)
    proxy = None
    if not enrolled:
        proxy = _as_date(facts.get("prior_visa_issued_on")
                         or facts.get("prior_visa_valid_from")
                         or facts.get("visa_valid_from"), kind="issue", today=on)

    anchor = enrolled or proxy
    if not anchor:
        return {**out, "required": UNKNOWN,
                "reason": "The date of the prior fingerprint enrolment is not on "
                          "record, and the 59-month reuse window is measured "
                          "from it. Without that date Ellis cannot say whether "
                          "the applicant must appear.",
                "evidence_needed": [dict(e) for e in PRIOR_VISA_EVIDENCE],
                "citations": _cite("visa_code_art13")}

    if anchor > on:
        return {**out, "required": UNKNOWN,
                "reason": f"The prior-enrolment date on record ({anchor.isoformat()}) "
                          f"is after the application date ({on.isoformat()}), so "
                          "it cannot be the date fingerprints were taken.",
                "evidence_needed": [dict(e) for e in PRIOR_VISA_EVIDENCE],
                "citations": _cite("visa_code_art13")}

    boundary = _add_months(anchor, VIS_REUSE_MONTHS)
    within = on < boundary
    elapsed = _months_elapsed(anchor, on)
    out = {**out, "within_59_months": within, "months_since_enrolment": elapsed,
           "anchor_date": anchor.isoformat(),
           "anchor_kind": "vis_enrolment" if enrolled else "prior_visa_issue_date_proxy",
           "boundary_date": boundary.isoformat()}

    if not within:
        return {**out, "required": True, "non_delegable": True,
                "basis": VIS_RULES["reuse"]["rule"],
                "reason": f"The prior fingerprints were enrolled {elapsed} months "
                          f"ago, past the {VIS_REUSE_MONTHS}-month reuse window, "
                          "so new fingerprints must be taken in person.",
                "citations": _cite("visa_code_art13", "visa_code_art43_45")}

    # Inside the window on a PROXY date: the true enrolment date is always
    # EARLIER than the sticker's issue date, so a proxy that only just clears
    # the boundary proves nothing.
    if proxy is not None and (boundary - on).days <= _PROXY_MARGIN_DAYS:
        return {**out, "required": UNKNOWN,
                "basis": VIS_RULES["reuse"]["rule"],
                "reason": "The only date on record is the prior visa's issue "
                          f"date, which clears the {VIS_REUSE_MONTHS}-month "
                          f"boundary by {(boundary - on).days} days. The "
                          "fingerprints were enrolled when the prior "
                          "APPLICATION was made — earlier than the sticker — so "
                          "this margin cannot prove reuse.",
                "evidence_needed": [dict(PRIOR_VISA_EVIDENCE[1])],
                "citations": _cite("visa_code_art13")}

    caveats = ["A consulate may still take fresh fingerprints where there is "
               "reasonable doubt about the applicant's identity (Art. 13(3)).",
               "Reuse removes the BIOMETRIC reason to appear; whether the member "
               "state permits submission without appearance is a separate, "
               "per-state fact."]
    if proxy is not None:
        caveats.append("Measured from the prior visa's issue date as a proxy for "
                       "the VIS enrolment date; the VAC receipt would make it "
                       "exact.")
    return {**out, "required": False, "non_delegable": False,
            "basis": VIS_RULES["reuse"]["rule"],
            "reason": f"The prior fingerprints were enrolled {elapsed} months ago "
                      f"— inside the {VIS_REUSE_MONTHS}-month window — so VIS "
                      "copies them into this application and no biometric "
                      "appearance is required.",
            "caveats": caveats,
            "citations": _cite("visa_code_art13")}


def _applicant_age(facts: dict, on: date) -> int | None:
    stated = facts.get("age")
    if stated not in (None, ""):
        try:
            return int(stated)
        except (TypeError, ValueError):
            return None
    birth_iso = dates.normalize_any(facts.get("date_of_birth"), kind="birth", today=on)
    return dates.derive_age(birth_iso, today=on) if birth_iso else None


# ---------------------------------------------------------------------------
# The combined verdict the cockpit renders.

# The lines the cockpit states out loud, on every route, in every payload.
NEVER_AUTOMATED = (
    "automated appointment slot search or booking (it gets the APPLICANT's "
    "appointment and visa cancelled)",
    "scraping a bot-protected appointment calendar",
    "solving a CAPTCHA",
    "entering a login, card or bank credential",
    "signing or submitting on the applicant's behalf",
    "giving biometrics on the applicant's behalf",
)

# Why a waived case still keeps an "attend the interview" line: the waiver is a
# convenience, not a right, and the applicant should stay available.
OFFICER_DISCLAIMER_ACT_WHY = (
    "Conditional: " + OFFICER_DISCRETION + " Keep the applicant available.")


def _act(key: str, who: str, *, non_delegable: bool, why: str,
         citations: list | None = None) -> dict:
    return {"act": key.split(".", 1)[-1], "label": _labels(key), "who": who,
            "non_delegable": non_delegable, "why": why,
            "citations": citations or []}


def triage(case, *, locale: str = "en", today: date | None = None) -> dict:
    """One verdict for one case: what the rules decide, and which human acts
    survive that decision.

    ``case`` is either a flat dict of case facts (``destination_country``,
    ``visa_category``/``visa_type``, ``nationality``, ``post_country``, ...) or a
    ``VisaApplication`` row, whose ``answers`` JSON supplies those same facts —
    the cockpit router hands over the row it already loaded. Either form may
    carry two nested blocks: ``prior_visa`` (Schengen prior-visa facts) and
    ``route_facts`` (human-verified route data, e.g. a RouteRule's
    ``personal_appearance_required`` / ``third_party_submission_allowed``).
    """
    facts = _case_facts(case)
    on = _today(today)
    destination = facts.get("destination_country") or facts.get("destination") or ""
    prior = dict(facts.get("prior_visa") or {})
    route_facts = dict(facts.get("route_facts") or {})

    out = {"as_of": AS_OF, "freshness": freshness(on),
           "destination": destination or None, "locale": _norm_locale(locale),
           "human_acts": [], "open_questions": [], "citations": [],
           "never_automated": NEVER_AUTOMATED}

    if _is_us(destination):
        out = {**out, **_triage_us(facts, prior, route_facts, on)}
    else:
        state = _schengen_state(destination)
        if state:
            out = {**out, **_triage_schengen(facts, prior, route_facts, on, state)}
        else:
            out = {**out, "route": "unsupported",
                   "verdict": {"in_person_required": UNKNOWN,
                               "agent_deliverable_end_to_end": UNKNOWN,
                               "summary": _labels("verdict.unknown")},
                   "reason": "Appointment triage currently covers US consular "
                             "posts and Schengen member states. This destination "
                             "is outside both, so Ellis makes no eligibility "
                             "claim about it.",
                   "open_questions": [
                       {"question": "Which appointment regime governs "
                                    f"{destination or 'this destination'}?",
                        "resolve_with": "the destination's official visa page, "
                                        "reviewed by a human before it becomes a "
                                        "rule here"}]}
    return _localized(out, locale)


def _case_facts(case) -> dict:
    """Case facts as a flat dict, from either a dict or a ``VisaApplication``
    row. A row's ``answers`` JSON holds the applicant's own facts; the row's
    columns win for destination and category because they are the case's
    identity, not an answer."""
    if isinstance(case, dict):
        return dict(case)
    if case is None:
        return {}
    answers = getattr(case, "answers", None)
    facts = dict(answers) if isinstance(answers, dict) else {}
    for column, key in (("destination_country", "destination_country"),
                        ("visa_type", "visa_type")):
        value = getattr(case, column, None)
        if value:
            facts[key] = value
    return facts


_LOCALES = {"zh-cn": "zh-CN", "zh": "zh-CN", "zh-hans": "zh-CN",
            "zh_cn": "zh-CN", "zh-hant": "zh-Hant", "zh-tw": "zh-Hant",
            "zh-hk": "zh-Hant", "zh_tw": "zh-Hant"}


def _norm_locale(locale) -> str:
    return _LOCALES.get(str(locale or "en").strip().lower(), "en")


def _localized(payload: dict, locale) -> dict:
    """Add the caller's language beside the trilingual strings — never instead
    of them, so a client can still render any of the three."""
    lang = _norm_locale(locale)
    verdict = dict(payload.get("verdict") or {})
    if verdict.get("summary"):
        verdict["summary_text"] = verdict["summary"].get(lang) or verdict["summary"]["en"]
        payload = {**payload, "verdict": verdict}
    acts = []
    for act in payload.get("human_acts") or []:
        acts.append({**act, "label_text": act["label"].get(lang) or act["label"]["en"]})
    if acts:
        payload = {**payload, "human_acts": acts}
    return payload


# A case's ``visa_type`` is a ROUTE word, not a visa class. On the US route
# these words have exactly one class behind them, so triage may supply it — and
# labels it in the payload, because the rule engine itself never infers a
# category (call us_appointment_needed directly and "tourist" stays unknown).
_US_B_ROUTE_WORDS = {"tourist", "tourism", "visitor", "visit", "business"}


def _us_category(facts: dict) -> tuple[dict, str]:
    if facts.get("visa_category"):
        return facts, "case"
    word = str(facts.get("visa_type") or "").strip().lower()
    if word in _US_B_ROUTE_WORDS:
        return {**facts, "visa_category": "B-1/B-2"}, "us_route_default"
    return facts, "case"


def _triage_us(facts: dict, prior: dict, route_facts: dict, on: date) -> dict:
    facts, category_source = _us_category(facts)
    interview = us_appointment_needed(facts, today=on)
    evus = evus_status(facts)
    needed = interview["needed"]

    acts = [_act("act.ds160_sign_submit", "applicant", non_delegable=True,
                 why="The DS-160 is the applicant's own sworn statement; "
                     "\"the applicant must complete his or her own Form "
                     "DS-160\"."),
            _act("act.pay_mrv", "applicant", non_delegable=True,
                 why="Ellis never enters card or bank credentials. The fee is "
                     "paid through the bank's own channel.")]

    booking_actor = "group_coordinator" if _is_group_request(facts) else "applicant"
    if needed is not False:
        acts.append(_act("act.book_interview_slot"
                         if booking_actor == "applicant" else "act.group_request_submit",
                         booking_actor, non_delegable=True,
                         why="Slot selection and confirmation are performed by an "
                             "authorized human in the official system. Automated "
                             "slot search is prohibited and cancels the "
                             "applicant's appointment and visa.",
                         citations=_cite("usvisascheduling")))
        acts.append(_act("act.attend_interview", "applicant", non_delegable=True,
                         why="Interview and biometrics are personal acts."))
    else:
        acts.append(_act("act.submit_documents", "applicant", non_delegable=False,
                         why="With the interview waived, the passport and "
                             "documents go to the designated drop-off or "
                             "courier; a courier may carry them."))
        acts.append(_act("act.attend_interview", "applicant", non_delegable=True,
                         why=OFFICER_DISCLAIMER_ACT_WHY))
    acts.append(_act("act.collect_passport", "applicant", non_delegable=False,
                     why="Passport return may be handled by a courier or agent."))
    if evus.get("required") is True:
        acts.append(_act("act.evus_enrol", "applicant", non_delegable=True,
                         why="The traveller attests to the EVUS answers. It is "
                             "an online enrolment, not an appointment.",
                         citations=_cite("cbp_evus")))

    questions = [{"question": f"US interview waiver: {m} is not on record",
                  "resolve_with": "ask the applicant, or read it from the prior "
                                  "visa sticker"}
                 for m in interview.get("missing_facts", [])]
    if evus.get("required") == UNKNOWN:
        questions.append({"question": "Is the traveller's B-1/B-2 visa a 10-year "
                                      "visa (EVUS)?",
                          "resolve_with": "the visa sticker's validity dates"})

    summary = (_labels("verdict.unknown") if needed == UNKNOWN
               else _labels("verdict.interview_required") if needed
               else _labels("verdict.waiver_eligible"))
    return {"route": "us",
            "us": interview, "evus": evus,
            "visa_category_source": category_source,
            "verdict": {"in_person_required": needed,
                        "interview_required": needed,
                        # A US case always keeps personal acts (DS-160 signature,
                        # fee, and the booking action itself), so no US route is
                        # ever agent-deliverable end to end.
                        "agent_deliverable_end_to_end": False,
                        "summary": summary},
            "human_acts": acts, "open_questions": questions,
            "citations": interview.get("citations", []) + evus.get("citations", [])}


def _is_group_request(facts: dict) -> bool:
    """Whether the booking action belongs to a group coordinator. Group
    ELIGIBILITY (10+ travelling together, tour groups yes, families no) is
    decided by the group-roster module; triage only needs to know who acts."""
    group = dict(facts.get("group") or {})
    if _tri(group.get("group_appointment")) or _tri(facts.get("group_appointment")):
        return True
    try:
        members = int(group.get("member_count") or 0)
    except (TypeError, ValueError):
        members = 0
    kind = str(group.get("kind") or "").lower()
    return members >= 10 and kind not in ("family", "relatives")


def _triage_schengen(facts: dict, prior: dict, route_facts: dict, on: date,
                     state: str) -> dict:
    prior_facts = {**{k: v for k, v in facts.items() if not isinstance(v, dict)},
                   **prior}
    prior_facts.setdefault("application_date", facts.get("application_date"))
    bio = schengen_biometrics_required(prior_facts, today=on)
    required = bio["required"]

    # Whether the member state lets an accredited intermediary lodge without the
    # applicant is a per-state fact that a human verifies (RouteRule). Absent
    # that, it is unknown — never assumed permitted.
    without_appearance = _tri(route_facts.get("submission_without_appearance_permitted"))
    if without_appearance is None:
        appearance = _tri(route_facts.get("personal_appearance_required"))
        if appearance is not None:
            without_appearance = not appearance

    acts = [_act("act.sign_mandate", "applicant", non_delegable=True,
                 why="Art. 45 requires the applicant's own authorization for an "
                     "accredited intermediary to act.",
                 citations=_cite("visa_code_art43_45"))]
    if required is not False:
        acts.append(_act("act.appear_biometrics", "applicant", non_delegable=True,
                         why="Fingerprints are collected by the consulate or "
                             "VAC; Art. 45(2) bars an intermediary from taking "
                             "them.",
                         citations=_cite("visa_code_art43_45")))
    elif without_appearance is False:
        # No biometric reason to appear, but the member state still wants the
        # applicant at the counter. That act must be on the list or the cockpit
        # under-reports what the trip actually costs.
        acts.append(_act("act.appear_to_lodge", "applicant", non_delegable=True,
                         why="This member state requires the applicant to lodge "
                             "in person even where fingerprints are reused."))
    acts.append(_act("act.agent_lodges_file", "accredited_agent",
                     non_delegable=False,
                     why="Booking and lodging sit inside the accredited-agent "
                         "envelope where the member state permits it."))
    acts.append(_act("act.collect_passport", "accredited_agent",
                     non_delegable=False,
                     why="Passport return may be handled by the agent's courier."))

    # Two separate gates, kept separate. Biometrics are EU-law; whether the
    # applicant must show up to LODGE is the member state's own rule. Reusable
    # fingerprints remove the first gate and say nothing about the second.
    if required is True:
        in_person = True
    elif required == UNKNOWN:
        in_person = UNKNOWN
    elif without_appearance is True:
        in_person = False
    elif without_appearance is False:
        in_person = True
    else:
        in_person = UNKNOWN

    if required is False and without_appearance is True:
        end_to_end = True
    elif required is True or without_appearance is False:
        end_to_end = False
    else:
        end_to_end = UNKNOWN

    questions = []
    for item in bio.get("evidence_needed", []):
        questions.append({"question": f"Schengen biometrics: {item['settles']}",
                          "resolve_with": item["how"]})
    if without_appearance is None:
        questions.append({"question": f"Does {state} permit an accredited "
                                      "intermediary to lodge without the "
                                      "applicant appearing?",
                          "resolve_with": "the member state's verified route "
                                          "rule (personal_appearance_required / "
                                          "third_party_submission_allowed), "
                                          "human-reviewed before use"})

    if required == UNKNOWN:
        summary = _labels("verdict.unknown")
    elif required is True:
        summary = _labels("verdict.appearance_required")
    elif end_to_end is True:
        summary = _labels("verdict.agent_end_to_end")
    elif without_appearance is False:
        summary = _labels("verdict.appearance_for_lodging")
    else:
        summary = _labels("verdict.biometrics_reused_state_unverified")
    return {"route": "schengen", "member_state": state,
            "schengen": bio,
            "submission_without_appearance_permitted": (
                UNKNOWN if without_appearance is None else without_appearance),
            "verdict": {"in_person_required": in_person,
                        "biometrics_required": required,
                        "agent_deliverable_end_to_end": end_to_end,
                        "summary": summary},
            "human_acts": acts, "open_questions": questions,
            "citations": bio.get("citations", [])}


# ---------------------------------------------------------------------------
# The curated tables, for an admin surface and for tests that assert every rule
# carries its source.

def curated_data(today: date | None = None) -> dict:
    return {
        "as_of": AS_OF,
        "freshness": freshness(today),
        "sources": SOURCES,
        "us": {"waiver_effective_from": WAIVER_EFFECTIVE_FROM,
               "rules": [dict(r) for r in WAIVER_RULES],
               "post_policy": POST_POLICY,
               "officer_discretion": OFFICER_DISCRETION,
               "known_categories": KNOWN_CATEGORIES},
        "evus": dict(EVUS),
        "schengen": {"reuse_window_months": VIS_REUSE_MONTHS,
                     "states": SCHENGEN_STATES,
                     "rules": {k: dict(v) for k, v in VIS_RULES.items()},
                     "prior_visa_evidence": [dict(e) for e in PRIOR_VISA_EVIDENCE]},
        "never_automated": NEVER_AUTOMATED,
    }
