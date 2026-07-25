"""Document-intake flow regression suite: secure first-party preview,
requirement-specific uploads (bind → review → explicit Submit → Fulfilled),
mismatch/uncertainty handling, checklist completion, and the server-validated
Continue that advances the EXISTING case per route kind. No mocks of Ellis
behavior — everything runs against the real FastAPI app + DB; no real portal,
payment, or government submission ever occurs here."""
import base64
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app, _doc_sig
from app import models as core_models
from app.visa_snapshot import kimi_primary
from app.visa_snapshot.models import KimiRouteGuidanceCache

from .test_intake_flow import (H, ANSWERS_SGP, EXEMPT_ANSWER, REQUIRED_ANSWER,
                               ETA_ANSWER, _passport_text, _two_pass,
                               _resolve_with_guidance, _new_intake)

H2 = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-other", "X-User-Id": "u2"}

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4 fake body"
TIFF = b"II*\x00" + b"\x00" * 64


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client():
    return TestClient(fastapi_app)


@pytest.fixture(autouse=True)
def _reset(db):
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars().all():
        db.delete(row)
    db.commit()
    yield
    kimi_primary.set_provider(None)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _continue_case(client, answer, destination, *, confirm_passport=True):
    """Guidance → (optional profile confirmation) → continuation. Returns the
    case id of the EXISTING converted case."""
    iid, _ = _resolve_with_guidance(
        client, answer, dict(ANSWERS_SGP, destination_country=destination))
    if confirm_passport:
        profile = client.get(f"/intake/{iid}/passport", headers=H).json()
        client.put(f"/intake/{iid}", json={"answers": profile["prefill"]}, headers=H)
    r = client.post(f"/intake/{iid}/continue", headers=H)
    assert r.status_code == 200
    return r.json()["case_id"]


def _item(checklist_json, item_id):
    return next(i for i in checklist_json["checklist"] if i["id"] == item_id)


def _upload(client, case_id, item_id, name, *, text="", mime="application/pdf",
            content=None):
    body = {"name": name, "mime": mime, "size_bytes": 1024}
    if item_id:
        body["checklist_item_id"] = item_id
    if text:
        body["text"] = text
    if content is not None:
        body["content_b64"] = _b64(content)
        body["size_bytes"] = len(content)
    return client.post(f"/cases/{case_id}/documents", json=body, headers=H)


FLIGHT_TEXT = ("Flight itinerary\nAirline: Example Air\nRound-trip booking\n"
               "Departure: SIN 26 JUL · Return: 26 AUG\nPNR: ABC123")
HOTEL_TEXT = ("Hotel booking confirmation\nAccommodation: Example Hotel\n"
              "Guest: J DOE\nCheck-in 26 Jul · Check-out 26 Aug")
BANK_TEXT = "Bank statement\nAccount statement closing balance USD ####\n"
GENERIC_TEXT = "some words that describe nothing in particular about a trip"


# =========================================================================
# Part 1 — secure first-party document preview
# =========================================================================
def test_image_preview_serves_bytes_with_correct_mime(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "PER")
    up = _upload(client, case_id, None, "portrait.jpg", mime="image/jpeg",
                 content=JPEG)
    assert up.status_code == 200
    doc_id = up.json()["id"]
    u = client.get(f"/cases/{case_id}/documents/{doc_id}/url", headers=H).json()
    assert u["available"] is True and u["mime"] == "image/jpeg"
    assert 0 < u["expires_in"] <= 300          # short-lived
    assert "local://" not in str(u)            # never a raw storage path
    res = client.get(u["url"])                 # signature IS the authorization
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in res.headers.get("content-security-policy", "")
    assert res.content == JPEG


def test_pdf_preview_serves_application_pdf(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "COL")
    up = _upload(client, case_id, "flight_itinerary", "itinerary-flight.pdf",
                 content=PDF)
    doc_id = up.json()["id"]
    u = client.get(f"/cases/{case_id}/documents/{doc_id}/url", headers=H).json()
    assert u["available"] is True and u["mime"] == "application/pdf"
    res = client.get(u["url"])
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/pdf")


