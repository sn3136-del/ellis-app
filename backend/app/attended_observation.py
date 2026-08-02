"""The attended session: an applicant drives, Ellis watches the page's shape.

This is the delivery mechanism for `authorized_observation` — that module
defines WHAT may be recorded (structure only, consented, provenance-marked);
this one runs the session in which recording happens. One applicant, already
making this application in their own secure window, agrees that their run may
also teach Ellis the portal. Ellis attaches to that same window read-only and
snapshots each new page's structure as the applicant works.

It serves the two honest gaps a credential-free build cannot cross:

  * portals whose form only exists behind the applicant's sign-in, and
  * portals whose entry sits behind a CAPTCHA, which the applicant solves
    themselves — Ellis merely gets to see the pages on the other side.

The doctrine is inherited, not re-argued: Ellis never fills, clicks, solves or
submits anything here (the one navigation is opening the portal's own start
page); nothing is recorded without the versioned consent; every observation
passes recon's sanitizer; every artifact keeps its authorized_session
provenance. When the session ends with a real form observed, the build is
walked back through the ordinary verification chain — spec, candidate, static,
contract, live layers, and the same sixteen release gates as every other
portal. Observation earns a rebuild, never a shortcut past the gates.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import authorized_observation

POLL_SECONDS = 2.0
# One applicant session is a bounded gift, not a firehose: enough pages for
# any real application flow, small enough that a runaway SPA can't flood the
# artifact table.
MAX_PAGES = 40
MAX_MINUTES = 45


class WindowUnavailable(Exception):
    """The applicant's secure window is not open (or already ended)."""


class SessionAlready(Exception):
    """An attended session is already recording for this build."""


class _Watch:
    def __init__(self, *, req_id: str, job_id: str):
        self.req_id = req_id
        self.job_id = job_id
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.pages = 0
        self.forms = 0
        self.error = ""
        self.started_at = datetime.now(timezone.utc).isoformat()


_watches: dict[str, _Watch] = {}
_rebuilds: dict[str, dict] = {}
_lock = threading.Lock()


# ------------------------------------------------------------- observations --
def looks_like_form(obs: dict) -> bool:
    """An application form, not a login gate: several real inputs and no
    password field. The login page is still worth recording (account flows
    need its shape) — it just must never be MISTAKEN for the form."""
    els = (obs or {}).get("elements", [])
    typed = sum(1 for e in els if (e.get("type") or "") in
                ("text", "email", "date", "tel", "number",
                 "select", "checkbox", "radio", "search-combobox"))
    has_password = any((e.get("type") or "") == "password" for e in els)
    return typed >= 3 and not has_password


def _signature(obs: dict) -> str:
    """Identity of a page's SHAPE, not its URL. SPA portals (India, Thailand)
    keep one URL across every screen — deduping on URL alone would collapse
    the whole application into a single artifact and lose every later step."""
    import hashlib
    from .adapter_factory import recon
    s = recon.sanitize_structure(obs or {})
    parts = [s.get("url_pattern", "")]
    for e in s.get("elements", []):
        parts.append(f"{e.get('name', '')}:{e.get('type', '')}")
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()[:16]


def existing_signatures(db, job_id: str) -> set[str]:
    """What this job has already seen (public recon or a prior attended
    session) — an attended run adds pages, it never duplicates them."""
    import hashlib
    from sqlalchemy import select
    from .adapter_factory import models as fm
    out = set()
    for art in db.execute(select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job_id)).scalars().all():
        s = art.structure or {}
        parts = [s.get("url_pattern", "")]
        for e in s.get("elements", []):
            parts.append(f"{e.get('name', '')}:{e.get('type', '')}")
        out.add(hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()[:16])
    return out


def ensure_recon_job(db, req, hosts: list[str]):
    """The job the artifacts hang off. A parked build usually has one from
    public recon; a build that never got that far gets a fresh one, honestly
    labelled as attended rather than pretending a public sweep happened."""
    from .adapter_factory import models as fm
    jid = (req.portal_evidence or {}).get("recon_job_id") or ""
    job = db.get(fm.AdapterReconJob, jid) if jid else None
    if job is None:
        job = fm.AdapterReconJob(
            build_request_id=req.id, org_id=req.org_id,
            portal_hostnames=hosts, status="complete", pages_observed=0)
        db.add(job)
        db.flush()
        req.portal_evidence = dict(req.portal_evidence or {}, recon_job_id=job.id)
        db.commit()
    return job


def _scrub_signed_in(obs: dict) -> dict:
    """A SIGNED-IN page can echo the applicant's data into any attribute — a
    review screen putting their passport number in a placeholder is one portal
    quirk away. The public sanitizer keeps placeholders because public pages
    author them; here they are dropped wholesale, with the URL's query string,
    before the ordinary sanitizer runs. Structure survives; echoes cannot."""
    out = dict(obs or {})
    out["url"] = (out.get("url") or "").split("?")[0]
    out["elements"] = [{k: v for k, v in dict(e).items()
                        if k not in ("placeholder", "value", "title")}
                       for e in out.get("elements", [])]
    return out


