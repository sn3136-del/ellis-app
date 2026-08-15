"""BrowserbaseLiveViewDriver safety invariants (brief §19-25).

None of these tests touch a real portal or a real Browserbase session — a fake
page + injected state probe exercise the logic deterministically.
"""
import os
import types

import pytest

from app import config


class _FakeAdapter:
    adapter_id = "vietnam-evisa-tourist-v1"
    approved_domains = ["evisa.gov.vn", "thithucdientu.gov.vn"]
    registration_url = "https://evisa.gov.vn/en_US/dang-ky-tai-khoan"
    login_url = "https://evisa.gov.vn/en_US/dang-nhap"
    application_url = "https://evisa.gov.vn/en_US/thong-tin-ho-so"
    registration_mappings = [{"field": "email", "selector": "#email"},
                             {"field": "password", "selector": "#password", "sensitive": True}]
    application_mappings = [{"field": "full_name", "selector": "#fullName"},
                            {"field": "passport_number", "selector": "#passportNumber"}]
    application_id_selector = "#dossierNo"
    fee_selector = "#feeAmount"
    confirmation_extraction = "#registrationCode"
    receipt_extraction = "#receiptNo"
    appointment_search = "none"

    def __init__(self, approved=True, enabled=True):
        self.production_approval_status = "production_approved" if approved else "tested"
        self.production_enabled = enabled


class _FakePage:
    """Records fills/navigations; serves inner_text from a dict."""
    def __init__(self, texts=None):
        self.texts = texts or {}
        self.filled = {}
        self.visited = []

    def goto(self, url):
        self.visited.append(url)

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector):
        pass

    def inner_text(self, selector):
        if selector in self.texts:
            return self.texts[selector]
        raise RuntimeError("no such node")

    @property
    def url(self):
        return self.visited[-1] if self.visited else ""


@pytest.fixture()
def real_only_mode():
    saved = os.environ.get("ELLIS_RUNTIME_MODE")
    os.environ["ELLIS_RUNTIME_MODE"] = "production"
    config.settings.cache_clear()
    yield
    if saved is None:
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
    else:
        os.environ["ELLIS_RUNTIME_MODE"] = saved
    config.settings.cache_clear()


def _driver(adapter=None, page=None, state_probe=None, session=None):
    from app.portal.live_driver import BrowserbaseLiveViewDriver
    return BrowserbaseLiveViewDriver(
        adapter or _FakeAdapter(), page=page or _FakePage(),
        state_probe=state_probe, session=session or {"id": "sess-test", "mode": "test",
                                                     "connect_url": None},
        require_real_key=False)


def test_fail_closed_outside_real_only_mode():
    from app.portal.live_driver import BrowserbaseLiveViewDriver
    from app.portal.driver_factory import RealOnlyStop
    os.environ["ELLIS_RUNTIME_MODE"] = "local_mock_demo"
    config.settings.cache_clear()
    try:
        with pytest.raises(RealOnlyStop):
            BrowserbaseLiveViewDriver(_FakeAdapter(), page=_FakePage(),
                                      session={"id": "x"}, require_real_key=False)
    finally:
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
        config.settings.cache_clear()


def test_fail_closed_when_adapter_not_production_approved(real_only_mode):
    from app.portal.live_driver import BrowserbaseLiveViewDriver
    from app.portal.driver_factory import RealOnlyStop
    with pytest.raises(RealOnlyStop):
        BrowserbaseLiveViewDriver(_FakeAdapter(approved=False), page=_FakePage(),
                                  session={"id": "x"}, require_real_key=False)
    with pytest.raises(RealOnlyStop):
        BrowserbaseLiveViewDriver(_FakeAdapter(enabled=False), page=_FakePage(),
                                  session={"id": "x"}, require_real_key=False)


# ---- Calendar read: deterministic, adapter-declared, read-only -------------

class _FakeNode:
    """A calendar slot element: sub-selector reads come from `parts`, and the
    node's OWN inner_text is a realistic full-cell blob so a regression that
    leaks the whole node into an undeclared field is caught, not hidden."""
    def __init__(self, parts=None, text=None):
        self.parts = parts or {}
        # Default whole-cell text = a blob built from the parts, mimicking a
        # real calendar cell ("2026-10-12 09:30 Beijing morning APPLY").
        self._text = text if text is not None else \
            " ".join(str(v) for v in self.parts.values() if v) + " APPLY"

    def query_selector(self, sel):
        v = self.parts.get(sel)
        return _FakeNode(parts={}, text=v) if v is not None else None

    def inner_text(self):
        return self._text


class _CalendarPage(_FakePage):
    def __init__(self, nodes=None, **kw):
        super().__init__(**kw)
        self.nodes = nodes or []
        self.queried = []

    def query_selector_all(self, sel):
        self.queried.append(sel)
        return self.nodes


