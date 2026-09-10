"""Regression gates for the incident's evidence and grading failure paths."""
import json

import pytest

from app.visa_snapshot import kimi_primary, tstation, verified_overrides as vo

ROUTE = {"passport_nationality": "ZZZ", "destination_country": "JPN",
         "travel_purpose": "tourism"}
FREE = {"disposition": "VISA_EXEMPT", "permitted_stay": "30 days",
        "permitted_stay_days": 30, "confidence": "high",
        # A complete test record needs a policy date, not the cache TTL.
        "policy_valid_until": "2026-12-31",
        # Passport requirements must be supplied and checked independently
        # of the exemption, rather than fabricated by the projection.
        "required_documents": ["Valid passport"],
        "source_url": "https://www.mofa.go.jp/visa/"}
PROV = {"source_url": "https://www.mofa.go.jp/visa/", "verified_at": "2026-09-09",
        "verified_by": "Operator", "verifier": "human", "fields": ["disposition", "permitted_stay_days", "required_documents"],
        "note": "Official page: passport holders are exempt for 30 days."}


def entry(fields, **extra):
    return dict(route={"nationality": "ZZZ", "destination": "JPN"},
                source_url=PROV["source_url"], verified_at=PROV["verified_at"],
                note=PROV["note"], fields=fields, **extra)


@pytest.fixture
def files(tmp_path, monkeypatch):
    seed, operator = tmp_path / "seed.json", tmp_path / "operator.json"
    seed.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", seed)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(operator))
    vo.reload()
    yield seed, operator
    vo.reload()


def record(g=FREE, prov=PROV, **kw):
    return tstation.records_for_route(ROUTE, g, prov,
                                    valid_until="2026-09-20", **kw)[0]


def test_high_allows_ai_with_evidence_and_complete_record():
    assert record()["confidence_level"] == "High"
    assert record(prov=dict(PROV, verifier="ai"))["confidence_level"] == "High"
    historical = {k: v for k, v in PROV.items() if k != "verifier"}
    assert record(prov=historical)["confidence_level"] == "High"
    assert record(prov=dict(PROV, fields=["arrival_card"]))["confidence_level"] != "High"
    assert record(prov=dict(PROV, note=""))["confidence_level"] == "Low"
    assert record(prov=dict(PROV, source_url="https://blog.example.com/"))["confidence_level"] == "Low"
    assert record(prov=dict(PROV, note=""), grounded_ok=True, grounded_fields=["disposition", "permitted_stay_days", "required_documents"])["confidence_level"] == "High"
    assert record(g={k: v for k, v in FREE.items() if not k.startswith("permitted_stay")})["confidence_level"] == "Low"


@pytest.mark.parametrize("patch", [
    {"note": None}, {"note": "  \n"}, {"note": {"claim": "verified"}},
    {"verified_at": None}, {"verified_at": ""}, {"verified_at": "yesterday"},
    {"verified_at": "2026-02-30"}, {"verified_at": "2026-01-01 fabricated"},
    {"verified_at": "2999-01-01"},
])
def test_imported_verdict_provenance_requires_note_and_actual_verification_date(patch):
    prov = dict(PROV, **patch)
    assert record(prov=prov)["confidence_level"] == "Low"
    assert record(prov=prov, grounded_ok=True, grounded_fields=["disposition", "permitted_stay_days", "required_documents"])["confidence_level"] == ("Low" if patch.get("verified_at", "present") in (None, "") else "High")


@pytest.mark.parametrize("field", ["permitted_stay_days", "arrival_card", "official_portal_url",
                                   "application_channel_detail", "processing_time", "new_future_field"])
def test_any_disputed_field_prevents_release(field):
    assert record(disputed_fields=[field])["confidence_level"] == "Low"


def test_model_only_source_label_does_not_claim_verification():
    assert "verified" not in record(prov=None)["data_source"].lower()


def test_official_homepage_alone_cannot_verify_a_productless_exemption():
    g = dict(FREE, source_url="https://www.mofa.go.jp/", visa_products=[])
    assert record(g, prov=None)["confidence_level"] == "Low"
    assert record(g, prov=None, grounded_ok=True, grounded_fields=["disposition", "permitted_stay_days", "required_documents"])["confidence_level"] == "Low"  # Collection date is still missing.
    assert record(g, prov=dict(PROV, verifier="ai"))["confidence_level"] == "High"


