"""Outward attribution uses Ellis labels without rewriting source evidence."""
from copy import deepcopy
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import main
from app.visa_snapshot import tstation, record_evidence
from app.visa_snapshot.reviewer_display import reviewer_label


ROUTE = {"passport_nationality": "CAN", "destination_country": "JPN",
         "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
URL = "https://www.mofa.go.jp/j_info/visit/visa/index.html?reference=OpenAI"
ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "org-b",
         "x-user-id": "operator-1"}


@pytest.mark.parametrize("author", ["Codex", "codex-guard_review-20260913", "CodexAI",
    "OpenAI source review", "OPENAI_agent", "ChatGPT", "chatgpt-review-1",
    "Claude", "ClaudeAI", "claude-agent-20260913", "agent_codex_review"])
def test_branded_compound_reviewer_names_are_display_only(author):
    assert reviewer_label(author, "ai") == "AI review"
    assert reviewer_label(author, " AI ") == "AI review"
    assert reviewer_label(author) == "Ellis review"
    assert reviewer_label(author, "human") == "Ellis review"


@pytest.mark.parametrize("value", [None, "", "Ellis review", "Japan Ministry of Foreign Affairs",
                                  "Dr. Jane Smith", "Ellis official-page check", "medicalcodex",
                                  "https://example.gov/codex", "HTTPS://example.gov/OpenAI",
                                  "www.example.gov/claude", "Medical Codexology"])
def test_unbranded_attribution_is_preserved(value):
    assert reviewer_label(value, "ai") == value


def case(product=False):
    proof = {"source_url": URL, "verified_at": "2026-08-01", "verified_by": "CodexAI",
             "verifier": "ai", "status": "reviewed", "quote": "Government fee: USD 25.",
             "fields": ["disposition", "government_fee"],
             "note": "Exact retained review metadata mentioning OpenAI."}
    g = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa", "confidence": "high",
         "visa_category": "Tourist visa", "government_fee": {"amount": 25, "currency": "USD"},
         "source_url": URL, "corroborating_sources": [{"url": URL,
             "quote": "Exact source text mentioning ChatGPT, Codex and Claude."}]}
    if product:
        p = {"type": "Tourist visa", "disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
             "fee": {"amount": 25, "currency": "USD"}, "entry": "single"}
        p["field_provenance"] = {"disposition": dict(proof, verified_by="claude-reviewer-1",
            quote="A visa is required.", subject={**ROUTE, "product_type": p["type"],
                "disposition": p["disposition"], "requirement_detail": p["requirement_detail"]})}
        g["visa_products"] = [p]
    return g, proof


@pytest.mark.parametrize("product", [False, True])
def test_only_attribution_changes_not_grade_facts_quotations_or_raw_proof(monkeypatch, product):
    g, proof = case(product)
    original = deepcopy((g, proof))
    with monkeypatch.context() as m:
        m.setattr(tstation, "reviewer_label", lambda value, verifier=None: value)
        before = tstation.records_for_route(ROUTE, g, proof)
    after = tstation.records_for_route(ROUTE, g, proof)
    assert len(before) == len(after) == 1
    assert before[0]["data_source"] in {"CodexAI", "claude-reviewer-1"}
    assert after[0]["data_source"] == "AI review"
    assert {k: v for k, v in before[0].items() if k != "data_source"} == {
        k: v for k, v in after[0].items() if k != "data_source"}
    assert (g, proof) == original
    assert proof["verified_by"] == "CodexAI"
    if product:
        assert g["visa_products"][0]["field_provenance"]["disposition"]["verified_by"] == "claude-reviewer-1"
    assert after[0]["source_url"] == URL
    assert after[0]["corroborating_sources"][0]["quote"] == g["corroborating_sources"][0]["quote"]


def test_qc_and_real_xlsx_share_normalized_attribution(monkeypatch):
    g, proof = case()
    row = tstation.records_for_route(ROUTE, g, proof)[0]
    row.update(_cache_key="CAN|CAN|JPN|tourism|default|unknown|v6", _product_index=0,
               _status="KIMI_PRIMARY", _source_check="ai-quote", _disputed=[],
               _publication_state="published", _held=False)
    monkeypatch.setattr(main, "_tstation_rows", lambda *args, **kwargs: [deepcopy(row)])
    with TestClient(main.app) as client:
        qc = client.get("/database/records?nationality=CAN&destination=JPN", headers=ADMIN)
        xlsx = client.get("/database/export.xlsx?nationality=CAN&destination=JPN", headers=ADMIN)
    assert qc.status_code == xlsx.status_code == 200
    assert qc.json()["records"][0]["data_source"] == "AI review"
    sheet = load_workbook(BytesIO(xlsx.content))["Data"]
    cells = dict(zip([c.value for c in sheet[1]], [c.value for c in sheet[2]]))
    assert cells["data_source"] == "AI review"
    assert cells["source_url"] == URL
    assert cells["visa_fee_amount"] == 25
    assert proof["verified_by"] == "CodexAI"


def test_legacy_export_row_is_normalized_without_mutating_row_or_quoted_fields():
    g, proof = case()
    row = tstation.records_for_route(ROUTE, g, proof)[0]
    row["data_source"] = "ClaudeAI"
    before = deepcopy(row)
    cells = dict(zip(tstation.FIELD_ORDER, tstation.export_values(row)))
    assert cells["data_source"] == "Ellis review"
    assert cells["source_url"] == URL
    assert row == before


def test_lazy_quotes_do_not_rewrite_literal_source_text_or_expose_reviewer_ids():
    g, proof = case()
    quote = "Government fee: USD 25. The document title mentions OpenAI and Claude."
    proof["field_provenance"] = {"government_fee": dict(proof, quote=quote,
        reviewed_value=g["government_fee"])}
    row = tstation.records_for_route(ROUTE, g, proof)[0]
    entries = record_evidence.for_record(row, ROUTE, g, proof, {})["visa_fee_amount"]
    assert entries and entries[0]["quote"] == quote
    assert entries[0]["source_url"] == URL
    assert set(entries[0]) <= {"quote", "source_url", "kind", "verified_at"}
