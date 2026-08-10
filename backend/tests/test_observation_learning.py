"""What an attended session is allowed to leave behind after the build ends.

One applicant walking one real form is the most expensive evidence Ellis will
ever get, so the session now teaches two family-scoped stores. These tests pin
the limits that make that safe:

  * a mapping reaches memory only after passing specgen's grounding chokepoint,
    and only from a page the applicant actually walked — a rejected proposal
    and a public recon page teach nothing;
  * a discovered endpoint is a READ-ONLY advisory note. Only GET/HEAD calls the
    portal's own frontend made are written down, applicant identifiers are
    masked out of them, and no submit path in the codebase can reach the store;
  * neither store records anything without the applicant's live consent.

Family-agnostic on purpose: the same code path is exercised for a tourist
e-visa family (vietnam-evisa, usa-esta) and a work-visa family
(usa-uscis-myaccount), because a portal family is all either one is here.
"""
import inspect
import json
import pathlib

import pytest
from sqlalchemy import select

from app import attended_observation as att
from app import authorized_observation as ao
from app.adapter_factory import mapping_memory, models as fm, recon, specgen
from app.adapter_factory.build_workflow import create_request, record_consent


VN_HOST = "evisa.xuatnhapcanh.gov.vn"      # tourist e-visa (vietnam-evisa)
ESTA_HOST = "esta.cbp.dhs.gov"             # tourist ETA (usa-esta)
USCIS_HOST = "myaccount.uscis.gov"         # work visa (usa-uscis-myaccount)


def _req(db, *, host, route_key, consented=True, org="orgLEARN"):
    req = create_request(db, org_id=org, user_id="applicant-1", application_id="",
                         route_key=route_key, destination="Testland",
                         visa_type="tourist",
                         portal_evidence={"hostnames": [host]},
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="applicant-1")
    if consented:
        ao.record_consent(db, req, application_id="app-learn", actor="applicant-1")
    return req


def _form_obs(host, **extra):
    return {"ok": True, "status": 200, "url": f"https://{host}/apply",
            "hostname": host, "title": "Apply", "links": [], "iframes": [],
            "elements": [
                {"selector": "#surname", "name": "surname", "label": "Surname",
                 "type": "text", "required": True},
                {"selector": "#given", "name": "givenNames",
                 "label": "Given names", "type": "text", "required": True},
                {"selector": "#dob", "name": "dateOfBirth",
                 "label": "Date of birth", "type": "date", "required": True},
                {"selector": "#nat", "name": "nationality", "label": "Nationality",
                 "type": "select", "required": True},
                {"selector": "#next", "name": "next", "label": "Continue",
                 "type": "button", "submits": "form"},
            ], **extra}


def _walk_and_spec(db, req, host):
    """One consented page walked by the applicant, then the ordinary spec
    generation the rebuild performs over the job's artifacts."""
    job = att.ensure_recon_job(db, req, [host])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, obs=_form_obs(host), seen=set(),
                    watch=watch)
    arts = recon.artifacts(db, job.id)
    spec = specgen.generate_specification(db, build_request=req, recon_job=job,
                                          artifacts=arts)
    return job, arts, spec


def _learned(db, family_id):
    return db.execute(select(fm.LearnedFieldMapping).where(
        fm.LearnedFieldMapping.family_id == family_id)).scalars().all()


# ------------------------------------------------- witnessed mapping capture --
@pytest.mark.parametrize("family_id,host,route_key", [
    # CROSS-EDITION: a tourist e-visa family and a work-visa family run the
    # identical path. Nothing here reads a visa type or an edition.
    ("vietnam-evisa", VN_HOST, "learn|vn-tourist"),
    ("usa-uscis-myaccount", USCIS_HOST, "learn|uscis-work"),
])
def test_what_the_applicant_walked_is_remembered_for_the_family(
        db, family_id, host, route_key):
    req = _req(db, host=host, route_key=route_key)
    job, arts, spec = _walk_and_spec(db, req, host)
    accepted = {m["ellis_field"] for m in spec.field_mappings}
    assert {"surname", "given_names", "birth_date", "nationality"} <= accepted

    n = att.remember_witnessed_mappings(db, req, family_id=family_id, spec=spec,
                                        artifacts=arts,
                                        source="human_correction",
                                        actor="applicant-1")
    assert n == len(spec.field_mappings)

    # The next build of this family recognizes those fields from its own
    # observation: memory supplies the meaning, the page supplies the address.
    recalled = mapping_memory.lookup(db, family_id,
                                     mapping_memory.observed_rows(arts))
    by_field = {p["ellis_field"]: p for p in recalled}
    assert {"surname", "given_names", "birth_date", "nationality"} <= set(by_field)
    assert by_field["surname"]["selector"] == "#surname"
    assert all(r.source == "human_correction" and r.confidence == "confirmed"
               for r in _learned(db, family_id))


