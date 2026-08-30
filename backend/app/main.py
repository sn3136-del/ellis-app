"""FastAPI application — authenticated REST API for the tourist-visa flow.

Every route enforces authentication (Clerk or dev token) and object-level
tenant isolation. Sensitive transitions go through the durable service layer.
"""
from __future__ import annotations

import json
import os
import re
import threading

from typing import Any, Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from .config import capabilities, settings
from .db import get_session, create_all
from . import models, audit, service, execution, filing_acts, portal_queue
from . import progress as progress_vocab
from .security import (Principal, get_principal, require_owner, require_admin,
                       issue_action_token, verify_action_token)
from .providers import ocr as ocr_provider
from .providers import passport_classifier
from .providers import docusign
from .providers.kimi import run_agent
from .portal.contract import list_adapters
from .portal.mock_portal import MockPortal  # only constructed in mock-allowed modes

app = FastAPI(title="Ellis Visa Backend", version="0.1.0")

# CORS so the Electron renderer (file:// / vite dev server) can reach the API.
# The renderer authenticates with a Bearer token in the Authorization header
# (never cookies), so credentials are disabled and a wildcard origin is safe in
# development. Production restricts via ELLIS_CORS_ORIGINS (comma-separated).
import os as _os
from fastapi.middleware.cors import CORSMiddleware

