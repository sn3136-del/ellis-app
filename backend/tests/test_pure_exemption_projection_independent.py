from copy import deepcopy
import pytest
from app.visa_snapshot import tstation as ts
R={'passport_nationality':'GBR','destination_country':'VNM','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
def mixed(amount=0,currency=None):
 return {'disposition':'VISA_EXEMPT','requirement_detail':'visa_free','visa_products':[
  {'type':'Visa exemption','requirement_detail':'visa_free','disposition':'VISA_EXEMPT','fee':{'amount':amount,'currency':currency},'max_stay_days':45},
  {'type':'Optional eVisa','requirement_detail':'evisa','disposition':'VISA_REQUIRED','fee':{'amount':25,'currency':'USD'},'entry':'single','validity':'90 days','permitted_stay':'Up to 90 days'}]}
@pytest.mark.parametrize('amount,currency',[(0,None),(0,'EUR'),(4,'THB')])
def test_mixed_exemption_currency_owned_and_optional_product_untouched(amount,currency):
 g=mixed(amount,currency);before=deepcopy(g);rows=ts.records_for_route(R,g)
 assert rows[0]['visa_fee_amount']==amount and rows[0]['visa_fee_currency']==currency
 assert rows[0]['validity_duration'] is None and rows[0]['entries'] is None
 assert rows[1]['visa_fee_amount']==25 and rows[1]['visa_fee_currency']=='USD'
 assert rows[1]['entries']=='Single' and rows[1]['validity_duration']==90
 assert g==before
@pytest.mark.parametrize('metadata',[
 {'_held':True,'held':False,'_source_check':'ai-quote'},
 {'_held':False,'_route_held':True,'_source_check':'ai-quote'},
 {'_held':False,'_source_check':'operator-release'},
 {'_held':False,'_source_check':'ai-quote','_disputed_fields':['passport_validity']},
 {'_held':False,'_source_check':'ai-quote','contradictions':['incorrect fee']},
])
def test_private_marker_cannot_override_real_publication_block(metadata):
 r={'visa_requirement':'Visa-free','visa_requirement_detail':'Unconditional Visa-free','application_method':None,'visa_fee_amount':0,'_reviewed_pure_exemption':True,**metadata}
 statuses=ts.field_status(r)
 assert all(statuses[f]=='missing' for f in ('validity_duration','validity_unit','entries','visa_fee_currency'))
def test_zero_unreviewed_exemption_does_not_create_currency_credit():
 row=ts.records_for_route(R,mixed())[0]
 assert not row['_reviewed_pure_exemption']
 assert ts.field_status(row)['visa_fee_currency']=='missing'
