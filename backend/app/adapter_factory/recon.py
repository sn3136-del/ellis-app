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
                  "radio", "file", "button", "tel", "number", "search-combobox"}

# Entry-gate replay vocabulary (declared, curated; see live_browser).
_ENTRY_GATE_ACTIONS = {"CLICK", "SCROLL_TO_BOTTOM", "CHECK"}
ENTRY_GATED_FORM_PAGE_KEY = "application_form"
ENTRY_GATED_FORM_CLASS = "application_form"

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
        if el.get("placeholder"):
            clean["placeholder"] = sanitize_label(el.get("placeholder", ""))
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
    # Entry-gate replay echo: WHICH declared reversible actions were performed
    # to reach this page (action names + selectors only — never values/text).
    if observation.get("entry_gate_replayed"):
        out["entry_gate_replayed"] = [
            {"action": s.get("action") if s.get("action") in _ENTRY_GATE_ACTIONS
             else "REFUSED",
             "selector": str(s.get("selector", ""))[:200],
             "ok": bool(s.get("ok"))}
            for s in list(observation["entry_gate_replayed"])[:12]]
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


# Visa/application path markers. Real portals localise their URLs, so the
# pattern covers the common non-English forms too (vi: thi-thuc/khai/ho-so,
# es/pt: visado/solicitud/tramite, fr: demande/formulaire, de: antrag,
# tr: basvuru, id/ms: permohonan, pl: wniosek).
_LINK_FOLLOW_RE = re.compile(
    r"appl(y|ication)|visa|e-?visa|eta\b|arrival|form|fee|appointment|register"
    r"|thi-?thuc|khai|ho-?so|visado|solicitud|tramite|demande|formulaire"
    r"|antrag|basvuru|permohonan|wniosek|zayavlenie|shinsei",
    re.IGNORECASE)
# Paths a portal serves for "this does not exist" — never a real flow page.
_ERROR_PATH_RE = re.compile(r"/(errors?|404|not-?found|denied|forbidden)(/|$)",
                            re.IGNORECASE)
_MAX_FOLLOWED_LINKS = 8


def _page_key_for(path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (path.strip("/") or "home").lower()).strip("_")
    return slug[:60] or "home"


def run_recon(db, *, build_request: fm.AdapterBuildRequest, observer,
              start_paths=("/", "/login", "/application", "/fees",
                           "/appointments", "/submit"),
              hostnames: list[str] | None = None,
              follow_links: bool = False,
              entry_gate: dict | None = None) -> fm.AdapterReconJob:
    """Observe the portal's public pages through `observer(url) -> observation`
    and persist sanitized artifacts. `observer` is the structural interface —
    SyntheticPortal.observe in tests, a Browserbase/Playwright structural probe
    in live runs. It never receives credentials and never authenticates.

    With follow_links=True (live builds), one bounded second wave probes
    same-host links whose sanitized pattern looks visa-relevant — real portals
    rarely use the standard probe paths. Never leaves the verified hosts.

    When the portal family declares an `entry_gate` (curated reversible
    click/scroll/acknowledge sequence gating the real application form), recon
    ALSO replays it through `observer.observe_with_entry_gate` and records the
    destination page as a distinct artifact whose content_class marks it as
    the application form. Defaults to the build request's portal_evidence."""
    hosts = [h.lower() for h in (hostnames or (build_request.portal_evidence or {}).get("hostnames", []))]
    if entry_gate is None:
        entry_gate = (build_request.portal_evidence or {}).get("entry_gate") or None
    if not hosts:
        raise ReconRefused("no verified portal hostnames — recon may not guess where to look")
    job = fm.AdapterReconJob(build_request_id=build_request.id, org_id=build_request.org_id,
                             portal_hostnames=hosts, status="running")
    db.add(job)
    db.flush()
    observed = 0
    followed: list[str] = []

    seen_patterns: set[str] = set()

    def _observe_one(host: str, path: str, page_key: str) -> dict | None:
        nonlocal observed
        url = f"https://{host}{path}"
        raw = observer(url)
        if not raw or not raw.get("ok"):
            return None
        if str(raw.get("hostname", host)).lower() not in hosts:
            return None         # never follow the portal off the verified hosts
        art = sanitize_structure(raw)
        pattern = art.get("url_pattern", "")
        # A portal that answers an unknown path with its error page (or with
        # a page already recorded) has not revealed a new flow page — storing
        # it would let an error shell claim a flow role.
        if _ERROR_PATH_RE.search(pattern) and not _ERROR_PATH_RE.search(path):
            return None
        if pattern and pattern in seen_patterns:
            return None
        seen_patterns.add(pattern)
        db.add(fm.AdapterReconArtifact(
            recon_job_id=job.id, page_key=page_key,
            hostname=art["hostname"], url_pattern=pattern,
            structure=art))
        observed += 1
        return art

    try:
        probed: set[tuple[str, str]] = set()
        used_keys: set[str] = set()

        def _unique_key(base: str, path: str) -> str:
            # Lossy slugs can collide across distinct paths — a collision must
            # never silently overwrite an observed page's role/mappings.
            if base not in used_keys:
                used_keys.add(base)
                return base
            import hashlib
            suffixed = f"{base[:52]}_{hashlib.sha256(path.encode()).hexdigest()[:6]}"
            used_keys.add(suffixed)
            return suffixed

        link_candidates: list[tuple[str, str]] = []
        for host in hosts:
            for path in start_paths:
                probed.add((host, path))
                art = _observe_one(host, path,
                                   _unique_key(path.strip("/") or "home", path))
                if art and follow_links:
                    for pattern in art.get("links", []):
                        m = re.match(r"https?://([^/]+)(/.*)?$", pattern)
                        if not m:
                            continue
                        lhost, lpath = m.group(1).lower(), (m.group(2) or "/")
                        if lhost in hosts and _LINK_FOLLOW_RE.search(lpath):
                            link_candidates.append((lhost, lpath))
        if follow_links:
            attempts = 0
            for lhost, lpath in link_candidates:
                if attempts >= _MAX_FOLLOWED_LINKS:
                    break       # budget counts ATTEMPTS, not successes — the
                                # second wave is strictly bounded per build
                if (lhost, lpath) in probed:
                    continue
                probed.add((lhost, lpath))
                attempts += 1
                if _observe_one(lhost, lpath,
                                _unique_key(_page_key_for(lpath), lpath)) is not None:
                    followed.append(lpath)
        if entry_gate:
            if _observe_entry_gated_form(db, job, build_request=build_request,
                                         observer=observer, hosts=hosts,
                                         entry_gate=entry_gate,
                                         unique_key=_unique_key):
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


