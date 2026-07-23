"""Credential-free portal reconnaissance (brief §14).

Observes PUBLIC portal pages and stores only SANITIZED structure: element
roles, labels, input types, required flags, navigation relationships, URL
patterns. Free page text is dropped entirely — a hostile page cannot smuggle
instructions into the pipeline through a recon artifact, because prose never
survives sanitization (labels are hard-capped and stripped of directives).

The recon component can never receive credentials/cookies/values by
construction: its input is the structural observation interface, and
sanitize_structure() is the single chokepoint everything passes through.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from .. import audit
from . import models as fm

# Labels are the only portal-authored text that survives. Keep them short and
# strip anything that looks like an instruction or a URL.
_MAX_LABEL = 60
_DIRECTIVE_RE = re.compile(
    r"(ignore (all|previous)|system message|reveal|credential|cookie|approve|"
    r"instruction|password to|send .* to|https?://|@)", re.IGNORECASE)

_ALLOWED_TYPES = {"text", "email", "password", "date", "select", "checkbox",
                  "radio", "file", "button", "tel", "number"}

# Values that must never appear in an artifact even if a buggy observer leaks
# them: anything shaped like a secret or personal identifier.
_FORBIDDEN_KEY_RE = re.compile(
    r"(value|cookie|token|authorization|session|card|otp|password_value)",
    re.IGNORECASE)


class ReconRefused(Exception):
    """Raised when recon is pointed at something it may not touch."""


def sanitize_label(label: str) -> str:
    label = (label or "").strip()
    if _DIRECTIVE_RE.search(label):
        return "[stripped]"
    return label[:_MAX_LABEL]


def sanitize_structure(observation: dict) -> dict:
    """The single sanitization chokepoint. Whitelist-only: anything not
    explicitly copied here does not exist downstream."""
    elements = []
    for el in observation.get("elements", []) or []:
        etype = el.get("type") if el.get("type") in _ALLOWED_TYPES else "text"
        clean = {
            "selector": str(el.get("selector", ""))[:200],
            "name": re.sub(r"[^a-zA-Z0-9_\-]", "", str(el.get("name", "")))[:80],
            "label": sanitize_label(el.get("label", "")),
            "type": etype,
            "required": bool(el.get("required", False)),
            "sensitive": bool(el.get("sensitive", False)) or etype == "password",
        }
        if el.get("submits"):
            clean["submits"] = re.sub(r"[^a-z_]", "", str(el.get("submits", "")))[:40]
        if el.get("navigates_to"):
            clean["navigates_to"] = str(el.get("navigates_to", ""))[:200]
        elements.append(clean)
    out = {
        "url_pattern": _pattern(observation.get("url", "")),
        "hostname": str(observation.get("hostname", ""))[:200].lower(),
        "title": sanitize_label(observation.get("title", "")),
        "status": int(observation.get("status", 0)),
        "elements": elements,
        "links": [_pattern(l) for l in (observation.get("links") or [])][:20],
        "iframes": [_pattern(f) for f in (observation.get("iframes") or [])][:10],
        "delayed_content": bool(observation.get("delayed", False)),
    }
    _assert_sanitized(out)
    return out


def _pattern(url: str) -> str:
    """URLs are reduced to a pattern: query VALUES never survive."""
    url = str(url or "")
    base = url.split("?")[0]
    return base[:300]


def _assert_sanitized(obj, path="root"):
    """Defense in depth: refuse to emit an artifact containing forbidden keys
    or long free text anywhere in its tree."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _FORBIDDEN_KEY_RE.fullmatch(str(k)):
                raise ReconRefused(f"forbidden key {k!r} at {path}")
            _assert_sanitized(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_sanitized(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        if len(obj) > 300:
            raise ReconRefused(f"overlong text at {path} — free text must not survive recon")


def run_recon(db, *, build_request: fm.AdapterBuildRequest, observer,
              start_paths=("/", "/login", "/application", "/fees",
                           "/appointments", "/submit"),
              hostnames: list[str] | None = None) -> fm.AdapterReconJob:
    """Observe the portal's public pages through `observer(url) -> observation`
    and persist sanitized artifacts. `observer` is the structural interface —
    SyntheticPortal.observe in tests, a Browserbase/Playwright structural probe
    in live runs. It never receives credentials and never authenticates."""
    hosts = [h.lower() for h in (hostnames or (build_request.portal_evidence or {}).get("hostnames", []))]
    if not hosts:
        raise ReconRefused("no verified portal hostnames — recon may not guess where to look")
    job = fm.AdapterReconJob(build_request_id=build_request.id, org_id=build_request.org_id,
                             portal_hostnames=hosts, status="running")
    db.add(job)
    db.flush()
    observed = 0
    try:
        for host in hosts:
            for path in start_paths:
                url = f"https://{host}{path}"
                raw = observer(url)
                if not raw or not raw.get("ok"):
                    continue
                if str(raw.get("hostname", host)).lower() not in hosts:
                    continue    # never follow the portal off the verified hosts
                art = sanitize_structure(raw)
                db.add(fm.AdapterReconArtifact(
                    recon_job_id=job.id, page_key=path.strip("/") or "home",
                    hostname=art["hostname"], url_pattern=art["url_pattern"],
                    structure=art))
                observed += 1
        job.pages_observed = observed
        job.status = "complete" if observed else "failed"
        if not observed:
            job.error = "no public pages could be observed"
    except ReconRefused as e:
        job.status = "failed"
        job.error = str(e)[:400]
    db.commit()
    audit.record(db, org_id=build_request.org_id, application_id=build_request.application_id,
                 action="adapter_recon_finished",
                 detail={"job": job.id, "pages": observed, "status": job.status},
                 actor="ellis")
    return job


def artifacts(db, recon_job_id: str) -> list[fm.AdapterReconArtifact]:
    return db.execute(select(fm.AdapterReconArtifact).where(
        fm.AdapterReconArtifact.recon_job_id == recon_job_id)).scalars().all()