@pytest.mark.parametrize("products", [False, True])
def test_exemption_conflict_preserves_fee_for_operator_and_grades_low(products):
    g = dict(FREE, government_fee={"amount": 25, "currency": "USD"})
    if products:
        g["visa_products"] = [{"type": "Tourist e-Visa", "fee": {"amount": 25, "currency": "USD"}}]
    r = record(g)
    assert r["visa_fee_amount"] == 25
    assert r["confidence_level"] == "Low"
    assert r["visa_type_name"] != "No visa needed"
    assert "disposition" in r["_disputed_fields"]


def test_on_arrival_detail_has_same_server_category_for_products_and_route():
    g = dict(FREE, disposition="VISA_REQUIRED", requirement_detail="paper_visa_on_arrival",
             application_channel="on_arrival")
    assert record(g)["visa_requirement"] == "Visa on Arrival"
    g["visa_products"] = [{"type": "Visa on arrival", "fee": {"amount": 25, "currency": "USD"}}]
    assert record(g)["visa_requirement"] == "Visa on Arrival"


@pytest.mark.parametrize("products", [25, {"type": "e-Visa"}, ["e-Visa"]])
def test_malformed_cached_products_cannot_crash_record_grading(products):
    r = record(dict(FREE, disposition="VISA_REQUIRED", visa_products=products))
    assert r["confidence_level"] != "High"


def test_nonfinite_cached_fee_cannot_crash_record_grading():
    r = record(dict(FREE, government_fee={"amount": float("inf"), "currency": "USD"}))
    assert r["visa_fee_amount"] is None
    assert r["confidence_level"] == "Low"


@pytest.mark.parametrize("fields", [
    {"disposition": "visa required"},
    {"disposition": ["VISA_REQUIRED"]},
    {"requirement_detail": "electronic visa"},
    {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "requirement_detail": "evisa"},
    {"government_fee": {"amount": 25, "currency": "USD"}},
    {"visa_products": [{"type": "e-Visa"}]},
    {"disposition": "VISA_REQUIRED", "government_fee": {"amount": "25"}},
    {"disposition": "VISA_REQUIRED", "government_fee": {"amount": -25}},
    {"disposition": "VISA_REQUIRED", "government_fee": {"amount": float("inf")}},
    {"disposition": "VISA_REQUIRED", "visa_products": {"type": "e-Visa"}},
    {"disposition": "VISA_REQUIRED", "visa_products": ["e-Visa"]},
    {"disposition": "VISA_REQUIRED", "visa_products": [{"type": "e-Visa", "fee": "25 USD"}]},
    {"disposition": "VISA_REQUIRED", "visa_products": [{"type": "e-Visa", "requirement_detail": "unknown"}]},
    {"disposition": "VISA_REQUIRED", "visa_products": [{"type": "e-Visa", "source_url": "https://blog.example.com"}]},
])
def test_invalid_decision_or_unanchored_machinery_is_refused(files, fields):
    with pytest.raises(ValueError):
        vo.append_operator_entry(entry(fields))
    assert vo.find(ROUTE) is None


def test_loader_quarantines_only_unsafe_fields_and_retains_raw_evidence(files):
    seed, _ = files
    unsafe = entry({"government_fee": {"amount": 25, "currency": "USD"},
                    "visa_products": [{"type": "e-Visa"}],
                    "processing_time": "3 working days"})
    seed.write_text(json.dumps([unsafe]))
    hit = vo.find(ROUTE)
    assert hit["fields"] == {"processing_time": "3 working days"}
    assert json.loads(seed.read_text())[0] == unsafe
    assert vo.lint_rows([unsafe])[0]["errors"]


def test_verified_detail_anchors_fee_and_defaults_to_ai(files):
    vo.append_operator_entry(entry({"requirement_detail": "evisa",
                                     "government_fee": {"amount": 25, "currency": "USD"}}))
    g, p = vo.apply(FREE, ROUTE)
    assert g["disposition"] == "VISA_REQUIRED"
    assert p["verifier"] == "ai"
    assert p["field_provenance"]["disposition"]["verifier"] == "ai"


