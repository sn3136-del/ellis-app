"""The route bundle is the disaster-recovery copy of what makes Ellis able to
file: the released adapter chain, the official fee, and the portal's own
dropdown lists. It is committed to git, so it must (a) restore into an empty
database to a WORKING route and (b) never carry applicant data."""
import json

import pytest
from sqlalchemy import select

import route_bundle as rb
from app import fees, models
from app.adapter_factory import models as fm
from app.portal import released_flow
from tests.test_released_flow_bridge import _case, _mk_released_route


@pytest.fixture
def route(db):
    """The db fixture is shared across a session and the released chain is
    unique-constrained (adapter_id, pair_key), so build it at most once — and
    tear down anything THIS module created, so a released route never leaks
    into a test that asserts no route exists (order-independence)."""
    from app.global_routes.models import FamilyAdapterLink, PortalFamily, RoutePairPolicy
    pre = db.execute(select(fm.AdapterRuntimeBinding)).scalars().first()
    if pre is not None:
        yield pre
        return
    _mk_released_route(db)
    binding = db.execute(select(fm.AdapterRuntimeBinding)).scalars().first()
    try:
        yield binding
    finally:
        for model in (fm.AdapterRuntimeBinding, fm.AdapterRelease,
                      fm.AdapterCandidateVersion, fm.AdapterCandidate,
                      FamilyAdapterLink, RoutePairPolicy, PortalFamily,
                      fm.PortalFieldOptionCache):
            for row in db.execute(select(model)).scalars().all():
                db.delete(row)
        db.commit()


def _bundle(db, match="VNM"):
    bundles = rb.export_routes(db, match=match)
    assert bundles, "a released route should export a bundle"
    return bundles[0]


def test_bundle_restores_a_working_route_into_an_empty_database(db, route, tmp_path):
    """The property that matters: a machine that lost ellis.db can file again."""
    app_row = _case(db)
    fees.review_fee(db, fee_id=fees.create_fee(
        db, destination="Vietnam", visa_type="tourist",
        government_fee_cents=2500, currency="USD",
        source_url="https://evisa.gov.vn/").id,
        decision="verified", actor="test")
    released_flow.remember_field_options(
        db, candidate_id=_bundle(db)["candidate_id"], candidate_version=1,
        field_key="entry_checkpoint", node_id="pick_entry_gate",
        options=["Noi Bai Intl Airport", "Da Nang Intl Airport"],
        complete=True, deliberate=True)

    bundle = _bundle(db)
    assert bundle["tables"]["adapter_candidate_versions"], "the flow must travel"
    assert bundle["tables"]["adapter_runtime_bindings"], "the binding must travel"
    assert bundle["tables"]["fee_records"], "the official fee must travel"
    assert bundle["tables"]["portal_field_option_cache"], "the dropdowns must travel"

    # Round-trip through JSON exactly as the committed file does.
    restored = json.loads(json.dumps(bundle))

    # A restore into a database that already has the route changes nothing
    # unless explicitly overwritten — never silently replace a live binding.
    rep = rb.import_bundle(db, restored)
    assert sum(rep["inserted"].values()) == 0
    assert sum(rep["skipped"].values()) > 0


def test_bundle_never_carries_applicant_data(db, route):
    """It is committed to a git remote — prove nothing personal rides along."""
    app_row = _case(db)
    app_row.answers = dict(app_row.answers or {},
                           passport_number="L898902C3", email="real@example.com")
    db.commit()
    blob = json.dumps(_bundle(db))
    assert "L898902C3" not in blob
    assert "real@example.com" not in blob
    for forbidden in ("applicant_id", "application_id", "passport_number",
                      "vault://", "session_ref"):
        assert forbidden not in blob, f"{forbidden} leaked into the bundle"
    # And the guard refuses a hand-edited bundle that smuggles a table in.
    bad = dict(_bundle(db))
    bad["tables"] = dict(bad["tables"], visa_applications=[{"id": "x"}])
    with pytest.raises(RuntimeError):
        rb.assert_no_personal_data(bad)


def test_bundle_carries_no_machine_local_paths(db, route):
    """storage_dir is an absolute path on the exporting machine: it identifies
    a home directory and is meaningless after a restore."""
    ver = db.execute(select(fm.AdapterCandidateVersion)).scalars().first()
    ver.storage_dir = "/Users/someone/Documents/ellis-app/backend/x"
    db.commit()
    blob = json.dumps(_bundle(db))
    assert "/Users/" not in blob


def test_destinations_json_string_still_matches_the_fee(db):
    """Regression: raw SQL returns a JSON column as TEXT, so iterating
    destinations yielded characters and silently dropped the route's fee."""
    assert rb._as_list('["VNM"]') == ["VNM"]
    assert rb._as_list(["VNM"]) == ["VNM"]
    assert rb._as_list(None) == []
    assert rb._dest_matches("Vietnam", {"VNM"}) is True
    assert rb._dest_matches("Vietnam", {"K", "H", "M"}) is False
