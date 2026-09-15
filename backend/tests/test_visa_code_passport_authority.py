from copy import deepcopy
import pytest
from app.visa_snapshot import source_authority as sa, record_evidence as ev, tstation

URL='https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02009R0810-20240628'
ROUTE={'passport_nationality':'IND','destination_country':'ESP','travel_purpose':'tourism','travel_document_type':'ordinary_passport'}
QUOTE='its validity shall extend at least three months after the intended date of departure from the territory of the Member States'

@pytest.mark.parametrize('field',['passport_validity','passport_validity_requirement'])
def test_visa_code_article12_has_passport_authority(field):
    assert sa.is_competent(URL,ROUTE,field)
    for other in ['https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230',
                  'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018R1240',
                  'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32004L0038',
                  'https://eur-lex.europa.eu/legal-content/EN/TXT/',
                  'https://example.org/810/2009']:
        assert not sa.is_competent(other,ROUTE,field)
    assert not sa.is_competent(URL,dict(ROUTE,destination_country='GBR'),field)
    assert not sa.is_competent(URL,dict(ROUTE,destination_country='IRL'),field)
    assert not sa.is_competent(URL,ROUTE,'processing_time')
    assert not sa.is_competent(URL,ROUTE,'required_documents')


def test_current_passport_quote_can_be_shown_without_changing_any_value():
    g={'disposition':'VISA_REQUIRED','requirement_detail':'visa_required','passport_validity':'General Schengen visa rule: at least three months after intended departure; follow any additional local consular filing instructions.'}
    proof={'source_url':URL,'verified_at':'2026-09-15','verifier':'ai','status':'reviewed','reviewed_value':g['passport_validity'],'quote':QUOTE,'subject':deepcopy(ROUTE)}
    prov={'field_provenance':{'passport_validity':proof}}
    row=tstation.records_for_route(ROUTE,g,prov)[0]
    before=deepcopy((g,prov,row))
    result=ev.for_record(row,ROUTE,g,prov,{})
    assert any(q['quote']==QUOTE and q['source_url']==URL for q in result['entry_requirements'])
    assert (g,prov,row)==before
