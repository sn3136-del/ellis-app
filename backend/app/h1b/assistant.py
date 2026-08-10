"""Ask Ellis (H1B): grounded assistant tools + the deterministic walkthrough.

Doctrine enforced here, not just documented:

 - Party wall: every read tool returns a PARTY-SCOPED view assembled on the
   backend (the api module's _party_roles / pipeline visibility rules). The
   model's grounding context never carries the other party's private facts,
   so no prompt can make the model echo them.
 - Personal acts stay personal: the assistant has NO sign / pay / submit /
   declaration tool (PROHIBITED_FOR_MODEL is untouched). Its two ACTION tools
   execute only through the existing party-gated api code paths AS the calling
   human's principal — the model can never exceed the caller's authority, and
   an unauthorized proposal comes back as an honest 403 action report, never
   an execution.
 - Execution-class honesty: the response's engine field carries the real
   provider name (the deterministic local stand-in never masquerades as live).
 - The attorney disclaimer (h1b/disclaimer.py) rides on every payload.
 - Every user-visible string lives in the STRINGS catalog (en/zh-CN/zh-Hant).

The counsel and forms modules are resolved through lazy import seams
(_rfe_risks_provider / _prepare_form_provider). Both modules have landed and
the seams resolve to the real code; the graceful degrade paths remain only as
guards against a genuine import failure, and the prepare seam wraps
forms.prepare_form with the endpoint's own petitioner gate and error contract.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from .. import models
from ..providers.kimi import ToolSecurityError, run_agent, validate_tool_call
from ..security import Principal, require_owner
from . import filing as h1b_filing
from . import models as h1b_models
from . import steps as h1b_steps
from .disclaimer import DISCLAIMER_VERSION, disclaimer
from .guidance import AS_OF, FEES, STEP_FACTS

# ---------------------------------------------------------------------------
# i18n catalog. Every user-visible string this module emits resolves through
# tr(); en is the fallback for any other locale (matching disclaimer()).
# ---------------------------------------------------------------------------
STRINGS = {
    "h1b.walkthrough.step.lca.explain": {
        "en": "The employer files the Labor Condition Application ({form}) on "
              "{portal}. DOL usually certifies or denies it within 7 working "
              "days; it can be filed no earlier than 6 months before the "
              "employment start date.",
        "zh-CN": "由雇主在 {portal} 提交劳工条件申请（{form}）。劳工部通常在 7 个"
                 "工作日内作出批准或拒绝决定；最早可在就业开始日期前 6 个月提交。",
        "zh-Hant": "由僱主在 {portal} 提交勞工條件申請（{form}）。勞工部通常在 7 個"
                   "工作日內作出核准或拒絕決定；最早可在就業開始日期前 6 個月提交。",
    },
    "h1b.walkthrough.step.registration.explain": {
        "en": "The employer submits the H-1B electronic registration ({form}) "
              "on {portal} during the annual window (~early March). Selection "
              "is wage-weighted; only selected registrations may file the "
              "I-129.",
        "zh-CN": "由雇主在年度窗口期（约 3 月初）内在 {portal} 提交 H-1B 电子注册"
                 "（{form}）。抽签按工资等级加权；只有被选中的注册才能提交 I-129。",
        "zh-Hant": "由僱主在年度窗口期（約 3 月初）內在 {portal} 提交 H-1B 電子註冊"
                   "（{form}）。抽籤按工資等級加權；只有被選中的註冊才能提交 I-129。",
    },
    "h1b.walkthrough.step.i129.explain": {
        "en": "The employer files the I-129 petition ({form}) on {portal}. It "
              "requires a certified LCA — and for cap cases a selection "
              "notice — before it can be filed.",
        "zh-CN": "由雇主在 {portal} 提交 I-129 申请（{form}）。提交前必须持有已认证"
                 "的 LCA；名额抽签案件还须持有中签通知。",
        "zh-Hant": "由僱主在 {portal} 提交 I-129 申請（{form}）。提交前必須持有已認證"
                   "的 LCA；名額抽籤案件還須持有中籤通知。",
    },
    "h1b.walkthrough.step.ds160_consular.explain": {
        "en": "You complete the DS-160 and attend the consular interview "
              "({form}) via {portal}. It requires the approved I-129 (I-797) "
              "first; H-1B interviews in China are in person.",
        "zh-CN": "由您本人填写 DS-160 并参加领事馆面谈（{form}），入口为 {portal}。"
                 "须先取得已批准的 I-129（I-797）；在中国，H-1B 面谈必须本人到场。",
        "zh-Hant": "由您本人填寫 DS-160 並參加領事館面談（{form}），入口為 {portal}。"
                   "須先取得已核准的 I-129（I-797）；在中國，H-1B 面談必須本人到場。",
    },
    "h1b.walkthrough.waiting.petitioner": {
        "en": "Waiting on the employer.",
        "zh-CN": "等待雇主处理。",
        "zh-Hant": "等待僱主處理。"},
    "h1b.walkthrough.waiting.beneficiary": {
        "en": "Waiting on the worker.",
        "zh-CN": "等待员工处理。",
        "zh-Hant": "等待員工處理。"},
    "h1b.walkthrough.blocker.dependency": {
        "en": "Blocked: step '{dep}' is not verified yet.",
        "zh-CN": "受阻：步骤“{dep}”尚未通过核验。",
        "zh-Hant": "受阻：步驟「{dep}」尚未通過核驗。"},
    "h1b.walkthrough.blocker.missing_document": {
        "en": "Missing required document: {item}",
        "zh-CN": "缺少必需文件：{item}",
        "zh-Hant": "缺少必需文件：{item}"},
    "h1b.walkthrough.blocker.window_closed": {
        "en": "The H-1B registration window is closed; next window: {window}.",
        "zh-CN": "H-1B 注册窗口目前关闭；下一个窗口：{window}。",
        "zh-Hant": "H-1B 註冊窗口目前關閉；下一個窗口：{window}。"},
    "h1b.walkthrough.action.upload": {
        "en": "Upload: {item}",
        "zh-CN": "上传：{item}",
        "zh-Hant": "上傳：{item}"},
    "h1b.walkthrough.action.answer": {
        "en": "Provide the job facts (job title, wage offer)",
        "zh-CN": "填写职位信息（职位名称、工资）",
        "zh-Hant": "填寫職位資訊（職位名稱、工資）"},
    "h1b.walkthrough.action.release": {
        "en": "Release the {form} filing",
        "zh-CN": "启动 {form} 申报",
        "zh-Hant": "啟動 {form} 申報"},
    "h1b.walkthrough.action.personal_login": {
        "en": "Sign in to {portal} yourself (personal act)",
        "zh-CN": "由您本人登录 {portal}（个人操作）",
        "zh-Hant": "由您本人登入 {portal}（個人操作）"},
    "h1b.walkthrough.action.sign": {
        "en": "Review and sign personally (penalty of perjury)",
        "zh-CN": "本人审核并签署（承担伪证处罚责任）",
        "zh-Hant": "本人審核並簽署（承擔偽證處罰責任）"},
    "h1b.walkthrough.action.verify": {
        "en": "Record the government outcome when it arrives",
        "zh-CN": "政府结果送达后进行登记核验",
        "zh-Hant": "政府結果送達後進行登記核驗"},
    "h1b.walkthrough.action.download_form": {
        "en": "Download the prepared {form}",
        "zh-CN": "下载已准备好的 {form}",
        "zh-Hant": "下載已準備好的 {form}"},
    "h1b.assistant.action.done": {
        "en": "{tool} completed.",
        "zh-CN": "{tool} 已完成。",
        "zh-Hant": "{tool} 已完成。"},
    "h1b.assistant.action.denied": {
        "en": "{tool} was refused ({status}): {reason}",
        "zh-CN": "{tool} 被拒绝（{status}）：{reason}",
        "zh-Hant": "{tool} 被拒絕（{status}）：{reason}"},
    "h1b.assistant.action.refused_security": {
        "en": "{tool} was blocked by the tool security policy.",
        "zh-CN": "{tool} 已被工具安全策略拦截。",
        "zh-Hant": "{tool} 已被工具安全策略攔截。"},
    "h1b.assistant.action.failed": {
        "en": "{tool} failed; nothing was changed.",
        "zh-CN": "{tool} 执行失败；未做任何更改。",
        "zh-Hant": "{tool} 執行失敗；未做任何更改。"},
    "h1b.assistant.rfe.petitioner_only": {
        "en": "RFE risk analysis contains employer-side facts and is available "
              "to the employer (petitioner) or an administrator only.",
        "zh-CN": "RFE 风险分析包含雇主侧信息，仅雇主（申请方）或管理员可查看。",
        "zh-Hant": "RFE 風險分析包含僱主側資訊，僅僱主（申請方）或管理員可查看。"},
    "h1b.assistant.reply.uncertain": {
        "en": "I can only answer from your case's own facts. Please check the "
              "official source: {source}",
        "zh-CN": "我只能依据您案件中的既有信息回答。请查阅官方来源：{source}",
        "zh-Hant": "我只能依據您案件中的既有資訊回答。請查閱官方來源：{source}"},
    "h1b.assistant.suggest.review_walkthrough": {
        "en": "Open the step-by-step walkthrough for your next actions.",
        "zh-CN": "打开分步指引查看您的下一步操作。",
        "zh-Hant": "開啟分步指引查看您的下一步操作。"},
}


def tr(key: str, locale: str = "en", **fmt) -> str:
    entry = STRINGS.get(key) or {}
    text = entry.get(locale) or entry.get("en") or key
    return text.format(**fmt) if fmt else text


# The system framing run_agent carries to the model. The Ellis identity guard
# (i18n.ELLIS_SYSTEM_IDENTITY) is prefixed by the live provider itself; the
# "h1b_assistant" prefix routes the deterministic local provider.
_ASSISTANT_GOAL = (
    "h1b_assistant: guide this user through their H1B case. You are Ellis. "
    "This is procedural guidance, not legal advice — recommend a licensed US "
    "immigration attorney for eligibility or strategy questions. Answer ONLY "
    "from the grounded context provided; if it does not answer the question, "
    "say you are not certain and point at the official source URLs in the "
    "context. To act, propose ONLY allowlisted tools as JSON "
    '{"reply": "...", "tool_calls": [{"tool": "...", "args": {}}]}. '
    "Never propose signing, paying, submitting, or accepting a declaration - "
    "those are personal acts the humans perform themselves."
)

# Curated fee rows relevant to each pipeline step (guidance.FEES keys).
_FEES_BY_STEP = {
    "lca": ("lca",),
    "registration": ("registration",),
    "i129": ("i129_base_online", "i129_base_paper", "i129_base_small_nonprofit",
             "asylum_program", "acwia_training", "fraud_prevention",
             "pl_114_113", "premium_processing"),
    "ds160_consular": (),
}

# Grounding deny-list: identity credentials never enter model context even for
# the party that owns them ("non-sensitive context only" — kimi.py contract).
_GROUNDING_ANSWER_DENY = ("passport_number",)


# ---------------------------------------------------------------------------
# Case access + party-scoped views
# ---------------------------------------------------------------------------
def load_parent_case(db, case_id: str, principal: Principal):
    parent = db.get(models.VisaApplication, str(case_id or ""))
    if parent is None or parent.visa_type != "h1b":
        raise HTTPException(404, "h1b case not found")
    require_owner(principal, parent.org_id)
    return parent


def checklist_status_for(db, principal: Principal, parent) -> dict:
    """The umbrella checklist with live status, scoped to the caller's party.
    An admin or an org caller bound to no party sees all items (mirroring the
    pipeline read's visibility rule); a bound party sees its own items only."""
    from ..checklist_intake import checklist_state
    from . import api as h1b_api
    state = checklist_state(db, parent)
    roles = h1b_api._party_roles(db, parent.id, principal)
    items = state.get("items") or []
    if roles and "admin" not in roles:
        items = [i for i in items if i.get("party", "beneficiary") in roles]
    slim = [{"id": i.get("id"), "label": i.get("label"),
             "party": i.get("party", "beneficiary"), "kind": i.get("kind"),
             "required": bool(i.get("required")), "status": i.get("status")}
            for i in items]
    required_missing = sum(
        1 for i in slim if i["required"] and i["kind"] == "document"
        and i["status"] not in ("submitted", "waived"))
    return {"case_id": parent.id, "items": slim,
            "counts": {"total": len(slim), "required_missing": required_missing},
            "intake_stage": state.get("intake_stage")}


def _party_scoped_answers(db, parent, roles: set) -> dict:
    """The answers the CALLER's party owns — never the other party's. The
    beneficiary view applies the same whitelist the filing partition uses, so
    petitioner facts salted into parent.answers can never surface here."""
    out: dict = {}
    if "beneficiary" in roles or "admin" in roles:
        for k, v in (parent.answers or {}).items():
            if v in (None, "") or k in _GROUNDING_ANSWER_DENY:
                continue
            if (k in h1b_filing.BENEFICIARY_SHARED_KEYS
                    or k.startswith(h1b_filing.BENEFICIARY_SHARED_PREFIXES)):
                out[k] = v
    if "petitioner" in roles or "admin" in roles:
        pet = h1b_filing._petitioner_party(db, parent.id)
        if pet is not None:
            for k, v in (pet.answers or {}).items():
                if v in (None, "") or k in _GROUNDING_ANSWER_DENY:
                    continue
                out[k] = v
            if pet.employer_profile_id:
                profile = db.get(h1b_models.EmployerProfile,
                                 pet.employer_profile_id)
                if profile is not None:
                    out["employer_legal_name"] = profile.legal_name
                    if profile.fein:  # last-4 only; the full FEIN never
                        out["employer_fein_last4"] = profile.fein[-4:]
    return out


def build_grounding(db, principal: Principal, parent, locale: str = "en") -> dict:
    """The server-assembled, party-scoped context the model reasons over."""
    from . import api as h1b_api
    roles = h1b_api._party_roles(db, parent.id, principal)
    steps_payload = h1b_steps.recompute_readiness(db, parent.id)
    parties = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == parent.id)).scalars().all()

    def _visible(role: str) -> bool:
        return ("admin" in roles) or (not roles) or (role in roles)

    facts = {s["step_key"]: dict(STEP_FACTS.get(s["step_key"], {}))
             for s in steps_payload}
    fee_keys: list = []
    for s in steps_payload:
        fee_keys.extend(_FEES_BY_STEP.get(s["step_key"], ()))
    return {
        "case_id": parent.id,
        "case_kind": (parent.answers or {}).get("h1b_case_kind"),
        "caller_roles": sorted(roles),
        "steps": steps_payload,
        "parties": [{"role": p.role, "display_name": p.display_name,
                     "status": p.status} for p in parties if _visible(p.role)],
        "checklist": checklist_status_for(db, principal, parent),
        "step_facts": facts,
        "fees": {k: FEES[k] for k in fee_keys if k in FEES},
        "party_answers": _party_scoped_answers(db, parent, roles),
        "sources": sorted({f.get("source", "") for f in facts.values()
                           if f.get("source")}),
    }


