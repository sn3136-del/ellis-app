"""Separate one reviewed visa-exemption threshold from an individual entry grant.

This is a comparison-scope check, never new policy evidence or a release gate.
Only the named two-source/own-proof contract can reject this exact scalar
proposal. New values, missing sources, changed proof or different scope keep
all existing validation and dispute paths. No stored proof substitutes for a
fresh source read.
"""
from datetime import date
import hashlib
import json
import re
from .evidence_validator import quote_in_text

EXPECTED_ROUTE = {'arrival_date': None,
 'consular_jurisdiction': None,
 'destination_country': 'SGP',
 'lawful_country_of_residence': 'HKG',
 'passport_nationality': 'HKG',
 'transit_countries': None,
 'travel_document_type': 'ordinary_passport',
 'travel_purpose': 'tourism',
 'visa_category': 'tourist_visa'}
EXPECTED_STAY_TEXT = 'Actual permitted stay is determined by the e-Pass issued at entry. The Singapore Consulate-General in Hong Kong publishes visa-free social visits of up to 30 days for HKSAR passports. Depart by the last day stated on your e-Pass.'
EXPECTED_PROOFS = {'disposition': 'bdb676d55aeba16e272643c0c2de07d16af5cc37f24a95634854a1c19a7654ea',
 'permitted_stay': '34b7f749d9b784f0e8bcd588ca1a0d42cd082687636bc35536cfdec36c934ce8',
 'permitted_stay_days': 'e9e76e910fdc623735f96b058520ccff88c41ee60742435ba69a33dce2579921'}
EXEMPTION_URL = 'https://hongkong.mfa.gov.sg/consular-services/visa-information/'
INDIVIDUAL_URL = 'https://www.ica.gov.sg/enter-transit-depart/entering-singapore/visa_requirements'
EXEMPTION_QUOTE = 'Hong Kong SAR and Macao SAR passports holders do not require a visa to enter Singapore for business or social visit purpose for up to 30 days.'
INDIVIDUAL_QUOTE = 'The period of stay in Singapore is not tied to the validity of your visa. The period of stay is determined by the duration of the Visit Pass issued to you in the form of electronic visit pass (e-Pass) at the checkpoint upon entry.'


def _digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        return None


def rejected_stay_fields(quoted, evidence, source_url, captures, guidance, fields, route, day):
    """Return diagnostic-only rejections after both current pages were fetched."""
    if (route != EXPECTED_ROUTE or not isinstance(guidance, dict)
            or guidance.get('disposition') != 'VISA_EXEMPT'
            or guidance.get('requirement_detail') != 'unconditional_visa_free'
            or 'permitted_stay_days' not in guidance or guidance['permitted_stay_days'] is not None
            or guidance.get('permitted_stay') != EXPECTED_STAY_TEXT
            or not isinstance(quoted, dict) or type(quoted.get('permitted_stay_days')) is not int
            or quoted['permitted_stay_days'] != 30 or source_url != EXEMPTION_URL
            or not isinstance(evidence, dict) or not isinstance(fields, dict)
            or not isinstance(captures, dict)):
        return {}
    quote = evidence.get('permitted_stay_days')
    if not isinstance(quote, str) or re.sub(r'\s+', ' ', quote).strip() != EXEMPTION_QUOTE:
        return {}
    if any(_digest(fields.get(k)) != h for k, h in EXPECTED_PROOFS.items()):
        return {}
    try:
        on = date.fromisoformat(day)
        if day != on.isoformat() or on < date(2026, 9, 10):
            return {}
    except (TypeError, ValueError):
        return {}
    for url, text in ((EXEMPTION_URL, EXEMPTION_QUOTE), (INDIVIDUAL_URL, INDIVIDUAL_QUOTE)):
        capture = captures.get(url)
        if (not isinstance(capture, dict) or capture.get('url') != url
                or capture.get('checked_at') != day or not isinstance(capture.get('text'), str)
                or not quote_in_text(text, capture['text'])):
            return {}
    return {'permitted_stay_days': {'value': 30, 'quote': quote,
        'reason': 'visa_exemption_threshold_is_not_individual_admission_grant',
        'retained_exemption_limit_days': 30, 'individual_grant': None,
        'source_urls': [EXEMPTION_URL, INDIVIDUAL_URL],
        'verification_credit': False}}
