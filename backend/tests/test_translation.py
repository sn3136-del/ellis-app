"""Document language detection + Kimi K3 machine translation.

Detection is local and deterministic (no provider, no text leaves the
backend). Translation runs only on the applicant's explicit request, sends
ONLY sentinel-masked OCR text (never raw bytes), stores the result as a
linked machine-translation artifact with the disclaimer, preserves
identifiers byte-for-byte, is cached per (document, target), and maps
provider failures to honest messages. No document text or PII in audit."""
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.main import app as fastapi_app
from app import models as core_models, translation
from app.visa_snapshot import kimi_primary
from app.visa_snapshot.models import KimiRouteGuidanceCache

from .test_intake_flow import (H, ANSWERS_SGP, EXEMPT_ANSWER, REQUIRED_ANSWER,
                               _resolve_with_guidance)
from .test_document_intake import _continue_case, _item, _upload


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
    translation.set_translator(None)


# ---- local deterministic detection ----------------------------------------
def test_detect_language_scripts_and_stopwords():
    zh = "酒店预订确认。入住日期：七月二十六日。退房日期：八月二十六日。客人姓名与房型确认无误，请出示证件办理入住手续。"
    assert translation.detect_language(zh)["code"] == "zh"
    ar = "تأكيد حجز الفندق. تاريخ الوصول السادس والعشرون من يوليو. اسم الضيف مؤكد في النظام."
    assert translation.detect_language(ar)["code"] == "ar"
    ru = "Подтверждение бронирования гостиницы. Дата заезда двадцать шестое июля. Имя гостя подтверждено."
    assert translation.detect_language(ru)["code"] == "ru"
    es = ("Confirmación de la reserva del hotel. El señor García tiene una "
          "cuenta con el banco para la fecha del viaje según el número de la reserva.")
    assert translation.detect_language(es)["code"] == "es"
    fr = ("Confirmation de la réservation. Monsieur Dupont est le client du "
          "compte pour la banque avec le numéro de la réservation dans les délais.")
    assert translation.detect_language(fr)["code"] == "fr"
    en = ("Hotel booking confirmation for the guest name and the account "
          "number with this bank statement from the date of travel.")
    assert translation.detect_language(en)["code"] == "en"
    # Too little text → honest empty result (never a guess).
    assert translation.detect_language("hi") == {}
    assert translation.detect_language("") == {}


def test_target_language_follows_destination():
    assert translation.target_for_destination("CHN") == "zh"
    assert translation.target_for_destination("JPN") == "ja"
    assert translation.target_for_destination("BRA") == "pt"
    assert translation.target_for_destination("USA") == "en"
    assert translation.target_for_destination("China") == "zh"     # name → registry
    assert translation.target_for_destination("Nowhereland") == "en"  # honest default


def test_certified_translation_flag_scans_guidance():
    assert translation.certified_translation_flag({
        "required_documents": ["bank statement with certified translation"]})
    assert translation.certified_translation_flag({
        "uncertainty": ["documents may need a notarized translation"]})
    assert not translation.certified_translation_flag({
        "required_documents": ["bank statement", "certified copy of deed"]})
    assert not translation.certified_translation_flag({})


# ---- endpoint flow ---------------------------------------------------------
ES_BANK = ("Extracto bancario del Banco Ejemplo\n"
           "Titular de la cuenta: JOHN DOE\n"
           "Número de cuenta: 9988776655\n"
           "Saldo final: USD 12.345,67\n"
           "Período del extracto: junio de 2026 según el banco para la cuenta")


def _upload_spanish_statement(client, case_id):
    return _upload(client, case_id, "bank_statement", "extracto.pdf", text=ES_BANK)


