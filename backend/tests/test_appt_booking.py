"""Agent-channel booking pipeline (app/appt_booking.py + its API).

Pins the compliance shape itself:
* the module performs no network I/O (source-level assert) — Ellis cannot
  poll, scrape, hold, or book a slot even by bug;
* every offered slot names who read it and when;
* `booked` exists only behind evidence (confirmation number + a confirmation
  document stored on the SAME case);
* the seats cannot cross: an applicant cannot offer slots or mark booked, an
  operator cannot pick for the applicant;
* erasure/export cover the table (privacy parity).
"""
import base64
from pathlib import Path

from tests.conftest import AUTH

ADMIN = {"Authorization": "Bearer admin-token",
         "X-Org-Id": "org1", "X-User-Id": "operator-1"}

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nBOOKING-CONFIRMATION-EVIDENCE").decode()


def _case(client):
    return client.post("/cases", headers=AUTH, json={
        "full_name": "Wei Zhang", "email": "wei@e.com",
        "destination_country": "USA"}).json()["id"]


def _request(client, cid, route="us_b1b2"):
    r = client.post(f"/appointments/booking/cases/{cid}", headers=AUTH, json={
        "route": route, "posts": ["Beijing", "Shanghai"],
        "date_windows": [{"from": "2026-10-01", "to": "2026-11-15"}],
        "note": "prefers mornings"})
    assert r.status_code == 200, r.text
    return r.json()


def _evidence_doc(client, rid):
    """A REAL booking-confirmation document: uploaded through the operator's
    /evidence endpoint (doc_type booking_confirmation), the only kind
    record_booked accepts. Requires the request to be in slot_picked."""
    r = client.post(f"/appointments/booking/{rid}/evidence", headers=ADMIN,
                    json={"name": "confirmation.png", "mime": "image/png",
                          "content_b64": PNG})
    assert r.status_code == 200, r.text
    return r.json()["document_id"]


# ------------------------------------------------------------ the wall itself

def test_booking_module_performs_no_network_io():
    # The DOMAIN module (appt_booking.py) and its API touch NO network — full
    # source scan, not just import lines, and including the indirect escape
    # hatches (importlib / __import__ / subprocess). The agent bridge
    # (appt_booking_agent.py) is deliberately excluded: it is the gated
    # browser layer, and the boundary there is the adapter contract's
    # prohibited-actions, tested separately.
    app_dir = Path(__file__).resolve().parents[1] / "app"
    # Network libraries: scanned on IMPORT lines (so domain prose like "booking
    # requests" is not a false positive), catching `import requests` /
    # `from urllib import ...` in any form.
    net_libs = ("httpx", "requests", "urllib", "aiohttp", "socket",
                "playwright", "browserbase", "http.client", "websocket")
    # Indirect-execution hatches: no legitimate prose use, so scanned in full
    # text — an importlib/__import__/subprocess sneak-around is caught too.
    hatches = ("importlib", "__import__", "subprocess", "eval(", "exec(")
    for name in ("appt_booking.py", "appt_booking_api.py"):
        text = (app_dir / name).read_text()
        imports = [ln for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        for tok in net_libs:
            for ln in imports:
                assert tok not in ln, f"{name} must not import {tok}: {ln}"
        for tok in hatches:
            assert tok not in text, f"{name} must never reference {tok}"
    # And proven at runtime: importing the domain module opens no socket.
    import socket as _socket
    real = _socket.socket

    def _boom(*a, **k):
        raise AssertionError("appt_booking must not open a socket")

    _socket.socket = _boom
    try:
        import importlib
        import app.appt_booking as _m
        importlib.reload(_m)
    finally:
        _socket.socket = real


# ------------------------------------------------------------- the lifecycle

def test_full_lifecycle_request_offer_pick_book_with_evidence(client):
    cid = _case(client)
    req = _request(client, cid)
    assert req["status"] == "requested"
    assert req["is_real_government_result"] is False
    assert "named person" in req["never_automated_notice"]
    assert req["legal_basis"]["basis"]
    rid = req["id"]

    # Operator records what they saw — each slot stamped who/when.
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": [
                        {"post": "Beijing", "when": "2026-10-12T09:30",
                         "label": "morning"},
                        {"post": "Shanghai", "when": "2026-10-19T14:00"}]})
    assert r.status_code == 200, r.text
    offered = r.json()
    assert offered["status"] == "slots_offered"
    for s in offered["offered_slots"]:
        assert s["recorded_by"] == "operator-1" and s["recorded_at"]
        assert s["source"] == "operator_manual"   # a person typed these
    assert offered["agent_read"] is False
    assert "not live inventory" in offered["slots_notice"].lower() or \
        "Not live inventory" in offered["slots_notice"]

    # Applicant picks inside Trip.com.
    r = client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                    json={"index": 0})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "slot_picked"
    assert r.json()["picked_slot"]["post"] == "Beijing"

    # Booked ONLY behind evidence.
    doc_id = _evidence_doc(client, rid)
    r = client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                    json={"confirmation_number": "USV-2026-778899",
                          "evidence_document_id": doc_id})
    assert r.status_code == 200, r.text
    booked = r.json()
    assert booked["status"] == "booked"
    assert booked["is_real_government_result"] is True
    assert booked["confirmation"]["number"] == "USV-2026-778899"
    assert booked["confirmation"]["evidence_document_id"] == doc_id