class _CalendarAdapter(_FakeAdapter):
    appointment_search = "automated"
    appointment_url = "https://evisa.gov.vn/en_US/lich-hen"
    appointment_slot_selector = ".slot.available"
    appointment_date_field = ".date"
    appointment_time_field = ".time"
    appointment_label_field = ".kind"
    appointment_post_field = ".post"


def test_reads_open_slots_from_the_official_calendar(real_only_mode):
    page = _CalendarPage(nodes=[
        _FakeNode({".date": "2026-10-12", ".time": "09:30",
                   ".kind": "morning", ".post": "Beijing"}),
        _FakeNode({".date": "2026-10-19", ".time": "14:00"}),
        _FakeNode({".time": ""}),          # nothing readable -> skipped
    ])
    d = _driver(adapter=_CalendarAdapter(), page=page)
    out = d.search_appointments(post="Shanghai")
    assert out["ok"] is True
    assert out["slots"] == [
        {"post": "Beijing", "when": "2026-10-12 09:30", "label": "morning"},
        # No post on the node -> the applicant's preferred post labels it.
        {"post": "Shanghai", "when": "2026-10-19 14:00", "label": ""},
    ]
    # It navigated the APPROVED appointment page and queried the declared
    # slot selector — no other host, no invented selector.
    assert page.visited == ["https://evisa.gov.vn/en_US/lich-hen"]
    assert page.queried == [".slot.available"]


def test_undeclared_fields_never_leak_the_whole_cell(real_only_mode):
    # A date-only calendar (no time field) and no post field declared: the
    # undeclared fields must read as "" (post falls back to default_post),
    # NEVER the whole cell blob.
    class _DateOnly(_CalendarAdapter):
        appointment_time_field = ""
        appointment_label_field = ""
        appointment_post_field = ""
    page = _CalendarPage(nodes=[_FakeNode({".date": "2026-10-12"})])
    d = _driver(adapter=_DateOnly(), page=page)
    out = d.search_appointments(post="Shanghai")
    assert out["slots"] == [
        {"post": "Shanghai", "when": "2026-10-12", "label": ""}]
    # Explicitly: the garbled duplicate ("2026-10-12 ... APPLY") never appears.
    assert "APPLY" not in out["slots"][0]["when"]
    assert out["slots"][0]["post"] == "Shanghai"   # default_post won, not blob


def test_time_only_calendar_reads_just_the_time(real_only_mode):
    class _TimeOnly(_CalendarAdapter):
        appointment_date_field = ""
        appointment_post_field = ""
        appointment_label_field = ""
    page = _CalendarPage(nodes=[_FakeNode({".time": "09:30"})])
    d = _driver(adapter=_TimeOnly(), page=page)
    out = d.search_appointments(post="Beijing")
    assert out["slots"] == [{"post": "Beijing", "when": "09:30", "label": ""}]


def test_calendar_read_is_none_without_declared_selectors(real_only_mode):
    class _NoSelectors(_CalendarAdapter):
        appointment_slot_selector = ""
    page = _CalendarPage(nodes=[_FakeNode({".date": "2026-10-12"})])
    d = _driver(adapter=_NoSelectors(), page=page)
    # Nothing declared -> nothing read. A calendar Ellis cannot parse yields
    # no slots, never a guess.
    assert d.search_appointments()["slots"] == []


def test_calendar_read_refuses_an_off_allowlist_appointment_url(real_only_mode):
    from app.portal.driver_factory import RealOnlyStop

    class _Offsite(_CalendarAdapter):
        appointment_url = "https://not-approved.example/cal"
    d = _driver(adapter=_Offsite(), page=_CalendarPage())
    with pytest.raises(RealOnlyStop):
        d.search_appointments()


def test_calendar_read_survives_a_broken_node(real_only_mode):
    class _Angry(_FakeNode):
        def query_selector(self, sel):
            raise RuntimeError("detached node")
    page = _CalendarPage(nodes=[_Angry(), _FakeNode({".date": "2026-11-02",
                                                     ".time": "10:00"})])
    d = _driver(adapter=_CalendarAdapter(), page=page)
    # One unreadable node never loses the rest of the calendar.
    assert d.search_appointments(post="Paris")["slots"] == [
        {"post": "Paris", "when": "2026-11-02 10:00", "label": ""}]


def test_evisa_route_reads_no_calendar_by_policy(real_only_mode):
    page = _CalendarPage(nodes=[_FakeNode({".date": "2026-10-12"})])
    d = _driver(adapter=_FakeAdapter(), page=page)   # appointment_search "none"
    assert d.search_appointments() == {"ok": True, "slots": []}
    assert page.queried == []