def test_a_rejected_mapping_never_lands_in_memory(db):
    """The chokepoint is the only judge. A proposal it refused is not evidence,
    however confidently the mapper offered it."""
    req = _req(db, host=ESTA_HOST, route_key="learn|esta-rejected")
    job = att.ensure_recon_job(db, req, [ESTA_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, obs=_form_obs(ESTA_HOST),
                    seen=set(), watch=watch)
    arts = recon.artifacts(db, job.id)

    def _lying_mapper(artifacts):
        art = artifacts[0]
        base = {"artifact_id": art.id, "page_key": art.page_key, "required": True,
                "kind": "text"}
        return [
            # Grounded and true: the selector recon actually observed.
            dict(base, ellis_field="surname", portal_field="surname",
                 selector="#surname"),
            # Cites a selector nobody observed -> selector_mismatch.
            dict(base, ellis_field="passport_number", portal_field="surname",
                 selector="#passport-number-that-was-never-there"),
            # Names an answer key Ellis does not have -> unknown_ellis_field.
            dict(base, ellis_field="favourite_colour", portal_field="nationality",
                 selector="#nat"),
        ]

    specgen.set_field_mapper(_lying_mapper)
    try:
        spec = specgen.generate_specification(db, build_request=req,
                                              recon_job=job, artifacts=arts)
    finally:
        specgen.set_field_mapper(None)

    reasons = {r for rej in spec.generation_basis["rejected_mappings"]
               for r in rej["reasons"]}
    assert "selector_mismatch_with_observation" in reasons
    assert "unknown_ellis_field" in reasons
    assert "passport_number" not in {m["ellis_field"] for m in spec.field_mappings}

    att.remember_witnessed_mappings(db, req, family_id="usa-esta", spec=spec,
                                    artifacts=arts, source="human_correction",
                                    actor="applicant-1")
    learned = {r.ellis_field for r in _learned(db, "usa-esta")}
    assert "surname" in learned                      # the accepted one did land
    assert "passport_number" not in learned
    assert "favourite_colour" not in learned


def test_a_page_nobody_walked_is_not_witnessed_evidence(db):
    """A job can also hold public recon pages. Calling those a human
    correction would be a lie, so they teach nothing."""
    req = _req(db, host=VN_HOST, route_key="learn|vn-public-mix")
    job, arts, _ = _walk_and_spec(db, req, VN_HOST)
    public = fm.AdapterReconArtifact(
        recon_job_id=job.id, page_key="apply", hostname=VN_HOST,
        url_pattern=f"https://{VN_HOST}/public",
        structure=recon.sanitize_structure(_form_obs(VN_HOST)),
        content_class="public_page")
    db.add(public)
    db.commit()
    arts = recon.artifacts(db, job.id)
    spec = specgen.generate_specification(db, build_request=req, recon_job=job,
                                          artifacts=arts)
    assert any(m["artifact_id"] == public.id for m in spec.field_mappings)

    att.remember_witnessed_mappings(db, req, family_id="vietnam-prearrival",
                                    spec=spec, artifacts=arts,
                                    source="human_correction", actor="applicant-1")
    pages = {r.page_key for r in _learned(db, "vietnam-prearrival")}
    assert pages and all(p.startswith(att.ATTENDED_PAGE_PREFIX) for p in pages)


def test_withdrawn_consent_stops_the_learning_too(db):
    """Withdrawal means nothing further is recorded, and a memory row derived
    from that session is a recording."""
    req = _req(db, host=VN_HOST, route_key="learn|vn-withdrawn")
    _job, arts, spec = _walk_and_spec(db, req, VN_HOST)
    ao.withdraw_consent(db, req, actor="applicant-1")
    assert att.remember_witnessed_mappings(
        db, req, family_id="thailand-tdac", spec=spec, artifacts=arts,
        source="human_correction", actor="applicant-1") == 0
    assert _learned(db, "thailand-tdac") == []


# ------------------------------------------ read-only internal-endpoint notes --
def _obs_with_network(host, calls):
    return dict(_form_obs(host), network=calls)


def test_the_frontends_own_read_calls_are_kept_as_advisory_notes(db):
    """CROSS-EDITION: a tourist e-visa portal's own status XHR."""
    req = _req(db, host=VN_HOST, route_key="learn|vn-endpoints")
    job = att.ensure_recon_job(db, req, [VN_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, seen=set(), watch=watch,
                    obs=_obs_with_network(VN_HOST, [
                        {"url": f"https://{VN_HOST}/api/application/status",
                         "method": "GET", "resource_type": "xhr"},
                        {"url": f"https://{VN_HOST}/api/reference/countries",
                         "method": "GET", "content_type": "application/json"},
                    ]))
    stored = (req.portal_evidence or {})[att.STATUS_ENDPOINT_EVIDENCE_KEY]
    assert [e["url_pattern"] for e in stored] == [
        f"https://{VN_HOST}/api/application/status",
        f"https://{VN_HOST}/api/reference/countries"]
    assert all(e["usage"] == att.STATUS_ENDPOINT_USAGE and e["method"] == "GET"
               and e["recon_job_id"] == job.id for e in stored)
    # The artifact itself is untouched by any of this: structure only.
    [art] = ao.artifacts_for(db, job.id)
    assert "network" not in art.structure


def test_a_write_endpoint_is_never_even_written_down(db):
    """The read-only rule is enforced on the DATA, not just on the label: a
    POST/PUT/DELETE is how a portal mutates, so it is never recorded and there
    is nothing for a submit path to find."""
    req = _req(db, host=ESTA_HOST, route_key="learn|esta-writes")
    job = att.ensure_recon_job(db, req, [ESTA_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, seen=set(), watch=watch,
                    obs=_obs_with_network(ESTA_HOST, [
                        {"url": f"https://{ESTA_HOST}/api/application/submit",
                         "method": "POST", "resource_type": "xhr"},
                        {"url": f"https://{ESTA_HOST}/api/application/1234567",
                         "method": "PUT", "resource_type": "fetch"},
                        {"url": f"https://{ESTA_HOST}/api/application/status",
                         "method": "GET", "resource_type": "xhr"},
                        # Somebody else's beacon is not this portal's endpoint.
                        {"url": "https://www.google-analytics.com/collect",
                         "method": "GET", "resource_type": "xhr"},
                        # Page chrome, not a data call.
                        {"url": f"https://{ESTA_HOST}/assets/logo.png",
                         "method": "GET", "resource_type": "image"},
                    ]))
    blob = json.dumps(req.portal_evidence or {})
    assert "/submit" not in blob and "POST" not in blob and "PUT" not in blob
    assert "google-analytics" not in blob and "logo.png" not in blob
    assert [e["url_pattern"] for e in
            req.portal_evidence[att.STATUS_ENDPOINT_EVIDENCE_KEY]] == [
        f"https://{ESTA_HOST}/api/application/status"]


def test_an_endpoint_note_carries_no_applicants_own_handle(db):
    req = _req(db, host=VN_HOST, route_key="learn|vn-idmask")
    job = att.ensure_recon_job(db, req, [VN_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, seen=set(), watch=watch,
                    obs=_obs_with_network(VN_HOST, [
                        {"url": f"https://{VN_HOST}/api/application/98761234/"
                                f"status?email=real@example.com",
                         "method": "GET", "resource_type": "xhr"},
                    ]))
    blob = json.dumps(req.portal_evidence or {})
    assert "98761234" not in blob and "real@example.com" not in blob
    assert req.portal_evidence[att.STATUS_ENDPOINT_EVIDENCE_KEY][0][
        "url_pattern"] == f"https://{VN_HOST}/api/application/:id/status"


def test_no_network_information_invents_no_endpoint(db):
    """A payload that says nothing about the network teaches nothing about it."""
    req = _req(db, host=VN_HOST, route_key="learn|vn-nonet")
    job = att.ensure_recon_job(db, req, [VN_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    att.record_tick(db, req, job_id=job.id, obs=_form_obs(VN_HOST), seen=set(),
                    watch=watch)
    assert att.STATUS_ENDPOINT_EVIDENCE_KEY not in (req.portal_evidence or {})


def test_an_spa_step_that_records_no_page_still_records_its_endpoints(db):
    """One URL, one page shape, new data calls: the artifact is a duplicate,
    the endpoint is not."""
    req = _req(db, host=VN_HOST, route_key="learn|vn-spa")
    job = att.ensure_recon_job(db, req, [VN_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    seen = set()
    assert att.record_tick(db, req, job_id=job.id, seen=seen, watch=watch,
                           obs=_obs_with_network(VN_HOST, [
                               {"url": f"https://{VN_HOST}/api/a", "method": "GET",
                                "resource_type": "xhr"}]))
    assert not att.record_tick(db, req, job_id=job.id, seen=seen, watch=watch,
                               obs=_obs_with_network(VN_HOST, [
                                   {"url": f"https://{VN_HOST}/api/b",
                                    "method": "GET", "resource_type": "xhr"}]))
    assert watch.pages == 1
    assert [e["url_pattern"] for e in
            req.portal_evidence[att.STATUS_ENDPOINT_EVIDENCE_KEY]] == [
        f"https://{VN_HOST}/api/a", f"https://{VN_HOST}/api/b"]


def test_endpoints_are_not_recorded_without_consent(db):
    req = _req(db, host=VN_HOST, route_key="learn|vn-noconsent", consented=False)
    job = att.ensure_recon_job(db, req, [VN_HOST])
    watch = att._Watch(req_id=req.id, job_id=job.id)
    with pytest.raises(ao.ObservationRefused):
        att.record_tick(db, req, job_id=job.id, seen=set(), watch=watch,
                        obs=_obs_with_network(VN_HOST, [
                            {"url": f"https://{VN_HOST}/api/application/status",
                             "method": "GET", "resource_type": "xhr"}]))
    assert att.STATUS_ENDPOINT_EVIDENCE_KEY not in (req.portal_evidence or {})


# ----------------------------------------------- the hard rule, in the source --
_SUBMIT_SIDE_MODULES = (
    "adapter_factory/runtime.py", "adapter_factory/live_driver.py",
    "adapter_factory/generator.py", "adapter_factory/specgen.py",
    "adapter_factory/release.py", "adapter_factory/compiler.py",
    "portal/live_browser.py",
)


def test_no_submit_path_can_reach_the_discovered_endpoints():
    """Read-only is a property of the codebase, not a promise in a docstring.

    Nothing that fills, submits or releases may so much as name the store, and
    any future READER of it must still be a reader: a module that references it
    and also carries an HTTP write verb would be exactly the caller this
    discovery is forbidden to grow."""
    import re
    app_dir = pathlib.Path(att.__file__).resolve().parent
    names = (att.STATUS_ENDPOINT_EVIDENCE_KEY, "_status_endpoint_candidates",
             "_record_status_endpoints", "STATUS_ENDPOINT_EVIDENCE_KEY")
    # An HTTP write, not an ORM one: `db.delete(row)` is a database call and
    # says nothing about the network.
    write_verb = re.compile(
        r"(requests|httpx|aiohttp|urllib|http|client|session|conn)\s*\.\s*"
        r"(post|put|patch|delete)\s*\(|method\s*=\s*[\"'](POST|PUT|PATCH|DELETE)")
    for path in sorted(app_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not any(n in text for n in names):
            continue
        rel = str(path.relative_to(app_dir))
        assert rel == "attended_observation.py" or not any(
            rel.endswith(m) for m in _SUBMIT_SIDE_MODULES), \
            f"{rel} is a submit path and must never name the endpoint store"
        assert not write_verb.search(text), \
            f"{rel} names the read-only endpoint store and also writes over HTTP"

    src = inspect.getsource(att)
    for caller in ("requests.", "httpx", "urlopen", "urllib.request",
                   "http.client", "aiohttp", "fetch("):
        assert caller not in src, \
            f"attended observation must build no caller ({caller})"


def test_the_sessions_own_limits_are_untouched():
    """Learning is added on top of the session's bounds, never instead of
    them: same page/minute ceiling, same wholesale scrub of applicant echoes."""
    assert (att.MAX_PAGES, att.MAX_MINUTES) == (40, 45)
    scrubbed = att._scrub_signed_in({
        "url": "https://host/apply?passport=L898902C3",
        "elements": [{"selector": "#sn", "name": "surname", "type": "text",
                      "value": "NAWALY", "placeholder": "e.g. L898902C3",
                      "title": "NAWALY"}]})
    assert scrubbed["url"] == "https://host/apply"
    assert set(scrubbed["elements"][0]) == {"selector", "name", "type"}