def test_preview_url_requires_authentication_and_ownership(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "ARG")
    up = _upload(client, case_id, None, "p.jpg", mime="image/jpeg", content=JPEG)
    doc_id = up.json()["id"]
    # No auth → refused.
    assert client.get(f"/cases/{case_id}/documents/{doc_id}/url").status_code in (401, 403)
    # Another tenant → refused (never another applicant's files).
    r2 = client.get(f"/cases/{case_id}/documents/{doc_id}/url", headers=H2)
    assert r2.status_code in (403, 404)


def test_preview_content_rejects_bad_and_expired_signatures(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "CHL")
    up = _upload(client, case_id, None, "p2.jpg", mime="image/jpeg", content=JPEG)
    doc_id = up.json()["id"]
    future = int(time.time()) + 60
    past = int(time.time()) - 60
    assert client.get(f"/documents/{doc_id}/content?exp={future}&sig=deadbeef").status_code == 401
    assert client.get(
        f"/documents/{doc_id}/content?exp={past}&sig={_doc_sig(doc_id, past)}").status_code == 401
    # A valid, unexpired signature works (refresh/reopen just mints a new one).
    ok = client.get(f"/documents/{doc_id}/content?exp={future}&sig={_doc_sig(doc_id, future)}")
    assert ok.status_code == 200


def test_unsupported_preview_format_still_serves_honest_bytes(client):
    """TIFF uploads are stored and served with their true MIME; the frontend
    shows the name/type/size fallback instead of a broken frame."""
    case_id = _continue_case(client, EXEMPT_ANSWER, "URY")
    up = _upload(client, case_id, None, "scan.tiff", mime="image/tiff", content=TIFF)
    doc_id = up.json()["id"]
    u = client.get(f"/cases/{case_id}/documents/{doc_id}/url", headers=H).json()
    assert u["available"] is True and u["mime"] == "image/tiff"
    res = client.get(u["url"])
    assert res.status_code == 200 and res.headers["content-type"].startswith("image/tiff")


def test_content_that_does_not_match_declared_type_is_refused(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "PRY")
    r = _upload(client, case_id, None, "evil.png", mime="image/png",
                content=b"<html><script>alert(1)</script></html>")
    assert r.status_code == 415


# =========================================================================
# Part 2 — requirement-specific uploads + explicit Submit
# =========================================================================
def test_every_needed_document_item_can_bind_and_submit(client):
    case_id = _continue_case(client, REQUIRED_ANSWER, "BOL")
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    # The intake-confirmed passport is already Fulfilled with provenance.
    assert _item(j, "passport")["status"] == "submitted"
    doc_items = [i for i in j["checklist"]
                 if i["kind"] == "document" and i["status"] == "pending"]
    assert {i["id"] for i in doc_items} >= {"photo", "flight_itinerary",
                                            "hotel_booking", "bank_statement"}
    uploads = {"photo": dict(name="me.jpg", mime="image/jpeg", content=JPEG),
               "flight_itinerary": dict(name="flight.pdf", text=FLIGHT_TEXT),
               "hotel_booking": dict(name="stay.pdf", text=HOTEL_TEXT),
               "bank_statement": dict(name="statement.pdf", text=BANK_TEXT)}
    for item_id, kw in uploads.items():
        up = _upload(client, case_id, item_id, **kw)
        assert up.status_code == 200, item_id
        assert up.json()["binding"]["match_verdict"] == "match", item_id
        j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
        assert _item(j, item_id)["status"] == "ready_to_submit", item_id
        s = client.post(f"/cases/{case_id}/checklist/{item_id}/submit",
                        json={"document_id": up.json()["id"]}, headers=H)
        assert s.status_code == 200 and s.json()["submitted"] is True, item_id
        j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
        assert _item(j, item_id)["status"] == "submitted", item_id
    assert j["checklist_counts"]["required_missing"] == 0


