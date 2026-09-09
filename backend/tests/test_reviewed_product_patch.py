import copy

import pytest

from scripts.prepare_reviewed_product_patch import PatchRejected, digest, prepare


ROUTE = {"passport_nationality": "IDN", "destination_country": "FRA",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02009R0810-20240628"
SOURCES = {"code": {"ok": True, "url": URL, "checked_at": "2026-09-09",
                    "text": "The visa fee shall be waived for children under six years."}}


def fixture():
    g = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
         "operator_released": False, "pending_issue": "unrelated passport review",
         "visa_products": [{"type": "Schengen C child under 6",
                            "fee": {"amount": 90, "currency": "EUR"}},
                           {"type": "Schengen C adult", "fee": {"amount": 90, "currency": "EUR"}}]}
    proof = {"verifier": "ai", "scope_note": "Indonesian ordinary tourism applicant under six; only the visa fee is waived.",
             "evidence": [{"source_id": "code", "source_url": URL,
                           "quote": "The visa fee shall be waived for children under six years."}]}
    e = {"route": ROUTE, "reviewed_at": "2026-09-09", "fields": {}, "expected_fields": {},
         "product_patches": [{"match": {"type": g["visa_products"][0]["type"]},
                              "expected_product_sha256": digest(g["visa_products"][0]),
                              "fields": {"fee": {"amount": 0, "currency": "EUR"}},
                              "field_provenance": {"fee": proof}}]}
    return g, e


def test_child_fee_waiver_preserves_visa_requirement_other_products_and_holds():
    g, entry = fixture()
    before = copy.deepcopy(g)
    result = prepare(g, ROUTE, entry, SOURCES)
    assert result["guidance"]["visa_products"][0]["fee"]["amount"] == 0
    assert result["guidance"]["disposition"] == "VISA_REQUIRED"
    assert result["guidance"]["visa_products"][1] == before["visa_products"][1]
    assert result["guidance"]["pending_issue"] == before["pending_issue"]
    assert not result["guidance"]["operator_released"]
    assert not result["source_review"]["renew_fresh_until"]
    assert g == before


@pytest.mark.parametrize("key,value", [("passport_nationality", "CHN"),
                                       ("destination_country", "ESP"),
                                       ("travel_purpose", "work"),
                                       ("travel_document_type", "diplomatic_passport")])
def test_exact_route_document_and_purpose_are_required(key, value):
    g, entry = fixture()
    with pytest.raises(PatchRejected, match="Nationality"):
        prepare(g, {**ROUTE, key: value}, entry, SOURCES)


def test_concurrent_product_change_rejects_atomically():
    g, entry = fixture()
    g["visa_products"][0]["notes"] = "Newer operator correction"
    before = copy.deepcopy(g)
    with pytest.raises(PatchRejected, match="Product changed"):
        prepare(g, ROUTE, entry, SOURCES)
    assert g == before


def test_duplicate_product_names_are_not_guessed():
    g, entry = fixture()
    g["visa_products"].append(copy.deepcopy(g["visa_products"][0]))
    with pytest.raises(PatchRejected, match="ambiguous"):
        prepare(g, ROUTE, entry, SOURCES)


def test_changed_route_field_is_not_overwritten():
    g, entry = fixture()
    entry["fields"] = {"source_url": URL}
    entry["expected_fields"] = {"source_url": {"present": False, "value": None}}
    entry["field_provenance"] = {"source_url": copy.deepcopy(entry["product_patches"][0]["field_provenance"]["fee"])}
    g["source_url"] = "https://www.france-visas.gouv.fr/en/etudiant"
    with pytest.raises(PatchRejected, match="Route field changed"):
        prepare(g, ROUTE, entry, SOURCES)


@pytest.mark.parametrize("change", ["unreadable", "wrong_url", "absent_quote", "missing_proof"])
def test_field_proof_must_belong_to_an_actual_read_source(change):
    g, entry = fixture()
    sources = copy.deepcopy(SOURCES)
    if change == "unreadable":
        sources["code"]["ok"] = False
    elif change == "wrong_url":
        sources["code"]["url"] = "https://www.france-visas.gouv.fr/en/etudiant"
    elif change == "absent_quote":
        sources["code"]["text"] = "Visa navigation page"
    else:
        entry["product_patches"][0]["field_provenance"] = {}
    with pytest.raises(PatchRejected):
        prepare(g, ROUTE, entry, sources)


