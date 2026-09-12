"""A fee waiver must never become a visa exemption or erase visa procedure."""
from copy import deepcopy

import pytest

from app.visa_snapshot import tstation

ROUTE = {"passport_nationality": "IDN", "destination_country": "FRA",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
GUIDANCE = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
            "visa_category": "Short-stay Schengen C visa", "application_channel": "visa_center",
            "required_documents": ["Passport", "Schengen visa application form"],
            "source_url": "https://france-visas.gouv.fr/", "processing_time": "15 calendar days",
            "permitted_stay": "90 days", "permitted_stay_days": 90,
            "government_fee": {"amount": 90, "currency": "EUR"}}
CHILD = {"type": "Short-stay Schengen C (child under 6)", "entry": "single",
         "validity": None, "max_stay_days": 90, "fee": {"amount": 0, "currency": "EUR"},
         "notes": "Visa fee waived for children under six years (Visa Code Art. 16(4)(a)); "
                  "France-Visas FAQ likewise lists 'children under 6 years old' among fee exemptions. "
                  "No fixed validity published; set per application (stay plus 15-day grace period)."}


def rows(product, **patch):
    guidance = dict(GUIDANCE, visa_products=[product], **patch)
    return tstation.records_for_route(ROUTE, guidance)


def test_actual_schengen_child_fee_waiver_preserves_visa_requirement_and_procedure():
    before = deepcopy(CHILD)
    row, = rows(CHILD)
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["visa_requirement_detail"] == "Paper Visa"
    assert (row["visa_fee_amount"], row["visa_fee_currency"]) == (0, "EUR")
    assert row["application_method"] == "Agency Service"
    assert "Schengen visa application form" in row["required_documents"]
    assert row["source_url"] == GUIDANCE["source_url"]
    assert row["processing_min_days"] == 15
    assert not row.get("_separate_permission")
    assert row["confidence_level"] == "Medium" and row["_evidence_low"] is True  # projection is not new proof: shown with its source, held
    assert CHILD == before


@pytest.mark.parametrize("name,notes", [
    ("Tourist visa (fee-exempt child)", "Children are exempt from the visa fee."),
    ("Short-stay visa for an EU citizen's family member", "Visa fee waiver; visa still required."),
    ("EU-family-member entry visa", "Issued free of charge under the facilitated visa procedure."),
    ("Visitor visa", "No visa fee is payable."),
    ("Visitor visa", "No visa application fee is payable."),
    ("Visa fee waiver", "Applicants are exempt from the application fee."),
    ("Tourist visa", "No fee for this category; biometrics are exempt."),
    ("Visitor visa", "Interview waiver is available."),
    ("Family visa waiver", "Fee-only waiver. A visa is still required."),
    ("Tourist visa", "This category is not eligible for visa-free entry."),
    ("Tourist visa", "There is no visa exemption for this category."),
    ("Tourist visa", "Visa-free entry is not available."),
    ("Visa free of charge", "A valid visa is required before travel."),
    ("Tourist visa", "The visa waiver is not applicable."),
    ("Tourist visa", "Visa-free entry is unavailable for this category."),
    ("Tourist visa", "This visa exemption does not apply."),
    ("Tourist visa", "Exempt from the visa processing fee."),
])
@pytest.mark.parametrize("conditional", [False, True])
def test_fee_and_nonvisa_waivers_do_not_change_product_permission(name, notes, conditional):
    product = {**CHILD, "type": name, "notes": notes}
    patch = ({"disposition": "CONDITIONAL", "requirement_detail": "conditional_visa_free"}
             if conditional else {})
    row, = rows(product, **patch)
    assert not tstation._product_is_exemption(product)
    assert row["visa_requirement"] != "Visa-free"
    assert row["visa_requirement_detail"] == "Paper Visa"
    assert tstation.field_status(row)["application_method"] != "not-applicable"


@pytest.mark.parametrize("name,notes", [
    ("Visa-free transit", "Free, no visa needed while staying in the transit area."),
    ("E-passport visa exemption registration", "Registration and entry are free of charge."),
    ("Free Entry for 14 Days", "Only eligible visitors using the stated exemption."),
    ("Conditional entry", "Entry without a visa for the stated eligible visitors."),
    ("Visa waiver registration", "Online registration is required."),
    ("Visa-free transit", "A visa is required if you leave the permitted transit area."),
])
def test_actual_visa_exemption_lanes_remain_distinct(name, notes):
    product = {**CHILD, "type": name, "notes": notes}
    assert tstation._product_is_exemption(product)
    row, = rows(product, disposition="CONDITIONAL", requirement_detail="conditional_visa_free")
    assert row["visa_requirement_detail"] == "Conditional Visa-free"


def test_zero_fee_alone_does_not_imply_no_visa():
    row, = rows({**CHILD, "type": "Tourist visa", "notes": None})
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["visa_requirement_detail"] == "Paper Visa"


@pytest.mark.parametrize("fee", [None, {}, {"amount": None, "currency": None}])
def test_explicit_group_entry_exemption_does_not_require_a_fee_cell(fee):
    product = {"type": "Visa-free entry as an organised tourist group (PRC nationals)"}
    if fee is not None:
        product["fee"] = fee
    row, = rows(product, disposition="CONDITIONAL", requirement_detail=None)
    assert row["visa_requirement_detail"] == "Conditional Visa-free"
    assert tstation.field_status(row)["application_method"] == "not-applicable"
    assert row["confidence_level"] == "Medium" and row["_evidence_low"] is True  # classification cannot manufacture proof: held