def record_tick(db, req, *, job_id: str, obs: dict, seen: set[str],
                watch: _Watch) -> bool:
    """One poll of the applicant's page. Records at most one artifact; returns
    whether it did. Separated from the thread loop so tests drive it directly."""
    if not obs or not obs.get("ok"):
        return False
    obs = _scrub_signed_in(obs)
    sig = _signature(obs)
    if sig in seen:
        return False
    seen.add(sig)
    is_form = looks_like_form(obs)
    path = urlparse(obs.get("url") or "").path.strip("/").replace("/", "_")
    page_key = f"attended_{watch.pages + 1}_{(path or 'page')[:40]}"
    # authorized_observation enforces consent and sanitization — going through
    # it is the point, not a convenience.
    authorized_observation.observe(
        db, req, recon_job_id=job_id, observation=obs,
        page_key=page_key, is_form=is_form)
    watch.pages += 1
    if is_form:
        watch.forms += 1
    return True


# ------------------------------------------------------------ session start --
def _window_connect_info(db, application_id: str):
    """The applicant's OWN secure window — never a second browser. Mirrors the
    calendar's attach rule: the session Ellis observes must be the session the
    applicant is actually working in."""
    from .portal_store import current_browser_session
    from .providers import browser as bb
    row = current_browser_session(db, application_id)
    if row is None or row.mode != "browserbase":
        raise WindowUnavailable("open the secure window first")
    if not bb.session_alive(row.provider_session_id):
        row.status = "closed"
        db.commit()
        raise WindowUnavailable("the secure window closed; open it again")
    return bb.session_connect_info(row.provider_session_id)


def start(db, req, *, application_id: str, hosts: list[str],
          start_url: str = "") -> dict:
    """Begin recording the applicant's session. Consent is checked here AND in
    every observe() call — a race can end a session, never smuggle a record."""
    if not authorized_observation.has_consent(req):
        raise authorized_observation.ObservationRefused(
            "the applicant has not consented to Ellis learning this portal "
            "from their session")
    with _lock:
        live = _watches.get(req.id)
        if live is not None and live.thread is not None and live.thread.is_alive():
            raise SessionAlready("an attended session is already recording")
    connect_info = _window_connect_info(db, application_id)
    job = ensure_recon_job(db, req, hosts)
    seen = existing_signatures(db, job.id)
    watch = _Watch(req_id=req.id, job_id=job.id)
    watch.thread = threading.Thread(
        target=_run_watch,
        args=(req.id, job.id, list(hosts), connect_info, start_url, seen, watch),
        daemon=True, name=f"attended-observation-{req.id[:8]}")
    with _lock:
        _watches[req.id] = watch
    watch.thread.start()
    return status(req.id)


def _run_watch(req_id: str, job_id: str, hosts: list[str], connect_info: dict,
               start_url: str, seen: set[str], watch: _Watch) -> None:
    """The watcher thread. Owns its DB session and its own CDP attach (sync
    Playwright objects must live and die on one thread)."""
    from .db import SessionLocal
    from .portal.live_browser import (LiveBrowserSession, _EXTRACT_JS,
                                      normalize_observation)
    db = SessionLocal()
    sess = LiveBrowserSession(allowed_hostnames=hosts, session=connect_info)
    deadline = time.monotonic() + MAX_MINUTES * 60
    try:
        from .adapter_factory import models as fm
        page = sess._ensure_page()
        # The ONE navigation Ellis performs: opening the portal's own start
        # page in the applicant's window, so they begin on the official site
        # rather than a blank tab. Everything after this is theirs.
        if start_url and not sess._host_ok(page.url):
            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001 — a slow load is not a failure
                pass
        while not watch.stop.is_set():
            if watch.pages >= MAX_PAGES or time.monotonic() > deadline:
                break
            try:
                url = page.url
                if sess._host_ok(url):
                    raw = page.evaluate(_EXTRACT_JS)
                    obs = normalize_observation(
                        url, 200, urlparse(url).netloc, raw)
                    req = db.get(fm.AdapterBuildRequest, req_id)
                    if req is None:
                        break
                    record_tick(db, req, job_id=job_id, obs=obs, seen=seen,
                                watch=watch)
            except authorized_observation.ObservationRefused:
                break               # consent withdrawn mid-session: stop, keep nothing new
            except Exception as e:  # noqa: BLE001 — a flaky poll must not kill the session
                watch.error = str(e)[:200]
            watch.stop.wait(POLL_SECONDS)
    except Exception as e:  # noqa: BLE001 — attach failures reported, not raised into nowhere
        watch.error = str(e)[:200]
    finally:
        try:
            sess.close()   # detaches CDP only — the applicant's session is not ours to close
        finally:
            db.close()