def test_flight_hotel_photo_recognition_is_deterministic(client):
    case_id = _continue_case(client, REQUIRED_ANSWER, "ECU")
    assert _upload(client, case_id, "flight_itinerary", "a.pdf",
                   text=FLIGHT_TEXT).json()["doc_type"] == "flight_itinerary"
    assert _upload(client, case_id, "hotel_booking", "b.pdf",
                   text=HOTEL_TEXT).json()["doc_type"] == "hotel_booking"
    assert _upload(client, case_id, "photo", "IMG_1.jpg", mime="image/jpeg",
                   content=JPEG).json()["doc_type"] == "photo"


def test_repeated_submit_is_idempotent(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "VEN")
    up = _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    doc_id = up.json()["id"]
    s1 = client.post(f"/cases/{case_id}/checklist/flight_itinerary/submit",
                     json={"document_id": doc_id}, headers=H)
    s2 = client.post(f"/cases/{case_id}/checklist/flight_itinerary/submit",
                     json={"document_id": doc_id}, headers=H)
    assert s1.json()["already_submitted"] is False
    assert s2.json()["already_submitted"] is True
    assert s1.json()["binding"]["submitted_at"] == s2.json()["binding"]["submitted_at"]


def test_replace_resets_submission_and_withdraw_returns_to_needed(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "MEX")
    up1 = _upload(client, case_id, "hotel_booking", "h1.pdf", text=HOTEL_TEXT)
    client.post(f"/cases/{case_id}/checklist/hotel_booking/submit",
                json={"document_id": up1.json()["id"]}, headers=H)
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "hotel_booking")["status"] == "submitted"
    # Replace with a different file → back to ready_to_submit, NOT fulfilled.
    up2 = _upload(client, case_id, "hotel_booking", "h2.pdf",
                  text=HOTEL_TEXT + "\nBooking ref 55")
    assert up2.json()["id"] != up1.json()["id"]
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "hotel_booking")["status"] == "ready_to_submit"
    assert _item(j, "hotel_booking")["binding"]["document_id"] == up2.json()["id"]
    # A stale submit against the replaced file is refused.
    stale = client.post(f"/cases/{case_id}/checklist/hotel_booking/submit",
                        json={"document_id": up1.json()["id"]}, headers=H)
    assert stale.status_code == 409
    # Withdraw → Needed again; idempotent.
    w1 = client.post(f"/cases/{case_id}/checklist/hotel_booking/withdraw", headers=H)
    assert w1.status_code == 200
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "hotel_booking")["status"] == "pending"
    w2 = client.post(f"/cases/{case_id}/checklist/hotel_booking/withdraw", headers=H)
    assert w2.status_code == 200 and w2.json()["withdrawn"] is False


def test_clear_mismatch_blocks_submit(client):
    """A confidently-classified different type (flight text on the hotel
    requirement) can never be submitted — even with confirm=true."""
    case_id = _continue_case(client, EXEMPT_ANSWER, "GTM")
    up = _upload(client, case_id, "hotel_booking", "not-hotel.pdf", text=FLIGHT_TEXT)
    assert up.json()["binding"]["match_verdict"] == "mismatch"
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "hotel_booking")["status"] == "mismatch"
    for confirm in (False, True):
        s = client.post(f"/cases/{case_id}/checklist/hotel_booking/submit",
                        json={"confirm": confirm}, headers=H)
        assert s.status_code == 409
        assert s.json()["detail"]["reason"] == "mismatch"
        assert s.json()["detail"]["detected_type"] == "flight_itinerary"


def test_uncertain_classification_requires_explicit_confirmation(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "HND")
    up = _upload(client, case_id, "hotel_booking", "unclear.pdf", text=GENERIC_TEXT)
    assert up.json()["binding"]["match_verdict"] == "uncertain"
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "hotel_booking")["status"] == "needs_review"
    s = client.post(f"/cases/{case_id}/checklist/hotel_booking/submit",
                    json={}, headers=H)
    assert s.status_code == 409 and s.json()["detail"]["reason"] == "confirm_required"
    s = client.post(f"/cases/{case_id}/checklist/hotel_booking/submit",
                    json={"confirm": True}, headers=H)
    assert s.status_code == 200 and s.json()["submitted"] is True