@pytest.mark.parametrize("amount", [25, "0", True])
def test_named_exemption_with_contradictory_or_malformed_price_is_not_reclassified(amount):
    product = {"type": "Visa-free entry", "fee": {"amount": amount, "currency": "EUR"}}
    assert not tstation._product_is_exemption(product)


def test_missing_fee_cannot_turn_a_visa_fee_waiver_into_entry_exemption():
    product = {"type": "Visa fee waiver", "notes": "Children are exempt from the visa fee."}
    assert not tstation._product_is_exemption(product)


@pytest.mark.parametrize("wording", ["Visa fee waived", "Visa fee waiver", "No visa fee is payable",
                                     "No visa application fee", "Issued free of charge"])
def test_explicit_fee_waiver_retains_zero_without_a_visa_exemption(wording):
    row, = rows({**CHILD, "type": "EU-family-member entry visa", "notes": wording})
    assert (row["visa_fee_amount"], row["visa_fee_currency"]) == (0, "EUR")
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["visa_requirement_detail"] == "Paper Visa"


def test_explicit_visa_product_detail_cannot_be_overridden_by_waiver_words():
    product = {**CHILD, "type": "Visa waiver category", "requirement_detail": "paper_visa",
               "notes": "Fee waiver; visa remains necessary."}
    assert not tstation._product_is_exemption(product)
    row, = rows(product, disposition="CONDITIONAL", requirement_detail="conditional_visa_free")
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["visa_requirement_detail"] == "Paper Visa"


def test_fee_waived_esta_stays_an_authorization():
    product = {**CHILD, "type": "ESTA under the Visa Waiver Program", "notes": "Fee waived."}
    assert not tstation._product_is_exemption(product)
    row, = rows(product)
    assert row["visa_requirement_detail"] == "ETA Electronic Authorization"
    assert row["visa_requirement"] != "Visa-free"


def test_mixed_child_fee_waiver_and_real_entry_exemption_do_not_share_family():
    exemption = {**CHILD, "type": "Visa-free entry with qualifying residence card",
                 "notes": "No visa needed only with the qualifying residence card."}
    guidance = dict(GUIDANCE, disposition="CONDITIONAL", requirement_detail="conditional_visa_free",
                    visa_products=[CHILD, exemption])
    child, free = tstation.records_for_route(ROUTE, guidance)
    assert child["visa_requirement"] == "Visa Required in Advance"
    assert child["visa_requirement_detail"] == "Paper Visa"
    assert child["visa_fee_amount"] == 0
    assert free["visa_requirement_detail"] == "Conditional Visa-free"


# Round 7 of the field-fill review: a zero fee survives only on a structured
# signal, never on the word "free" read out of a note that denies a waiver.

KUWAIT = {"type": "Tourist eVisa / visa on arrival", "entry": "single", "validity": "30 days from date of issuance",
          "max_stay_days": 90, "fee": {"amount": 0, "currency": "KWD"},
          "notes": "Amount not published; varies by nationality and shown during the online application. "
                   "0 here is a placeholder, not a claim that it is free."}
KUWAIT_ROUTE = {"passport_nationality": "HKG", "lawful_country_of_residence": "HKG", "destination_country": "KWT",
                "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
KUWAIT_GUIDANCE = {"disposition": "VISA_ON_ARRIVAL", "visa_products": [KUWAIT], "source_url": "https://kuwaitvisa.moi.gov.kw/"}


def test_a_placeholder_zero_under_a_note_that_denies_a_waiver_projects_as_missing():
    assert tstation._fee(KUWAIT, KUWAIT_GUIDANCE, KUWAIT_ROUTE) == (None, "KWD")
    row, = tstation.records_for_route(KUWAIT_ROUTE, KUWAIT_GUIDANCE)
    assert row["visa_fee_amount"] is None
    assert tstation.field_status(row)["visa_fee_amount"] == "missing"


@pytest.mark.parametrize("note", ["not free", "The fee is not waived", "0 is a placeholder", "Free if applying online",
                                  "It is not free of charge", "No fee unless you apply late", "Fee: 0 (unpublished)"])
def test_a_waiver_word_inside_a_denial_keeps_no_zero(note):
    assert tstation._fee(dict(KUWAIT, notes=note), KUWAIT_GUIDANCE, KUWAIT_ROUTE) == (None, "KWD")


@pytest.mark.parametrize("note", ["Issued free of charge.", "No visa fee is payable.", "Visa fee waived for children under six.",
                                  "Free", "Visa fee: Free", "Free of charge for Hong Kong SAR passport holders.",
                                  "Amount not published, but the visa is free-of-charge for this nationality."])
def test_a_clause_that_states_no_fee_on_its_own_keeps_the_zero(note):
    assert tstation._fee(dict(KUWAIT, notes=note), KUWAIT_GUIDANCE, KUWAIT_ROUTE) == (0, "KWD")


def test_a_product_named_as_the_exempt_lane_keeps_its_zero():
    named = dict(KUWAIT, type="Fee-exempt Type C visa (applicant under 18)",
                 notes="Free visas are granted to children who have not reached 18 years of age.")
    assert tstation._fee(named, dict(KUWAIT_GUIDANCE, disposition="VISA_REQUIRED"), KUWAIT_ROUTE) == (0, "KWD")
    lane = dict(KUWAIT, type="Tourist ETA (free of charge for 40 eligible nationalities)", notes=None)
    assert tstation._fee(lane, dict(KUWAIT_GUIDANCE, disposition="ELECTRONIC_AUTHORIZATION_REQUIRED"), KUWAIT_ROUTE) == (0, "KWD")
    assert tstation._fee(dict(KUWAIT, notes=None), dict(KUWAIT_GUIDANCE, disposition="VISA_REQUIRED"), KUWAIT_ROUTE) == (None, "KWD")
