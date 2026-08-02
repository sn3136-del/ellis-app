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
                  "radio", "file", "button", "submit", "tel", "number",
                  "search-combobox", "link"}

# Entry-gate replay vocabulary (declared, curated; see live_browser).
# The reversible actions a declared entry gate may perform during recon.
# TERMS_CHOICE is the portal's own agree-control: during RECON it is replayed
# only to observe the form behind the gate (and to capture the verbatim terms
# as evidence) in a throwaway observation session — at RUNTIME it is never
# enacted until the applicant has signed those exact terms in Ellis.
_ENTRY_GATE_ACTIONS = {"CLICK", "SCROLL_TO_BOTTOM", "CHECK", "TERMS_CHOICE",
                       "SELECT_OPTION", "WAIT_FOR_SELECTOR"}
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
        # Unknown control types become BUTTONS, never text inputs: coercing to
        # "text" invented fillable fields out of every anchor and submit
        # control the set didn't know (ASP.NET LinkButtons on evisa.gov.az),
        # drowning the real form — the root of "no form page was mappable".
        etype = el.get("type") if el.get("type") in _ALLOWED_TYPES else "button"
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
        # Appointment-slot handles: portal STRUCTURE (a slot id or its
        # datetime), never applicant data. Only the fixed allowlist survives,
        # each value length-bounded and stripped of anything but the safe
        # id/timestamp character set.
        attrs = el.get("attrs") or {}
        if isinstance(attrs, dict):
            keep = {}
            for k in ("data-slot", "data-slot-id", "data-datetime",
                      "data-date", "data-time", "data-appointment"):
                v = attrs.get(k)
                if v:
                    keep[k] = re.sub(r"[^A-Za-z0-9:_+\-./ ]", "", str(v))[:60]
            if keep:
                clean["attrs"] = keep
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
    """URLs are reduced to a pattern: query VALUES never survive.

    Query KEYS do. A portal that selects its form with a bare flag — Malaysia's
    MDAC serves the arrival form at /mdac/main?registerMain and an empty shell
    at /mdac/main — is otherwise unreachable from its own recorded pattern, so
    the live layer re-navigated the shell and every mapped selector "vanished".
    Keys are portal structure (a route name), values are data: `?email=a@b`
    becomes `?email`, never the address itself.
    """
    url = str(url or "")
    base, _, query = url.partition("?")
    base = base[:300]
    if not query:
        return base
    keys = []
    for part in query.split("&"):
        key = re.sub(r"[^A-Za-z0-9_\-]", "", part.split("=")[0])[:40]
        if key and key not in keys:
            keys.append(key)
    return f"{base}?{'&'.join(keys)}"[:300] if keys else base


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

# A control whose own visible text says it STARTS an application. Modern
# portals route these client-side from a <button> with no href, so link
# following never reaches the form (Myanmar "Apply Visa", Cambodia "Apply
# Now", K-ETA "Apply from the beginning"). Intent words only — never
# "check status", "track", "sign in".
_APPLY_CTA_RE = re.compile(
    r"\b(apply|application|start|begin|new applicant|get (your )?(visa|eta)"
    r"|solicitar|solicitud|nueva solicitud|demander|nouvelle demande"
    r"|beantragen|antrag stellen|richiedi|solicitar visto|başvur"
    r"|permohonan baru|ajukan|подать|оформить|申請|申请|신청|ยื่นคำขอ"
    r"|nộp hồ sơ|xin thị thực)\b", re.IGNORECASE)
_APPLY_CTA_EXCLUDE_RE = re.compile(
    r"(status|track|check|enquir|verify|sign\s?in|log\s?in|retrieve|resume"
    r"|download|help|faq|contact|consulta|estado|suivi|prüfen)", re.IGNORECASE)
_MAX_APPLY_CTA_ATTEMPTS = 2
_MAX_FOLLOWED_LINKS = 8


_FORM_INPUT_TYPES = ("text", "email", "date", "tel", "number", "select",
                     "checkbox", "radio", "search-combobox")


