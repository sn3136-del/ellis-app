"""Resumable global build orchestrator.

Deterministic, checkpointed, duplicate-proof driver for the worldwide build:

  inventory phase   — matrix populate, baseline import, research overrides,
                      full pair coverage, family sync + audits, pair binding,
                      release-status recompute, snapshot export.
  adapter phase     — one GlobalBuildTask per portal family that needs an
                      adapter (dedup_key = family id, unique platform-wide),
                      bounded live builds per run, transient-only retries.
  revalidate phase  — stale released families re-queued for verification.

Every task is idempotent; every state change is committed before the next
step; stop/resume are first-class. Failures isolate per family/destination
and record a precise, honest error — never fabricated success, never a
silent retry of an irreversible action (adapter builds are read-only by
construction; irreversible actions cannot occur here).
"""
from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..adapter_factory import build_workflow
from ..adapter_factory import models as fm
from ..config import settings
from ..visa_snapshot import SNAPSHOT_DATE
from ..visa_snapshot.routekey import RouteInput, route_key
from . import ELECTRONIC_OUTCOMES
from .models import (FamilyAdapterLink, GlobalBuildRun, GlobalBuildTask,
                     PortalFamily, RoutePairPolicy)

STALE_DAYS = 14
ORCHESTRATOR_ACTOR = "global-orchestrator"

_TRANSIENT_MARKERS = ("timeout", "timed out", "connection", "reset", "refused",
                      "429", "502", "503", "504", "network", "session",
                      "temporarily", "unavailable", "browserbase", "retryable")


def classify_error(message: str) -> str:
    m = (message or "").lower()
    if m.startswith("runtimeerror: permanent") or "permanent:" in m:
        return "permanent"
    return "transient" if any(t in m for t in _TRANSIENT_MARKERS) else "permanent"


# ---------------------------------------------------------------- run control

def start_run(db, command: str, params: dict | None = None) -> GlobalBuildRun:
    run = GlobalBuildRun(command=command, params=params or {}, status="running")
    db.add(run)
    db.commit()
    return run


def stop_run(db, run_id: str, reason: str = "operator stop") -> None:
    run = db.get(GlobalBuildRun, run_id)
    if run and run.status == "running":
        run.status = "stopped"
        run.stopped_reason = reason
        db.commit()


def latest_resumable_run(db, command: str) -> GlobalBuildRun | None:
    return db.execute(select(GlobalBuildRun).where(
        GlobalBuildRun.command == command,
        GlobalBuildRun.status.in_(("running", "stopped")))
        .order_by(GlobalBuildRun.created_at.desc())).scalars().first()


def _stopped(db, run: GlobalBuildRun) -> bool:
    db.refresh(run)
    return run.status != "running"


def _checkpoint(db, run: GlobalBuildRun, **kw) -> None:
    run.checkpoint = dict(run.checkpoint or {}, **kw)
    db.commit()


# ---------------------------------------------------------------- tasks

def enqueue(db, run: GlobalBuildRun, kind: str, subject: str,
            max_attempts: int = 3) -> GlobalBuildTask | None:
    """Queue one unit of work. dedup_key is platform-wide: a task for the same
    (kind, subject) is never duplicated across runs unless it FAILED with a
    transient error and retry is requested."""
    dedup = f"{kind}:{subject}"
    existing = db.execute(select(GlobalBuildTask).where(
        GlobalBuildTask.dedup_key == dedup)).scalars().first()
    if existing is not None:
        if existing.status == "failed" and existing.error_class == "transient" \
                and existing.attempts < existing.max_attempts:
            existing.status = "queued"
            existing.run_id = run.id
            db.commit()
            return existing
        return None
    task = GlobalBuildTask(run_id=run.id, kind=kind, subject=subject,
                           dedup_key=dedup, max_attempts=max_attempts)
    db.add(task)
    db.commit()
    return task


def _run_task(db, task: GlobalBuildTask, fn) -> bool:
    task.status = "running"
    task.attempts += 1
    db.commit()
    try:
        task.result = fn() or {}
        task.status = "complete"
        task.error = ""
        task.error_class = ""
        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — isolate failures per task, honestly
        db.rollback()
        task.status = "failed"
        task.error = f"{type(exc).__name__}: {exc}"[:2000]
        task.error_class = classify_error(str(exc))
        task.result = {"traceback_tail": traceback.format_exc()[-800:]}
        db.commit()
        return False


