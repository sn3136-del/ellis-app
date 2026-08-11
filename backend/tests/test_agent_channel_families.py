"""Sanctioned representative/agent channels as portal families.

Three real channels where somebody other than the traveller may lawfully file:
IRCC's representative declaration on the Canadian eTA form, Sri Lanka's
'Third Party' ETA application type, and VFS Global's registered
travel-partner appointment portal. They enter the registry exactly like every
other family — curated seeds upgraded ONLY by the deterministic
government-suffix path (families.sync_families -> authority.is_government_host)
— and these tests pin the four things that make them safe:

  * the two state channels verify by domain; the VFS contractor does NOT, and
    no allowlist edit is allowed to make it green;
  * account_required survives the sync (VFS's registered-agent login is a real
    credential wall; the two state forms are public and must not pretend
    otherwise);
  * supported_outcomes is EMPTY on all three, so pick_family/
    assign_families_to_pairs can never bind a tourist pair to an agent channel
    — a self-filing traveller keeps resolving to canada-ircc / srilanka-eta;
  * no entry_gate was invented ahead of a live probe, and lookalike hostnames
    never pass — a near-miss domain stays unverifiable, not almost-verified.

Docs: docs/SANCTIONED_AGENT_CHANNELS.md.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from sqlalchemy import select

from app.global_routes import ROUTE_OUTCOMES, families
from app.global_routes.models import PortalFamily
from app.visa_snapshot.authority import classify_evidence, is_government_host
from app.visa_snapshot.registry import load_registry

CANADA = "canada-eta-representative"
SRILANKA = "srilanka-eta-thirdparty"
VFS = "vfs-registered-agent-appointments"
AGENT_CHANNELS = (CANADA, SRILANKA, VFS)

# The state-operated channels: their host really is the government's own.
GOV_CHANNELS = (CANADA, SRILANKA)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "SANCTIONED_AGENT_CHANNELS.md"


@pytest.fixture(scope="module")
def seed() -> dict[str, dict]:
    """The curated seed entries, by family_id (load_seed also validates them)."""
    return {e["family_id"]: e for e in families.load_seed()}


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


# ---- registry shape --------------------------------------------------------

def test_registry_seeds_the_three_channels_and_counts_them():
    raw = json.loads((REPO_ROOT / "data" / "reference" /
                      "portal_families.json").read_text(encoding="utf-8"))
    assert raw["entry_count"] == len(raw["entries"])
    ids = [e["family_id"] for e in raw["entries"]]
    assert len(ids) == len(set(ids))
    for fid in AGENT_CHANNELS:
        assert fid in ids, fid


def test_seed_entries_use_exactly_the_established_key_shape(seed):
    """Same keys, same order as the H1B seeds (which are the closest kin: a
    curated family with notes and no gate). A stray key is silently ignored by
    sync_families, so it would be dead data pretending to be policy."""
    reference = list(seed["usa-dol-flag"])
    for fid in AGENT_CHANNELS:
        assert list(seed[fid]) == reference, fid


# ---- identity: who is actually operating the host --------------------------

def test_state_channel_hosts_are_on_the_government_allowlist(seed):
    """Canada rides the eTA form's own host (cic.gc.ca / canada.ca); Sri Lanka
    rides the state ETA host under gov.lk."""
    for fid in GOV_CHANNELS:
        hosts = seed[fid]["hostnames"]
        assert hosts
        for host in hosts:
            assert is_government_host(host), (fid, host)


def test_vfs_is_a_contractor_and_never_passes_the_government_check(seed):
    """vfsglobal.com is a commercial operator. The registered-agent channel is
    sanctioned by VFS, not by a government, and the domain check must keep
    saying so — a contractor is only ever upgraded by observed official-link
    evidence (mark_official_link_verified)."""
    entry = seed[VFS]
    assert entry["operator_kind"] == "contracted_visa_center"
    for host in entry["hostnames"]:
        assert not is_government_host(host), host
        assert classify_evidence({"final_hostname": host}) == "unverified"
    # ...and the ONLY sanctioned upgrade: a government page linking to it.
    assert classify_evidence({
        "final_hostname": "row1.vfsglobal.com",
        "official_linking_source": "https://www.canada.ca/en/some-official-page.html",
    }) == "verified"


def test_lookalike_hostnames_are_rejected():
    """Suffix matching is proper (host == suffix or endswith '.' + suffix): a
    lookalike that merely CONTAINS the official domain never passes."""
    for host in ("eta.gov.lk.example", "eta-gov.lk.example", "etagov.lk",
                 "eta.gov.lk.phish.example", "www.eta.gov.lk.evil.example",
                 "eta.onlineservices-servicesenligne.apps.cic.gc.ca.evil.example",
                 "cic.gc.ca.example", "cic-gc.ca", "canada.ca.example",
                 "wwwcanada-ca.example"):
        assert not is_government_host(host), host
        assert classify_evidence({"final_hostname": host}) == "unverified"
    # the real ones still pass, so the assertions above are not vacuous
    for host in ("eta.gov.lk", "www.eta.gov.lk", "www.canada.ca",
                 "eta.onlineservices-servicesenligne.apps.cic.gc.ca"):
        assert is_government_host(host), host


def test_only_the_checked_vfs_hosts_are_claimed_anywhere_in_the_registry(seed):
    """Two VFS hosts were checked (row1, row4) and exactly those two are
    seeded. Neighbouring row* hosts are plausible but UNVERIFIED, and a
    plausible host is not a claimed one; near-miss spellings must appear
    nowhere in the registry at all."""
    assert seed[VFS]["hostnames"] == ["row1.vfsglobal.com", "row4.vfsglobal.com"]
    every_host = {h for e in seed.values() for h in (e.get("hostnames") or [])}
    for host in ("row2.vfsglobal.com", "row3.vfsglobal.com",
                 "row1-vfsglobal.com", "vfsglobal-row4.com",
                 "row1.vfsglobal.com.appointment.example",
                 "srilankaevisa.lk", "www.srilankaevisa.lk"):
        assert host not in every_host, host
    # the public VFS site stays a SEPARATE family; the agent channel does not
    # inherit its identity (or its destination list) by sharing hostnames.
    assert "visa.vfsglobal.com" not in seed[VFS]["hostnames"]
    assert set(seed[VFS]["hostnames"]).isdisjoint(seed["vfs-global"]["hostnames"])


# ---- what the deterministic sync concludes ---------------------------------

def test_sync_verifies_the_two_state_channels_by_domain(synced):
    for fid in GOV_CHANNELS:
        fam = _family(synced, fid)
        assert fam.verification_status == "verified_official_domain", fid
        assert "government allowlist" in (
            fam.verification_evidence or {}).get("domain_check", ""), fid
        assert fam.operator_kind == "government", fid
    assert _family(synced, CANADA).destinations == ["CAN"]
    assert _family(synced, SRILANKA).destinations == ["LKA"]


def test_sync_leaves_the_vfs_agent_channel_unverified(synced):
    """Fail closed: the contractor stays seed_unverified with evidence that
    names the missing proof, so the dashboard shows an exact gap instead of a
    green tick."""
    fam = _family(synced, VFS)
    assert fam.verification_status == "seed_unverified"
    assert "NOT all on government allowlist" in (
        fam.verification_evidence or {}).get("domain_check", "")
    assert fam.operator == "VFS Global"
    assert fam.operator_kind == "contracted_visa_center"


def test_account_required_survives_the_sync(synced):
    """account_required carries the credential wall that released_flow reads
    (register/login return an APPLICANT_ACTION_REQUIRED handoff when true).
    Losing VFS's true would claim the agent login is a no-op; inventing a true
    on the two public state forms would demand credentials that do not exist."""
    assert _family(synced, VFS).account_required is True
    for fid in GOV_CHANNELS:
        assert _family(synced, fid).account_required is False, fid


def test_no_entry_gate_invented_before_a_live_probe(synced):
    """None of the three has been probed: Canada's curated gate deliberately
    answers the representative question 'No', Sri Lanka's Third Party branch
    has never been opened, and everything past the VFS login wall is
    unobservable without the account. Absent is the honest form."""
    for fid in AGENT_CHANNELS:
        assert _family(synced, fid).entry_gate == {}, fid


# ---- the binding guard: an agent channel is never a tourist route ----------

def test_supported_outcomes_are_empty_on_every_agent_channel(synced):
    for fid in AGENT_CHANNELS:
        assert _family(synced, fid).supported_outcomes == [], fid


def test_empty_outcomes_stop_pick_family_binding_a_tourist_pair(synced):
    """assign_families_to_pairs binds pairs through pick_family, which skips
    any family whose supported_outcomes lacks the pair's outcome. So no
    (destination, outcome, nationality) tuple can ever select an agent
    channel."""
    for dest in ("CAN", "LKA", "USA", "IND", "GBR"):
        for outcome in ROUTE_OUTCOMES:
            for nat in ("", "CHN", "USA"):
                fam = families.pick_family(synced, dest, outcome, nat)
                assert fam is None or fam.family_id not in AGENT_CHANNELS, (
                    dest, outcome, nat, fam.family_id)


def test_self_filing_travellers_still_resolve_to_the_ordinary_family(synced):
    """The guard must not have been bought by breaking the normal route: the
    self-filing eTA families are still the ones picked."""
    assert families.pick_family(
        synced, "CAN", "ELECTRONIC_AUTHORIZATION").family_id == "canada-ircc"
    assert families.pick_family(
        synced, "LKA", "ELECTRONIC_AUTHORIZATION").family_id == "srilanka-eta"


def test_vfs_agent_channel_serves_no_destination_until_the_account_exists(synced):
    """Which client governments an agent may book for is a property of the
    registered-agent ACCOUNT, not of the portal. destinations stays empty, so
    release_gates' destination check (build.destination in family.destinations)
    can never pass and the family is structurally unreleasable — the business
    prerequisite is enforced, not merely documented."""
    fam = _family(synced, VFS)
    assert fam.destinations == []
    every_dest = [c["alpha_3"] for c in load_registry("countries")["entries"]]
    assert every_dest
    assert all(d not in (fam.destinations or []) for d in every_dest)


def test_agent_channels_never_queue_an_adapter_build(synced):
    """families_needing_adapters only takes families that GOVERN a pair. None
    of the three can be bound to one, so none of them ever enters the build
    queue behind the backs of the gates above."""
    from app.global_routes.orchestrator import families_needing_adapters
    todo = {f.family_id for f in families_needing_adapters(synced)}
    assert todo.isdisjoint(AGENT_CHANNELS)


# ---- honesty that lives in the row, not only in the doc -------------------

def test_notes_carry_the_business_prerequisite_and_reverification_triggers(synced):
    """These sentences ARE the policy. Stripping them turns a channel with a
    legal precondition into an ordinary portal, so pin them in the DB row that
    the inventory export and the dashboard actually read."""
    canada = _family(synced, CANADA).notes
    assert "IRPA s.91" in canada
    assert "may NOT charge a fee" in canada          # eTA-specific fee rule
    assert "personal acts" in canada

    lanka = _family(synced, SRILANKA).notes
    assert "RE-VERIFY BEFORE ANY RELEASE" in lanka
    assert "SUSPENDED" in lanka                      # before-arrival mandate
    assert "srilankaevisa.lk" in lanka               # the reverted operation
    assert "eta.gov.lk" in lanka

    vfs = _family(synced, VFS).notes
    assert "100 inactive days" in vfs                # registration expiry
    assert "no registered-agent account is held" in vfs
    assert "never quoted as the government" in vfs   # a contractor, not an authority


def test_the_doc_records_every_seeded_channel():
    """A channel that exists in the registry but not in the doc has no written
    record of what it sanctions, what stays a personal act, or what would
    invalidate it."""
    text = DOC.read_text(encoding="utf-8")
    for fid in AGENT_CHANNELS:
        assert fid in text, fid
    # the finding that makes portal-fill the sanctioned path at all
    for country in ("India", "Vietnam", "Turkey", "Australia", "New Zealand",
                    "Kenya", "Cambodia", "Egypt"):
        assert country in text, country
    assert "no government offers a public API" in text
    assert "never present ellis as an intermediary" in text.lower()
