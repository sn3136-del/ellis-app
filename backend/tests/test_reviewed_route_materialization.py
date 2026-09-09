"""Reviewed imports are evidence-scoped, non-releasing and all-or-nothing."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.visa_snapshot.models import KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog

spec = importlib.util.spec_from_file_location('reviewed_materializer', Path(__file__).parents[1] / 'scripts/materialize_reviewed_routes.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
URL = 'https://www.mofa.go.jp/fixture-review'
TABLES = ('kimi_route_guidance_cache', 'database_issue_reports', 'database_change_log')


@pytest.fixture(autouse=True)
def isolated_overrides(tmp_path, monkeypatch):
    seed = tmp_path / 'overrides.json'
    seed.write_text('[]')
    monkeypatch.setattr(mod.overrides, 'OVERRIDES', seed)
    monkeypatch.setenv('ELLIS_OPERATOR_OVERRIDES', str(tmp_path / 'operator.json'))
    mod.overrides.reload()
    yield
    mod.overrides.reload()


def fixture_manifest(*nationalities):
    """Synthetic government-page text exercises the importer, not visa policy."""
    nationalities = nationalities or ('CAN',)
    names = {'CAN': 'Canadian', 'AUS': 'Australian', 'USA': 'United States'}
    manifest = {'schema_version': 1, 'id': 'synthetic-fixture', 'reviewed_at': '2026-09-09', 'sources': [], 'routes': []}
    for nat in nationalities:
        quote = f'{names[nat]} ordinary passport holders may enter Japan for tourism without a visa for a stay of 30 days.'
        source_id = nat.lower()
        manifest['sources'].append({'id': source_id, 'url': URL, 'checked_at': '2026-09-09', 'text': quote, 'reading_method': 'fetched_text'})
        guidance = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                    'permitted_stay_days': 30, 'source_url': URL, 'application_channel': 'not_required',
                    'government_fee': None, 'visa_products': []}
        proof = {'verifier': 'ai', 'verified_at': '2026-09-09', 'source_id': source_id,
                 'source_url': URL, 'quote': quote, 'note': 'Synthetic fixture review for the ordinary tourism baseline.'}
        manifest['routes'].append({'route': {'nationality': nat, 'destination': 'JPN',
            'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'},
            'guidance': guidance, 'field_provenance': {f: deepcopy(proof) for f, v in guidance.items() if v not in mod.UNKNOWN}})
    return manifest


def write_manifest(tmp_path, data):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(data))
    return path


def route_key(nat='CAN'):
    return mod.kp.cache_key({'passport_nationality': nat, 'destination_country': 'JPN',
                             'travel_purpose': 'tourism', 'travel_document_type': 'ordinary_passport'})


@pytest.fixture
def cache_database(tmp_path):
    path = tmp_path / 'cache.db'
    engine = create_engine('sqlite:///' + str(path))
    for model in (KimiRouteGuidanceCache, DatabaseIssueReport, DatabaseChangeLog):
        model.__table__.create(engine)
    with Session(engine) as db:
        route = {'passport_nationality': 'AUS', 'destination_country': 'JPN', 'travel_purpose': 'tourism'}
        db.add(KimiRouteGuidanceCache(cache_key=route_key('AUS'), route=route,
                                     guidance={'preserved': 'operator facts'}, verification={'operator_released': 'kept'}))
        db.add(DatabaseIssueReport(cache_key=route_key('AUS'), route=route, status='open', note='reader history', proposal={'proof': 'retained'}))
        db.add(DatabaseChangeLog(cache_key=route_key('AUS'), route=route, changes={'existing': 'history'}))
        db.commit()
    yield path, engine
    engine.dispose()


def rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('select * from ' + table + ' order by id').fetchall()


def snapshot(path):
    return {table: rows(path, table) for table in TABLES}


def test_dry_run_exact_import_backup_due_check_and_idempotency(cache_database, tmp_path):
    path, engine = cache_database
    data = fixture_manifest('CAN', 'AUS')
    manifest = write_manifest(tmp_path, data)
    before = snapshot(path)
    dry = mod.materialize(path, manifest=manifest, now=NOW)
    assert dry['skipped_invalid'] == 0, dry
    assert (dry['would_insert'], dry['existing'], dry['applied']) == (1, 1, False)
    assert snapshot(path) == before
    with pytest.raises(ValueError, match='backup'):
        mod.materialize(path, manifest=manifest, apply=True, now=NOW)
    backup = tmp_path / 'before.db'
    done = mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)
    assert (done['inserted'], done['existing']) == (1, 1)
    assert snapshot(backup) == before
    assert rows(path, 'database_issue_reports') == before['database_issue_reports']
    with Session(engine) as db:
        added = db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key()).one()
        assert added.guidance == data['routes'][0]['guidance']
        assert 'confidence' not in added.guidance and added.guidance['government_fee'] is None
        assert added.fresh_until == NOW.replace(tzinfo=None)
        assert added.generated_at == added.fresh_until
        assert set(added.verification) == {'source_review'}
        review = added.verification['source_review']
        assert review['verifier'] == 'ai' and review['requires_initial_source_check'] is True
        assert review['sources'][0]['text'] == data['sources'][0]['text']
        log = db.query(DatabaseChangeLog).filter_by(cache_key=route_key()).one()
        assert log.action == 'add' and log.origin == 'reviewed-source-import'
        assert log.changes['disposition']['to'] == 'VISA_EXEMPT'
        old = db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key('AUS')).one()
        assert old.guidance == {'preserved': 'operator facts'} and old.verification == {'operator_released': 'kept'}
    after = snapshot(path)
    again = mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'second.db', now=NOW)
    assert (again['inserted'], again['existing']) == (0, 2)
    assert snapshot(path) == after
    with pytest.raises(ValueError, match='new, separate'):
        mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)


@pytest.mark.parametrize('change,reason', [
    ('forged_quote', 'absent'), ('wrong_nationality', 'exact route'),
    ('wrong_document', 'exact route'), ('wrong_purpose', 'exact route'),
    ('human_stamp', 'AI field'), ('future_stamp', 'future review'),
    ('unsupported_number', 'numbers absent'), ('high_confidence', 'High confidence'),
    ('placeholder', 'placeholder'), ('contradiction', 'contradict'),
])
def test_one_bad_route_aborts_entire_batch_before_backup(cache_database, tmp_path, change, reason):
    path, _ = cache_database
    data = fixture_manifest('CAN', 'USA')
    entry = data['routes'][1]
    if change == 'forged_quote':
        entry['field_provenance']['disposition']['quote'] = 'Invented exemption proof.'
    elif change == 'wrong_nationality':
        entry['route']['nationality'] = 'GBR'
    elif change == 'wrong_document':
        entry['route']['travel_document_type'] = 'diplomatic_passport'
    elif change == 'wrong_purpose':
        entry['route']['travel_purpose'] = 'work'
    elif change == 'human_stamp':
        entry['field_provenance']['disposition']['verifier'] = 'human'
    elif change == 'future_stamp':
        entry['field_provenance']['disposition']['verified_at'] = '2027-01-01'
    elif change == 'unsupported_number':
        entry['guidance']['permitted_stay_days'] = 365
    elif change == 'high_confidence':
        entry['guidance']['confidence'] = 'high'
    elif change == 'placeholder':
        entry['guidance'].pop('permitted_stay_days')
    elif change == 'contradiction':
        entry['guidance']['requirement_detail'] = 'evisa'
    manifest = write_manifest(tmp_path, data)
    before = snapshot(path)
    dry = mod.materialize(path, manifest=manifest, now=NOW)
    assert dry['would_insert'] == 1 and dry['skipped_invalid'] == 1, dry
    assert reason in dry['invalid'][0]['reason'], dry
    backup = tmp_path / 'must-not-exist.db'
    with pytest.raises(mod.MaterializationError):
        mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)
    assert not backup.exists() and snapshot(path) == before


def test_country_table_scope_cannot_bleed_into_neighbouring_policy(cache_database, tmp_path):
    path, _ = cache_database
    data = fixture_manifest()
    heading = 'Nationals from the following countries are allowed to enter Japan without a visa for an initial stay of 30 days:'
    table = heading + '\nAustralia\nCanada\nUnited States of America'
    data['sources'][0]['text'] = table
    entry = data['routes'][0]
    for proof in entry['field_provenance'].values():
        proof['quote'] = heading
    proof = entry['field_provenance']['disposition']
    proof['source_table'] = {'heading_quote': heading, 'table_quote': table, 'nationality_quote': 'Canada'}
    assert mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)['skipped_invalid'] == 0
    bad_table = heading + '\nAustralia\nVisa-required countries:\nCanada'
    data['sources'][0]['text'] = bad_table
    proof['source_table']['table_quote'] = bad_table
    failed = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert failed['skipped_invalid'] == 1 and 'exact route' in failed['invalid'][0]['reason']


def test_additional_literal_quotes_and_current_policy_dates_are_recorded(cache_database, tmp_path):
    path, engine = cache_database
    data = fixture_manifest()
    extra = 'The policy is effective from 1 July 2026 until 30 June 2027. Arrival registration is required within 72 hours before arrival.'
    data['sources'][0]['text'] += '\n' + extra
    entry = data['routes'][0]
    proof = deepcopy(entry['field_provenance']['disposition'])
    proof['additional_quotes'] = [extra]
    entry['policy_valid_from'] = '2026-07-01'
    entry['policy_valid_through'] = '2027-06-30'
    entry['guidance']['arrival_card'] = {'required': True, 'name': 'Arrival registration', 'submission_window': 'Within 72 hours before arrival'}
    for field in ('policy_valid_from', 'policy_valid_through', 'arrival_card'):
        entry['field_provenance'][field] = deepcopy(proof)
    manifest = write_manifest(tmp_path, data)
    done = mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'before.db', now=NOW)
    assert done['inserted'] == 1
    with Session(engine) as db:
        review = db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key()).one().verification['source_review']
        assert review['policy_valid_from'] == '2026-07-01' and review['policy_valid_through'] == '2027-06-30'
    entry['field_provenance']['arrival_card']['additional_quotes'].append('Registration costs 999 dollars.')
    bad = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert bad['skipped_invalid'] == 1 and 'additional quote' in bad['invalid'][0]['reason']
    entry['field_provenance']['arrival_card']['additional_quotes'].pop()
    entry['policy_valid_through'] = '2026-06-30'
    expired = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert expired['skipped_invalid'] == 1 and 'not effective' in expired['invalid'][0]['reason']


def test_duplicate_and_legacy_orphan_require_explicit_review(cache_database, tmp_path):
    path, engine = cache_database
    data = fixture_manifest()
    data['routes'].append(deepcopy(data['routes'][0]))
    duplicate = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert duplicate['skipped_invalid'] == 1 and 'duplicate canonical' in duplicate['invalid'][0]['reason']
    data['routes'].pop()
    with Session(engine) as db:
        db.add(KimiRouteGuidanceCache(cache_key=route_key().replace('|unknown|', '|2026-10|'), route={}, guidance={'old': True}))
        db.commit()
    before = snapshot(path)
    manifest = write_manifest(tmp_path, data)
    dry = mod.materialize(path, manifest=manifest, now=NOW)
    assert dry['plan'][0]['action'] == 'invalid_legacy_orphan'
    with pytest.raises(mod.MaterializationError):
        mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'no.db', now=NOW)
    assert snapshot(path) == before


def test_concurrent_insert_is_preserved_after_review_before_write(cache_database, tmp_path, monkeypatch):
    path, engine = cache_database
    manifest = write_manifest(tmp_path, fixture_manifest())
    original = mod._manifest
    def race(*args):
        result = original(*args)
        with Session(engine) as db:
            db.add(KimiRouteGuidanceCache(cache_key=route_key(), route={}, guidance={'concurrent': 'newer answer'}))
            db.commit()
        return result
    monkeypatch.setattr(mod, '_manifest', race)
    result = mod.materialize(path, manifest=manifest, apply=True, backup=tmp_path / 'before.db', now=NOW)
    assert result['inserted'] == 0 and result['existing'] == 1
    with Session(engine) as db:
        assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=route_key()).one().guidance == {'concurrent': 'newer answer'}
        assert db.query(DatabaseChangeLog).filter_by(cache_key=route_key()).count() == 0


def test_history_failure_rolls_back_all_inserted_routes(cache_database, tmp_path):
    path, _ = cache_database
    manifest = write_manifest(tmp_path, fixture_manifest('CAN', 'USA'))
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER stop_import_history BEFORE INSERT ON database_change_log BEGIN SELECT RAISE(ABORT, 'history write failed'); END")
    before = snapshot(path)
    backup = tmp_path / 'before.db'
    with pytest.raises(sqlite3.IntegrityError, match='history write failed'):
        mod.materialize(path, manifest=manifest, apply=True, backup=backup, now=NOW)
    assert snapshot(path) == before and snapshot(backup) == before


def test_conflicting_verified_overlay_does_not_silently_change_approved_guidance(cache_database, tmp_path, monkeypatch):
    path, _ = cache_database
    manifest = write_manifest(tmp_path, fixture_manifest())
    def conflicting_overlay(guidance, route):
        return {**guidance, 'permitted_stay_days': 90}, {'fields': ['permitted_stay_days']}
    monkeypatch.setattr(mod.overrides, 'apply', conflicting_overlay)
    result = mod.materialize(path, manifest=manifest, now=NOW)
    assert result['skipped_invalid'] == 1 and 'changes a reviewed field' in result['invalid'][0]['reason']


def test_unofficial_source_is_rejected_without_opening_database(tmp_path):
    data = fixture_manifest()
    data['sources'][0]['url'] = 'https://visa-example.com/not-government'
    with pytest.raises(ValueError, match='unofficial'):
        mod.materialize(tmp_path / 'does-not-exist.db', manifest=write_manifest(tmp_path, data), now=NOW)


def closed_list_manifest():
    data = fixture_manifest()
    entry = data['routes'][0]
    entry['route'].update(nationality='IDN', destination='SGP')
    url = 'https://www.ica.gov.sg/enter-transit-depart/entering-singapore/visa_requirements'
    heading = 'If your travel document is issued by one of the countries/ places listed below, you will require a valid visa to enter Singapore. Click on individual countries/ places to find out more.'
    table = 'Afghanistan\nIndia\nRussia\nYemen'
    closing = 'You will also need a visa if you are travelling on:'
    text = heading + '\n' + table + '\n' + closing
    data['sources'][0].update(url=url, text=text)
    entry['guidance'] = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                         'source_url': url, 'entry_requirements': 'Admission remains subject to border assessment.'}
    base = dict(entry['field_provenance']['disposition'], source_url=url, quote=heading)
    entry['field_provenance'] = {field: deepcopy(base) for field in entry['guidance']}
    entry['field_provenance']['disposition']['source_closed_list'] = {
        'program': 'singapore_entry_visa', 'heading_quote': heading, 'table_quote': table,
        'closing_quote': closing, 'excluded_nationality': 'Indonesia'}
    return data


@pytest.mark.parametrize('change', ['valid', 'listed_nationality', 'omitted_row', 'wrong_program', 'wrong_document', 'wrong_purpose', 'reordered_boundary'])
def test_closed_list_exclusion_is_complete_and_program_scoped(cache_database, tmp_path, change):
    path, _ = cache_database
    data = closed_list_manifest()
    entry, source = data['routes'][0], data['sources'][0]
    rule = entry['field_provenance']['disposition']['source_closed_list']
    if change == 'listed_nationality':
        entry['route']['nationality'] = 'RUS'; rule['excluded_nationality'] = 'Russia'
    elif change == 'omitted_row':
        source['text'] = source['text'].replace('Russia', 'Indonesia\nRussia')
    elif change == 'wrong_program':
        rule['program'] = 'optional_evisa_eligible'
    elif change == 'wrong_document':
        entry['route']['travel_document_type'] = 'refugee_travel_document'
    elif change == 'wrong_purpose':
        entry['route']['travel_purpose'] = 'work'
    elif change == 'reordered_boundary':
        source['text'] = rule['closing_quote'] + '\n' + rule['heading_quote'] + '\n' + rule['table_quote']
    result = mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)
    assert result['skipped_invalid'] == (0 if change == 'valid' else 1), result


def eu_citizen_manifest():
    data = fixture_manifest()
    entry = data['routes'][0]
    entry['route'].update(nationality='ESP', destination='FRA')
    url = 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02004L0038-20110616'
    quote = 'No entry visa or equivalent formality may be imposed on Union citizens.'
    stay = 'Union citizens shall have the right of residence on the territory of another Member State for a period of up to three months without any conditions or any formalities other than the requirement to hold a valid identity card or passport.'
    data['sources'][0].update(url=url, text=quote + '\n' + stay)
    member = {'id': 'eu_spain', 'url': 'https://european-union.europa.eu/principles-countries-history/eu-countries/spain_en',
              'checked_at': '2026-09-09', 'text': 'Spain\nOverview\nEU Member State: since 1 January 1986', 'reading_method': 'fetched_text'}
    data['sources'].append(member)
    entry['guidance'] = {'disposition': 'VISA_EXEMPT', 'requirement_detail': 'unconditional_visa_free',
                         'source_url': url, 'permitted_stay': 'Up to three months under EU free movement rights', 'permitted_stay_days': None}
    base = dict(entry['field_provenance']['disposition'], source_url=url, quote=quote)
    entry['field_provenance'] = {field: deepcopy(base) for field, value in entry['guidance'].items() if value is not None}
    entry['field_provenance']['permitted_stay']['quote'] = stay
    entry['field_provenance']['disposition']['source_eu_citizen'] = {
        'membership_source_id': member['id'], 'membership_quote': member['text']}
    return data


@pytest.mark.parametrize('change', ['valid', 'british_nationality', 'wrong_country_page', 'future_membership', 'forged_quote', 'family_only', 'wrong_destination'])
def test_eu_citizenship_requires_current_country_membership_and_own_entry_right(cache_database, tmp_path, change):
    path, engine = cache_database
    data = eu_citizen_manifest()
    entry, member = data['routes'][0], data['sources'][1]
    rule = entry['field_provenance']['disposition']['source_eu_citizen']
    if change == 'british_nationality':
        entry['route']['nationality'] = 'GBR'
    elif change == 'wrong_country_page':
        member['url'] = member['url'].replace('spain_en', 'france_en')
    elif change == 'future_membership':
        member['text'] = member['text'].replace('1986', '2027'); rule['membership_quote'] = member['text']
    elif change == 'forged_quote':
        rule['membership_quote'] += ' This quoted sentence is invented.'
    elif change == 'family_only':
        quote = 'Family members who are not nationals of a Member State shall only be required to have an entry visa.'
        data['sources'][0]['text'] += '\n' + quote
        entry['field_provenance']['disposition']['quote'] = quote
    elif change == 'wrong_destination':
        entry['route']['destination'] = 'CAN'
    manifest = write_manifest(tmp_path, data)
    result = mod.materialize(path, manifest=manifest, now=NOW)
    assert result['skipped_invalid'] == (0 if change == 'valid' else 1), result
    if change == 'valid':
        mod.materialize(path, manifest=manifest, now=NOW, apply=True, backup=tmp_path / 'eu-before.db')
        with Session(engine) as db:
            added = db.query(KimiRouteGuidanceCache).filter_by(model='reviewed-source-import').one()
            assert len(added.verification['source_review']['sources']) == 2
            assert added.guidance['permitted_stay_days'] is None


def test_eur_lex_annex_heading_proves_only_its_own_list(cache_database, tmp_path):
    path, _ = cache_database
    data = fixture_manifest()
    entry = data['routes'][0]
    entry['route']['destination'] = 'FRA'
    url = 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230'
    heading = 'LIST OF THIRD COUNTRIES WHOSE NATIONALS ARE EXEMPT FROM THE REQUIREMENT TO BE IN POSSESSION OF A VISA WHEN CROSSING THE EXTERNAL BORDERS OF THE MEMBER STATES FOR STAYS OF NO MORE THAN 90 DAYS IN ANY 180-DAY PERIOD'
    table = heading + '\n1. STATES\nAustralia\nCanada\nJapan'
    data['sources'][0].update(url=url, text=table)
    entry['guidance'].update(source_url=url, permitted_stay_days=90)
    for proof in entry['field_provenance'].values():
        proof.update(source_url=url, quote=heading)
    entry['field_provenance']['disposition']['source_table'] = {'heading_quote': heading, 'table_quote': table, 'nationality_quote': 'Canada'}
    assert mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)['skipped_invalid'] == 0
    entry['route']['nationality'] = 'IDN'
    entry['field_provenance']['disposition']['source_table']['nationality_quote'] = 'Indonesia'
    assert mod.materialize(path, manifest=write_manifest(tmp_path, data), now=NOW)['skipped_invalid'] == 1


def program_fixture(program, nat=None):
    """Compact complete synthetic sections; these are not policy fixtures."""
    usa = program == 'usa_vwp_nonmember'
    canada = program.startswith('canada_')
    eta = program == 'canada_eta_member'
    nat = nat or ('MYS' if usa else 'GBR' if eta else 'IDN' if canada else 'FRA')
    dest = 'USA' if usa else 'CAN' if canada else 'AUS'
    if usa:
        url = 'https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visitor.html'
        listing_url = 'https://travel.state.gov/content/travel/en/us-visas/tourism-visit/visa-waiver-program.html'
        primary = 'Generally, a citizen of a foreign country who wishes to travel to the United States must first obtain a visa, for tourism (B-2 visa).'
        heading, closing = '## Must Be a Citizen or National of a VWP Designated Country*', '## Reference'
        table = heading + '\n* Andorra\n* Australia\n* France\n* United Kingdom**'
        exception = 'Citizens of Canada and Bermuda generally do not require visas to enter the United States, for visit, tourism and temporary business travel purposes.'
        rule = dict(program=program, source_id='list', heading_quote=heading, table_quote=table, closing_quote=closing, excluded_nationality=nat,
                    general_rule_source_id='main', general_rule_quote=primary, exception_source_id='main', exception_quote=exception)
    elif canada:
        url = listing_url = 'https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada/entry-requirements-country.html'
        heading = '## Travellers who need an electronic travel authorization (eTA)' if eta else '## Travellers who need a visa'
        closing = 'Find out how to apply for an eTA' if eta else 'Stateless individuals and those with a refugee travel document also need a visa to visit or transit through Canada.'
        primary = ('You need an eTA and a valid passport to board your flight to Canada if you’re a citizen of any of the countries or territories listed below. You don’t need a visitor visa.' if eta else
                   'If you’re a citizen of any of the countries or territories listed below, you need a valid visitor visa and a valid passport to visit or transit through Canada.')
        item = '* British citizen' if eta else '* Indonesia (Some citizens of Indonesia may be eligible for an eTA if they meet certain requirements .)'
        table = heading + '\n' + primary + '\n' + item + '\n* France' if eta else heading + '\n' + primary + '\n' + item
        rule = dict(program=program,source_id='main',heading_quote=heading,table_quote=table,closing_quote=closing,nationality_quote=item)
        exception = ''
    else:
        url = 'https://www.abf.gov.au/crossing/Pages/arriving-and-leaving.aspx'
        listing_url = 'https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/evisitor-651'
        primary = 'If you are not an Australian Citizen you must hold a valid visa when entering Australia.'
        heading, closing = 'You must be a citizen of and hold a valid passport from one of these countries to be eligible for the eVisitor:', 'You cannot apply with:'
        table = heading + '\nFrance\nSpain\nUnited Kingdom – British Citizen'
        rule = dict(program=program,source_id='list',heading_quote=heading,table_quote=table,closing_quote=closing,nationality_quote='France',general_rule_source_id='main',general_rule_quote=primary)
        exception = ''
    sources = {'main': dict(id='main',url=url,checked_at='2026-09-09',reading_method='fetched_text',text=primary+'\n'+exception)}
    listtext = table + '\n' + closing + '\nas a tourist\nThis is a temporary visa.'
    if canada: sources['main']['text'] = listtext
    else: sources['list'] = dict(id='list',url=listing_url,checked_at='2026-09-09',reading_method='fetched_text',text=listtext)
    route = dict(passport_nationality=nat,destination_country=dest,travel_purpose='tourism',travel_document_type='ordinary_passport')
    proof = dict(verifier='ai',verified_at='2026-09-09',source_id='main',source_url=url,quote=primary,note='Explicit synthetic structured evidence review.',source_closed_list=rule)
    return proof,sources,route,'ELECTRONIC_AUTHORIZATION_REQUIRED' if eta else 'VISA_REQUIRED'


@pytest.mark.parametrize('program', ['usa_vwp_nonmember','canada_eta_member','canada_visitor_member','australia_evisitor_member'])
@pytest.mark.parametrize('change', ['valid','omitted_row','wrong_doc','wrong_program','wrong_date'])
def test_named_closed_programs_reject_incomplete_or_wrong_scope(program,change):
    proof,sources,route,disp=program_fixture(program)
    rule=proof['source_closed_list']
    if change=='omitted_row': rule['table_quote']=rule['table_quote'].replace('\n* France','').replace('\nSpain','').replace(' if they meet certain requirements .','')
    if change=='wrong_doc': route['travel_document_type']='diplomatic_passport'
    if change=='wrong_program': rule['program']='optional_evisa_eligibility'
    if change=='wrong_date': sources[rule['source_id']]['checked_at']='2026-09-08'
    if change=='valid': mod._proof('disposition',disp,proof,sources,route,NOW.date())
    else:
        with pytest.raises(ValueError): mod._proof('disposition',disp,proof,sources,route,NOW.date())


@pytest.mark.parametrize('change',['member','canada_exception','missing_general','missing_exception'])
def test_us_nonmember_requires_general_rule_and_independent_exceptions(change):
    proof,sources,route,disp=program_fixture('usa_vwp_nonmember')
    if change=='member': route['passport_nationality']='FRA';proof['source_closed_list']['excluded_nationality']='FRA'
    if change=='canada_exception':route['passport_nationality']='CAN';proof['source_closed_list']['excluded_nationality']='CAN'
    if change=='missing_general':proof['source_closed_list'].pop('general_rule_source_id')
    if change=='missing_exception':proof['source_closed_list'].pop('exception_quote')
    with pytest.raises(ValueError):mod._proof('disposition',disp,proof,sources,route,NOW.date())


def test_qualified_country_item_cannot_be_truncated_or_substituted():
    for program,replacement in [('canada_visitor_member','* Indonesia'),('canada_eta_member','* British national overseas'),('australia_evisitor_member','United Kingdom')]:
        proof,sources,route,disp=program_fixture(program)
        proof['source_closed_list']['nationality_quote']=replacement
        with pytest.raises(ValueError):mod._proof('disposition',disp,proof,sources,route,NOW.date())


@pytest.mark.parametrize('value,quote,supported',[(500000,'Biaya visa B1 Rp500.000',True),(100,'100.00',True),(500000,'500,000',True),(500,'0.500',False),(500000,'Rp500.001',False),(100,'1100.00',False)])
def test_exact_numeric_format_equivalence(value,quote,supported):
    assert mod._numeric_support(value,quote) is supported


@pytest.mark.parametrize('field,old,new',[('permitted_stay_days',None,90),('visa_products',[],[{'name':'invented product'}]),('government_fee',None,{'amount':25,'currency':'USD'})])
def test_explicit_clear_cannot_be_refilled_by_surviving_overlay(tmp_path,monkeypatch,field,old,new):
    data=fixture_manifest();entry=data['routes'][0];entry['guidance'][field]=old
    entry['guidance']['required_documents']=['Valid ordinary passport']
    entry['field_provenance']['required_documents']=deepcopy(entry['field_provenance']['disposition'])
    def refill(g,r):
        g[field]=new
        return g,{'fields':[field]}
    monkeypatch.setattr(mod.overrides,'apply',refill)
    with pytest.raises(ValueError,match='reviewed field|contradiction'):
        mod._validate_entry(entry,{s['id']:s for s in data['sources']},NOW.date())


@pytest.mark.parametrize('change',['valid_exempt','valid_required','diplomatic_only','wrong_country','neighbour_bleed','evisa_without_general','forged_general'])
def test_russian_passport_section_and_general_visa_baseline(change):
    visa = change in {'valid_required','evisa_without_general','forged_general'}
    nat,country = ('SGP','Сингапур') if visa else ('KOR','Республика Корея')
    url='https://www.kdmid.ru/cons/visas/conditions-of-entry-foreign-citizens-in-russian-federation/'
    heading,closing='#### '+country,'#### Следующая страна'
    quote='Могут въезжать по электронной визе на срок до 30 дней.' if visa else '* по общегражданским паспортам – до 60 дней;'
    section=heading+'\nБез виз по дипломатическим и служебным паспортам – до 90 дней.\n'+('' if visa else 'Безвизовой режим:\n')+quote
    source=dict(id='ru',url=url,checked_at='2026-09-09',reading_method='fetched_text',text=section+'\n'+closing)
    general='Для въезда в Российскую Федерацию иностранного гражданина или лица без гражданства необходима виза. При наличии соглашения возможен въезд в Российскую Федерацию иностранных граждан без виз.'
    sources={'ru':source,'general':dict(id='general',url='https://www.kdmid.ru/cons/visas/',checked_at='2026-09-09',reading_method='fetched_text',text=general)}
    rule=dict(program='russia_mfa_country_rule',source_id='ru',heading_quote=heading,section_quote=section,closing_quote=closing,nationality_quote=country,rule_quote=quote,general_rule_source_id='general',general_rule_quote=general)
    proof=dict(verifier='ai',verified_at='2026-09-09',source_id='ru',source_url=url,quote=quote,note='Literal Russian rule reviewed for the specified passport.',source_country_section=rule)
    route=dict(passport_nationality=nat,destination_country='RUS',travel_purpose='tourism',travel_document_type='ordinary_passport')
    if change=='diplomatic_only':proof['quote']=rule['rule_quote']='Без виз по дипломатическим и служебным паспортам – до 90 дней.'
    if change=='wrong_country':route['passport_nationality']='THA'
    if change=='neighbour_bleed':rule['section_quote']+='\n'+closing
    if change=='evisa_without_general':rule.pop('general_rule_source_id')
    if change=='forged_general':rule['general_rule_quote']='Optional eVisa eligibility is proof everyone needs a visa.'
    if change.startswith('valid'):mod._proof('disposition','VISA_REQUIRED' if visa else 'VISA_EXEMPT',proof,sources,route,NOW.date())
    else:
        with pytest.raises(ValueError):mod._proof('disposition','VISA_REQUIRED' if visa else 'VISA_EXEMPT',proof,sources,route,NOW.date())
