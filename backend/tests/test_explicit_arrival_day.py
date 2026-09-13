"""Named arrival days must survive deterministic parsing and policy projection."""
from copy import deepcopy
from datetime import date
import datetime

import pytest
from app.visa_snapshot import kimi_primary as kp, scheduled_policies as sp

@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 13)
    monkeypatch.setattr(datetime, 'date', FixedDate)
    monkeypatch.setattr(sp, '_today', lambda: date(2026, 9, 13))

@pytest.mark.parametrize('text,expected', [
    ('September 15 2026','2026-09-15'),
    ('September 15,2027','2027-09-15'),
    ('15 Sep.,2027','2027-09-15'),
    ('September 14, 2026','2026-09-14'),
    ('September 15th 2026','2026-09-15'),
    ('September the 15th, 2026','2026-09-15'),
    ('15 September 2026','2026-09-15'),
    ('15th of September 2026','2026-09-15'),
    ('Sep. 15, 2026','2026-09-15'),
    ('Sept 15 2026','2026-09-15'),
    ('September 15','2026-09-15'),
    ('14th September','2026-09-14'),
    ('September 1','2027-09-01'),
    ('September 1 this year','2026-09-01'),
    ('September 15 next year','2027-09-15'),
    ('May 15 2027','2027-05-15'),
    ('29 February 2028','2028-02-29'),
    ('December 31st 2026','2026-12-31'),
    ('January 1st','2027-01-01'),
])
def test_explicit_day_is_never_replaced_by_month_start(text,expected):
    assert kp._extract_arrival('Chinese passport visiting Thailand on '+text) == expected

@pytest.mark.parametrize('text', ['September 31 2026','April 31st 2026','February 29 2027','September 00 2026','32 September 2026'])
def test_impossible_explicit_date_does_not_fall_back_to_first_day(text):
    assert kp._extract_arrival(text) is None

@pytest.mark.parametrize('text,expected', [
    ('September 2026','2026-09-01'),('in September','2026-09-01'),
    ('January 2027','2027-01-01'),('next month','2026-10-01'),
    ('明年3月去泰国','2027-03-01'),('中国护照9月15日去泰国','2026-09-15'),
    ('I may visit Thailand',None),('travelling tomorrow',None),
])
def test_existing_month_and_supported_relative_contracts_are_preserved(text,expected):
    assert kp._extract_arrival(text) == expected

@pytest.mark.parametrize('when,stay', [('September 14, 2026',60),('September 15th, 2026',30),('15 September 2026',30),('Sep. 15',30)])
def test_deterministic_question_uses_exact_policy_day_without_forking_cache(when,stay):
    # The installed Hong Kong schedule has the same 15 September boundary;
    # no model, fixture-only converter or uninstalled CHN data is involved.
    parsed = kp.parse_question('Hong Kong passport holder visiting Thailand for tourism on '+when)
    route = dict(parsed, passport_nationality=parsed['nationality'], destination_country=parsed['destination'])
    assert route is not None
    assert route['passport_nationality'] == 'HKG' and route['destination_country'] == 'THA'
    assert route['arrival_date'] in ('2026-09-14','2026-09-15')
    guidance = {'disposition':'VISA_EXEMPT','requirement_detail':'unconditional_visa_free','permitted_stay':'60 days','permitted_stay_days':60,'government_fee':{'amount':0,'currency':None},'visa_products':[],'exceptions':[]}
    original = deepcopy(guidance)
    selected,proof = sp.apply(guidance,None,route)
    assert selected['permitted_stay_days'] == stay
    assert not kp.serve_time_invariants(selected)
    assert guidance == original
    assert kp.cache_key(route) == kp.cache_key(dict(route,arrival_date=None))
