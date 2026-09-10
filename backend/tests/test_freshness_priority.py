"""Market priorities affect dispatch, never source eligibility or policy."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import pytest
from app.visa_snapshot import freshness, freshness_priority as p, kimi_primary as kp
from app.visa_snapshot.models import KimiRouteGuidanceCache
from tests.test_freshness_sweep_runtime import sweep, setup
NOW=datetime(2026,9,10,8,tzinfo=timezone.utc)


def row(origin,dest,at=None,*,purpose='tourism',suffix=''):
    return SimpleNamespace(cache_key=f'{origin}|{origin}|{dest}|{purpose}|default|unknown|v6{suffix}',
        route={'passport_nationality':origin,'destination_country':dest,'travel_purpose':purpose,
               'travel_document_type':'ordinary_passport'},
        guidance={'confidence':'high','disposition':'VISA_REQUIRED'},
        verification={'grounded_check':{'at':at}})


def test_exact_markets_exclude_extra_destinations_from_manual_priority_list():
    assert p.STATIONS=={'HKG','TWN','JPN','KOR','USA','THA','SGP','MYS','GBR','RUS','AUS',
                        'IDN','PHL','FRA','VNM','ESP','IND','CAN'}
    assert p.MAJOR_DESTINATIONS==p.STATIONS|{'CHN'}
    assert p.priority_tier(row('HKG','CHN').cache_key)==1
    assert p.priority_tier(row('JPN','CHN').cache_key)==2
    for dest in ('DEU','ITA','ARE','CHE','NZL','TUR'):
        assert p.priority_tier(row('JPN',dest).cache_key)==4


def test_six_tiers_and_then_age_order():
    rows=[row('CHN','JPN'),row('JPN','LCA'),row('USA','LCA'),row('KOR','CHN'),row('USA','JPN'),row('IDN','KOR')]
    assert p.prioritize_due_rows(rows,now=NOW)==list(reversed(rows))
    rows=[row('USA','JPN','2026-09-01T00:00:00Z'),row('HKG','JPN','2026-09-02T00:00:00Z'),row('HKG','CHN')]
    assert p.prioritize_due_rows(rows,now=NOW)==[rows[2],rows[0],rows[1]]


@pytest.mark.parametrize('origin,dest',[('IDN','KOR'),('MYS','RUS'),('THA','AUS'),('HKG','VNM')])
def test_four_exact_critical_cases(origin,dest):
    assert p.priority_tier(row(origin,dest).cache_key)==0


@pytest.mark.parametrize('changed',[
 'IDN|IDN|KOR|business|default|unknown|v6','IDN|IDN|KOR|transit|default|unknown|v6',
 'IDN|IDN|KOR|tourism|default|unknown|v6|doc:diplomatic_passport',
 'IDN|IDN|KOR|tourism|default|unknown|v6|doc:emergency_passport',
 'IDN|IDN|KOR|tourism|default|unknown|v6|district:Jakarta',
 'IDN|IDN|KOR|tourism|default|2026-09|v6','IDN|SGP|KOR|tourism|default|unknown|v6',
 'IDN|IDN|KOR|tourism|default|unknown|v6|via:JPN',
 'IDN|IDN|KOR|tourism|2026-09-10|unknown|v6','IDN|IDN|KOR|tourism|default|unknown|v7'])
def test_critical_boost_does_not_expand_to_document_purpose_date_or_other_variants(changed):
    assert p.priority_tier(changed)!=0


def test_incidental_arrival_date_does_not_fork_canonical_policy_priority():
    item=row('IDN','KOR');before=deepcopy(item)
    for date in ('2026-09-10','2027-01-01',None):
        item.route={**item.route,'arrival_date':date,'departure_date':'2027-02-01'}
        assert kp.cache_key(item.route)==item.cache_key
        assert p.prioritize_due_rows([item],now=NOW)[0] is item
        assert p.priority_tier(item.cache_key)==0
    assert item.verification==before.verification and item.guidance==before.guidance


def test_every_eighth_position_takes_global_oldest_remaining():
    major=[row('USA',dest,'2026-09-09T00:00:00Z') for dest in sorted(p.STATIONS)]
    oldest=[row('ZZZ',f'X{i:02}',f'2026-08-{i+1:02}T00:00:00Z') for i in range(3)]
    got=p.prioritize_due_rows(major+oldest,now=NOW)
    assert got[7] is oldest[0] and got[15] is oldest[1]
    assert len(got)==len(major+oldest) and {id(r) for r in got}=={id(r) for r in major+oldest}


def test_continuation_offset_preserves_next_fair_position():
    rows=[row('ZZZ','ZZZ'),row('HKG','JPN','2026-09-09T00:00:00Z')]
    assert p.prioritize_due_rows(rows,now=NOW,dispatch_offset=7)==rows
    assert p.prioritize_due_rows(rows,now=NOW,dispatch_offset=6)==list(reversed(rows))


def test_stable_key_ties_and_no_mutation():
    rows=[row('USA','JPN'),row('HKG','JPN'),row('HKG','CHN')];before=deepcopy(rows)
    got=p.prioritize_due_rows(rows,now=NOW)
    assert [r.cache_key for r in got]==sorted(r.cache_key for r in rows)
    assert rows==before
    assert p.prioritize_due_rows(list(reversed(rows)),now=NOW)==got


@pytest.mark.parametrize('bad',[None,'not-a-date','2999-01-01T00:00:00Z'])
def test_missing_malformed_future_attempts_keep_oldest_precedence(bad):
    a=row('USA','JPN',bad);b=row('HKG','CHN','2020-01-01T00:00:00Z')
    assert p.prioritize_due_rows([b,a],now=NOW)==[a,b]


def test_equivalent_zoned_and_naive_dates_use_stable_tie():
    a=row('USA','JPN','2026-09-01T03:00:00+03:00');b=row('HKG','JPN','2026-09-01T00:00:00')
    assert p.prioritize_due_rows([a,b],now=NOW)==[b,a]


@pytest.mark.parametrize('offset',[-1,True,1.5,'7'])
def test_invalid_offset_rejects(offset):
    with pytest.raises(ValueError):p.prioritize_due_rows([],dispatch_offset=offset)


def test_empty_set_and_duplicate_objects_do_not_create_or_lose_rows():
    assert p.prioritize_due_rows([],now=NOW)==[]
    item=row('HKG','JPN');assert p.prioritize_due_rows([item,item],now=NOW)==[item,item]


def test_real_due_set_excludes_fresh_pending_and_noncanonical_rows(db):
    db.query(KimiRouteGuidanceCache).delete();db.commit()
    now=datetime.now(timezone.utc)
    rows=[row('IDN','KOR',now.isoformat()),row('MYS','RUS'),row('THA','AUS'),
          row('HKG','VNM',suffix='|via:JPN'),row('JPN','KOR',suffix='|doc:diplomatic_passport')]
    rows[1].verification['detail_pending']=True
    rows[2].cache_key=rows[2].cache_key.replace('|unknown|','|2026-09|')
    for r in rows:
        db.add(KimiRouteGuidanceCache(cache_key=r.cache_key,route=r.route,guidance=r.guidance,
                                     verification=r.verification,status='KIMI_PRIMARY'))
    db.commit()
    eligible=freshness.due_rows(db,older_than_hours=.25,limit=10**9)
    assert [r.cache_key for r in eligible]==[rows[-1].cache_key]
    assert p.prioritize_due_rows(eligible,now=now)==eligible
    db.query(KimiRouteGuidanceCache).delete();db.commit()


def test_coordinator_prioritizes_before_existing_cap_without_changing_budget(sweep,monkeypatch):
    rows=[row('ZZZ','ZZZ'),row('USA','JPN'),row('IDN','KOR')];before=deepcopy(rows)
    setup(monkeypatch,rows);monkeypatch.setattr(sweep,'MAX_ROWS',2);captured={}
    def run(keys,deadline,stop,status,save):captured.update(keys=keys,deadline=deadline,status=deepcopy(status));return set()
    monkeypatch.setattr(sweep,'_run_workers',run)
    assert sweep.main()==0
    status=freshness.read_sweep_status()
    assert captured['keys']==[rows[2].cache_key,rows[1].cache_key]
    assert status['row_limit']==status['selected']==2 and status['due_before']==3
    assert status['time_budget_seconds']==status['cycle_time_budget_seconds']==sweep.MAX_SECONDS
    assert status['queue_priority_policy']==p.POLICY_ID and status['oldest_dispatch_every']==8
    assert status['backlog_remaining']==3 and rows==before


def test_resume_filters_attempted_rows_before_order_and_keeps_original_deadline(sweep,monkeypatch):
    now=datetime.now(timezone.utc);start=now-timedelta(hours=4);path=freshness.sweep_status_path()
    old={'schema_version':3,'state':'interrupted','running':False,'started_at':start.isoformat(),
         'cycle_started_at':start.isoformat(),'cycle_time_budget_seconds':18000,'time_budget_seconds':18000,
         'attempted':7,'cycle_scheduled':7}
    path.write_text(json.dumps(old))
    rows=[row('IDN','KOR',now.isoformat()),row('USA','JPN',(start-timedelta(hours=1)).isoformat()),row('ZZZ','AAA')]
    rows[0].verification['grounded_check']['outcome']='checked';setup(monkeypatch,rows);captured=[]
    def run(keys,deadline,stop,status,save):captured.extend(keys);status['scheduled']=len(keys);save();return set()
    monkeypatch.setattr(sweep,'_run_workers',run)
    assert sweep.main()==0
    status=freshness.read_sweep_status()
    assert captured==[rows[2].cache_key,rows[1].cache_key]
    assert status['cycle_started_at']==old['cycle_started_at'] and status['cycle_time_budget_seconds']==18000
    assert 3500<status['time_budget_seconds']<=3600
    assert status['prior_cycle_scheduled']==7 and status['cycle_scheduled']==9
    assert json.loads(path.with_suffix('.previous.json').read_text())==old
    plan=sweep._continuation({**status,'state':'interrupted'},datetime.now(timezone.utc))
    assert plan['prior_cycle_scheduled']==9 and plan['remaining_seconds']<=status['time_budget_seconds']