def test_unresolved_umbrella_work_product_cannot_be_prepared_for_publication():
    g, entry = fixture()
    entry["publication_blocked"] = ["ICT posted employee and talent local-contract mission must be distinguished"]
    with pytest.raises(PatchRejected, match="Unresolved product"):
        prepare(g, ROUTE, entry, SOURCES)


def test_unknown_detail_is_null_not_a_fabricated_filled_value():
    g, entry = fixture()
    patch = entry["product_patches"][0]
    patch["fields"]["processing_time"] = None
    patch["field_provenance"]["processing_time"] = {"verifier": "ai", "status": "unknown",
                                                    "reason": "No fixed long-stay processing time published"}
    assert prepare(g, ROUTE, entry, SOURCES)["guidance"]["visa_products"][0]["processing_time"] is None
    patch["fields"]["processing_time"] = "28 days"
    with pytest.raises(PatchRejected, match="Unknown values must be null"):
        prepare(g, ROUTE, entry, SOURCES)


def test_product_patch_cannot_release_or_rename_a_record():
    g, entry = fixture()
    entry["product_patches"][0]["fields"]["operator_released"] = True
    with pytest.raises(PatchRejected, match="cannot release"):
        prepare(g, ROUTE, entry, SOURCES)


def test_unrelated_existing_product_provenance_is_preserved():
    g, entry = fixture()
    g['visa_products'][0]['field_provenance'] = {'required_documents': {'verifier': 'human', 'note': 'Prior distinct review'}}
    entry['product_patches'][0]['expected_product_sha256'] = digest(g['visa_products'][0])
    result = prepare(g, ROUTE, entry, SOURCES)
    assert result['guidance']['visa_products'][0]['field_provenance']['required_documents'] == g['visa_products'][0]['field_provenance']['required_documents']


def test_unrelated_literal_quote_does_not_prove_a_numeric_fee():
    g, entry = fixture()
    entry['product_patches'][0]['fields']['fee'] = {'amount': 999, 'currency': 'EUR'}
    with pytest.raises(PatchRejected, match='Numeric field'):
        prepare(g, ROUTE, entry, SOURCES)


def test_changed_source_url_must_be_one_of_its_actual_read_sources():
    g, entry = fixture()
    entry['fields'] = {'source_url': 'https://eviza.mae.ro/TypeOfVisa'}
    entry['expected_fields'] = {'source_url': {'present': False, 'value': None}}
    entry['field_provenance'] = {'source_url': entry['product_patches'][0]['field_provenance']['fee']}
    with pytest.raises(PatchRejected, match='itself been read'):
        prepare(g, ROUTE, entry, SOURCES)


def actual_manifest():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'data/database_seed/reviewed_schengen_products_2026_09_09.json'
    m = json.loads(path.read_text())
    return m, {s['id']: s for s in m['sources']}


@pytest.mark.parametrize('index', range(17))
def test_every_actual_route_prepares_exact_current_layers_or_explicitly_blocks(index):
    from scripts.prepare_reviewed_product_patch import prepare_layers, _proofs
    m, sources = actual_manifest()
    e = m['routes'][index]
    b = e['baseline']
    before = copy.deepcopy(b)
    _proofs(e['fields'], e['field_provenance'], sources)
    for p in e['product_patches']:
        _proofs(p['fields'], p['field_provenance'], sources)
    args = (b['raw_guidance'], b['effective_guidance'], e['route'], e, sources,
            [x['entry'] for x in b['override_entries']])
    if e['publication_blocked']:
        assert e['route']['travel_purpose'] == 'work'
        with pytest.raises(PatchRejected, match='Unresolved product'):
            prepare_layers(*args)
    else:
        out = prepare_layers(*args)
        original = b['effective_guidance']
        candidate = out['guidance']
        assert [p['type'] for p in candidate.get('visa_products', [])] == e['retained_original_products']
        for key in set(original) - set(e['fields']) - {'visa_products'}:
            assert candidate[key] == original[key]
        assert not any(out['source_review'][key] for key in ['new_grounded_check', 'new_release', 'renew_fresh_until'])
    assert b == before


def test_actual_manifest_covers_all43_original_rows_and_preserves_raw_and_effective_products():
    m, _ = actual_manifest()
    assert len(m['routes']) == 17
    assert sorted(i for e in m['routes'] for i in e['original_backlog_rows']) == list(range(43))
    assert sum(len(e['retained_original_products']) for e in m['routes']) == 42
    assert sum(len(e['retained_original_raw_products']) for e in m['routes']) == 41
    assert sum(bool(e['publication_blocked']) for e in m['routes']) == 1