def test_malformed_products_do_not_erase_other_verified_facts(files):
    seed, _ = files
    seed.write_text(json.dumps([entry({"disposition": "VISA_REQUIRED",
        "government_fee": {"amount": 25, "currency": "USD"},
        "visa_products": {"type": "e-Visa"}})]))
    hit = vo.find(ROUTE)
    assert hit["fields"] == {"disposition": "VISA_REQUIRED",
                             "government_fee": {"amount": 25, "currency": "USD"}}


def test_human_side_edit_does_not_take_authorship_of_ai_seed_verdict(files):
    seed, _ = files
    seed.write_text(json.dumps([entry({"disposition": "VISA_REQUIRED", "requirement_detail": "evisa"})]))
    vo.append_operator_entry(entry({"government_fee": {"amount": 25, "currency": "USD"}}, verifier="human"))
    _, p = vo.apply(FREE, ROUTE)
    assert p["verifier"] == "ai"
    assert p["field_provenance"]["government_fee"]["verifier"] == "human"
    assert p["field_provenance"]["disposition"]["verifier"] == "ai"


def test_successive_operator_edits_preserve_fields_and_their_authors(files):
    vo.append_operator_entry(entry({"disposition": "VISA_REQUIRED", "requirement_detail": "evisa"}))
    vo.append_operator_entry(entry({"government_fee": {"amount": 25, "currency": "USD"}}, verifier="human"))
    vo.append_operator_entry(entry({"processing_time": "3 working days"}, verifier="human"))
    g, p = vo.apply(FREE, ROUTE)
    assert g["government_fee"]["amount"] == 25
    assert g["processing_time"] == "3 working days"
    assert p["verifier"] == "ai"
    assert p["field_provenance"]["government_fee"]["verifier"] == "human"


def test_operator_cannot_attach_fee_to_verified_exempt_verdict(files):
    vo.append_operator_entry(entry({"disposition": "VISA_EXEMPT"}, verifier="human"))
    with pytest.raises(ValueError, match="positive government fee"):
        vo.append_operator_entry(entry({"government_fee": {"amount": 25, "currency": "USD"}}, verifier="human"))
    assert vo.find(ROUTE)["fields"] == {"disposition": "VISA_EXEMPT"}


def test_ordinary_passport_spellings_share_one_operator_entry(files):
    _, operator = files
    first = entry({"disposition": "VISA_REQUIRED"})
    vo.append_operator_entry(first)
    second = entry({"processing_time": "3 working days"})
    second["route"].update(nationality="zzz", travel_document_type="ordinary_passport")
    vo.append_operator_entry(second)
    assert len(json.loads(operator.read_text())) == 1
    assert vo.find(ROUTE)["fields"] == {"disposition": "VISA_REQUIRED",
                                       "processing_time": "3 working days"}


def test_contradictory_verified_products_survive_for_review_and_hold(files):
    seed, _ = files
    seed.write_text(json.dumps([entry({"disposition": "VISA_EXEMPT",
        "visa_products": [{"type": "e-Visa", "fee": {"amount": 25, "currency": "USD"}}]})]))
    out = kimi_primary.apply_verified_overrides(
        kimi_primary._result("KIMI_PRIMARY", FREE, cached=True, stale=False), ROUTE)
    assert out["review_required"] is True
    assert out["guidance"]["visa_products"][0]["fee"]["amount"] == 25


def test_shipped_effective_overrides_have_valid_vocabulary_and_anchored_fees():
    # The raw seed retains the explicitly reported historical audit backlog.
    # This check guarantees none of those unsafe fields can become effective.
    raw = vo._read_rows(vo.OVERRIDES)
    assert raw
    table = vo._parse_rows(raw, {})
    for key, hit in table.items():
        assert not vo._field_errors(hit["fields"]), key
        assert hit["verifier"] in ("ai", "human")
    # Raw vocabulary errors must be fixed mechanically, never hidden by the
    # quarantine intended solely for unverified route verdicts.
    for problem in vo.lint_rows(raw):
        assert not any("unknown disposition" in e or "unknown requirement_detail" in e or
                       "contradicts disposition" in e for e in problem["errors"])


