"""Hold claims outside their announced interval without changing their values."""
from copy import deepcopy
from datetime import date, datetime, timezone
import re

FIELDS = ('disposition', 'requirement_detail')
BOUNDS = ('effective_from', 'effective_to')
MARKER = 'policy_interval_conflict'


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _today():
    return datetime.now(timezone.utc).date()


def inherit_bounds(previous, incoming):
    """A later verification date does not erase a known policy end date.

    Missing/null incoming bounds retain the earlier explicit bound. A reviewed
    new interval may replace a bound with a new explicit date. Malformed new
    bounds remain present so the reader fails closed instead of ignoring them.
    """
    prior = previous if isinstance(previous, dict) else {}
    current = deepcopy(incoming) if isinstance(incoming, dict) else {}
    old_evidence = prior.get('policy_interval_evidence')
    old_evidence = old_evidence if isinstance(old_evidence, dict) else {}
    interval_evidence = current.get('policy_interval_evidence')
    interval_evidence = deepcopy(interval_evidence) if isinstance(interval_evidence, dict) else {}
    for key in BOUNDS:
        old_proof = old_evidence.get(key) or prior
        new_proof = interval_evidence.get(key)
        # A saved recheck may carry an inherited old notice even though the
        # field itself was checked more recently. A newer reviewed notice in
        # the seed supersedes that old bound, using the bound's own evidence
        # clock. A malformed explicit new date must still fail closed.
        stale_inherited = (isinstance(new_proof, dict) and isinstance(old_proof, dict)
            and _date(prior.get(key)) is not None and _date(current.get(key)) is not None
            and _date(old_proof.get('verified_at')) is not None
            and _date(new_proof.get('verified_at')) is not None
            and _date(old_proof['verified_at']) > _date(new_proof['verified_at']))
        if prior.get(key) is not None and (current.get(key) is None or stale_inherited):
            current[key] = deepcopy(prior[key])
            # Evidence is retained per bound: a new end date must not inherit
            # the old notice merely because its original start was retained.
            interval_evidence[key] = deepcopy(old_evidence.get(key) or {
                name: prior.get(name) for name in ('source_url', 'verified_at', 'verifier', 'quote', 'quotes', key)
                if name in prior
            })
    if interval_evidence:
        current['policy_interval_evidence'] = interval_evidence
    return current


def annotate(guidance, provenance, route):
    """Attach a deterministic hold reason; retain every raw guidance claim.

    Dates are inclusive. Only actual verdict/detail evidence supplies policy
    bounds: a passport expiry, an advisory date, or an unrelated field cannot
    determine the visa decision. Invalid request dates use today's rule, as
    with the scheduled-policy reader.
    """
    if not isinstance(guidance, dict) or not guidance:
        return guidance
    route = route if isinstance(route, dict) else {}
    selected = _date(route.get('arrival_date')) or _today()
    p = provenance if isinstance(provenance, dict) else {}
    field_proofs = p.get('field_provenance')
    field_proofs = field_proofs if isinstance(field_proofs, dict) else {}
    claimed = p.get('fields')
    claimed = claimed if isinstance(claimed, (list, tuple, set)) else ()
    problems = []
    for field in FIELDS:
        proof = field_proofs.get(field)
        if not isinstance(proof, dict):
            proof = p if field in claimed else {}
        raw_from, raw_to = proof.get('effective_from'), proof.get('effective_to')
        if raw_from is None and raw_to is None:
            continue
        start, end = _date(raw_from), _date(raw_to)
        reason = None
        if ((raw_from is not None and start is None) or (raw_to is not None and end is None)
                or (start and end and end < start)):
            reason = 'invalid_policy_interval'
        elif start and selected < start:
            reason = 'policy_not_effective'
        elif end and selected > end:
            reason = 'policy_expired'
        if reason:
            problems.append({'field': field, 'reason': reason,
                             'effective_from': raw_from if isinstance(raw_from, str) else None,
                             'effective_to': raw_to if isinstance(raw_to, str) else None})
    if not problems and MARKER not in guidance:
        return guidance
    result = deepcopy(guidance)
    result.pop(MARKER, None)
    if problems:
        result[MARKER] = {'selected_date': selected.isoformat(), 'fields': problems,
                          'requires_new_policy_evidence': True}
    return result
