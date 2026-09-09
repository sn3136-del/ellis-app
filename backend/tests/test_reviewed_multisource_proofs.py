"""Secondary pages must be literal, current and from the destination authority."""
from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('multisource_materializer', Path(__file__).parents[1] / 'scripts/materialize_reviewed_routes.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def fixture():
    a = {'id': 'rule', 'url': 'https://www.mofa.go.jp/rule', 'checked_at': '2026-09-09', 'text': 'Eligible visitors need no visa.'}
    b = {'id': 'duration', 'url': 'https://www.mofa.go.jp/duration', 'checked_at': '2026-09-09', 'text': 'The authorized stay is 30 days.'}
    proof = {'verifier': 'ai', 'verified_at': '2026-09-09', 'source_id': 'rule', 'source_url': a['url'],
             'quote': a['text'], 'note': 'Synthetic evidence for two separately read official pages.',
             'supporting_evidence': [{'source_id': 'duration', 'source_url': b['url'], 'quote': b['text']}]}
    return proof, {'rule': a, 'duration': b}

def check(proof, sources):
    mod._proof('permitted_stay_days', 30, proof, sources,
               {'passport_nationality': 'CAN', 'destination_country': 'JPN', 'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'}, date(2026, 9, 9))

def test_distinct_official_pages_support_one_reviewed_field():
    check(*fixture())

@pytest.mark.parametrize('defect', ['fabricated_quote', 'different_url', 'old_capture', 'wrong_authority', 'missing_source'])
def test_secondary_proof_cannot_bypass_source_contract(defect):
    proof, sources = fixture()
    item = proof['supporting_evidence'][0]
    if defect == 'fabricated_quote': item['quote'] = 'The authorized stay is 300 days.'
    if defect == 'different_url': item['source_url'] = 'https://www.mofa.go.jp/another-page'
    if defect == 'old_capture': sources['duration']['checked_at'] = '2026-08-01'
    if defect == 'wrong_authority': item['source_url'] = sources['duration']['url'] = 'https://www.canada.ca/duration'
    if defect == 'missing_source': item['source_id'] = 'absent'
    with pytest.raises(ValueError): check(proof, sources)
