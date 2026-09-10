"""Resolve exactly three obsolete Japan caveats under an installed review.

This does not override arbitrary uncertainty and never clears a raw pending
bit, a freshness dispute or any other serving hold. Changed/new warning
objects remain. The review is scoped to the current ordinary HKSAR route and
2026; its future JESTA planning statement is not a perpetual launch guarantee.
"""
from copy import deepcopy
from datetime import date
from functools import lru_cache
import json,re

KEY='HKG|HKG|JPN|tourism|default|unknown|v6'

def health_shape_errors(value):
    if value is None:return []
    fields={'name','applicability','trigger_countries','trigger','question'}
    def valid(item):
        if not isinstance(item,dict) or set(item)!=fields:return False
        if not isinstance(item['name'],str) or not item['name'].strip():return False
        if item['applicability'] not in ('always_required','conditional','not_applicable'):return False
        countries=item['trigger_countries']
        if not isinstance(countries,list) or any(not isinstance(c,str) or not re.fullmatch('[A-Z]{3}',c) for c in countries):return False
        if any(item[k] is not None and not isinstance(item[k],str) for k in ('trigger','question')):return False
        if item['applicability']=='conditional' and any(not str(item[k] or '').strip() for k in ('trigger','question')):return False
        return True
    if not isinstance(value,list) or any(not valid(item) for item in value):
        return ['health_requirements must contain typed, explicitly scoped health rules']
    return []

@lru_cache(maxsize=2)
def _rebuilt(mt,ot):
    from scripts.convert_reviewed_japan_singapore_fields import convert
    manifest=json.loads(mt);prepared=json.loads(ot)
    baseline=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    overlay,_,previews=convert(manifest,baseline)
    if overlay!=prepared:raise ValueError('Installed overlay differs from complete reviewed conversion')
    preview=next(p for p in previews if p['cache_key']==KEY)
    spec=next(r for r in manifest['specification']['routes'] if r['cache_key']==KEY)
    return preview,spec['resolved_uncertainty']

def reconcile(route,guidance,provenance):
    from . import kimi_primary,verified_overrides as vo
    from scripts.convert_reviewed_japan_singapore_fields import MANIFEST,OVERLAY
    if not (date(2026,9,10)<=date.today()<=date(2026,12,31)) or not isinstance(guidance,dict):return guidance
    if kimi_primary.cache_key(route)!=KEY:return guidance
    try:
        root=vo.OVERRIDES.parent
        if sum(p.resolve()==(root/OVERLAY).resolve() for p in vo._reviewed_overlay_paths())!=1:return guidance
        expected,resolved=_rebuilt((root/MANIFEST).read_text(),(root/OVERLAY).read_text())
        if route!=expected['route'] or provenance!=expected['source_verified']:return guidance
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