# ---------------------------------------------------------------------------
# Tool executors. Each runs AS the calling principal through the party-gated
# api helpers; HTTPExceptions become honest tool results, never executions.
# ---------------------------------------------------------------------------
def _tool_get_pipeline(db, principal, args, locale):
    from . import api as h1b_api
    return h1b_api.get_pipeline(str(args.get("case_id") or ""), locale=locale,
                                principal=principal, db=db)


def _tool_get_checklist_status(db, principal, args, locale):
    parent = load_parent_case(db, args.get("case_id"), principal)
    return checklist_status_for(db, principal, parent)


def _tool_get_step_facts(db, principal, args, locale):
    step_key = str(args.get("step_key") or "")
    if step_key not in STEP_FACTS:
        raise HTTPException(404, f"unknown h1b step '{step_key}'")
    return {"step_key": step_key, "as_of": AS_OF,
            "facts": dict(STEP_FACTS[step_key]),
            "fees": {k: FEES[k] for k in _FEES_BY_STEP.get(step_key, ())
                     if k in FEES}}


def _rfe_risks_provider():
    """Import seam over the (now landed) counsel module. Tests monkeypatch
    this, and the graceful 'counsel unavailable' path below still guards a
    genuine import failure."""
    from .counsel import rfe_risks
    return rfe_risks


