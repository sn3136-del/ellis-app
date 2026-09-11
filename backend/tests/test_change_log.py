

def test_every_field_an_operator_may_correct_is_named_in_the_change_log():
    """A correction an operator may make has to be traceable by name.

    The status gate for a reviewed finding needs a change-log row naming
    every field of the finding's column, so a field that is overridable but
    unwatched leaves the finding open for ever even though the record was
    corrected.
    """
    from app.visa_snapshot import verified_overrides, change_log
    unwatched = sorted(set(verified_overrides.OVERRIDABLE) - set(change_log._WATCHED))
    assert unwatched == [], unwatched


def test_a_passport_rule_and_a_photo_rule_show_up_in_the_diff():
    from app.visa_snapshot import change_log
    old = {"passport_validity_requirement": {"kind": "months_after_departure", "months": 6},
           "photo_requirements": ["35x45mm"]}
    new = {"passport_validity_requirement": {"kind": "valid_through_departure", "months": 0},
           "photo_requirements": ["35x45mm", "white background"]}
    fields = change_log.diff(old, new)
    assert set(fields) == {"passport_validity_requirement", "photo_requirements"}
    assert fields["passport_validity_requirement"]["to"] == {"kind": "valid_through_departure", "months": 0}
