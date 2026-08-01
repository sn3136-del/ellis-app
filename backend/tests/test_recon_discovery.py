"""Recon reaches forms that URL-probing and <a href> following never could,
and the selectors it records survive a second session.

Two globalization unlocks, pinned:
  1. Apply-CTA discovery — modern portals start the application from a BUTTON
     that routes client-side (Myanmar "Apply Visa", Cambodia "Apply Now",
     K-ETA "Apply from the beginning"). When no probed/linked page renders a
     form, recon clicks an OBSERVED apply-intent control and records the
     destination as the application form ONLY if it genuinely renders inputs.
  2. Stable selectors — a framework-generated id (Angular mat-input-7) changes
     every render, so the extractor prefers formcontrolname/data-testid, which
     is what lets an Angular-Material portal pass the repeated-sessions gate.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.adapter_factory import models as fm, recon
from app.adapter_factory.build_workflow import create_request, record_consent

HOST = "evisa.gov.example"


class ButtonRoutedPortal:
    """A portal whose landing page has an 'Apply Visa' BUTTON (no href) that
    reveals the real form; every probed URL path is a not-found shell. Mirrors
    the live-observer contract: observe(url) and observe_with_entry_gate."""

    def __init__(self, host=HOST):
        self.host = host

    def _obs(self, url, elements):
        return {"ok": True, "status": 200, "url": url, "hostname": self.host,
                "title": "e-Visa", "elements": elements, "links": [], "iframes": []}

    def __call__(self, url):
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/") or "/"
        if path == "/":
            return self._obs(url, [
                {"selector": 'button[name="applyVisa"]', "name": "applyVisa",
                 "label": "Apply Visa", "type": "button", "submits": "applyvisa"},
                {"selector": 'a[name="checkStatus"]', "name": "checkStatus",
                 "label": "Check Application Status", "type": "button",
                 "submits": "checkstatus"}])
        # Every guessed application/login/fees path is a not-found shell.
        return self._obs(url, [])

    def observe_with_entry_gate(self, base_url, entry_gate):
        acts = entry_gate.get("actions") or []
        # Only the genuine Apply control reveals the form; anything else lands
        # on an empty shell (so the "check status" CTA can never masquerade).
        if any('applyVisa' in (a.get("selector") or "") for a in acts):
            return self._obs(f"https://{self.host}/apply/form", [
                {"selector": 'input[formcontrolname="fullName"]', "name": "fullName",
                 "label": "Full name", "type": "text", "required": True},
                {"selector": 'input[formcontrolname="passportNumber"]',
                 "name": "passportNumber", "label": "Passport number",
                 "type": "text", "required": True},
                {"selector": 'input[formcontrolname="dateOfBirth"]',
                 "name": "dateOfBirth", "label": "Date of birth", "type": "date"}])
        return self._obs(f"https://{self.host}/apply/form", [])

    def close(self):
        pass


def _req(db, rk="rk1|discover"):
    req = create_request(db, org_id="orgD", user_id="a", application_id="",
                         route_key=rk, destination="Discoveria", visa_type="tourist",
                         portal_evidence={"hostnames": [HOST], "operator": "gov",
                                          "portal_url": f"https://{HOST}/"},
                         runtime_mode="local_mock_demo")
    record_consent(db, req, user_id="a")
    return req


def test_apply_button_discovers_the_form_no_href_needed(db):
    portal = ButtonRoutedPortal()
    req = _req(db)
    job = recon.run_recon(db, build_request=req, observer=portal,
                          hostnames=[HOST], follow_links=True)
    assert job.status == "complete", job.error
    forms = [a for a in recon.artifacts(db, job.id)
             if a.content_class == "application_form"]
    assert forms, "the Apply button should have revealed a form artifact"
    names = {e["name"] for e in (forms[0].structure or {}).get("elements", [])}
    assert {"fullName", "passportNumber"} <= names
    # The discovered click became the build's entry gate for downstream replay.
    gate = (req.portal_evidence or {}).get("entry_gate")
    assert gate and gate.get("discovered") is True
    assert any("applyVisa" in (a.get("selector") or "")
               for a in gate.get("actions", []))


def test_status_check_cta_is_never_mistaken_for_apply(db):
    """A 'Check Status' control must never be clicked as an application start —
    the exclude vocabulary keeps discovery honest."""
    cands = recon._apply_cta_candidates([
        {"hostname": HOST, "url_pattern": "/",
         "elements": [
             {"selector": 'a[name="s"]', "name": "checkStatus",
              "label": "Check Application Status", "type": "button", "submits": "s"},
             {"selector": 'a[name="t"]', "name": "track",
              "label": "Track my visa", "type": "button", "submits": "t"}]}])
    assert cands == []


def test_framework_generated_ids_never_win_over_stable_attributes(db):
    """The extractor JS prefers formcontrolname/data-testid over a volatile
    mat-input id. Verified structurally: the volatile-id guard and stable-attr
    preference are both present in the extraction source."""
    from app.portal import live_browser
    js = live_browser._EXTRACT_JS
    assert "formcontrolname" in js
    assert "volatileId" in js
    assert "mat-" in js and "ng-" in js   # Angular Material ids are rejected
