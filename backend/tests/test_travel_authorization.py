"""Tourist portal families + the unified travel-authorization module.

Two things are pinned here, and they meet in the middle.

FIRST, three e-visa families that a traveller is actually sent to — Vietnam,
Cambodia, Egypt — enter the registry the way every family does: curated seeds
upgraded ONLY by the deterministic government-suffix path (families.sync_families
-> authority.is_government_host). The interesting one is Vietnam. Its portal
moved to evisa.gov.vn / thithucdientu.gov.vn and the old host is dead, so these
tests pin that the retired hostname is gone from the WHOLE registry — a dead
host on an allowlist is not a harmless leftover, it is a live-navigation target
somebody else can re-register — and that no entry_gate was invented for the new
host, or for the two families nobody has probed at all.

SECOND, app/travel_authorization.py: one vocabulary over ESTA, Canada's eTA,
the UK ETA, Australia's ETA and the NZeTA. The invariants under test are the
honesty ones:

  * every curated fee, validity and processing figure carries sources and an
    as_of, because an undated fee next to a payment is a liability;
  * Australia's ETA reports fill_supported False and says why in the words that
    make it unautomatable — app-only, NFC chip read, live facial capture — and
    ships a checklist instead of a fill;
  * an unknown nationality, an unknown destination, an uncurated pair or a
    merely PROVISIONAL pair all return unknown with a reason and the
    government's own checker. Never a guess, and never a soft no.

No test here touches the network: every figure under test is curated data and
every route is served by the in-process TestClient.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from sqlalchemy import select

from app import travel_authorization as ta
from app.global_routes import families
from app.global_routes.models import PortalFamily, RoutePairPolicy, pair_key
from app.visa_snapshot.authority import classify_evidence, is_government_host

from .conftest import AUTH

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "data" / "reference" / "portal_families.json"

VIETNAM = "vietnam-evisa"
CAMBODIA = "cambodia-evisa"
EGYPT = "egypt-evisa"
TOURIST_EVISA_FAMILIES = (VIETNAM, CAMBODIA, EGYPT)

# The host the Vietnam Immigration Department retired on 2024-11-11.
RETIRED_VN_HOST = "evisa.xuatnhapcanh.gov.vn"


@pytest.fixture(scope="module")
def seed() -> dict[str, dict]:
    """The curated seed entries by family_id (load_seed also validates them —
    an entry_gate typo fails here rather than during a build)."""
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


# ============================================================================
# Part 1 — the tourist e-visa families
# ============================================================================

def test_registry_counts_itself_and_has_no_duplicate_ids():
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert raw["entry_count"] == len(raw["entries"])
    ids = [e["family_id"] for e in raw["entries"]]
    assert len(ids) == len(set(ids))
    for fid in TOURIST_EVISA_FAMILIES:
        assert fid in ids, fid


def test_vietnam_points_at_both_current_hosts(seed):
    """The Immigration Department's own notice: from 11/11/2024 the portal
    operates on thithucdientu.gov.vn AND evisa.gov.vn. One platform, two
    domains, so one family carrying both."""
    assert seed[VIETNAM]["hostnames"] == ["evisa.gov.vn", "thithucdientu.gov.vn"]
    assert seed[VIETNAM]["base_url"] == "https://evisa.gov.vn/"


def test_the_retired_vietnam_host_is_gone_from_the_entire_registry(seed):
    """Not merely absent from the Vietnam entry — absent from every navigable
    field of every family. A retired hostname left on any allowlist is a host
    an adapter may still navigate to, and a domain somebody else may one day
    own. It survives in exactly ONE place, `notes`, which is prose nothing
    navigates to and which is the record of what was removed and why."""
    every_host = {h for e in seed.values() for h in (e.get("hostnames") or [])}
    assert RETIRED_VN_HOST not in every_host
    for fid, entry in seed.items():
        navigable = {k: v for k, v in entry.items() if k != "notes"}
        assert RETIRED_VN_HOST not in json.dumps(navigable), fid
    assert RETIRED_VN_HOST in seed[VIETNAM]["notes"]


def test_every_seeded_tourist_evisa_host_is_a_government_host(seed):
    """gov.vn, gov.kh and gov.eg are the operators' own delegations, so the
    deterministic domain check — not a human's opinion — verifies them."""
    for fid in TOURIST_EVISA_FAMILIES:
        hosts = seed[fid]["hostnames"]
        assert hosts, fid
        for host in hosts:
            assert is_government_host(host), (fid, host)
            assert classify_evidence({"final_hostname": host}) == "verified"