def _observe_entry_gated_form(db, job, *, build_request, observer, hosts,
                              entry_gate: dict, unique_key) -> bool:
    """Replay the DECLARED entry gate and record the destination page as the
    application-form artifact (returns True when recorded). Honest on every
    failure path: a replay that does not land on the expected path records
    the reason and NO form artifact — downstream gates then fail closed with
    that exact gap."""
    replay = getattr(observer, "observe_with_entry_gate", None)
    if replay is None:
        job.error = ("entry gate declared but the observer has no "
                     "entry-gate replay capability")[:400]
        return False
    for a in entry_gate.get("actions") or []:
        if (a or {}).get("action") not in _ENTRY_GATE_ACTIONS:
            raise ReconRefused(f"entry gate action {(a or {}).get('action')!r} "
                               f"outside the declared vocabulary")
    base = (build_request.portal_evidence or {}).get("portal_url") or \
        (f"https://{hosts[0]}/" if hosts else "")
    raw = replay(base, entry_gate)
    if not raw or not raw.get("ok"):
        job.error = f"entry gate replay failed: {str((raw or {}).get('error', ''))[:200]}"[:400]
        return False
    if str(raw.get("hostname", "")).lower() not in hosts:
        job.error = "entry gate replay ended off the verified hosts"[:400]
        return False
    art = sanitize_structure(raw)
    expect = str(entry_gate.get("expect_path") or "").rstrip("/")
    pattern = art.get("url_pattern", "")
    if expect and not pattern.rstrip("/").endswith(expect):
        job.error = (f"entry gate replay ended at {pattern[:120]!r}, "
                     f"expected path {expect!r}")[:400]
        return False
    db.add(fm.AdapterReconArtifact(
        recon_job_id=job.id,
        page_key=unique_key(ENTRY_GATED_FORM_PAGE_KEY, pattern or ENTRY_GATED_FORM_PAGE_KEY),
        hostname=art["hostname"], url_pattern=pattern, structure=art,
        content_class=ENTRY_GATED_FORM_CLASS))
    db.flush()
    return True


def artifacts(db, recon_job_id: str) -> list[fm.AdapterReconArtifact]:
    return db.execute(select(fm.AdapterReconArtifact).where(
        fm.AdapterReconArtifact.recon_job_id == recon_job_id)).scalars().all()
