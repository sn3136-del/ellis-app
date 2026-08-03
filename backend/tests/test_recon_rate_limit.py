"""A recon pass costs the PORTAL something, and the bill lands on applicants.

Six rebuild passes against tdac.immigration.go.th in one afternoon
(2026-08-03) put Thailand behind a Cloudflare challenge that then met real
applicants mid-run. Build iteration must not be able to spend a government
portal's tolerance on whoever runs next.
"""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.adapter_factory import models as fm
from app.adapter_factory.recon import (
    ReconRefused, _recent_recon_host, run_recon)

# The suite runs with the cooldown OFF (fixtures are not governments), so each
# test here turns it on for itself — the same knob production ships with.
QUIET_MINUTES = 15


@pytest.fixture(autouse=True)
def cooldown_on(monkeypatch):
    from app import config
    monkeypatch.setenv("ELLIS_RECON_HOST_QUIET_SECONDS", str(QUIET_MINUTES * 60))
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


def test_production_ships_with_the_cooldown_on():
    """The default must protect real portals; only a fixture turns it off."""
    import os

    from app import config
    os.environ.pop("ELLIS_RECON_HOST_QUIET_SECONDS", None)
    config.settings.cache_clear()
    try:
        assert config.settings().recon_host_quiet_seconds >= 10 * 60
    finally:
        config.settings.cache_clear()

HOST = "tdac.immigration.go.th"


def _artifact(db, host, *, minutes_ago):
    job = fm.AdapterReconJob(build_request_id="br-1", org_id="org-1",
                             portal_hostnames=[host], status="complete")
    db.add(job)
    db.flush()
    art = fm.AdapterReconArtifact(
        recon_job_id=job.id, page_key="home", hostname=host,
        url_pattern=f"https://{host}/", structure={"elements": []},
        content_class="public_page")
    db.add(art)
    db.commit()
    # Straight UPDATE: the ORM's own timestamp default overwrites an attribute
    # set on the instance, so the row would keep saying "just now".
    db.execute(sa.update(fm.AdapterReconArtifact)
               .where(fm.AdapterReconArtifact.id == art.id)
               .values(created_at=datetime.now(timezone.utc)
                       - timedelta(minutes=minutes_ago)))
    db.commit()
    return art


def _request(db):
    req = fm.AdapterBuildRequest(
        org_id="org-1", route_key="rk1|dest=THA", destination="THA",
        visa_type="arrival_card", portal_evidence={"hostnames": [HOST]})
    db.add(req)
    db.commit()
    return req


# The db fixture is shared across these tests, so each one owns its own host
# — which is also the behaviour under test: cooldowns are per portal.
def test_a_host_observed_moments_ago_is_inside_the_quiet_period(db):
    host = "recent.gov.example"
    _artifact(db, host, minutes_ago=2)
    assert _recent_recon_host(db, host) is not None


def test_a_host_observed_long_enough_ago_is_free_to_probe(db):
    host = "cooled.gov.example"
    _artifact(db, host, minutes_ago=QUIET_MINUTES + 1)
    assert _recent_recon_host(db, host) is None


def test_an_unobserved_host_is_never_rate_limited(db):
    assert _recent_recon_host(db, "evisa.gov.vn") is None


def test_the_quiet_period_is_per_host(db):
    """One portal's cooldown must never stall a build for a different
    government's site."""
    _artifact(db, "busy.gov.example", minutes_ago=1)
    assert _recent_recon_host(db, "quiet.gov.example") is None


def test_a_second_pass_is_refused_loudly_not_served_stale(db):
    """Refusing is the point: silently reusing yesterday's structure would
    ship an adapter built against a page nobody looked at."""
    _artifact(db, HOST, minutes_ago=1)
    probed = []

    def observer(url):
        probed.append(url)
        return {"status": 200, "elements": []}

    with pytest.raises(ReconRefused) as exc:
        run_recon(db, build_request=_request(db), observer=observer)
    assert HOST in str(exc.value)
    assert probed == [], "the portal must not be touched at all"


def test_an_operator_can_still_force_a_fresh_read(db):
    """A live outage needs current structure. The cost is explicit, not
    accidental."""
    _artifact(db, HOST, minutes_ago=1)
    probed = []

    def observer(url):
        probed.append(url)
        return {"status": 200, "url": url, "hostname": HOST, "elements": []}

    job = run_recon(db, build_request=_request(db), observer=observer, force=True)
    assert job is not None and probed