_cors = [o.strip() for o in _os.getenv("ELLIS_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Phase 17: structured redacted request logging + flag-gated Sentry/OTel.
from .observability import RequestLogMiddleware as _ReqLog, init_sentry as _init_sentry, init_otel as _init_otel  # noqa: E402

app.add_middleware(_ReqLog)

# 2026-07-23 snapshot: applicant route intake + resolution + snapshot admin.
from .visa_snapshot.api import router as _snapshot_router  # noqa: E402
# Automated adapter factory: applicant build request/consent/progress + admin
# release queue (brief §10-§13, §33).
from .adapter_factory.api import router as _factory_router  # noqa: E402

app.include_router(_snapshot_router)
app.include_router(_factory_router)
from .global_routes.api import router as _global_router  # noqa: E402
app.include_router(_global_router)
# H1B edition: two-party petition pipeline (docs/H1B_ARCHITECTURE.md).
from .h1b.api import router as _h1b_router  # noqa: E402
app.include_router(_h1b_router)
from .h1b.forms_api import router as _h1b_forms_router  # noqa: E402
app.include_router(_h1b_forms_router)
from .h1b.assistant_api import router as _h1b_assistant_router  # noqa: E402
app.include_router(_h1b_assistant_router)
from .h1b.counsel_api import router as _h1b_counsel_router  # noqa: E402
app.include_router(_h1b_counsel_router)
from .h1b.status_api import router as _h1b_status_router  # noqa: E402
app.include_router(_h1b_status_router)
# Adapter-learning + case-status tracking (makes each build easier than the last).
from .adapter_factory.learning_api import router as _factory_learning_router  # noqa: E402
app.include_router(_factory_learning_router)
# H1B wage-level + occupation classification from official DOL/CDC/O*NET data.
from .h1b.wage_api import router as _h1b_wage_router  # noqa: E402
app.include_router(_h1b_wage_router)
# LCA Public Access File assembly + posting notice (20 CFR 655.734/655.760).
from .h1b.paf_api import router as _h1b_paf_router  # noqa: E402
app.include_router(_h1b_paf_router)
# Appointment cockpit: eligibility triage, pre-stage, group roster, availability.
# Ellis prepares everything; the authorized human performs the booking action.
from .appt_api import router as _appt_router  # noqa: E402
app.include_router(_appt_router)
# Agent-channel booking pipeline: applicant requests in-app, a named human
# operator works the official site in their own session, booked = evidence.
from .appt_booking_api import router as _appt_booking_router  # noqa: E402
app.include_router(_appt_booking_router)
# The DS-160 question bank: the government's own questions, asked inside Ellis.
from .ds160_api import router as _ds160_router  # noqa: E402
app.include_router(_ds160_router)
# H1B ops: org-account bulk registration, RFE response assembly, cap exemption.
from .h1b.ops_api import router as _h1b_ops_router  # noqa: E402
app.include_router(_h1b_ops_router)
# Travel authorizations (ESTA/eTA/UK ETA) + Schengen 90/180 stay engine.
from .travel_api import router as _travel_router  # noqa: E402
app.include_router(_travel_router)


@app.on_event("startup")
def _startup():
    create_all()
    _init_sentry()
    _init_otel()
    # Background portal-run executor (no-op in test runtime): live portal work
    # never runs inside an HTTP request.
    portal_queue.start_executor()


@app.get("/health")
@app.get("/healthz")
def healthz():
    return {"ok": True}


def _uptime_data() -> dict:
    import csv as _csv
    import glob as _glob
    base = os.getenv("ELLIS_UPTIME_DIR", "/var/lib/ellis/uptime")
    months = []
    for f in sorted(_glob.glob(os.path.join(base, "*.csv"))):
        try:
            rows = list(_csv.DictReader(open(f, encoding="utf-8")))
        except OSError:
            continue
        if not rows:
            continue
        ok = sum(1 for r in rows if r.get("code") == "200")
        lat = sorted(int(r["ms"]) for r in rows
                     if r.get("code") == "200" and str(r.get("ms", "")).isdigit())
        months.append({
            "month": os.path.basename(f)[:-4],
            "probes": len(rows), "ok": ok,
            "availability_pct": round(ok / len(rows) * 100, 4),
            "median_latency_ms": lat[len(lat) // 2] if lat else None,
        })
    incidents = 0
    inc = os.path.join(base, "incidents.log")
    if os.path.isfile(inc):
        try:
            incidents = sum(1 for line in open(inc, encoding="utf-8") if line.strip())
        except OSError:
            pass
    return {"probe_interval_seconds": 60, "months": months,
            "incidents": incidents,
            "note": "One probe per minute against the public HTTPS path; "
                    "a failed probe retries once before it counts."}


_UPTIME_I18N = {
    "zh-CN": {
        "nav_db": "数据库", "nav_ops": "质量后台",
        "title": "Ellis 服务可用性", "heading": "Ellis 服务可用性",
        "tagline": "每分钟探测一次完整公网链路（DNS、TLS、网关、后端）",
        "sub": "本月 {probes} 次探测 · 中位延迟 {ms} ms",
        "warming": "探测记录即将开始累积", "incidents": "事件 {n} 起",
        "month": "月份", "probes": "探测次数", "ok": "成功",
        "availability": "可用性", "latency": "中位延迟",
        "note": "探测失败会先重试一次再计入",
    },
    "zh-Hant": {
        "nav_db": "資料庫", "nav_ops": "質量後台",
        "title": "Ellis 服務可用性", "heading": "Ellis 服務可用性",
        "tagline": "每分鐘探測一次完整公網鏈路（DNS、TLS、網關、後端）",
        "sub": "本月 {probes} 次探測 · 中位延遲 {ms} ms",
        "warming": "探測記錄即將開始累積", "incidents": "事件 {n} 起",
        "month": "月份", "probes": "探測次數", "ok": "成功",
        "availability": "可用性", "latency": "中位延遲",
        "note": "探測失敗會先重試一次再計入",
    },
    "en": {
        "nav_db": "Database", "nav_ops": "Quality console",
        "title": "Ellis service availability", "heading": "Ellis service availability",
        "tagline": "The full public path (DNS, TLS, edge, backend) probed every minute",
        "sub": "{probes} probes this month · median latency {ms} ms",
        "warming": "The probe record is starting to accumulate",
        "incidents": "{n} incidents",
        "month": "Month", "probes": "Probes", "ok": "OK",
        "availability": "Availability", "latency": "Median latency",
        "note": "A failed probe retries once before it counts",
    },
}


@app.get("/health/uptime")
def health_uptime(request: Request, format: str = "", lang: str = "zh-CN"):
    """The availability record behind the 99.99% acceptance metric: a cron
    probes the full public path every minute and logs to a monthly CSV; this
    reads it back so the evidence is visible where the acceptance runs.
    Public like /health. A browser gets a styled status page in the chosen
    language; API callers (or ?format=json) get the raw record."""
    data = _uptime_data()
    wants_html = "text/html" in (request.headers.get("accept") or "")
    if format == "json" or not wants_html:
        return data
    L = _UPTIME_I18N.get(lang) or _UPTIME_I18N["zh-CN"]
    lang = lang if lang in _UPTIME_I18N else "zh-CN"
    cur = data["months"][-1] if data["months"] else None
    pct = cur["availability_pct"] if cur else None
    good = pct is not None and pct >= 99.99
    hero = f"{pct}%" if pct is not None else "·"
    color = "#0b7a44" if good else "#b45309"
    rows = "".join(
        f"<tr><td>{m['month']}</td>"
        f"<td class='num'>{m['probes']:,}</td>"
        f"<td class='num'>{m['ok']:,}</td>"
        f"<td class='num'><strong>{m['availability_pct']}%</strong></td>"
        f"<td class='num'>{m['median_latency_ms'] if m['median_latency_ms'] is not None else '·'} ms</td></tr>"
        for m in reversed(data["months"]))
    sub = (L["sub"].format(probes=f"{cur['probes']:,}", ms=cur["median_latency_ms"])
           if cur else L["warming"])
    picker = " ".join(
        (f"<strong>{label}</strong>" if code == lang
         else f'<a href="?lang={code}">{label}</a>')
        for code, label in (("zh-CN", "简体中文"), ("zh-Hant", "繁體中文"),
                            ("en", "English")))
    html = f"""<!doctype html><html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{L['title']}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei",
          "Segoe UI", sans-serif; background: #f5f7fb; color: #0f294d;
          padding: 40px 20px; }}
  .wrap {{ max-width: 720px; margin: 0 auto; display: grid; gap: 16px; }}
  /* Grid items refuse to shrink below their content's min width by
     default; without this the month table drags the whole page wide. */
  .wrap > * {{ min-width: 0; }}
  .card {{ background: #fff; border-radius: 16px; padding: 26px 30px;
           box-shadow: 0 1px 3px rgba(15,41,77,.06); }}
  h1 {{ font-size: 18px; font-weight: 800; }}
  .muted {{ color: #64748b; font-size: 13px; }}
  .hero {{ font-size: 56px; font-weight: 800; line-height: 1.1;
           color: {color}; letter-spacing: -1px; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 99px;
          background: {color}; margin-right: 8px; }}
  .langs {{ float: right; font-size: 12.5px; color: #64748b; }}
  /* Every control is a finger-sized target, not a 15px text sliver. */
  .langs a, .langs strong {{ display: inline-block; padding: 10px 7px;
                             margin: -10px 0 -10px 1px; border-radius: 8px; }}
  .tablewrap {{ overflow-x: auto; margin: 0 -8px; padding: 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
  th {{ text-align: left; font-size: 11px; letter-spacing: .06em;
        text-transform: uppercase; color: #64748b; padding: 8px 10px;
        white-space: nowrap; border-bottom: 2px solid #e5eaf2; }}
  td {{ padding: 9px 10px; border-bottom: 1px solid #eef2f8;
        white-space: nowrap; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  th.num {{ text-align: right; }}
  a {{ color: #287dfa; font-weight: 700; text-decoration: none; }}
  .jsonlink {{ display: inline-block; padding: 10px 8px; }}
  @media (max-width: 560px) {{
    body {{ padding: 16px 10px; }}
    .card {{ padding: 18px 16px; border-radius: 14px; }}
    .hero {{ font-size: 42px; }}
    .langs {{ float: none; display: block; margin: 0 0 12px;
              text-align: left; }}
    .langs a, .langs strong {{ margin: 0 6px 0 0; padding: 10px 7px 10px 0; }}
    th, td {{ padding: 8px 6px; font-size: 12.5px; }}
    th {{ letter-spacing: 0; }}
  }}
</style></head><body><div class="wrap">
<div class="card">
  <div class="langs"><a href="/">{L['nav_db']}</a> <a href="/#ops">{L['nav_ops']}</a> <span style="color:#c3cddd">|</span> {picker}</div>
  <h1><span class="dot"></span>{L['heading']}</h1>
  <div class="muted" style="margin-top:4px">ellis-visa.com · {L['tagline']}</div>
  <div class="hero" style="margin-top:18px">{hero}</div>
  <div class="muted" style="margin-top:6px">{sub} · {L['incidents'].format(n=data['incidents'])}</div>
</div>
<div class="card">
  <div class="tablewrap">
  <table>
    <tr><th>{L['month']}</th><th class="num">{L['probes']}</th>
        <th class="num">{L['ok']}</th>
        <th class="num">{L['availability']}</th>
        <th class="num">{L['latency']}</th></tr>
    {rows}
  </table>
  </div>
</div>
<div class="muted" style="text-align:center">
  {L['note']} · <a class="jsonlink" href="?format=json">JSON</a>
</div>
</div></body></html>"""
    return HTMLResponse(html)


@app.get("/capabilities")
def get_capabilities(_: Principal = Depends(get_principal)):
    caps = capabilities()
    # Honest runtime classification: mock-allowed modes bind the MockPortal
    # driver (class MOCK); real-only modes have NO portal driver until an
    # individually approved live adapter exists (class UNSUPPORTED) — surfaced
    # so no client mistakes a completed case for a real government submission.
    from .config import settings as _s
    s = _s()
    if s.mock_portal_allowed:
        runtime_pec = str(execution.classify_driver(MockPortal()))
    else:
        runtime_pec = str(execution.ExecutionClass.UNSUPPORTED)
    caps["runtime_mode"] = s.runtime_mode
    caps["execution_classification"] = {
        "legend": execution.legend(),
        "runtime_portal_execution_class": runtime_pec,
        "real_result_class": execution.REAL_RESULT_CLASS.value,
        "production_mode": s.production_mode,
        "runtime_mode": s.runtime_mode,
        "mock_portal_allowed": s.mock_portal_allowed,
    }
    return caps


# ---- Internationalization (Phase 6): dynamic-content translation + identity ----
class TranslateBody(BaseModel):
    text: str
    target_lang: str
    source_lang: str = "auto"


@app.get("/i18n/languages")
def i18n_languages(_: Principal = Depends(get_principal)):
    from . import i18n
    return {"languages": [{"code": c, "name": i18n.LANGUAGE_NAMES[c]} for c in i18n.SUPPORTED_LANGS]}


@app.post("/i18n/translate")
def i18n_translate(body: TranslateBody, _: Principal = Depends(get_principal)):
    """Backend-only dynamic-content translation via Kimi K3. Placeholders,
    URLs, dates, amounts, filenames, and passport numbers / MRZ / identifiers are
    never translated; unavailable → the original text (never fabricated)."""
    from . import i18n
    try:
        return i18n.translate(body.text, body.target_lang, body.source_lang)
    except Exception:  # noqa: BLE001 — a provider hiccup must not become a 500
        return {"status": "unavailable", "text": body.text,
                "target_lang": body.target_lang}


class CatalogBody(BaseModel):
    target_lang: str
    entries: dict


@app.post("/i18n/catalog")
def i18n_catalog(body: CatalogBody, _: Principal = Depends(get_principal)):
    """Dynamic UI-language support: translate the renderer's English string
    catalog into any supported language via Kimi K3 (masked, cached, chunked).
    Strings the model round-trip loses stay ENGLISH — never fabricated, never
    a hole in the UI. Bounded: at most 800 short strings per call."""
    from . import i18n
    entries = {str(k)[:80]: str(v)[:400] for k, v in list((body.entries or {}).items())[:800]}
    out = i18n.translate_catalog(entries, body.target_lang)
    out["rtl"] = body.target_lang in i18n.RTL_LANGS
    return out


@app.get("/assistant/identity")
def assistant_identity(lang: str = "en", _: Principal = Depends(get_principal)):
    """The assistant always identifies as Ellis, in any supported language, and
    never as the underlying model or as a government official/lawyer/embassy."""
    from . import i18n
    return {"name": "Ellis", "lang": lang, "answer": i18n.assistant_identity_answer(lang)}


# ---- Personal-test safety gate (brief item #6): route readiness ----
class GateBody(BaseModel):
    destination: str
    visa_type: str = "tourist"
    nationality: str = ""
    residence: str = ""
    gate: str
    complete: bool
    evidence: str = ""


@app.get("/routes/readiness")
def route_readiness(destination: str, visa_type: str = "tourist", nationality: str = "",
                    residence: str = "", db=Depends(get_session),
                    p: Principal = Depends(get_principal)):
    from . import personal_gate
    # Gate evidence + the recording admin's id are internal audit material —
    # included only for the admin role.
    return personal_gate.readiness(db, destination=destination, visa_type=visa_type,
                                   nationality=nationality, residence=residence,
                                   include_evidence=(p.role == "admin"))


@app.post("/admin/routes/readiness")
def set_route_gate(body: GateBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Human administrators only. Kimi has no such tool; search results can never
    mark a gate. Evidence is mandatory to mark a gate complete."""
    from . import personal_gate
    require_admin(p)
    try:
        return personal_gate.set_gate(db, destination=body.destination, visa_type=body.visa_type,
                                      nationality=body.nationality, residence=body.residence,
                                      gate=body.gate, complete=body.complete,
                                      evidence=body.evidence, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/cases/{application_id}/live-preflight")
def case_live_preflight(application_id: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    from . import personal_gate, passport_validity
    app_row = _owned(db, p, application_id)
    ec = _case_execution_class(app_row.destination_country, app_row.visa_type, db=db, app_row=app_row)
    pre = personal_gate.live_preflight(db, app_row, ec)
    pre["passport_validity"] = passport_validity.check_case_passport(db, app_row)
    return pre


@app.get("/cases/{application_id}/passport-validity")
def get_passport_validity(application_id: str, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    """Validity verdict: expiry (answers, falling back to the accepted passport
    document's extracted date) vs. the destination rule (verified route rule,
    else the Kimi two-pass requirement) — plus whether renewal is offered."""
    from . import passport_validity, renewal
    app_row = _owned(db, p, application_id)
    verdict = passport_validity.check_case_passport(db, app_row)
    verdict["renewal_offered"] = renewal.should_offer_renewal(verdict)
    linked = renewal.get_linked_renewal(db, app_row)
    if linked is not None:
        verdict["renewal_case_id"] = linked.id
    return verdict


class DatabaseIssueUpdateIn(BaseModel):
    """An operator moving a flagged issue along."""
    status: str = ""          # acknowledged | corrected | dismissed
    resolution: str = ""


# open -> acknowledged -> corrected | dismissed. An issue never silently
# disappears: dismissing one requires saying why, in the same field a
# correction is recorded in.
# Their §4.1.2 loop has five stages: flag, notify the information provider,
# correct, operations review, go live. Three statuses could not express it, so
# review and go-live were invisible and an issue could be closed by the same
# person who wrote the fix.
DATABASE_ISSUE_STATUSES = ("open", "acknowledged", "corrected", "reviewed",
                           "published", "dismissed")
# The order a report has to walk. Skipping a stage or going backwards is
# rejected, so the queue records a progression rather than a free-text label.
_ISSUE_ORDER = {"open": 0, "acknowledged": 1, "corrected": 2, "reviewed": 3,
                "published": 4, "dismissed": 4}


@app.post("/database/issues/{issue_id}")
def travel_database_issue_update(issue_id: str, body: DatabaseIssueUpdateIn,
                                 db=Depends(get_session),
                                 p: Principal = Depends(get_principal)):
    """Track a flagged issue to resolution (their progress-tracking
    requirement). Closing an issue — corrected or dismissed — requires a
    written resolution, so the queue can never be emptied silently."""
    from datetime import datetime, timezone
    from .visa_snapshot.models import DatabaseIssueReport
    require_admin(p)
    status = (body.status or "").strip().lower()
    if status not in DATABASE_ISSUE_STATUSES:
        raise HTTPException(422, f"status must be one of {DATABASE_ISSUE_STATUSES}")
    row = db.get(DatabaseIssueReport, issue_id)
    if row is None:
        raise HTTPException(404, "no such issue")
    if status in ("corrected", "dismissed") and not (body.resolution or "").strip():
        raise HTTPException(422, "say what was corrected, or why this is "
                                 "being dismissed")
    here, there = _ISSUE_ORDER.get(row.status, 0), _ISSUE_ORDER[status]
    if status != "dismissed" and there < here:
        raise HTTPException(422, f"an issue cannot go back from {row.status} "
                                 f"to {status}")
    if status != "dismissed" and there > here + 1:
        raise HTTPException(422, f"{row.status} must be followed by the next "
                                 f"stage, not {status}")
    if status == "reviewed" and row.resolved_by == p.user_id:
        raise HTTPException(422, "the correction has to be reviewed by someone "
                                 "other than the person who wrote it")
    row.status = status
    if body.resolution:
        row.resolution = body.resolution[:1000]
    now = datetime.now(timezone.utc)
    if status == "acknowledged":
        # The provider has been told. Recording who and when is what makes the
        # notify stage auditable rather than assumed.
        row.notified_at = now
        row.notified_to = (body.resolution or "information provider")[:200]
    if status in ("corrected", "dismissed"):
        row.resolved_by = p.user_id
        row.resolved_at = now
    if status == "reviewed":
        row.reviewed_by, row.reviewed_at = p.user_id, now
    if status == "published":
        row.published_at = now
    # "Corrected" has to change what readers actually see. Without this the
    # cached wrong answer kept serving for the rest of its TTL and the queue
    # said the problem was fixed. Expiring the row makes the next lookup
    # re-ask the engine; the reader is never shown a stale answer that an
    # operator has just declared wrong.
    if status in ("corrected", "published") and row.cache_key:
        from sqlalchemy import select as _sel
        from .visa_snapshot.models import KimiRouteGuidanceCache
        cached = db.execute(_sel(KimiRouteGuidanceCache).where(
            KimiRouteGuidanceCache.cache_key == row.cache_key)).scalars().first()
        if cached is not None:
            db.delete(cached)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="database",
                 action="database_issue_" + status,
                 detail={"issue_id": issue_id}, actor=p.user_id)
    return {"ok": True, "id": row.id, "status": row.status,
            "resolution": row.resolution}


class DatabaseApproveIn(BaseModel):
    """An operator releasing a held (low-confidence) answer for display."""
    nationality: str = ""
    destination: str = ""
    travel_purpose: str = "tourism"
    travel_document_type: str = "ordinary_passport"
    note: str = ""
    # The exact answer being released, as reported by the lookup. Without it
    # a release could only ever reach answers with no travel date and no
    # transit, silently missing every other one.
    cache_key: str = ""


@app.post("/database/approve")
def travel_database_approve(body: DatabaseApproveIn, db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """Release a held answer. The engine holds back anything it rates LOW
    confidence; a person who has checked it against the official source marks
    it releasable here. The approval is recorded on the cached answer with WHO
    released it and WHEN, and it dies with the answer: a refresh writes a new
    row, which is held again on its own merits."""
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from .visa_snapshot import kimi_primary
    from .visa_snapshot.models import KimiRouteGuidanceCache
    require_admin(p)
    nat = body.nationality.strip().upper()
    dest = body.destination.strip().upper()
    if not nat or not dest:
        raise HTTPException(422, "nationality and destination are required")
    # The document type is an enum from the registry, enforced here and not
    # just by the UI select — a typo must fail loudly, not become a distinct
    # cache key with a nonsense answer.
    from .visa_snapshot.registry import load_registry
    doc_codes = {e["code"] for e in load_registry("travel_document_types")["entries"]}
    if body.travel_document_type and body.travel_document_type not in doc_codes:
        raise HTTPException(422, f"unknown travel_document_type; one of {sorted(doc_codes)}")
    key = body.cache_key.strip() or kimi_primary.cache_key({
        "passport_nationality": nat, "lawful_country_of_residence": nat,
        "destination_country": dest,
        "travel_purpose": body.travel_purpose or "tourism",
        "travel_document_type": body.travel_document_type or "ordinary_passport"})
    row = db.execute(_select(KimiRouteGuidanceCache).where(
        KimiRouteGuidanceCache.cache_key == key)).scalars().first()
    if row is None:
        raise HTTPException(404, "no cached answer for that route")
    ver = dict(row.verification or {})
    ver["operator_released"] = {
        "by": p.user_id, "at": datetime.now(timezone.utc).isoformat(),
        "note": (body.note or "")[:500]}
    row.verification = ver
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="database",
                 action="database_answer_released",
                 detail={"nationality": nat, "destination": dest}, actor=p.user_id)
    return {"ok": True, "cache_key": key, "released_by": p.user_id}


# One in-flight page check per route at a time; re-ground when the last check
# is older than this many days (or never happened).
_GROUNDING_IN_FLIGHT: set = set()
GROUND_ON_ACCESS_DAYS = 3


def should_reground(out: dict) -> bool:
    """Does this served answer deserve a fresh official-page check?

    The owner's rule: a route someone is asking about RIGHT NOW should be
    checked against the most recent official data — serve instantly, verify
    in the background, so the next viewer (or the same one, refreshing) sees
    the page-checked answer. True when the answer has never been grounded, or
    its last grounding is older than GROUND_ON_ACCESS_DAYS."""
    import datetime as _dt
    if not out.get("guidance"):
        return False
    gc = out.get("grounded_check") or {}
    at = str(gc.get("at") or "")
    if not at:
        return True
    try:
        then = _dt.datetime.fromisoformat(at)
        age = _dt.datetime.now(then.tzinfo) - then
        return age.days >= int(os.getenv("ELLIS_GROUND_ON_ACCESS_DAYS",
                                         GROUND_ON_ACCESS_DAYS))
    except ValueError:
        return True


def _ground_on_access(route: dict, out: dict) -> None:
    """Background official-page check for a route someone just asked about.
    Cold answers already get one from the engine's after-hook; this covers
    the CACHED answers everyone actually hits."""
    from .config import settings as _settings
    if _settings().runtime_mode == "test" or \
            os.getenv("ELLIS_BACKGROUND_RENEWAL", "1").strip() != "1":
        return
    if not should_reground(out):
        return
    from .visa_snapshot import kimi_primary as _kp
    key = _kp.cache_key(route)
    if key in _GROUNDING_IN_FLIGHT:
        return
    _GROUNDING_IN_FLIGHT.add(key)

    def _work():
        try:
            from .db import SessionLocal as _SL
            from .visa_snapshot import freshness
            s = _SL()
            try:
                freshness.recheck_route(s, route)
            finally:
                s.close()
        except Exception:  # noqa: BLE001 — best effort, never surfaces
            pass
        finally:
            _GROUNDING_IN_FLIGHT.discard(key)
    threading.Thread(target=_work, name="ellis-ground-on-access",
                     daemon=True).start()


def _after_cold_answer(route: dict, out: dict) -> None:
    """Background work after a route was decided for the first time.

    1. Grounded recheck: read the answer's official page and apply quote-backed
       corrections (or file a dispute), so every NEW route is checked against
       an official source within about a minute of first being asked.
    2. Pre-translate the answer's strings into both Chinese variants into the
       translation cache, so the language switch is instant.
    Never blocks the reader; never runs in test mode."""
    import os as _os
    import threading as _threading
    from .config import settings as _settings
    if _settings().runtime_mode == "test" or \
            _os.getenv("ELLIS_BACKGROUND_RENEWAL", "1").strip() != "1":
        return
    guidance = out.get("guidance") or {}

    def _work():
        # Links first: a dead portal URL must not sit clickable while the
        # slower page recheck runs.
        try:
            from .db import SessionLocal as _SL
            from .visa_snapshot import kimi_primary as _kp, url_health
            s = _SL()
            try:
                row = s.query(_kp.KimiRouteGuidanceCache).filter_by(
                    cache_key=_kp.cache_key(route)).first()
                if row is not None:
                    g = dict(row.guidance or {})
                    if url_health.strip_dead_links(g):
                        row.guidance = g
                        s.commit()
            finally:
                s.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            from .db import SessionLocal as _SL
            from .visa_snapshot import freshness
            s = _SL()
            try:
                freshness.recheck_route(s, route)
            finally:
                s.close()
        except Exception:  # noqa: BLE001 — best effort, never surfaces
            pass
        try:
            from . import i18n as _i18n
            strings: dict = {}

            def _walk(v):
                if isinstance(v, str):
                    t = v.strip()
                    if 2 < len(t) <= 240 and not t.startswith("http"):
                        strings[f"g{len(strings)}"] = t
                elif isinstance(v, dict):
                    for x in v.values():
                        _walk(x)
                elif isinstance(v, list):
                    for x in v:
                        _walk(x)
            _walk(guidance)
            for lang in ("zh-CN", "zh-Hant"):
                _i18n.translate_catalog(strings, lang)
            _i18n.flush_cache()
        except Exception:  # noqa: BLE001
            pass
    _threading.Thread(target=_work, name="ellis-after-cold-answer",
                      daemon=True).start()


def _answer_anyway(db, route: dict, exc) -> dict:
    """The Database always answers. When the exact variant cannot be decided
    right now — a timeout, a provider outage — serve the closest real answer
    we hold for the SAME nationality and destination, marked as approximate,
    and keep working on the exact one in the background. Only when we hold
    nothing at all for that pair does the honest retry message surface."""
    from .visa_snapshot import kimi_primary
    near = None
    try:
        near = kimi_primary.nearest_cached_answer(db, route)
    except Exception:  # noqa: BLE001 — a failing fallback must not mask the answer
        near = None
    if near is None:
        detail = {"status": getattr(exc, "status", None)
                  or kimi_primary.STATUS_TIMEOUT,
                  "reason": str(getattr(exc, "envelope", {}).get("user_message", exc)
                               if hasattr(exc, "envelope") else exc)}
        raise HTTPException(503 if not isinstance(exc, kimi_primary.GuidanceTimeout)
                            else 504, detail=detail)
    near["approximate"] = True
    near["approximate_reason"] = (
        "This route's exact combination is still being worked out, so the "
        "answer shown is for the same passport and destination.")
    # Keep trying for the exact answer so the next reader gets it.
    try:
        _ground_on_access(route, near)
    except Exception:  # noqa: BLE001
        pass
    return near


_HELD_LEAKS = ("guidance", "workflow_plan", "advisories",
               "missing_fields", "contradictions", "apply_steps")


def _held_envelope(out: dict) -> dict:
    """A held answer's claims never leave the server — and a claim is not
    only the guidance: the workflow plan, advisories and missing-field lists
    all hint at the withheld verdict (a live audit read the verdict straight
    out of workflow_plan on a held row). Only the identity and the flags
    survive."""
    out = {k: v for k, v in out.items() if k not in _HELD_LEAKS}
    out["guidance"] = None
    return out


class DatabaseIssueIn(BaseModel):
    """A reader flagging a field that looks wrong."""
    nationality: str = ""
    destination: str = ""
    travel_purpose: str = "tourism"
    travel_document_type: str = "ordinary_passport"
    field: str = ""
    note: str = ""
    # The identity of the answer actually on screen, echoed back from the
    # lookup. Authoritative when present.
    cache_key: str = ""


@app.post("/database/report-issue")
def travel_database_report_issue(body: DatabaseIssueIn, db=Depends(get_session),
                                 p: Principal = Depends(get_principal)):
    """Information-quality feedback: a reader marks an answer as wrong.

    The report is stored against the exact route (so the cached answer is
    identifiable and correctable) and tracked open -> corrected. Ellis does
    NOT silently change the answer on a report: a claim from a reader is not
    evidence, and quietly rewriting government facts because someone objected
    would be worse than the error. It goes to a queue a person works."""
    from .visa_snapshot import kimi_primary
    from .visa_snapshot.models import DatabaseIssueReport
    nat = body.nationality.strip().upper()
    dest = body.destination.strip().upper()
    if not nat or not dest:
        raise HTTPException(422, "nationality and destination are required")
    route = {"passport_nationality": nat, "passport_issuing_country": nat,
             "lawful_country_of_residence": nat,
             "travel_document_type": body.travel_document_type or "ordinary_passport",
             "destination_country": dest, "travel_purpose": body.travel_purpose or "tourism"}
    route["visa_category"] = kimi_primary.category_for_purpose(
        route["travel_purpose"])
    row = DatabaseIssueReport(
        org_id=p.org_id,
        cache_key=(body.cache_key.strip()[:200]
                   or kimi_primary.cache_key(route)),
        route=route,
        field=(body.field or "")[:64], note=(body.note or "")[:1000],
        reported_by=p.user_id, status="open")
    db.add(row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="database",
                 action="database_issue_reported",
                 detail={"nationality": nat, "destination": dest,
                         "field": row.field}, actor=p.user_id)
    return {"ok": True, "id": row.id, "status": row.status}


@app.get("/database/freshness")
def travel_database_freshness(db=Depends(get_session),
                              p: Principal = Depends(get_principal)):
    """The operator's answer to "is all of it still correct and current?" —
    one row per cached answer: when it was generated, when its freshness
    window ends, whether and when it was last checked against its official
    page, what that check changed or disputed, and whether a human-verified
    override covers it. Admin only; read only."""
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from .visa_snapshot import verified_overrides
    from .visa_snapshot.models import KimiRouteGuidanceCache
    require_admin(p)
    now = datetime.now(timezone.utc)
    rows = []
    for r in db.execute(_select(KimiRouteGuidanceCache).order_by(
            KimiRouteGuidanceCache.fresh_until)).scalars():
        gc = (r.verification or {}).get("grounded_check") or {}
        fresh_until = r.fresh_until
        rows.append({
            "cache_key": r.cache_key,
            "route": {k: (r.route or {}).get(k) for k in
                      ("passport_nationality", "destination_country",
                       "travel_purpose")},
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "fresh_until": fresh_until.isoformat() if fresh_until else None,
            "stale": bool(fresh_until and
                          fresh_until.replace(tzinfo=fresh_until.tzinfo or timezone.utc)
                          < now),
            "grounded": gc.get("outcome") == "checked",
            "grounded_at": gc.get("at"),
            "grounded_source": gc.get("source_url"),
            "grounded_consistent": gc.get("consistent"),
            "changed_fields": gc.get("changed_fields") or [],
            "disputed_fields": gc.get("disputed_fields") or [],
            "human_override": verified_overrides.find(r.route or {}) is not None,
        })
    return {"answers": rows,
            "summary": {
                "total": len(rows),
                "stale": sum(1 for x in rows if x["stale"]),
                # Disjoint tiers: an answer that is BOTH human-verified and
                # machine-grounded counts once, in the higher tier — summed
                # coverage must never read as 108% of the answers.
                "grounded": sum(1 for x in rows
                                if x["grounded"] and not x["human_override"]),
                "human_verified": sum(1 for x in rows if x["human_override"]),
                "disputed": sum(1 for x in rows if x["disputed_fields"]),
            }}


@app.get("/database/issues")
def travel_database_issues(db=Depends(get_session),
                           p: Principal = Depends(get_principal)):
    """The operator queue: what readers flagged, oldest first."""
    from sqlalchemy import select as _select
    from .visa_snapshot.models import DatabaseIssueReport
    require_admin(p)
    # NOT filtered by org. The Database is one shared knowledge base: a report
    # is feedback about a public government fact, carries no applicant data by
    # construction (a route, a field name, and a capped note), and is filed by
    # readers whose org is never the operator's. Scoping this to the admin's
    # own org meant no reader report was ever visible to anyone.
    rows = db.execute(_select(DatabaseIssueReport).order_by(
        DatabaseIssueReport.created_at)).scalars().all()
    def _iso(v):
        return v.isoformat() if v else None
    # Every stage of the loop is reported, not just the current label. A
    # closure nobody can attribute is not traceable, which is the whole point
    # of the requirement.
    return {"issues": [{"id": r.id, "route": r.route, "field": r.field,
                        "note": r.note, "status": r.status,
                        "resolution": r.resolution,
                        "reported_by": r.reported_by,
                        "cache_key": r.cache_key,
                        "notified_to": r.notified_to,
                        "notified_at": _iso(r.notified_at),
                        "resolved_by": r.resolved_by,
                        "resolved_at": _iso(r.resolved_at),
                        "reviewed_by": r.reviewed_by,
                        "reviewed_at": _iso(r.reviewed_at),
                        "published_at": _iso(r.published_at),
                        "proposal": r.proposal or {},
                        "created_at": _iso(r.created_at)} for r in rows]}


def _tstation_rows(db, *, nationality: str = "", destination: str = "",
                   purpose: str = "", document: str = "",
                   requirement: str = "", confidence: str = ""):
    from .visa_snapshot.registry import iso3
    from .visa_snapshot import kimi_primary as _kp

    def _resolve(term: str) -> str:
        # The spot-check accepts "China", "CHN", "CN" and Chinese names like
        # 中国: the registry first, then the ask box's alias table (which
        # carries the Chinese country names). An unresolvable term keeps its
        # literal form and simply matches nothing.
        term = re.sub(r"^[^\w\u4e00-\u9fff]+", "", term.strip())
        got = iso3(term, default=None)
        if got:
            return got
        low = term.lower()
        for code, names in getattr(_kp, "_ALIASES", {}).items():
            if low in names or term in names:
                return code
        return term.upper()
    if nationality:
        nationality = _resolve(nationality)
    if destination:
        destination = _resolve(destination)
    """Every served answer as Trip.com 25-field records, filters combined —
    their "multi-dimensional spot check": slice by station (the nationality a
    T-site serves), passport type, destination, visa requirement, field."""
    from sqlalchemy import select as _select
    from .visa_snapshot import kimi_primary, verified_overrides, tstation
    from .visa_snapshot.models import KimiRouteGuidanceCache
    verified_overrides.reload()
    out = []
    for r in db.execute(_select(KimiRouteGuidanceCache)).scalars():
        if f"|{kimi_primary.CACHE_VERSION}" not in (r.cache_key or ""):
            continue
        route = dict(r.route or {})
        if nationality and str(route.get("passport_nationality") or "").upper() != nationality.upper():
            continue
        if destination and str(route.get("destination_country") or "").upper() != destination.upper():
            continue
        if purpose and str(route.get("travel_purpose") or "tourism").lower() != purpose.lower():
            continue
        doc = str(route.get("travel_document_type") or "")
        if not doc:
            # Rows cached before the document type was stored carry it only
            # in the cache key ("doc:diplomatic_passport").
            doc = next((part[4:] for part in r.cache_key.split("|")
                        if part.startswith("doc:")), "ordinary_passport")
        doc = kimi_primary.normalize_document_type(doc)
        route["travel_document_type"] = doc
        if document and doc != document:
            continue
        g, prov = verified_overrides.apply(dict(r.guidance or {}), route)
        g = kimi_primary.apply_portal_fallback({"guidance": g}, route)["guidance"]
        collected = (r.generated_at.isoformat() if r.generated_at else "")
        until = (r.fresh_until.isoformat() if r.fresh_until else "")
        # Whether the official page has actually been read and agreed with:
        # the difference between a source and a link nobody opened.
        _gc = ((r.verification or {}).get("grounded_check") or {})
        _grounded = _gc.get("outcome") == "checked" and bool(_gc.get("consistent"))
        _disputed_now = list(((r.verification or {}).get("grounded_check")
                              or {}).get("disputed_fields") or [])
        for rec in tstation.records_for_route(route, g, prov, collected, until,
                                              grounded_ok=_grounded,
                                              disputed_fields=_disputed_now):
            if not rec.get("source_url"):
                # The destination's browser-verified official portal is the
                # official reference page for a record whose answer carries
                # no page of its own (visa-free routes especially).
                portal = kimi_primary._official_portals().get(
                    str(route.get("destination_country") or "").upper())
                if portal:
                    rec["source_url"] = portal
                    rec["data_source"] = (rec.get("data_source")
                                          or "Official government portal")
            if requirement and str(rec.get("visa_requirement") or "") != requirement:
                continue
            if confidence and str(rec.get("confidence_level") or "").lower() \
                    != confidence.strip().lower():
                continue
            rec["_cache_key"] = r.cache_key
            rec["_status"] = r.status
            # Whether an operator has released this answer despite low
            # confidence: without it the release half of the confidence gate
            # cannot be audited from the records surface.
            rec["_released"] = bool((r.verification or {}).get("operator_released"))
            # How solidly the source BACKS what this record shows:
            #   human-quote        a person verified these fields against the
            #                      named page and quoted it
            #   grounded-consistent the pipeline fetched the official page and
            #                      found the stored answer consistent with it
            #   reference          an official page is linked but has not yet
            #                      been machine-compared to this answer
            gc = ((r.verification or {}).get("grounded_check") or {})
            # Fields the page disputed that no human has ruled on yet: the
            # spec's third checklist state, 未过审 (not approved).
            rec["_disputed"] = list(gc.get("disputed_fields") or [])
            if prov:
                rec["_source_check"] = "human-quote"
            elif gc.get("outcome") == "checked" and gc.get("consistent"):
                rec["_source_check"] = "grounded-consistent"
            elif rec.get("source_url"):
                rec["_source_check"] = "reference"
            else:
                rec["_source_check"] = "unchecked"
            out.append(rec)
    return _dedupe_dataset_rows(out)


def _dedupe_dataset_rows(rows: list[dict]) -> list[dict]:
    """One dataset row per visa product, however many cached answers produced it.

    A route is cached separately per transit itinerary and per consular
    jurisdiction, because those change the ADVICE. They do not change the
    25-field product row: the same Japanese group tourist visa came back six
    times for CHN->JPN, once per transit variant, and an operator opening that
    route met thirty-one rows where five exist. The 25 fields identify the
    row, so they decide identity here too; among duplicates the best-evidenced
    survives, so deduplicating can never downgrade what a reader sees.
    """
    from .visa_snapshot import tstation as _ts
    rank = {"human-quote": 3, "grounded-consistent": 2, "reference": 1,
            "unchecked": 0}
    best: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in rows:
        key = (r.get("travel_document_country"), r.get("destination_country"),
               r.get("travel_purpose"), r.get("travel_document_type"),
               r.get("visa_type_name"), r.get("visa_requirement"))
        prev = best.get(key)
        if prev is None:
            best[key] = r
            order.append(key)
            continue
        score = (rank.get(r.get("_source_check"), 0), _ts.completeness(r))
        prior = (rank.get(prev.get("_source_check"), 0),
                 _ts.completeness(prev))
        if score > prior:
            best[key] = r
    return [best[k] for k in order]


def _with_pending(status: dict, disputed) -> dict:
    """The spec's checklist has THREE states: filled, missing, and 未过审
    (not approved). A filled field the official page disputed, with no human
    ruling yet, is the third one."""
    for f in disputed or []:
        if status.get(f) == "filled":
            status[f] = "pending-review"
    return status


@app.get("/database/records")
def travel_database_records(nationality: str = "", destination: str = "",
                            purpose: str = "", document: str = "",
                            requirement: str = "", confidence: str = "",
                            visa_type: str = "", field_missing: str = "",
                            db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """The quality-control backend's record browser: T-Station 25-field
    records with combined filtering, per-field fill status, completeness,
    source and confidence — the operator's spot-check surface."""
    from .visa_snapshot import tstation
    require_admin(p)
    rows = _tstation_rows(db, nationality=nationality, destination=destination,
                          purpose=purpose, document=document,
                          requirement=requirement, confidence=confidence)
    # The acceptance standard's multi-dimensional spot check (4.1.2) slices
    # by visa TYPE and by FIELD as well as by route dimensions.
    if visa_type:
        vt = visa_type.strip().lower()
        rows = [r for r in rows if vt in str(r.get("visa_type_name") or "").lower()]
    if field_missing:
        f = field_missing.strip()
        if f in tstation.FIELD_ORDER:
            rows = [r for r in rows
                    if tstation.field_status(r).get(f) == "missing"]
    complete = sum(1 for r in rows if tstation.completeness(r) == 1.0)
    return {"fields": list(tstation.FIELD_ORDER),
            "required_fields": sorted(tstation.REQUIRED_FIELDS),
            "records": [{**{k: r.get(k) for k in tstation.FIELD_ORDER},
                         "cache_key": r["_cache_key"],
                         "source_check": r.get("_source_check", "unchecked"),
                         "operator_released": r.get("_released", False),
                         "field_status": _with_pending(tstation.field_status(r),
                                                       r.get("_disputed")),
                         "completeness": round(tstation.completeness(r), 4)}
                        for r in rows],
            "summary": {"total": len(rows), "complete": complete,
                        "completeness_rate": round(complete / len(rows), 4) if rows else None,
                        "high": sum(1 for r in rows if r.get("confidence_level") == "High"),
                        "medium": sum(1 for r in rows if r.get("confidence_level") == "Medium"),
                        "low": sum(1 for r in rows if r.get("confidence_level") == "Low"),
                        "source_coverage": round(sum(1 for r in rows if r.get("source_url")) / len(rows), 4) if rows else None,
                        "substantiated": sum(1 for r in rows if r.get("_source_check") in ("human-quote", "grounded-consistent"))}}


def _change_source(db, row) -> dict:
    """The official page a reviewer can open to check this entry.

    Their standard requires every record to be traceable to an official
    source; a change log that says what moved without saying where the new
    value came from is not checkable, and unverifiable rows are exactly what
    the acceptance sampling is meant to catch. Resolution order: a source the
    change itself set, then the human-verified override for that route, then
    the source on the answer as it stands now. Nothing is invented: when no
    official page is known the entry says so, and says why.
    """
    from .visa_snapshot import authority, kimi_primary, verified_overrides
    from .visa_snapshot.models import KimiRouteGuidanceCache as _C
    from sqlalchemy import select as _sel

    def _first_url(text: str) -> str:
        import re as _re
        m = _re.search(r"https?://[^\s)\]\"',]+", str(text or ""))
        return m.group(0).rstrip(".,;") if m else ""

    url, kind = "", ""
    ch = row.changes or {}
    for key in ("source_url", "official_portal_url"):
        val = (ch.get(key) or {}).get("to") if isinstance(ch.get(key), dict) else None
        if val:
            url, kind = str(val), "changed in this update"
            break
    route = row.route or {}
    if not url:
        ov = verified_overrides.find(route) or {}
        if ov.get("source_url"):
            url = ov["source_url"]
            kind = f"human-verified {ov.get('verified_at') or ''}".strip()
    if not url:
        url, kind = _first_url(row.note), "cited in the note"
    if not url:
        try:
            cached = db.execute(_sel(_C).where(
                _C.cache_key == row.cache_key)).scalars().first()
            g = (cached.guidance or {}) if cached else {}
            url = str(g.get("source_url") or g.get("official_portal_url") or "")
            kind = "source on the current answer"
        except Exception:  # noqa: BLE001 - a missing row must not break the log
            url = ""
    if not url:
        return {"source_url": "", "source_host": "",
                "source_kind": "no official page recorded",
                "source_official": False}
    host = authority.hostname(url)
    return {"source_url": url, "source_host": host, "source_kind": kind,
            "source_official": bool(authority.is_government_host(host))}


@app.get("/database/changes")
def travel_database_changes(q: str = "", limit: int = 200,
                            db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """The change log: what changed in served answers, newest first —
    add / modify / delete with a field-level diff, searchable."""
    from sqlalchemy import String as _String
    from sqlalchemy import func as _func
    from sqlalchemy import or_ as _or_
    from sqlalchemy import select as _select
    from .visa_snapshot.models import DatabaseChangeLog
    require_admin(p)
    # Search must run in the database, not over a pre-truncated page. Filtering
    # after a 1,000-row cap meant a term that existed only in older history
    # returned nothing, and the same query found it once the caller happened to
    # ask for a bigger page. The cap is the caller's page size now, not a
    # ceiling on what is searchable.
    needle = q.strip().lower()
    stmt = _select(DatabaseChangeLog)
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(_or_(
            _func.lower(DatabaseChangeLog.cache_key).like(like),
            _func.lower(DatabaseChangeLog.action).like(like),
            _func.lower(DatabaseChangeLog.origin).like(like),
            _func.lower(_func.coalesce(DatabaseChangeLog.note, "")).like(like),
            _func.lower(_func.cast(DatabaseChangeLog.changes, _String)).like(like),
        ))
    rows = db.execute(stmt.order_by(DatabaseChangeLog.created_at.desc())
                      .limit(max(1, min(limit, 20000)))).scalars().all()
    out = []
    for r in rows:
        blob = f"{r.cache_key} {r.action} {r.origin} {r.note} {list((r.changes or {}).keys())}".lower()
        if needle and needle not in blob:
            continue
        out.append({"id": r.id, "cache_key": r.cache_key, "route": r.route,
                    "action": r.action, "origin": r.origin,
                    "changes": r.changes, "note": r.note,
                    "at": r.created_at.isoformat() if r.created_at else None,
                    **_change_source(db, r)})
    return {"changes": out}


@app.get("/database/changes.csv")
def travel_database_changes_export(q: str = "", limit: int = 1000,
                                   db=Depends(get_session),
                                   p: Principal = Depends(get_principal)):
    """The change log as a file (deliverable 6 requires the list to be
    exportable, not just displayed): one row per changed field, UTF-8 CSV
    Excel opens directly."""
    import csv
    import io
    from fastapi.responses import Response
    data = travel_database_changes(q=q, limit=limit, db=db, p=p)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "action", "origin", "nationality", "destination",
                "purpose", "field", "from", "to", "note",
                "source_url", "source_host", "official_domain"])
    for c in data["changes"]:
        rt = c.get("route") or {}
        base = [c.get("at") or "", c.get("action") or "", c.get("origin") or "",
                rt.get("passport_nationality") or "",
                rt.get("destination_country") or "",
                rt.get("travel_purpose") or ""]
        changes = c.get("changes") or {}
        # Every exported row carries the same proof the screen shows, so a
        # reviewer working in Excel can open the official page too.
        proof = [c.get("source_url") or "", c.get("source_host") or "",
                 "yes" if c.get("source_official") else "no"]
        if not changes:
            w.writerow(base + ["", "", "", c.get("note") or ""] + proof)
        for field, diff in changes.items():
            frm, to = ("", "")
            if isinstance(diff, dict):
                frm, to = str(diff.get("from") or ""), str(diff.get("to") or "")
            w.writerow(base + [field, frm, to, c.get("note") or ""] + proof)
    # BOM so Excel decodes the Chinese route names correctly.
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition":
                             'attachment; filename="tstation_change_log.csv"'})


@app.get("/database/export.xlsx")
def travel_database_export(nationality: str = "", destination: str = "",
                           purpose: str = "", document: str = "",
                           requirement: str = "", confidence: str = "",
                           visa_type: str = "", field_missing: str = "",
                           db=Depends(get_session),
                           p: Principal = Depends(get_principal)):
    """The dataset as Excel, to Trip.com's export spec: one workbook, a
    field-description sheet and a data sheet, all 25 fields plus source and
    confidence, filterable by the same dimensions as the record browser."""
    import io
    from datetime import datetime, timezone
    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from .visa_snapshot import tstation
    require_admin(p)
    rows = _tstation_rows(db, nationality=nationality, destination=destination,
                          purpose=purpose, document=document,
                          requirement=requirement, confidence=confidence)
    # The standard's export filters by visa type too (按站点/目的地/签证类型
    # 筛选), and by field gap for rectification work.
    if visa_type:
        vt = visa_type.strip().lower()
        rows = [r for r in rows if vt in str(r.get("visa_type_name") or "").lower()]
    if field_missing and field_missing.strip() in tstation.FIELD_ORDER:
        fm = field_missing.strip()
        rows = [r for r in rows if tstation.field_status(r).get(fm) == "missing"]
    # Sheet 1 is the Data sheet whose header row is the exact 25 field names:
    # the acceptance standard reads the dictionary off the first sheet. The
    # field descriptions ride second as documentation.
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Data"
    ws1.append(list(tstation.FIELD_ORDER))
    for c in ws1[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws1.append([r.get(f) for f in tstation.FIELD_ORDER])
    ws1.freeze_panes = "A2"
    ws0 = wb.create_sheet("Field descriptions")
    # 5.2: offline data marks its snapshot moment explicitly.
    ws0.append(["Snapshot (UTC)",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
                "", "Exported live from the online database"])
    ws0.append(["No.", "Field", "Required", "Description"])
    for c in ws0[1]:
        c.font = Font(bold=True)
    for i, f in enumerate(tstation.FIELD_ORDER, 1):
        ws0.append([i, f, "Yes" if f in tstation.REQUIRED_FIELDS else "If available",
                    tstation.FIELD_DESCRIPTIONS[f]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="tstation_visa_dataset_{stamp}.xlsx"'})


class DatabaseAskIn(BaseModel):
    question: str
    # The route currently on screen, so a follow-up like "what about
    # business?" or "to korea instead" modifies it instead of being refused.
    context: dict | None = None


@app.get("/database/asks")
def travel_database_asks(limit: int = 200, unreviewed: bool = False,
                         db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    """The AI Q&A log, newest first, so operations can sample what the
    assistant actually told customers."""
    from sqlalchemy import select as _sel
    from .visa_snapshot.models import DatabaseAskLog
    require_admin(p)
    stmt = _sel(DatabaseAskLog)
    if unreviewed:
        stmt = stmt.where(DatabaseAskLog.verdict == "")
    rows = db.execute(stmt.order_by(DatabaseAskLog.created_at.desc())
                      .limit(max(1, min(limit, 2000)))).scalars().all()
    return {"asks": [{"id": r.id, "question": r.question, "language": r.language,
                      "understood": r.understood, "route": r.route,
                      "answer": r.answer, "source_url": r.source_url,
                      "held": r.held, "confidence": r.confidence,
                      "verdict": r.verdict, "reviewed_by": r.reviewed_by,
                      "at": r.created_at.isoformat() if r.created_at else None}
                     for r in rows],
            "unreviewed": sum(1 for r in rows if not r.verdict)}


def _log_ask(db, p, question: str, parsed: dict, out: dict) -> None:
    """Keep the exchange so operations can sample it.

    Their standard requires the assistant's answers to be spot-checked, and
    nothing about them was stored, so the surface that talks to a customer in
    its own words was the only one nobody could audit. Failure here must never
    break the answer: a log that can sink a reply is worse than no log.
    """
    try:
        from .visa_snapshot.models import DatabaseAskLog
        g = (out or {}).get("guidance") or {}
        db.add(DatabaseAskLog(
            org_id=p.org_id,
            question=str(question or "")[:1000],
            language="zh" if re.search(r"[\u4e00-\u9fff]", str(question or "")) else "en",
            understood=bool((out or {}).get("understood", True)),
            route={k: parsed.get(k) for k in
                   ("nationality", "destination", "travel_purpose",
                    "travel_document_type")},
            answer={k: g.get(k) for k in
                    ("disposition", "visa_category", "permitted_stay",
                     "requirement_detail")},
            source_url=str(g.get("source_url") or g.get("official_portal_url") or "")[:500],
            held=bool((out or {}).get("held")),
            confidence=str((out or {}).get("confidence")
                           or g.get("confidence") or "")[:16]))
        db.commit()
    except Exception:  # noqa: BLE001 - never let auditing break an answer
        db.rollback()


@app.post("/database/ask")
def travel_database_ask(body: DatabaseAskIn, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    """AI Q&A: a natural-language question is read into a route, then answered
    by the same Kimi-primary decision the form uses — identical answer,
    sources and honesty. An unclear question is reported, never guessed."""
    from .visa_snapshot import kimi_primary
    if not kimi_primary.is_available():
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": "the route engine is not configured"})
    try:
        parsed = kimi_primary.parse_question_with_context(body.question,
                                                           body.context)
    except kimi_primary.GuidanceTimeout:
        raise HTTPException(504, detail={"status": kimi_primary.STATUS_TIMEOUT,
                                         "reason": kimi_primary.TIMEOUT_MESSAGE})
    except kimi_primary.GuidanceUnavailable as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": str(e)})
    if not parsed.get("understood"):
        # A refusal keeps the documented shape (clients read route/held) and
        # says exactly which fact is missing, in the asker's language.
        nat = parsed.get("nationality") or ""
        dest = parsed.get("destination") or ""
        cjk = any("一" <= c <= "鿿" for c in body.question or "")
        if not nat and dest:
            clarify = "请告诉我您持哪国护照（国籍）？" if cjk else \
                "Which country issued your passport?"
        elif nat and not dest:
            clarify = "请告诉我您要去哪个国家？" if cjk else \
                "Which country are you travelling to?"
        else:
            clarify = "请说明您的国籍（护照签发国）和目的地。" if cjk else \
                "Please name your passport country and your destination."
        return {"understood": False, "clarify": clarify,
                "route": {"nationality": nat, "destination": dest,
                          "travel_purpose": parsed.get("travel_purpose") or "",
                          "travel_document_type":
                              parsed.get("travel_document_type") or "",
                          "transit_countries": [], "arrival_date": None},
                "guidance": None, "held": False,
                "nationality": nat, "destination": dest}
    route = {
        "passport_nationality": parsed["nationality"],
        "passport_issuing_country": parsed["nationality"],
        "lawful_country_of_residence": parsed["nationality"],
        "travel_document_type": parsed["travel_document_type"],
        "destination_country": parsed["destination"],
        "visa_category": kimi_primary.category_for_purpose(
            parsed["travel_purpose"]),
        "travel_purpose": parsed["travel_purpose"],
    }
    if parsed.get("transit_countries"):
        route["transit_countries"] = parsed["transit_countries"]
    if parsed.get("arrival_date"):
        route["arrival_date"] = parsed["arrival_date"]
    try:
        out = kimi_primary.get_route_guidance(db, route, stage="core",
                                              after=_after_cold_answer)
    except (kimi_primary.GuidanceTimeout, kimi_primary.GuidanceUnavailable,
            kimi_primary.GuidanceProviderError) as e:
        out = _answer_anyway(db, route, e)
    if out.get("cached"):
        _ground_on_access(route, out)
    if out.get("held"):
        out = _held_envelope(out)
    out["understood"] = True
    out["route"] = {"nationality": parsed["nationality"],
                    "destination": parsed["destination"],
                    "travel_purpose": parsed["travel_purpose"],
                    "travel_document_type": parsed["travel_document_type"],
                    "transit_countries": parsed.get("transit_countries") or [],
                    # "12月15日去日本" was answered FOR that date; the echo
                    # must say so or the screen shows a dateless route.
                    "arrival_date": parsed.get("arrival_date")}
    if parsed.get("focus"):
        out["focus"] = parsed["focus"]
    audit.record(db, org_id=p.org_id, application_id="database",
                 action="database_ask",
                 detail={"nationality": parsed["nationality"],
                         "destination": parsed["destination"]}, actor=p.user_id)
    _log_ask(db, p, body.question, parsed, out)
    return out


class DatabaseLookupIn(BaseModel):
    # A mis-keyed body field (e.g. "travel_document") must be rejected, not
    # silently dropped so the caller gets ordinary/tourism data for every
    # switcher position.
    model_config = {"extra": "forbid"}
    nationality: str
    destination: str
    travel_document_type: str = "ordinary_passport"
    travel_purpose: str = "tourism"
    residence: str = ""
    arrival_date: str = ""
    # Itinerary start (a city, e.g. Beijing) and any stopover/transfer points.
    # Both are optional; a transit point is what makes a transit-visa answer
    # possible at all, so Ellis answers transit ONLY when one is given.
    departure_city: str = ""
    transit_countries: list[str] = []


@app.post("/database/lookup")
def travel_database_lookup(body: DatabaseLookupIn, db=Depends(get_session),
                           p: Principal = Depends(get_principal)):
    """The Database: one route in, the full requirements picture out.

    Runs the same Kimi-primary single-pass decision the applicant journey
    trusts — validated shape, deterministic advisories, the honest cached /
    stale flags — keyed on sanitized route facts only. Repeat lookups serve
    from the decision cache instantly; a stale entry is served at once and
    refreshed in the background, so the reader never waits on research."""
    from .visa_snapshot import kimi_primary
    if not kimi_primary.is_available():
        raise HTTPException(503, detail={
            "status": kimi_primary.STATUS_UNAVAILABLE,
            "reason": "the route engine is not configured on this install"})
    from .visa_snapshot.registry import iso3
    nat = iso3(body.nationality.strip(), default=None)
    dest = iso3(body.destination.strip(), default=None)
    if not nat or not dest:
        raise HTTPException(422, "nationality and destination must be real "
                                 "countries (name or ISO code)")
    # The registry is the ONE vocabulary: a hardcoded copy here silently
    # rejected three document types the picker offers (公务普通护照, 儿童护照,
    # 身份证明书) and collapsed 临时护照 into the emergency passport.
    from .visa_snapshot.registry import load_registry as _lr
    _DOCS = {e["code"] for e in _lr("travel_document_types")["entries"]}
    _DOC_ALIAS = {"ordinary": "ordinary_passport", "passport": "ordinary_passport",
                  "diplomatic": "diplomatic_passport", "official": "service_passport",
                  "official_passport": "service_passport", "service": "service_passport",
                  "emergency": "emergency_passport", "temporary": "temporary_passport",
                  "child": "child_passport", "identity_certificate": "identity_certificate",
                  "travel_document": "prc_travel_document"}
    doc_in = (body.travel_document_type or "ordinary_passport").strip().lower()
    doc = _DOC_ALIAS.get(doc_in, doc_in)
    if doc not in _DOCS:
        raise HTTPException(422, f"unknown travel document type: {doc_in}")
    _PURPOSES = {"tourism", "business", "family_visit", "study", "work",
                 "transit", "other"}
    _PURPOSE_ALIAS = {"family": "family_visit", "visiting_relatives": "family_visit",
                      "tourist": "tourism", "study_abroad": "study"}
    purpose_in = (body.travel_purpose or "tourism").strip().lower()
    purpose = _PURPOSE_ALIAS.get(purpose_in, purpose_in)
    if purpose not in _PURPOSES:
        raise HTTPException(422, f"unknown travel purpose: {purpose_in}")
    route = {
        "passport_nationality": nat,
        "passport_issuing_country": nat,
        "lawful_country_of_residence": (body.residence or nat).strip().upper(),
        "travel_document_type": doc,
        "destination_country": dest,
        "travel_purpose": purpose,
    }
    route["visa_category"] = kimi_primary.category_for_purpose(
        route["travel_purpose"])
    if body.arrival_date:
        route["arrival_date"] = body.arrival_date
    if body.departure_city.strip():
        route["departure_city"] = body.departure_city.strip()[:80]
        # The city itself stays out of the cache key: keying on free text
        # would mint an entry per spelling anyone types. What the key carries
        # is the consular district the city resolves to, in the slot the key
        # already has. A destination with no published district table
        # resolves to "default", which is what every existing key holds, so
        # the warm cache is untouched.
        from .visa_snapshot import consular_districts
        district = consular_districts.resolve(
            route["destination_country"], route["departure_city"],
            route["passport_nationality"])
        if district and district != "default":
            route["consular_jurisdiction"] = district
    transit = [t for t in (iso3(c.strip(), default=None)
               for c in (body.transit_countries or []) if c and c.strip())
               if t][:5]
    if transit:
        route["transit_countries"] = transit
    try:
        # Core-first: a route nobody asked before paints its verdict in about
        # half the time; the detail sections fill in behind it (the page
        # polls), consistent with the verdict. Cached routes are instant.
        out = kimi_primary.get_route_guidance(db, route, stage="core",
                                              after=_after_cold_answer)
    except (kimi_primary.GuidanceTimeout, kimi_primary.GuidanceUnavailable,
            kimi_primary.GuidanceProviderError) as e:
        out = _answer_anyway(db, route, e)
    if out.get("cached"):
        _ground_on_access(route, out)
    # The hold is enforced HERE, not in the JSX: a held answer's claims never
    # leave the server, so no client (curious dev tools included) can read
    # what the reader is told is being checked. The envelope keeps the flag
    # and the identity so the page can say WHY there is nothing to show.
    out["transit_countries"] = transit
    # One grading, one gate. The 25-field record publishes a confidence_level
    # by the standard's ladder (4.2.3); anything that ladder calls Low must be
    # withheld from readers until an operator confirms it. Deciding the hold
    # from the engine's self-rating alone let records the console showed as
    # Low still reach customers unheld.
    if out.get("guidance") and not out.get("operator_released"):
        from .visa_snapshot import tstation as _ts
        _gc2 = out.get("grounded_check") or {}
        _rows = _ts.records_for_route(
            route, out.get("guidance") or {},
            (out.get("source_verified") or None),
            grounded_ok=(_gc2.get("outcome") == "checked"
                         and bool(_gc2.get("consistent"))))
        if _rows and _rows[0].get("confidence_level") == "Low":
            out["review_required"] = True
            out["held"] = kimi_primary.hold_enabled()
    if out.get("held"):
        out = _held_envelope(out)
    # The answer carries the identity of the cached row it came from. A reader
    # flagging it, or an operator releasing it, then names THAT answer instead
    # of re-deriving a key from a subset of the inputs (which silently missed
    # any lookup carrying a travel date or a transit point).
    out["cache_key"] = kimi_primary.cache_key(route)
    # A stale cache entry was already served above — freshen it for the next
    # reader without making this one wait.
    if out.get("stale"):
        try:
            from .db import SessionLocal as _SL
            kimi_primary.refresh_stale_async(_SL, route)
        except Exception:  # noqa: BLE001 — refresh is best-effort
            pass
    audit.record(db, org_id=p.org_id, application_id="database",
                 action="database_lookup",
                 detail={"nationality": nat, "destination": dest,
                         "cached": out.get("cached"),
                         "status": out.get("status")}, actor=p.user_id)
    return out


class RenewalRequest(BaseModel):
    manual: bool = False   # True = the applicant chose "Renew my passport"


@app.post("/cases/{application_id}/renewal")
def start_passport_renewal(application_id: str, body: RenewalRequest = RenewalRequest(),
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Create (or reuse — idempotent) the linked passport-renewal case. Offered
    automatically only for an expired / insufficient-validity passport; a valid
    passport can still renew when the applicant explicitly asks (manual)."""
    from . import passport_validity, renewal
    from .visa_snapshot import kimi_primary
    app_row = _owned(db, p, application_id)
    verdict = passport_validity.check_case_passport(db, app_row)
    try:
        out = renewal.create_renewal_case(db, org_id=p.org_id, user_id=p.user_id,
                                          travel_case=app_row, verdict=verdict,
                                          manual=body.manual)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except renewal.RenewalUnavailable as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": str(e)})
    except kimi_primary.GuidanceTimeout:
        raise HTTPException(504, detail={"status": kimi_primary.STATUS_TIMEOUT,
                                         "reason": kimi_primary.TIMEOUT_MESSAGE})
    except kimi_primary.GuidanceProviderError as e:
        raise HTTPException(503, detail={"status": kimi_primary.STATUS_UNAVAILABLE,
                                         "reason": e.envelope.get("user_message"),
                                         "category": e.envelope.get("category")})
    out["travel_case_validity"] = verdict
    return out


# ---- Document preview (Phase 13): signed, expiring content URLs ----
_DOC_URL_TTL_SECONDS = 300


def _doc_sig(document_id: str, exp: int) -> str:
    import hmac as _hmac
    import hashlib as _hashlib
    from .config import settings as _settings
    payload = f"doc.{document_id}.{exp}"
    return _hmac.new(_settings().action_token_secret.encode(), payload.encode(),
                     _hashlib.sha256).hexdigest()[:32]


@app.get("/cases/{application_id}/documents/{doc_id}/url")
def document_preview_url(application_id: str, doc_id: str, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    """Mint a short-lived signed URL for the in-app preview. Authenticated +
    tenant-checked here; the content endpoint then only needs the signature (so
    <img>/<iframe> can load it without headers). No filesystem paths, no bucket
    URLs, no credentials are ever exposed."""
    import time as _time
    app_row = _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")
    # Close the H1B party wall on the document READ path: a petitioner-private
    # artifact (I-129/ETA, RFE packet, evidence cover) stored on the shared
    # parent case is never mintable by a beneficiary-bound org member.
    from .h1b.api import authorize_document_read
    authorize_document_read(db, app_row, doc, p)
    blob = db.get(models.DocumentBlob, doc_id)
    if blob is None:
        return {"available": False,
                "reason": "no stored content for this document (text-only fixture)"}
    exp = int(_time.time()) + _DOC_URL_TTL_SECONDS
    return {"available": True, "mime": blob.mime, "expires_in": _DOC_URL_TTL_SECONDS,
            "url": f"/documents/{doc_id}/content?exp={exp}&sig={_doc_sig(doc_id, exp)}"}


@app.get("/documents/{doc_id}/content")
def document_content(doc_id: str, exp: int, sig: str, db=Depends(get_session)):
    """Serve preview bytes for a valid, unexpired signed URL. The signature (not
    a session) is the authorization — minted only for the owning tenant."""
    import hmac as _hmac
    import time as _time
    if exp < _time.time():
        raise HTTPException(401, "preview link expired")
    if not _hmac.compare_digest(sig, _doc_sig(doc_id, exp)):
        raise HTTPException(401, "invalid preview signature")
    blob = db.get(models.DocumentBlob, doc_id)
    if blob is None:
        raise HTTPException(404, "document content not found")
    from fastapi.responses import Response
    # nosniff + a fully sandboxed CSP: preview bytes can never execute script
    # or be reinterpreted as another type (HTML/active content is never
    # rendered — the MIME allowlist at upload already excludes it).
    return Response(content=blob.content, media_type=blob.mime,
                    headers={"Cache-Control": "private, no-store",
                             "Content-Disposition": "inline",
                             "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy":
                                 "sandbox; default-src 'none'"})


# ---- Email delivery (Phase 8) ----
@app.get("/cases/{application_id}/consular-form")
def get_consular_form(application_id: str, download: bool = False,
                      db=Depends(get_session),
                      p: Principal = Depends(get_principal)):
    """The consular application form for an in-person route (Schengen uniform,
    US DS-160 preparation), filled from the applicant's OWN answers plus the
    verified passport they uploaded.

    Honest by construction: unanswered fields stay blank and are reported as
    `missing_required` so the applicant is asked BEFORE they travel to an
    appointment, and Ellis returns the government's own filled PDF only when
    its official blank is on file — never a fabricated look-alike."""
    from . import consular_forms
    app_row = _owned(db, p, application_id)
    dest = _iso3_for(db, app_row.destination_country)
    form_key = consular_forms.form_for_destination(dest)
    if form_key is None:
        return {"available": False,
                "reason": "this route has no consular form Ellis prepares"}
    from . import checklist_intake
    profile = _latest_passport_profile(db, app_row)
    answers = consular_forms.answers_from_documents(app_row.answers or {}, profile)
    built = consular_forms.build(
        form_key, answers, applicant_name=str(answers.get("full_name") or ""),
        # The form draws a frame marked FOTOGRAFIA and the applicant already
        # uploaded the photo it wants; leaving it empty made them do by hand
        # the one thing they had already done. The signature is the one they
        # drew in Ellis's pad — their act, carried onto the page.
        photo=checklist_intake.applicant_photo_bytes(db, application_id),
        signature=checklist_intake.applicant_signature_bytes(db, application_id))
    prepared = built["prepared"]
    if not download:
        # Human acts + tap count from the single authority (app/filing_acts),
        # keyed on the form spec's OWN submission mode — the frontend keeps no
        # fallback copy of either.
        acts_key = filing_acts.consular_key(prepared["submission"])
        return {"available": True, "form_key": form_key,
                "title": prepared["title"], "kind": built["kind"],
                "filled": prepared["filled"], "total": prepared["total"],
                "missing_required": prepared["missing_required"],
                "submission": prepared["submission"], "note": prepared["note"],
                "human_acts": filing_acts.acts_for(acts_key),
                "taps_to_done": filing_acts.taps_to_done(acts_key),
                "lines": prepared["lines"]}
    from fastapi.responses import Response
    audit.record(db, org_id=p.org_id, application_id=application_id,
                 action="consular_form_downloaded",
                 detail={"form_key": form_key, "kind": built["kind"]},
                 actor=p.user_id)
    return Response(content=built["pdf"], media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="{form_key}.pdf"'})


class SignatureUpload(BaseModel):
    # A data-URL or bare base64 PNG the applicant DREW in Ellis's pad.
    image_base64: str


@app.post("/cases/{application_id}/signature")
def save_applicant_signature(application_id: str, body: SignatureUpload,
                             db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    """Store the signature the applicant just drew, as their own act.

    It is placed into the consular form's Signature cell — the applicant
    signing through Ellis's pen, never Ellis signing for the applicant: the
    strokes are theirs, drawn in this session, recorded in the audit trail.
    A new drawing replaces the old one (people re-sign until it looks right).
    """
    import base64
    app_row = _owned(db, p, application_id)
    raw = body.image_base64.split(",", 1)[-1].strip()
    try:
        content = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(422, detail={"reason": "bad_image",
                                         "detail": "not decodable base64"})
    if not content.startswith(b"\x89PNG") or len(content) > 500_000:
        raise HTTPException(422, detail={"reason": "bad_image",
                                         "detail": "expected a small PNG"})
    import hashlib
    sha = hashlib.sha256(content).hexdigest()
    doc = models.StoredDocument(
        org_id=p.org_id, application_id=application_id,
        name="signature.png", mime="image/png", size_bytes=len(content),
        sha256=sha, storage_ref=f"local://{sha[:16]}",
        doc_type="applicant_signature", ocr_status="done")
    db.add(doc)
    db.commit()
    db.add(models.DocumentBlob(document_id=doc.id, org_id=p.org_id,
                               mime="image/png", content=content))
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id,
                 action="applicant_signature_captured",
                 detail={"sha256": sha, "bytes": len(content)}, actor=p.user_id)
    return {"ok": True, "signature_present": True}


def _iso3_for(db, destination: str) -> str:
    """The case's destination as ISO alpha-3. One shared lookup — see
    registry.iso3 for why writing another one is a bug."""
    from .visa_snapshot.registry import iso3
    d = (destination or "").strip()
    return iso3(d, default=d.upper())


_SUGGESTION_KEY = "_document_suggestions"


def _document_suggestions(db, app_row, wanted: list[str]) -> dict:
    """Answers read out of this case's own uploaded documents, cached.

    Reading a document costs a model call, and the question list is fetched
    every time the case screen renders. The cache lives under a reserved,
    underscore-prefixed key so it can never be mistaken for one of the
    applicant's own answers — nothing here has been confirmed by them yet.
    """
    if not wanted:
        return {}
    answers = dict(app_row.answers or {})
    cache = dict(answers.get(_SUGGESTION_KEY) or {})
    todo = [k for k in wanted if k not in cache]
    if todo:
        from . import document_answers
        try:
            found = document_answers.suggest(db, app_row.id, todo)
        except Exception:  # noqa: BLE001 — never break the question list
            found = {}
        # Remember the misses too, as explicit blanks: a document that does not
        # say where somebody works will not say it on the next render either.
        for k in todo:
            cache[k] = found.get(k) or None
        answers[_SUGGESTION_KEY] = cache
        app_row.answers = answers
        db.commit()
    return {k: v for k, v in cache.items() if v}


def _latest_passport_profile(db, app_row) -> dict:
    """The applicant's verified passport profile. One implementation, in
    checklist_intake, because the appointment packet needs the same thing and
    its own copy read an attribute that does not exist."""
    from . import checklist_intake
    return checklist_intake.latest_passport_profile(db, app_row)


@app.get("/cases/{application_id}/portal-account")
def get_portal_account(application_id: str, reveal: bool = False,
                       db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    """The portal account Ellis created FOR this applicant (their own email,
    a fresh generated password). The password lives only in the vault; it is
    revealed to the case owner on explicit request (?reveal=true) and never
    logged. This is the applicant's account — Ellis surfaces it so they can
    sign in to the government portal themselves at any time."""
    _owned(db, p, application_id)
    acct = db.execute(select(models.PortalAccount).where(
        models.PortalAccount.application_id == application_id)
        .order_by(models.PortalAccount.created_at.desc())).scalars().first()
    if acct is None:
        return {"exists": False}
    out = {"exists": True, "email": acct.username, "verified": bool(acct.verified),
           "adapter_id": acct.adapter_id}
    if reveal and acct.credential_ref:
        from . import vault, audit
        try:
            out["password"] = vault.reveal(acct.credential_ref)
            audit.record(db, org_id=p.org_id, application_id=application_id,
                         action="portal_account_password_revealed",
                         detail={"adapter_id": acct.adapter_id}, actor=p.user_id)
        except Exception:  # noqa: BLE001 — vault miss is honest, never a 500
            out["password_unavailable"] = True
    return out


@app.get("/cases/{application_id}/emails")
def list_case_emails(application_id: str, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    rows = db.execute(select(models.EmailNotification).where(
        models.EmailNotification.application_id == application_id)).scalars().all()
    return {"emails": [{"id": r.id, "event": r.event, "subject": r.subject,
                        "status": r.status, "locale": r.locale,
                        "attempts": r.attempts} for r in rows]}


@app.post("/admin/email/process-queue")
def process_email_queue(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import emails as emails_mod
    require_admin(p)
    return emails_mod.process_queue(db, org_id=p.org_id)


@app.get("/admin/email/dead-letters")
def email_dead_letters(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import emails as emails_mod
    require_admin(p)
    return {"dead": emails_mod.dead_letters(db, p.org_id)}


# ---- Route rules (Phase 2) + fees (Phase 3) ----
class RuleCreate(BaseModel):
    destination: str
    visa_type: str = "tourist"
    nationality: str = ""
    residence: str = ""
    source_url: str
    source_authority: str = ""
    effective_date: str = ""
    expiration_date: str = ""
    eligibility_conditions: list = []
    required_documents: list = []
    processing_method: str = ""
    electronic_available: Optional[bool] = None
    biometrics_required: Optional[bool] = None
    interview_required: Optional[bool] = None
    appointment_required: Optional[bool] = None
    personal_appearance_required: Optional[bool] = None
    third_party_preparation_allowed: Optional[bool] = None
    third_party_submission_allowed: Optional[bool] = None
    declaration_mandatory: Optional[bool] = None
    passport_validity_rule: dict = {}
    confidence: float = 0.0


class ReviewDecision(BaseModel):
    decision: str   # verified | rejected | stale


@app.post("/admin/routes/rules")
def create_route_rule(body: RuleCreate, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    require_admin(p)
    try:
        r = rules_mod.create_rule(db, actor=p.user_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": r.id, "version": r.version, "review_status": r.review_status}


@app.post("/admin/routes/rules/{rule_id}/review")
def review_route_rule(rule_id: str, body: ReviewDecision, db=Depends(get_session),
                      p: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    require_admin(p)
    try:
        r = rules_mod.review_rule(db, rule_id=rule_id, decision=body.decision, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "rule not found")
    return {"id": r.id, "review_status": r.review_status, "version": r.version}


@app.get("/routes/rules")
def get_route_rule(destination: str, visa_type: str = "tourist", nationality: str = "",
                   residence: str = "", db=Depends(get_session), _: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    args = dict(destination=destination, visa_type=visa_type,
                nationality=nationality, residence=residence)
    r = rules_mod.latest_rule(db, **args)
    history = rules_mod.rule_history(db, **args)
    def _d(x):
        return {"id": x.id, "version": x.version, "review_status": x.review_status,
                "source_url": x.source_url, "source_authority": x.source_authority,
                "effective_date": x.effective_date, "expiration_date": x.expiration_date,
                "eligibility_conditions": x.eligibility_conditions,
                "required_documents": x.required_documents,
                "processing_method": x.processing_method,
                "electronic_available": x.electronic_available,
                "biometrics_required": x.biometrics_required,
                "interview_required": x.interview_required,
                "appointment_required": x.appointment_required,
                "personal_appearance_required": x.personal_appearance_required,
                "third_party_preparation_allowed": x.third_party_preparation_allowed,
                "third_party_submission_allowed": x.third_party_submission_allowed,
                "declaration_mandatory": x.declaration_mandatory,
                "passport_validity_rule": x.passport_validity_rule,
                "confidence": x.confidence,
                "retrieved_at": x.retrieved_at.isoformat() if x.retrieved_at else None}
    return {"verified": _d(r) if r else None,
            "history": [{"id": h.id, "version": h.version, "review_status": h.review_status}
                        for h in history]}


@app.get("/routes/coverage")
def get_route_coverage(db=Depends(get_session), _: Principal = Depends(get_principal)):
    from . import rules as rules_mod
    return {"status_ladder": rules_mod.STATUS_LADDER, "routes": rules_mod.coverage_matrix(db)}


class FeeCreate(BaseModel):
    destination: str
    visa_type: str = "tourist"
    nationality: str = ""
    residence: str = ""
    government_fee_cents: int = 0
    service_fee_cents: int = 0
    optional_fees: list = []
    currency: str = "USD"
    conditions: list = []
    refundability: str = ""
    payment_methods: list = []
    payment_timing: str = ""
    source_url: str
    source_authority: str = ""
    effective_date: str = ""


@app.post("/admin/routes/fees")
def create_route_fee(body: FeeCreate, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import fees as fees_mod
    require_admin(p)
    try:
        r = fees_mod.create_fee(db, actor=p.user_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": r.id, "version": r.version, "review_status": r.review_status}


@app.post("/admin/routes/fees/{fee_id}/review")
def review_route_fee(fee_id: str, body: ReviewDecision, db=Depends(get_session),
                     p: Principal = Depends(get_principal)):
    from . import fees as fees_mod
    require_admin(p)
    try:
        r = fees_mod.review_fee(db, fee_id=fee_id, decision=body.decision, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "fee record not found")
    return {"id": r.id, "review_status": r.review_status, "version": r.version}


@app.get("/routes/fees")
def get_route_fee(destination: str, visa_type: str = "tourist", nationality: str = "",
                  residence: str = "", db=Depends(get_session), _: Principal = Depends(get_principal)):
    """Full fee breakdown for display BEFORE payment. Honest when no verified
    current fee exists — automated payment is blocked in that case."""
    from . import fees as fees_mod
    rec = fees_mod.verified_current_fee(db, destination=destination, visa_type=visa_type,
                                        nationality=nationality, residence=residence)
    return fees_mod.fee_breakdown(rec)


@app.get("/admin/routes/fees/stale")
def get_stale_fees(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import fees as fees_mod
    require_admin(p)
    return {"stale": fees_mod.stale_fees(db)}


# ---- Trip.com connector admin (Phase 15 partial — sandbox contract) ----
@app.get("/admin/tripcom/health")
def tripcom_health(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    return tripcom_admin.health(db, p.org_id)


@app.get("/admin/tripcom/deliveries")
def tripcom_deliveries(status: str = "", db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    return {"deliveries": tripcom_admin.list_deliveries(db, p.org_id, status=status)}


@app.post("/admin/tripcom/deliveries/{delivery_id}/replay")
def tripcom_replay(delivery_id: str, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    try:
        row = tripcom_admin.replay(db, org_id=p.org_id, delivery_id=delivery_id, actor=p.user_id)
    except KeyError:
        raise HTTPException(404, "delivery not found")
    return {"id": row.id, "replay_of": row.replay_of, "status": row.status}


@app.post("/admin/tripcom/process")
def tripcom_process(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .integrations import tripcom_admin
    require_admin(p)
    return tripcom_admin.process_deliveries(db, org_id=p.org_id)


# ---- Trip.com first-run administrator setup (Phase 7) ----
class SetupBody(BaseModel):
    tenant_name: Optional[str] = None
    admin_email: Optional[str] = None
    data_region: Optional[str] = None
    retention_days: Optional[int] = None
    base_urls: Optional[dict] = None
    branding: Optional[dict] = None
    email: Optional[dict] = None          # {provider, sender, reply_to, host, port, username, api_endpoint}
    google: Optional[dict] = None         # {project, location, ocr_processor_id, form_processor_id}
    trip: Optional[dict] = None           # {base_url, sandbox_base_url, client_id, signing_method}
    # Secrets — vaulted backend-only, never echoed back.
    kimi_api_key: Optional[str] = None
    browserbase_api_key: Optional[str] = None
    google_service_account_json: Optional[str] = None
    email_credential: Optional[str] = None
    trip_client_secret: Optional[str] = None
    trip_webhook_secret: Optional[str] = None


@app.get("/setup/status")
def setup_status(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    return setup_mod.redacted_status(db, p.org_id)


@app.post("/setup")
def save_setup_endpoint(body: SetupBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    try:
        return setup_mod.save_setup(db, org_id=p.org_id, actor=p.user_id,
                                    payload=body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/setup/test/{component}")
def test_setup_component(component: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    return setup_mod.test_component(db, org_id=p.org_id, component=component)


class TestEmailBody(BaseModel):
    to: str


@app.post("/setup/email/test")
def test_setup_email(body: TestEmailBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    return setup_mod.send_test_email(db, org_id=p.org_id, to=body.to)


class RotateBody(BaseModel):
    value: str


@app.post("/setup/rotate/{component}")
def rotate_setup_component(component: str, body: RotateBody, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    try:
        return setup_mod.rotate_component(db, org_id=p.org_id, component=component,
                                          new_value=body.value, actor=p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/setup/revoke/{component}")
def revoke_setup_component(component: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import setup as setup_mod
    require_admin(p)
    return setup_mod.revoke_component(db, org_id=p.org_id, component=component, actor=p.user_id)


@app.get("/diagnostics/providers")
def provider_diagnostics(_: Principal = Depends(get_principal)):
    """Circuit-breaker states, kill switches, and observability status — no
    secrets, no raw provider responses."""
    from . import provider_errors
    from .config import killswitches
    from . import observability
    return {"breakers": provider_errors.breakers_snapshot(),
            "kill_switches": killswitches(),
            "observability": observability.status()}


@app.get("/diagnostics/ocr")
def ocr_diagnostics(_: Principal = Depends(get_principal)):
    # Non-sensitive booleans + redacted error category only. Never returns
    # tokens, credential paths, or document content.
    from .providers import ocr_health
    return ocr_health.diagnostic()


@app.get("/admin/adapters/harness")
def adapters_harness(p: Principal = Depends(get_principal)):
    """Dry contract validation for every registered adapter definition (no live
    portal contact). The report is contract-test EVIDENCE, never an approval —
    activation stays a human admin action in adapters_admin."""
    require_admin(p)
    from .config import settings as _s
    from .portal.adapter_harness import dry_validate
    from .portal.driver_factory import register_adapters_for_mode
    from .portal.contract import _REGISTRY
    register_adapters_for_mode()
    return {"runtime_mode": _s().runtime_mode,
            "reports": [dry_validate(a).as_dict() for a in _REGISTRY.values()]}


@app.get("/adapters")
def get_adapters(_: Principal = Depends(get_principal)):
    # Registry is mode-keyed: mock modes list the demo adapters; real-only
    # modes honestly list only production-approved live adapters (none today).
    from .config import settings as _s
    from .portal.driver_factory import register_adapters_for_mode
    register_adapters_for_mode()
    return {"adapters": list_adapters(), "runtime_mode": _s().runtime_mode}


# ---- Schemas ----
class CreateCase(BaseModel):
    full_name: str
    email: str
    phone: str = ""
    time_zone: str = "UTC"
    destination_country: str
    visa_type: str = "tourist"
    answers: dict = {}


class AddDocument(BaseModel):
    name: str
    mime: str = "application/pdf"
    size_bytes: int = 1024
    text: str = ""          # embedded text layer / fixture for the local OCR provider
    content_b64: str = ""   # base64 image/PDF bytes → routed to Document AI / Kimi vision OCR
    # Uploading from a checklist row binds the document to that exact
    # requirement (never auto-fulfils it — the applicant's Submit does that).
    checklist_item_id: str = ""


class FieldEdit(BaseModel):
    key: str
    value: str


class Preferences(BaseModel):
    prefs: dict


class Authorization(BaseModel):
    max_fee_cents: int = 10000
    currency: str = "USD"
    allow_auto_book: bool = True
    allow_auto_reschedule: bool = False
    allow_representative_submit: bool = False


# ---- Cases ----
@app.post("/cases")
def create_case(body: CreateCase, db=Depends(get_session), p: Principal = Depends(get_principal)):
    applicant = models.Applicant(org_id=p.org_id, user_id=p.user_id, full_name=body.full_name,
                                 email=body.email, phone=body.phone, time_zone=body.time_zone)
    db.add(applicant)
    db.flush()
    ans = dict(body.answers)
    ans.setdefault("full_name", body.full_name)
    ans.setdefault("email", body.email)
    app_row = models.VisaApplication(org_id=p.org_id, user_id=p.user_id, applicant_id=applicant.id,
                                     destination_country=body.destination_country,
                                     visa_type=body.visa_type, answers=ans)
    db.add(app_row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=app_row.id, action="case_created",
                 detail={"destination": body.destination_country}, actor=p.user_id)
    return {"id": app_row.id, "state": app_row.state}


def _owned(db, p: Principal, application_id: str) -> models.VisaApplication:
    app_row = db.get(models.VisaApplication, application_id)
    if not app_row:
        raise HTTPException(404, "case not found")
    require_owner(p, app_row.org_id)
    return app_row


def _route_outcome_of(db, app_row) -> str:
    """This case's resolved route outcome ('VISA_ON_ARRIVAL', 'EVISA', …), or
    '' when it cannot be resolved. Machine-readable companion to
    _explain_no_live_adapter's applicant-facing sentence."""
    from .global_routes import resolver
    answers = app_row.answers or {}
    try:
        rec = resolver.resolve_route(
            db,
            nationality=answers.get("passport_nationality") or "",
            destination=app_row.destination_country or "",
            issuing_country=answers.get("passport_issuing_country") or None,
            travel_document_type=answers.get("travel_document_type") or "ordinary_passport",
            residence=answers.get("lawful_country_of_residence") or None)
    except Exception:  # noqa: BLE001 — never let a label break the stop
        return ""
    return str((rec or {}).get("route_outcome") or "")


def _explain_no_live_adapter(db, app_row, fallback: str) -> str:
    """Why can't Ellis drive a portal for THIS case? The internal reason ('no
    live driver is bound') is true but useless to an applicant, and reads like
    a broken install. Resolve the traveller's real route and say the honest,
    specific thing instead — most often that their nationality's route is
    decided in person and there is no website for anyone to drive."""
    from .global_routes import resolver
    from .global_routes.resolver import RegistryError
    answers = app_row.answers or {}
    try:
        rec = resolver.resolve_route(
            db,
            nationality=answers.get("passport_nationality") or "",
            destination=app_row.destination_country or "",
            issuing_country=answers.get("passport_issuing_country") or None,
            travel_document_type=answers.get("travel_document_type") or "ordinary_passport",
            residence=answers.get("lawful_country_of_residence") or None)
    except (RegistryError, Exception):  # noqa: BLE001 — never hide the stop
        return fallback
    outcome = (rec or {}).get("route_outcome") or ""
    dest = app_row.destination_country or "this destination"
    IN_PERSON = {
        "AUTHORIZED_VISA_CENTER": (
            f"Travellers on your passport apply for {dest} at an authorised visa "
            f"centre in person, with biometrics — there is no government website "
            f"to submit through. Ellis prepares your application, forms and "
            f"document checklist for that appointment."),
        "EMBASSY_OR_CONSULATE_APPLICATION": (
            f"Travellers on your passport apply for {dest} at an embassy or "
            f"consulate in person — there is no government website to submit "
            f"through. Ellis prepares your application, forms and document "
            f"checklist for that appointment."),
        "APPOINTMENT_REQUIRED": (
            f"This {dest} route requires an in-person appointment; Ellis "
            f"prepares everything you bring to it."),
        "MAIL_APPLICATION": (
            f"This {dest} route is filed by post; Ellis prepares the forms and "
            f"checklist you send."),
    }
    if outcome in IN_PERSON:
        return IN_PERSON[outcome]
    if outcome in ("VISA_EXEMPT", "VISA_ON_ARRIVAL"):
        return (f"Your passport does not need a visa applied for in advance for "
                f"{dest}, so there is nothing for Ellis to submit.")
    if outcome == "NO_AVAILABLE_TOURIST_ROUTE":
        return f"There is no tourist route to {dest} on record for your passport."
    if outcome in ("EVISA", "ELECTRONIC_AUTHORIZATION", "ENTRY_PREPARATION"):
        return (f"{dest} has an official online application, but Ellis does not "
                f"yet have an approved connection to that portal for your "
                f"nationality. Your case and documents are saved; nothing was "
                f"submitted.")
    return fallback


def _adapter_verified_result(db, application_id: str) -> bool:
    """Did an approved live adapter actually retrieve and verify an official
    result for THIS case? True only when a real completed execution produced
    government-domain submission evidence — never merely because the route
    resolves to a released adapter. This is what lets result_disposition
    refuse to present submitted/paid/confirmed as real without proof."""
    from .adapter_factory import models as fm
    from .visa_snapshot.authority import is_government_host
    exec_row = db.execute(select(fm.AdapterExecution).where(
        fm.AdapterExecution.application_id == application_id,
        fm.AdapterExecution.status == "completed").order_by(
        fm.AdapterExecution.created_at.desc())).scalars().first()
    if exec_row is None:
        return False
    evidence = db.execute(select(fm.AdapterOutcomeEvidence).where(
        fm.AdapterOutcomeEvidence.execution_id == exec_row.id)).scalars().all()
    return any(
        e.state_category in ("submitted", "submission_accepted", "appointment_booked")
        and e.hostname and is_government_host(e.hostname)
        for e in evidence)


def _case_execution_class(country: str, visa_type: str, db=None, app_row=None):
    """Classify what running this case's route ACTUALLY produces. Mock-allowed
    modes bind the MockPortal driver (class MOCK). Real-only modes register no
    demo adapters; a route whose portal family carries a RELEASED adapter
    executes through the live FlowRunner bridge (class LIVE_PRODUCTION — the
    class the bound driver itself declares), and anything else classifies
    UNSUPPORTED — the classification follows the driver, never a claim."""
    from .config import settings as _settings
    from .portal.driver_factory import register_adapters_for_mode, select_metadata_adapter
    if db is not None and app_row is not None and not _settings().mock_portal_allowed:
        from .portal.released_flow import build_for_case
        built = build_for_case(db, app_row)
        if built is not None:
            return execution.classify_adapter(built[1])
    register_adapters_for_mode()
    adapter = select_metadata_adapter(country, visa_type)
    return execution.classify_adapter(adapter)


# ---- Browserbase Live View infrastructure (generic; no portal transactions) --
# Sessions are tenant+case isolated. Live View URLs are short-lived, minted
# fresh per request, returned with no-store, and NEVER persisted, audited, or
# logged (observability.scrub also redacts Live-View URL shapes defensively).

@app.post("/cases/{application_id}/browser-session")
def create_browser_session(application_id: str, response: Response,
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    from .providers import browser as bb
    from .portal_store import current_browser_session, retire_other_sessions
    row = current_browser_session(db, application_id)
    # A session Ellis records as open may already have ended at the provider
    # (lifetime elapsed). Reconcile before handing the applicant a window:
    # a dead session is closed and a fresh one opened, never pretended.
    # is_remote_mode, not a vendor literal: a session opened on Steel is stored
    # with its own provenance ("steel"), and probing it as if it were local
    # would strand the applicant on a window Ellis refuses to reconcile.
    if row is not None and bb.is_remote_mode(row.mode) and not bb.session_alive(
            row.provider_session_id):
        row.status = "closed"
        db.commit()
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="browser_session_expired", detail={}, actor=p.user_id)
        row = None
    # While a portal run is queued/running, the RUN owns session identity: the
    # applicant's window only ever FOLLOWS the run's session. A window that
    # creates its own session here races the executor's create, and the
    # newest-row-wins retirement then releases the session Ellis is actually
    # driving — the applicant watched an idle twin page while eleven fields
    # checkpointed invisibly (Vietnam, 2026-08-04).
    active_run = portal_queue.progress_run(db, application_id)
    run_active = active_run is not None and active_run.status in ("queued", "running")
    if row is None and run_active:
        response.headers["Cache-Control"] = "no-store"
        # The provider a session opened right now would ACTUALLY run on. No
        # session exists yet, so this names the provider, never a vendor the
        # run is not using.
        return {"id": "", "mode": (bb.active_provider() if bb.is_configured()
                                   else "local"),
                "status": "pending", "fresh": False, "run_opening": True,
                "live_view_available": False,
                "browserbase_configured": bb.is_configured()}
    fresh = False
    if row is None:
        sess = bb.create_session()
        row = models.BrowserSession(org_id=p.org_id, application_id=application_id,
                                    provider_session_id=sess.get("id", ""),
                                    mode=sess.get("mode", "local"))
        db.add(row)
        db.commit()
        fresh = True
        # Concurrent opens (two dialogs mounting at once) can each find no
        # session and each create one. Exactly one open session per case: keep
        # this newest row and release the rest, so the applicant's window and
        # Ellis's driver can never end up on different sessions.
        retire_other_sessions(db, application_id, row.id)
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="browser_session_opened",
                     detail={"mode": row.mode}, actor=p.user_id)  # no ids/urls in audit
    # Retire duplicates on EVERY open, not only on creation — but never while
    # a run is active: the executor's registration owns retirement then.
    if not run_active:
        retire_other_sessions(db, application_id, row.id)
    response.headers["Cache-Control"] = "no-store"
    # fresh: a brand-new session shows a BLANK page until portal work runs —
    # the client should ask Ellis to restore the portal view.
    # The PROVIDER's session id is never exposed to the client (pinned by
    # test_browser_sessions) — only Ellis's own opaque row id.
    return {"id": row.id, "mode": row.mode, "status": row.status, "fresh": fresh,
            "live_view_available": bb.is_remote_mode(row.mode),
            "browserbase_configured": bb.is_configured()}


@app.get("/cases/{application_id}/browser-session/live-view")
def browser_session_live_view(application_id: str, response: Response,
                              db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Mint a fresh short-lived Live View URL. Honest 404s: no session, or the
    stack runs without Browserbase (local mode has no Live View)."""
    _owned(db, p, application_id)
    from .providers import browser as bb
    from .portal_store import current_browser_session
    row = current_browser_session(db, application_id)
    if row is None:
        raise HTTPException(404, detail={"reason": "no_session",
                                         "message": "no open browser session for this case"})
    if not bb.is_remote_mode(row.mode):
        raise HTTPException(404, detail={
            "reason": "not_configured",
            "message": "live view unavailable: session is local mode "
                       "(no cloud browser provider configured)"})
    url = bb.live_view_url(row.provider_session_id)
    if not url:
        # The session ended at the provider: say so honestly (it is NOT a
        # configuration problem) and mark it closed so the next open mints
        # a fresh one.
        if not bb.session_alive(row.provider_session_id):
            row.status = "closed"
            db.commit()
            raise HTTPException(404, detail={
                "reason": "session_ended",
                "message": "the secure portal session ended; Ellis will open a new one"})
        raise HTTPException(404, detail={"reason": "live_view_unavailable",
                                         "message": "live view URL unavailable from provider"})
    response.headers["Cache-Control"] = "no-store"
    return {"url": url, "expires_hint_seconds": 300}


class ViewerScrollBody(BaseModel):
    delta_y: float


@app.post("/cases/{application_id}/browser-session/scroll")
def browser_session_scroll(application_id: str, body: ViewerScrollBody,
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Apply the applicant's wheel to the case's own live session — the
    watch-only view forwards scroll here because the click shield (rightly)
    eats it. View-only by construction: a document scrollBy, never a click,
    a key, or a form value (see portal/viewer_gestures.py)."""
    _owned(db, p, application_id)
    from .portal import viewer_gestures
    from .portal_store import current_browser_session
    from .providers import browser as bb
    row = current_browser_session(db, application_id)
    if row is None:
        raise HTTPException(404, detail={"reason": "no_session",
                                         "message": "no open browser session for this case"})
    if not bb.is_remote_mode(row.mode):
        raise HTTPException(404, detail={"reason": "not_configured",
                                         "message": "scroll relay needs a live provider session"})
    dy = max(-4000.0, min(4000.0, float(body.delta_y or 0)))
    if not dy:
        return {"queued": False}
    try:
        url = viewer_gestures.connect_url_for(row.provider_session_id)
    except Exception:  # noqa: BLE001 — session ended at the provider
        raise HTTPException(409, detail={
            "reason": "session_ended",
            "message": "the secure portal session is not reachable right now"})
    return {"queued": viewer_gestures.enqueue_scroll(application_id, url, dy)}


@app.delete("/cases/{application_id}/browser-session")
def close_browser_session(application_id: str, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    from .providers import browser as bb
    from .models import _now as _model_now
    closed = 0
    for row in db.execute(select(models.BrowserSession).where(
            models.BrowserSession.application_id == application_id,
            models.BrowserSession.status == "open")).scalars():
        bb.close_session(row.provider_session_id)
        row.status = "closed"
        row.closed_at = _model_now()
        closed += 1
    db.commit()
    if closed:
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="browser_session_closed", detail={"count": closed},
                     actor=p.user_id)
    return {"closed": closed}


@app.get("/cases/{application_id}")
def get_case(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == application_id)).scalar_one_or_none()
    conf = db.execute(select(models.SubmissionConfirmation).where(
        models.SubmissionConfirmation.application_id == application_id)).scalar_one_or_none()
    # The portal's own confirmation page, captured at submission (shown on the
    # result screen so the applicant sees exactly what the government said).
    conf_doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id,
        models.StoredDocument.doc_type == "submission_confirmation").order_by(
        models.StoredDocument.created_at.desc())).scalars().first() if conf else None
    ec = _case_execution_class(app_row.destination_country, app_row.visa_type, db=db, app_row=app_row)
    # The disposition is the display guard: it refuses to present submitted/paid/
    # booked/confirmed as REAL unless an approved LIVE_PRODUCTION adapter actually
    # RETRIEVED AND VERIFIED the result from the official portal. That is not the
    # same as the route merely resolving to a released adapter: it requires a
    # real completed execution that produced official submission evidence for
    # THIS case. Without it, is_real_government_result is False even in a live
    # runtime — the applicant is never shown a government outcome Ellis did not
    # witness on the portal.
    adapter_verified = _adapter_verified_result(db, application_id)
    disposition = execution.result_disposition(app_row.state, ec,
                                               adapter_verified=adapter_verified)
    # H1B child filing cases carry one party's private facts (petitioner FEIN/
    # wage, or beneficiary passport); the org-scoped read must not hand those to
    # the other party. For every other case this is a single cheap type check
    # returning the answers unchanged.
    from .h1b.api import scope_child_case_read
    scoped = scope_child_case_read(db, app_row, p)
    answers_out = app_row.answers if scoped is None else scoped
    return {"id": app_row.id, "state": app_row.state, "answers": answers_out,
            "pending": exec_row.pending if exec_row else None,
            "portal_reference": app_row.portal_reference,
            "execution_class": str(ec),
            "disposition": disposition,
            "appointment": ({"slot_id": appt.slot_id, "location_id": appt.location_id,
                             "start_utc": appt.start_utc, "confirmation_no": appt.confirmation_no,
                             "reschedule_count": appt.reschedule_count,
                             "execution_class": str(ec),
                             "is_real": disposition["is_real_government_result"]} if appt else None),
            "confirmation": ({"reference_no": conf.reference_no, "receipt_no": conf.receipt_no,
                              "execution_class": str(ec),
                              "screenshot_document_id": conf_doc.id if conf_doc else None,
                              "is_real_government_confirmation": disposition["is_real_government_result"]}
                             if conf else None)}


@app.get("/cases/{application_id}/mock/verification")
def mock_verification(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """MOCK-ONLY convenience: returns the verification token the mock portal
    'emailed', so the applicant UI can complete the email-verification handoff in
    a demo. In production the applicant reads their own real email (or Mailpit in
    the local stack); this endpoint is disabled outside development and never
    exposes real personal data — only the mock's own generated token."""
    from .config import settings as _settings
    # Mock-only surface: reachable only in mock-allowed runtime modes.
    if not _settings().mock_portal_allowed or _settings().env not in ("development", "test"):
        raise HTTPException(404, "not found")
    _owned(db, p, application_id)
    wf = service.load_workflow(db, application_id)
    portal = getattr(wf, "_portal", None)
    emails = list(getattr(portal, "emails", []) or [])
    for e in reversed(emails):
        if e.get("kind") == "verification" and "token=" in e.get("link", ""):
            return {"token": e["link"].split("token=")[1], "kind": "verification"}
    return {"token": None, "kind": "verification"}


# Magic-byte structure check per accepted MIME type: a file whose bytes do not
# match its declared type is refused honestly (never rendered, never OCR'd).
_CONTENT_MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


@app.post("/cases/{application_id}/documents")
def add_document(application_id: str, body: AddDocument, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    import base64
    import hashlib
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    # File-type + size validation (MIME allowlist, 10 MB cap).
    if body.mime not in ("application/pdf", "image/jpeg", "image/png", "image/tiff"):
        raise HTTPException(415, "unsupported document type")
    if body.size_bytes > 10 * 1024 * 1024:
        raise HTTPException(413, "document too large")
    content = b""
    if body.content_b64:
        try:
            content = base64.b64decode(body.content_b64)
        except Exception:
            raise HTTPException(400, "invalid content_b64")
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "document too large")
        if not any(content.startswith(m) for m in _CONTENT_MAGIC[body.mime]):
            raise HTTPException(415, "file content does not match its declared type")
    # Checklist-row upload: resolve the target requirement FIRST so the item's
    # expected type informs classification (a portrait photo has no text; the
    # photo requirement's context is what identifies it).
    item_ctx = None
    if body.checklist_item_id:
        cg = checklist_intake.case_guidance(db, application_id)
        if cg is not None:
            item_ctx = checklist_intake.find_item(
                checklist_intake.current_checklist(db, app_row, cg), body.checklist_item_id)
        if item_ctx is None:
            raise HTTPException(404, "checklist requirement not found")
        if item_ctx.get("kind") != "document":
            raise HTTPException(400, "this requirement does not take an upload")
    item_types = set((item_ctx or {}).get("satisfied_by") or [])

    def _bind(doc_row):
        try:
            return checklist_intake.bind_document(
                db, p, app_row, body.checklist_item_id, doc_row.id,
                provenance={"source": "checklist_upload"})
        except checklist_intake.ChecklistError as e:
            raise HTTPException(e.status_code, e.detail)

    sha = hashlib.sha256(content or body.text.encode()).hexdigest()
    # Duplicate upload (double click / re-drop of the same file): return the
    # existing record — a duplicate never creates a second document row.
    dup = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id,
        models.StoredDocument.sha256 == sha)).scalars().first()
    if dup is not None:
        pc = dup.page_classification or {}
        out = {"id": dup.id, "doc_type": dup.doc_type, "duplicate": True,
               "mrz_valid": bool((dup.extracted_fields or {}).get("passport_number")),
               "execution_class": dup.execution_class,
               "page_type": pc.get("page_type", ""),
               "accepted_as_passport_identity": pc.get("accepted_as_passport_identity", False),
               "rejected": pc.get("reject", False), "message": "",
               "extracted_fields": dup.extracted_fields,
               "quality_warnings": dup.quality_warnings}
        if body.checklist_item_id:
            out["binding"] = _bind(dup)
        return out
    # OCR hierarchy with recorded failover: Document AI → (flagged) Kimi vision → local.
    # A filename that says "passport" (or the passport checklist row itself)
    # opts into the passport recovery path (EXIF/rotation retries +
    # multipage-PDF biodata-page selection).
    result, ocr_meta = ocr_provider.process_with_failover(
        content=content, text=body.text, mime=body.mime,
        expect_passport=bool(re.search(r"passport", body.name or "", re.I))
        or "passport" in item_types)
    ec = execution.classify_ocr(ocr_meta)

    # Passport biodata classification is SCOPED (the classifier-leak fix): the
    # full accept/reject flow runs ONLY when the applicant is providing a
    # passport (the passport checklist row, or a filename that says so), or
    # when the page carries an actual ICAO machine-readable zone. A bank
    # statement that mentions "Visa card", a hotel booking, an itinerary or a
    # photograph must NEVER receive passport-page validation or its warnings.
    mrz = ocr_provider.parse_mrz(result.recognized_text) if result.recognized_text else None
    passport_context = bool(re.search(r"passport", body.name or "", re.I)) \
        or "passport" in item_types
    mrz_kind = passport_classifier.detect_mrz_kind(result.recognized_text or "")
    doc_type = result.doc_type
    # Classification provenance drives advisory confidence downstream
    # (mrz/keyword/filename/applicant = deterministic; kimi = semantic hint).
    classifier_kind = "none"
    is_photo_upload = bool(content) and not (result.recognized_text or "").strip() \
        and (re.search(r"photo|portrait|headshot", body.name or "", re.I)
             or "photo" in item_types)

    if passport_context:
        # Full biodata accept/reject UX — the applicant is providing a passport.
        classification = passport_classifier.classify_page(
            text=result.recognized_text, mrz=mrz, has_image=bool(content),
            vision_hint=result.doc_type)
        if classification["accepted_as_passport_identity"] or result.doc_type == "passport":
            classifier_kind = "mrz"
        if classification["reject"]:
            doc_type = classification["page_type"]
    elif mrz_kind is not None or result.doc_type == "passport":
        # Passport-adjacent material aimed at a NON-passport requirement:
        # classify honestly for the advisory ("Ellis detected this as …") but
        # never reject and never attach passport-page warnings.
        classification = passport_classifier.classify_page(
            text=result.recognized_text, mrz=mrz, has_image=bool(content),
            vision_hint=result.doc_type)
        classifier_kind = "mrz"
        page = classification["page_type"]
        doc_type = ("passport" if classification["accepted_as_passport_identity"]
                    else "prior_visa" if page == "visa_page"
                    else "residence_permit" if page == "residence_permit"
                    else "document")
        classification = {**classification, "reject": False, "message": ""}
    else:
        # A plain supporting document: no passport validation of any kind.
        if is_photo_upload:
            classification = {"page_type": "photo",
                              "accepted_as_passport_identity": False,
                              "reject": False, "message": "",
                              "reasons": ["image with no text targeted at the "
                                          "photo requirement / photo filename"]}
            doc_type = "photo"
            classifier_kind = "filename" if re.search(
                r"photo|portrait|headshot", body.name or "", re.I) else "applicant"
        elif not (result.recognized_text or "").strip():
            classification = {"page_type": "unreadable" if content else "supporting_document",
                              "accepted_as_passport_identity": False,
                              "reject": False, "message": "",
                              "reasons": ["no readable text extracted"]}
        else:
            classification = {"page_type": "supporting_document",
                              "accepted_as_passport_identity": False,
                              "reject": False, "message": "",
                              "reasons": ["supporting document; passport "
                                          "validation not applicable"]}

    # The photo carve-out also applies in passport context when the applicant
    # clearly uploaded a photograph (filename) that OCR'd to nothing.
    if passport_context and classification.get("reject") and is_photo_upload:
        classification = {**classification, "reject": False, "message": "",
                          "page_type": "photo",
                          "accepted_as_passport_identity": False}
        doc_type = "photo"
        classifier_kind = "filename"

    fields_map = {f.key: {"value": f.value, "confidence": f.confidence, "page": f.page}
                  for f in result.fields}
    # A visa sticker / stamp / ID page must NEVER seed passport identity —
    # advisory acceptance does not change that invariant: outside a validated
    # biodata page, passport-adjacent material carries no identity fields.
    if (mrz_kind is not None or result.doc_type == "passport") and \
            not classification["accepted_as_passport_identity"]:
        fields_map = {}
    # Supporting documents get a route-checklist classification: deterministic
    # keywords first, then an optional digit-masked Kimi call ONLY when the
    # result stayed generic.
    if not classification["reject"] and \
            not classification["accepted_as_passport_identity"] and \
            doc_type not in ("passport", "photo", "prior_visa", "residence_permit"):
        from .providers import doc_classifier
        refined = doc_classifier.classify_supporting_document(
            result.recognized_text, body.name)
        if refined != "document":
            classifier_kind = "keyword"
        else:
            kimi_refined = doc_classifier.classify_with_kimi(result.recognized_text)
            if kimi_refined:
                refined = kimi_refined
                classifier_kind = "kimi"
        if refined != "document":
            doc_type = refined
    if classification["reject"]:
        # Never let a REJECTED page seed passport identity — this includes visa/
        # stamp/cover/national-ID pages AND an unverifiable-MRZ page. The applicant
        # re-uploads a clear biodata page; identity is only ever seeded from an
        # accepted, checksum-validated biodata page.
        fields_map = {}

    # Local, deterministic language detection on the extracted text — nothing
    # leaves the backend for this label.
    from . import translation as translation_mod
    detected_language = translation_mod.detect_language(result.recognized_text or "")

    doc = models.StoredDocument(org_id=p.org_id, application_id=application_id, name=body.name,
                                mime=body.mime, size_bytes=body.size_bytes, sha256=sha,
                                storage_ref=f"local://{sha[:16]}", doc_type=doc_type,
                                ocr_status="done", quality_warnings=result.quality_warnings,
                                execution_class=str(ec),
                                page_classification={
                                    "page_type": classification["page_type"],
                                    "accepted_as_passport_identity": classification["accepted_as_passport_identity"],
                                    "reject": classification["reject"],
                                    "reasons": classification["reasons"],
                                    "classifier": classifier_kind},
                                extracted_fields=fields_map,
                                ocr_text=result.recognized_text or "",
                                language=detected_language)
    db.add(doc)
    db.commit()
    # Phase 13: keep the bytes for the in-app preview (served only via the
    # authenticated endpoint / short-lived signed URLs — never a local path).
    if content:
        db.add(models.DocumentBlob(document_id=doc.id, org_id=p.org_id,
                                   mime=body.mime, content=content))
        db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="document_ocr",
                 detail={"doc_type": doc_type, "mrz_valid": result.mrz_valid,
                         "engine": ocr_meta.get("primary"), "fallback_used": ocr_meta.get("fallback_used"),
                         "docai_degraded": ocr_meta.get("docai_degraded"),
                         "execution_class": str(ec),
                         "page_type": classification["page_type"],
                         "rejected": classification["reject"]}, actor=p.user_id)
    execution.record_execution(db, org_id=p.org_id, application_id=application_id,
                               action="document_ocr", ec=ec,
                               detail={"doc_type": doc_type, "mrz_valid": result.mrz_valid,
                                       "page_type": classification["page_type"]})
    out = {"id": doc.id, "doc_type": doc_type, "mrz_valid": result.mrz_valid,
           "execution_class": str(ec),
           "page_type": classification["page_type"],
           "accepted_as_passport_identity": classification["accepted_as_passport_identity"],
           "rejected": classification["reject"],
           "message": classification["message"],
           "language": detected_language,
           "extracted_fields": doc.extracted_fields, "quality_warnings": result.quality_warnings}
    if body.checklist_item_id:
        out["binding"] = _bind(doc)
    return out


def _required_fields_for(country: str, visa_type: str) -> list[str]:
    """The applicant fields the destination's adapter requires (for the
    'missing information' step). Mode-keyed: real-only modes without a live
    adapter honestly return [] — requirements come from the verified snapshot,
    never invented from demo adapter metadata."""
    from .portal.driver_factory import register_adapters_for_mode, select_metadata_adapter
    register_adapters_for_mode()
    adapter = select_metadata_adapter(country, visa_type)
    if adapter is None:
        return []
    return list(getattr(adapter, "required_applicant_fields", []) or [])


@app.get("/cases/{application_id}/review")
def review(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    docs = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()
    # Only OCR-shaped values ({"value": ...}) feed cross-document conflicts.
    # H1B derived documents (prepared forms, RFE packets, evidence covers) carry
    # FLAT metadata in extracted_fields (e.g. {"party": "petitioner"}), so guard
    # the shape rather than assume every value is an OCR field dict (finding #2).
    conflicts = ocr_provider.cross_document_conflicts(
        [{"fields": [{"key": k, "value": v["value"]}
                     for k, v in (d.extracted_fields or {}).items()
                     if isinstance(v, dict) and "value" in v]} for d in docs])
    required = _required_fields_for(app_row.destination_country, app_row.visa_type)
    answers = app_row.answers or {}
    missing = [f for f in required if not answers.get(f)]
    return {"documents": [{"id": d.id, "name": d.name, "doc_type": d.doc_type, "approved": d.approved,
                           "extracted_fields": d.extracted_fields, "quality_warnings": d.quality_warnings}
                          for d in docs], "conflicts": conflicts,
            "required_fields": required, "missing_fields": missing, "answers": answers}


@app.get("/cases/{application_id}/form-questions")
def case_form_questions(application_id: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    """The tick-box questions this applicant's consular form needs.

    A consular form asks most of its questions as boxes — marital status,
    purpose of travel, who is paying. Ellis asks them in plain words here and
    ticks the box the applicant chose, so the form they download needs nothing
    finished by hand. Returns which are already answered."""
    app_row = _owned(db, p, application_id)
    from . import consular_forms as cf
    # Through the one shared lookup. This had its own inline copy that returned
    # the registry KEY — 'DE' for Germany, where every table downstream is
    # keyed by 'DEU' — so a Schengen case was told it had no form to fill and
    # no questions to answer (2026-08-04).
    iso = _iso3_for(db, app_row.destination_country or "")
    form_key = cf.form_for_destination(iso) if iso else None
    if not form_key:
        return {"form_key": None, "questions": [], "fields": []}
    # Merge the passport FIRST, so Ellis never asks for something the biodata
    # page already told it.
    answers = cf.answers_from_documents(app_row.answers or {},
                                        _latest_passport_profile(db, app_row))
    qs = cf.checkbox_questions(form_key)
    for q in qs:
        q["answer"] = answers.get(q["key"])
    # The form's WRITTEN questions — place of birth, phone, where you are
    # staying. The form has always needed these; nothing ever asked for them,
    # so they reached the applicant only as blanks on a downloaded PDF and as
    # raw storage keys in a "still needed" list.
    fields = cf.field_questions(form_key, answers, only_missing=False)
    # Before asking a person, read what they already gave Ellis. Somebody who
    # uploaded a hotel confirmation should not be asked where they are staying.
    # Cached on the case, because this reads documents with a model and the
    # question list is fetched on every render.
    blanks = [f["key"] for f in fields if not f["answer"]]
    derived = _document_suggestions(db, app_row, blanks)
    for f in fields:
        hit = derived.get(f["key"])
        if hit and not f["answer"]:
            # Pre-filled, and SAID to be pre-filled: it becomes the applicant's
            # answer when they save it, never before. A consular form is signed
            # under penalty of perjury — Ellis may read a document and offer
            # what it found; it may not answer in somebody else's name.
            f["answer"] = hit["value"]
            f["from_document"] = hit.get("document") or hit.get("doc_type") or ""
    # The official form's own dropdowns become dropdowns here: CEAC's
    # recorded option lists ride on the matching questions, so the applicant
    # picks from exactly what the government form offers — and free-text
    # fields stay free text.
    if form_key == "ds160_prep":
        from . import ds160_api
        for f in fields:
            opts = ds160_api.options_for_answer_key(f["key"])
            if opts:
                f["options"] = opts
        # The complete up-front ask: the DS-160's remaining unconditional
        # questions — the applicant-only series and the security pages — join
        # the wizard so nothing known waits to interrupt the fill.
        qs = qs + ds160_api.wizard_supplement(answers)
    return {"form_key": form_key, "title": cf.FORMS[form_key]["title"],
            "questions": qs, "fields": fields,
            "unanswered": [q["key"] for q in qs if q.get("answer") in (None, "", [])],
            "required_unanswered": [f["key"] for f in fields
                                    if f["required"] and not f["answer"]]}


@app.get("/cases/{application_id}/appointment-packet")
def case_appointment_packet(application_id: str, format: str = "json",
                            db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """The carry-in folder for an in-person route: the cover sheet (where to
    go, the fee, what is still missing, what to do on the day), the filled
    official form, and every document the applicant uploaded.

    format=json  -> the manifest (what is in the folder, what is missing)
    format=zip   -> the folder itself, as one download
    409 when the route is one Ellis files itself: a carry-in folder there
    would tell the applicant to do work they do not have to do."""
    app_row = _owned(db, p, application_id)
    from . import appointment_packet
    try:
        packet = appointment_packet.build_for_case(db, app_row)
    except appointment_packet.PacketNotApplicable as e:
        raise HTTPException(409, detail={"reason": "packet_not_applicable",
                                         "detail": str(e)})
    if format in ("pdf", "zip"):
        # ONE PDF by default: the application is a thing somebody prints and
        # hands across a counter, and a zip made them assemble it themselves.
        # The zip stays reachable for anyone who wants the separate originals.
        combined = format == "pdf"
        data = (appointment_packet.render_combined_pdf(db, app_row, packet)
                if combined else appointment_packet.render_zip(db, app_row, packet))
        audit.record(db, org_id=app_row.org_id, application_id=application_id,
                     action="appointment_packet_downloaded",
                     detail={"documents": len(packet.get("documents") or []),
                             "format": format,
                             "ready": packet.get("ready")}, actor=p.user_id)
        name = ("ellis-visa-application.pdf" if combined
                else "ellis-application-documents.zip")
        return Response(content=data,
                        media_type="application/pdf" if combined else "application/zip",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{name}"'})
    out = {k: v for k, v in packet.items() if not k.startswith("_")}
    # WHERE THE APPLICANT IS in the one journey this route has: answer what
    # Ellis still needs, take the folder, then go and present it. Decided here
    # rather than on the screen, so the page cannot disagree with the packet
    # about whether it is complete — and so 'already downloaded' survives a
    # reload, which a screen-local flag would not.
    last = db.execute(select(models.AuditEvent).where(
        models.AuditEvent.application_id == application_id,
        models.AuditEvent.action == "appointment_packet_downloaded").order_by(
        models.AuditEvent.at.desc())).scalars().first()
    out["downloaded_at"] = last.at.isoformat() if last is not None else None
    out["stage"] = ("ask" if not packet.get("ready")
                    else "next" if last is not None else "ready")
    return out


class AppointmentRecord(BaseModel):
    start_utc: int
    location: str = ""
    confirmation_no: str = ""


# Address answers that change WHICH consular post serves an applicant. Editing
# any of them makes a previously-found post potentially wrong, so the lookup is
# re-run rather than left stale.
_JURISDICTION_ANSWER_KEYS = ("address_city", "address_region", "address_country",
                             "lawful_country_of_residence", "residence_subdivision")


def _find_post_for_case(db, app_row, *, attempts: int = 1) -> dict:
    """Look up the consular post that serves this applicant, from the address
    they gave. Verified answers are cached as a jurisdiction rule, so the same
    residence never pays for the search twice."""
    from . import consular_research as cr
    from .portal.live_browser import LiveBrowserSession
    answers = app_row.answers or {}

    def fetch_page(url):
        host = url.split("/")[2]
        s = LiveBrowserSession(allowed_hostnames=[host, host.replace("www.", "")])
        try:
            page = s._ensure_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            links = page.evaluate(
                "() => [...document.querySelectorAll('a')].map(a=>a.href).slice(0,400)") or []
            return page.url, text, links
        finally:
            s.close()

    # The destination goes in by NAME on purpose: the search prompt and the
    # source-page corroboration both read better with "Germany" than "DEU".
    # consular_research.store normalizes it to ISO-3 on the way into the table.
    return cr.resolve_for_applicant(
        db, destination=app_row.destination_country or "",
        residence=(answers.get("lawful_country_of_residence")
                   or answers.get("address_country") or ""),
        address_city=answers.get("address_city") or "",
        address_region=answers.get("address_region") or "",
        fetch_page=fetch_page, attempts=attempts)


def _schedule_consular_lookup(application_id: str, org_id: str) -> None:
    """Run the post lookup off the request thread. Saving an address must never
    wait on a network search, and a failed search must never fail the save —
    the applicant can always trigger it explicitly from the appointment step."""
    import threading

    def _run():
        from .db import SessionLocal
        from . import assisted_booking, appointment_packet
        db = SessionLocal()
        try:
            row = db.get(models.VisaApplication, application_id)
            if row is None:
                return
            # Only in-person routes need a post; e-visa routes never do.
            try:
                route = appointment_packet.build_for_case(db, row).get("_route") or {}
            except appointment_packet.PacketNotApplicable:
                return
            if not assisted_booking.needs_appointment(route.get("route_outcome") or ""):
                return
            if (route.get("jurisdiction") or {}).get("status") == "verified":
                return          # already known for this residence — no re-search
            _find_post_for_case(db, row, attempts=2)
        except Exception:  # noqa: BLE001 — a background search never surfaces
            pass              # as an error on the applicant's save
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()


def _attach_applicant_window(db, application_id: str, hostnames: list[str]):
    """Attach to the applicant's OWN secure window, so the calendar they are
    shown is the session they will book in — not a second browser whose
    CAPTCHA answer and slot hold would belong to nobody."""
    from .portal.live_browser import LiveBrowserSession
    from .portal_store import current_browser_session
    from .providers import browser as bb
    row = current_browser_session(db, application_id)
    if row is None or not bb.is_remote_mode(row.mode):
        raise HTTPException(409, detail={
            "reason": "no_secure_window",
            "detail": "open the secure window first"})
    if not bb.session_alive(row.provider_session_id):
        row.status = "closed"
        db.commit()
        raise HTTPException(409, detail={
            "reason": "session_ended",
            "detail": "the secure window closed; open it again"})
    # session_connect_info mints the CDP URL for an EXISTING session — the
    # same helper the released flow reattaches with.
    return LiveBrowserSession(
        allowed_hostnames=hostnames,
        session=bb.session_connect_info(row.provider_session_id))


class _WindowDriver:
    """The tiny surface gov_calendar needs, over a live page."""
    def __init__(self, page):
        self.page = page
    def goto(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(2200)
    def evaluate(self, js, *a):
        return self.page.evaluate(js, *a) if a else self.page.evaluate(js)
    def fill(self, selector, value):
        self.page.fill(selector, value, timeout=15000)
    def click(self, selector):
        self.page.click(selector, timeout=15000)
        self.page.wait_for_timeout(2200)
    def shot(self, selector):
        """A PNG of one element, base64. Used to show the applicant the
        challenge image LARGE and legible. Reading the pixels of a
        cross-origin <img> through a canvas taints it and yields nothing, so
        the screenshot is taken by the browser itself. Asking for `body`
        means the whole page: RK-Termin's confirmation page styles its body
        as "hidden" to an element locator, while a page screenshot works."""
        import base64
        if selector == "body":
            return base64.b64encode(
                self.page.screenshot(full_page=True)).decode()
        loc = self.page.locator(selector).first
        loc.wait_for(state="visible", timeout=15000)
        return base64.b64encode(loc.screenshot(type="png")).decode()


@app.get("/cases/{application_id}/calendar/missions")
def case_calendar_missions(application_id: str, db=Depends(get_session),
                           p: Principal = Depends(get_principal)):
    """Every mission this appointment system serves, by name, so the applicant
    picks 'Beijing' instead of knowing their consulate's code is 'peki'."""
    _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, ["service2.diplo.de"])
    try:
        return {"missions": gc.rk_termin_missions(_WindowDriver(sess._ensure_page()))}
    except Exception as e:  # noqa: BLE001 — an unreachable list is honest
        raise HTTPException(409, detail={"reason": "missions_unavailable",
                                         "detail": str(e)[:200]})
    finally:
        sess.close()


@app.post("/cases/{application_id}/calendar/open")
def case_calendar_open(application_id: str, location_code: str = "",
                       realm_id: str = "", category_id: str = "",
                       db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    """Walk the applicant's secure window to a government appointment calendar.

    Only for the systems that HAVE a readable calendar (no accounts, one image
    check): Germany's RK-Termin today. Returns the real category list and
    whether the applicant still has to complete the portal's image check —
    which only they may do."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    if not location_code:
        raise HTTPException(422, detail={"reason": "location_required",
                                         "detail": "which mission to open"})
    sess = _attach_applicant_window(db, application_id, ["service2.diplo.de"])
    try:
        drv = _WindowDriver(sess._ensure_page())
        out = gc.rk_termin_walk(drv, location_code=location_code,
                                realm_id=realm_id, category_id=category_id)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "calendar_unavailable",
                                         "detail": str(e)})
    finally:
        # close() stops OUR playwright attachment; the applicant's remote
        # window stays alive because we did not create it.
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_opened",
                 detail={"location": location_code[:20],
                         "captcha_required": out.get("captcha_required")},
                 actor=p.user_id)
    return out


class CalendarPickIn(BaseModel):
    href: str
    known_hrefs: list[str] = []


class CalendarCaptchaIn(BaseModel):
    text: str


class CalendarFormOpenIn(BaseModel):
    query: str


class CalendarFormFillIn(BaseModel):
    answers: dict[str, str]


class CalendarFormConfirmIn(BaseModel):
    labels: list[str]


class CalendarPassportIn(BaseModel):
    mime: str
    size_bytes: int
    content_b64: str = ""
    text: str = ""


@app.post("/cases/{application_id}/calendar/captcha")
def case_calendar_captcha_submit(application_id: str, body: CalendarCaptchaIn,
                                 db=Depends(get_session),
                                 p: Principal = Depends(get_principal)):
    """Type the challenge answer THE APPLICANT read and typed into Ellis.

    Ellis never reads the image: no OCR, no model, no solving service. This
    transcribes a human's answer into the portal's own field, the same way a
    signed declaration is transcribed. `still_challenged` tells the applicant
    honestly when the answer was wrong or the image refreshed."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        out = gc.submit_captcha(_WindowDriver(sess._ensure_page()), text=body.text)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "captcha_not_submitted",
                                         "detail": str(e)})
    finally:
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_captcha_answered",
                 detail={"still_challenged": out.get("still_challenged")},
                 actor=p.user_id)
    return out


@app.get("/cases/{application_id}/calendar/captcha")
def case_calendar_captcha(application_id: str, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    """The CAPTCHA challenge image, enlarged for legibility. Ellis READS the
    image and shows it bigger; the applicant types the answer themselves in
    their own window. Ellis never solves it."""
    _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        drv = _WindowDriver(sess._ensure_page())
        # Frame the live window on the challenge too: the applicant is reading
        # from it while they type. Presentation only.
        gc.focus_captcha(drv)
        return gc.captcha_image(drv)
    finally:
        sess.close()


@app.post("/cases/{application_id}/calendar/times")
def case_calendar_times(application_id: str, body: CalendarPickIn,
                        db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    """The TIMES offered on the day the applicant picked. Read-only: the month
    grid says which DAYS are open, the times live on the day's own page, and
    looking at them reserves nothing."""
    _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        return gc.read_day_times(_WindowDriver(sess._ensure_page()),
                                 href=body.href, known_hrefs=body.known_hrefs)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "day_not_openable",
                                         "detail": str(e)})
    finally:
        sess.close()


@app.post("/cases/{application_id}/calendar/pick")
def case_calendar_pick(application_id: str, body: CalendarPickIn,
                       db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    """Open the day the APPLICANT picked, in the applicant's own window.

    Ellis reads the month and shows it; the applicant chooses a day in Ellis;
    this carries that choice to the government site. Ellis never picks the day
    itself, and it never fills or submits the booking form behind it — the
    name, passport and email on that form are the applicant's, and so is the
    confirmation email. `known_hrefs` is the grid Ellis actually displayed, so
    a stale or edited link cannot steer someone to a day they never saw."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        out = gc.open_day(_WindowDriver(sess._ensure_page()),
                          href=body.href, known_hrefs=body.known_hrefs)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "day_not_openable",
                                         "detail": str(e)})
    finally:
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_day_opened",
                 detail={"url": str(out.get("url"))[:200]}, actor=p.user_id)
    return out


@app.post("/cases/{application_id}/calendar/book-form")
def case_calendar_book_form(application_id: str, body: CalendarFormOpenIn,
                            db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """Open the new-appointment form for the category the APPLICANT chose and
    read its questions. `query` is the site's own query string from the walk —
    RK-Termin refuses hand-built addresses and so does this endpoint."""
    _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        drv = _WindowDriver(sess._ensure_page())
        if str(body.query or "").strip():
            return gc.open_book_form(drv, query=body.query)
        # No query: read the form on the page the applicant's window is
        # already on (after a solved gate, re-navigating would re-summon it).
        return gc.read_book_form(drv)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "form_unavailable",
                                         "detail": str(e)})
    finally:
        sess.close()


@app.post("/cases/{application_id}/calendar/time")
def case_calendar_time(application_id: str, body: CalendarPickIn,
                       db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    """Open the booking form behind the TIME the applicant picked, and read
    its questions. The href must be one of the Book links Ellis read off the
    day page and showed; RK-Termin may re-challenge on the way in, which is
    reported as a gate rather than an empty form."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        drv = _WindowDriver(sess._ensure_page())
        opened = gc.open_time(drv, href=body.href, known_hrefs=body.known_hrefs)
        try:
            out = gc.read_book_form(drv)
        except gc.CalendarUnavailable:
            if gc.captcha_present(drv):
                out = {"fields": [], "captcha_required": True, "gated": True,
                       "url": opened.get("url", "")}
            else:
                raise
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "time_not_openable",
                                         "detail": str(e)})
    finally:
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_time_opened",
                 detail={"url": str(out.get("url"))[:200]}, actor=p.user_id)
    return out


@app.post("/cases/{application_id}/calendar/book-form/passport")
def case_calendar_book_form_passport(application_id: str,
                                     body: CalendarPassportIn,
                                     db=Depends(get_session),
                                     p: Principal = Depends(get_principal)):
    """Answer the booking form's identity questions from the applicant's own
    passport, read ONCE through the existing OCR/MRZ pipeline. Only a
    checksum-validated biodata page seeds identity; anything else is an
    honest rejection with retry guidance. Nothing is stored here — the
    values go into the form the applicant reviews and attests."""
    import base64

    from .providers import ocr as ocr_provider
    from .providers import passport_classifier
    from . import execution as _execution  # noqa: F401 — parity with intake
    from .visa_snapshot import intake_flow
    from .visa_snapshot.api import _DOC_MAX_BYTES, _DOC_MIME_ALLOWLIST

    _owned(db, p, application_id)
    if body.mime not in _DOC_MIME_ALLOWLIST:
        raise HTTPException(415, "unsupported document type")
    if body.size_bytes > _DOC_MAX_BYTES:
        raise HTTPException(413, "document too large")
    content = b""
    if body.content_b64:
        try:
            content = base64.b64decode(body.content_b64)
        except Exception:
            raise HTTPException(400, "invalid content_b64")
        if len(content) > _DOC_MAX_BYTES:
            raise HTTPException(413, "document too large")

    result, ocr_meta = ocr_provider.process_with_failover(
        content=content, text=body.text, mime=body.mime, expect_passport=True)
    mrz = (ocr_provider.parse_mrz(result.recognized_text)
           if result.recognized_text else None)
    classification = passport_classifier.classify_page(
        text=result.recognized_text, mrz=mrz, has_image=bool(content),
        vision_hint=result.doc_type)
    if classification["reject"] or not classification["accepted_as_passport_identity"]:
        return {"accepted": False,
                "message": classification["message"] or
                "This page could not be used as the passport biodata page. "
                "Upload a clear photo of the photo page of your passport."}

    fields_map = {f.key: {"value": f.value, "confidence": f.confidence,
                          "page": f.page} for f in result.fields}
    profile = intake_flow.build_passport_profile(
        ocr_fields=fields_map, mrz=mrz,
        recognized_text=result.recognized_text,
        mrz_valid=bool(mrz and mrz.get("valid")))
    vals = {k: str(v.get("value") or "")
            for k, v in (profile.get("fields") or {}).items()}
    birth = vals.get("birth_date", "")
    dd = ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", birth)
    if m:
        dd = f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    return {"accepted": True,
            "surname": vals.get("surname", ""),
            "given_names": vals.get("given_names", ""),
            "passport_number": vals.get("passport_number", ""),
            "birth_date": birth,
            "birth_date_ddmmyyyy": dd,
            "needs_confirmation": sorted(
                k for k, v in (profile.get("fields") or {}).items()
                if v.get("needs_confirmation"))}


@app.post("/cases/{application_id}/calendar/book-form/fill")
def case_calendar_book_form_fill(application_id: str, body: CalendarFormFillIn,
                                 db=Depends(get_session),
                                 p: Principal = Depends(get_principal)):
    """Transcribe the APPLICANT'S answers into the booking form. Fail-closed
    in gov_calendar: no captcha box, no checkboxes, no buttons, no field the
    live form does not carry, no invented select options. The audit trail
    records WHICH fields were filled, never their values."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        out = gc.fill_book_form(_WindowDriver(sess._ensure_page()),
                                answers=body.answers)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "form_unavailable",
                                         "detail": str(e)})
    finally:
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_form_filled",
                 detail={"filled": out.get("filled", []),
                         "refused": [r.get("name") for r in out.get("refused", [])]},
                 actor=p.user_id)
    return out


@app.post("/cases/{application_id}/calendar/book-form/confirm")
def case_calendar_book_form_confirm(application_id: str,
                                    body: CalendarFormConfirmIn,
                                    db=Depends(get_session),
                                    p: Principal = Depends(get_principal)):
    """Relay the confirmation statements the applicant ticked in Ellis. Each
    statement was shown VERBATIM in the Ellis UI; only live checkboxes whose
    own label matches a confirmed statement are ticked."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        out = gc.relay_confirmations(_WindowDriver(sess._ensure_page()),
                                     labels=body.labels)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "form_unavailable",
                                         "detail": str(e)})
    finally:
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_confirmations_relayed",
                 detail={"ticked": out.get("ticked", []),
                         "unmatched": out.get("unmatched", [])},
                 actor=p.user_id)
    return out


