"""Deterministic, named source scopes shared by reviewed import and freshness.

Only explicit government text and complete bounded sections are accepted. An
accepted structured inference retains its scope; it is not a human review or
permission to discard a conditional qualification.
"""
from datetime import date, datetime
import re
from urllib.parse import urlsplit
from . import evidence_validator as evidence

EU_MEMBERS = dict(zip(
    'AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE'.split(),
    'austria belgium bulgaria croatia cyprus czechia denmark estonia finland france germany greece hungary ireland italy latvia lithuania luxembourg malta netherlands poland portugal romania slovakia slovenia spain sweden'.split()))

def _table_support(proof, source, route, disposition):
    table = proof.get('source_table')
    if not isinstance(table, dict) or route['travel_document_type'] != 'ordinary_passport' or route['travel_purpose'] != 'tourism':
        return False
    heading, region, country = (table.get(k) for k in ('heading_quote', 'table_quote', 'nationality_quote'))
    if not all(isinstance(v, str) and v.strip() for v in (heading, region, country)):
        return False
    if (len(region) > 50000 or len(country) > 180 or not evidence.quote_in_text(region, source['text'])
            or not evidence.quote_in_text(heading, region[:max(2000, len(heading))])
            or not evidence.quote_in_text(country, region)):
        return False
    legal_waiver = (disposition == 'VISA_EXEMPT'
        and urlsplit(source['url']).hostname == 'eur-lex.europa.eu'
        and re.search(r'WHOSE NATIONALS ARE EXEMPT FROM THE REQUIREMENT TO BE IN POSSESSION OF A VISA', heading, re.I))
    legal_required = (disposition == 'VISA_REQUIRED'
        and urlsplit(source['url']).hostname == 'eur-lex.europa.eu'
        and re.search(r'WHOSE NATIONALS MUST BE IN POSSESSION OF A VISA', heading, re.I))
    if not (evidence.supports_disposition(heading, disposition) or legal_waiver or legal_required):
        return False
    if not re.search(r'countries|nationalities|nationals|EO\s*408|Executive Order', heading, re.I):
        return False
    # This contract is a heading followed by country-name list items. Reject
    # a second policy paragraph/heading, so membership cannot bleed into a
    # neighbouring section with a different rule.
    if not region.strip().startswith(heading.strip()):
        return False
    body = region.strip()[len(heading.strip()):]
    if any(re.search(r'[:;!?]|\b(?:visa|passport|eligible|eligibility|required|conditions|enter)\b', line, re.I)
           for line in body.splitlines() if line.strip()):
        return False
    # Membership must be the actual list item, never another nationality
    # mentioned in a neighbouring policy paragraph or a mission's address.
    if not re.search(r'^\s*(?:\d+[.)]\s*)?' + re.escape(country.strip()) + r'\s*$', region, re.I | re.M):
        return False
    aliases = evidence._NATIONALITY_NAMES.get(route['passport_nationality'], ())
    if not any(re.search(r'(?<![a-z])' + re.escape(a.strip()) + r'(?![a-z])', _norm(country), re.I) for a in aliases):
        return False
    if re.search(r'diplomatic|official passport|service passport|travel document|work permit|student visa', heading, re.I):
        return False
    return True


def _norm(value):
    return ' '.join(str(value).split())


def _singapore_list_support(proof, source, route, disposition):
    """Only ICA's complete ordinary-passport visa-required list currently
    supports exclusion. Absence from an eVisa or optional-program list does
    not establish a visa verdict, and neither do truncated search snippets."""
    rule = proof.get('source_closed_list')
    if not isinstance(rule, dict) or rule.get('program') != 'singapore_entry_visa':
        return False
    url = urlsplit(source['url'])
    if (url.hostname != 'www.ica.gov.sg' or url.path.rstrip('/') != '/enter-transit-depart/entering-singapore/visa_requirements'
            or route['destination_country'] != 'SGP' or disposition != 'VISA_EXEMPT'
            or route['travel_document_type'] != 'ordinary_passport' or route['travel_purpose'] != 'tourism'):
        return False
    heading, table, closing, nationality = (rule.get(k) for k in
        ('heading_quote', 'table_quote', 'closing_quote', 'excluded_nationality'))
    if not all(isinstance(v, str) and v.strip() for v in (heading, table, closing, nationality)):
        return False
    if (_norm(heading) != 'If your travel document is issued by one of the countries/ places listed below, you will require a valid visa to enter Singapore. Click on individual countries/ places to find out more.'
            or _norm(closing) != 'You will also need a visa if you are travelling on:'
            or _norm(proof['quote']) != _norm(heading)):
        return False
    text, start, end = _norm(source['text']), _norm(heading), _norm(closing)
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        return False
    between = text.split(start, 1)[1].split(end, 1)[0].strip()
    # The page's image label is not a nationality. No other omitted text is
    # permitted between the actual rule heading, complete list and next rule.
    between = re.sub(r'^(?:Image: )?Travel Documents by Countries and Places\s*', '', between)
    if between != _norm(table) or len(table) > 10000:
        return False
    aliases = evidence._NATIONALITY_NAMES.get(route['passport_nationality'], ())
    if not aliases or not any(re.search(r'(?<![a-z])' + re.escape(a.strip()) + r'(?![a-z])', nationality, re.I) for a in aliases):
        return False
    if any(re.search(r'(?<![a-z])' + re.escape(a.strip()) + r'(?![a-z])', table, re.I) for a in aliases):
        return False
    # Reject a partial list even when its author supplies the closing heading
    # separately. These are the first and last rows of this named ICA layout,
    # not a frozen list of nationalities: the intervening list stays literal.
    return _norm(table).startswith('Afghanistan ') and _norm(table).endswith(' Yemen')



