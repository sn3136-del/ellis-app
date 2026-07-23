"""Candidate generation (brief §17): immutable versioned artifacts.

Every candidate version is a complete, self-describing bundle written to
backend/app/portal_adapters/generated/<adapter_id>/<version>/ AND persisted in
the DB (the DB copy is authoritative; the directory is the reviewable
artifact). A released version is never modified: repairs create version N+1.

The generated artifact is deliberately DATA, not code: a typed flow the
deterministic runtime interprets. Nothing generated is ever imported or
executed as Python, which structurally rules out the §18 code-injection
classes — and the static validator still scans every string in the bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from sqlalchemy import select

from .. import audit
from . import models as fm

_GENERATED_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "portal_adapters", "generated")


def _hash_bundle(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _safe_adapter_id(route_key: str, destination: str) -> str:
    base = f"{destination or 'route'}-{hashlib.sha256(route_key.encode()).hexdigest()[:10]}"
    return re.sub(r"[^a-zA-Z0-9_\-]", "-", base).lower()


def get_or_create_candidate(db, *, build_request: fm.AdapterBuildRequest) -> fm.AdapterCandidate:
    cand = db.execute(select(fm.AdapterCandidate).where(
        fm.AdapterCandidate.build_request_id == build_request.id)).scalars().first()
    if cand:
        return cand
    cand = fm.AdapterCandidate(
        build_request_id=build_request.id, route_key=build_request.route_key,
        adapter_id=_safe_adapter_id(build_request.route_key, build_request.destination))
    db.add(cand)
    db.commit()
    return cand


def generate_candidate_version(db, *, build_request: fm.AdapterBuildRequest,
                               spec: fm.AdapterSpecification,
                               created_by: str = "ellis-factory",
                               repair_of_version: int = 0,
                               limitations: list[str] | None = None) -> fm.AdapterCandidateVersion:
    cand = get_or_create_candidate(db, build_request=build_request)
    version = cand.current_version + 1
    manifest = {
        "adapter_id": cand.adapter_id,
        "version": version,
        "route_key": build_request.route_key,
        "route_scope": {"destination": build_request.destination,
                        "visa_type": build_request.visa_type},
        "portal_operator": spec.portal_operator,
        "allowed_hostnames": spec.allowed_hostnames,
        "allowed_redirect_hosts": spec.allowed_redirect_hosts,
        "specification_id": spec.id,
        "generator": spec.generator,
        "artifact_kind": "typed_flow_v1",
        "runtime": "deterministic_flow_runner",
    }
    evidence_rules = {
        "success_requires": ["network_or_official_record"],
        "banner_text_sufficient": False,
        "strength_ladder": ["network_state_transition", "official_record",
                            "official_receipt", "session_state_change",
                            "durable_url_change", "dom_evidence", "visible_text"],
    }
    recovery = {
        "resume": "reconcile_before_retry",
        "on_uncertain_outcome": "stop_and_reconcile",
        "on_unexpected_state": "fail_closed",
        "rate_limits": {"actions_per_minute": 30},
    }
    row = fm.AdapterCandidateVersion(
        candidate_id=cand.id, version=version, specification_id=spec.id,
        manifest=manifest, flow=spec.flow, field_mappings=spec.field_mappings,
        document_mappings=spec.document_mappings, evidence_rules=evidence_rules,
        recovery=recovery, kill_switch_key=f"adapter:{cand.adapter_id}",
        rollback_to_version=max(version - 1, 0),
        known_limitations=list(limitations or []),
        created_by=created_by, repair_of_version=repair_of_version)
    row.content_hash = _hash_bundle(manifest, spec.flow, spec.field_mappings,
                                    spec.document_mappings, evidence_rules, recovery)
    row.storage_dir = _write_bundle(cand.adapter_id, version, row)
    db.add(row)
    cand.current_version = version
    cand.status = "testing"
    build_request.current_candidate_id = cand.id
    db.commit()
    audit.record(db, org_id=build_request.org_id, application_id=build_request.application_id,
                 action="adapter_candidate_generated",
                 detail={"adapter_id": cand.adapter_id, "version": version,
                         "content_hash": row.content_hash,
                         "repair_of_version": repair_of_version},
                 actor="ellis")
    return row


def _write_bundle(adapter_id: str, version: int, row: fm.AdapterCandidateVersion) -> str:
    d = os.path.join(_GENERATED_ROOT, adapter_id, str(version))
    os.makedirs(d, exist_ok=True)
    def w(name, obj):
        with open(os.path.join(d, name), "w") as f:
            if name.endswith(".json"):
                json.dump(obj, f, indent=1, sort_keys=True, default=str)
            else:
                f.write(obj)
    w("manifest.json", row.manifest)
    w("flow.json", row.flow)
    w("mappings.json", {"fields": row.field_mappings, "documents": row.document_mappings})
    w("evidence.json", row.evidence_rules)
    w("recovery.json", row.recovery)
    w("limitations.md", "# Known limitations\n" +
      "".join(f"- {l}\n" for l in (row.known_limitations or ["- none recorded"])))
    return d


def get_version(db, candidate_id: str, version: int) -> fm.AdapterCandidateVersion | None:
    return db.execute(select(fm.AdapterCandidateVersion).where(
        fm.AdapterCandidateVersion.candidate_id == candidate_id,
        fm.AdapterCandidateVersion.version == version)).scalar_one_or_none()
