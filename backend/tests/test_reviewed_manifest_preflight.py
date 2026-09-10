"""Exercise shipped evidence catalogs through the actual deployment importer."""
from datetime import datetime, timezone
import json
import hashlib
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
def test_every_shipped_manifest_reports_exact_current_materialization_status(
        database, name, expected_routes):
    before = database.read_bytes()
    report = importer.materialize(database, manifest=SEEDS / name, now=NOW)
    if name == "reviewed_remaining25_coverage_2026_09_09.json":
        # This exact historical route is superseded by the separately reviewed
        # September10 India border-form overlay. Replaying it must stay invalid.
        assert hashlib.sha256((SEEDS / name).read_bytes()).hexdigest() == "79a518e7cdfbb9fe89ef0a47263644d0d78b9bb8a869388670d9e619e476f0d3"
        old = json.loads((SEEDS / name).read_text())["routes"][22]
        assert old["route"] == {"nationality": "VNM", "destination": "IND",
                                "travel_purpose": "tourism", "travel_document_type": "ordinary_passport"}
        assert report["invalid"] == [{"index": 22, "reason": "existing overlay changes a reviewed field; correct the overlay explicitly first"}]
        assert report["skipped_invalid"] == 1 and report["would_insert"] == 24
        assert all(row["cache_key"] != "VNM|VNM|IND|tourism|default|unknown|v6" for row in report["plan"])
    else:
        assert report["invalid"] == [] and report["skipped_invalid"] == 0, report
        assert report["would_insert"] == expected_routes
    assert report["manifest_routes"] == expected_routes
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


def test_superseded_remaining_manifest_cannot_apply_or_create_a_backup(database, tmp_path):
    before = database.read_bytes()
    backup = tmp_path / "must-not-create.db"
    with pytest.raises(importer.MaterializationError) as error:
        importer.materialize(database, manifest=SEEDS / "reviewed_remaining25_coverage_2026_09_09.json",
                             apply=True, backup=backup, now=NOW)
    report = error.value.report
    assert report["invalid"] == [{"index": 22, "reason": "existing overlay changes a reviewed field; correct the overlay explicitly first"}]
    assert report["applied"] is False and report["inserted"] == 0
    assert database.read_bytes() == before and not backup.exists()