def _tool_get_rfe_summary(db, principal, args, locale):
    from . import api as h1b_api
    parent = load_parent_case(db, args.get("case_id"), principal)
    roles = h1b_api._party_roles(db, parent.id, principal)
    if not ({"admin", "petitioner"} & roles):
        # Party wall: RFE risk analysis is built on employer-side facts (wage
        # level, financials, corporate structure); a caller not acting for the
        # petitioner gets the honest refusal, never the payload.
        return {"case_id": parent.id, "risks": [],
                "note": tr("h1b.assistant.rfe.petitioner_only", locale)}
    try:
        rfe_risks = _rfe_risks_provider()
        risks = list(rfe_risks(db, parent) or [])
    except Exception:
        return {"case_id": parent.id, "risks": [], "note": "counsel unavailable"}
    return {"case_id": parent.id, "risks": risks, "note": ""}


def _tool_release_step(db, principal, args, locale):
    # The SAME code path as the release endpoint, as the CALLING principal:
    # per-party authorization, the statutory window gate, FEIN-on-filing
    # validation, and honest 403/409s all apply unchanged.
    from . import api as h1b_api
    return h1b_api.release_step(str(args.get("case_id") or ""),
                                str(args.get("step_key") or ""),
                                locale=locale, principal=principal, db=db)


