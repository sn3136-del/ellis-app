"""Malformed public inputs cannot start provider work or create arbitrary routes."""
from types import SimpleNamespace
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from app import main, i18n
from app.security import Principal, get_principal
from app.visa_snapshot import kimi_primary as kp, freshness


@pytest.fixture
def public_qc(monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, get_principal,
        lambda: Principal(org_id="platform", user_id="public-qc-input-test", role="quality_tester"))


@pytest.mark.parametrize("field,value", [("travel_purpose", "new-purpose-every-call"),
    ("travel_document_type", "invented-passport"), ("travel_purpose", "x" * 1000)])
def test_invalid_research_scope_is_rejected_before_any_model_call(client, monkeypatch, public_qc, field, value):
    monkeypatch.setattr(kp, "get_route_guidance", lambda *a, **k: pytest.fail("invalid route reached Kimi"))
    monkeypatch.setattr(freshness, "recheck_route", lambda *a, **k: pytest.fail("invalid route started research"))
    response = client.post("/database/routes/research", json={"nationality": "CAN", "destination": "JPN", field: value})
    assert response.status_code == 422


@pytest.mark.parametrize("purpose,document,expected", [
    ("tourist", "ordinary", ("tourism", "ordinary_passport")),
    ("visiting_relatives", "official_passport", ("family_visit", "service_passport")),
    ("study_abroad", "travel_document", ("study", "prc_travel_document")),
    ("business", "diplomatic", ("business", "diplomatic_passport")),
])
def test_research_and_lookup_use_identical_finite_scope(client, monkeypatch, public_qc, purpose, document, expected):
    routes = []
    monkeypatch.setattr(kp, "_cached", lambda *a: SimpleNamespace(verification={}))
    def guidance(db, route, **kwargs):
        routes.append(route)
        return {"guidance": {}, "held": True, "status": "KIMI_UNCERTAIN", "cached": False, "stale": False}
    monkeypatch.setattr(kp, "is_available", lambda: True)
    monkeypatch.setattr(kp, "get_route_guidance", guidance)
    monkeypatch.setattr(freshness, "recheck_route", lambda *a, **k: {"outcome": "test_stub"})
    monkeypatch.setattr(main, "_apply_records_hold", lambda route, out, db: out)
    body = {"nationality": "CAN", "destination": "JPN", "travel_purpose": purpose, "travel_document_type": document}
    for path in ("/database/routes/research", "/database/lookup"):
        result = client.post(path, json=body)
        assert result.status_code == 200, result.text
    assert len(routes) == 3
    assert {(r["travel_purpose"], r["travel_document_type"]) for r in routes} == {expected}


@pytest.mark.parametrize("path,payload", [
    ("/i18n/translate", {"text": "x" * 100_001, "target_lang": "zh-CN"}),
    ("/i18n/catalog", {"entries": {str(i): "x" for i in range(2001)}, "target_lang": "zh-CN"}),
    ("/i18n/catalog", {"entries": {"key": "x" * 1001}, "target_lang": "zh-CN"}),
    ("/i18n/catalog", {"entries": {str(i): "x" * 1000 for i in range(321)}, "target_lang": "zh-CN"}),
    ("/i18n/catalog", {"entries": {"x" * 81: "value"}, "target_lang": "zh-CN"}),
    ("/i18n/catalog", {"entries": {"key": {"nested": "data"}}, "target_lang": "zh-CN"}),
])
def test_translation_bounds_reject_before_model_work(client, monkeypatch, public_qc, path, payload):
    monkeypatch.setattr(i18n, "translate", lambda *a, **k: pytest.fail("oversized translation reached provider"))
    monkeypatch.setattr(i18n, "translate_catalog", lambda *a, **k: pytest.fail("invalid catalog reached provider"))
    assert client.post(path, json=payload).status_code == 422


def test_translation_boundaries_preserve_valid_work(client, monkeypatch, public_qc):
    seen = []
    monkeypatch.setattr(i18n, "translate", lambda text, *a: seen.append(len(text)) or {"status": "ok"})
    monkeypatch.setattr(i18n, "translate_catalog", lambda entries, *a: seen.append(len(entries)) or {"status": "ok"})
    assert client.post("/i18n/translate", json={"text": "x" * 100_000, "target_lang": "zh-CN"}).status_code == 200
    assert client.post("/i18n/catalog", json={"entries": {str(i): "x" * 400 for i in range(800)}, "target_lang": "zh-CN"}).status_code == 200
    assert seen == [100_000, 800]


def test_full_renderer_sized_catalog_and_long_text_are_preserved(client, monkeypatch, public_qc):
    # The shipped catalog exceeds the former silent 800-entry cutoff and has
    # strings longer than400 characters. Translation must retain every key.
    entries = {f"catalog.key.{i}": f"English text{i}" for i in range(1678)}
    entries["ops.missingWhy"] = "x" * 459
    seen = []
    monkeypatch.setattr(i18n, "translate_catalog", lambda values, *a:
        seen.append(values) or {"status": "ok", "entries": values})
    response = client.post("/i18n/catalog", json={"entries": entries, "target_lang": "zh-CN"})
    assert response.status_code == 200
    assert response.json()["entries"] == entries and seen == [entries]


def test_actual_shipped_renderer_catalog_fits_api_without_truncation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to evaluate the shipped renderer catalog")
    repo = Path(__file__).resolve().parents[2]
    catalog = repo / "src/renderer/src/lib/i18n.js"
    script = f"import {{ STRINGS, DEFAULT_LANG }} from {json.dumps(catalog.as_uri())}; process.stdout.write(JSON.stringify(STRINGS[DEFAULT_LANG]));"
    completed = subprocess.run([node, "--input-type=module", "-e", script], cwd=repo,
        capture_output=True, text=True, check=True, timeout=10)
    entries = json.loads(completed.stdout)
    validated = main.CatalogBody(target_lang="zh-CN", entries=entries)
    assert len(entries) > 800 and max(map(len, entries.values())) > 400
    assert validated.entries == entries
