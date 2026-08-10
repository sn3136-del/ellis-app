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

An attended session is also the most expensive evidence Ellis will ever get —
a real person walked a real form — so it is not spent on one build. Two things
outlive the session, both family-scoped and both advisory:

  * WITNESSED MAPPINGS. The groundings specgen ACCEPTED from those pages are
    remembered for the family, so the next portal in it starts from what a
    human already proved instead of from nothing. Only accepted mappings are
    ever remembered — the grounding chokepoint stays the one place a mapping
    is judged, and a recalled mapping re-enters a build as a proposal through
    that same chokepoint, never around it.
  * READ-ONLY ENDPOINT NOTES. The JSON calls the portal's own frontend made
    while the applicant worked, written down as evidence a human can use to
    read a status later. Only GET/HEAD is ever recorded, so a submit endpoint
    is not written down at all, and Ellis builds no caller against the list:
    an application is submitted through the portal's own pages, the same ones
    a person would use, or it is not submitted.
"""
from __future__ import annotations

import re
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

# Every artifact this module records is page-keyed with this prefix. It is how
# a later reader tells the pages a HUMAN walked from the public recon pages
# sharing the same job — the difference between witnessed evidence and
# evidence nobody saw.
ATTENDED_PAGE_PREFIX = "attended_"

# --- READ-ONLY status-endpoint discovery -----------------------------------
# When the observation payload exposes the JSON/XHR calls the portal's OWN
# frontend already made, Ellis keeps a scrubbed, deduped list of them as an
# ADVISORY next step for a human: the addresses a future case-STATUS reader
# might one day poll to learn an outcome. HARD RULE, load-bearing and enforced
# in code + test below: these are READ-ONLY status candidates. Nothing in Ellis
# ever posts to them or places them on a submit path — the submit path is the
# released deterministic flow, which never reads this store. Ellis issues none
# of these calls itself; it only notes ones the applicant's own page made. When
# the payload carries no network info the discovery is a no-op — a portal is
# never handed a fabricated endpoint.
STATUS_ENDPOINT_EVIDENCE_KEY = "status_endpoint_candidates"
STATUS_ENDPOINT_USAGE = "READ_ONLY_STATUS_CANDIDATE"
MAX_STATUS_ENDPOINTS = 50
# A path segment that carries an applicant's own identity (a long numeric id, a
# uuid, an opaque token) is masked to ':id' so the stored value is the
# endpoint's structural TEMPLATE, never one traveller's handle — the same
# value-free doctrine the URL sanitizer keeps for query strings.
_ID_SEGMENT_RE = re.compile(
    r"^(\d{4,}|[0-9a-f]{8}-[0-9a-f]{4}|[A-Za-z0-9_+/=-]{16,})$", re.I)


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


def _scrub_endpoint_url(url: str) -> str:
    """host + path only, with the query and fragment dropped and id-shaped path
    segments masked — the endpoint's structural template, never an applicant's
    own identifier. Returns '' for anything that is not an http(s) URL."""
    try:
        p = urlparse(url or "")
    except Exception:  # noqa: BLE001 — a malformed URL is simply not an endpoint
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    segs = [(":id" if _ID_SEGMENT_RE.match(s) else s)
            for s in (p.path or "").split("/")]
    return f"{p.scheme}://{p.netloc.lower()}{'/'.join(segs)}"[:300]


def _same_site(host: str, page_host: str) -> bool:
    """Is this the PORTAL's own endpoint, or somebody else's? An analytics
    beacon and a chat widget are also XHR calls the page made, and neither is
    ever a status endpoint — writing them down would be noise at best and a
    third party's address in a government build's evidence at worst. Kept
    deliberately tight: the same host, or one a label-boundary below the
    other (api.evisa.gov.example under evisa.gov.example)."""
    host = (host or "").lower().removeprefix("www.")
    page_host = (page_host or "").lower().removeprefix("www.")
    if not host or not page_host:
        return False
    return (host == page_host or host.endswith("." + page_host)
            or page_host.endswith("." + host))


def _status_endpoint_candidates(obs: dict, *, job_id: str) -> list[dict]:
    """READ-ONLY status-endpoint candidates from an observation's network info.

    Reads only the JSON/XHR calls the portal's frontend already made — Ellis
    issues none of them — scrubs each to a value-free host+path template, and
    labels every record READ-ONLY. If the observation carries no network info
    this returns [] rather than inventing an endpoint (don't fabricate)."""
    net = (obs or {}).get("network") or (obs or {}).get("networkRequests") or []
    page_host = str((obs or {}).get("hostname")
                    or urlparse(str((obs or {}).get("url") or "")).netloc).lower()
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in net if isinstance(net, list) else []:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("resource_type") or r.get("type") or "").lower()
        ctype = str(r.get("content_type") or r.get("mime") or "").lower()
        # A data call the frontend made: an XHR/fetch, or anything answering
        # JSON. Documents, images and scripts are page chrome, not status.
        if kind not in ("xhr", "fetch") and "json" not in ctype:
            continue
        method = (re.sub(r"[^A-Z]", "",
                         str(r.get("method") or "GET").upper())[:8] or "GET")
        # ENFORCED AT CAPTURE, not just by labelling: only a read verb is ever
        # written down. A POST/PUT/PATCH/DELETE is how a portal MUTATES — the
        # submit path — so it is never stored, never recalled, never a caller's
        # target. The read-only guarantee is therefore true of the data itself.
        if method not in ("GET", "HEAD"):
            continue
        raw_url = str(r.get("url") or "")
        pattern = _scrub_endpoint_url(raw_url)
        if not pattern or not _same_site(urlparse(raw_url).hostname or "",
                                         page_host):
            continue
        key = (method, pattern)
        if key in seen:
            continue
        seen.add(key)
        out.append({"method": method, "url_pattern": pattern,
                    "kind": kind or "json", "usage": STATUS_ENDPOINT_USAGE,
                    "recon_job_id": job_id,
                    "discovered_by": "attended_observation"})
    return out