def _prepare_form_provider():
    """Import seam over the (now landed) forms module. Tests monkeypatch this,
    and the graceful 'forms unavailable' path below still guards a genuine
    import failure. The resolved callable has the pinned
    (db, principal, case, form_key) shape and applies the SAME
    petitioner-or-admin gate and error contract as the
    /forms/{form_key}/prepare endpoint — the model can never route around the
    party wall by asking the assistant instead of the endpoint."""
    from . import api as h1b_api
    from . import forms as h1b_forms

    # Chat users type the un-hyphenated spellings; the canonical FORM_KEYS
    # are hyphenated ('i-129', 'eta-9035').
    aliases = {"i129": "i-129", "eta9035": "eta-9035",
               "eta_9035": "eta-9035", "lca": "eta-9035"}

    def _prepare(db, principal, case, form_key):
        key = aliases.get(str(form_key or "").strip().lower(),
                          str(form_key or "").strip().lower())
        if key not in h1b_forms.FORM_KEYS:
            raise HTTPException(404, {
                "reason": f"unknown form '{form_key}'",
                "known_forms": list(h1b_forms.FORM_KEYS)})
        # Party wall: preparing an official form is a petitioner-side act
        # (same gate as forms_api._petitioner_case), as the CALLING principal.
        h1b_api._authorize_step_action(db, case.id, principal, "petitioner")
        try:
            out = h1b_forms.prepare_form(db, case, key,
                                         actor=principal.user_id)
        except LookupError as e:
            raise HTTPException(409, {"reason": str(e)})
        return {"prepared": True, **out}

    return _prepare


