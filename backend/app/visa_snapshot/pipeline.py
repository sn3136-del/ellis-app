"""One-time resumable research pipeline (brief section 13).

Persistent destination-based batches walk the 12 stages:
  ENUMERATE -> GROUP -> DISCOVER_OFFICIAL_SOURCES -> VERIFY_SOURCES ->
  EXTRACT_CANDIDATE_RULES -> NORMALIZE -> DETECT_CONFLICTS ->
  CREATE_POLICY_VERSIONS -> LINK_ROUTE_ENTRIES -> VERIFY_FEES ->
  QUEUE_HUMAN_REVIEW -> EXPORT_SNAPSHOT

Every batch persists stage/attempts/counts/hashes in SnapshotResearchBatch, so
processing is resumable at any point (worker restart, partial input). This is
ONE-TIME processing: no recurring schedules exist anywhere in this package.

Research INPUT is file-based: the one-time public-source research run writes
data/snapshots/2026-07-23/research/<ALPHA3>/research.json with sources,
candidate dispositions, policies, fees, portals and jurisdictions, each backed
by evidence (query, URL chain, hostname, excerpt, hash, retrieval time).
The pipeline deterministically verifies domains (authority.py), normalizes,
detects conflicts, creates immutable versions and links matrix entries.
A batch with no research input parks at DISCOVER_OFFICIAL_SOURCES with result
'awaiting_research_input' — honest, resumable, never guessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from . import SNAPSHOT_DATE
from .authority import classify_evidence, hostname, is_government_host
from .models import (ConsularJurisdictionRule, HumanReviewTask, OfficialPortalRecord,
                     PassportValidityRuleRecord, RouteMatrixEntry, SnapshotConflict,
                     SnapshotResearchBatch, SourceEvidence, VisaFeeVersion,
                     VisaPolicy, VisaPolicyVersion)
from .registry import SNAPSHOT_DIR, load_registry, normalize_country
from .routekey import canonical_hash

STAGES = ("ENUMERATE", "GROUP", "DISCOVER_OFFICIAL_SOURCES", "VERIFY_SOURCES",
          "EXTRACT_CANDIDATE_RULES", "NORMALIZE", "DETECT_CONFLICTS",
          "CREATE_POLICY_VERSIONS", "LINK_ROUTE_ENTRIES", "VERIFY_FEES",
          "QUEUE_HUMAN_REVIEW", "EXPORT_SNAPSHOT")

RESEARCH_DIR = SNAPSHOT_DIR / SNAPSHOT_DATE / "research"

DISPOSITIONS = ("VISA_FREE", "ETA_REQUIRED", "EVISA_REQUIRED", "VISA_ON_ARRIVAL",
                "EMBASSY_VISA_REQUIRED", "AUTHORIZED_VISA_CENTER_REQUIRED",
                "TRANSIT_AUTHORIZATION_REQUIRED", "NO_TOURIST_ROUTE", "NOT_APPLICABLE",
                "RESEARCH_INCOMPLETE", "CONFLICTED", "HUMAN_REVIEW_REQUIRED")


KNOWN_CATEGORIES = ("tourist_visa", "evisa_tourist", "eta", "visa_on_arrival_tourist",
                    "visa_free_entry", "transit_authorization")

KNOWN_POLICY_KINDS = ("visa_exemption_list", "evisa_program", "eta_program",
                      "visa_on_arrival_program", "consular_visa_regime",
                      "transit_regime", "special_regime")


def _normalize_category_label(raw) -> str:
    """Map a research-file category label onto the fixed taxonomy. Free-text
    labels (e.g. destination-specific visa class names) normalize to
    tourist_visa; the original label is preserved in the record JSON."""
    v = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if v in KNOWN_CATEGORIES:
        return v
    if "arrival" in v:
        return "visa_on_arrival_tourist"
    if "evisa" in v or "e_visa" in v:
        return "evisa_tourist"
    if v in ("eta", "electronic_travel_authorization") or "authorization" in v:
        return "eta"
    if "transit" in v:
        return "transit_authorization"
    if "exempt" in v or "free" in v or "waiver" in v:
        return "visa_free_entry"
    return "tourist_visa"


def _now_label() -> str:
    # Deterministic-friendly label; actual timestamps come from evidence records.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enumerate_batches(db) -> dict:
    """Stage 1: one batch per destination country (idempotent)."""
    created = 0
    for c in load_registry("countries")["entries"]:
        dest = c["alpha_3"]
        key = f"{SNAPSHOT_DATE}:{dest}"
        existing = db.execute(select(SnapshotResearchBatch).where(
            SnapshotResearchBatch.snapshot_date == SNAPSHOT_DATE,
            SnapshotResearchBatch.batch_key == key)).scalar_one_or_none()
        if existing:
            continue
        db.add(SnapshotResearchBatch(
            snapshot_date=SNAPSHOT_DATE, batch_key=key, destination_country=dest,
            nationality_group="all", residence_group="all",
            stage="GROUP", started_at=_now_label()))
        created += 1
    db.commit()
    return {"created": created}


def research_file(dest: str) -> Path:
    return RESEARCH_DIR / dest / "research.json"


def _load_research(dest: str) -> dict | None:
    path = research_file(dest)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"__parse_error__": True}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def process_batch(db, batch: SnapshotResearchBatch, *, max_stages: int = 12) -> SnapshotResearchBatch:
    """Advance one batch as far as possible. Each stage transition is committed
    so a crash resumes exactly where it stopped."""
    steps = 0
    while steps < max_stages:
        steps += 1
        stage = batch.stage
        if stage == "GROUP":
            # Single global group in this snapshot: dispositions carry their own
            # nationality applicability lists.
            batch.stage = "DISCOVER_OFFICIAL_SOURCES"

        elif stage == "DISCOVER_OFFICIAL_SOURCES":
            data = _load_research(batch.destination_country)
            if data is None:
                batch.result = "awaiting_research_input"
                batch.attempt_count += 1
                db.commit()
                return batch
            if data.get("__parse_error__"):
                batch.result = "research_input_invalid"
                batch.attempt_count += 1
                db.commit()
                return batch
            batch.result = ""
            batch.sources = [s.get("final_url", "") for s in data.get("sources", [])][:200]
            batch.stage = "VERIFY_SOURCES"

        elif stage == "VERIFY_SOURCES":
            data = _load_research(batch.destination_country) or {}
            verified = 0
            hashes = []
            for s in data.get("sources", []):
                final_url = s.get("final_url") or s.get("original_url") or ""
                if not final_url:
                    continue
                chash = s.get("content_sha256") or _sha(s.get("relevant_excerpt", "") + final_url)
                # Idempotent: skip if this evidence hash already stored for the dest.
                dup = db.execute(select(SourceEvidence).where(
                    SourceEvidence.snapshot_date == SNAPSHOT_DATE,
                    SourceEvidence.applicable_jurisdiction == batch.destination_country,
                    SourceEvidence.content_hash == chash)).scalar_one_or_none()
                status = classify_evidence({**s, "final_url": final_url,
                                            "final_hostname": s.get("final_hostname") or hostname(final_url)})
                if dup:
                    hashes.append(chash)
                    verified += 1 if status == "verified" else 0
                    continue
                db.add(SourceEvidence(
                    snapshot_date=SNAPSHOT_DATE,
                    search_query=(s.get("search_query") or "")[:2000],
                    original_url=(s.get("original_url") or final_url)[:1000],
                    final_url=final_url[:1000],
                    redirect_chain=s.get("redirect_chain") or [],
                    final_hostname=(s.get("final_hostname") or hostname(final_url))[:300],
                    source_authority=(s.get("source_authority") or "non_authoritative")
                        if status == "verified" else "non_authoritative",
                    applicable_jurisdiction=batch.destination_country,
                    relevant_excerpt=(s.get("relevant_excerpt") or "")[:5000],
                    retrieved_at=(s.get("retrieved_at") or data.get("retrieved_at") or "")[:40],
                    effective_date_evidence=(s.get("effective_date_evidence") or "")[:2000],
                    content_hash=chash,
                    page_language=(s.get("page_language") or "")[:16],
                    verification_status=status,
                ))
                hashes.append(chash)
                verified += 1 if status == "verified" else 0
            batch.content_hashes = hashes[:200]
            batch.detail = dict(batch.detail or {}, sources_total=len(data.get("sources", [])),
                                sources_verified=verified)
            batch.stage = "EXTRACT_CANDIDATE_RULES"

        elif stage == "EXTRACT_CANDIDATE_RULES":
            data = _load_research(batch.destination_country) or {}
            batch.detail = dict(batch.detail or {},
                                dispositions=len(data.get("dispositions", [])),
                                policies=len(data.get("policies", [])),
                                fees=len(data.get("fees", [])),
                                portals=len(data.get("portals", [])))
            batch.stage = "NORMALIZE"

        elif stage == "NORMALIZE":
            data = _load_research(batch.destination_country) or {}
            problems = []
            for d in data.get("dispositions", []):
                if d.get("disposition") not in DISPOSITIONS:
                    problems.append(f"bad disposition {d.get('disposition')!r}")
                for nat in d.get("nationalities", []):
                    if nat in ("ALL", "ALL_OTHERS"):
                        continue
                    try:
                        normalize_country(nat, field="nationality")
                    except Exception:
                        # Special classes (XXA...) are registry codes, not countries.
                        codes = {e["code"] for e in load_registry("nationalities")["entries"]}
                        if nat not in codes:
                            problems.append(f"unknown nationality {nat!r}")
            batch.detail = dict(batch.detail or {}, normalize_problems=problems[:20])
            batch.stage = "DETECT_CONFLICTS"

        elif stage == "DETECT_CONFLICTS":
            data = _load_research(batch.destination_country) or {}
            # Two candidate dispositions for the same (nationality, category)
            # from different sources that disagree -> conflict row.
            seen: dict[tuple, str] = {}
            conflicts = 0
            for d in data.get("dispositions", []):
                cat = _normalize_category_label(d.get("category"))
                for nat in d.get("nationalities", []) or ["ALL"]:
                    k = (nat, cat)
                    if k in seen and seen[k] != d.get("disposition"):
                        dup = db.execute(select(SnapshotConflict).where(
                            SnapshotConflict.snapshot_date == SNAPSHOT_DATE,
                            SnapshotConflict.destination_country == batch.destination_country,
                            SnapshotConflict.field == f"{nat}:{cat}")).scalar_one_or_none()
                        if not dup:
                            db.add(SnapshotConflict(
                                snapshot_date=SNAPSHOT_DATE, route_key="",
                                destination_country=batch.destination_country,
                                field=f"{nat}:{cat}",
                                values=[{"value": seen[k]}, {"value": d.get("disposition")}]))
                        conflicts += 1
                    else:
                        seen[k] = d.get("disposition")
            batch.conflict_count = conflicts
            batch.stage = "CREATE_POLICY_VERSIONS"

        elif stage == "CREATE_POLICY_VERSIONS":
            data = _load_research(batch.destination_country) or {}
            created = 0
            for i, p in enumerate(data.get("policies", [])):
                kind = p.get("policy_kind", "special_regime")
                if kind not in KNOWN_POLICY_KINDS:
                    kind = "special_regime"
                uid = f"{batch.destination_country}:{kind}:{i}"[:120]
                head = db.execute(select(VisaPolicy).where(
                    VisaPolicy.policy_uid == uid)).scalar_one_or_none()
                rec = {**p, "snapshot_date": SNAPSHOT_DATE,
                       "destination_country": batch.destination_country}
                chash = canonical_hash(rec)
                if head is None:
                    head = VisaPolicy(snapshot_date=SNAPSHOT_DATE, policy_uid=uid,
                                      destination_country=batch.destination_country,
                                      policy_kind=kind,
                                      policy_name=(p.get("policy_name") or "")[:300],
                                      research_status=data.get("research_status", "research_incomplete"))
                    db.add(head)
                    db.flush()
                    db.add(VisaPolicyVersion(policy_id=head.id, version=1, record=rec,
                                             content_hash=chash))
                    created += 1
                else:
                    latest = db.execute(select(VisaPolicyVersion).where(
                        VisaPolicyVersion.policy_id == head.id)
                        .order_by(VisaPolicyVersion.version.desc())).scalars().first()
                    if latest and latest.content_hash == chash:
                        continue
                    new_v = (latest.version if latest else 0) + 1
                    if latest:
                        latest.superseded_by = new_v
                    db.add(VisaPolicyVersion(policy_id=head.id, version=new_v, record=rec,
                                             content_hash=chash))
                    head.current_version = new_v
                    created += 1
            pv = data.get("passport_validity")
            if pv and pv.get("rule_kind"):
                dup = db.execute(select(PassportValidityRuleRecord).where(
                    PassportValidityRuleRecord.snapshot_date == SNAPSHOT_DATE,
                    PassportValidityRuleRecord.destination_country == batch.destination_country,
                )).scalar_one_or_none()
                if not dup:
                    db.add(PassportValidityRuleRecord(
                        snapshot_date=SNAPSHOT_DATE,
                        destination_country=batch.destination_country,
                        rule_kind=pv.get("rule_kind", "unknown"),
                        months=pv.get("months"),
                        min_blank_pages=pv.get("min_blank_pages"),
                        condition_text=(pv.get("condition_text") or "")[:2000],
                        research_status=data.get("research_status", "research_incomplete"),
                        evidence_ids=pv.get("source_urls", [])[:20]))
            for portal in data.get("portals", []):
                url = portal.get("url", "")
                if not url:
                    continue
                uid = f"{batch.destination_country}:{portal.get('portal_kind','immigration_authority')}:{hostname(url)}"
                dup = db.execute(select(OfficialPortalRecord).where(
                    OfficialPortalRecord.portal_uid == uid)).scalar_one_or_none()
                if dup:
                    continue
                host = hostname(url)
                link = portal.get("official_linking_source") or ""
                if is_government_host(host):
                    vs = "verified_official_domain"
                elif link and is_government_host(hostname(link)):
                    vs = "verified_via_official_link"
                else:
                    vs = "unverified"
                db.add(OfficialPortalRecord(
                    snapshot_date=SNAPSHOT_DATE, portal_uid=uid,
                    destination_country=batch.destination_country,
                    portal_kind=portal.get("portal_kind", "immigration_authority"),
                    operator=(portal.get("operator") or "")[:300],
                    operator_kind=portal.get("operator_kind", "unknown"),
                    url=url[:500], hostnames=[host],
                    allowed_redirect_hosts=portal.get("allowed_redirect_hosts", [])[:20],
                    official_linking_source=link[:500],
                    supported_categories=portal.get("supported_categories", [])[:10],
                    verification_status=vs,
                    evidence_ids=portal.get("source_urls", [])[:20]))
            for j in data.get("jurisdictions", []):
                res = j.get("residence_jurisdiction", "")
                try:
                    res3 = normalize_country(res, field="residence") if res else ""
                except Exception:
                    res3 = ""
                if not res3:
                    continue
                dup = db.execute(select(ConsularJurisdictionRule).where(
                    ConsularJurisdictionRule.snapshot_date == SNAPSHOT_DATE,
                    ConsularJurisdictionRule.destination_country == batch.destination_country,
                    ConsularJurisdictionRule.residence_jurisdiction == res3)).scalar_one_or_none()
                if dup:
                    continue
                url = j.get("competent_post_url", "")
                db.add(ConsularJurisdictionRule(
                    snapshot_date=SNAPSHOT_DATE,
                    destination_country=batch.destination_country,
                    residence_jurisdiction=res3,
                    residence_subdivisions=j.get("residence_subdivisions", [])[:60],
                    competent_post_name=(j.get("competent_post_name") or "")[:300],
                    competent_post_kind=j.get("competent_post_kind", "unknown"),
                    competent_post_url=url[:500],
                    covers_nationalities=j.get("covers_nationalities", [])[:250],
                    conditions=j.get("conditions", [])[:20],
                    verification_status=("verified" if is_government_host(hostname(url))
                                         else "manual_review_required"),
                    evidence_ids=j.get("source_urls", [])[:20]))
            batch.record_count = created
            batch.stage = "LINK_ROUTE_ENTRIES"

        elif stage == "LINK_ROUTE_ENTRIES":
            data = _load_research(batch.destination_country) or {}
            research_status = data.get("research_status", "research_incomplete")
            trusted = research_status in ("verified",)
            nat_codes = [e["code"] for e in load_registry("nationalities")["entries"]]
            listed: set[tuple[str, str]] = set()
            linked = 0
            explicit: dict[tuple[str, str], str] = {}
            all_others: dict[str, str] = {}
            for d in data.get("dispositions", []):
                cat = _normalize_category_label(d.get("category"))
                disp = d.get("disposition", "RESEARCH_INCOMPLETE")
                if disp not in DISPOSITIONS:
                    continue
                nats = d.get("nationalities") or []
                if nats in (["ALL"],) or nats == "ALL":
                    all_others[cat] = disp
                    continue
                if nats in (["ALL_OTHERS"],) or nats == "ALL_OTHERS":
                    all_others[cat] = disp
                    continue
                for nat in nats:
                    explicit[(nat, cat)] = disp
                    listed.add((nat, cat))
            # Conflicted pairs are forced CONFLICTED regardless of source order.
            for c in db.execute(select(SnapshotConflict).where(
                    SnapshotConflict.snapshot_date == SNAPSHOT_DATE,
                    SnapshotConflict.destination_country == batch.destination_country,
                    SnapshotConflict.status == "open")).scalars().all():
                nat, _, cat = c.field.partition(":")
                explicit[(nat, cat)] = "CONFLICTED"
            if trusted or explicit or all_others:
                rows = db.execute(select(RouteMatrixEntry).where(
                    RouteMatrixEntry.snapshot_date == SNAPSHOT_DATE,
                    RouteMatrixEntry.destination_country == batch.destination_country,
                    RouteMatrixEntry.travel_document_type == "ordinary_passport",
                )).scalars().all()
                for row in rows:
                    if row.disposition == "NOT_APPLICABLE":
                        continue
                    k = (row.passport_nationality, row.visa_category)
                    disp = explicit.get(k)
                    if disp is None and row.visa_category in all_others:
                        disp = all_others[row.visa_category]
                    if disp is None:
                        continue
                    if not trusted and disp not in ("CONFLICTED",):
                        # Untrusted research can only mark review states, never
                        # a definitive disposition.
                        disp = "HUMAN_REVIEW_REQUIRED" if research_status == "human_review_required" else "RESEARCH_INCOMPLETE"
                    if row.disposition != disp:
                        row.disposition = disp
                        linked += 1
                del rows
            batch.detail = dict(batch.detail or {}, linked_entries=linked,
                                research_status=research_status)
            batch.stage = "VERIFY_FEES"

        elif stage == "VERIFY_FEES":
            data = _load_research(batch.destination_country) or {}
            research_status = data.get("research_status", "research_incomplete")
            created = 0
            for i, f in enumerate(data.get("fees", [])):
                cat = _normalize_category_label(f.get("visa_category"))
                uid = f"{batch.destination_country}:{cat}:{i}"[:160]
                dup = db.execute(select(VisaFeeVersion).where(
                    VisaFeeVersion.fee_uid == uid)).scalar_one_or_none()
                if dup:
                    continue
                rec = {**f, "snapshot_date": SNAPSHOT_DATE,
                       "visa_category": cat,
                       "visa_category_source_label": f.get("visa_category"),
                       "destination_country": batch.destination_country}
                srcs = f.get("source_urls", []) or f.get("official_source_urls", [])
                fee_verified = (research_status == "verified" and
                                any(is_government_host(hostname(u)) for u in srcs))
                db.add(VisaFeeVersion(
                    snapshot_date=SNAPSHOT_DATE, fee_uid=uid, version=1,
                    destination_country=batch.destination_country,
                    visa_category=cat,
                    record=rec, content_hash=canonical_hash(rec),
                    fee_status="verified" if fee_verified else "unknown"))
                created += 1
            batch.detail = dict(batch.detail or {}, fee_versions=created)
            batch.stage = "QUEUE_HUMAN_REVIEW"

        elif stage == "QUEUE_HUMAN_REVIEW":
            data = _load_research(batch.destination_country) or {}
            queued = 0
            items = list(data.get("unresolved", []))
            if data.get("research_status") in ("research_incomplete", "human_review_required", "conflicted"):
                items.append(f"research status {data.get('research_status')} for {batch.destination_country}")
            for item in items[:50]:
                title = str(item)[:400]
                dup = db.execute(select(HumanReviewTask).where(
                    HumanReviewTask.snapshot_date == SNAPSHOT_DATE,
                    HumanReviewTask.subject_id == batch.destination_country,
                    HumanReviewTask.title == title)).scalar_one_or_none()
                if dup:
                    continue
                db.add(HumanReviewTask(snapshot_date=SNAPSHOT_DATE, kind="route_review",
                                       subject_id=batch.destination_country, title=title,
                                       detail={"destination": batch.destination_country}))
                queued += 1
            batch.review_count = queued
            batch.stage = "EXPORT_SNAPSHOT"

        elif stage == "EXPORT_SNAPSHOT":
            batch.result = "complete"
            batch.completed_at = _now_label()
            db.commit()
            return batch

        else:  # ENUMERATE or unknown — normalize forward
            batch.stage = "GROUP"
        db.commit()
    return batch


def resume(db, *, limit: int | None = None) -> dict:
    """Advance every incomplete batch as far as inputs allow. Safe to re-run."""
    q = select(SnapshotResearchBatch).where(
        SnapshotResearchBatch.snapshot_date == SNAPSHOT_DATE,
        SnapshotResearchBatch.result != "complete").order_by(SnapshotResearchBatch.batch_key)
    batches = db.execute(q).scalars().all()
    if limit:
        batches = batches[:limit]
    done = awaiting = invalid = errored = 0
    errors: list[dict] = []
    for b in batches:
        try:
            b = process_batch(db, b)
        except Exception as e:  # noqa: BLE001 - one bad batch never kills the run
            db.rollback()
            errored += 1
            try:
                b.attempt_count += 1
                b.result = "error"
                b.detail = dict(b.detail or {}, last_error=str(e)[:400])
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            if len(errors) < 10:
                errors.append({"batch": b.batch_key, "error": str(e)[:200]})
            continue
        if b.result == "complete":
            done += 1
        elif b.result == "awaiting_research_input":
            awaiting += 1
        elif b.result == "research_input_invalid":
            invalid += 1
    return {"processed": len(batches), "completed_now": done,
            "awaiting_research_input": awaiting, "research_input_invalid": invalid,
            "errored": errored, "errors": errors}


def status(db) -> dict:
    from sqlalchemy import func
    rows = db.execute(select(SnapshotResearchBatch.stage, SnapshotResearchBatch.result,
                             func.count(SnapshotResearchBatch.id))
                      .where(SnapshotResearchBatch.snapshot_date == SNAPSHOT_DATE)
                      .group_by(SnapshotResearchBatch.stage, SnapshotResearchBatch.result)).all()
    return {"snapshot_date": SNAPSHOT_DATE,
            "batches": [{"stage": s, "result": r, "count": c} for s, r, c in rows]}