def _record_status_endpoints(db, req, obs: dict, job_id: str) -> int:
    """Merge newly discovered READ-ONLY status endpoints into the build's
    evidence bag — a side record keyed to the recon job. Advisory only: a human
    later decides whether a status reader may poll them. This function is the
    ONLY writer of the store, and no code path reads it onto a submit/POST."""
    found = _status_endpoint_candidates(obs, job_id=job_id)
    if not found:
        return 0
    evidence = dict(req.portal_evidence or {})
    existing = list(evidence.get(STATUS_ENDPOINT_EVIDENCE_KEY) or [])
    have = {(e.get("method"), e.get("url_pattern")) for e in existing}
    added = 0
    for e in found:
        if len(existing) >= MAX_STATUS_ENDPOINTS:
            break
        k = (e.get("method"), e.get("url_pattern"))
        if k in have:
            continue
        existing.append(e)
        have.add(k)
        added += 1
    if not added:
        return 0
    evidence[STATUS_ENDPOINT_EVIDENCE_KEY] = existing
    req.portal_evidence = evidence
    db.commit()
    return added


def record_tick(db, req, *, job_id: str, obs: dict, seen: set[str],
                watch: _Watch) -> bool:
    """One poll of the applicant's page. Records at most one artifact; returns
    whether it did. Separated from the thread loop so tests drive it directly."""
    if not obs or not obs.get("ok"):
        return False
    obs = _scrub_signed_in(obs)
    # READ-ONLY harvest of any status-endpoint candidates the applicant's own
    # frontend revealed. Runs every tick (an SPA can call new endpoints on an
    # unchanged page shape), and is a no-op when there is no network info.
    # Consent is re-checked HERE and not inherited from the artifact path
    # below: this is a recording too, it happens on ticks that record no
    # artifact, and a withdrawn consent must stop every recording at once.
    if authorized_observation.has_consent(req):
        _record_status_endpoints(db, req, obs, job_id)
    sig = _signature(obs)
    if sig in seen:
        return False
    seen.add(sig)
    is_form = looks_like_form(obs)
    path = urlparse(obs.get("url") or "").path.strip("/").replace("/", "_")
    page_key = f"{ATTENDED_PAGE_PREFIX}{watch.pages + 1}_{(path or 'page')[:40]}"
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