def _tool_prepare_form(db, principal, args, locale):
    parent = load_parent_case(db, args.get("case_id"), principal)
    try:
        prepare_form = _prepare_form_provider()
    except Exception:
        return {"case_id": parent.id, "prepared": False,
                "note": "forms unavailable"}
    return dict(prepare_form(db, principal, parent,
                             str(args.get("form_key") or "")) or {})


_EXECUTORS = {
    "get_h1b_pipeline": _tool_get_pipeline,
    "get_h1b_checklist_status": _tool_get_checklist_status,
    "get_h1b_step_facts": _tool_get_step_facts,
    "get_h1b_rfe_summary": _tool_get_rfe_summary,
    "release_h1b_step": _tool_release_step,
    "prepare_h1b_form": _tool_prepare_form,
}


def execute_tool(db, principal: Principal, name: str, args: dict, *,
                 locale: str = "en") -> dict:
    """Validate (defense in depth — run_agent already validated the proposal)
    and execute one tool AS the calling principal. Always returns an honest
    result dict; a denial is reported, never silently retried or escalated."""
    name = str(name or "")
    args = dict(args or {})
    try:
        validate_tool_call(name, args)
    except ToolSecurityError as e:
        return {"tool": name, "ok": False,
                "summary": tr("h1b.assistant.action.refused_security", locale,
                              tool=name),
                "output": {"error": str(e)}}
    fn = _EXECUTORS.get(name)
    if fn is None:
        return {"tool": name, "ok": False,
                "summary": tr("h1b.assistant.action.failed", locale, tool=name),
                "output": {"error": "tool has no h1b assistant executor"}}
    try:
        out = fn(db, principal, args, locale)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {"reason": str(e.detail)}
        reason = str(detail.get("reason") or detail)
        return {"tool": name, "ok": False,
                "summary": tr("h1b.assistant.action.denied", locale, tool=name,
                              status=e.status_code, reason=reason),
                "output": {"status": e.status_code, "detail": detail}}
    except Exception as e:  # honest failure; internals never reach the user
        return {"tool": name, "ok": False,
                "summary": tr("h1b.assistant.action.failed", locale, tool=name),
                "output": {"error_type": type(e).__name__}}
    return {"tool": name, "ok": True,
            "summary": tr("h1b.assistant.action.done", locale, tool=name),
            "output": out}


