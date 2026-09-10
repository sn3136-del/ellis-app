"""Fresh-read evidence assembly. Stored reviews locate proof; they never verify it."""
from copy import deepcopy
import re
from . import structured_evidence as structured
from .evidence_validator import field_value_supported, jurisdiction_matches, quote_in_text


def reviewed_evidence(override, verification):
    review = (verification or {}).get('source_review') or {}
    fields = dict(review.get('field_provenance') or review.get('evidence') or {})
    fields.update((override or {}).get('field_provenance') or {})
    catalog = {}
    def collect(value):
        if isinstance(value, dict):
            sid = value.get('id') or value.get('source_id')
            url = value.get('url') or value.get('source_url')
            if isinstance(sid, str) and isinstance(url, str):
                # Conflicting IDs cannot silently point at another page.
                if sid in catalog and catalog[sid] != url:
                    catalog[sid] = None
                else:
                    catalog[sid] = url
            for item in value.values():
                if isinstance(item, (dict, list)): collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    collect(review.get('sources', []))
    collect(fields)
    return fields, {k:v for k,v in catalog.items() if v}


def source_map(captures, catalog):
    return {sid: captures[url] for sid, url in catalog.items() if url in captures}


def structured_route_result(answer, known, source, sources, route, verdict, guidance, day):
    """Only an exact named contract may bridge a heading and its country row."""
    candidates = []
    supplied = answer.get('route_evidence')
    if isinstance(supplied, dict): candidates.append(supplied)
    if isinstance(known, dict) and known.get('source_url') == source['url']:
        candidates.append(known)
    for candidate in candidates:
        if not any(k in candidate for k in structured.CONTRACTS):
            continue
        proof = deepcopy(candidate)
        proof['verified_at'] = day
        # Fresh captures, never stored capture timestamps/text, feed this call.
        scoped = dict(sources)
        if proof.get('source_id'): scoped[proof['source_id']] = source
        result = structured.validate_route_evidence(proof, source, scoped, route, verdict, policy_date=day)
        if result['ok'] and structured.guidance_conditions_preserved(result, guidance)['ok']:
            result['proof'] = proof
            for item in result['scope_quotes']:
                if item.get('source_id') in scoped:
                    item['source_url'] = scoped[item['source_id']]['url']
            return result
    return None


def field_scope_matches_route(name, quote, route):
    """A route verdict cannot lend applicability to a different fee-table row."""
    if name not in {'government_fee','processing_time','permitted_stay','permitted_stay_days','visa_products'}:
        return True
    if not isinstance(quote,str): return False
    if re.search(r'\b(?:other|remaining)\s+(?:nationalities|countries|citizens|nationals|applicants|passport holders)\b',quote,re.I):
        return False
    # An explicitly nationality-owned statement must actually name the route
    # traveller. Unknown groups do not inherit applicability from page context.
    if re.search(r'\b(?:citizens|nationals|nationalities|passport holders)\b',quote,re.I):
        from .evidence_validator import _NATIONALITY_NAMES
        if re.search(r'\ball (?:foreign )?(?:citizens|nationals|nationalities|passport holders)\b',quote,re.I):
            return not re.search(r'\bexcept\b|excluding',quote,re.I)
        nationality=route.get('passport_nationality')
        names=_NATIONALITY_NAMES.get(nationality,())
        for other,aliases in _NATIONALITY_NAMES.items():
            if other==nationality: continue
            if any(re.search(r'(?<![a-z])'+re.escape(alias.strip())+r'\s+(?:(?:ordinary|diplomatic|official|service)\s+)?(?:citizens|nationals|passport holders)\b',quote,re.I) for alias in aliases):
                return False
        if not any(re.search(r'(?<![a-z])'+re.escape(n.strip())+r'(?![a-z])',quote,re.I) for n in names):
            return False
    document=route.get('travel_document_type','ordinary_passport')
    for term,kind in [('diplomatic','diplomatic_passport'),('service','service_passport'),
                      ('official','official_passport'),('ordinary','ordinary_passport')]:
        if re.search(r'\b'+term+r'\s+passports?\b',quote,re.I) and document!=kind:
            return False
    purpose=route.get('travel_purpose','tourism')
    if purpose=='tourism' and re.search(r'(?:work|student|study|employment) visa',quote,re.I):
        return False
    if purpose!='tourism' and re.search(r'touris[tm]',quote,re.I):
        return False
    return True


