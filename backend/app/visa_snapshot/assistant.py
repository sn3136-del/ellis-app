"""Ellis, the conversational layer over the verified database.

The rules of this layer, in force by construction rather than hope:

1. FACTS COME FROM THE DATABASE. The model composes prose from a facts
   payload built out of the served answer (verified overrides applied,
   policy notes attached). It is told to use nothing else, and every reply
   ships alongside the full record card, so a reader can always check the
   prose against the fields.
2. THE NAME IS ELLIS. Identity questions are intercepted before any model
   call and answered deterministically. The composer's instructions forbid
   naming models, providers or systems.
3. IMMIGRATION ONLY. A question with no country and no immigration language
   is refused deterministically with one sentence. The composer carries the
   same instruction as a second net for questions that slip through with a
   country in them.
4. THE MODEL IS OPTIONAL. Any failure, timeout or nonsense from the
   composer falls back to the deterministic summary the page already knows
   how to build. A broken model can slow this feature down but cannot break
   the answer.
"""
from __future__ import annotations

import json
import re

REFUSAL_EN = "Sorry, I can only help with immigration matters."
REFUSAL_ZH = "抱歉，我只能协助出入境相关事务。"

_IDENTITY = (
    "what ai", "which ai", "what's your name", "what is your name",
    "whats your name", "your name?", "who are you", "what are you",
    "what model", "which model", "who made you", "who built you",
    "are you chatgpt", "are you gpt", "are you claude", "are you kimi",
    "你叫什么", "你叫什麼", "你是谁", "你是誰", "你是什么模型", "你是什麼模型",
    "什么模型", "什麼模型", "哪个模型", "哪個模型", "谁开发的", "誰開發的",
    "你的名字", "你是什么ai", "你是什麼ai", "你是ai吗", "你是ai嗎",
)

_IDENTITY_REPLY_EN = ("I am Ellis. I help with visas and entry rules for any "
                      "trip. Ask me about a route, a fee, a stay or a policy.")
_IDENTITY_REPLY_ZH = ("我是 Ellis。我负责签证与出入境规则。"
                      "您可以问我任意线路的签证、费用、停留或政策。")

# Words that mark a question as being about immigration and travel entry.
_TOPIC_WORDS = (
    "visa", "immigration", "passport", "entry", "enter", "transit",
    "travel", "trip", "border", "customs", "embassy", "consulate",
    "stay", "permit", "eta", "evisa", "e-visa", "arrival", "departure",
    "tourism", "tourist", "business", "study", "work", "layover",
    "stopover", "nationality", "citizen", "residence", "fly", "flight",
    "签证", "免签", "免簽", "入境", "出境", "过境", "過境", "护照", "護照",
    "移民", "出行", "旅行", "旅游", "旅遊", "出入境", "落地签", "落地簽",
    "使馆", "使館", "领事", "領事", "通行证", "通行證", "停留", "机票",
    "機票", "航班", "转机", "轉機", "商务", "商務", "留学", "留學", "探亲",
    "探親", "工作", "海关", "海關",
)


def _is_chinese(q: str) -> bool:
    return any("一" <= c <= "鿿" for c in q or "")


def identity_reply(question: str) -> str | None:
    """The deterministic answer to "what are you": Ellis, nothing else."""
    q = str(question or "").strip().lower()
    if not q:
        return None
    if any(p in q for p in _IDENTITY):
        return _IDENTITY_REPLY_ZH if _is_chinese(question) else _IDENTITY_REPLY_EN
    return None


# Clear off-topic subjects that refuse even when a place is named:
# "the weather in Tokyo" is about weather, not entry rules.
_OFFTOPIC_MARKERS = (
    "weather", "天气", "天氣", "joke", "笑话", "笑話", "capital of", "首都",
    "population", "人口", "exchange rate", "汇率", "匯率", "stock", "股票",
    "hotel", "酒店", "restaurant", "餐厅", "餐廳", "recipe", "菜谱", "菜譜",
    "song", "唱歌", "movie", "电影", "電影", "football", "nba", "world cup",
    "what time", "time in", "几点", "幾點", "who won", "math", "calculate",
    "算一下", "poem", "写诗", "寫詩", "story", "讲个故事", "講個故事",
)


