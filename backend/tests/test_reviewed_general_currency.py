"""Literal parenthesized official fees must work without widening evidence."""
import pytest

from scripts.convert_reviewed_general_batch import _check_proof, _monetary_text
from scripts.prepare_reviewed_product_patch import PatchRejected

URL = 'https://cdn.mt.gov.sa/tourist-visa-regulations.pdf'
QUOTE = ('The amount of SAR (300) shall be collected as a fee for a Tourist Visa, '
         'as prescribed by Royal Decree No. (2) dated 5/1/1441 AH.')
ROUTE = {'passport_nationality':'CHN','destination_country':'SAU','travel_purpose':'tourism',
         'travel_document_type':'ordinary_passport'}


def test_parenthesized_official_currency_amount_keeps_its_literal_evidence():
    proof = {'status':'reviewed','verifier':'ai','verified_at':'2026-09-09',
             'scope_note':'Tourist visa government fee only; insurance is additional.',
             'evidence':[{'source_id':'s','source_url':URL,'quote':QUOTE}]}
    sources = {'s':{'url':URL,'text':QUOTE}}
    assert _check_proof(proof,sources,ROUTE,'fee',{'amount':300,'currency':'SAR'}) is proof
    assert proof['evidence'][0]['quote'] == QUOTE and sources['s']['text'] == QUOTE
    for wrong in [{'amount':395,'currency':'SAR'},{'amount':300,'currency':'USD'}]:
        with pytest.raises(PatchRejected,match='fee amount is not in its evidence'):
            _check_proof(proof,sources,ROUTE,'fee',wrong)


@pytest.mark.parametrize('text',['SAR reference (300)', 'SAR (300 days)', 'SAR (300/395)', 'SAR (-300)'])
def test_parentheses_normalization_does_not_turn_other_figures_into_fees(text):
    assert _monetary_text(text,'SAR') == text
