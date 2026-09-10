"""Resolve exactly two historical permit fee/link warnings under the installed review.

No arbitrary uncertainty, raw pending bit, source issue or stay ambiguity is cleared.
The full installed conversion and current fact/proof ownership must still match.
"""
from copy import deepcopy
from datetime import date
from functools import lru_cache
import json
from ._reviewed_hkg_mainland_contract import CASE,RESOLVED_WARNINGS
KEY=CASE['cache_key']

@lru_cache(maxsize=2)
def _rebuilt(mt,ot):
    from scripts.convert_reviewed_hkg_mainland_permit import convert
    manifest=json.loads(mt);prepared=json.loads(ot)
    baseline=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    overlay,_,previews=convert(manifest,baseline)
    if overlay!=prepared:raise ValueError('Installed overlay differs from complete reviewed conversion')
    preview=next(p for p in previews if p['cache_key']==KEY)
    return preview,RESOLVED_WARNINGS

def reconcile(route,guidance,provenance):
    from . import kimi_primary,verified_overrides as vo
    from scripts.convert_reviewed_hkg_mainland_permit import MANIFEST,OVERLAY
    if date.today()<date(2026,9,10) or not isinstance(guidance,dict):return guidance
    if kimi_primary.cache_key(route)!=KEY:return guidance
    try:
        root=vo.OVERRIDES.parent
        if sum(p.resolve()==(root/OVERLAY).resolve() for p in vo._reviewed_overlay_paths())!=1:return guidance
        expected,resolved=_rebuilt((root/MANIFEST).read_text(),(root/OVERLAY).read_text())
        # The canonical reader drops an explicit null transit list. This
        # structural normalization must not prevent resolving the same route.
        normalized_route=dict(route)
        if 'transit_countries' not in normalized_route and expected['route'].get('transit_countries') is None:
            normalized_route['transit_countries']=None
        if normalized_route!=expected['route'] or provenance!=expected['source_verified']:return guidance
        if {k:v for k,v in guidance.items() if k!='uncertainty'}!={k:v for k,v in expected['guidance'].items() if k!='uncertainty'}:return guidance
        warnings=guidance.get('uncertainty')
        if not isinstance(warnings,list):return guidance
        remaining=deepcopy(warnings)
        for old in resolved:
            if old in remaining:remaining.remove(old)
        if remaining!=warnings:return dict(guidance,uncertainty=remaining)
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError):
        pass
    return guidance
