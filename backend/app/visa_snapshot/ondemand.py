"""On-demand research for ONE exact applicant route (strategy change §8-§16).

Durable 18-stage job:
  RECEIVE_ROUTE -> NORMALIZE_ROUTE -> CHECK_STORED_POLICY ->
  DISCOVER_OFFICIAL_SOURCES -> VERIFY_SOURCE_DOMAINS -> FETCH_SOURCE_CONTENT ->
  KIMI_EXTRACT_AND_TRANSLATE -> NORMALIZE_REQUIREMENTS ->
  RESOLVE_CONSULAR_JURISDICTION -> VERIFY_OFFICIAL_PORTAL -> VERIFY_ROUTE_FEES ->
  DETECT_CONFLICTS -> CREATE_IMMUTABLE_POLICY_VERSION -> LINK_ROUTE ->
  IMPORT_TO_POSTGRESQL -> EXPORT_TO_JSONL -> EVALUATE_ADAPTER_READINESS ->
  RETURN_ROUTE_STATUS

Every stage transition is persisted on the job row (resumable after any
restart); limits are enforced between stages; partial results are saved and
unresolved fields stay honestly UNKNOWN. Research touches ONLY this route's
destination — never unrelated countries. Results are cached (immutable route
versions + shared destination policies) so future matching routes reuse them
without new research. Date honesty: records researched after 2026-07-23 carry
their actual retrieval date and are never claimed as proof of the snapshot rule.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from . import SNAPSHOT_DATE, importer, pipeline
from .authority import is_government_host
from .fetching import SearchUnavailable, fetch, search
from .kimi_research import EXTRACTION_VERSION, KimiUnavailable, extract
from .models import (HumanReviewTask, OfficialPortalRecord, OnDemandRouteResearchJob,
                     RouteIntake, SnapshotResearchBatch, SourceEvidence)
from .registry import RegistryError
from .resolution import RESEARCHED, resolve
from .routekey import RouteInput, route_key

STAGES = ("RECEIVE_ROUTE", "NORMALIZE_ROUTE", "CHECK_STORED_POLICY",
          "DISCOVER_OFFICIAL_SOURCES", "VERIFY_SOURCE_DOMAINS",
          "FETCH_SOURCE_CONTENT", "KIMI_EXTRACT_AND_TRANSLATE",
          "NORMALIZE_REQUIREMENTS", "RESOLVE_CONSULAR_JURISDICTION",
          "VERIFY_OFFICIAL_PORTAL", "VERIFY_ROUTE_FEES", "DETECT_CONFLICTS",
          "CREATE_IMMUTABLE_POLICY_VERSION", "LINK_ROUTE", "IMPORT_TO_POSTGRESQL",
          "EXPORT_TO_JSONL", "EVALUATE_ADAPTER_READINESS", "RETURN_ROUTE_STATUS")

DISPOSITIONS = ("VISA_FREE", "ETA_REQUIRED", "EVISA_REQUIRED", "VISA_ON_ARRIVAL",
                "EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED",
                "TRANSIT_AUTHORIZATION_REQUIRED", "NOT_APPLICABLE")

DEFAULT_LIMITS = {"max_seconds": 300, "max_queries": 10, "max_pages": 12,
                  "per_source_timeout": 20, "max_kimi_retries": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def limits_from_env() -> dict:
    import os
    out = dict(DEFAULT_LIMITS)
    for k, env in (("max_seconds", "ELLIS_RESEARCH_MAX_SECONDS"),
                   ("max_queries", "ELLIS_RESEARCH_MAX_QUERIES"),
                   ("max_pages", "ELLIS_RESEARCH_MAX_PAGES"),
                   ("per_source_timeout", "ELLIS_RESEARCH_SOURCE_TIMEOUT"),
                   ("max_kimi_retries", "ELLIS_RESEARCH_KIMI_RETRIES")):
        v = os.getenv(env)
        if v and v.isdigit():
            out[k] = int(v)
    return out


def create_job(db, *, org_id: str, user_id: str = "", intake_id: str | None = None,
               case_id: str | None = None, answers: dict, normalized: dict,
               key: str, language: str = "en") -> OnDemandRouteResearchJob:
    """Create (or reuse) the focused research job for one exact route."""
    existing = db.execute(select(OnDemandRouteResearchJob).where(
        OnDemandRouteResearchJob.org_id == org_id,
        OnDemandRouteResearchJob.route_key == key,
        OnDemandRouteResearchJob.status.in_(("queued", "running")))).scalars().first()
    if existing:
        return existing
    job = OnDemandRouteResearchJob(
        org_id=org_id, user_id=user_id, intake_id=intake_id, case_id=case_id,
        route_key=key, route=normalized, requested_language=language,
        limits=limits_from_env(), researched_at_date=_today(),
        extraction_version=EXTRACTION_VERSION,
        progress=[{"stage": "RECEIVE_ROUTE", "at": _now(), "note": "job created"}])
    db.add(job)
    db.commit()
    return job


def _note(job, stage: str, note: str):
    job.progress = list(job.progress or []) + [{"stage": stage, "at": _now(), "note": note[:300]}]


def _budget_exceeded(job, t0: float) -> bool:
    return (time.time() - t0) > (job.limits or DEFAULT_LIMITS).get("max_seconds", 300)


def _search_queries(route: dict) -> list[str]:
    dest, nat, res = route["destination_country"], route["passport_nationality"], route["lawful_country_of_residence"]
    return [
        f"{dest} official tourist visa {nat}",
        f"{dest} immigration visa requirements {nat}",
        f"{dest} foreign ministry visa {nat}",
        f"{dest} embassy tourist visa {res}",
        f"official {dest} evisa",
        f"official {dest} electronic travel authorization",
        f"official {dest} visa exemption",
        f"{dest} consular jurisdiction {res}",
        f"{dest} tourist visa fee {nat}",
        f"{dest} passport validity tourism",
    ]


def run_job(db, job_id: str) -> OnDemandRouteResearchJob:
    """Advance the job to completion (or an honest stop). Idempotent + resumable:
    re-running a finished stage is safe; a crash resumes at the persisted stage."""
    job = db.get(OnDemandRouteResearchJob, job_id)
    if job is None:
        raise KeyError("research job not found")
    if job.status in ("complete", "conflicted", "failed"):
        return job
    t0 = time.time()
    job.status = "running"
    job.attempts += 1
    if not job.started_at:
        job.started_at = _now()
    db.commit()

    state = dict(job.counters or {})
    dest = job.route.get("destination_country", "")

    def _finish(status: str, note: str):
        job.status = status
        job.finished_at = _now()
        _note(job, job.stage, note)
        db.commit()

    try:
        while True:
            if _budget_exceeded(job, t0) and job.stage not in (
                    "IMPORT_TO_POSTGRESQL", "EXPORT_TO_JSONL",
                    "EVALUATE_ADAPTER_READINESS", "RETURN_ROUTE_STATUS"):
                # Time limit: save verified partials, queue review, stop honestly.
                db.add(HumanReviewTask(org_id=job.org_id, snapshot_date=SNAPSHOT_DATE,
                                       kind="route_review", subject_id=job.route_key,
                                       title=f"On-demand research timed out at {job.stage}",
                                       detail={"job_id": job.id}))
                _finish("timed_out", "time budget reached; partial results saved")
                return job

            stage = job.stage
            if stage == "RECEIVE_ROUTE":
                job.stage = "NORMALIZE_ROUTE"

            elif stage == "NORMALIZE_ROUTE":
                # Route was normalized at creation; re-verify key determinism.
                try:
                    k2 = route_key(RouteInput(
                        passport_nationality=job.route["passport_nationality"],
                        passport_issuing_country=job.route["passport_issuing_country"],
                        travel_document_type=job.route["travel_document_type"],
                        lawful_country_of_residence=job.route["lawful_country_of_residence"],
                        destination_country=job.route["destination_country"],
                        visa_category=job.route["visa_category"],
                        visa_subtype=job.route.get("visa_subtype"),
                        policy_period=job.route.get("policy_period"),
                        consular_jurisdiction=job.route.get("consular_jurisdiction")))
                    if k2 != job.route_key:
                        _note(job, stage, "route key mismatch — renormalized")
                        job.route_key = k2
                except (RegistryError, KeyError) as e:
                    job.error = f"normalization: {e}"
                    _finish("failed", "route could not be normalized")
                    return job
                job.stage = "CHECK_STORED_POLICY"

            elif stage == "CHECK_STORED_POLICY":
                pre = resolve(db, org_id=job.org_id, answers=_answers_from(job),
                              case_id=job.case_id, create_tasks=False)
                if pre["readiness_status"] != "NOT_READY":
                    # Stored data already sufficient — reuse it, no new research.
                    job.result = {"resolution": _slim(pre), "reused_stored": True}
                    _note(job, stage, "stored verified policy reused; no research needed")
                    job.stage = "RETURN_ROUTE_STATUS"
                else:
                    job.stage = "DISCOVER_OFFICIAL_SOURCES"

            elif stage == "DISCOVER_OFFICIAL_SOURCES":
                cands: list[str] = []
                # 1) verified stored evidence + portals for THIS destination only.
                for e in db.execute(select(SourceEvidence).where(
                        SourceEvidence.applicable_jurisdiction == dest,
                        SourceEvidence.verification_status == "verified")).scalars():
                    cands.append(e.final_url)
                for p in db.execute(select(OfficialPortalRecord).where(
                        OfficialPortalRecord.destination_country == dest)).scalars():
                    cands.append(p.url)
                # 2) prior research file for this destination (fleet or on-demand).
                rf = pipeline.research_file(dest)
                if rf.exists():
                    try:
                        prior = json.loads(rf.read_text())
                        cands += [s.get("final_url") for s in prior.get("sources", [])
                                  if s.get("final_url")]
                    except json.JSONDecodeError:
                        _note(job, stage, "prior research file unparseable — ignored")
                # 3) external search provider when configured (honest when not).
                queries_used = 0
                try:
                    for q in _search_queries(job.route)[: (job.limits or {}).get("max_queries", 10)]:
                        cands += search(q)
                        queries_used += 1
                except SearchUnavailable as e:
                    _note(job, stage, str(e))
                state["queries_used"] = queries_used
                # dedupe, keep order
                seen, ordered = set(), []
                for u in cands:
                    if u and u not in seen:
                        seen.add(u)
                        ordered.append(u)
                state["candidates"] = ordered[:40]
                _note(job, stage, f"{len(ordered)} candidate sources ({queries_used} queries)")
                job.stage = "VERIFY_SOURCE_DOMAINS"

            elif stage == "VERIFY_SOURCE_DOMAINS":
                from .authority import hostname
                gov = [u for u in state.get("candidates", []) if is_government_host(hostname(u))]
                other = [u for u in state.get("candidates", []) if not is_government_host(hostname(u))]
                # Government domains first; contractors only matter with official links.
                state["fetch_order"] = gov + other
                state["gov_candidates"] = len(gov)
                job.stage = "FETCH_SOURCE_CONTENT"

            elif stage == "FETCH_SOURCE_CONTENT":
                limits = job.limits or DEFAULT_LIMITS
                pages, failures = [], []
                for url in state.get("fetch_order", [])[: limits.get("max_pages", 12)]:
                    if _budget_exceeded(job, t0):
                        break
                    fr = fetch(url, timeout_seconds=limits.get("per_source_timeout", 20))
                    if fr.ok:
                        pages.append({"id": f"s{len(pages)+1}", "url": fr.final_url,
                                      "hostname": fr.final_hostname, "text": fr.content_text,
                                      "redirect_chain": fr.redirect_chain,
                                      "content_hash": fr.content_hash,
                                      "page_language": fr.page_language,
                                      "retrieved_at": fr.retrieved_at})
                    else:
                        # §11: preserve the attempt + failure; never guess around it.
                        failures.append({"url": url, "error": fr.error,
                                         "at": fr.retrieved_at})
                state["pages"] = pages
                state["fetch_failures"] = failures
                _note(job, stage, f"fetched {len(pages)} ok, {len(failures)} failed/blocked")
                job.stage = "KIMI_EXTRACT_AND_TRANSLATE"

            elif stage == "KIMI_EXTRACT_AND_TRANSLATE":
                if state.get("pages") and not any(p.get("text") for p in state["pages"]):
                    # Resumed after a restart: page text is not persisted (PII/size
                    # hygiene) — re-fetch idempotently before extracting.
                    job.stage = "FETCH_SOURCE_CONTENT"
                    db.commit()
                    continue
                gov_pages = [p for p in state.get("pages", [])
                             if is_government_host(p["hostname"])]
                if not gov_pages:
                    state["extraction"] = None
                    _note(job, stage, "no fetched government pages — extraction skipped")
                else:
                    try:
                        ex = extract(job.route, gov_pages,
                                     max_retries=(job.limits or {}).get("max_kimi_retries", 2))
                        state["extraction"] = ex
                        job.kimi_model = ex.get("model", "")
                        _note(job, stage,
                              f"extracted {len(ex['fields'])} grounded fields, "
                              f"rejected {len(ex['rejected'])} ungrounded")
                    except KimiUnavailable as e:
                        state["extraction"] = None
                        _note(job, stage, f"Kimi unavailable: {e}")
                job.stage = "NORMALIZE_REQUIREMENTS"

            elif stage == "NORMALIZE_REQUIREMENTS":
                ex = state.get("extraction") or {"fields": {}, "conflicts": []}
                f = ex["fields"]
                disp = None
                vr = f.get("visa_requirement", {}).get("value")
                if isinstance(vr, str) and vr.strip().upper().replace(" ", "_") in DISPOSITIONS:
                    disp = vr.strip().upper().replace(" ", "_")
                state["disposition"] = disp
                state["disposition_sources"] = f.get("visa_requirement", {}).get("sources", [])
                job.stage = "RESOLVE_CONSULAR_JURISDICTION"

            elif stage == "RESOLVE_CONSULAR_JURISDICTION":
                # Only evidence-backed; never proximity. Stored rules or grounded
                # extraction with a government source; else stays unresolved.
                ex = (state.get("extraction") or {}).get("fields", {})
                jur = ex.get("consular_jurisdiction") or ex.get("competent_embassy_or_consulate")
                state["jurisdiction"] = jur  # {value, sources} or None
                job.stage = "VERIFY_OFFICIAL_PORTAL"

            elif stage == "VERIFY_OFFICIAL_PORTAL":
                from .authority import hostname
                ex = (state.get("extraction") or {}).get("fields", {})
                portal = ex.get("official_application_portal")
                verified_portal = None
                if portal and isinstance(portal.get("value"), str):
                    url = portal["value"].strip()
                    if is_government_host(hostname(url)):
                        verified_portal = url
                state["portal"] = verified_portal
                job.stage = "VERIFY_ROUTE_FEES"

            elif stage == "VERIFY_ROUTE_FEES":
                ex = (state.get("extraction") or {}).get("fields", {})
                amt = ex.get("government_fee_amount", {})
                cur = ex.get("government_fee_currency", {})
                fee = None
                if amt.get("value") is not None and cur.get("value"):
                    from .authority import hostname as _h
                    srcs = (amt.get("sources") or []) + (cur.get("sources") or [])
                    if srcs and all(is_government_host(_h(u)) for u in srcs):
                        fee = {"amount": amt["value"], "currency": str(cur["value"]).upper(),
                               "sources": srcs}
                state["fee"] = fee
                job.stage = "DETECT_CONFLICTS"

            elif stage == "DETECT_CONFLICTS":
                conflicts = (state.get("extraction") or {}).get("conflicts", [])
                state["conflicts"] = conflicts
                material = [c for c in conflicts if c.get("field") == "visa_requirement"]
                state["material_conflict"] = bool(material)
                job.stage = "CREATE_IMMUTABLE_POLICY_VERSION"

            elif stage == "CREATE_IMMUTABLE_POLICY_VERSION":
                # Merge into the destination research file (shared cache), then
                # re-drive the idempotent destination pipeline for policies/
                # evidence/matrix (this destination ONLY).
                _write_research_file(job, state)
                _reprocess_destination_batch(db, dest)
                job.stage = "LINK_ROUTE"

            elif stage == "LINK_ROUTE":
                state["route_record"] = _build_route_record(job, state)
                job.stage = "IMPORT_TO_POSTGRESQL"

            elif stage == "IMPORT_TO_POSTGRESQL":
                rec = state.get("route_record")
                if rec:
                    tmp = Path(pipeline.RESEARCH_DIR) / dest / f"route-{job.id}.jsonl"
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_text(json.dumps(rec) + "\n")
                    rep = importer.import_file(db, tmp, apply=True,
                                               actor=f"ondemand:{job.id}", batch_id=job.id)
                    state["import_report"] = {k: v for k, v in rep.as_dict().items()
                                              if k != "errors"}
                    tmp.unlink(missing_ok=True)
                job.stage = "EXPORT_TO_JSONL"

            elif stage == "EXPORT_TO_JSONL":
                from .export import export_all
                try:
                    export_all(db)
                except Exception as e:  # noqa: BLE001 - export is re-runnable
                    _note(job, stage, f"export deferred: {e}")
                job.stage = "EVALUATE_ADAPTER_READINESS"

            elif stage == "EVALUATE_ADAPTER_READINESS":
                job.stage = "RETURN_ROUTE_STATUS"

            elif stage == "RETURN_ROUTE_STATUS":
                final = resolve(db, org_id=job.org_id, answers=_answers_from(job),
                                case_id=job.case_id)
                job.result = dict(job.result or {}, resolution=_slim(final),
                                  date_honesty=_date_honesty(job))
                if job.intake_id:
                    intake = db.get(RouteIntake, job.intake_id)
                    if intake:
                        intake.resolution_id = final["resolution_id"]
                if state.get("material_conflict"):
                    _finish("conflicted", "material conflict between official sources")
                elif (job.result.get("reused_stored")
                      or final["checks"]["snapshot_resolution"]["disposition"] in RESEARCHED):
                    _finish("complete", f"readiness {final['readiness_status']}")
                else:
                    db.add(HumanReviewTask(org_id=job.org_id, snapshot_date=SNAPSHOT_DATE,
                                           kind="route_review", subject_id=job.route_key,
                                           title="On-demand research incomplete",
                                           detail={"job_id": job.id}))
                    _finish("research_incomplete",
                            "insufficient official evidence; review task created")
                job.counters = _slim_counters(state)
                db.commit()
                return job

            job.counters = _slim_counters(state)
            db.commit()
    except Exception as e:  # noqa: BLE001 - job never crashes the API/worker
        db.rollback()
        job = db.get(OnDemandRouteResearchJob, job_id)
        job.error = str(e)[:400]
        job.status = "failed"
        job.finished_at = _now()
        db.commit()
        return job


def _answers_from(job) -> dict:
    r = dict(job.route)
    r.setdefault("arrival_date", r.get("policy_period"))
    return r


def _slim(resolution: dict) -> dict:
    return {"resolution_id": resolution["resolution_id"],
            "readiness_status": resolution["readiness_status"],
            "route_key": resolution.get("route_key"),
            "disposition": resolution.get("disposition"),
            "snapshot": resolution.get("snapshot")}


def _slim_counters(state: dict) -> dict:
    return {"queries_used": state.get("queries_used", 0),
            "pages_fetched": len(state.get("pages", [])),
            "fetch_failures": state.get("fetch_failures", [])[:10],
            "gov_candidates": state.get("gov_candidates", 0),
            "disposition": state.get("disposition"),
            "material_conflict": state.get("material_conflict", False),
            "extraction_fields": len(((state.get("extraction") or {}).get("fields", {}))),
            "extraction_rejected": len(((state.get("extraction") or {}).get("rejected", []))),
            # persisted so IMPORT/LINK stages survive a worker restart
            "pages": [{k: v for k, v in p.items() if k != "text"} for p in state.get("pages", [])],
            "route_record": state.get("route_record"),
            "fee": state.get("fee"), "portal": state.get("portal"),
            "jurisdiction": state.get("jurisdiction"),
            "conflicts": state.get("conflicts", []),
            "disposition_sources": state.get("disposition_sources", []),
            "extraction": state.get("extraction"),
            "candidates": state.get("candidates", []),
            "fetch_order": state.get("fetch_order", [])}


def _date_honesty(job) -> dict:
    on_snapshot = job.researched_at_date == SNAPSHOT_DATE
    return {
        "researched_at": job.researched_at_date,
        "snapshot_date": SNAPSHOT_DATE,
        "snapshot_supported": on_snapshot,
        "label": ("Snapshot rule captured as of July 23, 2026" if on_snapshot else
                  f"Route researched on demand on {job.researched_at_date}"),
    }


def _write_research_file(job, state) -> None:
    """Merge this job's evidence into the shared per-destination research file
    (the reusable cache the pipeline ingests). Never deletes prior content."""
    dest = job.route["destination_country"]
    path = pipeline.research_file(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"destination": dest, "retrieved_at": _now(), "research_status":
            "research_incomplete", "sources": [], "dispositions": [], "policies": [],
            "fees": [], "portals": [], "jurisdictions": [], "unresolved": []}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    have_hashes = {s.get("content_sha256") or s.get("content_hash") for s in data.get("sources", [])}
    for p in state.get("pages", []):
        if p["content_hash"] in have_hashes:
            continue
        data.setdefault("sources", []).append({
            "search_query": f"on-demand:{job.route_key}"[:200],
            "original_url": p["url"], "final_url": p["url"],
            "redirect_chain": p.get("redirect_chain", []),
            "final_hostname": p["hostname"],
            "source_authority": ("destination_government"
                                 if is_government_host(p["hostname"]) else "non_authoritative"),
            "relevant_excerpt": p["text"][:1500],
            "retrieved_at": p["retrieved_at"], "content_sha256": p["content_hash"],
            "page_language": p.get("page_language", "")})
    disp = state.get("disposition")
    if disp:
        nat = job.route["passport_nationality"]
        cat = job.route["visa_category"]
        entry = {"nationalities": [nat], "category": cat, "disposition": disp,
                 "maximum_stay": ((state.get("extraction") or {}).get("fields", {})
                                  .get("maximum_stay", {}) or {}).get("value"),
                 "conditions": [], "source_urls": state.get("disposition_sources", [])}
        existing = [d for d in data.get("dispositions", [])
                    if d.get("nationalities") == [nat] and d.get("category") == cat]
        if not any(d.get("disposition") == disp for d in existing):
            data.setdefault("dispositions", []).append(entry)
    fee = state.get("fee")
    if fee:
        data.setdefault("fees", []).append({
            "visa_category": job.route["visa_category"],
            "applies_to_nationalities": [job.route["passport_nationality"]],
            "government_fee_amount": fee["amount"],
            "government_fee_currency": fee["currency"], "source_urls": fee["sources"]})
    portal = state.get("portal")
    if portal and not any(p.get("url") == portal for p in data.get("portals", [])):
        data.setdefault("portals", []).append({
            "portal_kind": "evisa_portal" if "evisa" in portal.lower() else "immigration_authority",
            "url": portal, "operator_kind": "government", "source_urls": [portal]})
    for f in state.get("fetch_failures", []):
        data.setdefault("unresolved", []).append(
            f"source unavailable: {f['url']} ({f['error'][:80]})")
    # File-level status: only DOWNGRADE (conflict), never upgrade — a verified
    # file status would let the pipeline trust EVERY disposition in the file,
    # and one grounded route must not promote unrelated unverified entries.
    # This route's own verified status lives on its VisaRoute record instead
    # (created via the importer; resolution prefers route records over matrix).
    if state.get("material_conflict"):
        data["research_status"] = "conflicted"
    elif not data.get("research_status"):
        data["research_status"] = "research_incomplete"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def _reprocess_destination_batch(db, dest: str) -> None:
    batch = db.execute(select(SnapshotResearchBatch).where(
        SnapshotResearchBatch.snapshot_date == SNAPSHOT_DATE,
        SnapshotResearchBatch.batch_key == f"{SNAPSHOT_DATE}:{dest}")).scalar_one_or_none()
    if batch is None:
        batch = SnapshotResearchBatch(snapshot_date=SNAPSHOT_DATE,
                                      batch_key=f"{SNAPSHOT_DATE}:{dest}",
                                      destination_country=dest, stage="GROUP")
        db.add(batch)
        db.commit()
    else:
        batch.stage = "VERIFY_SOURCES"   # idempotent stages dedupe by hash
        batch.result = ""
        db.commit()
    pipeline.process_batch(db, batch)


def _build_route_record(job, state) -> dict | None:
    disp = state.get("disposition")
    ex = (state.get("extraction") or {}).get("fields", {})
    r = job.route
    verified = bool(disp and state.get("disposition_sources")
                    and not state.get("material_conflict"))
    if state.get("material_conflict"):
        research_status = "conflicted"
    elif verified:
        research_status = "verified"
    else:
        research_status = "research_incomplete"

    def v(name, default=None):
        return (ex.get(name) or {}).get("value", default)

    all_urls = sorted({u for f in ex.values() for u in (f.get("sources") or [])})
    on_snapshot = job.researched_at_date == SNAPSHOT_DATE
    rec = {
        "snapshot_date": SNAPSHOT_DATE,
        "record_id": f"od-{job.id}",
        "version": 1,
        "route_key": job.route_key,
        "passport_nationality": r["passport_nationality"],
        "passport_issuing_country": r["passport_issuing_country"],
        "travel_document_type": r["travel_document_type"],
        "lawful_country_of_residence": r["lawful_country_of_residence"],
        "lawful_residence_status": r.get("lawful_residence_status"),
        "destination_country": r["destination_country"],
        "visa_category": r["visa_category"],
        "visa_subtype": r.get("visa_subtype"),
        "travel_purpose": "tourism",
        "applicable_arrival_start": r.get("policy_period"),
        "applicable_arrival_end": None,
        "maximum_stay": v("maximum_stay"),
        "permitted_entries": v("permitted_entries"),
        "transit_conditions": v("transit_conditions"),
        "visa_required": None if disp is None else disp not in ("VISA_FREE", "NOT_APPLICABLE"),
        "visa_free": None if disp is None else disp == "VISA_FREE",
        "visa_on_arrival": None if disp is None else disp == "VISA_ON_ARRIVAL",
        "electronic_authorization_required": None if disp is None else disp == "ETA_REQUIRED",
        "evisa_available": None if disp is None else disp == "EVISA_REQUIRED",
        "disposition": disp or "RESEARCH_INCOMPLETE",
        "exemption_conditions": _as_list(v("exemption_conditions")),
        "required_documents": _as_list(v("required_documents")),
        "conditional_documents": None,
        "financial_evidence_requirements": None,
        "accommodation_evidence": None,
        "itinerary_evidence": None,
        "insurance_requirements": None,
        "invitation_requirements": None,
        "passport_validity_rule": ({"text": v("passport_validity_rule")}
                                   if v("passport_validity_rule") else None),
        "blank_page_rule": v("blank_page_rule"),
        "passport_condition_rule": None,
        "biometrics_required": _as_bool(v("biometrics_required")),
        "interview_required": _as_bool(v("interview_required")),
        "personal_appearance_required": _as_bool(v("personal_appearance_required")),
        "appointment_required": _as_bool(v("appointment_required")),
        "application_channel": _channel(disp),
        "competent_embassy_or_consulate": _text(v("competent_embassy_or_consulate")),
        "consular_jurisdiction": _text(v("consular_jurisdiction")),
        "official_application_portal": state.get("portal"),
        "authorized_visa_center": _text(v("authorized_visa_center")),
        "government_fee": ({"amount": state["fee"]["amount"],
                            "currency": state["fee"]["currency"], "notes": None}
                           if state.get("fee") else None),
        "service_fee": None, "appointment_fee": None, "biometrics_fee": None,
        "courier_fee": None, "optional_fees": None,
        "currency": (state.get("fee") or {}).get("currency"),
        "refundability": _text(v("refundability")),
        "accepted_payment_methods": _as_list(v("accepted_payment_methods")),
        "processing_time_guidance": _text(v("processing_time_guidance")),
        "third_party_preparation_allowed": _as_bool(v("third_party_preparation_allowed")),
        "third_party_submission_allowed": _as_bool(v("third_party_submission_allowed")),
        "representative_conditions": _text(v("representative_conditions")),
        "applicant_declaration_required": _as_bool(v("applicant_declaration_required")),
        "official_source_urls": all_urls or None,
        "official_verification_urls": None,
        "source_authorities": sorted({("destination_government"
                                       if is_government_host(_host(u)) else "non_authoritative")
                                      for u in all_urls}) or None,
        "source_excerpts": None,
        "source_hashes": [p["content_hash"] for p in state.get("pages", [])] or None,
        "retrieved_at": _now(),
        "effective_from": _text(v("effective_from")),
        "effective_until": _text(v("effective_until")),
        "review_after": None,
        "human_review_status": "not_reviewed",
        "research_status": research_status,
        "fee_status": "verified" if state.get("fee") else "unknown",
        "portal_verification_status": "verified" if state.get("portal") else "unknown",
        "adapter_status": "none",
        "conflict_status": "open" if state.get("material_conflict") else "none",
        "researched_on_demand_at": None if on_snapshot else job.researched_at_date,
        "machine_verification_status": ("kimi_grounded_extraction"
                                        if state.get("extraction") else "no_extraction"),
        "content_hash": "0" * 64,
        "notes": (None if on_snapshot else
                  f"Route researched on demand on {job.researched_at_date}; this is a "
                  f"later manually-researched version, NOT evidence of the "
                  f"{SNAPSHOT_DATE} snapshot state."),
    }
    from .routekey import canonical_hash
    rec["content_hash"] = canonical_hash(rec)
    return rec


def _as_list(v):
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("yes", "true", "required"):
            return True
        if low in ("no", "false", "not required"):
            return False
    return None


def _text(v):
    return str(v) if v not in (None, "") else None


def _channel(disp):
    return {"EVISA_REQUIRED": "online_portal", "ETA_REQUIRED": "online_portal",
            "VISA_ON_ARRIVAL": "on_arrival", "EMBASSY_VISA_REQUIRED": "embassy",
            "AUTHORIZED_VISA_CENTER_REQUIRED": "visa_center",
            "VISA_FREE": "not_required", "NOT_APPLICABLE": "not_required"}.get(disp, "unknown")


def _host(url: str) -> str:
    from .authority import hostname
    return hostname(url)