def test_manual_type_choice_is_whitelisted_and_reevaluates(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "NIC")
    up = _upload(client, case_id, "hotel_booking", "unclear2.pdf", text=GENERIC_TEXT)
    doc_id = up.json()["id"]
    # 'passport' is never manually claimable.
    r = client.post(f"/cases/{case_id}/documents/{doc_id}/set-type",
                    json={"doc_type": "passport"}, headers=H)
    assert r.status_code == 400
    r = client.post(f"/cases/{case_id}/documents/{doc_id}/set-type",
                    json={"doc_type": "hotel_booking"}, headers=H)
    assert r.status_code == 200
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "hotel_booking")["status"] == "ready_to_submit"
    # A confidently keyword-classified document refuses manual retyping.
    up2 = _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    r = client.post(f"/cases/{case_id}/documents/{up2.json()['id']}/set-type",
                    json={"doc_type": "bank_statement"}, headers=H)
    assert r.status_code == 409


def test_one_document_never_satisfies_two_incompatible_requirements(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "PAN")
    up = _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    assert up.status_code == 200
    # Re-uploading the SAME file against the hotel requirement dedups to the
    # same document and refuses the incompatible second binding.
    again = _upload(client, case_id, "hotel_booking", "f.pdf", text=FLIGHT_TEXT)
    assert again.status_code == 409
    assert again.json()["detail"]["reason"] == "document_already_used"


def test_unbound_upload_never_changes_checklist(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "CRI")
    before = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    r = client.post(f"/cases/{case_id}/documents",
                    json={"name": "loose-flight.pdf", "text": FLIGHT_TEXT}, headers=H)
    assert r.status_code == 200 and "binding" not in r.json()
    after = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert after["checklist_counts"]["required_missing"] == \
        before["checklist_counts"]["required_missing"]
    assert _item(after, "flight_itinerary")["status"] == "pending"


# =========================================================================
# Part 3 — checklist completion + Continue advances the existing case
# =========================================================================
def _submit_all_required(client, case_id):
    uploads = {"photo": dict(name="me.jpg", mime="image/jpeg", content=JPEG),
               "flight_itinerary": dict(name="f.pdf", text=FLIGHT_TEXT),
               "hotel_booking": dict(name="h.pdf", text=HOTEL_TEXT),
               "bank_statement": dict(name="b.pdf", text=BANK_TEXT)}
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    for item in j["checklist"]:
        if item["kind"] != "document" or item["status"] == "submitted" \
                or not item["required"]:
            continue
        kw = uploads.get(item["id"], dict(name=f"{item['id']}.pdf",
                                          text=GENERIC_TEXT))
        up = _upload(client, case_id, item["id"], **kw)
        assert up.status_code == 200, item["id"]
        s = client.post(f"/cases/{case_id}/checklist/{item['id']}/submit",
                        json={"confirm": True}, headers=H)
        assert s.status_code == 200, (item["id"], s.json())


