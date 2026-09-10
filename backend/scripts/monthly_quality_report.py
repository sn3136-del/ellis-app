"""Snapshot operator quality metrics without treating filled cells or links as proof.

Run monthly on the Ellis host. Credentials come only from deployed settings;
the report contains aggregate counts and never credentials or source payloads.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.visa_snapshot.tstation import CONTRACT_FIELDS, FIELD_ORDER, REQUIRED_FIELDS, acceptance_summary

REPORT_DIR = Path('/var/lib/ellis/quality-reports')
RECORDS_URL = 'http://127.0.0.1:8000/database/records'
CHECKED = frozenset({'human-quote', 'ai-quote', 'grounded-consistent'})
STATES = ('filled', 'pending-review', 'missing', 'not-applicable', 'not-published', 'optional-empty')


def _credentials(config):
    token = str(config.admin_token or '').strip()
    user = str(config.admin_user_id or '').strip()
    if (not config.require_secure_admin or len(token) < 32
            or token in {'admin-token', 'dev-token', str(config.dev_api_token or '')} or not user):
        raise ValueError('A configured private operator credential and bound identity are required')
    return token, user


def fetch_records(config) -> dict:
    token, user = _credentials(config)
    # A fixed loopback URL, disabled redirects and ignored proxy environment
    # keep the private credential on this host. HTTP errors are never printed.
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        response = client.get(RECORDS_URL, headers={'Authorization': 'Bearer ' + token,
            'X-Org-Id': 'tripcom', 'X-User-Id': user})
        response.raise_for_status()
        return response.json()


def quality_metrics(payload: dict, at: datetime) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('The records API did not return an object')
    records, fields, required, summary = (payload.get(k) for k in
        ('records', 'fields', 'required_fields', 'summary'))
    if (not isinstance(records, list) or not isinstance(fields, list) or not fields
            or not all(isinstance(f, str) for f in fields) or len(fields) != len(set(fields))
            or set(fields) not in (set(CONTRACT_FIELDS), set(FIELD_ORDER))
            or not isinstance(required, list) or not required
            or not all(isinstance(f, str) for f in required) or len(required) != len(set(required))
            or set(required) != REQUIRED_FIELDS
            or not isinstance(summary, dict) or summary.get('total') != len(records)):
        raise ValueError('The records API schema or record count is incomplete')
    all_counts, required_counts, confidence = Counter(), Counter(), Counter()
    complete = source_links = source_checked = held = review = pending_records = 0
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('A record is malformed')
        statuses = row.get('field_status')
        if (not isinstance(statuses, dict) or not set(fields) <= statuses.keys()
                or any(statuses[f] not in STATES for f in fields)
                or not isinstance(row.get('source_check'), str)
                or not isinstance(row.get('held'), bool)
                or not isinstance(row.get('review_required'), bool)):
            raise ValueError('A record has incomplete quality metadata')
        grade = str(row.get('confidence_level') or '').lower()
        if grade not in {'high', 'low'}:
            raise ValueError('A record has an unknown confidence level')
        all_counts.update(statuses[f] for f in fields)
        required_counts.update(statuses[f] for f in required)
        complete += all(statuses[f] in {'filled', 'not-applicable', 'not-published'} for f in required)
        pending_records += any(statuses[f] == 'pending-review' for f in fields)
        confidence[grade] += 1
        source_links += bool(row.get('source_url'))
        source_checked += row['source_check'] in CHECKED
        held += row['held']
        review += row['review_required']
    total = len(records)
    actual_coverage = round(source_checked / total, 4) if total else None
    coverage = summary.get('source_coverage')
    if ((total and (isinstance(coverage, bool) or not isinstance(coverage, (int, float))
                    or not 0 <= coverage <= 1 or abs(coverage - actual_coverage) > 0.0001))
            or (not total and coverage is not None)):
        raise ValueError('Source evidence coverage disagrees with the records API summary')

    def pct(part, whole):
        return round(100 * part / whole, 2) if whole else None

    required_denominator = sum(required_counts[s] for s in
        ('filled', 'pending-review', 'missing', 'optional-empty'))
    # Recompute the literal contractual denominator from the complete snapshot.
    # The public serializer uses field_status/source_check instead of the
    # internal metadata consumed by acceptance_summary. Pending values stay
    # populated but are excluded from the separately labelled unchallenged count.
    contract = acceptance_summary([{**row,
        '_disputed': [field for field, status in row['field_status'].items()
                      if status == 'pending-review'],
        '_unpublished': [field for field, status in row['field_status'].items()
                        if status == 'not-published'],
        '_source_check': row['source_check']} for row in records])
    return {'at': at.astimezone(timezone.utc).isoformat(), 'records': total,
        'field_completeness_pct': pct(required_counts['filled'], required_denominator),
        'record_completeness_pct': pct(complete, total),
        'high_confidence_pct': pct(confidence['high'], total),
        'source_coverage_pct': pct(source_checked, total),
        'source_link_coverage_pct': pct(source_links, total),
        'source_checked_records': source_checked, 'source_linked_records': source_links,
        'complete_records': complete, 'held_records': held, 'review_required_records': review,
        'pending_review_records': pending_records,
        'field_counts': {s: all_counts[s] for s in STATES},
        'required_field_counts': {s: required_counts[s] for s in STATES},
        'confidence_counts': {s: confidence[s] for s in ('high', 'low')},
        'contract_acceptance_metrics': contract,
        'scope': 'Snapshot of records/products, not unique routes. Completeness counts filled applicable '
            'required fields; pending review is not filled. Source coverage counts supported visa-verdict '
            'evidence, not bare links. Contract acceptance metrics independently count all 25 dictionary '
            'fields, with no blank exclusions. These metrics do not certify every detail or current policy accuracy.'}


def write_report(path: Path, report: dict) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                prefix=path.name + '.', delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(*, report_dir: Path = REPORT_DIR, now: datetime | None = None) -> int:
    try:
        at = now or datetime.now(timezone.utc)
        if at.tzinfo is None:
            raise ValueError('Report time must have a timezone')
        at = at.astimezone(timezone.utc)
        report = quality_metrics(fetch_records(settings()), at)
        destination = Path(report_dir) / (at.strftime('%Y-%m') + '.json')
        write_report(destination, report)
        print(json.dumps({'ok': True, 'report': str(destination), 'records': report['records'],
            'held_records': report['held_records']}))
        return 0
    except Exception as exc:
        # Exception messages, HTTP bodies and request objects can carry
        # credentials or private provider text. Emit only a bounded class.
        print('Monthly quality report failed: ' + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