def test_optional_visitor_visas_never_inherit_eta_procedures_or_evidence():
    g = dict(FREE, disposition="ELECTRONIC_AUTHORIZATION_REQUIRED",
        requirement_detail="eta_electronic_authorization", source_url="https://www.gov.uk/eta",
        processing_time="3 working days", application_channel="online_portal",
        government_fee={"amount": 20, "currency": "GBP"},
        required_documents=["Spanish passport", "ETA approval (digital)"],
        entry_requirements="ETA approval required. Funds not required for ETA.",
        visa_products=[
            {"type": "Electronic Travel Authorisation (ETA)", "fee": {"amount": 20, "currency": "GBP"}},
            {"type": "Standard Visitor visa (6 months)", "fee": {"amount": 135, "currency": "GBP"}},
            {"type": "Long-term Standard Visitor (2 years)", "fee": {"amount": 506, "currency": "GBP"}}])
    rows = tstation.records_for_route(ROUTE, g, PROV, grounded_ok=True, grounded_fields=["disposition", "permitted_stay_days", "required_documents"])
    assert len(rows) == 3
    assert rows[0]["visa_requirement_detail"] == "ETA Electronic Authorization"
    assert rows[0]["processing_min_days"] == 3
    for r in rows[1:]:
        assert r["visa_requirement"] == "Visa Required in Advance"
        assert r["visa_requirement_detail"] == "Paper Visa"
        for k in ("required_documents", "entry_requirements", "processing_min_days",
                  "processing_unit", "application_method", "source_url", "collected_at"):
            assert r[k] is None, (r["visa_type_name"], k, r[k])
        assert r["_separate_permission"]
        assert r["_product_source_verified"] is None
        assert r["confidence_level"] == "Low"
    assert rows[1]["visa_fee_amount"] == 135
    assert rows[2]["visa_fee_amount"] == 506


def test_product_specific_verified_facts_survive_permission_family_separation():
    visa = {"type": "Standard Visitor visa", "requirement_detail": "paper_visa",
            "fee": {"amount": 135, "currency": "GBP"},
            "processing_time": "Usually 3 weeks", "required_documents": ["passport", "supporting documents"],
            "application_channel": "visa_center", "entry_requirements": "Visa valid for visit",
            "source_url": "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa",
            "source_quote": "You should apply online and book an appointment at a visa application centre.",
            "verified_at": "2026-09-09"}
    g = dict(FREE, disposition="ELECTRONIC_AUTHORIZATION_REQUIRED",
             requirement_detail="eta_electronic_authorization", visa_products=[visa],
             processing_time="3 working days", required_documents=["ETA approval"])
    prov = dict(PROV, fields=["disposition", "visa_products"])
    r = record(g, prov)
    assert r["visa_requirement"] == "Visa Required in Advance"
    assert r["required_documents"] == "passport, supporting documents"
    assert (r["processing_min_days"], r["processing_unit"]) == (21, "Calendar Day")
    assert r["application_method"] == "Agency Service"
    assert r["source_url"] == visa["source_url"]
    assert r["_product_source_verified"]["source_url"] == visa["source_url"]


def test_visa_to_eta_correction_clears_unverified_visa_procedure(files):
    seed, _ = files
    seed.write_text(json.dumps([entry({"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
                                      "requirement_detail": "eta_electronic_authorization",
                                      "government_fee": {"amount": 20, "currency": "GBP"}})]))
    old = dict(FREE, disposition="VISA_REQUIRED", requirement_detail="paper_visa",
               processing_time="21 calendar days", application_channel="visa_center",
               required_documents=["visa application", "biometrics", "passport"],
               entry_requirements="HKSAR holders require a Standard Visitor visa.",
               exceptions=["A visa is required."],
               visa_products=[{"type": "Standard Visitor visa", "fee": {"amount": 135, "currency": "GBP"}}])
    g, prov = vo.apply(old, ROUTE)
    assert g["disposition"] == "ELECTRONIC_AUTHORIZATION_REQUIRED"
    for key in ("processing_time", "application_channel", "required_documents", "entry_requirements", "exceptions"):
        assert key not in g
    assert g["government_fee"]["amount"] == 20
    assert g["visa_products"] == old["visa_products"]
    assert g["source_url"] == prov["source_url"]


def test_explicit_evisa_issuance_does_not_mean_eta_or_override_visa_centre():
    g = dict(FREE, disposition="VISA_REQUIRED", requirement_detail="paper_visa",
             application_channel="embassy", visa_products=[
                 {"type": "Standard Visitor visa", "requirement_detail": "evisa",
                  "application_channel": "visa_center", "processing_time": "3 weeks"}])
    r = record(g)
    assert r["visa_requirement_detail"] == "eVisa"
    assert r["visa_requirement"] == "Visa Required in Advance"
    assert r["application_method"] == "Agency Service"
    assert (r["processing_min_days"], r["processing_unit"]) == (21, "Calendar Day")


