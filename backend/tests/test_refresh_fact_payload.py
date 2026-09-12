"""Official comparisons retain policy scope without resending review archives."""
from copy import deepcopy
import json

from app.visa_snapshot import freshness
from app.visa_snapshot.models import DatabaseIssueReport
from .test_freshness_comparison_reuse import h, seed, run, URL, TEXT


def guidance():
    return {
        'disposition': 'VISA_REQUIRED', 'source_url': URL,
        'application_channel': 'authorised_agent',
        'visa_products': [{
            'type': 'Tourist visa', 'entry': 'single', 'validity': '3 months',
            'max_stay_days': 30, 'requirement_detail': None,
            'fee': {'amount': 715, 'currency': 'CNY',
                    'note': 'Chinese nationals applying in mainland China; agency fee not included.'},
            'notes': 'Apply through the mission-designated agency. Stay is 15 or 30 days, as granted.',
            'field_provenance': {'fee': {'quote': 'Old fee archive ' * 2000,
                'subject': {'travel_document_type': 'ordinary_passport'},
                'capture_file': '/private/local/capture.json'}},
        }],
    }


def test_route_comparison_retains_every_product_value_without_resending_archives(h):
    original = guidance(); row = seed(h, deepcopy(original))
    run(h, row)
    sent = h.calls[0]['stored_answer']; expected = deepcopy(original)
    expected['visa_products'][0].pop('field_provenance')
    # Preserve the existing set of fields allowed into a comparison.
    expected = {k: v for k, v in expected.items() if k in freshness.OVERRIDABLE}
    assert sent == expected
    assert row.guidance == original
    assert h.calls[0]['official_page_text'] == TEXT
    assert len(json.dumps(sent)) < len(json.dumps(original)) / 5
    assert 'private/local' not in json.dumps(h.calls[0])
    assert row.fresh_until is None


def test_nested_proof_change_invalidates_reuse_even_when_prompt_facts_match(h):
    row = seed(h, guidance()); run(h, row)
    assert len(h.calls) == 1
    changed = deepcopy(row.guidance)
    changed['visa_products'][0]['field_provenance']['fee']['quote'] = 'Reviewed fee changed'
    row.guidance = changed; h.db.commit(); run(h, row)
    assert len(h.calls) == 2
    assert h.calls[0]['stored_answer'] == h.calls[1]['stored_answer']
    assert row.verification['grounded_check']['model_comparisons_reused'] == 0
    assert row.guidance == changed


def test_issue_research_uses_same_facts_and_keeps_original_route_and_issue_status(h):
    row = seed(h, guidance())
    issue = DatabaseIssueReport(org_id='platform', cache_key=row.cache_key,
        route=deepcopy(row.route), field='disposition', note='Check the visa requirement',
        reported_by='test-reader', status='open')
    h.db.add(issue); h.db.commit()
    original = deepcopy(row.guidance)
    result = freshness.propose_for_issue(h.db, issue.id)
    assert h.calls[0]['stored_answer'] == freshness._comparison_facts(original)
    assert 'field_provenance' not in json.dumps(h.calls[0]['stored_answer'])
    assert h.calls[0]['flag_from_reader']['note'] == issue.note
    assert row.guidance == original and issue.status == 'open'
    assert result['outcome'] == 'checked'


def test_compacting_does_not_drop_unknowns_conditions_or_modify_input():
    original = guidance()
    original['government_fee'] = {'amount': None, 'currency': None,
        'note': 'Fee waiver only for applicants under six; service charges may still apply.'}
    original['required_documents'] = ['Ordinary passport', {'condition': 'Residents only',
        'items': ['Valid residence permit'], 'field_provenance': {'quote': 'An archive'}}]
    before = deepcopy(original); result = freshness._comparison_facts(original)
    assert result['government_fee'] == original['government_fee']
    assert result['required_documents'] == ['Ordinary passport',
        {'condition': 'Residents only', 'items': ['Valid residence permit']}]
    assert result['visa_products'][0]['requirement_detail'] is None
    result['visa_products'][0]['notes'] = 'changed prompt copy'
    assert original == before
