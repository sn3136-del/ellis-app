"""Validate an exact installed warning review; never change route data or state."""
from copy import deepcopy
from datetime import date
from scripts.convert_reviewed_product_validity import BASELINE_KEYS, _current_map, _sources
from scripts.prepare_reviewed_product_patch import PatchRejected, digest
from scripts.convert_reviewed_product_patch import _subject
from scripts._reviewed_japan_station_warning_contract import BASELINES, RESOLUTIONS, SOURCES_DIGEST, SOURCE_PINS

MANIFEST='reviewed_japan_station_warning_manifest_20260910.json'
REVIEW_ID='japan-station-exact-warnings-20260910'
JESTA_STATEMENT='Japan’s Ministry of Justice stated on 28 August 2026 that preparations are for JESTA introduction during fiscal year 2028. That planned system is not a current 2026 application step.'
JESTA_QUOTE='ＪＥＳＴＡの２０２８年度中の導入に向けた準備作業'
ED_REQUIRED='日本に新規入国する全ての外国人の方に対し、「外国人入国記録（EDカード）」の提出を求めています。'
ED_PAPER='外国人入国記録（EDカード）の電子的な提出の対象者の方であっても、スマートフォン等の操作に不慣れな場合などには、紙媒体の「外国人入国記録」を提出してください。'
CUSTOMS_PAPER='The existing paper version of the Customs Declaration Form can still be used, and can also be printed out (on A4 sized paper) in advance.'

def _validate_sources(catalog):
    sources=_sources({'sources':catalog})
    if digest(catalog)!=SOURCES_DIGEST or {k:{f:s.get(f) for f in ('url','sha256','checked_at','published_at')} for k,s in sources.items()}!=SOURCE_PINS:
        raise PatchRejected('Exact reviewed official source captures changed')
    if any(s['checked_at']!='2026-09-10' for s in sources.values()) or date.today()<date(2026,9,10):
        raise PatchRejected('Source review date changed or is in the future')
    if sources['jpn_jesta']['published_at']!='2026-08-28' or JESTA_QUOTE not in sources['jpn_jesta']['text']:
        raise PatchRejected('Specific Ministry FY2028 statement missing')
    if ED_REQUIRED not in sources['jpn_ed_card']['text'] or ED_PAPER not in sources['jpn_ed_card']['text'] or CUSTOMS_PAPER not in sources['jpn_customs']['text']:
        raise PatchRejected('Current required ED and paper alternatives missing')
    return sources

def build_manifest(catalog,layers):
    sources=_validate_sources(catalog)
    if not isinstance(layers,list) or len(layers)!=19:raise PatchRejected('All19 reviewed contexts required')
    current=_current_map(layers,set(BASELINES));entries=[]
    from scripts.convert_reviewed_general_batch import _check_proof
    for key in sorted(BASELINES):
        layer=current[key]
        if any(k not in layer for k in BASELINE_KEYS) or {k:digest(layer[k]) for k in BASELINE_KEYS}!=BASELINES[key]:raise PatchRejected('Exact six-layer Japan baseline changed')
        resolutions=RESOLUTIONS.get(key,[])
        if resolutions and layer['operator_entries']:raise PatchRejected('Operator-owned warning outside this review')
        for item in resolutions:
            if layer['merged_guidance'].get('uncertainty',[]).count(item['warning'])!=1:raise PatchRejected('Exact old warning must occur once')
            evidence=[dict(source_id=s,source_url=sources[s]['url'],quote=sources[s]['text']) for s in item['source_ids']]
            proof=dict(status='reviewed',verifier='ai',verified_at='2026-09-10',subject=_subject(layer['route']),scope_note=item['scope'],evidence=evidence)
            if item['kind']=='obsolete_2026_jesta':field='exceptions';value=[JESTA_STATEMENT]
            else:field='arrival_card';value=layer['merged_guidance']['arrival_card']
            _check_proof(proof,sources,layer['route'],field,value)
        entries.append(dict(cache_key=key,baseline={k:deepcopy(layer[k]) for k in BASELINE_KEYS},resolutions=deepcopy(resolutions)))
    return dict(schema_version=1,kind='reviewed_japan_station_warning_resolution',review_id=REVIEW_ID,sources=deepcopy(catalog),routes=entries,
                scope='Eight exact obsolete warnings only; no route facts, field proof, issue, pending, grade, policy date or freshness change.')

def validate_prepared(manifest,current_layers=None):
    if not isinstance(manifest,dict) or type(manifest.get('schema_version')) is not int:raise PatchRejected('Malformed warning manifest')
    try:embedded=[dict(deepcopy(r['baseline']),cache_key=r['cache_key']) for r in manifest['routes']]
    except (KeyError,TypeError) as exc:raise PatchRejected('Malformed reviewed baseline') from exc
    rebuilt=build_manifest(manifest.get('sources'),embedded)
    if digest(rebuilt)!=digest(manifest):raise PatchRejected('Altered complete warning review')
    if current_layers is not None and digest(build_manifest(manifest['sources'],current_layers))!=digest(manifest):raise PatchRejected('Current state differs from reviewed manifest')
    return {r['cache_key']:r for r in rebuilt['routes']}