def reviewed_field_quote(name, value, fields, source, sources, route):
    """Re-read every fragment of a prior field review before reusing its proof."""
    proof = fields.get(name)
    if not isinstance(proof, dict) or proof.get('source_url') != source['url']:
        return None
    quotes = [proof.get('quote')] + (proof.get('additional_quotes') or [])
    if any(not isinstance(q, str) or not q.strip() or not quote_in_text(q, source['text']) for q in quotes):
        return None
    scope = [{'source_url':source['url'], 'quote':q} for q in quotes]
    for item in proof.get('supporting_evidence') or []:
        if not isinstance(item, dict): return None
        extra = sources.get(item.get('source_id'))
        if (not extra or extra['url'] != item.get('source_url')
                or not jurisdiction_matches(extra['url'], route.get('destination_country',''))
                or not isinstance(item.get('quote'), str)
                or not quote_in_text(item['quote'], extra['text'])):
            return None
        quotes.append(item['quote'])
        scope.append({'source_url':extra['url'], 'quote':item['quote']})
    combined = '\n'.join(quotes)
    return (combined, scope) if field_scope_matches_route(name,combined,route) and field_value_supported(name, value, combined) else None


def has_structured_contract(answer, known, source_url):
    return any(isinstance(p,dict) and any(k in p for k in structured.CONTRACTS)
               for p in (answer.get('route_evidence'), known if isinstance(known,dict)
                         and known.get('source_url')==source_url else None))


def field_program_matches(name, quote, guidance):
    if name not in {'government_fee','processing_time'} or not isinstance(quote,str):
        return True
    detail=guidance.get('requirement_detail')
    if guidance.get('disposition') in {'VISA_REQUIRED','VISA_ON_ARRIVAL'} and detail!='evisa':
        if re.search(r'\bESTA\b|\bK-?ETA\b|(?<![-\w])eTA(?![-\w])|electronic travel authori[sz]ation',quote,re.I):
            return False
    if detail=='paper_visa' and re.search(r'e-?visa|electronic visa',quote,re.I):
        return False
    if detail=='evisa' and re.search(r'paper visa|embassy visa',quote,re.I):
        return False
    return True



def field_workflow_matches(name, quote, guidance, route, *, confirmation=False):
    """A consular appointment is not an entry condition for an exempt visit.

    This gate only scopes the route-level appointment field for a currently
    no-application exemption. Other products and genuine changes to the
    default verdict use the existing proof/dispute path. A rejected passage
    remains a failed source check; it neither changes policy nor renews TTL.
    """
    if (name != 'appointment_required'
            or guidance.get('disposition') != 'VISA_EXEMPT'
            or guidance.get('application_channel') not in {'none', 'not_required'}):
        return True
    if not isinstance(quote, str):
        return False
    # A heading elsewhere on the page cannot lend scope to its footer. The
    # appointment and exempt journey must be in the same bounded clause.
    for clause in re.split(r'[.!?;\n]+', quote):
        if len(clause) > 650 or not re.search(r'\bappointments?\b', clause, re.I):
            continue
        if not re.search(r'\bvisa[- ](?:free|exempt)\b|\bvisa exemption\b', clause, re.I):
            continue
        # These are separate applications, not the default short exempt visit.
        if re.search(r'\b(?:certificate|extension|renewal|longer stays?|'
                     r'employment|work visa|student visa|unlike|except|excluding|optional|choose|choosing|'
                     r'consular visa|tourist visa|paper visa|visa applicants?|applying for (?:a )?visa|'
                     r'consular services?|legali[sz]ation|attestation|notari[sz]ation|residence visa|resident visa|'
                     r'applying for (?:a )?residence permit)\b', clause, re.I):
            continue
        if not re.search(r'\b(?:travel(?:l)?ers?|visitors?|tourists?|citizens?|nationals?|'
                         r'passport holders?|entry|travel|visits?)\b', clause, re.I):
            continue
        entry = re.search(r'\b(?:before|prior to) (?:entry|arrival|travel)\b|'
                          r'\b(?:for|to) (?:visa[- ]free )?entry\b|\badmission\b', clause, re.I)
        if not entry:
            continue
        # An independent service/activity is the purpose of the
        # appointment, not an admission rule ("before travel for a museum
        # visit"). Retain actual journey purposes and entry conditions.
        purposes = re.findall(r'\bfor\s+(?:(?:a|an|the)\s+)?([^,]+)', clause, re.I)
        if any(not re.match(r'(?:tourism|business|leisure|holidays?|family visits?|tourist visits?|'
                            r'entry|entering|arrival|admission|travel|appointments?|'
                            r'visa[- ](?:free|exempt))\b', purpose, re.I) for purpose in purposes):
            continue
        # A scoped conditional rule may need adjudication, but cannot prove
        # an unconditional stored Boolean. The full quote stays in the issue.
        if confirmation and re.search(r'\b(?:who|if|when|unless|only|provided|subject to)\b', clause, re.I):
            continue
        # Reuse the explicit nationality/document/purpose exclusions. The
        # legacy helper's visa_products branch performs those scope checks.
        if field_scope_matches_route('visa_products', clause, route):
            return True
    return False