def test_declares_live_production_class(real_only_mode):
    d = _driver()
    from app import execution
    assert execution.classify_driver(d) == execution.ExecutionClass.LIVE_PRODUCTION


def test_never_captures_secrets(real_only_mode):
    from app.portal.live_driver import SecretCaptureRefused
    d = _driver()
    for bad in ({"password": "x"}, {"otp": "123456"}, {"card_number": "4111"},
                {"cvv": "123"}, {"three_ds_code": "z"}):
        with pytest.raises(SecretCaptureRefused):
            d.pay(session_token="t", application_id="a", **bad)
    with pytest.raises(SecretCaptureRefused):
        d.submit_captcha(captcha_answer="cat")


def test_sensitive_marked_fields_never_typed(real_only_mode):
    page = _FakePage()
    d = _driver(page=page)
    d.create_application(session_token="t", answers={"full_name": "A B", "passport_number": "X1",
                                                     "password": "should-not-type"})
    assert page.filled.get("#fullName") == "A B"
    assert page.filled.get("#passportNumber") == "X1"
    # password has no application mapping and is sensitive-marked in registration
    assert "#password" not in page.filled


def test_navigation_confined_to_allowlist(real_only_mode):
    from app.portal.driver_factory import RealOnlyStop
    adapter = _FakeAdapter()
    adapter.application_url = "https://evil.example/steal"
    d = _driver(adapter=adapter)
    with pytest.raises(RealOnlyStop):
        d.create_application(session_token="t", answers={})


def test_payment_success_only_from_official_evidence(real_only_mode):
    # No official 'paid' state -> OUTCOME_UNCERTAIN, never a fabricated success.
    d = _driver(state_probe=lambda t, a: {"paid": None})
    res = d.pay(session_token="t", application_id="a", payment_ref="ref")
    assert res["ok"] is False and res["status"] == "OUTCOME_UNCERTAIN"
    # Official paid state -> success with the portal's own receipt.
    d2 = _driver(state_probe=lambda t, a: {"paid": True, "receipt": "RCPT-9",
                                           "reference": "REG-9"})
    res2 = d2.pay(session_token="t", application_id="a", payment_ref="ref")
    assert res2["ok"] and res2["receipt"] == "RCPT-9"


def test_reconcile_prevents_duplicate_payment(real_only_mode):
    calls = {"n": 0}

    def probe(t, a):
        calls["n"] += 1
        return {"paid": True, "receipt": "RCPT-1", "reference": "REG-1"}
    d = _driver(state_probe=probe)
    res = d.pay(session_token="t", application_id="a", payment_ref="ref")
    assert res["reconciled"] is True and res["receipt"] == "RCPT-1"
    # It read state and short-circuited — it did not attempt a second charge.


def test_reconcile_prevents_duplicate_submission(real_only_mode):
    d = _driver(state_probe=lambda t, a: {"submitted": True, "reference": "APP-77"})
    res = d.submit(session_token="t", application_id="a")
    assert res["ok"] and res["reconciled"] is True and res["reference"] == "APP-77"


def test_submit_uncertain_when_no_evidence(real_only_mode):
    page = _FakePage(texts={})  # no confirmation node present
    d = _driver(page=page, state_probe=lambda t, a: {"submitted": None})
    res = d.submit(session_token="t", application_id="a")
    assert res["ok"] is False and res["status"] == "OUTCOME_UNCERTAIN"
    assert "live_view" in res


def test_submit_reads_official_confirmation(real_only_mode):
    page = _FakePage(texts={"#registrationCode": "VN-EVISA-2026-000123",
                            "#receiptNo": "PAY-555"})
    d = _driver(page=page, state_probe=lambda t, a: {"submitted": None})
    res = d.submit(session_token="t", application_id="a")
    assert res["ok"] and res["reference"] == "VN-EVISA-2026-000123"
    assert res["receipt"] == "PAY-555"


def test_personal_steps_hand_off_never_auto(real_only_mode):
    d = _driver()
    reg = d.register(email="a@b.com")
    assert reg["ok"] is False and reg["handoff"] == "registration_and_password"
    ver = d.verify_email()
    assert ver["status"].endswith("APPLICANT_ACTION_REQUIRED")
    dec = d.declare_personally(session_token="t", application_id="a")
    assert dec["ok"] is False  # not declared until the portal records it


def test_recovery_checks_state_before_retry(real_only_mode):
    """After a disconnect the workflow calls get_application_state; a submit that
    already succeeded is detected and not repeated."""
    d = _driver(state_probe=lambda t, a: {"submitted": True, "reference": "APP-RC"})
    state = d.get_application_state(session_token="t", application_id="a")
    assert state["submitted"] is True
    res = d.submit(session_token="t", application_id="a")
    assert res["reconciled"] is True
