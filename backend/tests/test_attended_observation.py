"""The attended session records STRUCTURE from a consented applicant run —
these tests pin the rules that make it safe to exist: no consent no record,
values never survive, shape-dedupe keeps SPA steps, and a session that saw no
form is said plainly to have seen no form (never a hopeful rebuild)."""
import pytest

from app import attended_observation as att
from app import authorized_observation as ao
from app.adapter_factory import models as fm
from app.adapter_factory.build_workflow import create_request, record_consent


HOST = "evisa.gov.example"


def _req(db, *, consented=True, route_key="att|test", org="orgATT"):
    req = create_request(db, org_id=org, user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Testland",
                         visa_type="tourist",
                         portal_evidence={"hostnames": [HOST]},
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")
    if consented:
        ao.record_consent(db, req, application_id="app-att-1", actor="applicant-1")
    return req


def _job(db, req):
    return att.ensure_recon_job(db, req, [HOST])


def _obs(url="https://evisa.gov.example/apply", elements=(), **kw):
    return {"ok": True, "status": 200, "url": url, "hostname": HOST,
            "title": kw.get("title", "Apply"),
            "elements": list(elements), "links": [], "iframes": []}


LOGIN_PAGE = _obs(url=f"https://{HOST}/login", elements=[
    {"selector": "#u", "name": "username", "label": "Username", "type": "email"},
    {"selector": "#p", "name": "password", "label": "Password", "type": "password"},
])
FORM_PAGE = _obs(url=f"https://{HOST}/apply", elements=[
    {"selector": "#sn", "name": "surname", "label": "Surname", "type": "text"},
    {"selector": "#gn", "name": "given", "label": "Given names", "type": "text"},
    {"selector": "#dob", "name": "dob", "label": "Date of birth", "type": "date"},
    {"selector": "#nat", "name": "nationality", "label": "Nationality", "type": "select"},
])


def test_recording_refuses_without_consent(db):
    req = _req(db, consented=False, route_key="att|noconsent")
    with pytest.raises(ao.ObservationRefused):
        att.start(db, req, application_id="app-x", hosts=[HOST])


def test_login_page_is_recorded_but_never_mistaken_for_the_form(db):
    req = _req(db)
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    seen = set()
    assert att.record_tick(db, req, job_id=job.id, obs=LOGIN_PAGE, seen=seen, watch=w)
    assert att.record_tick(db, req, job_id=job.id, obs=FORM_PAGE, seen=seen, watch=w)
    arts = ao.artifacts_for(db, job.id)
    classes = sorted(a.content_class for a in arts)
    assert classes == ["application_form", "authorized_session"]
    assert w.pages == 2 and w.forms == 1


def test_what_the_applicant_typed_never_survives(db):
    """Plant applicant-shaped data everywhere a raw extraction could carry it
    and prove none of it reaches the database."""
    import json
    from sqlalchemy import select
    req = _req(db, route_key="att|pii")
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    dirty = _obs(url=f"https://{HOST}/apply?passport=L898902C3", elements=[
        {"selector": "#sn", "name": "surname", "label": "Surname",
         "type": "text", "value": "NAWALY", "placeholder": "e.g. L898902C3"},
        {"selector": "#em", "name": "email", "label": "real@example.com",
         "type": "email", "value": "real@example.com"},
        {"selector": "#dob", "name": "dob", "label": "Date of birth", "type": "date"},
    ])
    att.record_tick(db, req, job_id=job.id, obs=dirty, seen=set(), watch=w)
    blob = json.dumps([a.structure for a in db.execute(
        select(fm.AdapterReconArtifact).where(
            fm.AdapterReconArtifact.recon_job_id == job.id)).scalars().all()])
    assert "L898902C3" not in blob
    assert "real@example.com" not in blob
    assert "NAWALY" not in blob


def test_dedupe_is_by_shape_so_spa_steps_all_survive(db):
    """India/Thailand keep ONE url across every screen. URL-dedupe would
    collapse the whole application into its first page."""
    req = _req(db, route_key="att|spa")
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    seen = set()
    assert att.record_tick(db, req, job_id=job.id, obs=FORM_PAGE, seen=seen, watch=w)
    # The identical page polled again: nothing new.
    assert not att.record_tick(db, req, job_id=job.id, obs=FORM_PAGE, seen=seen, watch=w)
    # Same URL, next SPA step (different fields): a REAL new page.
    step2 = _obs(url=f"https://{HOST}/apply", elements=[
        {"selector": "#pn", "name": "passport_number", "label": "Passport number",
         "type": "text"},
        {"selector": "#pi", "name": "issue_date", "label": "Issue date", "type": "date"},
        {"selector": "#pe", "name": "expiry_date", "label": "Expiry date", "type": "date"},
    ])
    assert att.record_tick(db, req, job_id=job.id, obs=step2, seen=seen, watch=w)
    assert w.pages == 2


def test_a_sessions_artifacts_carry_their_provenance(db):
    """A gate report must be able to say HOW the evidence was obtained; the
    attended path must never launder its artifacts into public ones."""
    req = _req(db, route_key="att|prov")
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, obs=LOGIN_PAGE, seen=set(), watch=w)
    [art] = ao.artifacts_for(db, job.id)
    assert art.content_class == ao.CONTENT_CLASS
    assert "consented signed-in applicant session" in ao.provenance_note(req)