def test_lookalike_hostnames_are_rejected():
    """Suffix matching is proper (host == suffix or endswith '.' + suffix): a
    lookalike that merely CONTAINS an official domain never passes. These are
    exactly the shapes the e-visa scam sites use."""
    for host in ("evisa.gov.vn.example", "evisa-gov.vn", "evisagov.vn",
                 "evisa.gov.vn.apply.example", "www.evisa.gov.vn.evil.example",
                 "thithucdientu.gov.vn.example", "thithucdientu-gov.vn",
                 "evisa.gov.kh.example", "evisa-gov.kh", "www.evisa.gov.kh.phish.example",
                 "visa2egypt.gov.eg.example", "visa2egypt-gov.eg", "visa2egypt.com"):
        assert not is_government_host(host), host
        assert classify_evidence({"final_hostname": host}) == "unverified"
    # not vacuous: the real hosts still pass
    for host in ("evisa.gov.vn", "thithucdientu.gov.vn", "www.evisa.gov.kh",
                 "visa2egypt.gov.eg", "www.visa2egypt.gov.eg"):
        assert is_government_host(host), host


def test_lookalike_hostnames_appear_nowhere_in_the_registry(seed):
    """A near-miss spelling must not be claimed by ANY family — including the
    plausible-but-unobserved bare apex of Cambodia's portal. A plausible host
    is not a claimed one."""
    every_host = {h for e in seed.values() for h in (e.get("hostnames") or [])}
    for host in ("evisa-gov.vn", "evisa.gov.vn.example", "thithucdientu.com",
                 "evisa.gov.kh", "evisa-gov.kh", "visa2egypt.com",
                 "egypt-evisa.gov.eg"):
        assert host not in every_host, host


def test_sync_verifies_all_three_by_domain(synced):
    for fid in TOURIST_EVISA_FAMILIES:
        fam = _family(synced, fid)
        assert fam.verification_status == "verified_official_domain", fid
        assert "government allowlist" in (
            fam.verification_evidence or {}).get("domain_check", ""), fid
        assert fam.operator_kind == "government", fid
    assert _family(synced, VIETNAM).destinations == ["VNM"]
    assert _family(synced, CAMBODIA).destinations == ["KHM"]
    assert _family(synced, EGYPT).destinations == ["EGY"]


def test_repaired_hostnames_survive_the_sync(synced):
    """The repair has to reach the DB row the runtime actually reads, not just
    the JSON file."""
    assert _family(synced, VIETNAM).hostnames == ["evisa.gov.vn",
                                                  "thithucdientu.gov.vn"]
    every_host = {h for f in synced.execute(select(PortalFamily)).scalars()
                  for h in (f.hostnames or [])}
    assert RETIRED_VN_HOST not in every_host


def test_account_walls_survive_the_sync(synced):
    """account_required carries the credential wall released_flow reads.
    Visa2Egypt really does make the traveller register first; Vietnam's and
    Cambodia's forms are public and must not pretend to demand credentials
    that do not exist."""
    assert _family(synced, EGYPT).account_required is True
    assert _family(synced, VIETNAM).account_required is False
    assert _family(synced, CAMBODIA).account_required is False


def test_no_entry_gate_invented_where_no_probe_has_run(synced):
    """Cambodia and Egypt have never been probed live, so the honest form is
    an absent gate — not a plausible one."""
    for fid in (CAMBODIA, EGYPT):
        assert _family(synced, fid).entry_gate == {}, fid


def test_vietnams_real_probe_evidence_survived_the_hostname_repair(synced):
    """The gate was authored from a live credential-free probe of
    evisa.gov.vn. Repairing the hostname list must not destroy real evidence —
    and the CAPTCHA the probe found must still be declared as a handoff, since
    losing it would silently promise Ellis solves one."""
    gate = _family(synced, VIETNAM).entry_gate
    assert gate.get("expect_path") == "/e-visa/foreigners"
    assert gate.get("declared_handoffs") == ["captcha"]
    assert gate.get("actions")