def _exact_page(source, url):
    return bool(source and source['url'].rstrip('/') == url.rstrip('/'))


def _companion(rule, name, sources, proof):
    source = sources.get(rule.get(name))
    return source if source and source['checked_at'] == proof['verified_at'] else None


def _bounded_list(rule, source, heading, closing):
    """A captured complete section, never a selected set of favourable rows."""
    if not source or _norm(rule.get('heading_quote')) != _norm(heading) or _norm(rule.get('closing_quote')) != _norm(closing):
        return False
    text, heading, closing = _norm(source['text']), _norm(heading), _norm(closing)
    table = _norm(rule.get('table_quote'))
    if len(table) > 50000 or text.count(heading) != 1 or text.count(closing) != 1:
        return False
    start, stop = text.index(heading), text.index(closing)
    return start < stop and text[start:stop].strip() == table


def _country_member(rule, nat):
    item = rule.get('nationality_quote')
    if not isinstance(item, str) or not item.strip() or len(item) > 400:
        return False
    # Retain a qualified line in its entirety, including conditional eTA and
    # document restrictions. Substring membership would erase those clauses.
    if _norm(item) not in {_norm(line) for line in rule['table_quote'].splitlines()}:
        return False
    bare = re.sub(r'^\s*[*•]\s*', '', item).strip()
    if nat == 'GBR':
        return _norm(bare).lower() in {'british citizen', 'united kingdom – british citizen'}
    aliases = evidence._NATIONALITY_NAMES.get(nat, ())
    return any(re.search(r'(?<![a-z])' + re.escape(a.strip()) + r'(?![a-z])', bare, re.I) for a in aliases)