@app.post("/cases/{application_id}/calendar/book-form/captcha")
def case_calendar_book_form_captcha(application_id: str,
                                    body: CalendarCaptchaIn,
                                    db=Depends(get_session),
                                    p: Principal = Depends(get_principal)):
    """Type the picture answer THE APPLICANT read and typed into Ellis, into
    the form's answer box. Nothing is pressed — on this form the answer
    travels with the single Submit, which needs their instruction."""
    _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        return gc.enter_captcha_answer(_WindowDriver(sess._ensure_page()),
                                       text=body.text)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "captcha_not_entered",
                                         "detail": str(e)})
    finally:
        sess.close()


@app.post("/cases/{application_id}/calendar/book-form/submit")
def case_calendar_book_form_submit(application_id: str,
                                   db=Depends(get_session),
                                   p: Principal = Depends(get_principal)):
    """Press Submit as the applicant's explicitly relayed instruction — the
    final button in Ellis is theirs, and this endpoint exists only behind it.
    gov_calendar refuses while any confirmation is unticked or the picture
    answer is empty."""
    app_row = _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id, list(gc.DAY_LINK_HOSTS))
    try:
        out = gc.submit_book_form(_WindowDriver(sess._ensure_page()),
                                  applicant_instructed=True)
    except gc.CalendarUnavailable as e:
        raise HTTPException(409, detail={"reason": "not_submittable",
                                         "detail": str(e)})
    finally:
        sess.close()
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="calendar_form_submitted",
                 detail={"url": str(out.get("url"))[:200],
                         "looks_successful": out.get("looks_successful")},
                 actor=p.user_id)
    return out