def needs_program_scope(guidance):
    return guidance.get('disposition') == 'ELECTRONIC_AUTHORIZATION_REQUIRED'


def ancillary_fields(source, answer, guidance, route, route_results):
    """A generic program page can attest its price/time, never route eligibility.

    This deliberately narrow contract needs an independently proven program
    and one literal bounded passage naming that same program and field.
    """
    if not jurisdiction_matches(source['url'], route.get('destination_country','')):
        return set()
    route_quotes = ' '.join(str(r.get('quote','')) for r in route_results)
    country = route.get('destination_country')
    detail = guidance.get('requirement_detail')
    patterns = {
        ('CAN', 'eta_electronic_authorization'): r'(?<![-\w])eTA(?![-\w])|electronic travel authori[sz]ation',
                ('USA', 'eta_electronic_authorization'): r'\bESTA\b|electronic system for travel authori[sz]ation',
        ('GBR', 'eta_electronic_authorization'): r'(?<![-\w])ETA(?![-\w])|electronic travel authori[sz]ation',
        ('KOR', 'eta_electronic_authorization'): r'\bK-?ETA\b',
    }
    pattern = patterns.get((country, detail))
    if not pattern or not re.search(pattern, route_quotes, re.I):
        return set()
    quoted = answer.get('evidence') or {}
    scopes = answer.get('field_scope') or {}
    if not isinstance(quoted, dict) or not isinstance(scopes, dict): return set()
    accepted = set()
    for name in ('government_fee', 'processing_time'):
        quote = quoted.get(name)
        scope = scopes.get(name, quote)
        if (not isinstance(quote,str) or not isinstance(scope,str) or len(scope)>1800
                or not quote_in_text(scope,source['text']) or not quote_in_text(quote,scope)
                or not re.search(pattern,scope,re.I)):
            continue
        label = r'fee|cost|pay|price' if name=='government_fee' else r'process|approv|minute|hour|day'
        # Program and price must belong to the same literal statement. A
        # neighbouring eTA sentence cannot lend its name to a visitor-visa fee.
        statements = re.split(r'(?<=[.!?])\s+|\n+',scope)
        ownership_quote = re.sub(r'^\s*\d+[.)]\s+', '',quote)
        owners = [part for part in statements if quote_in_text(ownership_quote,part)
                  and re.search(pattern,part,re.I) and re.search(label,part,re.I)]
        if not owners: continue
        if any(re.search(r'visitor visa|tourist visa|work visa|study permit|e-?visa|\bnot\b|\bno\b',part,re.I) for part in owners):
            continue
        # A conditional/country-priced excerpt needs another explicit contract.
        if re.search(r'\b(?:if|except|citizens|nationals|passport holders)\b',scope,re.I): continue
        value = (answer.get('corrected_fields') or {}).get(name,guidance.get(name))
        if field_scope_matches_route(name,quote,route) and field_value_supported(name,value,quote): accepted.add(name)
    return accepted
