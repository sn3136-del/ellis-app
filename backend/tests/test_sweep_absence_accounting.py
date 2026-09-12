"""guard-20260912 T8: four honest populations behind the two labels.

The workbook keeps printing a value, "Not publicly available" or "Not
applicable". Underneath, an absence proven on a competent page is a
documented absence and an absence asserted without proof is an
undocumented gap, so relabelling a gap can never raise completeness once
ELLIS_ABSENCE_STRICT is on, and the report phase measures it before then."""
import json
from datetime import datetime, timezone

import pytest

from app.visa_snapshot import tstation, verified_overrides as vo

PROOF = {"source_url": "https://evisa.gov.vn/",
         "quote": "The e-Visa portal states no end date for this visa policy.",
         "checked_at": "2026-09-01"}
HOME_PAGE = {"source_url": "https://www.immd.gov.hk/eng/services/visas/visit-transit/visit-visa-entry-permit.html",
             "quote": "The Hong Kong page lists no end date for Vietnam.", "checked_at": "2026-09-01"}


def route(nat="HKG", dest="VNM"):
    return {"passport_nationality": nat, "destination_country": dest, "travel_purpose": "tourism",
            "travel_document_type": "ordinary_passport"}


def guidance(unpublished=(), evidence=None, **extra):
    g = {"disposition": "VISA_REQUIRED", "requirement_detail": "evisa", "visa_category": "tourist e-Visa",
         "source_url": "https://evisa.gov.vn/", "official_portal_url": "https://evisa.gov.vn/",
         "application_channel": "online_portal", "government_fee": {"amount": 25, "currency": "USD"},
         "permitted_stay": "90 days", "permitted_stay_days": 90, "processing_time": "3 working days",
         "required_documents": ["passport", "photo"],
         "visa_products": [{"type": "Single-entry tourist e-Visa", "entry": "single", "validity": "90 days",
                            "max_stay_days": 90, "fee": {"amount": 25, "currency": "USD"}}],
         "unpublished_fields": list(unpublished)}
    if evidence is not None:
        g["unpublished_evidence"] = evidence
    g.update(extra)
    return g


def record(g, rt=None):
    return tstation.records_for_route(rt or route(), g, None, "2026-09-01", "2026-12-01")[0]


@pytest.fixture
def files(tmp_path, monkeypatch):
    seed, operator = tmp_path / "seed.json", tmp_path / "operator.json"
    seed.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", seed)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(operator))
    monkeypatch.delenv("ELLIS_ABSENCE_STRICT", raising=False)
    vo.reload()
    yield seed, operator
    vo.reload()


def test_four_populations_partition_the_25_cells():
    visa = record(guidance(["info_validity"], {"info_validity": PROOF}))
    exempt = record({"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
                     "visa_category": "Visa-free", "source_url": "https://evisa.gov.vn/",
                     "application_channel": "not_required", "permitted_stay_days": 45,
                     "permitted_stay": "45 days"})
    for rec in (visa, exempt):
        result = tstation.absence_populations(rec)
        assert set(result["populations"]) == set(tstation.CONTRACT_FIELDS)
        assert len(tstation.CONTRACT_FIELDS) == 25
        assert set(result["populations"].values()) <= set(tstation.ABSENCE_POPULATIONS)
        assert sum(result["counts"].values()) == 25
        assert set(result["undocumented_reasons"]) == {f for f, p in result["populations"].items()
                                                       if p == "undocumented_gap"}
    assert tstation.absence_populations(visa)["populations"]["info_validity"] == "documented_absence"
    assert tstation.absence_populations(exempt)["counts"]["not_applicable"] > 0


def test_unproven_absence_is_an_undocumented_gap():
    rec = record(guidance(["info_validity"]))
    result = tstation.absence_populations(rec)
    assert result["populations"]["info_validity"] == "undocumented_gap"
    assert result["undocumented_reasons"]["info_validity"] == "asserted_absence"
    # The label it prints is the same as a documented absence's.
    cells = dict(zip(tstation.FIELD_ORDER, tstation.export_values(rec)))
    assert cells["info_validity"] == tstation.NOT_PUBLICLY_AVAILABLE
    # A cell nobody researched is a gap for a different reason.
    gap = record(guidance([], required_documents=None))
    assert tstation.absence_populations(gap)["undocumented_reasons"]["required_documents"] == "not_researched"


def test_proven_absence_counts_complete(monkeypatch):
    proven = record(guidance(["info_validity"], {"info_validity": PROOF}))
    unproven = record(guidance(["info_validity"]))
    assert proven["_unpublished_proven"] == ["info_validity"]
    assert tstation.absence_populations(proven)["documented_by"]["info_validity"] == "evidence"
    # Report phase: the strict denominator is computed in every switch state.
    assert tstation.completeness(proven, strict=True) == tstation.completeness(proven, strict=False)
    assert tstation.completeness(unproven, strict=True) < tstation.completeness(unproven, strict=False)
    # Switch off (shipped): nothing a record shows moves.
    monkeypatch.delenv("ELLIS_ABSENCE_STRICT", raising=False)
    assert tstation.field_status(unproven)["info_validity"] == "not-published"
    # Switch on: only the proven absence stays documented.
    monkeypatch.setenv("ELLIS_ABSENCE_STRICT", "1")
    assert tstation.field_status(proven)["info_validity"] == "not-published"
    assert tstation.field_status(unproven)["info_validity"] == "missing"


