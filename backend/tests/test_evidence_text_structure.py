from app.visa_snapshot.fetching import html_to_text
from app.visa_snapshot.evidence_validator import quote_in_text


def test_minified_official_country_list_retains_heading_members_and_next_section():
    html = '<h2>Nationals of the following countries may enter without a visa:</h2><ul><li>Japan</li><li>Republic of Korea</li></ul><h2>Visa required</h2><ul><li>Other applicants</li></ul>'
    assert html_to_text(html).splitlines() == ['Nationals of the following countries may enter without a visa:',
        'Japan', 'Republic of Korea', 'Visa required', 'Other applicants']


def test_source_code_whitespace_does_not_split_a_rule_and_entities_match_visible_quote():
    html = '<p>Hong Kong SAR\npassport holders may enter\n<b>without a visa</b>. Pay CAN&#36;7&nbsp;for an eTA.</p>'
    text = html_to_text(html)
    assert len(text.splitlines()) == 1
    assert quote_in_text('Hong Kong SAR passport holders may enter without a visa.', text)
    assert quote_in_text('Pay CAN$7 for an eTA.', text)


def test_table_rows_keep_cells_together_and_exclude_script_style_claims():
    html = '<script>Japan is visa-free</script><style>visa required</style><table><tr><th>Fees</th><th>$CAN</th></tr><tr><td>Electronic Travel Authorization</td><td>7.00</td></tr></table>'
    assert html_to_text(html).splitlines() == ['Fees | $CAN', 'Electronic Travel Authorization | 7.00']