# ---------------------------------------------------------------- inventory

def run_inventory(db, run: GlobalBuildRun, *, log=None) -> dict:
    """Idempotent global inventory + policy build. Each step checkpoints."""
    from ..visa_snapshot.matrix import populate
    from . import baseline, families
    log = log or (lambda *_: None)
    steps: list[tuple[str, callable]] = [
        ("matrix_populate", lambda: populate(db)),
        ("baseline_import", lambda: baseline.import_reference_baseline(db)),
        ("research_overrides", lambda: baseline.apply_research_overrides(db)),
        ("full_pair_coverage", lambda: baseline.ensure_full_pair_coverage(db)),
        ("matrix_sync", lambda: baseline.sync_matrix_dispositions(db)),
        ("family_sync", lambda: families.sync_families(db)),
        ("portal_record_audit", lambda: families.audit_official_portal_records(db)),
        ("pair_family_binding", lambda: families.assign_families_to_pairs(db)),
        ("release_status_recompute", lambda: recompute_release_statuses(db)),
    ]
    out: dict = {}
    for name, fn in steps:
        if _stopped(db, run):
            out["stopped_at"] = name
            return out
        done = (run.checkpoint or {}).get("inventory_done", [])
        if name in done:
            out[name] = "checkpointed"
            continue
        log(f"inventory: {name}")
        out[name] = fn()
        _checkpoint(db, run, inventory_done=done + [name])
    return out


# ------------------------------------------------------- release status

def _revalidate_family_releases(db) -> int:
    """A 'released' family link is only as good as its live binding: kill
    switch, quarantine, revocation or rollback must revert coverage. Returns
    the number of links revoked."""
    from ..adapter_factory import release as releasesvc
    revoked = 0
    for link in db.execute(select(FamilyAdapterLink).where(
            FamilyAdapterLink.released.is_(True))).scalars().all():
        binding = releasesvc.active_binding(
            db, route_key=link.representative_route_key, tier="sandbox")
        killed = bool(link.candidate_id) and releasesvc.kill_engaged(
            db, link.candidate_id)
        if binding is None or killed or (
                link.candidate_id and binding.candidate_id != link.candidate_id):
            link.released = False
            link.release_tier = ""
            link.status = "revoked"
            link.last_error = ("release revoked: " +
                               ("kill switch engaged" if killed else
                                "no active sandbox binding for the adapter"))
            revoked += 1
    if revoked:
        db.commit()
    return revoked


def recompute_release_statuses(db) -> dict:
    """Recompute honest per-pair release classification from verification
    level, outcome and family-adapter release state. Provisional data can
    NEVER produce a released or legally-unavailable claim."""
    _revalidate_family_releases(db)
    released_families = {l.family_id for l in db.execute(
        select(FamilyAdapterLink).where(FamilyAdapterLink.released.is_(True))).scalars()}
    counts: dict[str, int] = {}
    for p in db.execute(select(RoutePairPolicy)).scalars():
        if p.verification_status == "unresolved" or p.route_outcome == "UNRESOLVED":
            status, reason = "unresolved", (p.release_reason or
                                            "no data for this pair yet — official research required")
        elif p.verification_status == "provisional":
            status = "defined_provisional"
            reason = ("reference dataset reports no lawful tourist admission; "
                      "official confirmation required before this is final"
                      if p.route_outcome == "NO_AVAILABLE_TOURIST_ROUTE" else
                      "outcome defined from non-official reference dataset; "
                      "official verification pending")
        elif p.route_outcome == "NO_AVAILABLE_TOURIST_ROUTE":
            status, reason = "legally_unavailable", "verified: no lawful tourist route"
        elif p.route_outcome in ELECTRONIC_OUTCOMES:
            if p.portal_family_id and p.portal_family_id in released_families:
                status, reason = "released_adapter", (
                    f"verified route; adapter for portal family "
                    f"{p.portal_family_id} released by deterministic gates")
            elif p.portal_family_id:
                status, reason = "defined_verified", (
                    f"verified route; adapter for portal family "
                    f"{p.portal_family_id} not released yet")
            else:
                status, reason = "defined_verified", (
                    "verified electronic route but no official portal family "
                    "registered — portal identification required (never invented)")
        else:
            # Verified non-portal outcome: the preparation/checklist/journey
            # workflow ships with the product and requires no portal adapter.
            status, reason = "released_workflow", (
                f"verified route; non-portal workflow ({p.route_outcome}) "
                f"released — no submission adapter required")
        if p.release_status != status or p.release_reason != reason:
            p.release_status = status
            p.release_reason = reason
        counts[status] = counts.get(status, 0) + 1
    db.commit()
    return counts