def _closed_list_support(proof, source, sources, route, disposition):
    rule = proof.get('source_closed_list')
    if not isinstance(rule, dict) or route['travel_document_type'] != 'ordinary_passport' or route['travel_purpose'] != 'tourism':
        return False
    program, nat, dest = rule.get('program'), route['passport_nationality'], route['destination_country']
    if program == 'singapore_entry_visa':
        return _singapore_list_support(proof, source, route, disposition)
    listing = _companion(rule, 'source_id', sources, proof)
    if program == 'usa_vwp_nonmember':
        visitor_url = 'https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html'
        if dest != 'USA' or disposition != 'VISA_REQUIRED' or nat in {'CAN', 'BMU', 'USA'} or rule.get('excluded_nationality') != nat:
            return False
        if not _exact_page(listing, 'https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visa-waiver-program.html'):
            return False
        if not _bounded_list(rule, listing, '## Must Be a Citizen or National of a VWP Designated Country*', '## Reference'):
            return False
        general = _companion(rule, 'general_rule_source_id', sources, proof)
        exception = _companion(rule, 'exception_source_id', sources, proof)
        gq, eq = rule.get('general_rule_quote', ''), rule.get('exception_quote', '')
        if not (_exact_page(general, visitor_url) and _exact_page(exception, visitor_url) and source == general
                and _norm(proof['quote']) == _norm(gq) and evidence.quote_in_text(gq, general['text']) and evidence.quote_in_text(eq, exception['text'])):
            return False
        if not ('a citizen of a foreign country who wishes to travel to the United States must first obtain a visa' in _norm(gq)
                and 'for tourism (B-2 visa)' in gq
                and 'Citizens of Canada and Bermuda generally do not require visas to enter the United States, for visit, tourism and temporary business travel purposes.' in _norm(eq)):
            return False
        aliases = evidence._NATIONALITY_NAMES.get(nat, ())
        return bool(aliases) and not any(re.search(r'(?<![a-z])' + re.escape(a.strip()) + r'(?![a-z])', rule['table_quote'], re.I) for a in aliases)
    if program in {'canada_eta_member', 'canada_visitor_member'}:
        if dest != 'CAN' or source != listing or not _exact_page(listing, 'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/entry-requirements-country.html'):
            return False
        eta = program == 'canada_eta_member'
        expected = 'ELECTRONIC_AUTHORIZATION_REQUIRED' if eta else 'VISA_REQUIRED'
        heading = '## Travellers who need an electronic travel authorization (eTA)' if eta else '## Travellers who need a visa'
        closing = 'Find out how to apply for an eTA' if eta else 'Stateless individuals and those with a refugee travel document also need a visa to visit or transit through Canada.'
        primary = ('You need an eTA and a valid passport to board your flight to Canada if you’re a citizen of any of the countries or territories listed below. You don’t need a visitor visa.' if eta else
                   'If you’re a citizen of any of the countries or territories listed below, you need a valid visitor visa and a valid passport to visit or transit through Canada.')
        return (disposition == expected and _norm(proof['quote']) == primary
            and _bounded_list(rule, listing, heading, closing) and _country_member(rule, nat)
            # The importer has reviewed these uncomplicated passport classes;
            # other document-qualified rows need their own exact contract.
            and nat in ({'KOR', 'GBR', 'AUS', 'FRA'} if eta else {'MYS', 'IDN'}))
    if program == 'australia_evisitor_member':
        general = _companion(rule, 'general_rule_source_id', sources, proof)
        gq = rule.get('general_rule_quote', '')
        return (dest == 'AUS' and disposition == 'VISA_REQUIRED' and nat in EU_MEMBERS.keys() | {'GBR'}
            and _exact_page(listing, 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/evisitor-651')
            and _exact_page(general, 'https://www.abf.gov.au/crossing/Pages/arriving-and-leaving.aspx')
            and source == general and _norm(proof['quote']) == _norm(gq)
            and evidence.quote_in_text(gq, general['text'])
            and 'If you are not an Australian Citizen you must hold a valid visa when entering Australia.' in _norm(gq)
            and _bounded_list(rule, listing, 'You must be a citizen of and hold a valid passport from one of these countries to be eligible for the eVisitor:', 'You cannot apply with:')
            and _country_member(rule, nat)
            and 'as a tourist' in listing['text'] and 'This is a temporary visa.' in listing['text'])
    return False


def _country_section_support(proof, source, sources, route, disposition):
    rule = proof.get('source_country_section')
    names = {'KOR': 'Республика Корея', 'THA': 'Таиланд', 'SGP': 'Сингапур', 'IDN': 'Индонезия', 'VNM': 'Вьетнам', 'ESP': 'Испания'}
    nat = route['passport_nationality']
    if (not isinstance(rule, dict) or rule.get('program') != 'russia_mfa_country_rule'
            or nat not in names or route['destination_country'] != 'RUS'
            or route['travel_document_type'] != 'ordinary_passport' or route['travel_purpose'] != 'tourism'
            or source != _companion(rule, 'source_id', sources, proof)
            or not _exact_page(source, 'https://www.kdmid.ru/cons/visas/conditions-of-entry-foreign-citizens-in-russian-federation/')):
        return False
    heading, section, closing, quote = (rule.get(k, '') for k in ('heading_quote', 'section_quote', 'closing_quote', 'rule_quote'))
    if (heading != '#### ' + names[nat] or rule.get('nationality_quote') != names[nat]
            or _norm(quote) != _norm(proof['quote']) or not evidence.quote_in_text(quote, section)
            or not closing.startswith('#### ') or section.count('#### ') != 1):
        return False
    bounded = {'heading_quote': heading, 'table_quote': section, 'closing_quote': closing}
    if not _bounded_list(bounded, source, heading, closing):
        return False
    if disposition == 'VISA_EXEMPT':
        return (nat in {'KOR', 'THA'} and 'Безвизовой режим:' in section
            and re.fullmatch(r'\* по общегражданским паспортам [–-] до (?:30|60) дней[;.]', _norm(quote)) is not None)
    if disposition != 'VISA_REQUIRED':
        return False
    if _norm(quote) == 'Визы по всем видам паспортов.':
        return True
    # Optional electronic visa availability cannot establish necessity alone.
    general = _companion(rule, 'general_rule_source_id', sources, proof)
    gq = rule.get('general_rule_quote', '')
    return (nat in {'SGP', 'IDN', 'VNM'} and _norm(quote) == 'Могут въезжать по электронной визе на срок до 30 дней.'
        and _exact_page(general, 'https://www.kdmid.ru/cons/visas/')
        and evidence.quote_in_text(gq, general['text']) and len(gq) < 2000
        and 'Для въезда в Российскую Федерацию иностранного гражданина или лица без гражданства необходима виза.' in _norm(gq)
        and 'При наличии соглашения возможен въезд в Российскую Федерацию иностранных граждан без виз.' in _norm(gq)
        and 'общегражданским паспортам' not in section)

def _eu_citizen_support(proof, source, sources, route, disposition):
    rule = proof.get('source_eu_citizen')
    nat = route['passport_nationality']
    if (not isinstance(rule, dict) or disposition != 'VISA_EXEMPT'
            or nat not in EU_MEMBERS or route['destination_country'] not in {'FRA', 'ESP'}
            or route['travel_document_type'] != 'ordinary_passport' or route['travel_purpose'] != 'tourism'):
        return False
    quote = proof['quote']
    # Explicit Union-citizen entry rule; family-member residence provisions
    # cannot supply an EU citizen's nationality or entry-visa exemption.
    if len(quote) > 1000 or not (re.search(r'No entry visa or equivalent formality may be imposed on Union citizens', quote, re.I)
            or re.search(r'not required to hold a visa.{0,180}Citizens of the European Union', _norm(quote), re.I)):
        return False
    membership = sources.get(rule.get('membership_source_id'))
    member_quote = rule.get('membership_quote')
    if not membership or not isinstance(member_quote, str) or len(member_quote) > 1000:
        return False
    expected = 'https://european-union.europa.eu/principles-countries-history/eu-countries/' + EU_MEMBERS[nat] + '_en'
    if (membership['url'] != expected or membership['checked_at'] != proof['verified_at']
            or not evidence.quote_in_text(member_quote, membership['text'])):
        return False
    since = re.search(r'EU Member State:\s*since\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})', member_quote, re.I)
    try:
        joined = datetime.strptime(since[1], '%d %B %Y').date() if since else None
    except ValueError:
        return False
    return (joined is not None and joined <= date.fromisoformat(proof['verified_at'])
        and re.search(r'(?<![a-z])' + re.escape(EU_MEMBERS[nat]) + r'(?![a-z])', member_quote, re.I) is not None
        and not re.search(r'not (?:an? )?EU member|former member|left the EU|candidate countr', member_quote, re.I))



CONTRACTS = ('source_table', 'source_closed_list', 'source_eu_citizen', 'source_country_section')


def validate_route_evidence(proof, source, sources, route, disposition, *, policy_date):
    """Return exact supporting passages; reject invalid structured contracts
    without retrying a looser general-text matcher."""
    result = {'ok': False, 'reason': 'disposition lacks exact route/document/purpose evidence',
              'quote': proof.get('quote', '') if isinstance(proof, dict) else '',
              'scope_quotes': [], 'conditions': [], 'program': None}
    if not isinstance(proof, dict) or not isinstance(source, dict):
        return result
    quote = proof.get('quote')
    try:
        date.fromisoformat(policy_date)
        checked = proof.get('verified_at', policy_date)
        if checked > policy_date or checked != source.get('checked_at'):
            return result
        if not isinstance(quote, str) or not quote.strip() or not evidence.quote_in_text(quote, source.get('text', '')):
            return result
        if not evidence.source_is_official(source['url']) or not evidence.jurisdiction_matches(source['url'], route['destination_country']):
            return result
        contracts = {key for key in CONTRACTS if key in proof}
        if len(contracts) > 1:
            result['reason'] = 'competing structured evidence contracts'
            return result
        if 'source_table' in contracts:
            ok = _table_support(proof, source, route, disposition)
        elif 'source_closed_list' in contracts:
            ok = _closed_list_support(proof, source, sources, route, disposition)
        elif 'source_eu_citizen' in contracts:
            ok = _eu_citizen_support(proof, source, sources, route, disposition)
        elif 'source_country_section' in contracts:
            ok = _country_section_support(proof, source, sources, route, disposition)
        else:
            ok = bool(evidence.route_supporting_excerpt(quote, disposition, route, policy_date=policy_date))
        if not ok:
            return result
        scope = [{'source_id': proof.get('source_id'), 'quote': quote}]
        for key in contracts:
            rule = proof[key]
            result['program'] = rule.get('program', key)
            for field, value in rule.items():
                if field.endswith('_quote') and isinstance(value, str):
                    prefix = field[:-6]
                    sid = rule.get(prefix + '_source_id', rule.get('source_id', proof.get('source_id')))
                    if key == 'source_eu_citizen': sid = rule.get('membership_source_id')
                    companion = sources.get(sid)
                    if not companion or companion.get('checked_at') != checked or not evidence.source_is_official(companion['url']) or not evidence.quote_in_text(value, companion.get('text', '')):
                        return result
                    scope.append({'source_id': sid, 'quote': value})
            if key == 'source_closed_list' and rule.get('program') in {'canada_eta_member', 'canada_visitor_member', 'australia_evisitor_member'}:
                result['conditions'] = [quote, rule['nationality_quote']]
        result.update(ok=True, reason='exact named scope accepted' if contracts else 'exact statement accepted', scope_quotes=scope)
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        return result