def off_topic_reply(question: str) -> str | None:
    """One sentence for questions that are not about immigration at all.

    Two rules, in order. A clear off-topic subject (weather, jokes, hotels)
    refuses even when a place is named, unless an immigration word is also
    present. Otherwise any country name or immigration word keeps the
    question in scope, so terse route questions ("china to japan") are
    never refused. The composer carries the same rule as a final net."""
    q = str(question or "").strip()
    if not q:
        return None
    low = q.lower()
    on_topic = any(w in low or w in q for w in _TOPIC_WORDS)
    if any(m in low or m in q for m in _OFFTOPIC_MARKERS) and not on_topic:
        return REFUSAL_ZH if _is_chinese(q) else REFUSAL_EN
    if on_topic:
        return None
    from . import kimi_primary
    if kimi_primary._country_mentions(q):
        return None
    if region_destination(q):
        return None          # "hainan" or "jeju" is a place, not off topic
    return REFUSAL_ZH if _is_chinese(q) else REFUSAL_EN


def region_destination(question: str) -> str | None:
    """A sub-national region named in the question steers the DESTINATION:
    "hainan" means China, "jeju" means Korea. The map lives in the policy
    store, so a new regional policy automatically teaches the parser its
    region words."""
    from . import special_policies
    q = str(question or "").lower()
    if not q.strip():
        return None
    for e in special_policies._load():
        if e.get("region") and e.get("destination"):
            if any(t in q for t in e.get("triggers") or []):
                return str(e["destination"]).upper()
    return None


_COMPARE_MARKERS = (" or ", " vs ", " versus ", "compare", "easier",
                    "cheaper", "better", "which one", "哪个", "哪個",
                    "还是", "還是", "对比", "對比", "比较", "比較")


_FROM_MARKERS = ("from ", "hold a ", "holder", "护照", "護照", "持")


def split_nationality(question: str, isos: list[str]) -> tuple[str, list[str]]:
    """When a comparative question names its own passport ("From China, is
    Japan or Korea easier?"), peel that country off the destination list."""
    q = str(question or "")
    low = q.lower()
    from . import kimi_primary
    for pos, iso, _dem in kimi_primary._country_mentions(q):
        lead = low[max(0, pos - 12):pos]
        tail = q[pos:pos + 10]
        if "from " in lead or "持" in q[max(0, pos - 3):pos]                 or "护照" in tail or "護照" in tail:
            if iso in isos:
                return iso, [d for d in isos if d != iso]
    return "", isos


def comparison_destinations(question: str, nationality: str) -> list[str]:
    """Two or three destinations a comparative question names, or []. The
    nationality itself never counts as a destination."""
    q = str(question or "")
    low = q.lower()
    if not any(m in low or m in q for m in _COMPARE_MARKERS):
        return []
    from . import kimi_primary
    isos = []
    for _pos, iso, _dem in kimi_primary._country_mentions(q):
        if iso != (nationality or "").upper() and iso not in isos:
            isos.append(iso)
    return isos[:3] if len(isos) >= 2 else []


_SYSTEM = """You are Ellis, the visa assistant on a travel information site.
Compose one short reply to the traveller using ONLY the facts in the FACTS
JSON. Hard rules:
- Never invent a fact. If the FACTS do not answer the question, say what is
  known and point the reader to the official source page.
- Never state a number that is not in the FACTS, even one you believe you
  know. A missing figure is described as not shown here, with the source
  page as the place to check.
- Reply in the language of the question. A Chinese question gets a Chinese
  reply.
- 2 to 5 short sentences. State fees, stays and dates exactly as given in
  the FACTS. Plain, warm, professional.
- If the question is not about immigration, visas, entry rules or travel,
  reply exactly: Sorry, I can only help with immigration matters.
  For a Chinese question: 抱歉，我只能协助出入境相关事务。
- If asked your name or what you are, you are Ellis. Never mention AI,
  models, providers or internal systems.
- No em dashes. No semicolons. The page shows the full record below your
  reply, so refer the reader to it when detail matters.
- When FACTS carries a "comparison" list, answer the comparison directly:
  say which option is simpler or cheaper for the traveller and why, using
  only the listed facts, one sentence per route.
Return JSON: {"reply": "..."}"""

_FACT_FIELDS = ("disposition", "requirement_detail", "visa_category",
                "permitted_stay", "government_fee", "processing_time",
                "application_channel", "application_channel_detail",
                "official_portal_url", "source_url", "visa_products",
                "exceptions", "required_documents", "entry_requirements",
                "arrival_card")


