"""Ellis must never report an outcome it did not truly witness on the portal.
Regressions from the 2026-08-01 'nothing simulated or fake' audit:

  1. pay() fabricated a receipt by regex-scraping page body text, so a page
     reading 'Transaction: X — declined' was reported as a captured payment.
  2. submit_captcha treated ANY dialog with a Confirm button as acceptance, so
     an 'Invalid security code' error dialog advanced the flow and got scraped
     for a reference.
  3. the case display guard passed adapter_verified=True unconditionally, so a
     COMPLETED case on a released route showed is_real_government_result=True
     with no live evidence at all.
"""
from __future__ import annotations

import pytest

from app.portal.released_flow import ReleasedFlowDriver


class _FakePage:
    """Minimal driver stand-in exposing only what the methods under test call.
    body_text is what the live page would show; notice_present toggles a
    blocking dialog."""
    def __init__(self, body_text="", notice_present=False, manifest=None):
        self.body_text = body_text
        self.notice_present = notice_present
        self.confirmed = False
        self.captcha_applied = False
    def read_text(self, selector):
        return {"ok": True, "text": self.body_text}
    def notice_state(self):
        return {"ok": True, "present": self.notice_present}
    def notice_text(self):
        return {"ok": True, "text": self.body_text}
    def confirm_notice(self):
        self.confirmed = True
        return {"ok": True}
    def settle(self, *_a):
        pass
    def apply_captcha_answer(self, ans):
        self.captcha_applied = True
        return {"ok": True}
    def click_next_button(self):
        return {"ok": True}
    def captcha_state(self, *_a):
        return {"ok": True, "present": False}


def _driver_with(page, manifest=None):
    """A ReleasedFlowDriver with its live driver and manifest stubbed, without
    running __init__ (which needs a full case + binding)."""
    d = ReleasedFlowDriver.__new__(ReleasedFlowDriver)
    d._live = page
    d._ensure_live = lambda: page
    class _V: pass
    v = _V(); v.manifest = manifest or {}
    class _R: pass
    r = _R(); r.version_row = v
    d.released = r
    return d


# --- 1. payment receipts are never scraped from page text ------------------

def test_pay_never_reads_a_receipt_from_page_body_text():
    """_read_extract(declared_only=True) refuses the body-text fallback — a
    scraped 'transaction' token can't distinguish paid from declined."""
    page = _FakePage(body_text="Transaction: 20260801VN12345 — declined")
    d = _driver_with(page, manifest={})   # no declared receipt selector
    # declared_only path: no selector, so no scrape, returns None
    assert d._read_extract("receipt_extraction", declared_only=True) is None
    # the permissive path would have scraped it (proving the text DOES match) —
    # which is exactly why pay() must use declared_only
    assert d._read_extract("receipt_extraction") == "20260801VN12345"


def test_pay_reports_a_receipt_only_from_a_declared_selector():
    page = _FakePage(body_text="Receipt No: RCPT-9988776")
    d = _driver_with(page, manifest={"receipt_extraction": "#receipt"})
    assert d._read_extract("receipt_extraction", declared_only=True) == "Receipt No: RCPT-9988776"


# --- 2. an error dialog is never read as CAPTCHA acceptance -----------------

@pytest.mark.parametrize("err", [
    "Invalid security code. Please try again.",
    "Mã không đúng",                 # vi: code is wrong
    "Code de sécurité incorrect",    # fr
    "Ungültiger Sicherheitscode",    # de
    "Неверный код",                  # ru
])
def test_error_notice_is_not_treated_as_acceptance(err):
    page = _FakePage(body_text=err, notice_present=True)
    d = _driver_with(page)
    assert d._notice_is_error(page) is True


def test_success_registration_notice_is_not_flagged_as_error():
    page = _FakePage(body_text="Registration code: E-VISA-2026-556677. Please note it.",
                     notice_present=True)
    d = _driver_with(page)
    assert d._notice_is_error(page) is False


def test_unreadable_notice_fails_safe_as_error():
    """If the notice text can't be read, it is treated as an error — never
    scraped or confirmed as a success."""
    page = _FakePage(notice_present=True)
    page.notice_text = lambda: {"ok": False}
    d = _driver_with(page)
    assert d._notice_is_error(page) is True


# --- 3. the display guard requires real evidence ---------------------------

def _completed_case(db, org="orgV"):
    from app import models
    applicant = models.Applicant(org_id=org, user_id="u1", full_name="T A",
                                 email="t@example.com")
    db.add(applicant); db.flush()
    app_row = models.VisaApplication(
        org_id=org, user_id="u1", applicant_id=applicant.id,
        destination_country="Vietnam", visa_type="tourist", adapter_id="",
        state="COMPLETED", answers={})
    db.add(app_row); db.commit()
    return app_row


def test_completed_case_without_live_evidence_is_not_a_real_government_result(db):
    """A COMPLETED case whose route resolves to a released adapter but that has
    no completed execution with government submission evidence must NOT display
    as a real government result."""
    from app.main import _adapter_verified_result
    app_row = _completed_case(db)
    assert _adapter_verified_result(db, app_row.id) is False


