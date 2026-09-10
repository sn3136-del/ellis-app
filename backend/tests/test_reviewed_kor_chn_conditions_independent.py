"""Independent regressions for the exact pending-warning release boundary."""
from copy import deepcopy
from datetime import date
import pytest
from tests.test_reviewed_kor_chn_conditions import installed, unresolved, reader
from app.visa_snapshot import reviewed_condition_resolution as resolution, verified_overrides as vo
from app.visa_snapshot.records_guard import apply_records_hold

@pytest.mark.parametrize('transit',[{'required':True,'note':None},{'required':None,'note':'A new transit visa is required.'},{'required':None,'note':None,'new_condition':'Must obtain permission'}])
def test_only_exact_empty_transit_wrapper_difference_is_accepted(installed,transit):
    _,_,baseline,_,db,_=installed
    out=reader(baseline);out['guidance']['transit_requirement']=transit
    assert apply_records_hold(baseline['route'],out,db)['detail_pending']

def test_field_authorship_change_prevents_pending_release(installed):
    _,_,baseline,_,db,_=installed
    out=reader(baseline);out['source_verified']=deepcopy(out['source_verified'])
    out['source_verified']['field_provenance']['passport_validity']['subject']['passport_nationality']='USA'
    assert apply_records_hold(baseline['route'],out,db)['detail_pending']

def test_duplicate_registration_cannot_reuse_approved_warning_resolution(installed,monkeypatch):
    _,_,baseline,root,db,_=installed
    old=vo._reviewed_overlay_paths()
    monkeypatch.setattr(vo,'_reviewed_overlay_paths',lambda:old+old)
    result=apply_records_hold(baseline['route'],reader(baseline),db)
    assert result['held'] and result['detail_pending']

def test_policy_deadline_never_borrowed_from_cache_ttl(installed,monkeypatch):
    _,_,baseline,_,db,_=installed
    monkeypatch.setattr(resolution,'_today',lambda:date(2027,1,1))
    out=reader(baseline)
    assert out['guidance']['uncertainty']
    assert apply_records_hold(baseline['route'],out,db)['detail_pending']
