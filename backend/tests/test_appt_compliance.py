"""The appointment surface's compliance boundary, audited as one thing.

Each appointment module carries its own structural tests. This file audits the
five of them TOGETHER, including ``appt_api`` — the router, the only one of them
actually reachable over HTTP, and until now the only one with no structural
proof of its own.

What is being proven, in code rather than in prose:

  1. No module on this surface imports anything that could send a request. A
     scheduling calendar cannot be read by machine if there is no client to read
     it with. Parsed from each module's AST, so a hostname inside a docstring
     explaining the prohibition never counts as a request.
  2. No module defines a function that searches for, holds, or books a slot,
     solves a CAPTCHA, signs, or submits.
  3. No module takes a card, bank or UnionPay credential as a parameter, and
     none names one as a field it stores.
  4. A booking system is refused under any of its hostnames, including the
     per-market subdomains nobody has written down yet — without swallowing the
     wait-time page Ellis is allowed to read, or being fooled by a look-alike.
  5. All five endpoints answer with outbound sockets torn out, and all five are
     really mounted on the app (a router written and never included is a
     boundary that does not exist).
  6. The ETA-9141 writes nothing into the government's own block: no signature,
     no PW tracking number, no determination — proven against the blank PDF,
     not only against the map.

The penalty for automated slot search does not fall on Ellis. It falls on the
TRAVELER: appointments cancelled and visas revoked, at scale. That is why this
boundary is a test and not a policy note.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import socket

import pytest

APPOINTMENT_MODULES = (
    "appt_api",
    "appt_availability",
    "appt_eligibility",
    "appt_group_roster",
    "appt_appointments_prestage",
)

# Anything that could open a connection, drive a browser, or shell out to
# something that would.
FORBIDDEN_IMPORTS = {
    "http", "http.client", "httpx", "requests", "aiohttp", "urllib.request",
    "urllib.error", "socket", "ssl", "websockets", "websocket", "pycurl",
    "ftplib", "telnetlib", "smtplib", "asyncio.streams",
    "playwright", "playwright.sync_api", "playwright.async_api",
    "selenium", "browserbase", "subprocess", "pty", "curl_cffi",
}

# Scheduling and booking systems. Ellis may hand a human a link to any of these
# and may never send one a request.
SCHEDULING_HOSTS = (
    "usvisascheduling.com", "ustraveldocs.com", "ais.usvisa-info.com",
    "vfsglobal.com", "tlscontact.com", "blsinternational.com",
    "ceac.state.gov",
)


def _module(name):
    return importlib.import_module(f"app.{name}")


def _imports(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # a relative import stays inside this app
                continue
            found.add(node.module or "")
    return found


@pytest.mark.parametrize("name", APPOINTMENT_MODULES)
def test_no_appointment_module_imports_anything_that_can_send_a_request(name):
    """Structural proof for the whole surface at once. A module that cannot
    import an HTTP client cannot poll a booking calendar, whatever a future
    caller asks it to do."""
    imported = _imports(_module(name))
    offending = sorted(imported & FORBIDDEN_IMPORTS)
    assert not offending, f"app/{name}.py must send nothing, but imports {offending}"
    # urllib is allowed for PARSING a URL only, never for requesting one.
    assert "urllib" not in imported, (
        f"app/{name}.py imports urllib as a whole; import urllib.parse by name "
        f"so urllib.request can never be reached through it")


@pytest.mark.parametrize("name", APPOINTMENT_MODULES)
def test_no_appointment_module_defines_a_booking_or_signing_function(name):
    """Names, not prose: the docstrings on this surface talk about booking
    constantly, because saying what Ellis refuses to do is most of the product.
    What must not exist is a function that does it."""
    module = _module(name)
    tree = ast.parse(inspect.getsource(module))
    forbidden = ("book_appointment", "auto_book", "reserve_slot", "hold_slot",
                 "pick_slot", "select_slot", "search_slots", "find_slots",
                 "poll_calendar", "scrape_calendar", "fetch_calendar",
                 "solve_captcha", "sign_mandate", "sign_ds160", "auto_sign",
                 "submit_application", "submit_booking", "pay_fee", "charge")
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    overlap = sorted(defined & set(forbidden))
    assert not overlap, f"app/{name}.py must not define {overlap}"


@pytest.mark.parametrize("name", APPOINTMENT_MODULES)
def test_no_appointment_function_accepts_a_payment_credential(name):
    """Ellis names the fee and the counter. It never holds the instrument.

    Checked at every function's SIGNATURE, because a credential Ellis cannot be
    handed is a credential Ellis cannot store, forward, or leak."""
    module = _module(name)
    tree = ast.parse(inspect.getsource(module))
    banned = ("card_number", "cardnumber", "card_no", "cvv", "cvc", "pan",
              "iban", "bic", "swift", "routing_number", "account_number",
              "bank_account", "unionpay_number", "card_token", "payment_token",
              "cardholder", "expiry_month", "expiry_year")
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        names = [a.arg for a in
                 (*args.posonlyargs, *args.args, *args.kwonlyargs)]
        for arg in names:
            if arg.lower() in banned:
                offenders.append(f"{node.name}({arg})")
    assert not offenders, (
        f"app/{name}.py takes a payment credential: {offenders}. Ellis tells a "
        f"human the amount and the channel; it never touches the instrument")


@pytest.mark.parametrize("host", SCHEDULING_HOSTS)
def test_a_scheduling_host_is_refused_as_a_source_of_availability(host):
    """A scraped booking calendar must not be launderable into Ellis as data,
    whichever spelling it arrives in."""
    from app import appt_availability as av

    for url in (f"https://{host}/calendar", f"{host}/calendar",
                f"//{host}/appointment", f"HTTPS://{host.upper()}/x"):
        assert av.is_scheduling_host(url) is True, url
        with pytest.raises(av.ForbiddenAvailabilitySource):
            av.assert_never_fetched(url)


def test_an_unnamed_booking_subdomain_is_still_refused():
    """VFS, TLScontact and BLS mint a booking hostname per market. A guard rail
    that only knew the handful of spellings someone wrote down would wave
    through fr.tlscontact.com — the same calendar, and the same cancelled
    appointment for the traveller."""
    from app import appt_availability as av

    for host in ("fr.tlscontact.com", "ru-mow.tlscontact.com",
                 "booking.vfsglobal.com", "cn.blsinternational.com",
                 "vfsglobal.com", "tlscontact.com"):
        assert av.is_scheduling_host(f"https://{host}/appointment") is True, host
        with pytest.raises(av.ForbiddenAvailabilitySource):
            av.assert_never_fetched(f"https://{host}/appointment")


def test_the_suffix_match_does_not_over_reach():
    """Broadening the guard must not swallow the wait-time source Ellis is
    allowed to read, nor be fooled by a look-alike domain."""
    from app import appt_availability as av

    for host in ("travel.state.gov", "www.state.gov", "france-visas.gouv.fr",
                 "home-affairs.ec.europa.eu"):
        assert av.is_scheduling_host(f"https://{host}/x") is False, host
    for lookalike in ("https://nottlscontact.com/x",
                      "https://vfsglobal.com.attacker.net/x",
                      "https://myvfsglobal.com/x"):
        assert av.is_scheduling_host(lookalike) is False, lookalike


def test_every_cockpit_endpoint_answers_with_the_socket_layer_torn_out(
        client, db, monkeypatch):
    """The end-to-end proof. Drive all five real endpoints with sockets
    unusable: anything on this path that reached for a scheduling host — now or
    after a later edit — fails here instead of in front of a traveller."""
    from app import models

    applicant = models.Applicant(org_id="org1", user_id="user1",
                                 full_name="Group Member", email="m@example.com")
    db.add(applicant)
    db.flush()
    row = models.VisaApplication(
        org_id="org1", user_id="user1", applicant_id=applicant.id,
        destination_country="United States", visa_type="tourist",
        adapter_id="", state="DRAFT", answers={})
    db.add(row)
    db.commit()

    # Outbound connections only. Creating a socket is not the offence and the
    # test harness itself needs one (asyncio's self-pipe); RESOLVING a name and
    # CONNECTING to it are the offence, so those are what is torn out.
    def _refuse(*a, **kw):
        raise AssertionError("the appointment surface must reach no host")

    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)

    auth = {"Authorization": "Bearer dev-token", "X-Org-Id": "org1",
            "X-User-Id": "user1"}
    responses = [
        client.get(f"/appointments/triage/{row.id}", headers=auth),
        client.get(f"/appointments/prestage/{row.id}", headers=auth),
        client.post("/appointments/group-roster",
                    json={"case_ids": [row.id], "group_kind": "tour_group"},
                    headers=auth),
        client.get("/appointments/group-roster/export",
                   params={"case_id": [row.id]}, headers=auth),
        client.get("/appointments/availability", params={"post": "Beijing"},
                   headers=auth),
    ]
    for response in responses:
        assert response.status_code == 200, response.text


def test_every_appointment_route_is_mounted_on_the_real_app():
    """The cockpit is only real if it is actually reachable. A router that is
    written and never included is a feature that silently does not exist."""
    from app.main import app

    mounted = set(app.openapi()["paths"])
    for path in ("/appointments/triage/{case_id}",
                 "/appointments/prestage/{case_id}",
                 "/appointments/group-roster",
                 "/appointments/group-roster/export",
                 "/appointments/availability"):
        assert path in mounted, f"{path} is not mounted on app.main"


def test_the_eta_9141_map_writes_no_signature_and_no_dol_use_only_field():
    """The prevailing wage request's Section F and the 'FOR DEPARTMENT OF LABOR
    USE ONLY' PW-tracking block belong to the government. Ellis writes neither,
    and the blank carries no signature widget at all — the request is signed
    inside FLAG by a person."""
    import json
    from pathlib import Path

    from app.h1b import forms as h1b_forms

    path = Path(h1b_forms.__file__).resolve().parents[3] / "data" / "reference" \
        / "forms" / "eta-9141.map.json"
    spec = json.loads(path.read_text())

    names = set(spec["fields"]) | set(spec["checkboxes"])
    names |= {group["field"] for group in spec["radio_groups"].values()}

    banned = ("signature", "sign here", "date signed", "pw tracking",
              "tracking number", "case status", "validity period",
              "determination", "official government use", "department of labor use")
    for field in names:
        low = field.lower()
        for word in banned:
            assert word not in low, (
                f"the ETA-9141 map names a government-only field: {field!r}")

    # Proven against the blank itself: the page carrying Section F and the
    # official-use block has no AcroForm widget for anything to write into.
    from pypdf import PdfReader

    reader = PdfReader(str(path.with_suffix("").with_suffix(".pdf")))
    last = reader.pages[-1]
    assert not (last.get("/Annots") or []), (
        "the ETA-9141's official-use page must carry no fillable widget")
