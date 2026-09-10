"""Read-only acceptance audit; never fills data or changes verification labels.

Usage: python scripts/audit_acceptance_snapshot.py records.json report.json
Accepts the full /database/records response. Percentages are diagnostics, not
an accuracy certificate. Keep held rows and optional blanks in the strict
denominator; report the existing dashboard denominator separately.
"""
from __future__ import annotations

import json
import sys
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.visa_snapshot.tstation import CONTRACT_FIELDS, FIELD_ORDER, acceptance_summary

STATIONS = ('HKG', 'TWN', 'JPN', 'KOR', 'USA', 'THA', 'SGP', 'MYS', 'GBR',
            'RUS', 'AUS', 'IDN', 'PHL', 'FRA', 'VNM', 'ESP', 'IND', 'CAN')
SUPPORTED = {'human-quote', 'ai-quote', 'grounded-consistent'}


def populated(value):
    # Zero fees are valid values. Whitespace and empty containers are not.
    return value is not None and value != [] and value != {} and (
        not isinstance(value, str) or bool(value.strip()))


def percent(numerator, denominator):
    return 100 * numerator / denominator if denominator else None


def audit(payload):
    rows = payload['records']
    fields = payload['fields']
    required = payload['required_fields']
    # A truncated caller-supplied schema must not shrink the contractual
    # denominator. Field 5 may be split into primary/subcategory for export;
    # either shape must still contain every exact numbered dictionary name.
    if (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
            or len(fields) != len(set(fields))
            or set(fields) not in (set(CONTRACT_FIELDS), set(FIELD_ORDER))):
        raise ValueError('The snapshot must contain the exact 25-field contract dictionary, without duplicates')
    contract_fields = list(CONTRACT_FIELDS)
    if (not isinstance(required, list) or not required
            or not all(isinstance(f, str) for f in required)
            or len(required) != len(set(required)) or not set(required) <= set(fields)):
        raise ValueError('Missing or inconsistent field dictionary')
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError('Malformed snapshot records')
    # /database/records supplies summary.total. Older snapshots may provide
    # total at the top level. Require a count and check every supplied one.
    totals = [payload['total']] if 'total' in payload else []
    if 'summary' in payload:
        summary = payload['summary']
        if not isinstance(summary, dict) or 'total' not in summary:
            raise ValueError('Missing snapshot summary total')
        totals.append(summary['total'])
    if not totals or any(type(n) is not int or n != len(rows) for n in totals):
        raise ValueError('A paginated or incomplete snapshot cannot certify full coverage')
    # Recompute from rows rather than trusting a supplied summary. The public
    # serializer names this metadata differently from the internal projector.
    # A recorded unpublished disposition is completion evidence, not a new
    # verification of its official source by this read-only auditor.
    normalized = []
    for row in rows:
        statuses = row.get('field_status', {})
        if not isinstance(statuses, dict):
            raise ValueError('Malformed field status metadata')
        normalized.append({**row,
            '_disputed': [f for f, status in statuses.items() if status == 'pending-review'],
            '_unpublished': [f for f, status in statuses.items() if status == 'not-published'],
            '_source_check': row.get('source_check')})
    contract = acceptance_summary(normalized)
    counts = Counter(r.get('confidence_level') for r in rows)
    linked = sum(populated(r.get('source_url')) for r in rows)
    supported = sum(r.get('source_check') in SUPPORTED for r in rows)
    filled = sum(populated(r.get(f)) for r in rows for f in fields)
    strict_complete = sum(all(populated(r.get(f)) for f in fields) for r in rows)
    pending = sum(r.get('field_status', {}).get(f) == 'pending-review'
                  for r in rows for f in fields)
    routes = defaultdict(list)
    pairs = defaultdict(list)
    for r in rows:
        routes[r['cache_key']].append(r)
        a, b = r.get('travel_document_country'), r.get('destination_country')
        if (a in STATIONS and b in STATIONS and a != b and
                r.get('travel_document_type') == 'ordinary_passport' and
                r.get('travel_purpose') == 'tourism'):
            pairs[(a, b)].append(r)
    queue = []
    for key, products in sorted(routes.items()):
        missing = Counter(f for r in products for f in fields if not populated(r.get(f)))
        unsupported = sum(r.get('source_check') not in SUPPORTED for r in products)
        conflicts = sum(bool(r.get('contradictions')) for r in products)
        held = sum(r.get('held') is True for r in products)
        if missing or unsupported or conflicts or held:
            queue.append({'cache_key': key, 'product_rows': len(products),
                          'missing_cells': dict(missing), 'unsupported_rows': unsupported,
                          'conflict_rows': conflicts, 'held_rows': held})
    return {
        'acceptance_certified': False,
        'accuracy_certified': False,
        'measurement_notes': [
            'A URL present is not proof that it is official or supports any field.',
            'Requirement support is not verification of every product field.',
            'Strict counts include every exported column; no N/A or unpublished exclusions.',
            'The numbered dictionary has 25 fields; the exported visa subcategory belongs to field 5.',
            'Pending review is never counted as operator approval.',
            'Documented completion counts recorded Not applicable/Not published states; it does not reverify their sources.',
        ],
        'product_rows': len(rows), 'canonical_routes': len(routes),
        'exported_columns': len(fields), 'dashboard_required_columns': len(required),
        'contract_field_count': len(contract_fields),
        **{key: contract[key] for key in (
            'documented_completed_cells', 'documented_disposition_cells',
            'documented_complete_records', 'documented_field_completeness_rate',
            'documented_record_completeness_rate', 'documented_completion_policy')},
        'contract_field_completeness_percent': percent(
            sum(populated(r.get(f)) for r in rows for f in contract_fields),
            len(rows) * len(contract_fields)),
        'contract_record_completeness_percent': percent(
            sum(all(populated(r.get(f)) for f in contract_fields) for r in rows), len(rows)),
        'strict_exported_cell_completeness_percent': percent(filled, len(rows) * len(fields)),
        'strict_exported_complete_records': strict_complete,
        'strict_exported_record_completeness_percent': percent(strict_complete, len(rows)),
        'pending_review_cells': pending,
        'source_url_present_rows': linked,
        'source_url_present_percent': percent(linked, len(rows)),
        'requirement_supported_rows': supported,
        'requirement_supported_percent': percent(supported, len(rows)),
        'medium_or_above_percent': percent(counts['Medium'] + counts['High'], len(rows)),
        'low_rows_exposed': sum(r.get('confidence_level') == 'Low' and r.get('held') is not True for r in rows),
        'conflict_rows_exposed': sum(bool(r.get('contradictions')) and r.get('held') is not True for r in rows),
        'sample_306': {
            'present': len(pairs),
            'served': sum(all(r.get('held') is False for r in rs) for rs in pairs.values()),
            'missing': [{'nationality': a, 'destination': b} for a in STATIONS for b in STATIONS
                        if a != b and (a, b) not in pairs],
            'scope': 'Breadth sample only; not the total route universe.',
        },
        'route_remediation_queue': queue,
    }


if __name__ == '__main__':
    source, destination = map(Path, sys.argv[1:])
    report = audit(json.loads(source.read_text()))
    report['snapshot_file'] = str(source.resolve())
    report['snapshot_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    report['audit_generated_at'] = datetime.now(timezone.utc).isoformat()
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'route_remediation_queue'}, indent=2))
