"""Phase 4 — passport biodata-page classifier + visa/stamp-page rejection.

Proves: only a validated biodata page is accepted as the passport identity
source; visa/stamp/cover/ID pages are rejected with the exact required message;
and a visa sticker never seeds passport-identity fields.
"""
from tests.conftest import AUTH, PASSPORT_MRZ
from app.providers import passport_classifier as pc
from app.providers import ocr
from app import models
from sqlalchemy import select

VISA_MRV = ("V<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
            "L8988901C4UTO7408122F1204159<<<<<<<<<<<<<<06")
TD1_ID = ("I<UTOD231458907<<<<<<<<<<<<<<<\n"
          "7408122F1204159UTO<<<<<<<<<<<6\n"
          "ERIKSSON<<ANNA<MARIA<<<<<<<<<<")
# A TD3 passport MRZ with a broken passport-number check digit → cannot validate.
INVALID_TD3 = ("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
               "L898902C30UTO7408122F1204159ZE184226B<<<<<10")


def _mrz(text):
    return ocr.parse_mrz(text)


# ---- pure classifier ----
def test_valid_biodata_is_accepted():
    r = pc.classify_page(text=PASSPORT_MRZ, mrz=_mrz(PASSPORT_MRZ))
    assert r["page_type"] == "passport_biodata"
    assert r["accepted_as_passport_identity"] is True
    assert r["reject"] is False


def test_machine_readable_visa_is_rejected_with_exact_message():
    r = pc.classify_page(text=VISA_MRV, mrz=_mrz(VISA_MRV))
    assert r["page_type"] == "visa_page"
    assert r["reject"] is True
    assert r["accepted_as_passport_identity"] is False
    assert r["message"] == pc.VISA_STAMP_MESSAGE


def test_visa_keywords_without_mrz_rejected():
    txt = "REPUBLIC OF ELSEWHERE\nVISA\nVISA TYPE: TOURIST\nMULTIPLE ENTRY\nDURATION OF STAY: 30 DAYS"
    r = pc.classify_page(text=txt, mrz=None)
    assert r["page_type"] == "visa_page"
    assert r["message"] == pc.VISA_STAMP_MESSAGE


def test_entry_stamp_page_rejected():
    txt = "IMMIGRATION\nADMITTED\nPORT OF ENTRY: JFK\nDEPARTURE\n10 JAN 2026"
    r = pc.classify_page(text=txt, mrz=None)
    assert r["page_type"] == "stamp_page"
    assert r["message"] == pc.VISA_STAMP_MESSAGE


def test_td1_zone_is_national_id_or_residence():
    r = pc.classify_page(text=TD1_ID, mrz=None)
    assert r["page_type"] in ("national_id", "residence_permit")
    assert r["reject"] is True


def test_residence_permit_keyword():
    r = pc.classify_page(text="RESIDENCE PERMIT\nType: long stay", mrz=None)
    assert r["page_type"] == "residence_permit"
    assert r["reject"] is True


def test_observation_page_rejected():
    r = pc.classify_page(text="OFFICIAL OBSERVATIONS\nENDORSEMENTS AND AMENDMENTS", mrz=None)
    assert r["page_type"] == "observation_page"
    assert r["reject"] is True


def test_blank_page_rejected():
    r = pc.classify_page(text="", mrz=None, has_image=False)
    assert r["page_type"] == "blank_page"
    assert r["reject"] is True


def test_unreadable_image_rejected():
    r = pc.classify_page(text="", mrz=None, has_image=True)
    assert r["page_type"] == "unreadable"
    assert r["reject"] is True


def test_passport_cover_rejected():
    r = pc.classify_page(text="PASSPORT\nUNITED STATES OF AMERICA", mrz=None)
    assert r["page_type"] == "passport_cover"
    assert r["reject"] is True


def test_invalid_mrz_is_unverified_not_accepted():
    r = pc.classify_page(text=INVALID_TD3, mrz=_mrz(INVALID_TD3))
    assert r["page_type"] == "passport_biodata_unverified"
    assert r["accepted_as_passport_identity"] is False
    assert r["reject"] is True


def test_supporting_document_is_not_an_error():
    r = pc.classify_page(text="Account balance: 5000\nAccount holder: Anna Eriksson", mrz=None)
    assert r["reject"] is False
    assert r["accepted_as_passport_identity"] is False


def test_biodata_labels_guard_against_visa_false_positive():
    # A page that mentions "visa" but carries biodata field labels must NOT be
    # misclassified as a visa page (goes to a non-rejecting classification).
    txt = ("Surname: ERIKSSON\nGiven names: ANNA\nNationality: UTOPIA\n"
           "Date of birth: 12 AUG 1974\nThis passport is valid for a visa-free stay")
    r = pc.classify_page(text=txt, mrz=None)
    assert r["page_type"] != "visa_page"
    assert r["reject"] is False


# ---- endpoint integration ----
def _case(client):
    return client.post("/cases", headers=AUTH, json={
        "full_name": "Anna", "email": "a@e.com", "destination_country": "Mockland"}).json()["id"]


def test_endpoint_accepts_valid_passport(client):
    cid = _case(client)
    r = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "passport.pdf", "mime": "application/pdf", "text": PASSPORT_MRZ})
    b = r.json()
    assert b["doc_type"] == "passport"           # unchanged for a valid biodata page
    assert b["page_type"] == "passport_biodata"
    assert b["accepted_as_passport_identity"] is True
    assert b["rejected"] is False
    assert b["extracted_fields"].get("passport_number")


def test_endpoint_rejects_visa_page_and_stores_no_identity(client, db):
    cid = _case(client)
    r = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "visa.jpg", "mime": "image/jpeg", "text": VISA_MRV})
    b = r.json()
    assert b["rejected"] is True
    assert b["doc_type"] == "visa_page"
    assert b["message"] == pc.VISA_STAMP_MESSAGE
    # A visa sticker must NEVER seed passport identity.
    assert b["extracted_fields"] == {}
    doc = db.execute(select(models.StoredDocument).where(
        models.StoredDocument.application_id == cid)).scalar_one()
    assert doc.doc_type == "visa_page"
    assert doc.page_classification["page_type"] == "visa_page"
    assert doc.extracted_fields == {}


def test_endpoint_supporting_document_not_rejected(client):
    cid = _case(client)
    r = client.post(f"/cases/{cid}/documents", headers=AUTH,
                    json={"name": "bank.pdf", "mime": "application/pdf",
                          "text": "Account balance: 5000\nAccount holder: Anna"})
    b = r.json()
    assert b["rejected"] is False
    assert b["accepted_as_passport_identity"] is False