# ---------------------------------------------------------------- adapters

def families_needing_adapters(db) -> list[PortalFamily]:
    """Families that govern at least one electronic pair, are officially
    verified, and have no released adapter yet. Unverified families are
    excluded (fail closed) and surface on the dashboard as exact gaps."""
    linked_released = {l.family_id for l in db.execute(
        select(FamilyAdapterLink).where(FamilyAdapterLink.released.is_(True))).scalars()}
    used = {fid for (fid,) in db.execute(
        select(RoutePairPolicy.portal_family_id).where(
            RoutePairPolicy.portal_family_id != "").distinct()).all()}
    fams = db.execute(select(PortalFamily).where(
        PortalFamily.active.is_(True),
        PortalFamily.verification_status.in_(("verified_official_domain", "verified_live")))
        .order_by(PortalFamily.family_id)).scalars().all()
    return [f for f in fams if f.family_id in used and f.family_id not in linked_released]


def representative_route_key(db, family: PortalFamily) -> str:
    """Deterministic representative route for a family adapter build: the
    first governed pair (by pair_key) whose nationality is a plain state
    code, so issuer/residence normalize cleanly."""
    pairs = db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.portal_family_id == family.family_id)
        .order_by(RoutePairPolicy.pair_key)).scalars().all()
    if not pairs:
        raise ValueError(f"family {family.family_id} governs no pairs")
    for pair in pairs:
        try:
            return route_key(RouteInput(
                passport_nationality=pair.passport_nationality,
                passport_issuing_country=pair.passport_nationality,
                travel_document_type="ordinary_passport",
                lawful_country_of_residence=pair.passport_nationality,
                destination_country=pair.destination_country,
                visa_category=pair.primary_category or "evisa_tourist",
                visa_subtype="any", travel_purpose="tourism",
                policy_period=SNAPSHOT_DATE, consular_jurisdiction="default"))
        except Exception:  # noqa: BLE001 — special document classes can't
            continue        # serve as issuer/residence; try the next pair
    raise ValueError(f"family {family.family_id}: no representative pair "
                     f"with a normalizable state nationality")