def test_real_government_result_requires_gov_domain_submission_evidence(db):
    """With a completed execution carrying government-domain submission
    evidence, the guard returns True — a non-government host never counts."""
    from app.main import _adapter_verified_result
    from app.adapter_factory import models as fm
    app_row = _completed_case(db)
    ex = fm.AdapterExecution(org_id="orgV", application_id=app_row.id,
                             candidate_id="c"*32, candidate_version=1,
                             status="completed")
    db.add(ex); db.commit()
    db.add(fm.AdapterOutcomeEvidence(execution_id=ex.id, kind="network",
                                     state_category="submission_accepted",
                                     hostname="evisa.gov.vn"))
    db.commit()
    assert _adapter_verified_result(db, app_row.id) is True

    only_bad = _completed_case(db)
    ex3 = fm.AdapterExecution(org_id="orgV", application_id=only_bad.id,
                              candidate_id="e"*32, candidate_version=1, status="completed")
    db.add(ex3); db.commit()
    db.add(fm.AdapterOutcomeEvidence(execution_id=ex3.id, kind="network",
                                     state_category="submission_accepted",
                                     hostname="tracker.example.com"))
    db.commit()
    assert _adapter_verified_result(db, only_bad.id) is False


# --- 6. the notice is judged by ITSELF, never by the page behind it ---------

# eVisa's own entry conditions, verbatim from evisa.gov.vn. The Spanish error
# stem `fall` used to match inside "falling", so the whole-page fallback read
# this as a rejection and told applicants their correct CAPTCHA code "wasn't
# accepted" (2026-08-03).
_EVISA_PAGE = ("Foreigners with valid international travel document. Not "
               "falling under the cases of suspension from entry.")
_EVISA_NOTICE = ("DECLARATION COMPLETED\nElectronic document code: "
                 "E260803CHNE9936145772\nNotice: You have to note e-Visa app "
                 "no. for check status of this file")


def test_success_notice_survives_error_words_on_the_page_behind_it():
    page = _FakePage(body_text=_EVISA_PAGE, notice_present=True)
    page.notice_text = lambda: {"ok": True, "text": _EVISA_NOTICE}
    assert _driver_with(page)._notice_is_error(page) is False


def test_page_body_is_never_the_notice_reader():
    """Without a notice-scoped reader the answer is 'error', not a guess made
    from the whole page — that guess is what broke eVisa."""
    class _NoNoticeText(_FakePage):
        notice_text = None
    page = _NoNoticeText(body_text=_EVISA_PAGE, notice_present=True)
    assert _driver_with(page)._notice_is_error(page) is True


@pytest.mark.parametrize("text,is_error", [
    ("Not falling under the cases of suspension from entry", False),  # en
    ("La verificación falló. Intente de nuevo.", True),               # es
    ("Se ha producido una falla en el envío", True),                  # es
    ("Your application has been submitted successfully", False),
    ("Nhập sai mã bảo mật", True),                                    # vi
    ("Hồ sơ đã hoàn thành", False),                                   # vi
])
def test_error_vocabulary_is_word_anchored(text, is_error):
    page = _FakePage(notice_present=True)
    page.notice_text = lambda: {"ok": True, "text": text}
    assert _driver_with(page)._notice_is_error(page) is is_error


def test_a_success_marker_outranks_a_cautionary_word_in_the_same_notice():
    """Portals print conditions inside their success dialogs; one stray
    'invalid' must not reclassify a completed declaration."""
    page = _FakePage(notice_present=True)
    page.notice_text = lambda: {"ok": True, "text":
        "DECLARATION COMPLETED. An invalid passport will void this e-visa."}
    assert _driver_with(page)._notice_is_error(page) is False


# --- 7. Ellis presses a blocking notice itself ------------------------------

def _notice_page(text, sigs):
    page = _FakePage(body_text=_EVISA_PAGE, notice_present=True)
    page.notice_text = lambda: {"ok": True, "text": text}
    page._sigs = list(sigs)
    page.page_signature = lambda: page._sigs[min(len(page._sigs) - 1,
                                                 int(page.confirmed))]
    return page


def test_blocking_notice_is_confirmed_by_ellis_and_reports_movement():
    page = _notice_page(_EVISA_NOTICE, ["form-page", "payment-page"])
    d = _driver_with(page)
    d._capture_registration_notice = lambda _drv: None
    assert d._confirm_blocking_notice() is True
    assert page.confirmed is True


def test_a_notice_that_does_not_move_the_page_is_not_progress():
    """A pressed Confirm that leaves the page identical is not movement —
    reporting it as progress would spin the loop."""
    page = _notice_page(_EVISA_NOTICE, ["same-page"])
    d = _driver_with(page)
    d._capture_registration_notice = lambda _drv: None
    assert d._confirm_blocking_notice() is False


def test_an_error_notice_is_never_pressed_past():
    page = _notice_page("Invalid security code. Please try again.",
                        ["form-page", "form-page"])
    d = _driver_with(page)
    d._capture_registration_notice = lambda _drv: None
    assert d._confirm_blocking_notice() is False
    assert page.confirmed is False


def test_no_notice_on_screen_is_not_a_confirm():
    page = _FakePage(body_text=_EVISA_PAGE, notice_present=False)
    assert _driver_with(page)._confirm_blocking_notice() is False


# --- 8. a challenge behind a modal is not a challenge ----------------------

def test_captcha_probe_ignores_widgets_covered_by_an_overlay():
    """A modal leaves the form (CAPTCHA included) laid out and visible
    underneath, so size and offsetParent both still pass. Only a hit test
    tells the truth — without it eVisa's notice read as a live CAPTCHA."""
    from app.adapter_factory.live_driver import BrowserbasePageDriver as _D
    js = _D._CAPTCHA_JS
    assert "elementFromPoint" in js
    assert "reachable" in js