def compose_reply(question: str, history: list | None, out: dict) -> str | None:
    """A grounded reply from the composer model, or None to let the page
    fall back to its deterministic summary."""
    from . import kimi_primary
    g = out.get("guidance") or {}
    facts = {k: g.get(k) for k in _FACT_FIELDS if g.get(k) is not None}
    facts["route"] = out.get("route") or {}
    if out.get("comparison"):
        facts["comparison"] = out["comparison"]
    if out.get("special_policies"):
        facts["special_policies"] = out["special_policies"]
    if out.get("held"):
        facts = {"route": out.get("route") or {},
                 "held": ("This route's answer is being checked against the "
                          "official source before it is shown. Say so and "
                          "invite the reader to check back shortly.")}
    turns = []
    for h in (history or [])[-8:]:
        role = "traveller" if (h.get("role") == "user") else "ellis"
        text = str(h.get("text") or "")[:300]
        if text:
            turns.append(f"{role}: {text}")
    payload = {"question": str(question or "")[:500],
               "conversation": turns, "facts": facts}
    try:
        raw = kimi_primary._call(_SYSTEM,
                                 json.dumps(payload, ensure_ascii=False),
                                 timeout=10.0, max_tokens=1200)
    except Exception:  # noqa: BLE001 - the fallback summary always exists
        return None
    reply = raw.get("reply") if isinstance(raw, dict) else None
    if not reply or not isinstance(reply, str):
        return None
    reply = reply.strip()[:900]
    if len(reply) < 8:
        return None          # "..." and friends are not answers
    # Grounding guard: every number in the reply must exist in the facts
    # payload or the question itself. The composer once added a correct but
    # unserved fee from its own memory, which is exactly the inference the
    # standard prohibits. A reply that fails this is discarded for the
    # deterministic summary.
    allowed = set(re.findall(r"\d+", json.dumps(payload, ensure_ascii=False)))
    used = set(re.findall(r"\d+", reply))
    if not used.issubset(allowed):
        return None
    # House style and identity discipline, enforced after the fact too.
    reply = reply.replace("—", ". ").replace(";", ".")
    if re.search(r"\b(kimi|moonshot|gpt|claude|llm|language model)\b",
                 reply, re.I):
        return None
    return reply or None


_CLARIFY_SYSTEM = """You are Ellis, the visa assistant on a travel site.
The traveller's route is not fully known yet. Write ONE short, warm reply
(1 to 3 short sentences) that responds to what the traveller just said and
asks for exactly what is still missing.
Hard rules:
- KNOWN lists what is already known, MISSING what you still need (the
  passport country, the destination, or both). Ask only for what is
  missing, and never re-ask what KNOWN already answers.
- You may answer a general immigration point briefly and truthfully, but
  NEVER state a number: no fees, no day counts, no dates. The exact rules
  come once the route is known.
- If the question is not about immigration, entry rules or travel, reply
  exactly: Sorry, I can only help with immigration matters.
  For a Chinese question: 抱歉，我只能协助出入境相关事务。
- If asked your name or what you are, you are Ellis. Never mention AI,
  models, providers or internal systems.
- Reply in the language of the question. No em dashes. No semicolons.
Return JSON: {"reply": "..."}"""


def compose_clarify(question: str, history: list | None, known: dict,
                    missing: list[str]) -> str | None:
    """A conversational ask for the missing route facts, or None to keep
    the deterministic clarify line. Same guards as the answer composer: a
    reply may not carry a single digit of its own, so nothing numeric can
    be invented on a turn that has no verified facts at all."""
    from . import kimi_primary
    turns = []
    for h in (history or [])[-8:]:
        role = "traveller" if (h.get("role") == "user") else "ellis"
        text = str(h.get("text") or "")[:300]
        if text:
            turns.append(f"{role}: {text}")
    payload = {"question": str(question or "")[:500], "conversation": turns,
               "known": {k: v for k, v in (known or {}).items() if v},
               "missing": missing}
    try:
        raw = kimi_primary._call(_CLARIFY_SYSTEM,
                                 json.dumps(payload, ensure_ascii=False),
                                 timeout=10.0, max_tokens=700)
    except Exception:  # noqa: BLE001 - the deterministic line always exists
        return None
    reply = raw.get("reply") if isinstance(raw, dict) else None
    if not reply or not isinstance(reply, str):
        return None
    reply = reply.strip()[:500]
    if len(reply) < 8:
        return None
    allowed = set(re.findall(r"\d+", json.dumps(payload, ensure_ascii=False)))
    if not set(re.findall(r"\d+", reply)).issubset(allowed):
        return None
    reply = reply.replace("—", ". ").replace(";", ".")
    if re.search(r"\b(kimi|moonshot|gpt|claude|llm|language model)\b",
                 reply, re.I):
        return None
    return reply or None
