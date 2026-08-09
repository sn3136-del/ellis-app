"""H1B portal families — seed identity and verification honesty.

The two government portals of the H1B pipeline (DOL FLAG for the ETA-9035
LCA, myUSCIS for the H-1B registration and I-129) enter the registry the same
way every family does: as curated seeds upgraded ONLY by the deterministic
government-suffix path (families.sync_families -> authority.is_government_host).
These tests pin that both verify as verified_official_domain, that the login
wall (account_required) survives the sync, that no entry_gate was invented
ahead of live gate_probe evidence, and that lookalike hostnames never pass —
a near-miss domain must stay unverifiable, not almost-verified.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.global_routes import families
from app.global_routes.models import PortalFamily
from app.visa_snapshot.authority import classify_evidence, is_government_host

H1B_FAMILY_IDS = ("usa-dol-flag", "usa-uscis-myaccount")


def test_h1b_hosts_are_on_the_government_allowlist():
    """flag.dol.gov via the dol.gov addition; my.uscis.gov via the uscis.gov
    entry that already passed (and both under the generic 'gov' suffix)."""
    for host in ("flag.dol.gov", "dol.gov", "my.uscis.gov", "uscis.gov"):
        assert is_government_host(host), host


def test_lookalike_hostnames_are_rejected():
    """Suffix matching is proper (host == suffix or endswith '.' + suffix):
    a lookalike that merely CONTAINS the official domain never passes."""
    for host in ("flag-dol.gov.example", "flag.dol.gov.example",
                 "flag.dol.gov.evil.example", "my.uscis.gov.phish.example",
                 "myuscis-gov.example", "dolgov.example"):
        assert not is_government_host(host), host
    # And the evidence classifier follows the same line.
    assert classify_evidence({"final_hostname": "flag-dol.gov.example"}) == "unverified"
    assert classify_evidence({"final_hostname": "flag.dol.gov"}) == "verified"
    assert classify_evidence({"final_hostname": "my.uscis.gov"}) == "verified"


@pytest.fixture()
def synced(db):
    """sync_families is a deterministic upsert: fresh DB or re-run, the
    verification outcome is the same."""
    families.sync_families(db)
    return db


def _family(db, family_id: str) -> PortalFamily:
    fam = db.execute(select(PortalFamily).where(
        PortalFamily.family_id == family_id)).scalars().first()
    assert fam is not None, f"seed family {family_id} did not load"
    return fam


def test_sync_verifies_both_h1b_families_as_official_domain(synced):
    for fid in H1B_FAMILY_IDS:
        fam = _family(synced, fid)
        assert fam.verification_status == "verified_official_domain", fid
        assert "government allowlist" in (
            fam.verification_evidence or {}).get("domain_check", ""), fid
        assert fam.operator_kind == "government", fid
        assert fam.destinations == ["USA"], fid


def test_login_wall_survives_the_sync(synced):
    """account_required carries the credential wall (released_flow reads it);
    losing it in the sync would silently drop the personal login handoff."""
    for fid in H1B_FAMILY_IDS:
        assert _family(synced, fid).account_required is True, fid


def test_family_identity_matches_the_curated_seed(synced):
    flag = _family(synced, "usa-dol-flag")
    assert flag.hostnames == ["flag.dol.gov"]
    assert flag.kind == "immigration_authority"
    assert flag.operator == ("U.S. Department of Labor, "
                             "Office of Foreign Labor Certification")
    uscis = _family(synced, "usa-uscis-myaccount")
    assert uscis.hostnames == ["my.uscis.gov"]
    assert uscis.kind == "immigration_authority"
    assert uscis.operator == "U.S. Citizenship and Immigration Services"


def test_no_entry_gate_invented_before_a_live_probe(synced):
    """Both portals are login-walled; their entry gates (and the otp
    declared_handoff that rides inside a gate) can only be authored from live
    gate_probe evidence in a later attended step. Until then the honest form
    is an absent gate — and no tourist pair outcome, so pick_family can never
    bind these families (H1B resolution is step-keyed)."""
    for fid in H1B_FAMILY_IDS:
        fam = _family(synced, fid)
        assert fam.entry_gate == {}, fid
        assert fam.supported_outcomes == [], fid
