from copy import deepcopy

import pytest

from app.visa_snapshot import kimi_primary as kp, permission_eligibility as pe, verified_overrides as vo


def route(nationality="THA", destination="AUS", document="ordinary_passport"):
    return {"passport_nationality": nationality, "destination_country": destination,
            "travel_purpose": "tourism", "travel_document_type": document}


def eta():
    return {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
            "requirement_detail": "eta_electronic_authorization",
            "visa_category": "Electronic Travel Authority (601)", "confidence": "high",
            "source_url": pe.PROGRAMS[0].source_url, "application_channel": "online_portal",
            "government_fee": {"amount": 20, "currency": "AUD"}}


@pytest.mark.parametrize("nationality", ["THA", "IDN", "VNM", "IND", "PHL"])
def test_internally_consistent_eta_still_held_for_ineligible_passport(monkeypatch, nationality):
    monkeypatch.setattr(vo, "find", lambda _: None)
    g = eta()
    assert kp.serve_time_invariants(g) == []  # A fee/label consistency check cannot catch eligibility.
    original = deepcopy(g)
    out = kp._result(kp.STATUS_PRIMARY, g, cached=True, stale=False, released=True)
    out = kp.apply_verified_overrides(out, route(nationality))
    assert out["held"] and out["review_required"]
    assert any("product eligibility:" in issue for issue in out["contradictions"])
    assert out["apply_steps"] == out["workflow_plan"] == []
    assert g == original


def test_invalid_alternate_is_checked_even_with_eligible_primary():
    g = {"disposition": "VISA_REQUIRED", "visa_category": "Visitor 600",
         "visa_products": [{"type": "Visitor (600)"}, {"type": "ETA (601)"}]}
    assert pe.issues(g, route())
    g["visa_products"].pop()
    # A citation explaining the exclusion is not a recommendation to apply.
    g["source_url"] = pe.PROGRAMS[0].source_url
    assert pe.issues(g, route()) == []


@pytest.mark.parametrize("nationality", ["JPN", "HKG", "GBR", "MYS", "USA"])
def test_list_membership_does_not_add_verification_or_grant_a_visa(nationality):
    g = eta()
    annotated = pe.annotate(g, route(nationality))
    assert annotated == g
    assert "source_verified" not in annotated
    assert pe.issues(g, route(nationality, destination="CAN")) == []


@pytest.mark.parametrize("document", ["official_passport", "diplomatic_passport", "service_passport"])
def test_taiwan_official_document_exclusion(document):
    assert pe.issues(eta(), route("TWN", document=document))
    assert pe.issues(eta(), route("TWN")) == []


def test_recomputation_removes_old_hold_after_product_correction():
    rejected = pe.annotate(eta(), route())
    rejected.update(disposition="VISA_REQUIRED", requirement_detail="evisa", visa_category="Visitor 600")
    assert "_permission_eligibility_issues" not in pe.annotate(rejected, route())


def test_operator_cannot_save_invalid_product_or_change_file(monkeypatch, tmp_path):
    path = tmp_path / "operator.json"
    path.write_text("[]\n")
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(path))
    monkeypatch.setenv("ELLIS_DATA_DIR", str(tmp_path))
    vo.reload()
    entry = {"route": {"nationality": "THA", "destination": "AUS", "travel_purpose": "tourism"},
             "verified_at": "2026-09-09", "verified_by": "AI regression",
             "verifier": "ai", "source_url": pe.PROGRAMS[0].source_url,
             "note": "Source review must not bypass the published eligibility list.", "fields": eta()}
    entry["fields"].pop("confidence")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="product eligibility"):
        vo.append_operator_entry(entry)
    assert path.read_bytes() == before
    vo.reload()


def test_malaysian_russia_correction_exposes_only_supported_products(monkeypatch, tmp_path):
    from pathlib import Path
    from app.visa_snapshot.records_guard import apply_records_hold
    monkeypatch.setattr(vo, "OVERRIDES", Path(__file__).resolve().parents[2] / "data/database_seed/verified_overrides.json")
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    vo.reload()
    rt = route("MYS", "RUS")
    raw = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
           "visa_category": "No visa needed", "permitted_stay": "30 days", "permitted_stay_days": 30,
           "government_fee": {"amount": 0, "currency": None}, "visa_products": [], "confidence": "high"}
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, raw, cached=True, stale=False), rt)
    out = apply_records_hold(rt, out)
    assert not out["held"]  # Binary grading preserves the existing source/conflict release boundary.
    g = out["guidance"]
    assert g["disposition"] == "VISA_REQUIRED" and g["requirement_detail"] == "evisa"
    assert g["permitted_stay_days"] == 30
    assert len(g["visa_products"]) == 1
    product = g["visa_products"][0]
    assert product["entry"] == "single" and "120 days" in product["validity"]
    assert product["source_url"] == "https://evisa.kdmid.ru/"
    assert g["government_fee"] is None  # Unverified amount is never a free visa.
    vo.reload()