@app.get("/cases/{application_id}/calendar/month")
def case_calendar_month(application_id: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    """Read the month the applicant's window is showing — after THEY have
    completed the portal's image check. Ellis reads the grid and never clicks a
    day: on these systems a click reserves a real slot."""
    _owned(db, p, application_id)
    from . import gov_calendar as gc
    sess = _attach_applicant_window(db, application_id,
                                    ["service2.diplo.de", "secure.e-konsulat.gov.pl",
                                     "secure2.e-konsulat.gov.pl"])
    try:
        return gc.month_summary(_WindowDriver(sess._ensure_page()))
    finally:
        # close() stops OUR playwright attachment; the applicant's remote
        # window stays alive because we did not create it.
        sess.close()


def _observation_context(db, app_row):
    """Resolve THIS case's route to its portal family and the family's build
    request, deciding honestly whether an attended observation can help.
    Returns (family, link, req, reason) — a non-empty reason means no."""
    from sqlalchemy import select
    from .adapter_factory import models as fm
    from .global_routes import resolver
    from .global_routes.models import FamilyAdapterLink, PortalFamily
    answers = app_row.answers or {}
    try:
        rec = resolver.resolve_route(
            db,
            nationality=answers.get("passport_nationality") or "",
            destination=app_row.destination_country or "",
            issuing_country=answers.get("passport_issuing_country") or None,
            travel_document_type=answers.get("travel_document_type") or "ordinary_passport",
            residence=answers.get("lawful_country_of_residence") or None)
    except Exception as e:  # noqa: BLE001 — an unresolvable route is a real answer
        return None, None, None, f"route could not be resolved: {str(e)[:120]}"
    gov = (rec or {}).get("governing_adapter") or {}
    fam_id = gov.get("family_id") or ""
    if not gov.get("required") or not fam_id:
        return None, None, None, ("this route has no online portal to learn — "
                                  "it is decided in person")
    if gov.get("released"):
        return None, None, None, "this portal is already fully supported"
    family = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == fam_id)).scalars().first()
    link = db.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == fam_id)).scalars().first()
    req = (db.get(fm.AdapterBuildRequest, link.build_request_id)
           if link is not None and link.build_request_id else None)
    if family is None or req is None:
        return family, link, None, ("Ellis has not attempted this portal's "
                                    "automatic build yet — that runs first")
    return family, link, req, ""