def test_notes_record_the_repair_and_the_unprobed_host(synced):
    """The sentence IS the provenance. Stripping it turns a documented domain
    migration into an unexplained hostname edit, and turns an unprobed host
    into one that looks probed."""
    notes = _family(synced, VIETNAM).notes
    assert RETIRED_VN_HOST in notes            # names what was removed
    assert "thithucdientu.gov.vn" in notes
    assert "NOT been probed" in notes
    assert "2026-07-25" in notes               # which host the gate came from

    cambodia = _family(synced, CAMBODIA).notes
    assert "no live probe has run" in cambodia
    assert "plausible host is not a claimed one" in cambodia

    egypt = _family(synced, EGYPT).notes
    assert "no live probe has run" in egypt
    assert "account_required is true and REAL" in egypt


def test_the_evisa_families_still_serve_their_destinations(synced):
    """The repair must not have been bought by breaking resolution: each
    family is still the one pick_family selects for its destination."""
    assert families.pick_family(synced, "VNM", "EVISA").family_id == VIETNAM
    assert families.pick_family(synced, "KHM", "EVISA").family_id == CAMBODIA
    assert families.pick_family(synced, "EGY", "EVISA").family_id == EGYPT


def test_cambodias_evisa_and_earrival_stay_separate_platforms(seed):
    """Two different ministries, two different platforms. Merging them would
    let an e-Arrival adapter be presented as an e-Visa."""
    assert set(seed[CAMBODIA]["hostnames"]).isdisjoint(
        seed["cambodia-earrival"]["hostnames"])
    assert seed[CAMBODIA]["kind"] == "evisa_portal"
    assert seed["cambodia-earrival"]["kind"] == "arrival_card"


# ============================================================================
# Part 2 — the travel-authorization module
# ============================================================================

def test_the_five_visa_waiver_authorizations_are_curated():
    assert set(ta.keys()) == {"usa-esta", "canada-eta", "uk-eta",
                              "australia-eta", "nz-nzeta"}


def test_every_authorization_has_an_official_url_on_a_government_host():
    """The link Ellis hands a traveller is the government's own, verified by
    the same deterministic check the portal registry uses — never a
    convenient aggregator."""
    from app.visa_snapshot.authority import hostname
    for key in ta.keys():
        rec = ta.get(key)
        for url in (rec["official_url"], rec["official_check_url"]):
            assert url.startswith("https://"), (key, url)
            assert is_government_host(hostname(url)), (key, url)


def test_every_fee_carries_a_source_and_an_as_of():
    """An undated fee shown next to a payment is the exact failure this rule
    exists to prevent."""
    for key in ta.keys():
        fee = ta.get(key)["fee"]
        assert fee["status"] == "curated", key
        assert isinstance(fee["amount"], (int, float)) and fee["amount"] > 0, key
        assert fee["currency"], key
        assert fee["source"].startswith("https://"), key
        assert fee["as_of"] == ta.AS_OF, key
        assert fee["evidence"] in ta.EVIDENCE_KINDS, key


def test_validity_and_processing_carry_sources_and_degrade_honestly():
    """Same rule for the other two figures a traveller plans around — and
    where Ellis has not verified a figure it says so with a reason instead of
    printing a plausible duration."""
    for key in ta.keys():
        rec = ta.get(key)
        for field in ("validity", "processing"):
            block = rec[field]
            assert block["status"] in ("curated", "unknown"), (key, field)
            assert block["source"].startswith("https://"), (key, field)
            assert block["as_of"] == ta.AS_OF, (key, field)
            if block["status"] == "curated":
                assert block["summary"], (key, field)
            else:
                assert block["summary"] is None, (key, field)
                assert block["reason"], (key, field)


def test_the_australia_eta_is_the_one_that_declines_to_guess_a_duration():
    """Not vacuous: exactly one curated figure in the set is an honest
    unknown, and it is the one nobody has verified."""
    unknowns = [(k, f) for k in ta.keys() for f in ("validity", "processing")
                if ta.get(k)[f]["status"] == "unknown"]
    assert unknowns == [("australia-eta", "processing")]