@pytest.mark.parametrize('document', [
    'identity_certificate', 'certificate_of_identity', 'document_of_identity',
    'refugee_travel_document', 'stateless_travel_document', 'prc_travel_document',
    'laissez_passer', 'non_citizen_passport', 'noncitizen_passport',
    'alien_passport', 'emergency_travel_document', ' Certificate of Identity ',
])
def test_eta_rejects_known_nonpassport_documents_for_listed_nationality(monkeypatch, document):
    monkeypatch.setattr(vo, 'find', lambda _: None)
    rt = route('HKG', document=document)
    assert pe.issues(eta(), rt)
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, eta(),
        cached=True, stale=False, released=True), rt)
    assert out['held'] and out['review_required']
    assert any('non-citizen passports' in issue for issue in out['contradictions'])
    assert out['apply_steps'] == out['workflow_plan'] == []


@pytest.mark.parametrize('document', ['ordinary_passport', 'child_passport',
    'bno_passport', 'british_national_overseas_passport', 'British National Overseas Passport'])
def test_explicit_bno_passports_are_not_treated_as_nonpassport_documents(document):
    assert pe.issues(eta(), route('GBR', document=document)) == []


def test_operator_recomputes_stale_marker_but_preserves_unrelated_conflicts(monkeypatch, tmp_path):
    import json
    path = tmp_path / 'operator.json'; path.write_text('[]\n')
    seed = tmp_path / 'seed.json'; seed.write_text('[]\n')
    monkeypatch.setattr(vo, 'OVERRIDES', seed)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(path))
    vo.reload()
    rt = route()
    raw = pe.annotate(eta(), rt)
    original = deepcopy(raw)
    fields = {'disposition':'VISA_REQUIRED','requirement_detail':'evisa',
              'visa_category':'Visitor visa (subclass 600)', 'visa_products':[],
              'government_fee':{'amount':250,'currency':'AUD','qualifier':'from'},
              'application_channel':'online_portal'}
    entry = {'route':{'nationality':'THA','destination':'AUS','travel_purpose':'tourism'},
             'verified_at':'2026-09-09','verified_by':'AI regression','verifier':'ai',
             'source_url':'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/visitor-600/tourist-stream-overseas',
             'note':'Official Visitor600 stream replacement for an ineligible ETA.','fields':fields}
    try:
        vo.append_operator_entry(entry, guidance=raw)
        assert len(json.loads(path.read_text())) == 1
        assert raw == original
        g, _ = vo.apply(raw, rt)
        assert '_permission_eligibility_issues' not in g
        before = path.read_bytes()
        # Recomputing the private marker cannot erase a malformed substantive
        # field in the original answer or write half of the correction.
        with pytest.raises(ValueError, match='health_requirements'):
            vo.append_operator_entry(entry, guidance=dict(raw, health_requirements={'unsupported':'shape'}))
        assert path.read_bytes() == before
        invalid = deepcopy(entry)
        invalid['fields']['visa_products']=[{'type':'ETA (601)','requirement_detail':'eta_electronic_authorization'}]
        with pytest.raises(ValueError, match='product eligibility'):
            vo.append_operator_entry(invalid, guidance=raw)
        assert path.read_bytes() == before
    finally:
        vo.reload()


def test_operator_cannot_authorise_eta_for_certificate_of_identity(monkeypatch, tmp_path):
    path = tmp_path / 'operator.json'; path.write_text('[]\n')
    seed = tmp_path / 'seed.json'; seed.write_text('[]\n')
    monkeypatch.setattr(vo, 'OVERRIDES', seed)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(path))
    vo.reload()
    fields = eta(); fields.pop('confidence')
    entry = {'route':{'nationality':'HKG','destination':'AUS','travel_purpose':'tourism',
                      'travel_document_type':'identity_certificate'},
             'verified_at':'2026-09-09','verified_by':'Operator fixture','verifier':'human',
             'source_url':pe.PROGRAMS[0].source_url,
             'note':'A human attribution cannot override the explicit document exclusion.','fields':fields}
    before = path.read_bytes()
    try:
        with pytest.raises(ValueError, match='non-citizen passports'):
            vo.append_operator_entry(entry)
        assert path.read_bytes() == before
    finally:
        vo.reload()


@pytest.mark.parametrize('detail', [None, 'unconditional_visa_free', 'eta_electronic_authorization'])
def test_indonesian_korea_independent_tourism_cannot_be_visa_free_or_keta(monkeypatch, detail):
    monkeypatch.setattr(vo, 'find', lambda _: None)
    g = {'disposition': 'VISA_EXEMPT', 'requirement_detail': detail,
         'permitted_stay_days': 90, 'government_fee': {'amount': 0, 'currency': 'USD'},
         'confidence': 'high', 'source_url': 'https://www.k-eta.go.kr/'}
    rt = route('IDN', 'KOR')
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, g,
        cached=True, stale=False, released=True), rt)
    assert out['held'] and out['review_required']
    assert any('Indonesian ordinary-passport' in s for s in out['contradictions'])