# ---------------------------------------------------------------------------
# Deterministic guided walkthrough (no model anywhere in this path)
# ---------------------------------------------------------------------------
def _checklist_items(db, parent) -> list[dict]:
    from ..checklist_intake import checklist_state
    return checklist_state(db, parent).get("items") or []


def _missing_required_items(items: list[dict], party: str) -> list[dict]:
    return [i for i in items
            if i.get("party", "beneficiary") == party
            and i.get("kind") == "document" and i.get("required")
            and i.get("status") not in ("submitted", "waived")]


def _next_actions(step_payload: dict, facts: dict, missing_items: list[dict],
                  missing_job_facts: list[str], window_closed: bool,
                  locale: str) -> list[dict]:
    key = step_payload["step_key"]
    party = step_payload["acting_party"]
    status = step_payload["status"]
    acts: list[dict] = []
    if status == "verified":
        return acts
    if status in ("blocked", "ready"):
        for i in missing_items:
            acts.append({"kind": "upload", "who": party,
                         "label": tr("h1b.walkthrough.action.upload", locale,
                                     item=i.get("label") or i.get("id"))})
        if party == "petitioner" and missing_job_facts:
            acts.append({"kind": "answer", "who": party,
                         "label": tr("h1b.walkthrough.action.answer", locale)})
    if status == "ready" and not window_closed:
        acts.append({"kind": "release", "who": party,
                     "label": tr("h1b.walkthrough.action.release", locale,
                                 form=facts.get("form", key))})
    if status == "in_progress":
        acts.append({"kind": "personal_login", "who": party,
                     "label": tr("h1b.walkthrough.action.personal_login",
                                 locale, portal=facts.get("portal", ""))})
        if key == "ds160_consular":
            acts.append({"kind": "download_form", "who": party,
                         "label": tr("h1b.walkthrough.action.download_form",
                                     locale, form=facts.get("form", key))})
        acts.append({"kind": "sign", "who": party,
                     "label": tr("h1b.walkthrough.action.sign", locale)})
        acts.append({"kind": "verify", "who": party,
                     "label": tr("h1b.walkthrough.action.verify", locale)})
    if status == "awaiting_government":
        acts.append({"kind": "verify", "who": party,
                     "label": tr("h1b.walkthrough.action.verify", locale)})
    return acts