def status(req_id: str) -> dict:
    with _lock:
        w = _watches.get(req_id)
        rb = _rebuilds.get(req_id, {})
    if w is None:
        return {"active": False, "pages": 0, "forms": 0, "error": "",
                "rebuild": rb}
    return {"active": bool(w.thread and w.thread.is_alive()),
            "pages": w.pages, "forms": w.forms, "error": w.error,
            "started_at": w.started_at, "rebuild": rb}


# ----------------------------------------------------------- session finish --
def finish(db, req, *, family_id: str, actor: str) -> dict:
    """The applicant says they are done. Stop recording, and — only if a real
    application form was observed — send the build back through the full
    verification chain in the background. The gates alone decide release."""
    with _lock:
        w = _watches.pop(req.id, None)
    if w is not None:
        w.stop.set()
        if w.thread is not None:
            w.thread.join(timeout=10)
    from sqlalchemy import select
    from .adapter_factory import models as fm, recon
    job_id = (req.portal_evidence or {}).get("recon_job_id") or ""
    arts = db.execute(select(fm.AdapterReconArtifact).where(
        fm.AdapterReconArtifact.recon_job_id == job_id)).scalars().all()
    forms = [a for a in arts if a.content_class == recon.ENTRY_GATED_FORM_CLASS]
    summary = {"pages": w.pages if w else 0, "forms": w.forms if w else 0,
               "error": w.error if w else "", "total_form_pages": len(forms)}
    if not forms:
        # No form seen means nothing to build from — said plainly, with the
        # session's own numbers, never a hopeful rebuild that must fail.
        return dict(summary, rebuilt=False,
                    reason="no application form page was observed in this "
                           "session — the portal's form never came into view")
    with _lock:
        _rebuilds[req.id] = {"active": True, "released": False, "missing": [],
                             "error": ""}
    t = threading.Thread(target=_run_rebuild,
                         args=(req.id, job_id, family_id, actor),
                         daemon=True, name=f"attended-rebuild-{req.id[:8]}")
    t.start()
    return dict(summary, rebuilt=True)


def _run_rebuild(req_id: str, job_id: str, family_id: str, actor: str) -> None:
    """Regenerate the spec from the now-complete evidence, then run the same
    orchestrated build every portal goes through. Backgrounded because the
    live layers take minutes; the status endpoint reports honestly meanwhile."""
    from .db import SessionLocal
    from . import audit
    from .adapter_factory import generator, models as fm, recon, specgen
    from .adapter_factory.build_workflow import default_observer
    from .adapter_factory.statemachine import transition
    db = SessionLocal()
    try:
        req = db.get(fm.AdapterBuildRequest, req_id)
        if req.state == "QUARANTINED":
            # Leaving quarantine is an explicit operator decision, never a
            # side effect of an applicant's session. The structure stays
            # recorded; the rebuild waits for the operator.
            with _lock:
                _rebuilds[req_id] = {
                    "active": False, "released": False, "missing": [],
                    "error": "this portal's build is quarantined — an operator "
                             "must clear it before the observed structure can "
                             "be used"}
            return
        job = db.get(fm.AdapterReconJob, job_id)
        arts = recon.artifacts(db, job_id)
        spec = specgen.generate_specification(
            db, build_request=req, recon_job=job, artifacts=arts,
            generator_name="kimi-k3+deterministic-skeleton")
        req.portal_evidence = dict(req.portal_evidence or {}, spec_id=spec.id)
        db.commit()
        # Walk the parked build to CODE_GENERATED along declared edges only —
        # the state machine stays the single authority on what moves.
        note = authorized_observation.provenance_note(req)
        if req.state in ("AWAITING_INTERNAL_RELEASE", "TESTS_FAILED"):
            transition(req, "MANUAL_REVIEW_REQUIRED", f"attended session: {note}")
        generator.generate_candidate_version(db, build_request=req, spec=spec)
        if req.state != "CODE_GENERATED":
            transition(req, "CODE_GENERATED", f"attended session: {note}")
        db.commit()
        audit.record(db, org_id=req.org_id,
                     application_id=req.application_id or "",
                     action="attended_observation_rebuild",
                     detail={"family_id": family_id, "pages": len(arts)},
                     actor=actor)
        hosts = (req.portal_evidence or {}).get("hostnames", [])
        observer = default_observer(hosts)
        try:
            from .global_routes.orchestrator import build_family_adapter
            result = build_family_adapter(db, family_id, observer=observer)
        finally:
            close = getattr(observer, "close", None)
            if close:
                try:
                    close()
                except Exception:  # noqa: BLE001 — cleanup is best-effort
                    pass
        with _lock:
            _rebuilds[req_id] = {"active": False,
                                 "released": bool(result.get("released")),
                                 "missing": result.get("missing", []),
                                 "error": ""}
    except Exception as e:  # noqa: BLE001 — the status endpoint must see the truth
        with _lock:
            _rebuilds[req_id] = {"active": False, "released": False,
                                 "missing": [], "error": str(e)[:300]}
    finally:
        db.close()