def _form_input_count(observation: dict) -> int:
    """How many real applicant-fillable controls a page rendered."""
    return sum(1 for e in (observation or {}).get("elements", [])
               if (e.get("type") or "") in _FORM_INPUT_TYPES)


def _shape_key(pattern: str, art: dict) -> str:
    """Identity of an observed page: its sanitized pattern PLUS the controls it
    renders. Structure only — names and types the sanitizer already cleared,
    never values."""
    import hashlib
    parts = [f"{e.get('name', '')}:{e.get('type', '')}"
             for e in (art or {}).get("elements", [])]
    digest = hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()[:12]
    return f"{pattern}#{digest}"


def _page_key_for(path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (path.strip("/") or "home").lower()).strip("_")
    return slug[:60] or "home"


def run_recon(db, *, build_request: fm.AdapterBuildRequest, observer,
              start_paths=("/", "/login", "/application", "/fees",
                           "/appointments", "/submit"),
              hostnames: list[str] | None = None,
              follow_links: bool = False,
              entry_gate: dict | None = None,
              curated_paths: tuple = ()) -> fm.AdapterReconJob:
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
        # A CURATED form path is known to render a form — an operator verified
        # it live. When it comes back empty, the portal was slow, not wrong
        # (Malaysia's MDAC renders 22 fields on a good load and a bare shell on
        # a bad one). One retry, only for curated paths, only when the page
        # rendered no form: without it a single flaky load silently costs the
        # whole portal and reports "no form page was mappable".
        if path in curated_paths and (
                not raw or not raw.get("ok") or _form_input_count(raw) < 3):
            retry = observer(url)
            if retry and retry.get("ok") and _form_input_count(retry) >= 3:
                raw = retry
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
        # Dedupe on the page's SHAPE, not its URL alone. Sanitized patterns
        # drop the query (values must never survive), so /mdac/main and
        # /mdac/main?registerMain collapse to one pattern — and Malaysia's
        # real 22-field arrival form was discarded as a duplicate of the empty
        # shell it shares a path with. Same story on SPA portals that serve
        # every step from one URL. Two pages are the same page only when the
        # controls on them are the same.
        if pattern and _shape_key(pattern, art) in seen_patterns:
            return None
        seen_patterns.add(_shape_key(pattern, art))
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
        elif follow_links and observed:
            # URL probing and link-following found pages but maybe no FORM:
            # modern portals start the application from a BUTTON that routes
            # client-side, which no <a href> sweep can follow. If nothing
            # observed so far renders like a form, click an observed
            # apply-intent control and record only what genuinely renders.
            def _renders_form(a) -> bool:
                els = (a or {}).get("elements", [])
                return sum(1 for e in els if (e.get("type") or "") in
                           ("text", "email", "date", "tel", "number",
                            "select", "combobox", "search-combobox")) >= 3 \
                    and not any((e.get("type") or "") == "password" for e in els)
            db.flush()   # make this job's pending artifacts queryable
            all_arts = [a.structure for a in
                        db.query(fm.AdapterReconArtifact)
                        .filter_by(recon_job_id=job.id).all()]
            if not any(_renders_form(a) for a in all_arts):
                if _discover_application_form(
                        db, job, build_request=build_request, observer=observer,
                        hosts=hosts, artifacts=all_arts, unique_key=_unique_key):
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


def _apply_cta_candidates(artifacts: list[dict]) -> list[dict]:
    """Observed clickable controls whose OWN visible text says they start an
    application, with a deterministic selector. Ordered by how strongly the
    page looks like an entry point, best first."""
    from .schema import deterministic_selector
    out: list[dict] = []
    for art in artifacts:
        for el in (art or {}).get("elements", []):
            if el.get("sensitive"):
                continue
            if not (el.get("submits") or (el.get("type") or "") in ("button", "submit")):
                continue
            text = f"{el.get('name', '')} {el.get('label', '')}".strip()
            if not text or _APPLY_CTA_EXCLUDE_RE.search(text):
                continue
            if not _APPLY_CTA_RE.search(text):
                continue
            sel = (el.get("selector") or "").strip()
            if not sel or not deterministic_selector(sel):
                continue
            out.append({"selector": sel, "text": text[:60],
                        "hostname": art.get("hostname", ""),
                        "url_pattern": art.get("url_pattern", "")})
    return out


