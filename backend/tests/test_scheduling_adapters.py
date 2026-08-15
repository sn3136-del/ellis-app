"""US + Schengen appointment scheduling adapters.

These pin the two properties that matter: (1) the adapters are REAL and valid
(official domains, calendar selectors declared, CAPTCHA/bot-evasion prohibited);
and (2) they are honestly PRE-PRODUCTION — `tested`, not enabled — so the
fail-closed live-driver gate refuses to bind them until they are individually
production-approved. A `tested` adapter that a live driver would bind is the
exact fabrication this doctrine forbids.
"""
import os

import pytest

from app import config
from app.portal import contract
from app.portal.adapters.schengen_scheduling import build_schengen_scheduling_adapter
from app.portal.adapters.us_visa_scheduling import build_us_visa_scheduling_adapter
from app.portal.contract import validate_adapter


class _MockPortal:
    def search_appointments(self, **kw):
        return {"ok": True, "slots": []}


@pytest.fixture(autouse=True)
def _clean_registry():
    contract.clear_registry()
    yield
    contract.clear_registry()


def _both():
    p = _MockPortal()
    return (build_us_visa_scheduling_adapter(p),
            build_schengen_scheduling_adapter(p))


def test_both_adapters_validate_and_declare_a_calendar_read():
    for a in _both():
        assert validate_adapter(a) == [], a.adapter_id
        assert a.appointment_search == "automated"
        assert a.appointment_slot_selector, "must declare how to read the calendar"
        # Every official URL is on the allowlist (validate_adapter checks this,
        # but pin it explicitly for the scheduling hosts).
        for url in (a.login_url, a.appointment_url):
            host = url.split("/")[2]
            assert host in a.approved_domains


def test_captcha_and_bot_evasion_are_prohibited():
    for a in _both():
        for forbidden in ("solve_captcha", "bypass_bot_detection",
                          "spoof_fingerprint", "rotate_proxy"):
            assert forbidden in a.prohibited_actions, (a.adapter_id, forbidden)
        # ...and never accidentally allowed.
        assert not (set(a.allowed_actions) & set(a.prohibited_actions))


def test_adapters_are_pre_production_not_live():
    # The honest state: filled in and tested, but NOT enabled. A validator that
    # let production_enabled ride on a non-approved status would be the hole.
    for a in _both():
        assert a.production_approval_status == "tested"
        assert a.production_enabled is False


def test_official_scheduling_hosts_are_the_real_ones():
    us, sch = _both()
    assert us.destination_country == "United States" and us.visa_type == "b1b2"
    assert "ais.usvisa-info.com" in us.approved_domains
    assert sch.visa_type == "schengen"
    assert "vfsglobal.com" in sch.approved_domains


def test_live_driver_refuses_to_bind_a_tested_scheduling_adapter():
    # The activation gate: even though the adapter now EXISTS and is selectable,
    # the live driver's fail-closed constructor refuses it because it is not
    # production_approved+enabled. This is what keeps 'tested' from ever
    # reading a real traveller's calendar.
    from app.portal.driver_factory import RealOnlyStop
    from app.portal.live_driver import BrowserbaseLiveViewDriver
    os.environ["ELLIS_RUNTIME_MODE"] = "production"
    config.settings.cache_clear()
    try:
        for a in _both():
            with pytest.raises(RealOnlyStop):
                BrowserbaseLiveViewDriver(a, page=object(),
                                          session={"id": "s"},
                                          require_real_key=False)
    finally:
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
        config.settings.cache_clear()


def test_the_us_adapters_selectors_actually_read_a_calendar_dom():
    # The adapter's DECLARED selectors, run through the real calendar-read code
    # against a fake DOM, extract dates — proving the selectors are wired
    # correctly (the selector VALUES are confirmed live at activation). The
    # production flags are flipped LOCALLY here only to exercise the read path;
    # the shipped adapter stays tested/disabled.
    from tests.test_live_driver import _CalendarPage, _FakeNode, _driver
    us, _ = _both()
    us.production_approval_status = "production_approved"
    us.production_enabled = True
    page = _CalendarPage(nodes=[_FakeNode(text="2026-11-03"),
                                _FakeNode(text="2026-11-10")])
    os.environ["ELLIS_RUNTIME_MODE"] = "production"
    config.settings.cache_clear()
    try:
        d = _driver(adapter=us, page=page)
        out = d.search_appointments(post="Beijing")
    finally:
        os.environ.pop("ELLIS_RUNTIME_MODE", None)
        config.settings.cache_clear()
    # US adapter: the whole cell is the date; no post field -> default_post.
    assert out["ok"] is True
    assert [s["when"] for s in out["slots"]] == ["2026-11-03", "2026-11-10"]
    assert all(s["post"] == "Beijing" for s in out["slots"])
    assert page.visited == ["https://ais.usvisa-info.com/en-us/niv/schedule"]
