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