def test_additional_charges_are_sourced_too():
    """New Zealand's IVL is NZD 100 on top of the NZeTA and is not refunded
    when the request is declined — a charge a traveller must be told about
    before they pay, so it carries the same receipts as a fee."""
    ivl = ta.get("nz-nzeta")["additional_charges"]
    assert len(ivl) == 1
    charge = ivl[0]
    assert charge["amount"] == 100 and charge["currency"] == "NZD"
    assert charge["source"].startswith("https://www.immigration.govt.nz/")
    assert charge["as_of"] == ta.AS_OF
    assert "not" in charge["note"].lower() and "refund" in charge["note"].lower()
    # and the fee itself is honest that the app channel is cheaper
    fee = ta.get("nz-nzeta")["fee"]
    assert fee["channel_amounts"] == {"official_app": 17, "official_website": 23}
    assert fee["amount"] == 23  # the channel Ellis can actually fill


def test_esta_fee_is_the_current_inflation_adjusted_amount():
    """USD 40.27 from 2026-01-01, and the record says HR-1 moves it every
    fiscal year — so a stale amount is recognisable as stale."""
    fee = ta.get("usa-esta")["fee"]
    assert fee["amount"] == 40.27 and fee["currency"] == "USD"
    assert fee["effective"] == "2026-01-01"
    assert "inflation" in fee["note"]
    assert fee["evidence"] == "primary_fetch"


# ---- the app-only case ------------------------------------------------------

def test_australia_eta_reports_fill_unsupported_with_the_app_only_reason():
    """The flag that earns its keep. An NFC chip read and a LIVE facial
    capture on the traveller's own device cannot be done by a browser, and may
    not be done by anyone else — so no partial fill is claimed."""
    rec = ta.get("australia-eta")
    assert rec["fill_supported"] is False
    assert rec["app_only"] is True
    reason = rec["fill_unsupported_reason"]
    assert "app" in reason.lower()
    assert "NFC" in reason
    assert "facial capture" in reason.lower() or "facial" in reason.lower()
    assert "cannot apply for an ETA online" in reason      # Home Affairs' own words
    assert rec["fill_unsupported_reason"] and not rec.get("fill_note")


def test_australia_eta_hands_over_a_checklist_and_the_official_app():
    """Because it cannot fill, the checklist IS the deliverable — and it names
    the NFC and live-selfie steps, which is the warning that saves a traveller
    who has no NFC phone from booking a flight first."""
    rec = ta.detail("australia-eta")
    assert rec["how_to_apply"]["channel"] == "official_mobile_app"
    assert rec["app"]["name"] == "AustralianETA"
    assert "NFC" in rec["app"]["device_requirement"]
    steps = rec["traveler_checklist"]
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    blob = json.dumps(steps).lower()
    assert "nfc" in blob and "live facial image" in blob and "chip" in blob


def test_australia_eta_is_bound_to_no_portal_family():
    """No portal carries the subclass 601, so binding one would be a lie that
    a build could later act on. The ImmiAccount family says the same thing in
    its own notes, so the two cannot drift apart silently."""
    assert ta.get("australia-eta")["portal_family_id"] == ""
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    immi = next(e for e in raw["entries"] if e["family_id"] == "australia-immi")
    assert "ETA app" in immi["notes"]
    assert "travel_authorization.py" in immi["notes"]
    assert "eVisitor" in immi["notes"]


def test_every_other_authorization_is_fillable_and_says_how(seed):
    """Not vacuous: the other four ARE fillable, each names its channel, and
    each names a portal family that really exists in the registry."""
    for key in ("usa-esta", "canada-eta", "uk-eta", "nz-nzeta"):
        rec = ta.get(key)
        assert rec["fill_supported"] is True, key
        assert rec["fill_unsupported_reason"] == "", key
        assert rec["fill_note"], key
        assert rec["portal_family_id"] in seed, key
        assert ta.detail(key)["how_to_apply"]["channel"] == "official_web_form", key


def test_every_authorization_keeps_the_personal_acts_personal():
    """A record with no human act left would mean Ellis had done something it
    must not. Payment and submission are on every single one."""
    for key in ta.keys():
        acts = ta.get(key)["human_acts"]
        assert acts, key
        keys = {a["key"] for a in acts}
        assert "pay_fee" in keys, key
        assert "submit_application" in keys, key
        for act in acts:
            assert act["actor"], (key, act["key"])
            assert act["why_human"], (key, act["key"])
            assert act["ellis_prepared"], (key, act["key"])
    never = ta.detail("usa-esta")["ellis_never"]
    assert "enters card details" in never
    assert "presses submit" in never
    assert "solves or evades a CAPTCHA" in never
    assert "performs or supplies a biometric" in never


