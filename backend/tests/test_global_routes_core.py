"""Global route coverage — policy core invariants (brief Part 11).

Covers: every destination has a defined outcome; deterministic resolution;
nationality vs residence never conflated; provisional data never verified or
released; non-ordinary documents never inherit ordinary policy; visa-exempt
routes need no submission adapter; manual routes generate no fake portal.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.global_routes import (ELECTRONIC_OUTCOMES, ROUTE_OUTCOMES, baseline,
                               families, resolver)
from app.global_routes.models import PortalFamily, RoutePairPolicy, pair_key
from app.visa_snapshot.models import ConsularJurisdictionRule, RouteMatrixEntry
from app.visa_snapshot.registry import RegistryError, load_registry


@pytest.fixture(scope="module")
def gdb(request):
    """Module-scoped DB with the full pair-policy layer built once."""
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


def test_every_pair_has_exactly_one_defined_row(gdb):
    nats = load_registry("nationalities")["entries"]
    dests = load_registry("countries")["entries"]
    total = gdb.execute(select(func.count(RoutePairPolicy.id))).scalar_one()
    assert total == len(nats) * len(dests)
    # and every row resolves to a defined outcome value
    bad = gdb.execute(select(func.count(RoutePairPolicy.id)).where(
        RoutePairPolicy.route_outcome.notin_(ROUTE_OUTCOMES))).scalar_one()
    assert bad == 0


def test_every_destination_has_rows_for_every_nationality(gdb):
    dests = {c["alpha_3"] for c in load_registry("countries")["entries"]}
    per_dest = dict(gdb.execute(select(
        RoutePairPolicy.destination_country, func.count()).group_by(
        RoutePairPolicy.destination_country)).all())
    n_nat = len(load_registry("nationalities")["entries"])
    assert set(per_dest) == dests
    assert all(c == n_nat for c in per_dest.values())


def test_resolution_is_deterministic(gdb):
    a = resolver.resolve_route(gdb, nationality="DEU", destination="THA",
                               residence="DEU")
    b = resolver.resolve_route(gdb, nationality="DEU", destination="THA",
                               residence="DEU")
    assert a == b


def test_nationality_and_residence_are_independent(gdb):
    """Changing ONLY residence never changes the visa disposition; changing
    ONLY nationality changes the policy row consulted."""
    r1 = resolver.resolve_route(gdb, nationality="IND", destination="CHN",
                                residence="IND")
    r2 = resolver.resolve_route(gdb, nationality="IND", destination="CHN",
                                residence="USA")
    assert r1["disposition"] == r2["disposition"]
    assert r1["route_outcome"] == r2["route_outcome"]
    # nationality change consults a different pair policy
    r3 = resolver.resolve_route(gdb, nationality="SGP", destination="CHN",
                                residence="USA")
    assert r3["inputs"]["passport_nationality"] != r2["inputs"]["passport_nationality"]
    p_ind = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pair_key("IND", "ordinary_passport", "CHN"))).scalars().one()
    p_sgp = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pair_key("SGP", "ordinary_passport", "CHN"))).scalars().one()
    assert p_ind.id != p_sgp.id


def test_residence_changes_jurisdiction_only(gdb):
    """A verified jurisdiction rule for one residence never leaks to another."""
    from app.visa_snapshot import SNAPSHOT_DATE
    gdb.add(ConsularJurisdictionRule(
        snapshot_date=SNAPSHOT_DATE, destination_country="CHN",
        residence_jurisdiction="SGP", competent_post_name="Embassy of China in Singapore",
        competent_post_kind="embassy", competent_post_url="",
        covers_nationalities=[], conditions=[], verification_status="verified"))
    gdb.commit()
    with_rule = resolver.resolve_route(gdb, nationality="IND", destination="CHN",
                                       residence="SGP")
    without = resolver.resolve_route(gdb, nationality="IND", destination="CHN",
                                     residence="FRA")
    if with_rule["jurisdiction"] is not None:
        assert with_rule["jurisdiction"]["status"] == "verified"
        assert without["jurisdiction"]["status"] == "manual_selection_required"
        assert with_rule["disposition"] == without["disposition"]


def test_physical_route_without_residence_requires_manual_jurisdiction(gdb):
    rec = resolver.resolve_route(gdb, nationality="PAK", destination="DZA")
    if rec["disposition"] == "EMBASSY_VISA_REQUIRED":
        assert rec["route_outcome"] == "REQUIRES_MANUAL_JURISDICTION_SELECTION"
        assert rec["jurisdiction"]["status"] == "residence_required"


def test_provisional_rows_are_never_verified_or_released(gdb):
    rows = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.source == "reference_dataset").limit(500)).scalars().all()
    assert rows
    for p in rows:
        assert p.verification_status == "provisional"
        assert p.release_status not in ("released_adapter", "released_workflow",
                                        "legally_unavailable")


def test_verified_rows_come_only_from_official_research_or_self_pairs(gdb):
    rows = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.verification_status == "verified").limit(500)).scalars().all()
    assert rows
    for p in rows:
        assert p.source == "official_research" or p.source_ref == "own-state destination"


def test_non_ordinary_document_never_inherits_ordinary_policy(gdb):
    ordinary = resolver.resolve_route(gdb, nationality="DEU", destination="THA")
    assert ordinary["route_outcome"] != "UNRESOLVED"
    refugee = resolver.resolve_route(gdb, nationality="DEU", destination="THA",
                                     travel_document_type="refugee_travel_document")
    assert refugee["route_outcome"] == "UNRESOLVED"
    assert "never assumed" in refugee["release_reason"]


def test_visa_exempt_routes_require_no_submission_adapter(gdb):
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.route_outcome == "VISA_EXEMPT")).scalars().first()
    assert p is not None
    rec = resolver.resolve_route(gdb, nationality=p.passport_nationality,
                                 destination=p.destination_country)
    assert rec["governing_adapter"]["required"] is False
    assert rec["governing_adapter"]["status"] == "not_required"


def test_manual_routes_generate_no_fake_portal(gdb):
    """An embassy route with no registered portal family shows NO channel and
    no adapter — nothing invented."""
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.route_outcome == "EMBASSY_OR_CONSULATE_APPLICATION",
        RoutePairPolicy.portal_family_id == "")).scalars().first()
    assert p is not None
    rec = resolver.resolve_route(gdb, nationality=p.passport_nationality,
                                 destination=p.destination_country,
                                 residence=p.passport_nationality)
    assert rec["official_channel"] is None
    assert rec["governing_adapter"]["required"] is False


def test_electronic_route_without_family_is_honest_gap(gdb):
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.route_outcome.in_(ELECTRONIC_OUTCOMES),
        RoutePairPolicy.portal_family_id == "")).scalars().first()
    if p is None:
        pytest.skip("every electronic pair has a family in this dataset")
    rec = resolver.resolve_route(gdb, nationality=p.passport_nationality,
                                 destination=p.destination_country)
    assert rec["governing_adapter"]["status"] == "no_official_portal_identified"
    assert "never invented" in rec["governing_adapter"]["reason"]


def test_no_admission_pairs_resolve_to_no_available_route(gdb):
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.disposition == "NO_TOURIST_ROUTE")).scalars().first()
    assert p is not None
    rec = resolver.resolve_route(gdb, nationality=p.passport_nationality,
                                 destination=p.destination_country)
    assert rec["route_outcome"] == "NO_AVAILABLE_TOURIST_ROUTE"


def test_self_pairs_are_explicit_not_applicable(gdb):
    p = gdb.execute(select(RoutePairPolicy).where(
        RoutePairPolicy.pair_key == pair_key("FRA", "ordinary_passport", "FRA"))).scalars().one()
    assert p.disposition == "NOT_APPLICABLE"
    assert p.release_status == "legally_unavailable"


def test_unknown_inputs_are_rejected_not_guessed(gdb):
    with pytest.raises(RegistryError):
        resolver.resolve_route(gdb, nationality="Narnia", destination="THA")
    with pytest.raises(RegistryError):
        resolver.resolve_route(gdb, nationality="USA", destination="Atlantis")


def test_baseline_never_overwrites_researched_matrix_rows(gdb):
    """A matrix row already carrying a researched disposition is preserved."""
    from app.visa_snapshot import SNAPSHOT_DATE
    from app.visa_snapshot.matrix import matrix_key
    key = matrix_key("USA", "ordinary_passport", "IRQ", "tourist_visa")
    row = gdb.execute(select(RouteMatrixEntry).where(
        RouteMatrixEntry.matrix_key == key)).scalars().first()
    if row is None:
        row = RouteMatrixEntry(snapshot_date=SNAPSHOT_DATE, matrix_key=key,
                               passport_nationality="USA",
                               travel_document_type="ordinary_passport",
                               destination_country="IRQ",
                               visa_category="tourist_visa", visa_subtype="any",
                               disposition="EMBASSY_VISA_REQUIRED")
        gdb.add(row)
    else:
        row.disposition = "EMBASSY_VISA_REQUIRED"
    gdb.commit()
    baseline.sync_matrix_dispositions(gdb)
    gdb.refresh(row)
    assert row.disposition == "EMBASSY_VISA_REQUIRED"


def test_dataset_import_requires_provenance(gdb, monkeypatch, tmp_path):
    monkeypatch.setattr(baseline, "BASELINE_PROVENANCE", tmp_path / "missing.json")
    with pytest.raises(RuntimeError, match="provenance"):
        baseline.import_reference_baseline(gdb)