def test_finish_with_no_form_declines_to_rebuild(db):
    req = _req(db, route_key="att|noform")
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, obs=LOGIN_PAGE, seen=set(), watch=w)
    att._watches[req.id] = w
    out = att.finish(db, req, family_id="fam-x", actor="applicant-1")
    assert out["rebuilt"] is False
    assert "no application form" in out["reason"]
    # And no background rebuild was registered for it.
    assert att._rebuilds.get(req.id) is None


def test_ellis_never_acts_in_the_applicants_session():
    """The watcher may read and (once) open the start page — nothing else.
    Pin the absence of every acting verb on the page object."""
    import inspect
    src = inspect.getsource(att)
    for verb in ("page.click", "page.fill", "page.type", "page.press",
                 "page.check", "page.select_option", "page.set_input_files",
                 "captcha"):
        assert verb not in src, f"attended observation must never call {verb}"
    assert src.count("page.goto") == 1, "exactly one navigation: the start page"


def test_frontends_own_read_calls_are_kept_as_readonly_status_notes(db):
    """The JSON reads the portal's own page made are written down as READ-ONLY
    status candidates — scrubbed to a value-free template (the applicant's id
    masked, the token-bearing query dropped) and never a submit target."""
    import json
    req = _req(db, route_key="att|ep")
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    obs = dict(FORM_PAGE, url=f"https://{HOST}/apply", network=[
        {"url": f"https://api.{HOST}/v1/applications/778899/status?tok=SESSIONTOK",
         "method": "GET", "resource_type": "xhr", "content_type": "application/json"},
        # A POST is the SUBMIT verb — it must never be written down.
        {"url": f"https://api.{HOST}/v1/applications", "method": "POST",
         "resource_type": "xhr", "content_type": "application/json"}])
    att.record_tick(db, req, job_id=job.id, obs=obs, seen=set(), watch=w)
    store = (req.portal_evidence or {}).get(att.STATUS_ENDPOINT_EVIDENCE_KEY)
    assert [e["url_pattern"] for e in store] == \
        [f"https://api.{HOST}/v1/applications/:id/status"]
    assert store[0]["usage"] == att.STATUS_ENDPOINT_USAGE
    blob = json.dumps(store)
    assert "778899" not in blob and "SESSIONTOK" not in blob and "POST" not in blob


def test_a_session_with_no_network_info_records_no_endpoint_notes(db):
    """No network info is a no-op — a portal is never handed a fabricated
    endpoint."""
    req = _req(db, route_key="att|noep")
    job = _job(db, req)
    w = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, obs=FORM_PAGE, seen=set(), watch=w)
    assert att.STATUS_ENDPOINT_EVIDENCE_KEY not in (req.portal_evidence or {})