def build_walkthrough(db, principal: Principal, parent, *,
                      locale: str = "en") -> dict:
    """Party-scoped, deterministic walkthrough: each caller sees their own
    actions in full and the other party's step only as 'waiting on the
    employer/worker'. Blockers are honest — unverified dependencies by name,
    the acting party's missing required documents, and the statutory
    registration window state."""
    from . import api as h1b_api
    roles = h1b_api._party_roles(db, parent.id, principal)
    steps_payload = h1b_steps.recompute_readiness(db, parent.id)
    by_key = {s["step_key"]: s for s in steps_payload}
    items = _checklist_items(db, parent)
    win = (h1b_api._registration_window_status()
           if "registration" in by_key else None)
    pet = h1b_filing._petitioner_party(db, parent.id)
    pet_answers = (pet.answers or {}) if pet is not None else {}
    missing_job_facts = [k for k in ("job_title", "wage_offer")
                         if not pet_answers.get(k)]
    out_steps = []
    for s in steps_payload:
        key, party, status = s["step_key"], s["acting_party"], s["status"]
        facts = STEP_FACTS.get(key, {})
        explain = tr(f"h1b.walkthrough.step.{key}.explain", locale,
                     form=facts.get("form", key), portal=facts.get("portal", ""))
        missing = _missing_required_items(items, party)
        blockers = []
        for dep in s.get("depends_on") or []:
            d = by_key.get(dep)
            if d is None or d["status"] != "verified":
                blockers.append(tr("h1b.walkthrough.blocker.dependency",
                                   locale, dep=dep))
        if status in ("blocked", "ready"):
            for i in missing:
                blockers.append(tr("h1b.walkthrough.blocker.missing_document",
                                   locale, item=i.get("label") or i.get("id")))
        window_closed = (key == "registration" and win is not None
                         and not win.get("open") and status != "verified")
        if window_closed:
            blockers.append(tr("h1b.walkthrough.blocker.window_closed", locale,
                               window=win.get("next_window", "")))
        mine = ("admin" in roles) or (party in roles)
        if mine:
            actions = _next_actions(s, facts, missing, missing_job_facts,
                                    window_closed, locale)
            waiting_on = ""
        else:
            actions = []
            waiting_on = tr(f"h1b.walkthrough.waiting.{party}", locale)
        out_steps.append({"step_key": key, "status": status,
                          "acting_party": party,
                          "child_case_id": s.get("child_case_id"),
                          "explain": explain, "source": facts.get("source", ""),
                          "blockers": blockers, "next_actions": actions,
                          "waiting_on": waiting_on})
    return {"case_id": parent.id,
            "case_kind": (parent.answers or {}).get("h1b_case_kind"),
            "caller_roles": sorted(roles),
            "steps": out_steps,
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}


def _suggestions(walkthrough: dict, locale: str) -> list[str]:
    labels: list[str] = []
    for s in walkthrough.get("steps") or []:
        for a in s.get("next_actions") or []:
            if a["label"] not in labels:
                labels.append(a["label"])
    if not labels:
        labels.append(tr("h1b.assistant.suggest.review_walkthrough", locale))
    return labels[:3]


# ---------------------------------------------------------------------------
# The assistant conversation turn
# ---------------------------------------------------------------------------
def run_assistant(db, principal: Principal, parent, *, message: str,
                  locale: str = "en", history: list | None = None) -> dict:
    from ..i18n import assistant_identity_answer, is_identity_question
    grounding = build_grounding(db, principal, parent, locale)
    walkthrough = build_walkthrough(db, principal, parent, locale=locale)
    base = {"case_id": parent.id,
            "suggestions": _suggestions(walkthrough, locale),
            "attorney_disclaimer": disclaimer(locale),
            "disclaimer_version": DISCLAIMER_VERSION}
    # Identity guard: Ellis, never the underlying model — deterministic, so
    # the answer cannot drift with providers.
    if is_identity_question(message):
        return {**base, "reply": assistant_identity_answer(locale),
                "actions": [], "engine": "deterministic-identity-guard"}
    context = {"case_id": parent.id, "message": str(message or ""),
               "locale": locale, "history": list(history or [])[-20:],
               "grounding": grounding}
    res = run_agent(_ASSISTANT_GOAL, context)
    actions = [execute_tool(db, principal, c.get("tool"), c.get("args") or {},
                            locale=locale)
               for c in res.tool_calls]
    reply = str((res.output or {}).get("reply") or "").strip()
    if not reply:
        sources = grounding.get("sources") or [""]
        reply = tr("h1b.assistant.reply.uncertain", locale, source=sources[0])
    return {**base, "reply": reply,
            "actions": [{"tool": a["tool"], "summary": a["summary"],
                         "ok": a["ok"]} for a in actions],
            "engine": res.engine}