def test_continue_refused_until_all_mandatory_items_fulfilled(client, db):
    case_id = _continue_case(client, REQUIRED_ANSWER, "DOM")
    r = client.post(f"/cases/{case_id}/checklist/complete", headers=H)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["reason"] == "checklist_incomplete" and d["required_remaining"] > 0
    assert "Submit" in d["message"] and "remaining required" in d["message"]
    # The workflow itself also refuses to start with documents outstanding.
    s = client.post(f"/cases/{case_id}/start", headers=H)
    assert s.status_code == 409
    assert s.json()["detail"]["reason"] == "documents_incomplete"
    # Fulfil everything → Continue succeeds, advances the SAME case.
    cases_before = len(db.execute(select(core_models.VisaApplication)).scalars().all())
    _submit_all_required(client, case_id)
    r = client.post(f"/cases/{case_id}/checklist/complete", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is True and body["already_completed"] is False
    assert body["next_stage"] == "application_preparation"
    cases_after = len(db.execute(select(core_models.VisaApplication)).scalars().all())
    assert cases_after == cases_before          # no new case, no restarted wizard
    # Idempotent on duplicate clicks.
    r2 = client.post(f"/cases/{case_id}/checklist/complete", headers=H)
    assert r2.status_code == 200 and r2.json()["already_completed"] is True
    assert r2.json()["completed_at"] == body["completed_at"]
    # The stage marker survives refresh (any later GET).
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert j["intake_stage"]["completed"] is True


def test_visa_exempt_route_continues_to_entry_preparation(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "SLV")
    _submit_all_required(client, case_id)
    r = client.post(f"/cases/{case_id}/checklist/complete", headers=H)
    assert r.status_code == 200
    assert r.json()["next_stage"] == "entry_preparation"


def test_electronic_authorization_route_continues_to_preparation(client):
    case_id = _continue_case(client, ETA_ANSWER, "JAM")
    _submit_all_required(client, case_id)
    r = client.post(f"/cases/{case_id}/checklist/complete", headers=H)
    assert r.status_code == 200
    assert r.json()["next_stage"] == "application_preparation"


def test_progress_and_submissions_survive_refresh_and_new_session(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "BLZ")
    up = _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    client.post(f"/cases/{case_id}/checklist/flight_itinerary/submit",
                json={"document_id": up.json()["id"]}, headers=H)
    # A brand-new client (fresh browser / restarted backend reading the same
    # DB) sees identical state.
    fresh = TestClient(fastapi_app)
    j = fresh.get(f"/cases/{case_id}/checklist", headers=H).json()
    item = _item(j, "flight_itinerary")
    assert item["status"] == "submitted"
    assert item["binding"]["document_name"] == "f.pdf"
    assert item["binding"]["submitted_at"]


def test_withdraw_after_completion_reblocks_start(client):
    """Retracting a mandatory submission re-opens the gate — the workflow can
    never start on a checklist that regressed."""
    case_id = _continue_case(client, EXEMPT_ANSWER, "TTO")
    _submit_all_required(client, case_id)
    assert client.post(f"/cases/{case_id}/checklist/complete",
                       headers=H).status_code == 200
    client.post(f"/cases/{case_id}/checklist/flight_itinerary/withdraw", headers=H)
    s = client.post(f"/cases/{case_id}/start", headers=H)
    assert s.status_code == 409
    assert s.json()["detail"]["reason"] == "documents_incomplete"


# =========================================================================
# Part 4 — security: tenancy, logs, no PII leakage
# =========================================================================
def test_other_tenant_cannot_bind_submit_or_complete(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "GUY")
    up = _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    assert up.status_code == 200
    assert client.post(f"/cases/{case_id}/checklist/flight_itinerary/submit",
                       json={}, headers=H2).status_code in (403, 404)
    assert client.post(f"/cases/{case_id}/checklist/flight_itinerary/withdraw",
                       headers=H2).status_code in (403, 404)
    assert client.post(f"/cases/{case_id}/checklist/complete",
                       headers=H2).status_code in (403, 404)


def test_checklist_audit_events_carry_no_pii(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "SUR")
    up = _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    client.post(f"/cases/{case_id}/checklist/flight_itinerary/submit",
                json={"document_id": up.json()["id"]}, headers=H)
    events = client.get(f"/cases/{case_id}/audit", headers=H).json()["events"]
    intake_events = [e for e in events if e["action"].startswith("checklist_")
                     or e["action"] == "document_intake_completed"]
    assert intake_events
    import json as _json
    dump = _json.dumps(intake_events)
    assert "X1234567" not in dump      # passport number never in audit details
    assert "ABC123" not in dump        # booking reference never in audit details
    assert "1990-01-15" not in dump    # birth date never in audit details


def test_checklist_responses_never_expose_storage_paths(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "HTI")
    _upload(client, case_id, "flight_itinerary", "f.pdf", text=FLIGHT_TEXT)
    import json as _json
    j = client.get(f"/cases/{case_id}/checklist", headers=H)
    assert "local://" not in _json.dumps(j.json())
    assert "storage_ref" not in _json.dumps(j.json())
