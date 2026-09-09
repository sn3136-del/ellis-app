"""Stopovers never create or inherit a second destination decision."""
from copy import deepcopy
import json

import pytest

from app.visa_snapshot import kimi_primary as kp, verified_overrides as vo
from app.visa_snapshot.models import KimiRouteGuidanceCache
from tests.test_freshness import STALE_JPN

ROUTE = {"passport_nationality": "HKG", "destination_country": "VNM", "travel_purpose": "tourism",
         "travel_document_type": "ordinary_passport"}
REQUIRED = {**STALE_JPN, "requirement_detail": "paper_visa", "source_url": "https://evisa.gov.vn/"}
FREE = {**STALE_JPN, "disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
        "application_channel": "not_required", "visa_products": [], "government_fee": {"amount": 0, "currency": None},
        "source_url": "https://www.ica.gov.sg/enter-transit-depart/transiting-through-singapore"}


@pytest.fixture(autouse=True)
def isolate(db, tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"; path.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", path); vo.reload()
    db.query(KimiRouteGuidanceCache).delete(); db.commit()
    yield
    kp.set_provider(None); vo.reload()


def seed(db, route, guidance, *, release=False, key=None):
    row = KimiRouteGuidanceCache(cache_key=key or kp.cache_key(route), route=route, guidance=deepcopy(guidance),
        status=kp.STATUS_PRIMARY, missing_fields=[], contradictions=[], model="fixture",
        verification={"operator_released": {"by": "operator"}} if release else {})
    db.add(row); db.commit()
    return row


def verify_transit(nat="HKG", doc="ordinary_passport"):
    vo.OVERRIDES.write_text(json.dumps([{"route": {"nationality": nat, "destination": "SGP",
        "travel_purpose": "transit", "travel_document_type": doc}, "fields": {"disposition": "VISA_EXEMPT",
        "requirement_detail": "unconditional_visa_free"}, "source_url": FREE["source_url"],
        "verified_at": "2026-09-09", "verifier": "ai", "note": "Synthetic checked transit test evidence"}]))
    vo.reload()


def test_canonical_required_release_never_releases_legacy_via_exempt(db):
    canonical = seed(db, ROUTE, REQUIRED, release=True)
    seed(db, {**ROUTE, "transit_countries": ["SGP"]}, FREE, key=canonical.cache_key + "|via:SGP")
    kp.set_provider(lambda *_: (_ for _ in ()).throw(AssertionError("must serve canonical")))
    out = kp.get_route_guidance(db, {**ROUTE, "transit_countries": ["SGP"]})
    assert out["guidance"]["disposition"] == "VISA_REQUIRED" and out["operator_released"]
    transit = out["guidance"]["transit_requirement"]
    assert transit["required"] is None and transit["checks"][0]["status"] == "unknown"
    assert transit["checks"][0]["source_url"].startswith("https://www.ica.gov.sg/")
    assert not out["held"]


def test_two_stopovers_make_one_destination_decision_and_no_persisted_transit(db):
    calls = []
    def provider(system, user):
        calls.append(json.loads(user))
        return deepcopy(REQUIRED)
    kp.set_provider(provider)
    first = kp.get_route_guidance(db, {**ROUTE, "transit_countries": ["SGP"]})
    second = kp.get_route_guidance(db, {**ROUTE, "transit_countries": ["JPN"]})
    assert first["guidance"]["disposition"] == second["guidance"]["disposition"] == "VISA_REQUIRED"
    assert len(calls) == 1 and "transit_countries" not in calls[0]
    row = db.query(KimiRouteGuidanceCache).one()
    assert "|via:" not in row.cache_key
    assert "transit_countries" not in (row.route or {})
    assert (row.guidance.get("transit_requirement") or {}).get("required") is None


def test_only_separately_verified_exact_transit_purpose_and_document_counts(db):
    seed(db, ROUTE, REQUIRED, release=True)
    tr = {**ROUTE, "destination_country": "SGP", "travel_purpose": "transit"}
    seed(db, tr, FREE)
    # Unverified transit rows cannot inherit the destination's release.
    out = kp.get_route_guidance(db, {**ROUTE, "transit_countries": ["SGP"]})
    assert out["guidance"]["transit_requirement"]["required"] is None
    verify_transit()
    out = kp.get_route_guidance(db, {**ROUTE, "transit_countries": ["SGP"]})
    assert out["guidance"]["transit_requirement"]["required"] is False
    assert out["guidance"]["transit_requirement"]["checks"][0]["source_verified"]["verifier"] == "ai"


@pytest.mark.parametrize("other", [{"travel_purpose": "tourism"}, {"travel_document_type": "diplomatic_passport"},
                                    {"passport_nationality": "CHN"}])
def test_transit_never_borrows_different_purpose_document_or_passport(db, other):
    seed(db, ROUTE, REQUIRED, release=True)
    tr = {**ROUTE, "destination_country": "SGP", "travel_purpose": "transit", **other}
    seed(db, tr, FREE, release=True)
    verify_transit(tr["passport_nationality"], tr["travel_document_type"])
    out = kp.get_route_guidance(db, {**ROUTE, "transit_countries": ["SGP"]})
    assert out["guidance"]["transit_requirement"]["required"] is None