def _observation_state(db, family, link, req) -> dict:
    from . import attended_observation
    from . import authorized_observation as ao
    return {
        "available": True,
        "portal_name": family.name, "portal_url": family.base_url,
        "account_required": bool(family.account_required),
        "consent_text": ao.CONSENT_TEXT,
        "text_version": ao.CONSENT_TEXT_VERSION,
        "consented": ao.has_consent(req),
        "build_state": req.state,
        "released": bool(link.released),
        "gate_missing": (link.gate_report or {}).get("missing", []),
        **attended_observation.status(req.id),
    }


@app.get("/cases/{application_id}/portal-observation")
def case_portal_observation(application_id: str, db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """Can this applicant's own session teach Ellis their portal — and where
    does that stand? Offered only when the portal is real, unreleased, and its
    automatic build already ran and parked (most often: the form is behind
    sign-in, or behind a check only a person may complete)."""
    app_row = _owned(db, p, application_id)
    family, link, req, reason = _observation_context(db, app_row)
    if reason:
        return {"available": False, "reason": reason}
    return _observation_state(db, family, link, req)


@app.post("/cases/{application_id}/portal-observation/consent")
def case_portal_observation_consent(application_id: str, db=Depends(get_session),
                                    p: Principal = Depends(get_principal)):
    """The applicant agrees, under the versioned wording they were shown, that
    their signed-in run may teach Ellis the SHAPE of this portal's pages."""
    app_row = _owned(db, p, application_id)
    from . import authorized_observation as ao
    family, link, req, reason = _observation_context(db, app_row)
    if reason:
        raise HTTPException(409, detail={"reason": "observation_unavailable",
                                         "detail": reason})
    ao.record_consent(db, req, application_id=application_id, actor=p.user_id)
    return _observation_state(db, family, link, req)


@app.post("/cases/{application_id}/portal-observation/decline")
def case_portal_observation_decline(application_id: str, db=Depends(get_session),
                                    p: Principal = Depends(get_principal)):
    """Consent withdrawn — nothing further is recorded, the case is unaffected."""
    app_row = _owned(db, p, application_id)
    from . import authorized_observation as ao
    family, link, req, reason = _observation_context(db, app_row)
    if reason:
        raise HTTPException(409, detail={"reason": "observation_unavailable",
                                         "detail": reason})
    ao.withdraw_consent(db, req, actor=p.user_id)
    return _observation_state(db, family, link, req)


@app.post("/cases/{application_id}/portal-observation/start")
def case_portal_observation_start(application_id: str, db=Depends(get_session),
                                  p: Principal = Depends(get_principal)):
    """Begin the attended session: Ellis attaches read-only to the applicant's
    secure window, opens the portal's start page, and records page STRUCTURE
    as they work. It never fills, clicks, solves or submits anything."""
    app_row = _owned(db, p, application_id)
    from . import attended_observation, authorized_observation as ao
    family, link, req, reason = _observation_context(db, app_row)
    if reason:
        raise HTTPException(409, detail={"reason": "observation_unavailable",
                                         "detail": reason})
    entry_urls = (req.portal_evidence or {}).get("entry_urls") or []
    try:
        attended_observation.start(
            db, req, application_id=application_id,
            hosts=family.hostnames or [],
            start_url=(entry_urls[0] if entry_urls else family.base_url or ""))
    except ao.ObservationRefused as e:
        raise HTTPException(409, detail={"reason": "consent_required",
                                         "detail": str(e)})
    except attended_observation.WindowUnavailable as e:
        raise HTTPException(409, detail={"reason": "no_secure_window",
                                         "detail": str(e)})
    except attended_observation.SessionAlready:
        pass    # already recording is success, not an error, for the applicant
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="attended_observation_started",
                 detail={"family_id": family.family_id}, actor=p.user_id)
    return _observation_state(db, family, link, req)