def test_language_detected_at_upload_and_surfaced_in_binding(client):
    case_id = _continue_case(client, REQUIRED_ANSWER, "AZE")
    up = _upload_spanish_statement(client, case_id)
    assert up.status_code == 200
    assert up.json()["language"]["code"] == "es"
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    b = _item(j, "bank_statement")["binding"]
    assert b["language"]["code"] == "es" and b["has_text"] is True
    # The route's target language rides on the checklist response.
    assert j["translation"]["target"] == "en"          # AZE → honest default
    assert j["translation"]["target_name"] == "English"


def test_translate_masks_identifiers_stores_artifact_and_caches(client, db):
    case_id = _continue_case(client, REQUIRED_ANSWER, "GEO")
    up = _upload_spanish_statement(client, case_id)
    doc_id = up.json()["id"]

    seen = {}

    def fake_translate(text, target, source):
        seen["text"] = text
        seen["target"] = target
        return "Bank statement (translated). Holder JOHN DOE. All sentinels: " + text

    translation.set_translator(fake_translate)
    r = client.post(f"/cases/{case_id}/documents/{doc_id}/translate",
                    json={}, headers=H)
    assert r.status_code == 200
    out = r.json()
    # Raw identifiers were MASKED before the provider saw the text…
    assert "9988776655" not in seen["text"]
    assert seen["target"] == "en"
    # …and restored byte-for-byte in the stored translation.
    assert "9988776655" in out["translated_text"]
    assert out["disclaimer"] == translation.DISCLAIMER
    assert out["source_language"] == "es" and out["target_language"] == "en"
    # Linked artifact with disclaimer in the previewable blob.
    art = db.get(core_models.StoredDocument, out["document_id"])
    assert art.translation_of == doc_id
    assert art.doc_type == "translation" and art.mime == "text/plain"
    blob = db.get(core_models.DocumentBlob, art.id)
    assert translation.DISCLAIMER.encode() in blob.content
    # Preview URL works for the artifact (download/preview supported).
    u = client.get(f"/cases/{case_id}/documents/{art.id}/url", headers=H).json()
    assert u["available"] is True and u["mime"] == "text/plain"
    # Cached: a second request returns the SAME artifact without re-translating.
    translation.set_translator(lambda *a: (_ for _ in ()).throw(AssertionError("re-translated")))
    r2 = client.post(f"/cases/{case_id}/documents/{doc_id}/translate",
                     json={}, headers=H)
    assert r2.status_code == 200
    assert r2.json()["document_id"] == out["document_id"]
    assert r2.json()["cached"] is True
    # The artifact surfaces on the source document's checklist binding.
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert _item(j, "bank_statement")["binding"]["translation_document_id"] == art.id


def test_translation_artifact_can_be_bound_and_submitted(client):
    case_id = _continue_case(client, REQUIRED_ANSWER, "ARM")
    up = _upload_spanish_statement(client, case_id)
    doc_id = up.json()["id"]
    translation.set_translator(lambda text, target, source: "translated body " + text)
    art_id = client.post(f"/cases/{case_id}/documents/{doc_id}/translate",
                         json={}, headers=H).json()["document_id"]
    r = client.post(f"/cases/{case_id}/checklist/bank_statement/bind",
                    json={"document_id": art_id}, headers=H)
    assert r.status_code == 200
    s = client.post(f"/cases/{case_id}/checklist/bank_statement/submit",
                    json={"document_id": art_id, "confirm": True}, headers=H)
    assert s.status_code == 200 and s.json()["submitted"] is True