def test_indonesia_korea_guard_keeps_c39_and_conditional_exemptions_distinct():
    rt = route('IDN', 'KOR')
    visa = {'disposition': 'VISA_REQUIRED', 'requirement_detail': 'paper_visa',
            'visa_category': 'Short-term tourist visa (C-3-9)'}
    assert not pe.issues(visa, rt)
    for detail in ('conditional_visa_free', 'transit_visa_free'):
        assert not pe.issues({'disposition': 'CONDITIONAL', 'requirement_detail': detail}, rt)
    assert pe.issues(dict(visa, visa_products=[{'type': 'K-ETA'}]), rt)
    # guard-20260912 T6 rule 3: Malaysia IS on the established K-ETA list, so a
    # bare exemption is not unconditional; the one issue names the scheme,
    # never the Indonesia branch, and a stated condition is silent.
    mys = pe.issues({'disposition': 'VISA_EXEMPT'}, route('MYS', 'KOR'))
    assert len(mys) == 1 and mys[0].startswith('KOR operates') and 'Indonesian' not in mys[0]
    assert not pe.issues({'disposition': 'VISA_EXEMPT', 'requirement_detail': 'conditional_visa_free'},
                         route('MYS', 'KOR'))
    assert not pe.issues({'disposition': 'VISA_EXEMPT'}, route('IDN', 'KOR', 'diplomatic_passport'))


def test_shipped_indonesia_korea_rule_replaces_old_visa_free_answer(monkeypatch):
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', '/nonexistent/indonesia-regression.json')
    vo.reload()
    old = {'disposition': 'VISA_EXEMPT', 'permitted_stay_days': 90, 'confidence': 'high'}
    for extra in ({}, {'arrival_date': '2026-12-01'}, {'lawful_country_of_residence': 'SGP'}):
        rt = dict(route('IDN', 'KOR'), **extra)
        out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, old,
            cached=True, stale=False), rt)
        assert out['guidance']['disposition'] == 'VISA_REQUIRED'
        assert 'C-3-9' in out['guidance']['visa_category']
        assert 'independent tourists' in ' '.join(out['guidance']['exceptions'])
    vo.reload()



# ---------------------------------------------------------------------------
# guard-20260912 T6: the registry path grades Australia exactly as the legacy
# catalogue did, for all 33 listed and five unlisted nationalities.
# ---------------------------------------------------------------------------

def test_australia_behaviour_is_unchanged_by_the_migration(tmp_path, monkeypatch):
    import json
    from app.visa_snapshot import scheme_registry as sr
    listed = sorted(pe._LEGACY_AUS_ETA.eligible_nationalities)
    unlisted = ["THA", "IDN", "VNM", "IND", "PHL"]
    shapes = [eta(), {"disposition": "VISA_REQUIRED", "visa_category": "Visitor 600",
                      "visa_products": [{"type": "Visitor (600)"}, {"type": "ETA (601)"}]},
              {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "visa_category": "Electronic Travel Authority"}]
    docs = ["ordinary_passport", "identity_certificate", "diplomatic_passport"]
    sr.reload()
    assert pe._entries_for("AUS")[0]["id"] == "aus_eta_601" and not pe._entries_for("AUS")[0].get("legacy")
    registry_path = {(n, d, i): pe.issues(g, route(n, "AUS", d)) for n in listed + unlisted
                     for d in docs for i, g in enumerate(shapes)}
    path = tmp_path / "scheme_lists.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(path))
    sr.reload()
    try:
        assert pe._entries_for("AUS")[0].get("legacy") is True
        legacy_path = {(n, d, i): pe.issues(g, route(n, "AUS", d)) for n in listed + unlisted
                       for d in docs for i, g in enumerate(shapes)}
    finally:
        sr.reload()
    strip = lambda msgs: [m.split(" (list checked")[0] for m in msgs]
    for key in registry_path:
        assert strip(registry_path[key]) == strip(legacy_path[key]), key
    # Listed nationalities pass on an ordinary passport, unlisted ones are refused.
    for n in listed:
        assert registry_path[(n, "ordinary_passport", 0)] == []
    for n in unlisted:
        assert registry_path[(n, "ordinary_passport", 0)]
    # The document exclusion and the Taiwan official-passport rule survive.
    assert "non-citizen passports" in registry_path[("JPN", "identity_certificate", 0)][0]
    assert "Taiwan official and diplomatic passports" in registry_path[("TWN", "diplomatic_passport", 0)][0]