def test_absence_proof_on_a_non_competent_page_is_rejected_at_write_and_quarantined_at_load(files, monkeypatch):
    seed, _operator = files
    entry = {"route": {"nationality": "HKG", "destination": "VNM", "travel_purpose": "tourism"},
             "source_url": "https://evisa.gov.vn/", "verified_at": "2026-09-12", "verifier": "human",
             "verified_by": "Trip.com operations (test)", "note": "Checked the e-Visa portal for an end date.",
             "fields": {"unpublished_fields": ["info_validity"],
                        "unpublished_evidence": {"info_validity": HOME_PAGE}}}
    with pytest.raises(ValueError) as err:
        vo.append_operator_entry(entry)
    assert "competent for the destination" in str(err.value)
    with pytest.raises(ValueError):
        vo.append_operator_entry(json.loads(json.dumps(entry).replace("info_validity\"]", "processing_min_days\"]")))
    # A proof that stands is accepted.
    good = json.loads(json.dumps(entry))
    good["fields"]["unpublished_evidence"] = {"info_validity": PROOF}
    vo.append_operator_entry(good)
    # Load: a bad proof is dropped together with the absence it claimed.
    seed.write_text(json.dumps([{
        "route": {"nationality": "HKG", "destination": "KHM", "travel_purpose": "tourism"},
        "source_url": "https://www.evisa.gov.kh/", "verified_at": "2026-09-01", "verifier": "ai",
        "verified_by": "Ellis source audit", "note": "Checked the Cambodian e-Visa page.",
        "fields": {"unpublished_fields": ["info_validity", "processing_min_days"],
                   "unpublished_evidence": {
                       "info_validity": HOME_PAGE,
                       "processing_min_days": dict(PROOF, source_url="https://www.evisa.gov.kh/")}}}]))
    vo.reload()
    hit = vo.find(route("HKG", "KHM"))
    assert hit["fields"]["unpublished_fields"] == ["processing_min_days"]
    assert set(hit["fields"]["unpublished_evidence"]) == {"processing_min_days"}
    # Under the switch an absence with no proof is refused and quarantined too.
    monkeypatch.setenv("ELLIS_ABSENCE_STRICT", "1")
    bare = json.loads(json.dumps(entry))
    bare["fields"].pop("unpublished_evidence")
    with pytest.raises(ValueError) as err:
        vo.append_operator_entry(bare)
    assert "ELLIS_ABSENCE_STRICT" in str(err.value)
    seed.write_text(json.dumps([{
        "route": {"nationality": "HKG", "destination": "KHM", "travel_purpose": "tourism"},
        "source_url": "https://www.evisa.gov.kh/", "verified_at": "2026-09-01", "verifier": "ai",
        "verified_by": "Ellis source audit", "note": "Checked the Cambodian e-Visa page.",
        "fields": {"unpublished_fields": ["info_validity"], "processing_time": "3 working days"}}]))
    vo.reload()
    assert "unpublished_fields" not in vo.find(route("HKG", "KHM"))["fields"]


def test_workbook_still_prints_only_the_two_labels_verbatim():
    records = [record(guidance(["info_validity"], {"info_validity": PROOF})),
               record(guidance(["info_validity"])),
               record(guidance([], required_documents=None))]
    shown = [dict(zip(tstation.FIELD_ORDER, tstation.export_values(rec))) for rec in records]
    # A documented absence and an asserted one print the same label.
    assert shown[0]["info_validity"] == shown[1]["info_validity"] == "Not publicly available"
    for rec, cells in zip(records, shown):
        statuses = tstation.field_status(rec)
        for f, value in cells.items():
            if statuses[f] != "filled" and f not in ("max_stay_duration", "validity_duration"):
                assert value in (tstation.NOT_PUBLICLY_AVAILABLE, tstation.NOT_APPLICABLE), (f, value)


def test_the_word_missing_is_never_a_cell_value(monkeypatch):
    records = [record(guidance(["info_validity"])), record(guidance([], required_documents=None)),
               record(guidance(["info_validity", "required_documents"], required_documents=None))]
    for strict in ("", "1"):
        monkeypatch.setenv("ELLIS_ABSENCE_STRICT", strict)
        for rec in records:
            for value in tstation.export_values(rec):
                assert not (isinstance(value, str) and "missing" in value.lower()), value


def test_sweep_files_only_published_cells_whose_grade_depends_on_them():
    from app.visa_snapshot import sweep_absence

    class Row:
        cache_key = "HKG|HKG|VNM|tourism|default|unknown|v6"
    published = record(guidance(["info_validity", "processing_min_days"]))
    held = dict(published, _held=True)
    proven = record(guidance(["info_validity"], {"info_validity": PROOF}))
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    out = sweep_absence.check_rows([(Row(), {"qc_rows": [published, held, proven]})], now=now)
    # info_validity is required and asserted on the published record: filed.
    # processing_min_days is optional, the held record is not published, and
    # the proven record is documented: none of those files.
    assert [(f.code, f.field) for f in out["findings"]] == [("absence_undocumented", "info_validity")]
    summary = out["summary"]
    assert summary["record_count"] == 3 and summary["undocumented_findings"] == 1
    assert sum(summary["populations"].values()) == 75
    assert summary["info_validity"]["documented_absence"] == 1