def test_verified_entry_rules_reach_both_guidance_and_records(files):
    changes = {"disposition": "VISA_REQUIRED", "entry_requirements": "Valid passport for visit",
               "passport_validity": "Valid throughout the visit",
               "passport_validity_requirement": {"kind": "valid_through_departure", "months": 0},
               "biometrics_required": True, "appointment_required": True,
               "onward_travel_evidence": "Return or onward itinerary"}
    vo.append_operator_entry(entry(changes, verifier="human"))
    g, p = vo.apply(FREE, ROUTE)
    for key, value in changes.items():
        assert g[key] == value
        assert p["field_provenance"][key]["verifier"] == "human"
    with pytest.raises(ValueError, match="boolean"):
        vo.append_operator_entry(entry({"biometrics_required": "yes"}))


def test_legacy_text_lists_keep_exact_text_without_splitting(files):
    seed, _ = files
    old = dict(FREE, required_documents=" passport; tickets, if requested ",
               forms="", exceptions="No work. Tourism only.",
               account_registration_steps="Create account", payment_process="Pay online",
               submission_process="Submit; then await decision",
               health_requirements="malformed health shape")
    g, _ = vo.apply(old, ROUTE)
    for key in vo._TEXT_LIST_FIELDS:
        assert g[key] == ([old[key]] if old[key].strip() else [])
    assert old["required_documents"] == " passport; tickets, if requested "
    assert g["health_requirements"] == "malformed health shape"
    seed.write_text(json.dumps([entry({"required_documents":"Source-backed passport sentence"})]))
    vo.reload()
    g, _ = vo.apply(old, ROUTE)
    assert g["required_documents"] == ["Source-backed passport sentence"]
    assert vo._normalise_text_lists({"required_documents":{"bad":"shape"}})["required_documents"] == {"bad":"shape"}


def test_arrival_declaration_uses_actual_timing_and_does_not_require_optional_portal():
    g = dict(FREE, arrival_card={"required":True, "name":"ED and customs declaration",
                                "submission_window":"On arrival; paper forms are accepted",
                                "official_url":"https://www.vjw.digital.go.jp/"},
             application_channel="online_portal", exceptions=["Visit Japan Web is optional"])
    text = tstation._entry_requirements(g)
    assert "On arrival" in text
    assert "before travel" not in text
    assert not tstation._files_something_online(g)
    assert "approved travel authorisation" not in text


def test_authorisation_exemption_text_never_creates_a_requirement():
    g = dict(FREE, application_channel_detail="No eTA required",
             exceptions=["US citizens are exempt from Canada's eTA requirement for air travel."])
    assert not tstation._files_something_online(g)
    assert "approved travel authorisation" not in (tstation._entry_requirements(g) or "")


@pytest.mark.parametrize("text", ["Up to 6 months", "Usually six calendar months, decided on arrival", "Up to 1 year"])
def test_calendar_stays_never_become_exact_day_counts(text):
    # Covers Canada visa-free/eTA and the UK visitor-visa product. Even a
    # historical 180-day cache value cannot replace the precise source text.
    g = dict(FREE, permitted_stay=text, permitted_stay_days=180)
    r = record(g)
    assert (r["max_stay_duration"], r["max_stay_unit"]) == (None, None)
    assert r["max_stay_text"] == text
    assert text in r["special_conditions"]
    assert "calendar" in r["_max_stay_representation_reason"]
    g = dict(g, disposition="VISA_REQUIRED", requirement_detail="evisa",
             visa_products=[{"type":"Standard Visitor visa", "requirement_detail":"evisa",
                             "permitted_stay":text,"max_stay_days":180,
                             "fee":{"amount":135,"currency":"GBP"}}])
    p = record(g)
    assert (p["max_stay_duration"],p["max_stay_unit"]) == (None,None)
    assert text in p["special_conditions"]


def test_explicit_day_and_hour_stays_remain_exact():
    for text, value, unit in (("180 days",180,"Day"),("72 hours",72,"Hour")):
        r = record(dict(FREE,permitted_stay=text,permitted_stay_days=None))
        assert (r["max_stay_duration"],r["max_stay_unit"]) == (value,unit)