def test_the_canada_eta_carries_the_irpa_fee_rule():
    """A travel professional may not charge a fee specifically for submitting
    an eTA. That is a release blocker for a paid-agent framing, so it rides in
    the row the product reads, not only in a doc."""
    note = ta.get("canada-eta")["fee"]["note"]
    assert "IRPA s.91" in note
    assert "may NOT charge" in note


# ---- the requirement question ----------------------------------------------

def test_unknown_nationality_returns_unknown_never_a_guess():
    v = ta.is_required("usa-esta", "ZZZ", "USA")
    assert v["status"] == ta.UNKNOWN
    assert v["required"] is None
    assert v["basis"] == "unrecognized_nationality"
    assert v["official_check_url"].startswith("https://esta.cbp.dhs.gov")


def test_unknown_destination_returns_unknown_never_a_guess():
    v = ta.is_required("usa-esta", "FRA", "NOWHERE")
    assert v["status"] == ta.UNKNOWN
    assert v["required"] is None
    assert v["basis"] == "unrecognized_destination"


def test_an_uncurated_destination_is_a_gap_not_a_clearance():
    """The distinction the whole module exists for: 'Ellis holds nothing for
    Japan' must never read as 'Japan needs nothing'."""
    out = ta.applicable("FRA", "JPN")
    assert out["status"] == ta.UNKNOWN
    assert out["authorizations"] == []
    assert "NOT a statement that JPN requires no pre-travel authorization" in out["note"]


def test_an_authorization_for_another_country_is_not_applicable():
    """`required` stays None rather than False. A False here would let a UI
    render 'Not required' beside a Canada trip and read as clearance to fly;
    not_applicable forces the caller to read the status and say the true
    thing — an ESTA is not a Canadian document at all."""
    v = ta.is_required("usa-esta", "FRA", "CAN")
    assert v["status"] == ta.NOT_APPLICABLE
    assert v["basis"] == "destination_mismatch"
    assert v["required"] is None
    assert "says nothing about entry to CAN" in v["reason"]


def test_a_purpose_outside_the_scheme_is_not_applicable_and_gives_no_advice():
    """The ESTA does not cover employment. Saying so is a sourced fact; saying
    which visa the traveller needs instead would be legal advice, so the
    verdict explicitly declines to."""
    v = ta.is_required("usa-esta", "FRA", "USA", "work")
    assert v["status"] == ta.NOT_APPLICABLE
    assert v["basis"] == "purpose_out_of_scope"
    assert "not something this module decides" in v["reason"]


def test_an_unrecognized_purpose_word_is_unknown_not_assumed_tourism():
    v = ta.is_required("usa-esta", "FRA", "USA", "holidaying about")
    assert v["status"] == ta.UNKNOWN
    assert v["basis"] == "unrecognized_purpose"


def test_a_published_exemption_is_the_only_hard_coded_country_answer():
    """British and Irish citizens are named exempt by the Home Office in so
    many words, which is why they are the only nationalities this module
    answers from its own table. Everything else defers to the sourced
    route-policy layer."""
    for nat in ("GBR", "IRL"):
        v = ta.is_required("uk-eta", nat, "GBR")
        assert v["status"] == ta.NOT_REQUIRED, nat
        assert v["basis"] == "published_exemption", nat
    hard_coded = {n for k in ta.keys() for n in ta.get(k)["exempt_nationalities"]}
    assert hard_coded == {"GBR", "IRL"}


def test_without_a_route_policy_layer_the_answer_is_unknown():
    v = ta.is_required("uk-eta", "FRA", "GBR")
    assert v["status"] == ta.UNKNOWN
    assert v["basis"] == "no_route_policy_layer"
    assert "official checker" in v["reason"]


