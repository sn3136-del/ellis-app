"""Arrival-card filing for visa-exempt (ENTRY_PREPARATION) routes.

A visa-exempt route has no visa to apply for, but most still require a digital
arrival card, and the destination decides WHEN it may be filed: Thailand's TDAC
opens three days before arrival, Malaysia's MDAC three days, Singapore's SG
Arrival Card three. Filing early is refused by the portal, so a case that is
otherwise ready has to wait.

Ellis used to leave the applicant there: the checklist's Continue recorded the
document stage and returned, nothing was ever enqueued, and the screen said
"Entry preparation complete" while the arrival card had not been filed and
never would be (2026-08-03). Everything needed was already in place — the
released adapter, the resolver, the durable queue — with no path from the
applicant's click to any of it.

This module supplies the missing piece: the applicant's ONE press schedules
the filing, and it runs the moment the destination's window opens. Ellis never
decides on its own initiative to file — the intent, and the moment it was
given, are recorded here.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from . import models

# Stage marker on CaseStageProgress. One per case (uq_case_stage), so a
# repeated press updates the schedule rather than queueing a second filing.
STAGE_ENTRY_FILING = "entry_filing_scheduled"

# "within 3 days before arrival", "72 hours prior to arrival", "3 days before
# your arrival in Thailand". Portals state the window in prose; only the
# NUMBER and its unit are load-bearing.
# A trailing \b works for Latin units but never for CJK: 天 and 内 are both
# word characters, so "抵达前 3 天内" has no boundary to match. CJK units are
# matched without one.
_WINDOW_RE = re.compile(
    r"(\d{1,3})\s*(?:(day|days|hour|hours|ngày|giờ)\b|(天|日|小时|小時|時間|시간))",
    re.IGNORECASE)
_HOUR_WORDS = ("hour", "giờ", "小时", "小時", "時間", "시간")


def window_days(text: str) -> int | None:
    """Days before arrival that a portal opens its arrival card, parsed from
    the researched window. None when the text says nothing usable — the
    caller then files immediately and lets the PORTAL be the authority on
    its own timing, which is better than inventing a number and holding a
    ready case back on a guess.
    """
    m = _WINDOW_RE.search(str(text or ""))
    if m is None:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or m.group(3) or "").lower()
    if any(h in unit for h in _HOUR_WORDS):
        # Round up: a 72-hour window is 3 days, and a 36-hour one still opens
        # on the day before arrival rather than on the day itself.
        n = -(-n // 24)
    return n if 0 < n <= 365 else None


def _arrival_date(app_row) -> date | None:
    from . import dates as dates_mod
    answers = app_row.answers or {}
    for key in ("arrival_date", "intended_arrival", "entry_date"):
        raw = (answers.get(key) or "").strip() if isinstance(answers.get(key), str) else ""
        if not raw:
            continue
        iso = dates_mod.normalize_any(raw, kind="future") or raw
        try:
            return date.fromisoformat(iso[:10])
        except ValueError:
            continue
    return None


def _guidance_blob(cg) -> dict:
    """case_route_guidance.guidance is the Kimi envelope; the route facts sit
    one level in, under its own 'guidance' key."""
    outer = cg.guidance if isinstance(cg.guidance, dict) else {}
    inner = outer.get("guidance")
    return inner if isinstance(inner, dict) else outer


def plan(db, app_row) -> dict:
    """What arrival-card filing this case needs, and whether it can run now.

    status:
      not_required    the route needs no arrival card
      unsupported     it needs one, but no released adapter can file it
      open            fileable now
      waiting         the destination's window has not opened yet
    """
    from . import checklist_intake
    from .portal.released_flow import resolve_released_route

    out = {"required": False, "status": "not_required", "name": "",
           "arrival_date": None, "opens_on": None, "window_days": None,
           "family_id": ""}
    cg = checklist_intake.case_guidance(db, app_row.id)
    if cg is None or cg.continuation_kind != "entry_preparation":
        return out
    card = (_guidance_blob(cg).get("arrival_card") or {})
    if not isinstance(card, dict) or not card.get("required"):
        return out
    out.update(required=True, name=str(card.get("name") or "arrival card"))

    route = None
    try:
        route = resolve_released_route(db, app_row)
    except Exception:  # noqa: BLE001 — an unresolvable route is simply unsupported
        route = None
    if route is None:
        out["status"] = "unsupported"
        return out
    out["family_id"] = route.family.family_id

    days = window_days(card.get("submission_window"))
    arrival = _arrival_date(app_row)
    out["window_days"] = days
    out["arrival_date"] = arrival.isoformat() if arrival else None
    if days is None or arrival is None:
        # No usable window, or no arrival date to measure it from: the portal
        # itself refuses an early filing, so trying now is honest and cheap.
        out["status"] = "open"
        return out
    opens = arrival - timedelta(days=days)
    out["opens_on"] = opens.isoformat()
    out["status"] = "open" if date.today() >= opens else "waiting"
    return out


def scheduled(db, application_id: str) -> models.CaseStageProgress | None:
    return db.execute(select(models.CaseStageProgress).where(
        models.CaseStageProgress.application_id == application_id,
        models.CaseStageProgress.stage == STAGE_ENTRY_FILING)).scalars().first()


def schedule(db, *, org_id: str, app_row, plan_out: dict) -> models.CaseStageProgress:
    """Record the applicant's instruction to file when the window opens.

    Idempotent per case: pressing Continue again refreshes the recorded plan
    (their trip dates may have changed) instead of scheduling a second filing.
    """
    detail = {"opens_on": plan_out.get("opens_on"),
              "arrival_date": plan_out.get("arrival_date"),
              "window_days": plan_out.get("window_days"),
              "family_id": plan_out.get("family_id"),
              "name": plan_out.get("name")}
    row = scheduled(db, app_row.id)
    if row is None:
        row = models.CaseStageProgress(
            org_id=org_id, application_id=app_row.id, stage=STAGE_ENTRY_FILING,
            completed_at=datetime.now(timezone.utc), detail=detail)
        db.add(row)
    else:
        row.detail = detail
    db.commit()
    return row


def due_cases(db, *, limit: int = 50) -> list[models.VisaApplication]:
    """Cases whose applicant asked Ellis to file and whose window has opened.

    Deliberately narrow: the case must still be a DRAFT the applicant never
    started by hand, and must have no run of its own in flight. The worker
    executes an instruction already given — it never decides to file.
    """
    rows = db.execute(select(models.CaseStageProgress).where(
        models.CaseStageProgress.stage == STAGE_ENTRY_FILING)
        .order_by(models.CaseStageProgress.completed_at.asc()).limit(limit * 4)).scalars().all()
    today = date.today().isoformat()
    out: list[models.VisaApplication] = []
    for row in rows:
        opens = (row.detail or {}).get("opens_on")
        if opens and str(opens) > today:
            continue
        app_row = db.get(models.VisaApplication, row.application_id)
        if app_row is None or app_row.state != "DRAFT":
            continue
        active = db.execute(select(models.PortalRun).where(
            models.PortalRun.application_id == app_row.id,
            models.PortalRun.status.in_(("queued", "running")))).scalars().first()
        if active is not None:
            continue
        out.append(app_row)
        if len(out) >= limit:
            break
    return out