def test_conditional_route_without_primary_family_keeps_product_facts_separate():
    g = dict(FREE, disposition="CONDITIONAL", requirement_detail=None,
             required_documents=["Visitor visa biometrics and evidence of ties"],
             processing_time="20 days", entry_requirements="Visitor visa machinery",
             visa_products=[
                 {"type":"Visitor visa", "requirement_detail":"paper_visa",
                  "required_documents":["Visa checklist"],"fee":{"amount":100,"currency":"CAD"}},
                 {"type":"Conditional eTA", "requirement_detail":"eta_electronic_authorization",
                  "required_documents":["Eligible passport"],"fee":{"amount":7,"currency":"CAD"}}])
    visa, eta = tstation.records_for_route(ROUTE,g,PROV)
    assert visa["visa_requirement"] == "Visa Required in Advance"
    assert eta["visa_requirement"] == "Conditional"
    assert visa["required_documents"] == "Visa checklist"
    assert eta["required_documents"] == "Eligible passport"
    for row in (visa,eta):
        assert row["_separate_permission"]
        assert row["processing_min_days"] is None
        assert row["entry_requirements"] is None


@pytest.mark.parametrize("route_detail,product_detail,route_docs", [
    ("evisa","paper_visa",["No invitation needed for the unified eVisa"]),
    ("paper_visa","evisa",["Tourist voucher", "Consular biometrics"]),
])
def test_different_visa_issuance_subtypes_do_not_share_procedures(route_detail, product_detail, route_docs):
    g = dict(FREE, disposition="VISA_REQUIRED", requirement_detail=route_detail,
             required_documents=route_docs, processing_time="4 days",
             entry_requirements="Conditions for the route's primary visa",
             visa_products=[{"type":"Separate tourist visa", "requirement_detail":product_detail,
                             "fee":{"amount":50,"currency":"USD"}}])
    row = record(g)
    assert row["visa_requirement"] == "Visa Required in Advance"
    assert row["_separate_permission"]
    assert row["required_documents"] is None
    assert row["entry_requirements"] is None
    assert row["processing_min_days"] is None


@pytest.mark.parametrize("hours,days", [(24,1),(72,3),(120,5),(12,None),(25,None)])
def test_hour_validity_is_exact_or_preserved_as_text(hours,days):
    text = f"{hours} hours from issuance"
    g = dict(FREE, disposition="VISA_REQUIRED", requirement_detail="paper_visa",
             visa_products=[{"type":"Transit visa", "requirement_detail":"paper_visa",
                             "validity":text,"max_stay_days":None,
                             "fee":{"amount":10,"currency":"USD"}}])
    r = record(g)
    assert (r["validity_duration"],r["validity_unit"]) == (days,"Day" if days is not None else None)
    assert r["validity_text"] == text
    if days is None:
        assert text in r["special_conditions"]
        assert "whole number" in r["_validity_representation_reason"]


def test_binary_grading_requires_support_for_filled_values_not_just_verdict():
    assert record(prov=dict(PROV, verifier='ai'))['confidence_level'] == 'High'
    assert record(prov=dict(PROV, fields=['disposition']))['confidence_level'] == 'Low'
    assert record(prov=None, grounded_ok=True, collected_at='2026-09-09',
                  grounded_fields=['disposition', 'permitted_stay_days', 'required_documents'])['confidence_level'] == 'High'
    assert record(prov=None, grounded_ok=True, collected_at='2026-09-09',
                  grounded_fields=['disposition'])['confidence_level'] == 'Low'
    for verifier in ('ai', 'human', 'public'):
        for complete in (True, False):
            for disputed in (True, False):
                grade = tstation._confidence(FREE, dict(PROV, verifier=verifier),
                                              complete=complete, disputed=disputed)
                assert grade in {'High', 'Low'}
                assert (grade == 'High') == (verifier != 'public' and complete and not disputed)


def test_binary_label_does_not_change_existing_publication_evidence_gate():
    # Previously Medium: source-backed verdict, but no verification of stay.
    incomplete_review = record(prov=dict(PROV, fields=['disposition']))
    assert incomplete_review['confidence_level'] == 'Low'
    assert incomplete_review['_evidence_low'] is False
    assert record(prov=None)['_evidence_low'] is True
    assert record(disputed_fields=['permitted_stay_days'])['_evidence_low'] is True