def _witnessed_element(art, mapping: dict) -> dict | None:
    """The observed element an accepted mapping cites, or None.

    The chokepoint already proved this selector is one recon actually SAW on
    this page, so an exact selector+name match is the whole lookup. When it
    somehow does not match, nothing is remembered — memory is keyed on the
    observed field, and a guessed element would poison every later recall."""
    for el in ((getattr(art, "structure", None) or {}).get("elements") or []):
        if el.get("selector") == mapping.get("selector") and \
                str(el.get("name") or "") == str(mapping.get("portal_field") or ""):
            return el
    return None


def remember_witnessed_mappings(db, req, *, family_id: str, spec, artifacts,
                                source: str, actor: str) -> int:
    """Teach the builder what this attended session just proved: feed the
    family-scoped mapping memory every mapping a human's own run grounded.

    This is the loop's other half, and it is deliberately narrow.

      * ONLY ACCEPTED MAPPINGS. `spec.field_mappings` is exactly the pile that
        PASSED the single grounding chokepoint in
        specgen.generate_specification; the rejected proposals live only in
        `spec.generation_basis['rejected_mappings']` and are never read here.
        No raw proposal and no rejected proposal is ever remembered, and a
        recalled one comes back as a proposal through that same chokepoint.
      * ONLY WITNESSED PAGES. A job can also hold public recon pages, and a
        page nobody walked is not human-witnessed evidence. Only artifacts
        this module recorded from the applicant's own session qualify, so the
        `human_correction` label stays literally true.
      * ONLY WITH CONSENT. Withdrawal means nothing further is recorded, and
        a memory row derived from that session is a recording.

    Family-scoped and family-agnostic: the key is the family id the build was
    called with, so a tourist e-visa family and a work-visa family fill the
    same memory identically. The import stays lazy only to keep this module
    free of an import-time dependency on the factory package. Learning is
    advisory and must never break, delay or alter a build — which is what the
    per-mapping guard below is for."""
    if not authorized_observation.has_consent(req):
        return 0
    from .adapter_factory import mapping_memory, recon
    remember = mapping_memory.remember
    witnessed = {
        a.id: a for a in (artifacts or [])
        if str(getattr(a, "page_key", "") or "").startswith(ATTENDED_PAGE_PREFIX)
        and getattr(a, "content_class", "") in (
            authorized_observation.CONTENT_CLASS, recon.ENTRY_GATED_FORM_CLASS)}
    n = 0
    for m in (getattr(spec, "field_mappings", None) or []):
        art = witnessed.get(m.get("artifact_id"))
        el = _witnessed_element(art, m) if art is not None else None
        if el is None:
            continue
        try:
            remember(db, family_id=family_id, mapping=m, observed_field=el,
                     source=source, actor=actor)
            n += 1
        except Exception:  # noqa: BLE001 — one bad record never fails a build
            pass
    return n


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
        # The applicant drove this real form, so every grounding specgen just
        # ACCEPTED on a page they walked is human-witnessed. Record it now —
        # before the downstream build can fail or park — so a later failure
        # never discards what this session already proved. Recorded even if
        # release does not follow: the evidence was witnessed either way.
        remember_witnessed_mappings(db, req, family_id=family_id, spec=spec,
                                    artifacts=arts, source="human_correction",
                                    actor=actor)
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
        # What a released adapter carries is recorded where release happens,
        # not here: the orchestrator builds its own specification, so this
        # session's pile is not the one that shipped and must not claim to be.
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