def research_entry_urls(db, family: PortalFamily, limit: int = 6) -> list[str]:
    """Official-source URLs already VERIFIED by route research that live on
    this family's own hostnames. Real portals put the application form on a
    localised deep path; starting recon there beats guessing '/application'.
    Never widens the hostname allowlist — paths only, on verified hosts."""
    from ..visa_snapshot.models import SourceEvidence
    hosts = {h.lower() for h in (family.hostnames or [])}
    if not hosts:
        return []
    urls: list[str] = []
    rows = db.execute(select(SourceEvidence).where(
        SourceEvidence.verification_status == "verified",
        SourceEvidence.applicable_jurisdiction.in_(family.destinations or []))
        .order_by(SourceEvidence.created_at.desc()).limit(200)).scalars().all()
    for ev in rows:
        url = ev.final_url or ev.original_url or ""
        if (ev.final_hostname or "").lower() in hosts and url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def build_family_adapter(db, family_id: str, *, observer=None) -> dict:
    """Build (or resume) THE one adapter for a portal family and evaluate the
    deterministic release gates. Idempotent: an existing link is resumed."""
    from . import release_gates
    family = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()
    if family is None:
        raise ValueError(f"unknown portal family {family_id}")
    if family.verification_status not in ("verified_official_domain", "verified_live"):
        raise PermanentBuildStop(
            f"portal family {family_id} is not officially verified "
            f"({family.verification_status}) — build refused, fail closed")

    link = db.execute(select(FamilyAdapterLink).where(
        FamilyAdapterLink.family_id == family_id)).scalars().first()
    if link is None:
        link = FamilyAdapterLink(family_id=family_id, status="building")
        db.add(link)
        db.commit()
    if link.released:
        return {"family_id": family_id, "released": True, "status": "already_released"}

    rk = link.representative_route_key or representative_route_key(db, family)
    link.representative_route_key = rk
    db.commit()

    dest = family.destinations[0] if family.destinations else ""
    pair = db.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.portal_family_id == family_id)
        .order_by(RoutePairPolicy.pair_key)).scalars().first()
    if pair is not None:
        dest = pair.destination_country

    portal_evidence = {"hostnames": family.hostnames or [],
                       "operator": family.operator,
                       "portal_url": family.base_url,
                       "entry_urls": research_entry_urls(db, family)}
    if family.entry_gate:
        # Curated entry-gate declaration rides on the build's portal evidence:
        # recon replays it, specgen emits its nodes, testing re-replays it.
        portal_evidence["entry_gate"] = family.entry_gate
    req = build_workflow.create_request(
        db, org_id="global", user_id=ORCHESTRATOR_ACTOR, application_id="",
        route_key=rk, destination=dest,
        visa_type=(pair.primary_category if pair else "evisa_tourist"),
        portal_evidence=portal_evidence,
        runtime_mode=settings().runtime_mode)
    if family.entry_gate and not (req.portal_evidence or {}).get("entry_gate"):
        # A pre-existing (resumed) request predating the family's entry-gate
        # declaration still gets it — the resumed build must not re-run recon
        # without the gate and honestly fail on a stale evidence shape.
        req.portal_evidence = dict(req.portal_evidence or {},
                                   entry_gate=family.entry_gate)
        db.commit()
    # A national online portal is the destination's own single application
    # jurisdiction; residence-dependent consular routing does not apply.
    req.jurisdiction_evidence = {
        "verified": True,
        "basis": f"online national portal for {dest}; application jurisdiction "
                 f"is the portal itself (online_only)"}
    if not req.consent_given:
        build_workflow.record_consent(db, req, user_id=ORCHESTRATOR_ACTOR)
    link.build_request_id = req.id
    db.commit()

    req = build_workflow.run_build(db, req.id, observer=observer)
    cand = db.execute(select(fm.AdapterCandidate).where(
        fm.AdapterCandidate.build_request_id == req.id)).scalars().first()
    link.candidate_id = cand.id if cand else ""
    link.status = req.state
    db.commit()

    gates = release_gates.evaluate_and_release(db, build_request=req, family=family)
    link.gate_report = {"passed": gates.get("passed", False),
                        "missing": gates.get("missing", []),
                        "gates": {k: v for k, v in (gates.get("gates") or {}).items()}}
    link.released = bool(gates.get("released"))
    link.release_tier = "sandbox" if link.released else ""
    link.status = req.state if not link.released else "released"
    if not link.released:
        link.last_error = "; ".join(gates.get("missing", []))[:1900] or req.error or req.state
    else:
        link.last_error = ""
    db.commit()
    return {"family_id": family_id, "build_state": req.state,
            "released": link.released, "missing": gates.get("missing", [])}


class PermanentBuildStop(RuntimeError):
    """Raised for honest, non-retryable stops (legal/manual/unverified)."""


def _recover_orphaned_tasks(db, run: GlobalBuildRun) -> int:
    """A crash while a task was 'running' (SIGKILL, reboot) would strand it
    forever; tasks stuck running under a run that is no longer running are
    honest interruptions — requeue them."""
    recovered = 0
    for t in db.execute(select(GlobalBuildTask).where(
            GlobalBuildTask.status == "running",
            GlobalBuildTask.run_id != run.id)).scalars().all():
        owner = db.get(GlobalBuildRun, t.run_id)
        if owner is None or owner.status != "running":
            t.status = "queued"
            t.run_id = run.id
            recovered += 1
    if recovered:
        db.commit()
    return recovered


