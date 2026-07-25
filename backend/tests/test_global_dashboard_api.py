"""Global coverage dashboard + API (brief Parts 10/11).

Covers: single aggregated payload with every brief metric; admin-only access;
honest released counting; applicant endpoint free of operator language; no
secrets or PII in any payload; inventory export shape.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.global_routes import baseline, dashboard, families, inventory
from app.global_routes.models import RoutePairPolicy

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "orgG", "X-User-Id": "u1"}
ADMIN_H = {"Authorization": "Bearer admin-token", "X-Org-Id": "orgG", "X-User-Id": "admin"}


@pytest.fixture(scope="module")
def gdb(request):
    from app.db import SessionLocal, create_all
    create_all()
    db = SessionLocal()
    baseline.import_reference_baseline(db)
    baseline.apply_research_overrides(db)
    baseline.ensure_full_pair_coverage(db)
    families.sync_families(db)
    families.assign_families_to_pairs(db)
    from app.global_routes.orchestrator import recompute_release_statuses
    recompute_release_statuses(db)
    request.addfinalizer(db.close)
    return db


def test_dashboard_payload_has_every_brief_metric(gdb):
    cov = dashboard.coverage(gdb)
    totals = cov["totals"]
    assert totals["route_pair_combinations"] > 50000
    assert set(cov["by_outcome"]) >= {"visa_exempt", "entry_preparation",
                                      "electronic_routes", "visa_on_arrival",
                                      "physical_or_manual_routes",
                                      "no_available_tourist_route", "unresolved"}
    assert "portal_families" in cov and "adapters" in cov
    assert "failed_tasks" in cov and "last_family_verification" in cov
    assert "honesty_note" in cov


def test_released_coverage_counts_only_verified(gdb):
    cov = dashboard.coverage(gdb)
    by_release = cov["by_release_status"]
    released = cov["totals"]["released_combinations"]
    assert released == (by_release.get("released_adapter", 0) +
                        by_release.get("released_workflow", 0) +
                        by_release.get("legally_unavailable", 0))
    # provisional rows never count
    assert by_release.get("defined_provisional", 0) > 0
    assert released + by_release.get("defined_provisional", 0) <= \
        cov["totals"]["route_pair_combinations"]


def test_admin_endpoints_require_admin(client, gdb):
    r = client.get("/admin/global/coverage", headers=H)
    assert r.status_code == 403
    r = client.get("/admin/global/coverage", headers=ADMIN_H)
    assert r.status_code == 200
    assert r.json()["totals"]["route_pair_combinations"] > 0
    r = client.get("/admin/global/unsupported?limit=5", headers=ADMIN_H)
    assert r.status_code == 200
    assert "unresolved_total" in r.json()


def test_applicant_route_outcome_has_no_operator_language(client, gdb):
    r = client.get("/global/route-outcome",
                   params={"nationality": "DEU", "destination": "THA",
                           "residence": "DEU"}, headers=H)
    assert r.status_code == 200
    body = json.dumps(r.json()).lower()
    for banned in ("admin", "operator", "orchestrator", "build_request",
                   "candidate", "gate_report", "dedup", "quarantine"):
        assert banned not in body, banned
    assert r.json()["route_outcome"]
    assert "disclaimer" in r.json()


def test_applicant_route_outcome_rejects_unknown_input(client, gdb):
    r = client.get("/global/route-outcome",
                   params={"nationality": "Narnia", "destination": "THA"},
                   headers=H)
    assert r.status_code == 400


def test_no_secrets_or_pii_in_dashboard(gdb):
    cov = json.dumps(dashboard.coverage(gdb)).lower()
    for banned in ("passport_number", "api_key", "authorization: bearer",
                   "moonshot", "browserbase_api"):
        assert banned not in cov


def test_inventory_export_shapes(gdb, tmp_path, monkeypatch):
    import app.global_routes.inventory as inv_mod
    monkeypatch.setattr(inv_mod, "SNAPSHOT_DIR", tmp_path)
    from sqlalchemy import func
    out = inventory.export_inventory(gdb)
    assert out["destinations"] == 249
    n_pairs = gdb.execute(select(func.count(RoutePairPolicy.id))).scalar_one()
    assert out["pairs_exported"] == n_pairs > 50000
    from app.visa_snapshot import SNAPSHOT_DATE
    lines = (tmp_path / SNAPSHOT_DATE / "destination_inventory.jsonl").read_text().splitlines()
    assert len(lines) == 249
    row = json.loads(lines[0])
    assert {"destination_country", "channels", "portal_families",
            "inbound_outcomes"} <= set(row)
    fam_lines = (tmp_path / SNAPSHOT_DATE / "global_portal_families.jsonl").read_text().splitlines()
    assert len(fam_lines) >= 90


def test_every_destination_inventory_channel_is_backed_by_data(gdb):
    inv = inventory.build_inventory(gdb)
    assert len(inv) == 249
    for row in inv:
        # channels only appear when an outcome resolves there — never invented
        for ch in row["channels"]:
            assert ch in inventory._OUTCOME_TO_CHANNEL.values()
        for fam in row["portal_families"]:
            assert fam["verification_status"] in (
                "seed_unverified", "verified_official_domain", "verified_live",
                "conflicted", "unavailable")
