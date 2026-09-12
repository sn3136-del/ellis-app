"""guard-20260912 T4: the five report-only proof checks."""
from datetime import datetime, timezone

import pytest

from app.visa_snapshot import kimi_primary, sweep_proof_checks as spc

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
KEY = "X|X|Y|tourism|default|unknown|v6"


def route(nat, dest):
    return {"passport_nationality": nat, "destination_country": dest, "travel_purpose": "tourism",
            "travel_document_type": "ordinary_passport"}


def prov(url, note, fields=("disposition",), **extra):
    base = dict(source_url=url, verified_at="2026-08-30", verified_by="Ellis source audit",
                verifier="ai", note=note, fields=list(fields), field_provenance={})
    base.update(extra)
    return base


def run(route_, merged, provenance, rows=None):
    rows = rows if rows is not None else [{"_held": False, "confidence_level": "High"}]
    return spc.check_route(route_, merged, provenance, rows, now=NOW, canonical_key=KEY)


def codes(findings):
    return sorted(f.code for f in findings)


def test_quoteless_verified_row_is_found():
    # The live MYS to RUS seed note, whose whole text carries no quotation.
    merged = {"disposition": "VISA_REQUIRED", "requirement_detail": "evisa",
              "source_url": "https://evisa.kdmid.ru/"}
    p = prov("https://electronic-visa.kdmid.ru/faq_en.html",
             "Verified 2026-08-30. Consular jurisdiction for RUS.")
    found = run(route("MYS", "RUS"), merged, p)
    assert codes(found) == ["proof_missing_quote"]
    f = found[0]
    assert f.severity == "report" and f.observed == "asserted"
    assert f.evidence["reason"] == "no quotation in the verdict proof"
    assert f.evidence["authority_kind"] == "destination" and f.evidence["host_owner"] == "RUS"
    assert f.evidence["published"] is True and f.evidence["grades"] == ["High"]
    assert f.checked_at == NOW.isoformat()
    assert spc.provenance_quality(p) == "asserted"


def test_quote_that_does_not_support_the_verdict_is_found():
    merged = {"disposition": "VISA_REQUIRED", "requirement_detail": "evisa"}
    p = prov("https://evisa.kdmid.ru/", "Quote: The portal is open from 9 to 5. Scope: MYS")
    found = run(route("MYS", "RUS"), merged, p)
    assert codes(found) == ["proof_missing_quote"]
    assert found[0].observed == "quoted but unsupporting"
    assert found[0].evidence["quote"].startswith("The portal is open")
    assert spc.provenance_quality(p) == "quoted"
    # A supporting, nationality-anchored quotation files nothing.
    good = prov("https://evisa.kdmid.ru/", "Quote: Malaysian citizens must obtain an e-visa before travel. Scope: MYS")
    assert run(route("MYS", "RUS"), merged, good) == []


def test_off_jurisdiction_proof_is_found_with_its_authority_kind():
    merged = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free"}
    p = prov("https://www.immd.gov.hk/eng/services/visas/visit-transit/visit-visa-entry-permit.html",
             "Quote: HKSAR passport holders do not require a visa to visit Cambodia. Scope: HKG")
    found = run(route("HKG", "KHM"), merged, p)
    assert codes(found) == ["proof_off_jurisdiction"]
    assert found[0].evidence["authority_kind"] == "traveller_government"
    assert found[0].evidence["host_owner"] == "HKG"
    third = prov("https://www.mofa.go.jp/visa/", "Quote: Hong Kong passport holders do not require a visa to visit Cambodia. Scope: HKG")
    found = run(route("HKG", "KHM"), merged, third)
    assert found[0].evidence["authority_kind"] == "third_party_government"


def test_eu_law_proof_is_not_reported_after_the_exemption():
    merged = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free"}
    p = prov("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230",
             "Quote: Japan is listed in Annex II: nationals are exempt from the visa requirement. Scope: JPN")
    for dest in ("ITA", "DEU", "FRA"):
        assert "proof_off_jurisdiction" not in codes(run(route("JPN", dest), merged, p))
    assert "proof_off_jurisdiction" in codes(run(route("JPN", "GBR"), merged, p))