def test_every_verdict_stays_inside_the_declared_vocabulary(db, pair):
    """VERDICTS and BASES are the contract a UI renders against. A verdict
    reaching a caller under a status or basis that is not declared would be a
    string nobody has decided how to display — which is how an unknown ends up
    looking like a no."""
    pair("FRA", "USA", outcome="VISA_EXEMPT", verification="verified")
    pair("FRA", "NZL", outcome="ELECTRONIC_AUTHORIZATION", verification="verified")
    cases = [
        ("nz-nzeta", "FRA", "NZL", "tourism"),
        ("usa-esta", "ZZZ", "USA", "tourism"),
        ("usa-esta", "FRA", "NOWHERE", "tourism"),
        ("usa-esta", "FRA", "CAN", "tourism"),
        ("usa-esta", "FRA", "USA", "work"),
        ("usa-esta", "FRA", "USA", "wandering"),
        ("uk-eta", "GBR", "GBR", "tourism"),
        ("usa-esta", "FRA", "USA", "tourism"),
        ("nz-nzeta", "SSD", "NZL", "tourism"),
    ]
    seen_status, seen_basis = set(), set()
    for key, nat, dest, purpose in cases:
        for session in (None, db):
            v = ta.is_required(key, nat, dest, purpose, db=session)
            assert v["status"] in ta.VERDICTS, v
            assert v["basis"] in ta.BASES, v
            assert v["reason"], v
            assert v["as_of"] == ta.AS_OF
            assert v["official_check_url"].startswith("https://")
            seen_status.add(v["status"])
            seen_basis.add(v["basis"])
    # not vacuous: this walk really exercised all four verdicts
    assert seen_status == set(ta.VERDICTS)
    assert "no_route_policy_layer" in seen_basis
    assert "route_policy_verified" in seen_basis


def test_unknown_authorization_key_raises():
    with pytest.raises(ta.UnknownAuthorization):
        ta.get("usa-esta-plus")
    with pytest.raises(ta.UnknownAuthorization):
        ta.is_required("nope", "FRA", "USA")


def test_the_curated_table_cannot_be_mutated_through_a_reader():
    """get() hands out a copy. A caller that edits its payload must not be
    able to rewrite a government fee for every later caller."""
    rec = ta.get("uk-eta")
    rec["fee"]["amount"] = 1
    rec["human_acts"].clear()
    assert ta.get("uk-eta")["fee"]["amount"] == 20
    assert ta.get("uk-eta")["human_acts"]
    # ...and the same holds for the nested figures the summary hands out.
    nz = next(a for a in ta.summary() if a["key"] == "nz-nzeta")
    nz["fee"]["channel_amounts"]["official_app"] = 999
    nz["additional_charges"][0]["amount"] = 0
    fresh = next(a for a in ta.summary() if a["key"] == "nz-nzeta")
    assert fresh["fee"]["channel_amounts"]["official_app"] == 17
    assert fresh["additional_charges"][0]["amount"] == 100


# ---- the route-policy delegation -------------------------------------------

@pytest.fixture()
def pair(db):
    """Set one (nationality, destination) pair policy and put it back after.
    The db fixture shares one SQLite file across the session, so a test that
    leaves a row behind would answer a later test's question for it."""
    saved: list[tuple[RoutePairPolicy, dict]] = []

    def _set(nat: str, dest: str, *, outcome: str, verification: str,
             source: str = "official_research", urls: list | None = None):
        pk = pair_key(nat, "ordinary_passport", dest)
        row = db.execute(select(RoutePairPolicy).where(
            RoutePairPolicy.pair_key == pk)).scalars().first()
        if row is None:
            row = RoutePairPolicy(snapshot_date="2026-07-23", pair_key=pk,
                                  passport_nationality=nat,
                                  travel_document_type="ordinary_passport",
                                  destination_country=dest)
            db.add(row)
            db.flush()
            saved.append((row, None))
        else:
            saved.append((row, {"route_outcome": row.route_outcome,
                                "verification_status": row.verification_status,
                                "source": row.source,
                                "official_source_urls": list(
                                    row.official_source_urls or [])}))
        row.route_outcome = outcome
        row.verification_status = verification
        row.source = source
        row.official_source_urls = urls or []
        db.commit()
        return row

    yield _set

    for row, before in reversed(saved):
        if before is None:
            db.delete(row)
        else:
            for k, v in before.items():
                setattr(row, k, v)
    db.commit()