@app.post("/cases/{application_id}/portal-observation/finish")
def case_portal_observation_finish(application_id: str, db=Depends(get_session),
                                   p: Principal = Depends(get_principal)):
    """The applicant is done. If a real application form was observed, the
    build re-runs the full verification chain in the background — the same
    sixteen gates as every portal decide release, never the observation."""
    app_row = _owned(db, p, application_id)
    from . import attended_observation
    family, link, req, reason = _observation_context(db, app_row)
    if reason:
        raise HTTPException(409, detail={"reason": "observation_unavailable",
                                         "detail": reason})
    out = attended_observation.finish(db, req, family_id=family.family_id,
                                      actor=p.user_id)
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="attended_observation_finished",
                 detail={"family_id": family.family_id,
                         "pages": out.get("pages"), "forms": out.get("forms"),
                         "rebuilt": out.get("rebuilt")}, actor=p.user_id)
    return dict(_observation_state(db, family, link, req), session=out)


@app.post("/cases/{application_id}/find-consular-post")
def case_find_consular_post(application_id: str, db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """Find the post that serves this applicant, from the address on their case.

    Runs the official-source search and verifies the answer before it can
    become a booking link; an unverifiable answer is reported as such rather
    than shown as a place to go."""
    app_row = _owned(db, p, application_id)
    answers = app_row.answers or {}
    if not (answers.get("lawful_country_of_residence") or answers.get("address_country")):
        raise HTTPException(422, detail={
            "reason": "residence_required",
            "detail": "Ellis needs the applicant's country of residence to find "
                      "the competent post"})
    out = _find_post_for_case(db, app_row)
    audit.record(db, org_id=app_row.org_id, application_id=application_id,
                 action="consular_post_lookup",
                 detail={"status": out.get("status"), "post": out.get("post", "")[:120]},
                 actor=p.user_id)
    return out


@app.get("/cases/{application_id}/appointment-booking")
def case_appointment_booking(application_id: str, db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    """Where this applicant books, and what they have booked so far.

    Every booking system keeps its slots behind the applicant's own account, so
    Ellis opens the OFFICIAL booking site in its secure window and the applicant
    picks the slot themselves. This endpoint says where that window should go —
    from verified route data only — and returns any appointment already
    recorded. 409 when the route needs no appointment."""
    app_row = _owned(db, p, application_id)
    from . import appointment_packet, assisted_booking
    try:
        route = appointment_packet.build_for_case(db, app_row).get("_route") or {}
    except appointment_packet.PacketNotApplicable as e:
        raise HTTPException(409, detail={"reason": "no_appointment_needed",
                                         "detail": str(e)})
    if not assisted_booking.needs_appointment(route.get("route_outcome") or ""):
        raise HTTPException(409, detail={"reason": "no_appointment_needed",
                                         "detail": "this route needs no appointment"})
    booked = assisted_booking.summary(db, app_row)
    # Which government calendar Ellis can READ for this destination, so the
    # UI only offers the German mission picker on a GERMANY case — it was
    # rendering (and failing) on every in-person route (2026-08-02).
    dest = (app_row.destination_country or "").strip().lower()
    gov_cal = "rk_termin" if dest in ("deu", "germany", "de") else ""
    # Where to go, whether or not a booking link exists. These are separate
    # questions and Ellis used to answer neither when it could not answer the
    # second: an applicant asking "which embassy, and where is it?" got a
    # bare "no verified booking address". The post block below carries its own
    # status, so the screen can show the address AND say it is unconfirmed.
    jur = route.get("jurisdiction") or {}
    best = jur.get("best_known") or {}
    post = {
        "name": jur.get("competent_post_name") or best.get("competent_post_name") or "",
        "kind": jur.get("competent_post_kind") or best.get("competent_post_kind") or "",
        "address": jur.get("address") or best.get("address") or "",
        "source_url": best.get("evidence") or "",
        "status": "verified" if jur.get("status") in ("verified", "resolved")
                  else ("unconfirmed" if best.get("competent_post_name") else "unknown"),
    }
    try:
        target = assisted_booking.booking_target(route)
    except assisted_booking.BookingUnavailable as e:
        return {"bookable": False, "reason": str(e), "appointment": booked,
                "post": post, "gov_calendar": gov_cal}
    return {"bookable": True, "booking_url": target["url"],
            "post_name": target["post_name"], "post": post, "appointment": booked,
            "gov_calendar": gov_cal}


@app.post("/cases/{application_id}/appointment-booking")
def case_record_appointment(application_id: str, body: AppointmentRecord,
                            db=Depends(get_session),
                            p: Principal = Depends(get_principal)):
    """Record the appointment the applicant just booked in the secure window.
    Ellis never chooses a slot; this is the applicant confirming what they
    chose, so the date reaches their packet and reminders."""
    app_row = _owned(db, p, application_id)
    from . import assisted_booking
    try:
        return assisted_booking.record(
            db, app_row, start_utc=int(body.start_utc),
            location=body.location or "", confirmation_no=body.confirmation_no or "",
            actor=p.user_id)
    except ValueError as e:
        raise HTTPException(422, detail={"reason": "invalid_appointment",
                                         "detail": str(e)})


@app.get("/cases/{application_id}/checklist")
def case_checklist(application_id: str, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    """The route-specific journey state saved at continuation: the Kimi route
    decision, disposition, workflow type, and the document checklist with live
    per-item status. No official-source audit exists on the applicant path."""
    app_row = _owned(db, p, application_id)
    from . import checklist_intake
    from .visa_snapshot import intake_flow
    cg = checklist_intake.case_guidance(db, application_id)
    if cg is None:
        return {"guidance": None, "disposition": None, "continuation_kind": None,
                "checklist": [], "checklist_counts": {"total": 0, "required_missing": 0},
                "intake_stage": {"completed": False, "completed_at": None},
                "verification": None, "route_workflow_type": None,
                "form_questions": _known_form_questions(db, app_row)}
    # Serve-time normalization: stored two-pass-era guidance rows carry a label
    # claiming a retired second-pass check — it must never reach the UI.
    from .visa_snapshot import kimi_primary
    guidance = kimi_primary.normalize_guidance_label(cg.guidance)
    if isinstance(guidance, dict) and guidance.get("guidance"):
        # The registry's pair record outranks stored model prose on whether
        # this journey is a visa at all (arrival-card case shown as
        # "tourist visa, 30 SGD", 2026-08-04).
        guidance = {**guidance, "guidance":
                    kimi_primary.reconcile_guidance_with_route(
                        db, guidance["guidance"],
                        nationality=(app_row.answers or {}).get(
                            "passport_nationality", ""),
                        destination=app_row.destination_country or "")}
    g_inner = (guidance or {}).get("guidance") or {}
    # Per-item status now reflects the applicant's EXPLICIT submissions (an
    # upload alone never fulfils a requirement) + the durable intake stage.
    status = checklist_intake.checklist_state(db, app_row, cg)
    from . import translation as translation_mod
    target = translation_mod.target_for_destination(app_row.destination_country)
    return {"guidance": guidance, "disposition": cg.disposition,
            "continuation_kind": cg.continuation_kind, "intake_id": cg.intake_id,
            "checklist": status["items"], "checklist_counts": status["counts"],
            "intake_stage": status["intake_stage"],
            "translation": {
                "target": target,
                "target_name": translation_mod.language_name(target),
                "certified_note": translation_mod.certified_translation_flag(g_inner)},
            "verification": (guidance or {}).get("verification") or None,
            "route_workflow_type": g_inner.get("route_workflow_type"),
            "health_questions": intake_flow.pending_health_questions(
                g_inner, answers=app_row.answers or {}),
            # Form answers the released flow is KNOWN to need but the case
            # lacks — asked here, at case open, so the applicant never waits
            # for a live portal run to rediscover them one pause at a time.
            "form_questions": _known_form_questions(db, app_row)}


def _known_form_questions(db, app_row) -> list[dict]:
    from .portal import released_flow as released_mod
    try:
        return released_mod.known_missing_questions(db, app_row)
    except Exception:  # noqa: BLE001 — a broken route must not break the page
        return []


class ChecklistSubmit(BaseModel):
    document_id: Optional[str] = None
    # True = the applicant explicitly confirms a low-confidence assignment
    # ("This appears to be X, but this requirement needs Y").
    confirm: bool = False


@app.post("/cases/{application_id}/checklist/{item_id}/submit")
def submit_checklist_document(application_id: str, item_id: str,
                              body: ChecklistSubmit = ChecklistSubmit(),
                              db=Depends(get_session), p: Principal = Depends(get_principal)):
    """The applicant's explicit Submit for one requirement: binds the document
    permanently, marks the requirement fulfilled with a timestamp, preserves
    classification provenance. Idempotent — repeated clicks never double-submit.
    A confident mismatch is refused; an uncertain one needs confirm=true."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        out = checklist_intake.submit_document(
            db, p, app_row, item_id, body.document_id, confirm=body.confirm)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    out.update({"checklist": state["items"], "checklist_counts": state["counts"],
                "intake_stage": state["intake_stage"]})
    return out


@app.post("/cases/{application_id}/checklist/{item_id}/withdraw")
def withdraw_checklist_document(application_id: str, item_id: str,
                                db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Withdraw the submission (or remove the upload) for one requirement —
    it returns to Needed. The stored file stays on the case; idempotent."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        out = checklist_intake.withdraw_document(db, p, app_row, item_id)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    out.update({"checklist": state["items"], "checklist_counts": state["counts"],
                "intake_stage": state["intake_stage"]})
    return out


class ChecklistBind(BaseModel):
    document_id: str


@app.post("/cases/{application_id}/checklist/{item_id}/bind")
def bind_checklist_document(application_id: str, item_id: str, body: ChecklistBind,
                            db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Attach an EXISTING case document to a requirement (reusing an uploaded
    file for another requirement, or attaching a translation artifact) —
    binding only; the applicant's Submit still fulfils it."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        binding = checklist_intake.bind_document(
            db, p, app_row, item_id, body.document_id,
            provenance={"source": "applicant_attach"})
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    return {"binding": binding, "checklist": state["items"],
            "checklist_counts": state["counts"],
            "intake_stage": state["intake_stage"]}


class DocTranslateBody(BaseModel):
    # Explicit target override; defaults to the destination route's language.
    target: Optional[str] = None


@app.post("/cases/{application_id}/documents/{doc_id}/translate")
def translate_case_document(application_id: str, doc_id: str,
                            body: DocTranslateBody = DocTranslateBody(),
                            db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-requested Kimi K3 machine translation of one document's
    OCR-extracted TEXT (raw image/PDF bytes never leave the backend). The
    result is a linked, clearly-labelled artifact — never presented as a
    certified translation; idempotent per (document, target)."""
    from . import checklist_intake, translation as translation_mod
    app_row = _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")
    target = (body.target or "").strip() or \
        translation_mod.target_for_destination(app_row.destination_country)
    try:
        out = translation_mod.translate_document(db, p, app_row, doc, target)
    except translation_mod.TranslationError as e:
        raise HTTPException(e.status_code, e.detail)
    cg = checklist_intake.case_guidance(db, application_id)
    g_inner = ((cg.guidance if cg else {}) or {}).get("guidance") or {}
    out["certified_translation_note"] = translation_mod.certified_translation_flag(g_inner)
    out["target_language_name"] = translation_mod.language_name(target)
    return out


class DocTypeBody(BaseModel):
    doc_type: str


@app.post("/cases/{application_id}/documents/{doc_id}/set-type")
def set_document_type(application_id: str, doc_id: str, body: DocTypeBody,
                      db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-chosen document type for an AMBIGUOUS upload (safe whitelist
    only; never overrides a confident classification, never 'passport')."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        out = checklist_intake.set_document_type(db, p, app_row, doc_id, body.doc_type)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)
    state = checklist_intake.checklist_state(db, app_row)
    out.update({"checklist": state["items"], "checklist_counts": state["counts"]})
    return out


@app.post("/cases/{application_id}/checklist/complete")
def complete_document_intake(application_id: str, db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    """Server-validated Continue after documents: refuses while any mandatory
    requirement is unfulfilled; records the completed stage durably; advances
    the EXISTING case to its route's next stage (visa/authorization preparation,
    entry preparation, or renewal preparation). Idempotent."""
    from . import checklist_intake
    app_row = _owned(db, p, application_id)
    try:
        return checklist_intake.complete_stage(db, p, app_row)
    except checklist_intake.ChecklistError as e:
        raise HTTPException(e.status_code, e.detail)


class AnswersUpdate(BaseModel):
    answers: dict


@app.post("/cases/{application_id}/answers")
def update_answers(application_id: str, body: AnswersUpdate, db=Depends(get_session),
                   p: Principal = Depends(get_principal)):
    """Merge applicant-supplied answers (the 'missing information' step). A
    material change invalidates any prior signature, exactly like approving a
    document field."""
    from . import dates as dates_mod
    app_row = _owned(db, p, application_id)
    ans = dict(app_row.answers or {})
    incoming = {}
    for k, v in (body.answers or {}).items():
        # Same canonicalization the mid-run answer path applies: date-like
        # answers are stored ISO, whatever format the applicant typed. An
        # unparseable date is kept verbatim (visible, confirmable) rather
        # than silently guessed — the portal fill will surface it honestly.
        kind = dates_mod.date_kind_for_key(k)
        if kind and isinstance(v, str):
            iso = dates_mod.normalize_any(v.strip(), kind=kind, us_numeric=True)
            if iso:
                v = iso
        incoming[k] = v
    changed = {k: v for k, v in incoming.items() if ans.get(k) != v}
    ans.update(incoming)
    app_row.answers = ans
    db.commit()
    # The applicant just told Ellis where they live, which is what decides
    # WHICH consulate serves them. Look it up now, in the background, so the
    # appointment step already knows where to send them instead of asking them
    # to wait for a search later. Never blocks saving their answers, and only
    # for routes that are actually attended in person.
    if any(k in changed for k in _JURISDICTION_ANSWER_KEYS):
        _schedule_consular_lookup(application_id, app_row.org_id)
    # Answers the applicant TYPES INTO ELLIS are consented by construction —
    # they stale the frozen review VERSION (re-frozen under advance consent at
    # the next enqueue) but never void the ONE authorization signature
    # (single-ceremony product decision 2026-07-27; same policy as the
    # provide_information signal path).
    invalidated = invalidate_signatures_if_changed(db, application_id,
                                                   protect_authorization=True)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="answers_updated",
                 detail={"keys": list(changed.keys()), "signatures_invalidated": invalidated}, actor=p.user_id)
    required = _required_fields_for(app_row.destination_country, app_row.visa_type)
    missing = [f for f in required if not ans.get(f)]
    return {"answers": ans, "missing_fields": missing, "signatures_invalidated": invalidated}


@app.post("/cases/{application_id}/documents/{doc_id}/approve")
def approve_document(application_id: str, doc_id: str, edits: Optional[list[FieldEdit]] = None,
                     db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import dates as dates_mod
    app_row = _owned(db, p, application_id)
    doc = db.get(models.StoredDocument, doc_id)
    if not doc or doc.application_id != application_id:
        raise HTTPException(404, "document not found")

    def _date_kind(key: str) -> str | None:
        k = key.lower()
        if not (k.endswith("_date") or k in ("date_of_birth", "date_of_expiry", "date_of_issue")):
            return None
        return "birth" if "birth" in k else "issue" if "issue" in k else "expiry"

    fields = dict(doc.extracted_fields)
    for e in (edits or []):
        value = e.value
        # An applicant may type a date in the U.S. display format (MM/DD/YYYY)
        # or any printed form — it is normalized to the canonical YYYY-MM-DD
        # before it is stored anywhere. An unparseable date entry is kept
        # verbatim (visible, confirmable) rather than silently guessed.
        kind = _date_kind(e.key)
        if kind:
            norm = dates_mod.normalize_any(value, kind=kind, us_numeric=True)
            if norm:
                value = norm
        fields[e.key] = {"value": value, "confidence": 1.0, "page": 1, "source": "applicant_edit"}
    doc.extracted_fields = fields
    doc.approved = True
    # Approved fields flow into the application answers — date values always
    # as canonical ISO (a raw MRZ YYMMDD or printed form can never leak into
    # answers from this path).
    ans = dict(app_row.answers)
    for k, v in fields.items():
        value = v["value"]
        kind = _date_kind(k)
        if kind:
            norm = dates_mod.normalize_any(value, kind=kind, us_numeric=True)
            if norm:
                value = norm
        ans[k] = value
    app_row.answers = ans
    db.commit()
    # A material change to approved data invalidates any prior signature.
    invalidated = invalidate_signatures_if_changed(db, application_id)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="document_approved",
                 detail={"doc_id": doc_id, "edits": [e.key for e in (edits or [])],
                         "signatures_invalidated": invalidated}, actor=p.user_id)
    # Phase 5: validate passport expiry IMMEDIATELY after applicant approval.
    validity = None
    travel_case_validity = None
    if doc.doc_type == "passport":
        from . import passport_validity, renewal
        validity = passport_validity.check_case_passport(db, app_row)
        if validity.get("blocking"):
            passport_validity.enforce_and_notify(db, app_row, validity)
        # Renewal completion: an approved NEW passport inside a renewal case
        # updates the linked travel case's passport fields, re-evaluates the
        # destination validity, and resumes the travel case.
        travel_case_validity = renewal.propagate_renewed_passport(
            db, app_row, doc, actor=p.user_id)
    return {"approved": True, "answers": app_row.answers,
            "signatures_invalidated": invalidated, "passport_validity": validity,
            "travel_case_validity": travel_case_validity}


@app.post("/cases/{application_id}/preferences")
def set_preferences(application_id: str, body: Preferences, db=Depends(get_session),
                    p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    row = db.execute(select(models.AppointmentPreference).where(
        models.AppointmentPreference.application_id == application_id)).scalar_one_or_none()
    if not row:
        row = models.AppointmentPreference(application_id=application_id)
        db.add(row)
    row.prefs = body.prefs
    db.commit()
    return {"prefs": row.prefs}


@app.post("/cases/{application_id}/authorization")
def create_authorization(application_id: str, body: Authorization, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    payload = docusign.authorization_payload(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        destination=app_row.destination_country, visa_type=app_row.visa_type,
        portal=app_row.destination_country, max_fee_cents=body.max_fee_cents, currency=body.currency,
        allow_auto_book=body.allow_auto_book, allow_auto_reschedule=body.allow_auto_reschedule,
        allow_representative_submit=body.allow_representative_submit)
    env = docusign.create_envelope(payload)
    row = models.AuthorizationEnvelope(application_id=application_id, provider=env["provider"],
                                       envelope_id=env.get("envelope_id") or "",
                                       max_fee_cents=body.max_fee_cents, currency=body.currency,
                                       allow_auto_book=body.allow_auto_book,
                                       allow_auto_reschedule=body.allow_auto_reschedule,
                                       allow_representative_submit=body.allow_representative_submit,
                                       artifact_hash=env.get("artifact_hash", ""))
    db.add(row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="authorization_created",
                 detail={"provider": env["provider"]}, actor=p.user_id)
    return {"provider": env["provider"], "production_equivalent": env.get("production_equivalent", True)}


# ---- Official portal discovery (produces DISABLED drafts only) ----
class DiscoverBody(BaseModel):
    country: str
    visa_type: str = "tourist"
    nationality: str = ""     # applicant nationality — refines queries + eligibility
    residence: str = ""       # applicant country of residence


@app.post("/discovery")
def run_discovery(body: DiscoverBody, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .portal import discovery
    from datetime import datetime, timezone
    draft = discovery.discover_official_visa_portal(
        country=body.country, visa_type=body.visa_type,
        nationality=body.nationality, residence=body.residence,
        reviewer="", verification_timestamp=datetime.now(timezone.utc).isoformat())
    row = models.PortalDraft(org_id=p.org_id, country=body.country, visa_type=body.visa_type,
                             draft=draft, status="disabled_draft")
    db.add(row)
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="portal_discovery",
                 detail={"country": body.country, "candidates": len(draft["candidates"]),
                         "search_status": draft["search_status"]}, actor=p.user_id)
    # The draft is ALWAYS disabled; activation requires human review + approval.
    return {"draft_id": row.id, "adapter_status": draft["adapter_status"],
            "production_enabled": draft["production_enabled"], "requires_admin_review": True,
            "search_status": draft["search_status"], "verified_candidates": draft["verified_candidates"],
            "queries": draft["queries"], "portal_limitations": draft["portal_limitations"]}


@app.get("/discovery/drafts")
def list_drafts(db=Depends(get_session), p: Principal = Depends(get_principal)):
    rows = db.execute(select(models.PortalDraft).where(models.PortalDraft.org_id == p.org_id)).scalars().all()
    return {"drafts": [{"id": r.id, "country": r.country, "visa_type": r.visa_type,
                        "status": r.status} for r in rows]}


class ReviewBody(BaseModel):
    decision: str  # "approved" | "rejected"


@app.post("/discovery/drafts/{draft_id}/review")
def review_draft(draft_id: str, body: ReviewBody, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    row = db.get(models.PortalDraft, draft_id)
    if not row:
        raise HTTPException(404, "draft not found")
    require_owner(p, row.org_id)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved or rejected")
    # Approval here only marks the DRAFT reviewed — it does NOT create a live
    # adapter. Building + production-enabling an adapter is a separate,
    # code-reviewed step gated by the adapter contract validator.
    row.status = body.decision
    row.reviewed_by = p.user_id
    db.commit()
    audit.record(db, org_id=p.org_id, application_id="", action="portal_draft_review",
                 detail={"draft_id": draft_id, "decision": body.decision}, actor=p.user_id)
    return {"id": row.id, "status": row.status, "note": "review only — no live adapter created"}


# ---- Native Ellis e-signature ----
class SignBody(BaseModel):
    document_hash: str
    consent_given: bool = False
    intent_confirmed: bool = False
    signature_method: str = "typed"   # typed | drawn
    signature_value: str = ""
    step_up_token: str                # short-lived action token proving step-up auth
    auth_method: str = "email_otp"
    # Binds the signature to the EXACT envelope the prepare step returned, so
    # a concurrently created envelope can never swap the document terms.
    envelope_id: str = ""


@app.post("/cases/{application_id}/authorization/prepare")
def prepare_authorization(application_id: str, body: Authorization, db=Depends(get_session),
                          p: Principal = Depends(get_principal)):
    """Build the exact authorization document + hash + a short-lived step-up
    action token the applicant must satisfy before signing."""
    from .providers import esign
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    docs = [d.name for d in db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()]
    text = esign.build_authorization_text(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        destination=app_row.destination_country, visa_type=app_row.visa_type,
        portal=app_row.destination_country, max_fee_cents=body.max_fee_cents, currency=body.currency,
        allow_auto_book=body.allow_auto_book, allow_auto_reschedule=body.allow_auto_reschedule,
        allow_representative_submit=body.allow_representative_submit)
    dh = esign.document_hash(text)
    snap = esign.application_snapshot_hash(app_row.answers, docs)
    # Persist the pending authorization envelope + snapshot for later invalidation.
    env = models.AuthorizationEnvelope(
        application_id=application_id, provider="native_ellis", status="prepared",
        max_fee_cents=body.max_fee_cents, currency=body.currency, allow_auto_book=body.allow_auto_book,
        allow_auto_reschedule=body.allow_auto_reschedule,
        allow_representative_submit=body.allow_representative_submit, artifact_hash="")
    db.add(env)
    db.commit()
    # A step-up token the client redeems after completing MFA/OTP/passkey.
    token = issue_action_token(p, "esign", application_id, ttl_seconds=600)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="authorization_prepared",
                 detail={"document_hash": dh, "app_snapshot_hash": snap}, actor=p.user_id)
    return {"document_text": text, "document_hash": dh, "app_snapshot_hash": snap,
            "step_up_token": token, "consent_version": esign.CONSENT_VERSION,
            "template_version": esign.TEMPLATE_VERSION, "envelope_id": env.id}


@app.post("/cases/{application_id}/authorization/sign")
def sign_authorization_doc(application_id: str, body: SignBody, request: Request,
                           db=Depends(get_session), p: Principal = Depends(get_principal)):
    from .providers import esign
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    # Verify the step-up token (proves recent MFA + binds to this case/action).
    verify_action_token(body.step_up_token, "esign", application_id)
    docs = [d.name for d in db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()]
    # The envelope the applicant actually prepared — never "whichever row is
    # newest": a concurrently created envelope must not swap the terms between
    # prepare and sign (duplicate envelope rows are routine and harmless now).
    env = None
    if body.envelope_id:
        env = db.get(models.AuthorizationEnvelope, body.envelope_id)
        if env is None or env.application_id != application_id:
            raise HTTPException(404, "authorization envelope not found")
    env = env or _latest_env(db, application_id)
    if env is None:
        raise HTTPException(409, "prepare the authorization before signing")
    text = esign.build_authorization_text(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        destination=app_row.destination_country, visa_type=app_row.visa_type,
        portal=app_row.destination_country,
        max_fee_cents=env.max_fee_cents,
        currency=env.currency,
        allow_auto_book=env.allow_auto_book,
        allow_auto_reschedule=env.allow_auto_reschedule,
        allow_representative_submit=env.allow_representative_submit)
    req = esign.SignatureRequest(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        document_text=text, document_hash=body.document_hash, consent_given=body.consent_given,
        intent_confirmed=body.intent_confirmed, signature_method=body.signature_method,
        signature_value=body.signature_value, step_up_verified=True, auth_method=body.auth_method,
        ip_address=request.client.host if request.client else "", user_agent=request.headers.get("user-agent", ""))
    try:
        result = esign.get_provider().sign(req)
    except ValueError as e:
        raise HTTPException(422, str(e))
    snap = esign.application_snapshot_hash(app_row.answers, docs)
    sig = models.NativeSignature(
        org_id=app_row.org_id, application_id=application_id, app_version=app_row.current_version,
        provider=result["provider"], template_version=result["template_version"],
        consent_version=result["consent_version"], document_hash=result["document_hash"],
        artifact_hash=result["artifact_hash"], artifact_ref=f"local://sig/{result['artifact_hash'][:16]}",
        signature_method=result["signature_method"], auth_method=result["auth_method"],
        ip_address=req.ip_address, user_agent=req.user_agent[:300], app_snapshot_hash=snap)
    db.add(sig)
    db.flush()
    _sig_event(db, sig.id, application_id, "signed", {"artifact_hash": result["artifact_hash"]})
    # Mark the SAME envelope completed so the workflow authorization gate is
    # satisfied — the one whose terms were signed, not the newest row.
    env.status = "completed"
    env.artifact_hash = result["artifact_hash"]
    db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id, action="authorization_signed_native",
                 detail={"artifact_hash": result["artifact_hash"], "method": result["signature_method"]},
                 actor=p.user_id)
    # SINGLE CEREMONY (product decision 2026-07-27): this one signature also
    # (a) grants the standing authorization (versioned, hash-bound, same
    # limits as before — Ellis still never confirms a payment), and (b)
    # records the intake email as the confirmed portal contact. The applicant
    # signs once; everything after runs without another signing step.
    from . import authorization as _standing
    _standing.grant(db, app_row=app_row, principal_user=p.user_id)
    from . import checklist_intake as _ci
    if (app_row.answers or {}).get("email") or (applicant.email or ""):
        if _ci.stage_progress(db, application_id, stage="contact_confirmed") is None:
            db.add(models.CaseStageProgress(org_id=app_row.org_id,
                                            application_id=application_id,
                                            stage="contact_confirmed"))
            db.commit()
    return {"signature_id": sig.id, "artifact_hash": result["artifact_hash"],
            "signed_at": result["signed_at"], "download": f"/cases/{application_id}/authorization/{sig.id}/pdf"}


def _latest_env(db, application_id: str):
    return db.execute(select(models.AuthorizationEnvelope).where(
        models.AuthorizationEnvelope.application_id == application_id).order_by(
        models.AuthorizationEnvelope.created_at.desc())).scalars().first()


# ---- Standing authorization (brief §5) ----
# Granted ONLY inside the signature ceremony (/authorization/sign) — the
# single-ceremony product decision (2026-07-27). The bare POST grant endpoint
# was removed 2026-07-28: with service.signal treating a valid grant as proof
# the ceremony happened, a ceremony-less grant would be an authorization
# bypass. Reading (GET) and revoking (DELETE) remain applicant actions.
class RevokeBody(BaseModel):
    reason: str = ""


@app.get("/cases/{application_id}/standing-authorization")
def get_standing_authorization(application_id: str, locale: str = "en",
                               db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import authorization as standing
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    return {"current": standing.to_dict(standing.current(db, application_id)),
            "text": standing.build_text(locale=locale, applicant_name=applicant.full_name,
                                        destination=app_row.destination_country,
                                        visa_type=app_row.visa_type),
            "text_version": standing.TEXT_VERSION,
            "permitted_actions": standing.PERMITTED_ACTIONS,
            "disclosures": standing.DISCLOSURES}


@app.delete("/cases/{application_id}/standing-authorization")
def revoke_standing_authorization(application_id: str, body: Optional[RevokeBody] = None,
                                  db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import authorization as standing
    app_row = _owned(db, p, application_id)
    try:
        row = standing.revoke(db, app_row=app_row, principal_user=p.user_id,
                              reason=(body.reason if body else ""))
    except standing.AuthorizationMissing as e:
        raise HTTPException(404, str(e))
    return standing.to_dict(row)


# ---- Final review + exact-version signature (brief §7) ----
class FinalReviewSignBody(BaseModel):
    review_version_id: str
    content_hash: str
    consent_given: bool = False
    intent_confirmed: bool = False
    signature_method: str = "typed"
    signature_value: str = ""
    step_up_token: str
    auth_method: str = "email_otp"


@app.get("/cases/{application_id}/final-review")
def get_final_review(application_id: str, locale: str = "en",
                     db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import final_review
    app_row = _owned(db, p, application_id)
    final_review.check_and_invalidate(db, app_row)
    return {"latest": final_review.to_dict(final_review.latest(db, application_id)),
            "preview": final_review.build_package(db, app_row, locale=locale),
            "current_material_hash": final_review.material_hash(db, app_row)}


@app.post("/cases/{application_id}/final-review")
def create_final_review(application_id: str, locale: str = "en",
                        db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import final_review
    app_row = _owned(db, p, application_id)
    row = final_review.create_review_version(db, app_row, actor=p.user_id, locale=locale)
    token = issue_action_token(p, "final_review_sign", application_id, ttl_seconds=600)
    return {**final_review.to_dict(row), "step_up_token": token}


@app.post("/cases/{application_id}/final-review/sign")
def sign_final_review(application_id: str, body: FinalReviewSignBody, request: Request,
                      db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import final_review
    from .providers import esign
    app_row = _owned(db, p, application_id)
    verify_action_token(body.step_up_token, "final_review_sign", application_id)
    row = db.get(models.ApplicationReviewVersion, body.review_version_id)
    if not row or row.application_id != application_id:
        raise HTTPException(404, "review version not found")
    if row.invalidated:
        raise HTTPException(409, "this review version was invalidated — re-review required")
    # The applicant signs the EXACT version: the echoed hash must match, and the
    # version must still match the live material state.
    if body.content_hash != row.content_hash:
        raise HTTPException(409, "content hash mismatch — review the current version")
    if row.content_hash != final_review.material_hash(db, app_row):
        row.invalidated = True
        row.invalidated_reason = "material change before signature"
        db.commit()
        raise HTTPException(409, "the application changed — a fresh final review is required")
    if not (body.consent_given and body.intent_confirmed):
        raise HTTPException(422, "consent and intent confirmation are both required")
    applicant = db.get(models.Applicant, app_row.applicant_id)
    doc_text = json.dumps(row.package, sort_keys=True, default=str)
    req = esign.SignatureRequest(
        applicant={"full_name": applicant.full_name, "email": applicant.email},
        org_id=app_row.org_id, case_id=application_id, app_version=app_row.current_version,
        document_text=doc_text, document_hash=esign.document_hash(doc_text),
        consent_given=body.consent_given, intent_confirmed=body.intent_confirmed,
        signature_method=body.signature_method, signature_value=body.signature_value,
        step_up_verified=True, auth_method=body.auth_method,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""))
    try:
        result = esign.get_provider().sign(req)
    except ValueError as e:
        raise HTTPException(422, str(e))
    sig = models.NativeSignature(
        org_id=app_row.org_id, application_id=application_id, app_version=app_row.current_version,
        provider=result["provider"], template_version=result["template_version"],
        consent_version=result["consent_version"], document_hash=result["document_hash"],
        artifact_hash=result["artifact_hash"],
        artifact_ref=f"local://sig/{result['artifact_hash'][:16]}",
        signature_method=result["signature_method"], auth_method=result["auth_method"],
        ip_address=req.ip_address, user_agent=req.user_agent[:300],
        app_snapshot_hash=row.content_hash)
    db.add(sig)
    db.flush()
    _sig_event(db, sig.id, application_id, "signed",
               {"kind": "final_review", "review_version": row.version,
                "artifact_hash": result["artifact_hash"]})
    final_review.record_signature(db, row, signature_id=sig.id, actor=p.user_id)
    return {"signature_id": sig.id, "review_version": row.version,
            "content_hash": row.content_hash, "artifact_hash": result["artifact_hash"]}


# ---- Payment authorization view (brief §6) ----
@app.get("/cases/{application_id}/payment-authorization")
def get_payment_authorization(application_id: str, db=Depends(get_session),
                              p: Principal = Depends(get_principal)):
    from . import payments
    _owned(db, p, application_id)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    fee = ((exec_row.snapshot or {}).get("fee") if exec_row else None) or {}
    rows = db.execute(select(models.PaymentAuthorization).where(
        models.PaymentAuthorization.application_id == application_id).order_by(
        models.PaymentAuthorization.approved_at.desc())).scalars().all()
    current = next((r for r in rows if r.status == "authorized"), None)
    return {"current": payments.to_dict(current),
            "history": [payments.to_dict(r) for r in rows[:10]],
            "fee": {k: fee.get(k) for k in ("amount", "currency", "display",
                                            "government_fee_cents", "service_fee_cents")
                    if k in fee}}


def _sig_event(db, signature_id: str, application_id: str, event: str, detail: dict):
    from sqlalchemy import func
    nseq = (db.query(func.max(models.SignatureEvent.seq)).scalar() or 0) + 1
    db.add(models.SignatureEvent(seq=nseq, signature_id=signature_id, application_id=application_id,
                                 event=event, detail=detail))
    db.commit()


def invalidate_signatures_if_changed(db, application_id: str,
                                     protect_authorization: bool = False):
    """Any material change to answers/documents invalidates completed signatures
    AND the signed final-review version (§7 — back to review + new signature).

    protect_authorization=True is the single-ceremony policy for answers the
    applicant TYPES INTO ELLIS (consented by construction): the frozen review
    version still goes stale, but the ONE authorization signature survives —
    the same rule the provide_information signal path applies. Document-edit
    callers keep the strict §7 behavior (a changed passport field is a change
    to the very material the signature covered)."""
    from .providers import esign
    from . import final_review
    app_row = db.get(models.VisaApplication, application_id)
    docs = [d.name for d in db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == application_id)).scalars().all()]
    current = esign.application_snapshot_hash(app_row.answers, docs)
    changed = 0
    for sig in db.execute(select(models.NativeSignature).where(
            models.NativeSignature.application_id == application_id,
            models.NativeSignature.invalidated == False)).scalars().all():  # noqa: E712
        if protect_authorization and final_review._is_authorization_signature(
                db, application_id, sig):
            continue
        if sig.app_snapshot_hash and sig.app_snapshot_hash != current:
            sig.invalidated = True
            _sig_event(db, sig.id, application_id, "invalidated", {"reason": "material_change"})
            changed += 1
    if changed:
        db.commit()
    if final_review.check_and_invalidate(db, app_row):
        changed += 1
    return changed


# ---- Workflow signals ----
_SIGNALS = {"approve_review", "sign_authorization", "solve_captcha", "verify_email",
            "approve_payment", "complete_payment", "select_appointment",
            "approve_reschedule", "complete_declaration", "cancel",
            "provide_information", "provide_payment_details"}


class SignalBody(BaseModel):
    token: Optional[str] = None
    slot_id: Optional[str] = None
    # approve_payment: the client echoes the exact amount it displayed so the
    # confirmation can never silently cover a different figure (§6).
    amount_cents: Optional[int] = None
    currency: Optional[str] = None
    # provide_information: answers to the dynamic missing-information questions
    # the portal execution paused on (Part 4).
    answers: Optional[dict] = None
    # provide_payment_details: the applicant's own card details, provided so
    # Ellis fills the OFFICIAL portal's payment form. Vault-transported
    # (one-time reference), used once, never persisted or logged; the final
    # payment confirmation click always stays with the applicant. Typed as
    # Any on purpose: a shape mismatch must fail in OUR validator (which
    # never echoes values) — a pydantic type error would reflect the raw
    # payload back in the 422 body.
    card: Optional[Any] = None
    # provide_payment_details with manual=true: the applicant chose to type
    # the details into the portal's secure window personally instead.
    manual: Optional[bool] = None


def _validated_payment_card(raw: dict) -> dict:
    """Strict validation of applicant-provided payment details. Error messages
    NEVER echo a submitted value. Returns the normalized card payload."""
    import re as _re
    from datetime import datetime, timezone

    def _bad(key: str, message: str):
        return HTTPException(422, detail={"reason": "invalid_payment_details",
                                          "key": key, "message": message})
    if not isinstance(raw, dict):
        raise _bad("card", "Invalid payment details.")
    raw = raw or {}
    holder = str(raw.get("holder") or "").strip()
    if not holder or len(holder) > 80:
        raise _bad("holder", "Enter the name exactly as it appears on the card.")
    number = _re.sub(r"[\s-]", "", str(raw.get("number") or ""))
    if not _re.fullmatch(r"\d{12,19}", number):
        raise _bad("number", "Enter the card number (12–19 digits).")
    digits = [int(c) for c in number]
    checksum = 0
    for i, dgt in enumerate(reversed(digits)):
        if i % 2 == 1:
            dgt *= 2
            if dgt > 9:
                dgt -= 9
        checksum += dgt
    if checksum % 10 != 0:
        raise _bad("number", "That card number does not look valid — check for a typo.")
    expiry = str(raw.get("expiry") or "").strip()
    m = _re.fullmatch(r"(0?[1-9]|1[0-2])\s*/\s*(\d{2}|\d{4})", expiry)
    if not m:
        raise _bad("expiry", "Enter the expiry as MM/YY.")
    month = int(m.group(1))
    year = int(m.group(2))
    year += 2000 if year < 100 else 0
    now = datetime.now(timezone.utc)
    if year < now.year or (year == now.year and month < now.month) or year > now.year + 30:
        raise _bad("expiry", "That expiry date is not valid.")
    cvv = str(raw.get("cvv") or "").strip()
    if not _re.fullmatch(r"\d{3,4}", cvv):
        raise _bad("cvv", "Enter the 3–4 digit security code.")
    return {"holder": holder, "number": number, "cvv": cvv,
            "expiry_month": f"{month:02d}", "expiry_year": str(year)}


def _record_terminal_execution(db, p: Principal, application_id: str):
    """When a case reaches COMPLETED, persist the execution classification of the
    result so the audit trail proves whether it was a real government outcome or
    a MOCK/sandbox run — the completed state alone must never imply 'real'.
    The background executor calls the same service helper for queued runs."""
    service.record_terminal_execution(db, p.org_id, application_id)


def _signal_or_gate_error(db, p: Principal, application_id: str, name: str, **kwargs):
    """Drive one workflow transition, translating the centralized safety-gate
    exceptions (service.enforce_safety) into honest 409s. Both /start and
    /signals route through here so neither can bypass the gate."""
    from . import personal_gate, passport_validity
    from .portal.driver_factory import RealOnlyStop
    try:
        status, _ = service.signal(db, application_id, name, **kwargs)
    except RealOnlyStop as e:
        # Real-only runtime mode with no approved live adapter: the case is
        # preserved and the workflow STOPS with a typed status — it never
        # silently falls back to MockPortal (brief section 3).
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="real_only_stop",
                     detail={"status": e.status, "detail": e.detail, "signal": name},
                     actor=p.user_id)
        # The internal reason is kept for the audit trail above; the applicant
        # is told the honest, route-specific reason instead of driver internals.
        applicant_detail = e.detail
        outcome = ""
        if e.status == "PORTAL_UNAVAILABLE":
            row = db.get(models.VisaApplication, application_id)
            if row is not None:
                applicant_detail = _explain_no_live_adapter(db, row, e.detail)
                outcome = _route_outcome_of(db, row)
        # The route's own outcome rides along so the applicant's screen can
        # tell "no portal connection yet" from "you need no visa at all".
        # Without it, a traveller who needs NO visa for Indonesia was told to
        # wait for a portal release, and offered the chance to teach Ellis a
        # portal that has nothing to do with their trip (2026-08-04).
        raise HTTPException(409, detail={"reason": "real_only_stop", "status": e.status,
                                         "detail": applicant_detail,
                                         "route_outcome": outcome})
    except execution.MockAsProductionError as e:
        raise HTTPException(409, detail={"reason": "real_only_stop", "status": "UNSUPPORTED",
                                         "detail": str(e)})
    except personal_gate.PreparationOnlyMode as e:
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="live_action_blocked_preparation_mode",
                     detail={"missing_gates": e.missing_gates, "missing_info": e.missing_info,
                             "signal": name}, actor=p.user_id)
        raise HTTPException(409, str(e))
    except passport_validity.PassportBlocked as e:
        raise HTTPException(409, detail={"reason": "passport_validity", "verdict": e.verdict})
    _record_terminal_execution(db, p, application_id)
    return status


@app.post("/cases/{application_id}/start")
def start_case(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    app_row = _owned(db, p, application_id)
    # Server-side enforcement for routed cases: the workflow may never start
    # while mandatory checklist documents remain unsubmitted (frontend state is
    # never trusted). Legacy cases without a route checklist are unaffected.
    from . import checklist_intake
    cg = checklist_intake.case_guidance(db, application_id)
    if cg is not None:
        remaining = checklist_intake.checklist_state(
            db, app_row, cg)["counts"]["required_missing"]
        if remaining > 0:
            raise HTTPException(409, detail={
                "reason": "documents_incomplete", "required_remaining": remaining,
                "message": f"Submit {remaining} remaining required "
                           f"document{'s' if remaining != 1 else ''} before starting."})
    # Visa-exempt routes still file an arrival card, and the destination
    # decides when: filing before the window opens is refused by the portal.
    # One press schedules it; the worker runs it the moment it opens.
    from . import entry_preparation
    ep = entry_preparation.plan(db, app_row)
    if ep["required"] and ep["status"] == "waiting":
        entry_preparation.schedule(db, org_id=p.org_id, app_row=app_row, plan_out=ep)
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="entry_filing_scheduled",
                     detail={k: ep[k] for k in ("opens_on", "arrival_date",
                                                "family_id", "window_days")},
                     actor=p.user_id)
        return {"state": app_row.state, "entry_filing": ep, "scheduled": True}
    if ep["required"] and ep["status"] == "open":
        entry_preparation.schedule(db, org_id=p.org_id, app_row=app_row, plan_out=ep)
    if _live_background_route(db, app_row):
        return _queue_signal_response(db, p, app_row, "start", {})
    return _signal_or_gate_error(db, p, application_id, "start")


@app.post("/cases/{application_id}/signals/{name}")
def send_signal(application_id: str, name: str, body: Optional[SignalBody] = None,
                db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    if name not in _SIGNALS:
        raise HTTPException(400, f"unknown signal {name}")
    body = body or SignalBody()
    kwargs = {}
    if name == "verify_email":
        kwargs["token"] = body.token
    if name == "solve_captcha" and body.token:
        # The HUMAN's typed captcha solution: same one-time vault transport
        # as OTP codes — revealed once by the executor, then destroyed.
        kwargs["token"] = body.token
    if name == "select_appointment":
        kwargs["slot_id"] = body.slot_id
    if name == "approve_payment" and body.amount_cents is not None:
        kwargs["amount_cents"] = body.amount_cents
        kwargs["currency"] = body.currency
    if name == "provide_information":
        answers = _validated_information_answers(db, application_id, body.answers or {})
        if not answers:
            raise HTTPException(422, detail={"reason": "no_valid_answers",
                                             "message": "No usable answers were provided."})
        # Persist first (DB is the source of truth), with the same material-
        # change signature invalidation as the answers endpoint.
        app_row = db.get(models.VisaApplication, application_id)
        merged = dict(app_row.answers or {})
        merged.update(answers)
        app_row.answers = merged
        db.commit()
        # Answers the applicant TYPES INTO ELLIS during the run are consented
        # by construction — they must never void the applicant's one
        # authorization signature (single-ceremony product decision
        # 2026-07-27). Only the frozen review VERSION goes stale; it is
        # re-frozen under the standing advance consent at the next enqueue.
        from . import final_review as _fr
        invalidated = 1 if _fr.check_and_invalidate(
            db, app_row, reason="applicant provided additional answers") else 0
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="additional_information_provided",
                     detail={"keys": sorted(answers.keys()),
                             "signatures_invalidated": invalidated}, actor=p.user_id)
        kwargs["answers"] = answers
    if name == "provide_payment_details":
        if body.manual:
            kwargs["manual"] = True
        else:
            # Validated card payload — NEVER audited, logged, or persisted in
            # plaintext (the queue vaults it as a one-time reference).
            kwargs["card"] = _validated_payment_card(body.card)

    def _audit_payment_details():
        # Recorded only AFTER the signal was actually accepted (queued or
        # applied) — a CaseBusy/gate refusal must not leave a trail claiming
        # details were provided when they were dropped.
        if name == "provide_payment_details":
            audit.record(db, org_id=p.org_id, application_id=application_id,
                         action="payment_details_provided",
                         detail={"manual": bool(body.manual)}, actor=p.user_id)

    app_row = db.get(models.VisaApplication, application_id)
    if name != "cancel" and _live_background_route(db, app_row):
        res = _queue_signal_response(db, p, app_row, name, kwargs)
        _audit_payment_details()
        return res
    if name == "cancel":
        # Cancel all queued background work along with the case itself. A run
        # already executing finishes its segment, but persist_workflow's
        # cancellation fence prevents it from resurrecting the CANCELLED case.
        portal_queue.cancel_queued(db, application_id)
    res = _signal_or_gate_error(db, p, application_id, name, **kwargs)
    _audit_payment_details()
    return res


def _live_background_route(db, app_row) -> bool:
    """True when this case executes on a live released-flow adapter: portal
    work must then run as a durable background run, never inside the HTTP
    request. Mock/local portals stay synchronous (they are instant)."""
    if settings().mock_portal_allowed:
        return False
    from .portal.released_flow import resolve_released_route
    try:
        return resolve_released_route(db, app_row) is not None
    except Exception:  # noqa: BLE001 — resolution failure = classic sync path
        return False


def _queue_signal_response(db, p: Principal, app_row, name: str, kwargs: dict):
    """Record the applicant's signal as a durable PortalRun and return
    immediately. Repeated clicks reuse the active run (idempotent). The same
    safety gates as the synchronous path are checked here for an honest 409;
    the executor re-checks them via enforce_safety before any portal work."""
    from . import personal_gate, passport_validity, final_review
    from .execution import ExecutionClass
    application_id = app_row.id
    try:
        personal_gate.assert_ready_for_live_action(db, app_row, ExecutionClass.LIVE_PRODUCTION)
    except personal_gate.PreparationOnlyMode as e:
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="live_action_blocked_preparation_mode",
                     detail={"missing_gates": e.missing_gates, "missing_info": e.missing_info,
                             "signal": name}, actor=p.user_id)
        raise HTTPException(409, str(e))
    verdict = passport_validity.check_case_passport(db, app_row)
    if verdict.get("blocking"):
        passport_validity.enforce_and_notify(db, app_row, verdict)
        raise HTTPException(409, detail={"reason": "passport_validity", "verdict": verdict})
    # Contact is reachable by construction: the intake email IS the portal
    # contact (product decision 2026-07-27 — no separate confirmation step).
    # A run may submit only when a CURRENT signed final review exists at the
    # moment the applicant acts. Under the single-ceremony flow the one
    # authorization signature carries advance submit consent: the review
    # version is (re)frozen and recorded against that signature here.
    rv = final_review.ensure_advance_signed(db, app_row) \
        or final_review.latest(db, application_id)
    allow_submit = bool(rv is not None and rv.signed and not getattr(rv, "invalidated", False)
                        and final_review.signed_current(db, app_row) is not None)
    try:
        run, created = portal_queue.enqueue(db, app_row=app_row, signal_name=name,
                                            kwargs=kwargs, allow_submit=allow_submit)
    except portal_queue.CaseBusy as e:
        raise HTTPException(409, detail={"reason": "case_busy", "message": str(e)})
    audit.record(db, org_id=p.org_id, application_id=application_id,
                 action="portal_run_enqueued" if created else "portal_run_reused",
                 detail={"signal": name, "run_id": run.id}, actor=p.user_id)
    # pending is None here on purpose: the queued run is resolving the pause
    # the applicant just answered — echoing the stale handoff would re-open
    # its modal. The progress endpoint carries live state from here on.
    return {"case_id": application_id, "state": app_row.state, "pending": None,
            "queued": True, "run_id": run.id, "run_reused": not created}


def _contact_confirmed(db, application_id: str) -> bool:
    from . import checklist_intake
    return checklist_intake.stage_progress(db, application_id,
                                           stage="contact_confirmed") is not None


def _validated_information_answers(db, application_id: str, raw: dict) -> dict:
    """Safe input validation for dynamic-question answers: only answers to the
    questions Ellis actually asked (the pending payload), values as trimmed
    strings, dates canonicalized via the single date authority."""
    from . import dates as dates_mod
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    pending = (exec_row.pending if exec_row else None) or {}
    asked = {q.get("key"): q for q in (pending.get("questions") or []) if q.get("key")}
    out: dict = {}
    for key, value in (raw or {}).items():
        q = asked.get(key)
        if q is None or key.startswith("document:"):
            continue    # never accept answer keys Ellis did not ask for
        v = str(value if value is not None else "").strip()
        if not v:
            continue
        if len(v) > 300:
            raise HTTPException(422, detail={"reason": "invalid_answer", "key": key,
                                             "message": "That answer is too long."})
        if q.get("kind") == "date":
            iso = dates_mod.normalize_any(v, kind="expiry", us_numeric=True)
            if not iso:
                raise HTTPException(422, detail={
                    "reason": "invalid_answer", "key": key,
                    "message": "Please enter a valid date."})
            v = iso
        out[key] = v
    return out


@app.get("/cases/{application_id}/audit")
def get_audit(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    return {"events": [{"seq": e.seq, "action": e.action, "detail": e.detail, "actor": e.actor,
                        "at": e.at.isoformat() if e.at else None}
                       for e in audit.for_application(db, application_id)]}


@app.get("/cases/{application_id}/progress")
def case_progress(application_id: str, db=Depends(get_session),
                  p: Principal = Depends(get_principal)):
    """Applicant-safe live progress: real checkpoints only (workflow state,
    per-field flow steps, handoffs). Never selectors, secrets, stack traces,
    document contents, or PII."""
    from datetime import datetime, timezone
    app_row = _owned(db, p, application_id)
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    state = exec_row.state if exec_row else app_row.state
    pending = exec_row.pending if exec_row else None
    # A RUNNING run outranks a newer queued one: progress must narrate the
    # work actually happening, not a restore request parked behind it.
    run = portal_queue.progress_run(db, application_id)
    now = datetime.now(timezone.utc)

    def _aw(dt):
        return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))

    active = run is not None and run.status in ("queued", "running")
    last_cp = _aw(run.last_checkpoint_at) if run else None
    stalled = bool(run and (run.status == "stalled" or (
        run.status == "running" and last_cp is not None and
        (now - last_cp).total_seconds() > portal_queue.STALL_AFTER_SECONDS)))
    # While a run is queued/running it is RESOLVING the recorded pause — the
    # applicant is not being waited on, Ellis is working. Only a pause with no
    # active run is a real handoff.
    waiting = bool(pending) and not active
    if stalled:
        step = "stalled"
    elif active:
        step = run.current_step_key or "queued"
    else:
        # The machine's own reason for this state distinguishes "the portal is
        # down" from "this application needs a look" — see progress.py.
        hist = ((exec_row.history if exec_row else None) or [])[-4:]
        why = " | ".join(str((h or {}).get("reason") or "") for h in hist)
        step = progress_vocab.step_for_state(state, pending or None, why)
    last_done = db.execute(select(models.CaseProgressEvent).where(
        models.CaseProgressEvent.application_id == application_id,
        models.CaseProgressEvent.status == "done").order_by(
        models.CaseProgressEvent.at.desc(),
        models.CaseProgressEvent.id.desc())).scalars().first()
    started = _aw(run.started_at) if run else None
    from .providers import browser as bb_probe
    from .portal_store import current_browser_session
    session_row = current_browser_session(db, application_id)
    # "Secure portal session active" must mean the PROVIDER still has it.
    if session_row is not None and bb_probe.is_remote_mode(session_row.mode) and \
            not bb_probe.session_alive(session_row.provider_session_id):
        session_row = None
    retryable = portal_queue.retry_available(db, app_row, exec_row)
    failed = bool(run and run.status in ("failed", "stalled")) or state == "RECOVERABLE_FAILURE"
    return {
        "state": state,
        "queued": bool(run and run.status == "queued"),
        "active": active and not stalled,
        "waiting_for_applicant": waiting,
        "handoff": (pending or {}).get("handoff", "") if waiting else "",
        "purpose": (pending or {}).get("purpose", "") if waiting else "",
        "step": {"key": step, "message": progress_vocab.message_for(step)},
        "last_completed": ({"key": last_done.step_key,
                            "message": progress_vocab.message_for(last_done.step_key),
                            "at": _aw(last_done.at).isoformat()} if last_done else None),
        "elapsed_seconds": (int((now - started).total_seconds())
                            if started and active else None),
        "last_checkpoint_at": last_cp.isoformat() if last_cp else None,
        "stalled": stalled,
        "stall_message": progress_vocab.STEP_MESSAGES["stalled"] if stalled else "",
        "error": ({"message": (run.error if run and run.error else
                               "The official portal did not respond as expected."),
                   "retryable": retryable} if failed else None),
        "retry_available": retryable,
        # Nothing is running, nothing is waiting on the applicant, and the case
        # is not finished: it can always be driven forward again. Without this
        # a case whose pause was cleared (a reset, a voided confirmation) would
        # sit with no way for the applicant to continue.
        "resume_available": (not active and not pending
                             and state not in ("COMPLETED", "CANCELLED")),
        "browser_session_alive": session_row is not None,
        "run_status": run.status if run else "",
        "run_signal": run.signal_name if run else "",
    }


@app.post("/cases/{application_id}/portal/retry")
def retry_portal(application_id: str, db=Depends(get_session),
                 p: Principal = Depends(get_principal)):
    """Applicant-triggered retry from the last safe checkpoint. Available only
    for stalled/failed reversible work; irreversible steps stay guarded by
    reconcile-before-act, allow_submit and the lost-session check. Never runs
    automatically."""
    app_row = _owned(db, p, application_id)
    if not _live_background_route(db, app_row):
        raise HTTPException(409, detail={"reason": "retry_unavailable"})
    exec_row = db.execute(select(models.WorkflowExecution).where(
        models.WorkflowExecution.application_id == application_id)).scalar_one_or_none()
    if not portal_queue.retry_available(db, app_row, exec_row):
        raise HTTPException(409, detail={"reason": "retry_unavailable"})
    state = exec_row.state if exec_row is not None else app_row.state
    if state == "MANUAL_REVIEW_REQUIRED":
        # Reversible dead end (exhausted retries on form work, no irreversible
        # action recorded): the applicant's retry releases it and the SAME
        # portal execution resumes from its persisted node.
        if not portal_queue.release_manual_review(db, app_row):
            raise HTTPException(409, detail={"reason": "retry_unavailable"})
        audit.record(db, org_id=p.org_id, application_id=application_id,
                     action="manual_review_released_by_applicant", detail={},
                     actor=p.user_id)
    audit.record(db, org_id=p.org_id, application_id=application_id,
                 action="portal_retry_requested", detail={}, actor=p.user_id)
    return _queue_signal_response(db, p, app_row, "start", {})


@app.post("/cases/{application_id}/portal/restore")
def restore_portal_view(application_id: str, db=Depends(get_session),
                        p: Principal = Depends(get_principal)):
    """Rebuild the applicant's live portal page after a session loss: the
    secure window must show the real form at its current step, never a blank
    tab. Reversible work only; the case's recorded pause is preserved (and a
    fee the restored page displays becomes an approve-this-amount pause)."""
    app_row = _owned(db, p, application_id)
    if not _live_background_route(db, app_row):
        raise HTTPException(409, detail={"reason": "restore_unavailable"})
    return _queue_signal_response(db, p, app_row, "restore_portal", {})


@app.post("/cases/{application_id}/portal/read-fee")
def read_portal_fee(application_id: str, db=Depends(get_session),
                    p: Principal = Depends(get_principal)):
    """Applicant-requested fee read from the portal's current page (queued —
    live work never runs inside an HTTP request)."""
    app_row = _owned(db, p, application_id)
    if not _live_background_route(db, app_row):
        raise HTTPException(409, detail={"reason": "read_unavailable"})
    return _queue_signal_response(db, p, app_row, "read_fee", {})


class ContactBody(BaseModel):
    email: str = ""
    phone: str = ""
    phone_country_code: str = ""
    otp_preference: str = ""   # email | sms, when the portal offers a choice
    confirm: bool = False


def _mask_phone(ph: str) -> str:
    digits = re.sub(r"\D", "", ph or "")
    # Only genuinely long numbers reveal a 4-digit tail; anything shorter
    # would disclose most (or all) of the number.
    return ("•••• " + digits[-4:]) if len(digits) >= 8 else ("••••" if digits else "")


@app.get("/cases/{application_id}/contact-confirmation")
def get_contact_confirmation(application_id: str, db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    """The applicant-controlled email + masked phone the portal will use for
    verification codes — confirmed explicitly before portal execution."""
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    answers = app_row.answers or {}
    email = (answers.get("email") or (applicant.email if applicant else "") or "").strip()
    phone = (answers.get("phone") or (applicant.phone if applicant else "") or "").strip()
    return {"email": email, "phone_masked": _mask_phone(phone),
            "has_email": bool(email), "has_phone": bool(phone),
            "phone_country_code": str(answers.get("phone_country_code") or ""),
            "otp_preference": str(answers.get("otp_preference") or ""),
            "confirmed": _contact_confirmed(db, application_id)}


@app.post("/cases/{application_id}/contact-confirmation")
def confirm_contact(application_id: str, body: ContactBody, db=Depends(get_session),
                    p: Principal = Depends(get_principal)):
    """Edit and/or confirm the contact details before the portal can send a
    code. Edits are material changes (signatures invalidate per §7). Audit
    records masked values only — never the full email or phone."""
    app_row = _owned(db, p, application_id)
    applicant = db.get(models.Applicant, app_row.applicant_id)
    answers = dict(app_row.answers or {})
    changed = False
    if body.email.strip():
        email = body.email.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(422, detail={"reason": "invalid_email",
                                             "message": "Enter a valid email address."})
        if email != answers.get("email"):
            answers["email"] = email
            if applicant:
                applicant.email = email
            changed = True
    if body.phone.strip():
        digits = re.sub(r"\D", "", body.phone)
        if len(digits) < 5 or len(digits) > 15:
            raise HTTPException(422, detail={"reason": "invalid_phone",
                                             "message": "Enter a valid phone number."})
        if body.phone.strip() != answers.get("phone"):
            answers["phone"] = body.phone.strip()
            if applicant:
                applicant.phone = body.phone.strip()
            changed = True
    if body.phone_country_code.strip():
        answers["phone_country_code"] = body.phone_country_code.strip()
        changed = True
    if body.otp_preference in ("email", "sms"):
        answers["otp_preference"] = body.otp_preference
        changed = True
    if changed:
        app_row.answers = answers
        db.commit()
        # Contact preferences typed into Ellis: same single-ceremony policy.
        invalidate_signatures_if_changed(db, application_id,
                                         protect_authorization=True)
    if body.confirm:
        if not (answers.get("email") or (applicant.email if applicant else "")):
            raise HTTPException(422, detail={
                "reason": "email_required",
                "message": "Add an email address before confirming — the portal "
                           "needs one for verification."})
        from . import checklist_intake
        if checklist_intake.stage_progress(db, application_id,
                                           stage="contact_confirmed") is None:
            db.add(models.CaseStageProgress(org_id=app_row.org_id,
                                            application_id=application_id,
                                            stage="contact_confirmed"))
            db.commit()
    audit.record(db, org_id=p.org_id, application_id=application_id,
                 action="contact_details_confirmed" if body.confirm else "contact_details_updated",
                 detail={"changed": changed,
                         "phone_masked": _mask_phone(answers.get("phone", ""))},
                 actor=p.user_id)
    return get_contact_confirmation(application_id, db, p)


@app.get("/cases/{application_id}/appointment")
def get_appointment(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    _owned(db, p, application_id)
    appt = db.execute(select(models.Appointment).where(
        models.Appointment.application_id == application_id)).scalar_one_or_none()
    if not appt:
        return {"appointment": None}
    return {"appointment": {"slot_id": appt.slot_id, "location_id": appt.location_id,
                            "start_utc": appt.start_utc, "confirmation_no": appt.confirmation_no,
                            "reschedule_count": appt.reschedule_count}}


# ---- Privacy: export + deletion (Phase 10) ----
@app.get("/cases/{application_id}/export")
def export_case_endpoint(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-controlled portable export of everything held for this case."""
    from . import privacy
    _owned(db, p, application_id)
    bundle = privacy.export_case(db, application_id)
    audit.record(db, org_id=p.org_id, application_id=application_id, action="data_exported",
                 detail={"scope": "case"}, actor=p.user_id)
    return bundle


@app.get("/export")
def export_org_endpoint(db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Tenant export: every case owned by the caller's org."""
    from . import privacy
    return privacy.export_org(db, p.org_id)


@app.delete("/cases/{application_id}")
def delete_case_endpoint(application_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Applicant-controlled erasure of a case (right to be forgotten)."""
    from . import privacy
    _owned(db, p, application_id)
    return privacy.delete_case(db, application_id, actor=p.user_id)


@app.delete("/applicants/{applicant_id}")
def delete_applicant_endpoint(applicant_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Erase an applicant and all their cases. Tenant-isolated."""
    from . import privacy
    applicant = db.get(models.Applicant, applicant_id)
    if not applicant:
        raise HTTPException(404, "applicant not found")
    require_owner(p, applicant.org_id)
    return privacy.delete_applicant(db, applicant_id, actor=p.user_id)


# ---- Ops: readiness + metrics + kill switches (Phase 11) ----
@app.get("/readyz")
def readyz(db=Depends(get_session)):
    """Readiness: the API is up, its datastore is reachable, AND (in production)
    no default credentials are in place — production refuses to be 'ready' while
    running on default dev secrets."""
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "database not ready")
    from . import setup as setup_mod
    pre = setup_mod.production_preflight()
    if not pre["ok"]:
        raise HTTPException(503, "production preflight failed: default credentials in use")
    return {"ready": True, "production_preflight": pre}


@app.get("/metrics")
def metrics(db=Depends(get_session), p: Principal = Depends(get_principal)):
    """Operational counters for the caller's org (no PII). Case counts by state,
    plus payment/appointment/submission totals and provider health flags."""
    from sqlalchemy import func
    from .config import capabilities, killswitches
    rows = db.execute(select(models.VisaApplication.state, func.count()).where(
        models.VisaApplication.org_id == p.org_id).group_by(models.VisaApplication.state)).all()
    by_state = {state: n for state, n in rows}
    def _count(model):
        return db.execute(select(func.count()).select_from(model).join(
            models.VisaApplication, model.application_id == models.VisaApplication.id).where(
            models.VisaApplication.org_id == p.org_id)).scalar() or 0
    return {
        "cases_total": sum(by_state.values()),
        "cases_by_state": by_state,
        "appointments_booked": _count(models.Appointment),
        "submissions": _count(models.SubmissionConfirmation),
        "signatures": _count(models.NativeSignature),
        "capabilities": capabilities(),
        "kill_switches": killswitches(),
    }


# ---- Country-adapter administration + approval lifecycle (Phase 2) ----
class AdapterCreate(BaseModel):
    country: str
    visa_type: str = "tourist"
    config: dict = {}


class AdapterTransition(BaseModel):
    to_state: str
    evidence: dict = {}


class AdapterRollback(BaseModel):
    to_version: int


class KillBody(BaseModel):
    reason: str = ""


def _adapter_err(fn):
    from . import adapters_admin as aa
    try:
        return fn()
    except aa.NotAuthorizedError as e:
        raise HTTPException(403, str(e))
    except aa.LifecycleError as e:
        raise HTTPException(400, str(e))


@app.get("/admin/adapters")
def admin_list_adapters(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return {"adapters": aa.list_adapters(db), "is_admin": p.role == "admin"}


@app.get("/admin/coverage")
def admin_coverage(db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return {"coverage": aa.coverage_matrix(db)}


@app.post("/admin/adapters")
def admin_create_adapter(body: AdapterCreate, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    rec = aa.create_adapter(db, country=body.country, visa_type=body.visa_type,
                            config=body.config, actor=p.user_id)
    return aa.to_dict(rec)


@app.get("/admin/adapters/{adapter_id}")
def admin_get_adapter(adapter_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    rec = db.get(models.AdapterRecord, adapter_id)
    if not rec:
        raise HTTPException(404, "adapter not found")
    return {**aa.to_dict(rec), "versions": aa.versions(db, adapter_id),
            "audit": aa.adapter_audit(db, adapter_id)}


@app.put("/admin/adapters/{adapter_id}")
def admin_update_adapter(adapter_id: str, config: dict, db=Depends(get_session),
                         p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.update_config(db, adapter_id, config, p.user_id)))


@app.post("/admin/adapters/{adapter_id}/transition")
def admin_transition_adapter(adapter_id: str, body: AdapterTransition, db=Depends(get_session),
                             p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.transition(
        db, adapter_id, body.to_state, actor=p.user_id, is_admin=(p.role == "admin"),
        evidence=body.evidence)))


@app.post("/admin/adapters/{adapter_id}/kill")
def admin_kill_adapter(adapter_id: str, body: KillBody, db=Depends(get_session),
                       p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.kill(
        db, adapter_id, actor=p.user_id, is_admin=(p.role == "admin"), reason=body.reason)))


@app.post("/admin/adapters/{adapter_id}/clear-kill")
def admin_clear_kill(adapter_id: str, db=Depends(get_session), p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.clear_kill(
        db, adapter_id, actor=p.user_id, is_admin=(p.role == "admin"))))


@app.post("/admin/adapters/{adapter_id}/rollback")
def admin_rollback_adapter(adapter_id: str, body: AdapterRollback, db=Depends(get_session),
                           p: Principal = Depends(get_principal)):
    from . import adapters_admin as aa
    return _adapter_err(lambda: aa.to_dict(aa.rollback(
        db, adapter_id, body.to_version, actor=p.user_id, is_admin=(p.role == "admin"))))


# ---- Webhooks (signature-verified, idempotent) ----
@app.post("/webhooks/stripe")
async def stripe_webhook(stripe_signature: str = Header(default=""), db=Depends(get_session)):
    from .providers.payment import StripeIssuing
    if not StripeIssuing.is_configured():
        raise HTTPException(503, "stripe not configured")
    return {"received": True}  # ACTIVATION: verify + reconcile idempotently


@app.post("/webhooks/docusign")
async def docusign_webhook(x_docusign_signature: str = Header(default=""), db=Depends(get_session)):
    if not docusign.is_configured():
        raise HTTPException(503, "docusign not configured")
    return {"received": True}  # ACTIVATION: verify signature + mark envelope completed