def test_nationality_unanchored_quote_is_found():
    merged = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free"}
    p = prov("https://www.evisa.gov.kh/", "Quote: Visa-free entry applies to the countries listed below. Thailand. Vietnam. Scope: HKG")
    found = run(route("HKG", "KHM"), merged, p)
    assert codes(found) == ["proof_nationality_unnamed"]
    assert found[0].expected == "HKG"
    anchored = prov("https://www.evisa.gov.kh/", "Quote: Hong Kong passport holders enjoy visa-free entry. Scope: HKG")
    assert run(route("HKG", "KHM"), merged, anchored) == []


def test_missing_requirement_detail_is_found():
    merged = {"disposition": "VISA_REQUIRED", "requirement_detail": None, "source_url": "https://www.mofa.go.jp/"}
    found = run(route("CHN", "JPN"), merged, None)
    assert codes(found) == ["verdict_detail_missing"]
    assert found[0].expected == ["evisa", "paper_visa"]
    assert run(route("CHN", "JPN"), dict(merged, requirement_detail="paper_visa"), None) == []


def test_unconditional_exemption_on_a_scheme_page_is_found():
    merged = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
              "source_url": "https://www.k-eta.go.kr/portal/apply/index.do"}
    found = run(route("HKG", "KOR"), merged, None)
    assert codes(found) == ["verdict_page_scheme_conflict"]
    assert found[0].evidence["source_url"] == "https://www.k-eta.go.kr/portal/apply/index.do"
    # A detail-less exemption on the same page is the same shape.
    assert "verdict_page_scheme_conflict" in codes(run(route("HKG", "KOR"), dict(merged, requirement_detail=None), None))


def test_conditional_exemption_on_a_scheme_page_is_allowed():
    merged = {"disposition": "VISA_EXEMPT", "requirement_detail": "conditional_visa_free",
              "source_url": "https://www.k-eta.go.kr/portal/apply/index.do"}
    assert run(route("HKG", "KOR"), merged, None) == []
    transit = dict(merged, requirement_detail="transit_visa_free")
    assert run(route("HKG", "KOR"), transit, None) == []
    eta = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "requirement_detail": "eta_electronic_authorization",
           "source_url": "https://www.k-eta.go.kr/portal/apply/index.do"}
    assert run(route("USA", "KOR"), eta, None) == []


def test_verdict_proof_resolves_the_per_field_proof_first():
    p = prov("https://www.mofa.go.jp/", "no quote here",
             field_provenance={"disposition": {"source_url": "https://www.mofa.go.jp/visa/",
                                               "quote": "Chinese nationals must obtain a visa.",
                                               "verified_at": "2026-09-01", "verifier": "ai"}})
    proof = spc.verdict_proof(p)
    assert proof["source_url"] == "https://www.mofa.go.jp/visa/" and proof["fields"] == ["disposition"]
    assert spc.provenance_quality(p) == "quoted"
    assert spc.verdict_proof(prov("https://x.gov", "n", fields=("government_fee",))) is None
    assert spc.provenance_quality(None) == "none"


def test_check_rows_reports_quality_and_counts(db, monkeypatch, tmp_path):
    from app.visa_snapshot import verified_overrides as vo
    from tests import _guard_fixtures as gf
    from app.visa_snapshot import consistency_sweep as cs
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    vo.reload()
    try:
        with gf.seeded(db) as keys:
            evidence = cs.run(db, now=NOW, trigger="test", keys=keys, absence_checks=False, coverage=False)
    finally:
        vo.reload()
    summary = evidence["proof"]
    assert set(summary["counts"]) == set(spc.CODES)
    assert sum(summary["provenance_quality"].values()) == 12
    # The Korea row on the K-ETA portal is the scheme-page conflict.
    conflicts = [f for f in evidence["findings"] if f["code"] == "verdict_page_scheme_conflict"]
    assert any(f["cache_key"].startswith("HKG|HKG|KOR") for f in conflicts)
    # Hong Kong to Cambodia rests on the traveller's own government page.
    off = [f for f in evidence["findings"] if f["code"] == "proof_off_jurisdiction"]
    assert any(f["cache_key"].startswith("HKG|HKG|KHM") and f["evidence"]["authority_kind"] == "traveller_government"
               for f in off)
    # Nothing here is blocking, and every finding carries the evidence keys.
    for f in evidence["findings"]:
        if f["code"] in spc.CODES:
            assert f["severity"] == "report"
            assert {"source_url", "published", "grades"} <= set(f["evidence"])
            assert len(f["evidence"].get("quote") or "") <= spc.QUOTE_CHARS
