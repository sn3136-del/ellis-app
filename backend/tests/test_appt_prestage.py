"""Appointment pre-stage: fill everything, invent nothing, and never take the
human's act.

These pin the four things that make the pre-stage trustworthy:

  * The form answer set really comes from consular_forms. If this module ever
    forks the DS-160 field list, one copy will drift and an applicant will
    carry the drift into a form sworn under penalty of perjury.
  * A fact Ellis does not hold is REPORTED, in the applicant's words, with the
    one action that closes it. Never defaulted, never a plausible blank.
  * Every fee carries the page it came from and the date it was checked, and
    an amount Ellis has not verified is stated as unknown rather than guessed.
  * The irreducible human acts are always present, and the Art. 45 mandate is
    always unsigned. Booking, paying, signing and submitting stay human, and
    no state of the case can make them disappear.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app import appt_appointments_prestage as ps
from app import consular_forms as cf, models
from app.visa_snapshot.models import CaseRouteGuidance


# --------------------------------------------------------------- fixtures

CHINA_ANSWERS = {
    "surname": "ZHANG",
    "given_names": "WEI",
    "sex": "F",
    "marital_status": "single",
    "birth_date": "1990-04-12",
    "place_of_birth": "Shanghai",
    "nationality": "CHN",
    "address_line1": "88 Nanjing Road",
    "address_city": "Shanghai",
    "address_country": "CHN",
    "phone": "+86 21 5555 0000",
    "email": "wei@example.com",
    "passport_number": "E12345678",
    "issuing_country": "CHN",
    "passport_issue_date": "2019-03-01",
    "passport_expiry_date": "2029-02-28",
    "travel_purpose": "tourism",
    "arrival_date": "2026-11-02",
    "accommodation": "Hotel Example, New York",
}


def _case(db, *, org="org-prestage", destination="United States",
          answers=None) -> models.VisaApplication:
    applicant = models.Applicant(org_id=org, user_id="u-prestage",
                                 full_name="Zhang Wei", email="wei@example.com")
    db.add(applicant)
    db.flush()
    row = models.VisaApplication(
        org_id=org, user_id="u-prestage", applicant_id=applicant.id,
        destination_country=destination, visa_type="tourist",
        adapter_id="", state="DRAFT",
        answers=dict(answers if answers is not None else CHINA_ANSWERS))
    db.add(row)
    db.commit()
    return row


def _checklist(db, case, items):
    db.add(CaseRouteGuidance(org_id=case.org_id, case_id=case.id,
                            route_key="test", checklist=items, guidance={}))
    db.commit()


def _submit(db, case, item_id, *, name="bank statement.pdf"):
    """A document the applicant explicitly submitted against a checklist item."""
    doc = models.StoredDocument(
        org_id=case.org_id, application_id=case.id, name=name,
        mime="application/pdf", size_bytes=10, sha256="x" * 64,
        doc_type="bank_statement", ocr_status="done")
    db.add(doc)
    db.flush()
    db.add(models.ChecklistSubmission(
        org_id=case.org_id, application_id=case.id, item_id=item_id,
        document_id=doc.id, status="submitted", match_verdict="match",
        submitted_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    return doc


# ------------------------------------------------------------- route keys

@pytest.mark.parametrize("value", ["us", "USA", "us_b1b2", "B1/B2", "ds160"])
def test_us_aliases_resolve_to_the_us_route(value):
    assert ps.route_key(value) == ps.ROUTE_US_B1B2


@pytest.mark.parametrize("value", ["schengen", "France", "DEU", "esp"])
def test_schengen_aliases_and_member_states_resolve_to_schengen(value):
    assert ps.route_key(value) == ps.ROUTE_SCHENGEN


def test_a_route_ellis_has_not_verified_is_refused_not_guessed(db):
    """Pre-staging the wrong country's form for somebody's appointment is
    worse than pre-staging nothing."""
    case = _case(db, org="org-unsup", destination="Brazil", answers={})
    with pytest.raises(ps.UnsupportedRoute):
        ps.prestage(db, case)


def test_the_case_supplies_the_route_when_the_caller_does_not(db):
    case = _case(db, org="org-derive")
    assert ps.prestage(db, case)["route"] == ps.ROUTE_US_B1B2


# ------------------------------------- the DS-160 really comes from consular_forms

def test_ds160_prestage_calls_consular_forms_rather_than_reimplementing_it(db, monkeypatch):
    """One place in Ellis knows what a DS-160 asks. A second copy would drift,
    and the drift would be carried into a sworn form."""
    seen = []
    original = cf.prepare

    def spy(form_key, answers):
        seen.append(form_key)
        return original(form_key, answers)

    monkeypatch.setattr(cf, "prepare", spy)
    payload = ps.prestage(db, _case(db, org="org-spy"), "us")
    assert "ds160_prep" in seen
    assert payload["form"]["form_key"] == "ds160_prep"


def test_filled_fields_use_the_official_forms_own_wording(db):
    payload = ps.prestage(db, _case(db, org="org-wording"), "us")
    labels = {f["key"]: f["form_label"] for f in payload["filled"]}
    official = {key: label.strip() for label, key, _ in cf.DS160_PREP}
    assert labels["surname"] == official["surname"]
    assert labels["passport_number"] == official["passport_number"]


def test_stored_answers_are_written_the_way_the_form_asks(db):
    """ISO dates and ISO country codes are Ellis's vocabulary, not CEAC's."""
    payload = ps.prestage(db, _case(db, org="org-vocab"), "us")
    values = {f["key"]: f["value"] for f in payload["filled"]}
    assert values["birth_date"] == "12 April 1990"
    assert values["nationality"] == "China"
    assert values["sex"] == "Female"


def test_every_filled_value_names_where_it_came_from(db):
    payload = ps.prestage(db, _case(db, org="org-prov"), "us")
    assert payload["filled"]
    assert all(f["source"] == "applicant_answer" for f in payload["filled"])


# ------------------------------------------------ missing is reported, not invented

def test_a_missing_passport_fact_is_reported_not_invented(db):
    """No passport on file and no typed answer means Ellis does not have the
    number. It says so, and tells the applicant the one thing that fixes it."""
    answers = {k: v for k, v in CHINA_ANSWERS.items()
               if k not in ("passport_number", "passport_issue_date")}
    case = _case(db, org="org-nopass", answers=answers)
    payload = ps.prestage(db, case, "us")

    gaps = {m["key"]: m for m in payload["missing"]}
    assert "passport_number" in gaps
    assert gaps["passport_number"]["required"] is True
    assert "passport" in gaps["passport_number"]["how_to_resolve"].lower()
    # Not filled, not defaulted, not a placeholder anybody could mistake for
    # an answer.
    assert not any(f["key"] == "passport_number" for f in payload["filled"])
    assert cf.BLANK not in json.dumps(payload)


def test_the_missing_list_is_worded_for_the_applicant_not_in_storage_keys(db):
    answers = {k: v for k, v in CHINA_ANSWERS.items() if k != "place_of_birth"}
    payload = ps.prestage(db, _case(db, org="org-words", answers=answers), "us")
    gap = next(m for m in payload["missing"] if m["key"] == "place_of_birth")
    assert gap["label"] == cf.FIELD_QUESTIONS["place_of_birth"]["label"]
    assert gap["label"] != "place_of_birth"
    assert gap["how_to_resolve"]


def test_optional_gaps_are_listed_but_do_not_block(db):
    payload = ps.prestage(db, _case(db, org="org-opt"), "us")
    optional = [m for m in payload["missing"] if not m["required"]]
    assert optional and all(m["how_to_resolve"] for m in optional)
    assert payload["readiness"]["missing_required"] == 0
    assert payload["readiness"]["ready_for_the_human"] is True


# ------------------------------------------------------------------- fees

def test_every_fee_carries_a_source_and_an_as_of_date(db):
    for case, route in ((_case(db, org="org-fee-us"), "us"),
                        (_case(db, org="org-fee-sch", destination="France"),
                         "schengen")):
        for fee in ps.prestage(db, case, route)["fees"]:
            assert fee["source"].startswith("https://"), fee
            assert fee["as_of"] == ps.AS_OF, fee


def test_the_mrv_fee_is_the_curated_amount_and_ellis_only_gives_instructions(db):
    fees = ps.prestage(db, _case(db, org="org-mrv"), "us")["fees"]
    mrv = next(f for f in fees if f["key"] == "mrv")
    assert mrv["amount"] == 185.0 and mrv["currency"] == "USD"
    assert mrv["status"] == "curated"
    assert mrv["ellis_role"] == "instructions_only"
    channels = {c["key"] for c in mrv["payment_channels"]}
    assert {"citic_smart_counter", "citic_atm", "citic_online"} <= channels
    assert "never" in mrv["ellis_never"].lower()


def test_the_schengen_visa_fee_is_banded_by_the_applicants_age(db):
    def fee_for(birth_date, org):
        answers = {**CHINA_ANSWERS, "birth_date": birth_date}
        case = _case(db, org=org, destination="France", answers=answers)
        fees = ps.prestage(db, case, "schengen")["fees"]
        return next(f for f in fees if f["key"] == "schengen_visa_fee")

    adult = fee_for("1990-04-12", "org-adult")
    assert adult["amount"] == 90.0 and adult["status"] == "curated"
    today = dt.date.today()
    child = fee_for(today.replace(year=today.year - 8).isoformat(), "org-child")
    assert child["amount"] == 45.0
    infant = fee_for(today.replace(year=today.year - 3).isoformat(), "org-infant")
    assert infant["amount"] == 0.0


def test_without_a_date_of_birth_the_visa_fee_is_unknown_not_assumed(db):
    answers = {k: v for k, v in CHINA_ANSWERS.items() if k != "birth_date"}
    case = _case(db, org="org-nodob", destination="France", answers=answers)
    fee = next(f for f in ps.prestage(db, case, "schengen")["fees"]
               if f["key"] == "schengen_visa_fee")
    assert fee["status"] == ps.UNKNOWN
    assert fee["amount"] is None
    assert fee["how_to_resolve"]


def test_the_service_fee_is_unknown_with_its_legal_cap_rather_than_a_guess(db):
    """A plausible number beside a payment instruction is the invention this
    codebase refuses. Ellis knows the cap, not the charge."""
    case = _case(db, org="org-esp", destination="France")
    fee = next(f for f in ps.prestage(db, case, "schengen")["fees"]
               if f["key"] == "esp_service_fee")
    assert fee["status"] == ps.UNKNOWN and fee["amount"] is None
    assert fee["maximum_amount"] == 45.0
    assert "half the visa fee" in fee["maximum_rule"]


# --------------------------------------------------------- national portal

def test_the_french_national_portal_is_named_and_others_are_not_guessed(db):
    """One uniform form, one answer set, and a different site per member state.
    Naming the wrong ministry sends somebody to a system that cannot receive
    their application."""
    france = _case(db, org="org-fra", destination="France")
    portal = ps.prestage(db, france, "schengen")["national_portal"]
    assert portal["status"] == "curated" and portal["name"] == "France-Visas"
    assert portal["url"].startswith("https://")

    other = _case(db, org="org-svk", destination="Slovakia")
    unknown = ps.prestage(db, other, "schengen")["national_portal"]
    assert unknown["status"] == ps.UNKNOWN
    assert unknown["name"] == "" and unknown["how_to_resolve"]


# ------------------------------------------------------------- human acts

def test_a_fully_prepared_us_case_still_lists_every_irreducible_act(db):
    """Nothing the pre-stage achieves can retire one of these."""
    payload = ps.prestage(db, _case(db, org="org-acts-us"), "us")
    keys = [a["key"] for a in payload["human_acts"]]
    assert keys == ["pay_mrv_fee", "esign_ds160", "book_appointment",
                    "attend_appointment"]
    assert all(a["why_human"] and a["actor"] for a in payload["human_acts"])


def test_the_schengen_acts_keep_the_mandate_signature_and_biometrics_personal(db):
    case = _case(db, org="org-acts-sch", destination="France")
    payload = ps.prestage(db, case, "schengen")
    acts = {a["key"]: a for a in payload["human_acts"]}
    assert {"sign_mandate", "give_biometrics", "book_appointment",
            "pay_fees"} <= set(acts)
    assert acts["give_biometrics"]["conditional"] is True
    assert "59 months" in acts["give_biometrics"]["detail"]


def test_booking_is_never_something_ellis_did(db):
    for org, dest, route in (("org-noboo-us", "United States", "us"),
                             ("org-noboo-sch", "France", "schengen")):
        payload = ps.prestage(db, _case(db, org=org, destination=dest), route)
        assert payload["submitted"] is False
        assert "appointment_slot_search" in payload["never_automated"]
        assert "appointment_booking" in payload["never_automated"]
        assert any(a["key"] == "book_appointment" for a in payload["human_acts"])


# -------------------------------------------------------------- documents

def test_documents_report_present_and_missing_from_the_case_checklist(db):
    case = _case(db, org="org-docs")
    _checklist(db, case, [
        {"id": "bank", "kind": "document", "label": "Bank statement",
         "required": True},
        {"id": "insurance", "kind": "document", "label": "Travel insurance",
         "required": True},
        {"id": "validity", "kind": "check", "label": "Passport validity",
         "required": True},
    ])
    _submit(db, case, "bank")

    payload = ps.prestage(db, case, "us")
    docs = {d["item_id"]: d for d in payload["documents"]}
    assert docs["bank"]["present"] is True and docs["bank"]["document_id"]
    assert docs["insurance"]["present"] is False
    # A 'check' is Ellis's to verify, never a file the applicant owes.
    assert "validity" not in docs

    gap = next(m for m in payload["missing"] if m["key"] == "document:insurance")
    assert gap["label"] == "Travel insurance"
    assert "upload" in gap["how_to_resolve"].lower()
    assert payload["readiness"]["ready_for_the_human"] is False


def test_an_uploaded_but_unsubmitted_document_is_not_counted_as_present(db):
    """An upload is not the applicant's confirmation, and the post will ask
    for anything they did not stand behind."""
    case = _case(db, org="org-unsub")
    _checklist(db, case, [{"id": "bank", "kind": "document",
                           "label": "Bank statement", "required": True}])
    doc = models.StoredDocument(org_id=case.org_id, application_id=case.id,
                                name="b.pdf", mime="application/pdf",
                                doc_type="bank_statement", ocr_status="done")
    db.add(doc)
    db.flush()
    db.add(models.ChecklistSubmission(org_id=case.org_id, application_id=case.id,
                                      item_id="bank", document_id=doc.id,
                                      status="bound", match_verdict="match"))
    db.commit()
    payload = ps.prestage(db, case, "us")
    assert payload["documents"][0]["present"] is False


# ------------------------------------------------- document-return preference

def test_the_return_location_is_proposed_and_left_unconfirmed(db):
    payload = ps.prestage(db, _case(db, org="org-return"), "us")
    ret = payload["document_return"]
    assert ret["status"] == "proposed"
    assert ret["location"] == "Shanghai"      # from the address they gave
    assert ret["confirmed"] is False
    assert ret["source"].startswith("https://") and ret["as_of"] == ps.AS_OF


def test_an_unknown_return_location_says_unknown(db):
    answers = {k: v for k, v in CHINA_ANSWERS.items() if k != "address_city"}
    case = _case(db, org="org-return-unknown", answers=answers)
    ret = ps.prestage(db, case, "us")["document_return"]
    assert ret["status"] == ps.UNKNOWN and ret["location"] == ""
    assert ret["how_to_resolve"]


# ----------------------------------------------------------- the mandate

def test_the_mandate_renders_and_is_never_marked_signed(db):
    case = _case(db, org="org-mandate", destination="France")
    payload = ps.prestage(db, case, {"route_key": "schengen",
                                     "intermediary": "Trip.com Travel"})
    doc = db.get(models.StoredDocument, payload["mandate_document_id"])
    assert doc.doc_type == ps.MANDATE_DOC_TYPE
    assert doc.extracted_fields["signed"] is False
    assert doc.extracted_fields["signature_required"] is True
    assert doc.extracted_fields["signed_by"] == ""
    assert payload["mandate"]["signed"] is False

    blob = db.get(models.DocumentBlob, doc.id)
    assert blob.content.startswith(b"%PDF")
    assert b"UNSIGNED DRAFT" in blob.content
    assert b"Article 45" in blob.content
    assert b"Trip.com Travel" in blob.content
    # The signature is a blank the applicant fills, not a rendered name.
    assert b"Signature: ___" in blob.content


def test_signing_the_mandate_is_an_act_this_module_cannot_perform(db):
    """No function here flips the signed flag. If one is ever added, this
    fails, and it should: only the applicant signs."""
    assert not [name for name in dir(ps)
                if "sign" in name.lower() and callable(getattr(ps, name))]


def test_the_mandate_signature_is_listed_as_the_applicants_own_act(db):
    case = _case(db, org="org-mandate-act", destination="France")
    payload = ps.prestage(db, case, "schengen")
    gap = next(m for m in payload["missing"] if m["key"] == "signature:mandate")
    assert gap["kind"] == "signature" and gap["source"] == "personal"
    entry = next(d for d in payload["documents"]
                 if d.get("source") == "prepared_by_ellis")
    assert entry["signed"] is False


def test_drafting_the_mandate_twice_leaves_one_document(db):
    case = _case(db, org="org-mandate-idem", destination="France")
    first = ps.prestage(db, case, "schengen")["mandate_document_id"]
    second = ps.prestage(db, case, "schengen")["mandate_document_id"]
    assert first == second


def test_an_unnamed_intermediary_is_a_blank_and_a_reported_gap(db):
    """Ellis leaves the authority-holder blank rather than naming somebody the
    applicant never chose."""
    case = _case(db, org="org-mandate-noint", destination="France")
    payload = ps.prestage(db, case, "schengen")
    assert any(m["key"] == "intermediary_name" for m in payload["missing"])
    blob = db.get(models.DocumentBlob, payload["mandate_document_id"])
    assert cf.BLANK.encode() in blob.content


def test_the_mandate_records_that_biometrics_cannot_be_delegated(db):
    lines = "\n".join(ps.mandate_lines(
        applicant_name="Zhang Wei", member_state="France",
        intermediary="Trip.com Travel", answers=CHINA_ANSWERS,
        today=dt.date(2026, 8, 11)))
    assert "fingerprints" in lines
    assert "no intermediary may give them for me" in lines
    assert "Visa Information System" in lines


def test_the_mandate_is_deterministic_for_the_same_day(db):
    args = dict(applicant_name="Zhang Wei", member_state="France",
                intermediary="Trip.com Travel", answers=CHINA_ANSWERS,
                today=dt.date(2026, 8, 11))
    assert ps.render_mandate(**args) == ps.render_mandate(**args)


# -------------------------------------------------------------- the payload

def test_the_payload_has_the_shape_the_cockpit_renders(db):
    payload = ps.prestage(db, _case(db, org="org-shape"), "us")
    for key in ("filled", "missing", "fees", "human_acts", "documents"):
        assert isinstance(payload[key], list), key
    assert all({"key", "label", "how_to_resolve"} <= set(m)
               for m in payload["missing"])
    assert payload["as_of"] == ps.AS_OF
    assert "mandate_document_id" not in payload      # US route has no mandate