def _discover_application_form(db, job, *, build_request, observer, hosts,
                               artifacts: list[dict], unique_key) -> bool:
    """No form was reachable by URL probing or link following: click an
    OBSERVED control whose own text says it starts an application, then
    observe whatever the portal actually shows.

    This is discovery, not fabrication — the click target is a real observed
    element, the replay runs through the same restricted (CLICK-only,
    sensitive-refusing) entry-gate machinery, and the destination is recorded
    as an application form ONLY if it genuinely renders form inputs. A page
    that turns out to be marketing or a login wall is recorded honestly and
    claims nothing."""
    replay = getattr(observer, "observe_with_entry_gate", None)
    if replay is None:
        return False
    tried = 0
    for cta in _apply_cta_candidates(artifacts):
        if tried >= _MAX_APPLY_CTA_ATTEMPTS:
            break
        tried += 1
        base = cta.get("url_pattern") or \
            (build_request.portal_evidence or {}).get("portal_url") or \
            (f"https://{hosts[0]}/" if hosts else "")
        gate = {"actions": [{"action": "CLICK", "selector": cta["selector"],
                             "purpose": f"discovered application entry: {cta['text']}"}]}
        try:
            raw = replay(base, gate)
        except Exception:  # noqa: BLE001 — a refused/failed click is just a miss
            continue
        if not raw or not raw.get("ok"):
            continue
        if str(raw.get("hostname", "")).lower() not in hosts:
            continue        # never leave the verified hosts
        art = sanitize_structure(raw)
        inputs = [e for e in art.get("elements", [])
                  if (e.get("type") or "") in
                  ("text", "email", "date", "tel", "number", "select",
                   "combobox", "search-combobox")]
        if len(inputs) < 3:
            continue        # not a form — claim nothing
        key = unique_key("discovered_application_form",
                         art.get("url_pattern", "") or cta["selector"])
        db.add(fm.AdapterReconArtifact(
            recon_job_id=job.id, page_key=key, hostname=art.get("hostname", ""),
            url_pattern=art.get("url_pattern", ""),
            content_class="application_form", structure=art))
        # The click that reached the form becomes the build's entry gate, so
        # specgen, the generator manifest and the live test layer all replay
        # the identical, already-proven sequence — a discovered gate is held
        # to exactly the same evidence as a curated one.
        gate["expect_path"] = art.get("url_pattern", "") or ""
        gate["discovered"] = True
        build_request.portal_evidence = dict(build_request.portal_evidence or {},
                                             entry_gate=gate)
        db.commit()
        return True
    return False


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
    expect = str(entry_gate.get("expect_path") or "")
    pattern = art.get("url_pattern", "")
    # Same segment-boundary tolerance the live replay used to accept the page
    # (SPA step suffixes like …/individual-form/draft). The single source of
    # truth lives in live_browser so the two checks can never disagree again.
    from ..portal.live_browser import path_reaches_expected
    if expect and not path_reaches_expected(pattern, expect):
        job.error = (f"entry gate replay ended at {pattern[:120]!r}, "
                     f"expected path {expect!r}")[:400]
        return False
    # Verbatim portal terms captured at a TERMS_CHOICE gate ride into the
    # artifact structure as evidence: the flow's portal_terms_consent handoff
    # shows the applicant exactly this text, and the runtime binds their
    # signature to its hash. Structural only — no applicant data.
    captured = raw.get("terms_captured") or []
    if captured:
        art = dict(art, portal_terms=[{
            "title": str(t.get("title") or "")[:300],
            "text": str(t.get("text") or "")[:20000],
            "text_selector": str(t.get("selector") or "")[:200],
            "source_url": str(t.get("source_url") or "")[:500]}
            for t in captured])
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