def test_booked_is_refused_without_evidence(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    # No confirmation number.
    r = client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                    json={"confirmation_number": "",
                          "evidence_document_id": "whatever"})
    assert r.status_code == 422
    # No evidence document.
    r = client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                    json={"confirmation_number": "USV-1",
                          "evidence_document_id": ""})
    assert r.status_code == 422
    # An applicant's OWN upload (a passport, a photo — not a booking
    # confirmation) is refused: booked accepts only doc_type
    # booking_confirmation, never any file that happens to be on the case.
    passport = client.post(f"/cases/{cid}/documents", headers=AUTH,
                           json={"name": "passport.png", "mime": "image/png",
                                 "size_bytes": 64, "content_b64": PNG}).json()["id"]
    r = client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                    json={"confirmation_number": "USV-1",
                          "evidence_document_id": passport})
    assert r.status_code == 409
    assert "not a booking confirmation" in r.json()["detail"]["reason"]


def test_booked_requires_a_picked_slot_first(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    # Status is checked before the document, so a 'requested' request is
    # refused regardless of the evidence id.
    r = client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                    json={"confirmation_number": "USV-1",
                          "evidence_document_id": "anything"})
    assert r.status_code == 409  # the applicant has not picked


def test_seats_cannot_cross(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    # Applicant cannot offer slots or mark booked or fail.
    for path, body in ((f"/appointments/booking/{rid}/offer-slots",
                        {"slots": [{"post": "B", "when": "2026-10-12"}]}),
                       (f"/appointments/booking/{rid}/booked",
                        {"confirmation_number": "X",
                         "evidence_document_id": "Y"}),
                       (f"/appointments/booking/{rid}/failed",
                        {"reason": "nope"})):
        r = client.post(path, headers=AUTH, json=body)
        assert r.status_code == 403, path
    # Applicant queue read is refused too (it spans cases).
    assert client.get("/appointments/booking/queue",
                      headers=AUTH).status_code == 403
    # Operator offers; the pick endpoint enforces case ownership, which the
    # operator's org headers do satisfy in local dev — the wall that matters
    # in production is the role split above plus Clerk-scoped ownership.
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": [{"post": "B", "when": "2026-10-12"}]})
    assert r.status_code == 200


def test_transitions_are_guarded_and_failure_needs_a_reason(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    # Pick before any slots exist -> 409.
    r = client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                    json={"index": 0})
    assert r.status_code == 409
    # Empty slot list is refused with the honest alternative.
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": []})
    assert r.status_code == 422
    # A slot without post/when is refused.
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": [{"post": "", "when": ""}]})
    assert r.status_code == 422
    # Failure without a reason is refused; with one it lands and terminates.
    r = client.post(f"/appointments/booking/{rid}/failed", headers=ADMIN,
                    json={"reason": ""})
    assert r.status_code == 422
    r = client.post(f"/appointments/booking/{rid}/failed", headers=ADMIN,
                    json={"reason": "no slots inside the requested windows"})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    # Terminal is terminal.
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": [{"post": "B", "when": "2026-10-12"}]})
    assert r.status_code == 409


def test_one_active_request_per_case_and_cancel_frees_it(client):
    cid = _case(client)
    _request(client, cid)
    r = client.post(f"/appointments/booking/cases/{cid}", headers=AUTH,
                    json={"route": "us_b1b2", "posts": ["Beijing"]})
    assert r.status_code == 409
    rid = client.get(f"/appointments/booking/cases/{cid}",
                     headers=AUTH).json()["id"]
    r = client.post(f"/appointments/booking/{rid}/cancel", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _request(client, cid)["status"] == "requested"  # free again


def test_reoffer_supersedes_and_clears_the_pick(client):
    cid = _case(client)
    rid = _request(client, cid, route="schengen")["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Paris", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": [{"post": "Paris", "when": "2026-10-20"}]})
    out = r.json()
    assert out["status"] == "slots_offered"
    assert out["picked_slot"] == {}
    # And the Schengen basis cites Art. 45.
    assert "Art. 45" in out["legal_basis"]["basis"]


def test_operator_evidence_upload_validates_and_enables_booked(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    # Applicant may not upload operator evidence.
    r = client.post(f"/appointments/booking/{rid}/evidence", headers=AUTH,
                    json={"name": "c.png", "mime": "image/png",
                          "content_b64": PNG})
    assert r.status_code == 403
    # Bytes must match the declared type.
    r = client.post(f"/appointments/booking/{rid}/evidence", headers=ADMIN,
                    json={"name": "c.pdf", "mime": "application/pdf",
                          "content_b64": PNG})
    assert r.status_code == 415
    # A real upload lands on the case and unlocks booked.
    r = client.post(f"/appointments/booking/{rid}/evidence", headers=ADMIN,
                    json={"name": "confirmation.png", "mime": "image/png",
                          "content_b64": PNG})
    assert r.status_code == 200, r.text
    doc_id = r.json()["document_id"]
    r = client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                    json={"confirmation_number": "USV-42",
                          "evidence_document_id": doc_id})
    assert r.status_code == 200
    assert r.json()["is_real_government_result"] is True


def test_queue_is_admin_only_and_oldest_first(client):
    a, b = _case(client), _case(client)
    _request(client, a)
    _request(client, b)
    q = client.get("/appointments/booking/queue", headers=ADMIN).json()
    ids = [r["case_id"] for r in q["requests"]]
    assert ids.index(a) < ids.index(b)
    assert "OWN session" in q["never_automated_notice"]


# ------------------------------------------------- the agent execution layer

class _FakeSchedulingDriver:
    """Deterministic stand-in for the gated live driver: serves a calendar
    read and a booking confirmation the way live_driver's evidence-only
    contract does. Injection is test-only — production resolves the driver
    through select_runtime_adapter's fail-closed path."""

    def __init__(self, slots=None, confirmation="", capture=b""):
        self._slots = slots if slots is not None else []
        self._confirmation = confirmation
        self._capture = capture

    def search_appointments(self, **kw):
        return {"ok": True, "slots": self._slots}

    def book_appointment(self, **kw):
        out = {"ok": bool(self._confirmation),
               "confirmation": self._confirmation}
        if self._capture:
            out["confirmation_capture"] = self._capture
            out["capture_mime"] = "image/png"
        return out

    def live_view_url(self):
        return "https://live.example/session"


def test_agent_reads_the_calendar_and_records_sourced_slots(client, db):
    from app import appt_booking, appt_booking_agent, models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    row = db.get(models.AppointmentBookingRequest, rid)
    drv = _FakeSchedulingDriver(slots=[
        {"post": "Beijing", "when": "2026-10-12T09:30", "label": "morning"},
        {"location": "Shanghai", "start": "2026-10-19T14:00"}])
    out = appt_booking_agent.read_slots(db, row, operator="operator-1",
                                        driver=drv)
    assert out == {"ran": True, "count": 2}
    view = appt_booking.view(row)
    assert view["agent_read"] is True
    for s in view["offered_slots"]:
        assert s["source"] == "ellis_agent"
        assert "Ellis agent" in s["recorded_by"]
        assert "operator-1" in s["recorded_by"]   # whose session it ran in


def test_agent_empty_calendar_is_an_honest_answer_not_slots(client, db):
    from app import appt_booking_agent, models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    row = db.get(models.AppointmentBookingRequest, rid)
    out = appt_booking_agent.read_slots(db, row, operator="op",
                                        driver=_FakeSchedulingDriver(slots=[]))
    assert out["ran"] is True and out["empty"] is True
    assert row.status == "requested"          # nothing invented, nothing moved


def test_agent_books_with_captured_evidence(client, db):
    from app import appt_booking, appt_booking_agent, models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    db.expire_all()
    row = db.get(models.AppointmentBookingRequest, rid)
    stored = {}

    def _store(name, mime, content):
        doc = models.StoredDocument(org_id=row.org_id,
                                    application_id=row.application_id,
                                    name=name, mime=mime,
                                    size_bytes=len(content), sha256="x",
                                    doc_type="booking_confirmation")
        db.add(doc)
        db.flush()
        stored["id"] = doc.id
        return doc.id

    drv = _FakeSchedulingDriver(confirmation="USV-AGENT-1",
                                capture=b"\x89PNG\r\n\x1a\ncapture")
    out = appt_booking_agent.book(db, row, operator="operator-1", driver=drv,
                                  store_evidence=_store)
    assert out["confirmation_number"] == "USV-AGENT-1"
    assert row.status == "booked"
    assert row.confirmation["evidence_document_id"] == stored["id"]
    assert "Ellis agent" in row.confirmation["recorded_by"]


def test_agent_without_capture_never_records_booked(client, db):
    from app import appt_booking_agent, models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    db.expire_all()
    row = db.get(models.AppointmentBookingRequest, rid)
    drv = _FakeSchedulingDriver(confirmation="USV-NO-CAPTURE", capture=b"")
    try:
        appt_booking_agent.book(db, row, operator="op", driver=drv,
                                store_evidence=None)
        raise AssertionError("must not book without captured evidence")
    except appt_booking_agent.AgentUnavailable as e:
        # The real confirmation number is preserved for the operator, but
        # booked is NOT recorded without the document.
        assert e.kind == "capture_missing"
        assert e.confirmation_hint == "USV-NO-CAPTURE"
    assert row.status == "slot_picked"


def test_agent_reads_a_real_calendar_dom_end_to_end(client, db, monkeypatch):
    """The full chain with the REAL driver class: adapter-declared selectors
    -> DOM read -> agent normalization -> sourced slots on the request -> the
    payload the Trip.com panel renders. Only the page is fake (a served
    calendar); every layer of Ellis in between is the production code path."""
    import os

    from app import appt_booking, appt_booking_agent, config, models
    from tests.test_live_driver import (_CalendarAdapter, _CalendarPage,
                                        _FakeNode)

    monkeypatch.setenv("ELLIS_RUNTIME_MODE", "production")
    config.settings.cache_clear()
    try:
        from app.portal.live_driver import BrowserbaseLiveViewDriver
        page = _CalendarPage(nodes=[
            _FakeNode({".date": "2026-10-12", ".time": "09:30",
                       ".kind": "morning"}),
            _FakeNode({".date": "2026-10-19", ".time": "14:00"})])
        driver = BrowserbaseLiveViewDriver(
            _CalendarAdapter(), page=page,
            session={"id": "sess", "connect_url": None},
            require_real_key=False)
        cid = _case(client)
        rid = _request(client, cid)["id"]
        row = db.get(models.AppointmentBookingRequest, rid)
        out = appt_booking_agent.read_slots(db, row, operator="operator-1",
                                            driver=driver)
        assert out == {"ran": True, "count": 2}
        view = appt_booking.view(row)
        assert view["status"] == "slots_offered"
        assert view["agent_read"] is True
        assert view["offered_slots"][0]["when"] == "2026-10-12 09:30"
        # No post named on the calendar node -> the applicant's own first
        # preference (Beijing) labels the slot.
        assert view["offered_slots"][0]["post"] == "Beijing"
    finally:
        monkeypatch.delenv("ELLIS_RUNTIME_MODE", raising=False)
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
        config.settings.cache_clear()


def test_agent_endpoint_fails_closed_honestly_with_no_live_adapter(client):
    # No production-approved scheduling adapter exists in this environment, so
    # the AGENT endpoint must answer with ran:False and a reason — the manual
    # desk remains fully usable, and the request is untouched.
    cid = _case(client)
    rid = _request(client, cid)["id"]
    r = client.post(f"/appointments/booking/{rid}/agent/read-slots",
                    headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["agent"]["ran"] is False
    assert out["agent"]["kind"] in ("unsupported", "not_ready")
    assert out["agent"]["reason"]
    assert out["status"] == "requested"
    # Applicant cannot invoke the agent.
    assert client.post(f"/appointments/booking/{rid}/agent/read-slots",
                       headers=AUTH).status_code == 403
    assert client.post(f"/appointments/booking/{rid}/agent/book",
                       headers=AUTH).status_code == 403


def test_agent_translates_a_human_check_to_the_operator(client, db):
    from app import appt_booking_agent, models

    class _ChallengedDriver(_FakeSchedulingDriver):
        def search_appointments(self, **kw):
            raise RuntimeError("portal presented a CAPTCHA challenge")

    cid = _case(client)
    rid = _request(client, cid)["id"]
    row = db.get(models.AppointmentBookingRequest, rid)
    try:
        appt_booking_agent.read_slots(db, row, operator="op",
                                      driver=_ChallengedDriver())
        raise AssertionError("a human check must surface, not vanish")
    except appt_booking_agent.AgentUnavailable as e:
        assert e.kind == "human_check"
        assert "never clears one" in e.reason
        assert e.live_view_url  # the operator gets the live view to clear it


def test_a_generic_stop_is_not_mislabeled_as_a_human_check(client, db):
    # A RealOnlyStop (off-allowlist URL, no session, unsupported route) has
    # nothing for the operator to clear — it must fall back to manual
    # (kind='not_ready'), never an unbreakable 'clear the check and retry'.
    from app import appt_booking_agent, models
    from app.portal.driver_factory import RealOnlyStop, STOP_PORTAL_UNAVAILABLE

    class _StoppedDriver(_FakeSchedulingDriver):
        def search_appointments(self, **kw):
            raise RealOnlyStop(STOP_PORTAL_UNAVAILABLE,
                               "no Browserbase connect URL for the session")

    cid = _case(client)
    rid = _request(client, cid)["id"]
    row = db.get(models.AppointmentBookingRequest, rid)
    try:
        appt_booking_agent.read_slots(db, row, operator="op",
                                      driver=_StoppedDriver())
        raise AssertionError("should raise AgentUnavailable")
    except appt_booking_agent.AgentUnavailable as e:
        assert e.kind == "not_ready"       # NOT human_check
        assert "could not continue" in e.reason


def test_on_an_h1b_case_only_the_beneficiary_may_book(client, db):
    # An H1B case has two parties. Booking the appointment is the BENEFICIARY's
    # personal act: the petitioner (employer), though same-org and non-admin,
    # is refused. This closes the seat-crossing hole where 'not admin' was
    # treated as 'is the applicant'.
    from app.h1b import models as h1b_models
    from sqlalchemy import select
    PET = {"Authorization": "Bearer dev-token", "X-Org-Id": "org1",
           "X-User-Id": "employer-hr"}
    BEN = {"Authorization": "Bearer dev-token", "X-Org-Id": "org1",
           "X-User-Id": "worker-wei"}
    # The employer opens the petition (acting_as petitioner -> bound to
    # petitioner, beneficiary seat invited/unbound).
    r = client.post("/h1b/cases", headers=PET, json={
        "case_kind": "extension", "beneficiary_full_name": "WEI ZHANG",
        "beneficiary_email": "wei@e.com", "acting_as": "petitioner"})
    assert r.status_code == 200, r.text
    cid = r.json()["case_id"]
    # Bind the worker to the beneficiary seat (what the worker's join does).
    ben = db.execute(select(h1b_models.CaseParty).where(
        h1b_models.CaseParty.application_id == cid,
        h1b_models.CaseParty.role == "beneficiary")).scalars().first()
    ben.user_id = "worker-wei"
    db.commit()
    # The petitioner is refused the booking act...
    r = client.post(f"/appointments/booking/cases/{cid}", headers=PET,
                    json={"route": "us_b1b2", "posts": ["Beijing"]})
    assert r.status_code == 403
    assert "beneficiary" in r.json()["detail"]["reason"]
    # ...the beneficiary is allowed.
    r = client.post(f"/appointments/booking/cases/{cid}", headers=BEN,
                    json={"route": "us_b1b2", "posts": ["Beijing"]})
    assert r.status_code == 200, r.text


def test_privacy_export_returns_the_booking_and_its_evidence(client, db):
    from app import models
    cid = _case(client)
    rid = _request(client, cid, route="us_b1b2")["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    doc_id = _evidence_doc(client, rid)
    client.post(f"/appointments/booking/{rid}/booked", headers=ADMIN,
                json={"confirmation_number": "USV-9", "evidence_document_id": doc_id})
    out = client.get(f"/cases/{cid}/export", headers=AUTH).json()
    reqs = out["appointment_booking_requests"]
    assert reqs and reqs[0]["confirmation"]["number"] == "USV-9"
    assert reqs[0]["requested_by"] and reqs[0]["requested_at"]
    # The evidence document id resolves to a document INSIDE the bundle.
    doc_ids = {d["id"] for d in out["documents"]}
    assert reqs[0]["confirmation"]["evidence_document_id"] in doc_ids


def test_privacy_erasure_deletes_booking_rows_and_evidence(client, db):
    from app import models
    from sqlalchemy import select
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH, json={"index": 0})
    doc_id = _evidence_doc(client, rid)
    # Erase the case (right to be forgotten).
    r = client.delete(f"/cases/{cid}", headers=AUTH)
    assert r.status_code in (200, 204), r.text
    db.expire_all()
    assert db.get(models.AppointmentBookingRequest, rid) is None
    assert db.get(models.StoredDocument, doc_id) is None
    remaining = db.execute(select(models.AppointmentBookingRequest).where(
        models.AppointmentBookingRequest.application_id == cid)).scalars().all()
    assert remaining == []


def test_operator_cannot_perform_the_applicants_booking_acts(client):
    # The seat wall runs BOTH ways: an operator (admin) is refused the
    # applicant's create / pick / cancel, only their own operator endpoints.
    cid = _case(client)
    # create as operator -> 403
    assert client.post(f"/appointments/booking/cases/{cid}", headers=ADMIN,
                       json={"route": "us_b1b2", "posts": ["Beijing"]}).status_code == 403
    rid = _request(client, cid)["id"]   # the applicant creates it
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    assert client.post(f"/appointments/booking/{rid}/pick", headers=ADMIN,
                       json={"index": 0}).status_code == 403
    assert client.post(f"/appointments/booking/{rid}/cancel",
                       headers=ADMIN).status_code == 403


def test_pick_binds_to_the_seen_slot_not_the_position(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    # The applicant echoes a slot that is no longer at that index (the operator
    # re-offered) -> refused, honestly, rather than binding to a new slot.
    r = client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                    json={"index": 0, "post": "Beijing", "when": "STALE-TIME"})
    assert r.status_code == 409
    assert "changed" in r.json()["detail"]["reason"]
    # Echoing the real slot succeeds.
    r = client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                    json={"index": 0, "post": "Beijing", "when": "2026-10-12"})
    assert r.status_code == 200


def test_cross_org_applicant_is_refused(client):
    from tests.conftest import AUTH2
    cid = _case(client)
    rid = _request(client, cid)["id"]
    assert client.get(f"/appointments/booking/cases/{cid}",
                      headers=AUTH2).status_code == 403
    assert client.post(f"/appointments/booking/{rid}/cancel",
                       headers=AUTH2).status_code == 403


def test_double_booked_and_cancel_from_offered(client):
    # Cancel is allowed from slots_offered (mid-flight withdrawal).
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                json={"slots": [{"post": "Beijing", "when": "2026-10-12"}]})
    assert client.post(f"/appointments/booking/{rid}/cancel",
                       headers=AUTH).status_code == 200
    # A second cancel on a terminal request is refused.
    assert client.post(f"/appointments/booking/{rid}/cancel",
                       headers=AUTH).status_code == 409


def test_evidence_upload_refused_on_a_terminal_request(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    client.post(f"/appointments/booking/{rid}/cancel", headers=AUTH)
    r = client.post(f"/appointments/booking/{rid}/evidence", headers=ADMIN,
                    json={"name": "c.png", "mime": "image/png", "content_b64": PNG})
    assert r.status_code == 409   # a cancelled request takes no evidence


def test_input_caps_bound_posts_and_windows(client, db):
    from app import models
    cid = _case(client)
    many_posts = [f"City{i}" for i in range(50)]
    long_post = "x" * 300
    r = client.post(f"/appointments/booking/cases/{cid}", headers=AUTH, json={
        "route": "us_b1b2", "posts": many_posts + [long_post],
        "date_windows": [{"from": "not-a-date", "to": "2026-11-15"}]})
    assert r.status_code == 200
    row = db.get(models.AppointmentBookingRequest, r.json()["id"])
    assert len(row.posts) <= 10
    assert all(len(p) <= 80 for p in row.posts)
    # A non-ISO 'from' is dropped, never stored as a fake date.
    assert row.date_windows[0]["from"] == ""
    assert row.date_windows[0]["to"] == "2026-11-15"


# ------------------------------------ ranked preferred times (up to five)
#
# Trip.com's ask: "up to 5 preferred appointment times in one session; the
# system prioritizes and schedules the earliest available time slot based on
# the order of preference." What these tests pin is that honouring it did NOT
# cost the doctrine: every candidate is a slot the applicant personally named,
# the order is theirs, and when they are all gone Ellis books nothing at all
# rather than substituting a time they never chose.

FIVE = [{"post": "Beijing", "when": f"2026-10-{d}T09:30", "label": "morning"}
        for d in ("05", "12", "19", "26")] + \
       [{"post": "Shanghai", "when": "2026-11-02T14:00"}]


def _offer(client, rid, slots=None):
    r = client.post(f"/appointments/booking/{rid}/offer-slots", headers=ADMIN,
                    json={"slots": slots if slots is not None else FIVE})
    assert r.status_code == 200, r.text
    return r.json()


def _rank(client, rid, indices, slots=None):
    body = {"indices": indices}
    if slots is not None:
        body["slots"] = slots
    return client.post(f"/appointments/booking/{rid}/rank", headers=AUTH,
                       json=body)


def test_ranking_five_preferred_times_is_one_applicants_own_choice(client, db):
    from app import models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    _offer(client, rid)
    # Their order, not the calendar's: the 26th first, then the 5th, and so on.
    r = _rank(client, rid, [3, 0, 4, 1, 2])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "slot_picked"
    assert out["max_ranked"] == 5
    ranked = out["ranked_slots"]
    assert [s["rank"] for s in ranked] == [1, 2, 3, 4, 5]
    assert [s["when"] for s in ranked] == [FIVE[i]["when"]
                                           for i in (3, 0, 4, 1, 2)]
    # Every ranked entry is one of the slots that was OFFERED — nothing Ellis
    # added, and each keeps the provenance of the reading it came from.
    offered = {(s["post"], s["when"]) for s in out["offered_slots"]}
    for s in ranked:
        assert (s["post"], s["when"]) in offered
        assert s["recorded_by"] == "operator-1" and s["recorded_at"]
        assert s["ranked_by"] == "user1"
    # Rank 1 IS the pick, so every downstream step is unchanged.
    assert out["picked_slot"]["when"] == FIVE[3]["when"]
    assert out["picked_slot"]["rank"] == 1
    assert out["picked_at"] and out["ranked_at"]
    row = db.get(models.AppointmentBookingRequest, rid)
    db.refresh(row)
    assert len(row.ranked_slots) == 5


def test_ranking_is_capped_deduplicated_and_range_checked(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    _offer(client, rid)
    # Six preferred times is more than the applicant was offered the chance to
    # name — refused, never silently truncated to five.
    r = _rank(client, rid, [0, 1, 2, 3, 4, 0])
    assert r.status_code == 422
    assert "at most 5" in r.json()["detail"]["reason"]
    # The same slot twice is not a preference order.
    r = _rank(client, rid, [1, 3, 1])
    assert r.status_code == 422
    assert "twice" in r.json()["detail"]["reason"]
    # A position nobody was offered.
    for bad in ([9], [0, 5], [-1]):
        r = _rank(client, rid, bad)
        assert r.status_code == 422, bad
        assert "not one of the 5 offered" in r.json()["detail"]["reason"]
    # An empty list is not a choice.
    assert _rank(client, rid, []).status_code == 422
    # Nothing landed: the request is still waiting on the applicant.
    view = client.get(f"/appointments/booking/cases/{cid}", headers=AUTH).json()
    assert view["status"] == "slots_offered"
    assert view["ranked_slots"] == []


def test_ranking_is_only_possible_from_slots_offered(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    # Before any slots exist there is nothing to rank.
    r = _rank(client, rid, [0])
    assert r.status_code == 409
    assert "cannot rank on a requested request" in r.json()["detail"]["reason"]
    _offer(client, rid)
    assert _rank(client, rid, [0, 1]).status_code == 200
    # And ranking is one act: a second one needs a fresh offer, so a stale tab
    # cannot quietly re-order a choice the desk is already working.
    assert _rank(client, rid, [2, 3]).status_code == 409
    # The operator cannot rank for the applicant either.
    assert client.post(f"/appointments/booking/{rid}/rank", headers=ADMIN,
                       json={"indices": [0]}).status_code == 403


def test_a_reoffer_clears_the_ranking_with_the_pick(client, db):
    from app import models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    _offer(client, rid)
    assert _rank(client, rid, [0, 1, 2]).status_code == 200
    # The calendar moved on: those five slots are superseded. A preference
    # order over slots that no longer exist is not a choice — it is dropped,
    # never re-pointed at whatever now sits at those positions.
    out = _offer(client, rid, [{"post": "Beijing", "when": "2026-12-01T09:00"}])
    assert out["status"] == "slots_offered"
    assert out["ranked_slots"] == [] and out["picked_slot"] == {}
    assert out["ranked_at"] == ""
    row = db.get(models.AppointmentBookingRequest, rid)
    db.refresh(row)
    assert row.ranked_slots == [] and row.ranked_at is None


def test_ranking_binds_to_the_slots_the_applicant_saw(client):
    cid = _case(client)
    rid = _request(client, cid)["id"]
    _offer(client, rid)
    # The echo is what the applicant had on screen. A mismatch means the
    # operator re-offered between render and rank -> refused, honestly.
    stale = [{"post": "Beijing", "when": FIVE[0]["when"]},
             {"post": "Beijing", "when": "STALE-TIME"}]
    r = _rank(client, rid, [0, 1], slots=stale)
    assert r.status_code == 409
    assert "changed" in r.json()["detail"]["reason"]
    # A short echo is a mismatch too, not a partial check.
    r = _rank(client, rid, [0, 1], slots=[{"post": "Beijing",
                                           "when": FIVE[0]["when"]}])
    assert r.status_code == 409
    # Echoing what they really saw succeeds.
    honest = [{"post": s["post"], "when": s["when"]} for s in (FIVE[0], FIVE[1])]
    r = _rank(client, rid, [0, 1], slots=honest)
    assert r.status_code == 200, r.text
    assert r.json()["picked_slot"]["when"] == FIVE[0]["when"]


# ------------------------------------------- next_available_rank (pure rules)

class _Ranked:
    """A bare carrier of ranked_slots — next_available_rank does no I/O, so it
    needs no database, no clock and no request row."""

    def __init__(self, entries):
        self.ranked_slots = entries


def _entries(*pairs):
    return [{"post": p, "when": w, "rank": i}
            for i, (p, w) in enumerate(pairs, start=1)]


def test_next_available_rank_walks_down_the_applicants_own_list():
    from app.appt_booking import next_available_rank as nxt
    row = _Ranked(_entries(("Beijing", "2026-10-05T09:30"),
                           ("Beijing", "2026-10-12T09:30"),
                           ("Shanghai", "2026-11-02T14:00")))
    everything = [{"post": "Beijing", "when": "2026-10-05T09:30"},
                  {"post": "Beijing", "when": "2026-10-12T09:30"},
                  {"post": "Shanghai", "when": "2026-11-02T14:00"}]
    # First choice open -> first choice.
    assert nxt(row, everything)["rank"] == 1
    # First choice gone -> their SECOND choice, not the earliest on offer.
    gone_first = everything[1:] + [{"post": "Beijing", "when": "2026-09-01T08:00"}]
    picked = nxt(row, gone_first)
    assert picked["rank"] == 2 and picked["when"] == "2026-10-12T09:30"
    # ...and the 1 September slot, earlier than anything they chose, is never
    # returned: it is not on their list.
    assert picked["when"] != "2026-09-01T08:00"
    # Only the last choice left -> the last choice.
    assert nxt(row, [everything[2]])["rank"] == 3
    # Every choice gone -> None. Not a substitute, not the nearest match.
    assert nxt(row, [{"post": "Beijing", "when": "2026-09-01T08:00"}]) is None
    assert nxt(row, []) is None
    # Post and time must BOTH match: same time at another post is another slot.
    assert nxt(row, [{"post": "Shanghai", "when": "2026-10-05T09:30"}]) is None
    # A row with no ranking at all has no ranked answer.
    assert nxt(_Ranked([]), everything) is None


def test_next_available_rank_honours_rank_over_stored_position():
    """Ordered by the applicant's rank, not by however the list was stored — a
    hand-edited or legacy row can never silently reorder their preferences."""
    from app.appt_booking import next_available_rank as nxt
    row = _Ranked([{"post": "B", "when": "T2", "rank": 2},
                   {"post": "B", "when": "T1", "rank": 1}])
    assert nxt(row, [{"post": "B", "when": "T1"},
                     {"post": "B", "when": "T2"}])["when"] == "T1"


# ------------------------------- the agent works down the list, and stops

class _RankedDriver(_FakeSchedulingDriver):
    """Books only the times it still shows as open; anything else answers with
    the codebase's SLOT_GONE — 'someone took it first, nothing was booked'."""

    def __init__(self, open_whens, echo_fresh=False, ambiguous=False):
        super().__init__(confirmation="USV-RANK-1",
                         capture=b"\x89PNG\r\n\x1a\ncapture")
        self.open = set(open_whens)
        self.echo_fresh = echo_fresh
        self.ambiguous = ambiguous
        self.tried = []

    def book_appointment(self, *, slot, **kw):
        self.tried.append(slot["when"])
        if slot["when"] in self.open:
            return {"ok": True, "confirmation": self._confirmation,
                    "confirmation_capture": self._capture,
                    "capture_mime": "image/png"}
        if self.ambiguous:
            return {"ok": False, "status": "OUTCOME_UNCERTAIN"}
        out = {"ok": False, "code": "SLOT_GONE"}
        if self.echo_fresh:
            out["slots"] = [{"post": "Beijing", "when": w}
                            for w in sorted(self.open)]
        return out


def _ranked_case(client, db, indices):
    """A request sitting at slot_picked with the applicant's ranked list."""
    from app import models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    _offer(client, rid)
    assert _rank(client, rid, indices).status_code == 200
    db.expire_all()
    return db.get(models.AppointmentBookingRequest, rid)


def _store_evidence(db, row, stored):
    from app import models

    def _save(name, mime, content):
        doc = models.StoredDocument(org_id=row.org_id,
                                    application_id=row.application_id,
                                    name=name, mime=mime,
                                    size_bytes=len(content), sha256="x",
                                    doc_type="booking_confirmation")
        db.add(doc)
        db.flush()
        stored["id"] = doc.id
        return doc.id
    return _save


def test_agent_books_the_next_ranked_choice_when_the_first_is_gone(client, db):
    from app import appt_booking_agent
    row = _ranked_case(client, db, [0, 1, 2])
    stored = {}
    drv = _RankedDriver(open_whens=[FIVE[1]["when"]])   # only their 2nd choice
    out = appt_booking_agent.book(db, row, operator="operator-1", driver=drv,
                                  store_evidence=_store_evidence(db, row, stored))
    # It tried their first choice FIRST, then stopped at the first that booked.
    assert drv.tried == [FIVE[0]["when"], FIVE[1]["when"]]
    assert out["booked_rank"] == 2 and out["ranks_gone"] == [1]
    assert row.status == "booked"
    # The record names what was ACTUALLY booked, not the choice that was taken.
    assert row.picked_slot["when"] == FIVE[1]["when"]
    assert row.picked_slot["rank"] == 2
    assert "ranked 2" in row.confirmation["note"]
    assert row.confirmation["evidence_document_id"] == stored["id"]


def test_agent_uses_the_sites_own_fresh_list_to_skip_to_their_next_choice(
        client, db):
    """When the site hands back what it still shows, the agent jumps to the
    applicant's highest-ranked choice that is really on it — still only ever
    one of theirs — instead of knocking on dead slots one by one."""
    from app import appt_booking_agent
    row = _ranked_case(client, db, [0, 1, 2])
    drv = _RankedDriver(open_whens=[FIVE[2]["when"]], echo_fresh=True)
    out = appt_booking_agent.book(db, row, operator="op", driver=drv,
                                  store_evidence=_store_evidence(db, row, {}))
    assert drv.tried == [FIVE[0]["when"], FIVE[2]["when"]]   # rank 2 skipped
    assert out["booked_rank"] == 3


def test_agent_never_substitutes_a_slot_the_applicant_did_not_rank(client, db):
    from app import appt_booking_agent
    row = _ranked_case(client, db, [0, 1, 2])
    # Their three choices are gone; a fourth and fifth slot ARE open on the
    # calendar — and Ellis books neither, because they were never chosen.
    drv = _RankedDriver(open_whens=[FIVE[3]["when"], FIVE[4]["when"]])
    try:
        appt_booking_agent.book(db, row, operator="op", driver=drv,
                                store_evidence=_store_evidence(db, row, {}))
        raise AssertionError("must not book a time the applicant never chose")
    except appt_booking_agent.AgentUnavailable as e:
        assert e.kind == "not_ready"
        assert "did not choose" in e.reason
    # Only their own three were ever attempted, and nothing was booked.
    assert drv.tried == [FIVE[0]["when"], FIVE[1]["when"], FIVE[2]["when"]]
    assert row.status == "slot_picked"
    assert row.confirmation == {}


def test_agent_stops_rather_than_guessing_when_the_site_is_ambiguous(client, db):
    """'The page did not confirm' is not 'the slot is gone' — it could mean the
    booking went through unseen. Walking to the next preference on that guess
    is how someone ends up with two appointments, so the agent stops."""
    from app import appt_booking_agent
    row = _ranked_case(client, db, [0, 1, 2])
    drv = _RankedDriver(open_whens=[FIVE[2]["when"]], ambiguous=True)
    try:
        appt_booking_agent.book(db, row, operator="op", driver=drv,
                                store_evidence=_store_evidence(db, row, {}))
        raise AssertionError("an uncertain outcome must not walk the list")
    except appt_booking_agent.AgentUnavailable as e:
        assert e.kind == "not_ready"
        assert "did not confirm" in e.reason
    assert drv.tried == [FIVE[0]["when"]]      # it did not move on
    assert row.status == "slot_picked"


def test_a_human_check_mid_list_never_advances_the_ranking(client, db):
    """A CAPTCHA says nothing about whether the slot is free. It surfaces to
    the operator with the list untouched, not as 'that one must be taken'."""
    from app import appt_booking_agent
    row = _ranked_case(client, db, [0, 1])

    class _Challenged(_RankedDriver):
        def book_appointment(self, *, slot, **kw):
            self.tried.append(slot["when"])
            raise RuntimeError("portal presented a CAPTCHA challenge")

    drv = _Challenged(open_whens=[])
    try:
        appt_booking_agent.book(db, row, operator="op", driver=drv,
                                store_evidence=_store_evidence(db, row, {}))
        raise AssertionError("a human check must surface, not vanish")
    except appt_booking_agent.AgentUnavailable as e:
        assert e.kind == "human_check"
    assert drv.tried == [FIVE[0]["when"]]
    assert row.status == "slot_picked"


def test_an_unranked_pick_still_books_exactly_as_before(client, db):
    """The single-pick path is untouched: one slot, one attempt, no rank in the
    payload."""
    from app import appt_booking_agent, models
    cid = _case(client)
    rid = _request(client, cid)["id"]
    _offer(client, rid, [{"post": "Beijing", "when": "2026-10-12"}])
    client.post(f"/appointments/booking/{rid}/pick", headers=AUTH,
                json={"index": 0})
    db.expire_all()
    row = db.get(models.AppointmentBookingRequest, rid)
    assert row.ranked_slots in ([], None)
    drv = _RankedDriver(open_whens=["2026-10-12"])
    out = appt_booking_agent.book(db, row, operator="op", driver=drv,
                                  store_evidence=_store_evidence(db, row, {}))
    assert out == {"ran": True, "confirmation_number": "USV-RANK-1"}
    assert drv.tried == ["2026-10-12"]
    assert row.status == "booked"


# --------------------------------------------------- nearest-centre (Kimi K3)

def test_nearest_centre_is_authenticated_and_fails_closed(client):
    """The centre finder is part of the booking desk (moved off the demo
    surface 2026-08-18): a session endpoint, never an open model relay, and
    every failure is available:false — the caller's own great-circle sort is
    the fallback, never an error and never an invented consulate."""
    centres = [{"id": "us-sh", "name": "U.S. Consulate General Shanghai",
                "city": "Shanghai", "address": "1038 Nanjing West Road"}]
    # No auth -> refused.
    r = client.post("/appointments/booking/nearest-centre",
                    json={"address": "Pudong, Shanghai", "centres": centres})
    assert r.status_code in (401, 403)
    # Authenticated: either Kimi answered with an id FROM THE LIST, or the
    # endpoint honestly says available:false. Nothing else is possible.
    r = client.post("/appointments/booking/nearest-centre", headers=AUTH,
                    json={"address": "Pudong, Shanghai", "centres": centres})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["available"]:
        assert body["centre_id"] == "us-sh"
    else:
        assert body["reason"]
    # Empty inputs are refused the same honest way, not with a 4xx.
    r = client.post("/appointments/booking/nearest-centre", headers=AUTH,
                    json={"address": "", "centres": []})
    assert r.status_code == 200
    assert r.json()["available"] is False