def test_translate_without_enough_text_is_refused_honestly(client):
    case_id = _continue_case(client, EXEMPT_ANSWER, "MDA")
    up = _upload(client, case_id, None, "photo.jpg", mime="image/jpeg",
                 content=b"\xff\xd8\xff\xe0" + b"\x00" * 40)
    r = client.post(f"/cases/{case_id}/documents/{up.json()['id']}/translate",
                    json={}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"]["message"] == translation.NO_TEXT_MESSAGE


def test_provider_errors_map_to_honest_categories(client, monkeypatch):
    from app.providers import kimi as kimi_provider_mod
    from app.providers.kimi import KimiHttpError, KimiTimeout

    case_id = _continue_case(client, EXEMPT_ANSWER, "BLR")
    up = _upload(client, case_id, "flight_itinerary", "extracto2.pdf", text=ES_BANK)
    doc_id = up.json()["id"]

    class FakeProvider:
        name = "live"

        def __init__(self, exc):
            self.exc = exc

        def translate(self, *a, **k):
            raise self.exc

    cases = [(KimiHttpError(401), 503, "kimi_auth_failed"),
             (KimiHttpError(402), 503, "kimi_quota_exhausted"),
             (KimiHttpError(429), 503, "kimi_rate_limited"),
             (KimiHttpError(503), 503, "kimi_unavailable")]
    for exc, http_status, category in cases:
        monkeypatch.setattr(kimi_provider_mod, "get_provider",
                            lambda exc=exc: FakeProvider(exc))
        r = client.post(f"/cases/{case_id}/documents/{doc_id}/translate",
                        json={}, headers=H)
        assert r.status_code == http_status, category
        assert r.json()["detail"]["category"] == category
    # Timeout → 504 with an honest retry message; the original is unchanged.
    monkeypatch.setattr(kimi_provider_mod, "get_provider",
                        lambda: FakeProvider(KimiTimeout("120s")))
    r = client.post(f"/cases/{case_id}/documents/{doc_id}/translate",
                    json={}, headers=H)
    assert r.status_code == 504
    assert "timed out" in r.json()["detail"]["message"]
    # Unconfigured provider (the hermetic test default) → honest unavailable.
    monkeypatch.undo()
    r = client.post(f"/cases/{case_id}/documents/{doc_id}/translate",
                    json={}, headers=H)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "translation_unavailable"


def test_no_document_text_in_audit_or_checklist_payload(client):
    case_id = _continue_case(client, REQUIRED_ANSWER, "LTU")
    up = _upload_spanish_statement(client, case_id)
    doc_id = up.json()["id"]
    translation.set_translator(lambda text, target, source: "translated " + text)
    client.post(f"/cases/{case_id}/documents/{doc_id}/translate", json={}, headers=H)
    import json as _json
    events = client.get(f"/cases/{case_id}/audit", headers=H).json()["events"]
    dump = _json.dumps(events)
    assert "9988776655" not in dump        # account number never in audit
    assert "Extracto bancario" not in dump  # document text never in audit
    tr_events = [e for e in events if e["action"] == "document_translated"]
    assert tr_events and tr_events[0]["detail"]["target_language"] == "en"
    # The checklist payload carries language codes, never the OCR text.
    j = client.get(f"/cases/{case_id}/checklist", headers=H).json()
    assert "Extracto bancario" not in _json.dumps(j)


def test_translation_is_bounded_and_deterministic():
    """K3 is a reasoning model, and this call set neither temperature nor
    max_tokens — so a translation could spend most of its wall clock
    deliberating about text that needs no deliberation, and the applicant
    waited. Translation is not a reasoning task."""
    import inspect
    from app.providers import kimi
    src = inspect.getsource(kimi.LiveKimiProvider.translate)
    assert "temperature=0.0" in src, "translation must be deterministic"
    assert "max_tokens=budget" in src, "translation must be bounded"
    assert "do not deliberate" in src
    # The budget scales with the input, with a floor and a ceiling.
    assert "max(1200" in src and "min(16000" in src


def test_the_translate_button_needs_one_press():
    """A second confirmation explained an internal boundary (extracted text,
    never the image bytes) to someone who just wanted their document readable.
    The boundary is still enforced in the backend, where it belongs."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / \
        "src/renderer/src/components/visa/Checklist.jsx"
    text = src.read_text()
    assert "askTranslate" not in text, "the confirmation step is back"
    assert "translateConsent" not in text, "the consent copy is back"
    # And the button still exists, wired straight to the action.
    assert "onClick={translate}" in text