def run_adapter_phase(db, run: GlobalBuildRun, *, observer_factory=None,
                      max_live_builds: int | None = None, only_family: str = "",
                      only_destination: str = "", log=None) -> dict:
    """Queue + execute family adapter builds, bounded and resumable. The
    only_family/only_destination filters bound EXECUTION as well as queueing —
    a scoped command never drains the global queue or the build budget."""
    log = log or (lambda *_: None)
    _recover_orphaned_tasks(db, run)
    todo = families_needing_adapters(db)
    if only_family:
        todo = [f for f in todo if f.family_id == only_family]
    if only_destination:
        todo = [f for f in todo if only_destination in (f.destinations or [])]
    for fam in todo:
        enqueue(db, run, "family_adapter", fam.family_id)

    allowed_subjects = {f.family_id for f in todo} if (only_family or only_destination) else None
    executed = released = failed = 0
    tasks = db.execute(select(GlobalBuildTask).where(
        GlobalBuildTask.kind == "family_adapter",
        GlobalBuildTask.status == "queued").order_by(GlobalBuildTask.subject)).scalars().all()
    if allowed_subjects is not None:
        tasks = [t for t in tasks if t.subject in allowed_subjects]
    for task in tasks:
        if _stopped(db, run):
            break
        if max_live_builds is not None and executed >= max_live_builds:
            log(f"live-build budget ({max_live_builds}) reached; "
                f"{len(tasks) - executed} task(s) stay queued")
            break
        fam_id = task.subject
        log(f"building adapter for portal family {fam_id}")
        observer = observer_factory(fam_id) if observer_factory else None

        def _work(fid=fam_id, obs=observer):
            try:
                out = build_family_adapter(db, fid, observer=obs)
            except PermanentBuildStop as exc:
                raise RuntimeError(f"permanent: {exc}") from exc
            if not out.get("released") and out.get("status") != "already_released":
                # A gates-not-passed build is a retryable failure, never a
                # silent 'complete' — otherwise dedup would freeze the family
                # in an unreleased state forever.
                missing = "; ".join(out.get("missing") or []) or out.get("build_state", "")
                raise RuntimeError(f"release gates not passed (retryable): {missing}"[:1800])
            return out

        ok = _run_task(db, task, _work)
        executed += 1
        if ok and (task.result or {}).get("released"):
            released += 1
        elif not ok:
            failed += 1
    recompute_release_statuses(db)
    return {"queued": len(tasks), "executed": executed,
            "released": released, "failed": failed}


# ---------------------------------------------------------------- staleness

def mark_stale_families(db, *, now=None) -> dict:
    """Time-based staleness for released/live-verified families."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_DAYS)
    stale = 0
    for fam in db.execute(select(PortalFamily).where(
            PortalFamily.verification_status == "verified_live")).scalars():
        last = fam.last_verified_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or last < cutoff:
            if not fam.stale:
                fam.stale = True
                stale += 1
    db.commit()
    return {"marked_stale": stale, "stale_after_days": STALE_DAYS}


def queue_revalidation(db, run: GlobalBuildRun) -> dict:
    """Queue re-verification tasks for stale families (retryable)."""
    queued = 0
    for fam in db.execute(select(PortalFamily).where(
            PortalFamily.stale.is_(True))).scalars():
        t = enqueue(db, run, "family_revalidate", fam.family_id)
        if t is not None:
            queued += 1
    return {"revalidation_queued": queued}


# ---------------------------------------------------------------- reporting

def unsupported_routes(db, limit: int = 200) -> list[dict]:
    """Sample BOTH classes so unresolved routes always surface alongside
    legally-unavailable ones."""
    half = max(limit // 2, 1)
    rows = []
    for status in ("unresolved", "legally_unavailable"):
        rows.extend(db.execute(select(RoutePairPolicy).where(
            RoutePairPolicy.release_status == status)
            .order_by(RoutePairPolicy.pair_key).limit(half)).scalars().all())
    return [{"pair": p.pair_key, "outcome": p.route_outcome,
             "release_status": p.release_status, "reason": p.release_reason}
            for p in rows]