def test_a_verified_electronic_authorization_pair_answers_required(db, pair):
    pair("FRA", "GBR", outcome="ELECTRONIC_AUTHORIZATION", verification="verified",
         urls=["https://www.gov.uk/eta"])
    v = ta.is_required("uk-eta", "FRA", "GBR", db=db)
    assert v["status"] == ta.REQUIRED
    assert v["required"] is True
    assert v["basis"] == "route_policy_verified"
    assert v["route_policy"]["route_outcome"] == "ELECTRONIC_AUTHORIZATION"
    assert v["sources"] == ["https://www.gov.uk/eta"]


def test_a_verified_non_eta_pair_answers_not_required_without_advising(db, pair):
    pair("CHN", "GBR", outcome="EMBASSY_OR_CONSULATE_APPLICATION",
         verification="verified")
    v = ta.is_required("uk-eta", "CHN", "GBR", db=db)
    assert v["status"] == ta.NOT_REQUIRED
    assert v["required"] is False
    assert "not advice about what the traveller does need" in v["reason"]


def test_a_provisional_pair_never_produces_a_verdict(db, pair):
    """Provisional reference data has never counted as released coverage, and
    it does not get to tell a traveller they may board either. It is surfaced,
    labelled, beside an explicit unknown."""
    pair("BRA", "GBR", outcome="ELECTRONIC_AUTHORIZATION",
         verification="provisional", source="reference_dataset")
    v = ta.is_required("uk-eta", "BRA", "GBR", db=db)
    assert v["status"] == ta.UNKNOWN
    assert v["required"] is None
    assert v["basis"] == "route_policy_provisional"
    assert v["provisional_indication"]["route_outcome"] == "ELECTRONIC_AUTHORIZATION"
    assert v["provisional_indication"]["verification_status"] == "provisional"
    assert "official checker" in v["reason"]


def test_a_pair_with_no_row_at_all_is_unknown(db, pair):
    """Deliberately an unresolved pair: no row means Ellis does not know, and
    says which pair it does not know about."""
    v = ta.is_required("nz-nzeta", "SSD", "NZL", db=db)
    assert v["status"] == ta.UNKNOWN
    assert v["basis"] == "no_route_policy_row"
    assert "SSD" in v["reason"] and "NZL" in v["reason"]


def test_applicable_answers_each_authorization_on_its_own(db, pair):
    pair("FRA", "AUS", outcome="ELECTRONIC_AUTHORIZATION", verification="verified")
    out = ta.applicable("FRA", "AUS", db=db)
    assert out["status"] == "curated"
    assert [a["key"] for a in out["authorizations"]] == ["australia-eta"]
    verdict = out["authorizations"][0]
    assert verdict["status"] == ta.REQUIRED
    # ...and the required one is still the one Ellis cannot fill.
    assert ta.get(verdict["key"])["fill_supported"] is False


# ============================================================================
# Part 3 — the endpoints
# ============================================================================

def test_authorizations_endpoint_requires_a_principal(client):
    assert client.get("/travel/authorizations").status_code == 401