@pytest.mark.parametrize('changed_layer', ['raw', 'merged', 'override'])
def test_actual_manifest_rejects_changed_raw_overlay_or_effective_layer(changed_layer):
    from scripts.prepare_reviewed_product_patch import prepare_layers
    m, sources = actual_manifest()
    e = next(e for e in m['routes'] if e['route']['passport_nationality'] == 'IDN')
    raw = copy.deepcopy(e['baseline']['raw_guidance'])
    merged = copy.deepcopy(e['baseline']['effective_guidance'])
    overrides = [copy.deepcopy(x['entry']) for x in e['baseline']['override_entries']]
    if changed_layer == 'raw': raw['permitted_stay'] = 'A newer independently reviewed rule'
    elif changed_layer == 'merged': merged['hold_reasons'] = ['New unresolved material dispute']
    else: overrides[0]['note'] += ' Newer review'
    with pytest.raises(PatchRejected, match='changed since'):
        prepare_layers(raw, merged, e['route'], e, sources, overrides)


def test_actual_child_product_has_own_passport_and_fee_evidence_and_still_requires_visa():
    m, sources = actual_manifest()
    e = next(e for e in m['routes'] if e['route']['passport_nationality'] == 'IDN')
    child = next(p for p in e['product_patches'] if 'child under 6' in p['match']['type'])
    assert child['fields']['disposition'] == 'VISA_REQUIRED'
    assert child['fields']['requirement_detail'] == 'paper_visa'
    assert child['fields']['fee']['amount'] == 0
    assert 'required_documents' in child['fields']
    rule = child['field_provenance']['disposition']['evidence']
    assert any('\nIndonesia\n' in p['quote'] for p in rule)
    assert 'eu-code' in {p['source_id'] for p in child['field_provenance']['fee']['evidence']}
    assert 'fr-short' in {p['source_id'] for p in rule}


def test_actual_student_products_do_not_inherit_each_others_fees_times_or_residence_charges():
    m, sources = actual_manifest()
    e = next(e for e in m['routes'] if e['route']['travel_purpose'] == 'study')
    out = prepare(e['baseline']['effective_guidance'], e['route'], e, sources)['guidance']
    short, long = out['visa_products']
    assert short['fee']['amount'] == 0 and '45 calendar days' in short['processing_time']
    assert long['fee'] is None and long['processing_time'] is None and long['max_stay_days'] is None
    assert 'EUR99' in long['notes'] and 'EUR50 only' in long['notes']
    assert 'EUR100 tax' in long['notes'] and 'EUR877.50/month' in long['notes']
    assert 'State-agreement' in long['notes']
    assert '99' not in short['notes'] and '100' not in short['notes']
    assert not any('short-stay' in x.lower() for x in long['required_documents'])
    assert '615' not in out['financial_evidence']
    assert not any('250' in x for x in out['exceptions'])


def test_actual_direct403_attempts_are_separate_from_web_observations_and_never_grounded():
    m, sources = actual_manifest()
    web = [s for s in sources.values() if s.get('direct_origin_attempt')]
    assert len(web) == 6
    assert all(not s['direct_origin_attempt']['ok'] for s in web)
    assert all(s['origin_http_status'] is None and not s['origin_same_day_read_confirmed'] for s in web)
    assert all(s['reading_method'] == 'official_web_tool_extracted_text' for s in web)
    assert all(not e['new_grounded_check'] and not e['new_operator_release'] and not e['renew_fresh_until'] for e in m['routes'])


def test_actual_conditional_circulation_and_russian_scope_are_preserved():
    m, _ = actual_manifest()
    sen = next(e for e in m['routes'] if e['route']['passport_nationality'] == 'SEN')
    assert len(sen['product_patches']) == 7
    assert all('not a guaranteed' in p['fields']['notes'] for p in sen['product_patches'] if 'multiple' in p['match']['type'])
    rus = next(e for e in m['routes'] if e['route']['passport_nationality'] == 'RUS')
    multiple = next(p for p in rus['product_patches'] if p['fields']['entry'] == 'multiple')
    assert 'resident in Russia' in multiple['fields']['notes']
    assert 'not a worldwide ban' in multiple['fields']['notes']
