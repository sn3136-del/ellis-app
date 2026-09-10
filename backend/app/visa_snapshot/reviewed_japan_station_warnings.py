"""Reconcile only literal obsolete warnings against the installed source review.

The manifest is an auditable warning review, not an overlay or publication
permission. All route facts/proofs and external hold state remain unchanged.
"""
from copy import deepcopy
from datetime import date
from functools import lru_cache
import json

@lru_cache(maxsize=2)
def _reviewed(text):
    from scripts.prepare_reviewed_japan_station_warnings import validate_prepared
    return validate_prepared(json.loads(text))

def _applicable(item,today):
    # Current selected ED evidence is not a statement about a future policy.
    if item['kind']=='ed_paper_option':return date(2026,9,10)<=today<=date(2026,12,31)
    # This exact review resolves guesses about2025/2026, using the published
    # 28Aug2026 FY2028 preparation statement. The limit scopes this review;
    # it is not an inferred policy expiry or guarantee of a launch date.
    return item['kind']=='obsolete_2026_jesta' and date(2026,9,10)<=today<=date(2026,12,31)

def reconcile(route,guidance,provenance):
    if not isinstance(route,dict) or not isinstance(guidance,dict) or not isinstance(provenance,dict):return guidance
    from . import kimi_primary,verified_overrides as vo
    from scripts.prepare_reviewed_japan_station_warnings import MANIFEST
    from scripts.prepare_reviewed_product_patch import digest
    try:
        entry=_reviewed((vo.OVERRIDES.parent/MANIFEST).read_text()).get(kimi_primary.cache_key(route))
        if not entry or not entry['resolutions']:return guidance
        baseline=entry['baseline'];expected=baseline['merged_guidance']
        expected_route=deepcopy(baseline['route'])
        # The established cached-reader wrapper omits only a null transit list.
        # Accept that exact representation; no nonempty/empty transit itinerary,
        # passport, residence, arrival date or document alias is generalized.
        if 'transit_countries' in expected_route and expected_route['transit_countries'] is None and 'transit_countries' not in route:
            expected_route.pop('transit_countries',None)
        if digest(route)!=digest(expected_route) or digest(provenance)!=digest(baseline['source_provenance']):return guidance
        if digest({k:v for k,v in guidance.items() if k!='uncertainty'})!=digest({k:v for k,v in expected.items() if k!='uncertainty'}):return guidance
        warnings=guidance.get('uncertainty')
        if not isinstance(warnings,list):return guidance
        remaining=deepcopy(warnings)
        for item in entry['resolutions']:
            if _applicable(item,date.today()) and item['warning'] in remaining:remaining.remove(item['warning'])
        if remaining!=warnings:return dict(guidance,uncertainty=remaining)
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError):pass
    return guidance