def test_catalogue_lists_every_authorization_with_its_fill_state(client):
    r = client.get("/travel/authorizations", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "catalogue"
    assert body["org_id"] == "org1"
    by_key = {a["key"]: a for a in body["authorizations"]}
    assert set(by_key) == set(ta.keys())
    assert by_key["australia-eta"]["fill_supported"] is False
    assert by_key["australia-eta"]["app_only"] is True
    assert by_key["usa-esta"]["fee"]["amount"] == 40.27
    for entry in by_key.values():
        assert entry["fee"]["source"].startswith("https://")
        assert entry["as_of"] == ta.AS_OF


def test_listing_for_a_traveller_returns_a_verdict_per_authorization(client):
    r = client.get("/travel/authorizations",
                   params={"nationality": "GBR", "destination": "GBR"},
                   headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["destination"] == "GBR"
    assert body["authorizations"][0]["status"] == ta.NOT_REQUIRED


def test_listing_endpoint_reaches_the_verified_route_policy_layer(client, db, pair):
    """The endpoint must wire a DB session (finding #4): a verified
    ELECTRONIC_AUTHORIZATION pair on file resolves to route_policy_verified /
    required through GET /travel/authorizations, never the db-less short-circuit
    'no_route_policy_layer'."""
    pair("FRA", "GBR", outcome="ELECTRONIC_AUTHORIZATION", verification="verified",
         urls=["https://www.gov.uk/eta"])
    r = client.get("/travel/authorizations",
                   params={"nationality": "FRA", "destination": "GBR"},
                   headers=AUTH)
    assert r.status_code == 200, r.text
    uk = next(a for a in r.json()["authorizations"] if a["key"] == "uk-eta")
    assert uk["basis"] == "route_policy_verified"
    assert uk["status"] == ta.REQUIRED and uk["required"] is True


def test_listing_for_an_uncurated_destination_says_so(client):
    r = client.get("/travel/authorizations",
                   params={"nationality": "FRA", "destination": "BRA"},
                   headers=AUTH)
    body = r.json()
    assert body["status"] == ta.UNKNOWN
    assert body["authorizations"] == []
    assert "gap in Ellis's data" in body["note"]


def test_detail_endpoint_serves_the_link_the_fee_and_the_human_acts(client):
    r = client.get("/travel/authorizations/nz-nzeta", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["official_url"].startswith("https://www.immigration.govt.nz/")
    assert body["fee"]["currency"] == "NZD"
    assert body["additional_charges"][0]["amount"] == 100
    assert {a["key"] for a in body["human_acts"]} >= {"pay_fee", "submit_application"}
    assert body["org_id"] == "org1"


def test_detail_endpoint_404s_on_an_unknown_key(client):
    r = client.get("/travel/authorizations/atlantis-eta", headers=AUTH)
    assert r.status_code == 404
    assert "unknown travel authorization" in r.json()["detail"]


def test_detail_of_the_app_only_authorization_leads_with_the_app(client):
    r = client.get("/travel/authorizations/australia-eta", headers=AUTH)
    body = r.json()
    assert body["how_to_apply"]["channel"] == "official_mobile_app"
    assert body["how_to_apply"]["fill_supported"] is False
    assert "cannot apply for an ETA online" in body["how_to_apply"]["reason"]
    assert body["traveler_checklist"]


def test_schengen_stay_delegates_and_never_claims_to_decide_admission(client):
    """The arithmetic belongs to app/schengen_stay.py; this router only carries
    it. Whichever way it resolves, the response must say out loud that a day
    count is not a permission — a border officer decides that, on the day."""
    r = client.get("/travel/schengen-stay",
                   params={"stays": json.dumps(
                       [{"entry": "2026-01-01", "exit": "2026-01-10"}]),
                           "as_of": "2026-08-11", "route": "FRA"},
                   headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["stays_considered"] == [{"entry": "2026-01-01", "exit": "2026-01-10"}]
    assert body["decides_admissibility"] is False
    assert "border officer" in body["note"]
    assert body["requested_as_of"] == "2026-08-11"
    if body["status"] == "computed":
        assert body["available"] is True
    else:
        assert body["status"] == "unavailable" and body["available"] is False


def test_schengen_stay_degrades_honestly_when_the_calculator_is_absent(
        client, monkeypatch):
    """The reason the import is lazy. With no usable entry point the endpoint
    still answers — saying precisely that the calculator is not there, and
    returning NO day count, not even a zero. A fabricated remaining-days
    figure is how a traveller overstays."""
    from app import travel_api
    monkeypatch.setattr(travel_api, "_SCHENGEN_ENTRYPOINTS",
                        ("no_such_entry_point",))
    r = client.get("/travel/schengen-stay",
                   params={"stays": json.dumps(
                       [{"entry": "2026-01-01", "exit": "2026-01-10"}])},
                   headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable" and body["available"] is False
    assert body["days_used"] is None and body["days_remaining"] is None
    assert "not implemented yet" in body["reason"]
    assert body["official_calculator"].startswith("https://home-affairs.ec.europa.eu/")
    # the honest shape still refuses to imply a decision
    assert body["decides_admissibility"] is False


def test_schengen_stay_rejects_a_malformed_trip_rather_than_dropping_it(client):
    """Dropping an unparseable trip would UNDERSTATE the days used, which is
    the dangerous direction to be wrong in."""
    for bad in ('{"entry": "2026-01-01"}',
                '[{"entry": "01/01/2026", "exit": "2026-01-10"}]',
                '[{"entry": "2026-01-10", "exit": "2026-01-01"}]',
                'not json'):
        r = client.get("/travel/schengen-stay", params={"stays": bad}, headers=AUTH)
        assert r.status_code == 400, bad


def test_schengen_stay_requires_a_principal(client):
    assert client.get("/travel/schengen-stay").status_code == 401
