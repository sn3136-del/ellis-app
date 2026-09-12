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
                                     "No visa application fee is charged", "Issued free of charge"])
def test_explicit_fee_waiver_retains_zero_without_a_visa_exemption(wording):
    row, = rows({**CHILD, "type": "EU-family-member entry visa", "notes": wording})
    assert (row["visa_fee_amount"], row["visa_fee_currency"]) == (0, "EUR")
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["visa_requirement_detail"] == "Paper Visa"


@pytest.mark.parametrize("label,note,expected", [
    ("Schengen C visa, children under 6", "No fee for applicants aged 0 to 6", 0),
    ("Schengen C visa, children under 6", "No fee for applicants aged 0 to 6 per official embassy notice. Entries not specified by the official page, so the entries fact remains unset.", 0),
    ("Schengen C visa, children under six", "No visa fee for applicants aged 0–6 years old", 0),
    ("Schengen C visa, children under 5", "No fee for children aged 0 to 6 years", 0),
    ("Schengen C visa", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa for adults", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, children under 12", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, children under 6", "No fee for applicants aged 6 to 12", None),
    ("Schengen C visa, children under 6", "No fee for applicants aged 0 to 5", None),
    ("Schengen C visa, children aged 6 to 12", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, adults and children under 6", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, children under 6 or adults", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, applicants not under 6", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, children under 6 or under 12", "No fee for applicants aged 0 to 6", None),
    ("Tourist visa for parents with children under 6", "No fee for applicants aged 0 to 6", None),
    ("Visitor visa, guardians accompanying children under 6", "No fee for applicants aged 0 to 6", None),
    ("Schengen C visa, children under 6", "No fee information published for applicants aged 0 to 6", None),
    ("Schengen C visa, children under 6", "No fee for applicants aged 0 to 6 if applying online", None),
    ("Schengen C visa, children under 6", "No fee for applicants aged 0 to 6. Fee is unknown.", None),
])
def test_explicit_applicant_age_fee_waiver_stays_inside_product_population(label, note, expected):
    row, = rows(dict(CHILD, type=label, notes=note))
    assert row["visa_fee_amount"] == expected
    assert row["visa_requirement"] == "Visa Required in Advance"


def test_explicit_applicant_age_waiver_cannot_override_own_unpublished_fee():
    row, = rows(dict(CHILD, type="Schengen C visa, children under 6",
                     notes="No fee for applicants aged 0 to 6", unpublished_fields=["fee"]))
    assert row["visa_fee_amount"] is None


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


@pytest.mark.parametrize("note", ["Issued free of charge.", "No visa fee is payable.",
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


@pytest.mark.parametrize('note', [
    'The official BCBP page publishes no issuance fee for the on-arrival tourist visa, so the initial-fee fact remains unset.',
    'No fee information published.', 'No fee data available.', 'No fees listed.',
    'No fee waiver applies to this visa.', 'There is no fee waiver for tourist visas.',
    'The fee waiver ended on 31 December 2024.', 'Fee waivers were abolished in 2023.',
    'Fee waivers have been discontinued.', 'The visa used to be free.',
    'Das Visum ist nicht kostenlos.', 'El visado no es gratuito.', "Le visa n'est pas gratuit.",
    'The visa is free for children under 12', 'Free of charge for holders of diplomatic passports.',
    'Fee: nil (to be confirmed)', 'No fee shown. The 0 is a placeholder.',
    'Visa fee waived. This 0 is a placeholder.', 'No visa application fee',
    'The IMUGA declaration is free of charge.', 'Registration is free.',
    'Free help is available for completing this application.',
])
def test_unknown_expired_negated_and_other_applicant_waivers_cannot_price_a_general_product(note):
    row, = rows(dict(CHILD, type='Tourist visa', notes=note))
    assert row['visa_fee_amount'] is None


@pytest.mark.parametrize('scope', ['route', 'product'])
@pytest.mark.parametrize('field', ['visa_fee_amount', 'visa_fee_currency', 'fee', 'government_fee'])
def test_unpublished_fee_vetoes_even_an_explicit_waiver(scope, field):
    product = dict(CHILD, type='Tourist visa', notes='No visa fee is payable.')
    guidance = dict(GUIDANCE, government_fee={'amount': 0, 'currency': 'USD'}, visa_products=[product])
    (product if scope == 'product' else guidance)['unpublished_fields'] = [field]
    row, = tstation.records_for_route(ROUTE, guidance)
    assert row['visa_fee_amount'] is None


def test_palau_unpublished_arrival_fee_stays_unpublished_in_product_projection():
    route = dict(ROUTE, passport_nationality='CHN', destination_country='PLW')
    product = dict(CHILD, type='Visa on arrival', requirement_detail='visa_on_arrival',
                   fee={'amount': 0, 'currency': 'USD'},
                   notes='The official BCBP page publishes no issuance fee for the on-arrival tourist visa, so the initial-fee fact remains unset.',
                   unpublished_fields=['visa_fee_amount', 'visa_fee_currency'])
    guidance = dict(GUIDANCE, disposition='VISA_ON_ARRIVAL', requirement_detail='visa_on_arrival',
                    source_url='https://bcbp.pw/?page_id=165', visa_products=[product])
    row, = tstation.records_for_route(route, guidance)
    assert (row['visa_fee_amount'], row['visa_fee_currency']) == (None, None)
    assert tstation.field_status(row)['visa_fee_amount'] == 'not-published'


def test_explicit_transit_waiver_preserves_its_zero_without_borrowing_route_detail():
    product = dict(CHILD, type='Third-country transit waiver (B-2)',
                   notes='Free, no Korean visa needed. Conditions apply to the transit itinerary.',
                   requirement_detail='paper_visa', fee={'amount': 0, 'currency': 'KRW'})
    row, = rows(product, disposition='CONDITIONAL', requirement_detail='transit_visa_free')
    assert (row['visa_fee_amount'], row['visa_fee_currency']) == (0, 'KRW')


def test_a_named_free_product_cannot_override_a_note_denying_its_fee_waiver():
    row, = rows(dict(CHILD, type='Tourist visa (free of charge)', notes='The fee waiver has been discontinued.'))
    assert row['visa_fee_amount'] is None


def test_child_waiver_cannot_be_extended_to_an_older_child_lane():
    row, = rows(dict(CHILD, type='Tourist visa (child under 12)', notes='Visa fee waived for children under six.'))
    assert row['visa_fee_amount'] is None


def test_unknown_other_field_does_not_erase_a_stated_fee_waiver():
    row, = rows(dict(CHILD, type='Tourist visa', notes='No visa fee is charged. The entries fact remains unset.'))
    assert row['visa_fee_amount'] == 0


@pytest.mark.parametrize('sentence', ['La solicitud ante BLS no es posible.',
                                      'Les demandes au centre TLScontact ne sont plus acceptées.',
                                      'Please refrain from submitting applications at the VFS centre.'])
def test_multilingual_negative_agent_wordings_are_not_application_instructions(sentence):
    from app.visa_snapshot.evidence_validator import field_value_supported
    assert not field_value_supported('application_channel', 'authorised_agent', sentence)
