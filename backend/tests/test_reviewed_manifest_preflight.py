"""Exercise shipped evidence catalogs through the actual deployment importer."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import materialize_reviewed_routes as importer

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "data/database_seed"
MANIFESTS = [
    ("reviewed_phl_coverage_2026_09_09.json", 17),
    ("reviewed_us_can12_coverage_2026_09_09.json", 12),
    ("reviewed_rus_aus_idn15_coverage_2026_09_09.json", 15),
    ("reviewed_remaining25_coverage_2026_09_09.json", 25),
]
NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path, monkeypatch):
    # Use shipped overrides, including their field/effective-date validation.
    monkeypatch.setattr(importer.overrides, "OVERRIDES", SEEDS / "verified_overrides.json")
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "no-operator-overrides.json"))
    importer.overrides.reload()
    path = tmp_path / "preflight.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE kimi_route_guidance_cache (cache_key TEXT PRIMARY KEY)")
        db.execute("INSERT INTO kimi_route_guidance_cache VALUES ('preserved-fixture-row')")
    yield path
    importer.overrides.reload()


@pytest.mark.parametrize("name,expected_routes", MANIFESTS, ids=[name for name, _ in MANIFESTS])
def test_every_shipped_manifest_passes_actual_read_only_materialization_preflight(
        database, name, expected_routes):
    before = database.read_bytes()
    report = importer.materialize(database, manifest=SEEDS / name, now=NOW)
    assert report["invalid"] == [] and report["skipped_invalid"] == 0, report
    assert report["manifest_routes"] == expected_routes
    assert report["would_insert"] == expected_routes
    assert report["applied"] is False and report["inserted"] == 0 and report["backup"] is None
    assert report["review_is_not_grounded_verification"] is True
    assert database.read_bytes() == before


def test_preflight_covers_every_committed_reviewed_coverage_manifest():
    assert {p.name for p in SEEDS.glob("reviewed_*coverage_*.json")} == {name for name, _ in MANIFESTS}


def test_unused_empty_capture_is_rejected_before_any_database_change(database, tmp_path):
    data = json.loads((SEEDS / MANIFESTS[0][0]).read_text())
    data["sources"].append({"id": "unused-failed-capture", "url": "https://www.ica.gov.sg/",
                            "text": "", "checked_at": "2026-09-09", "reading_method": "fetched_text"})
    bad_manifest = tmp_path / "empty-capture.json"
    bad_manifest.write_text(json.dumps(data))
    before = database.read_bytes()
    with pytest.raises(ValueError, match="captured source"):
        importer.materialize(database, manifest=bad_manifest, now=NOW)
    assert database.read_bytes() == before
