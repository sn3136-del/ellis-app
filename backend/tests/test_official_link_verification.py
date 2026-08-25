"""A portal on a non-government domain proves it is official ONE way: the
destination's own government page links to it. These tests pin that the
verifier refuses everything short of that, and that the release gate's
long-promised 'official-link evidence' path actually works."""
import pytest

from app.global_routes import verify_live
from app.global_routes.models import PortalFamily
from app.visa_snapshot.authority import is_government_host


def _family(db, fid="qatar-hayya-test", hosts=("hayya.qa",)):
    fam = PortalFamily(family_id=fid, name="Qatar Hayya (test)",
                       kind="evisa_portal", operator="State of Qatar",
                       operator_kind="government", base_url=f"https://{hosts[0]}/",
                       hostnames=list(hosts), destinations=["QAT"],
                       verification_status="seed_unverified")
    db.add(fam)
    db.commit()
    return fam


def test_new_government_suffixes_are_factual():
    """Each added suffix is the state's own domain, verified live (2026-08-02
    batch, plus the 2026-08-22 batch: Mongolia's evisa.mn — loaded in a real
    browser, footer links to immigration.gov.mn and mfa.gov.mn — Lithuania's
    MIGRIS, the Russian MFA consular department, and France's state portal)."""
    for host in ("evisa.gouv.dj", "voyage.gouv.tg", "evisa.mfa.am",
                 "evisatraveller.mfa.ir", "www.evisa.e-gov.kg",
                 "evisa.mn", "www.migracija.lt", "evisa.kdmid.ru",
                 "www.service-public.fr"):
        assert is_government_host(host), host
    # And the additions did not accidentally bless whole ccTLDs.
    for host in ("evil.dj", "hayya.qa", "visa.visitsaudi.com", "evil.mn",
                 "korea-evisa.com", "www.vfsglobal.com"):
        assert not is_government_host(host), host


def test_refuses_a_non_government_linking_page(db):
    """A blog, a contractor's own site, or the portal itself can never vouch."""
    _family(db, fid="olv-nongov")
    out = verify_live.verify_family_official_link(
        db, "olv-nongov", "https://www.vfsglobal.com/partners",
        fetch=lambda url: '<a href="https://hayya.qa/">apply</a>')
    assert out["ok"] is False
    assert "not a government host" in out["reason"]


def test_refuses_when_the_page_does_not_link_the_portal(db):
    _family(db, fid="olv-nolink")
    out = verify_live.verify_family_official_link(
        db, "olv-nolink", "https://www.mofa.gov.qa/visas",
        fetch=lambda url: '<a href="https://somewhere-else.example/">x</a>')
    assert out["ok"] is False
    assert "does not link" in out["reason"]
    fam = db.query(PortalFamily).filter_by(family_id="olv-nolink").one()
    assert fam.verification_status == "seed_unverified"   # nothing upgraded on hope


def test_government_link_verifies_and_records_evidence(db):
    _family(db, fid="olv-good")
    out = verify_live.verify_family_official_link(
        db, "olv-good", "https://www.mofa.gov.qa/en/services",
        fetch=lambda url: '<p><a href="https://hayya.qa/en/apply">Hayya</a></p>')
    assert out["ok"] is True
    fam = db.query(PortalFamily).filter_by(family_id="olv-good").one()
    assert fam.verification_status == "verified_via_official_link"
    ev = fam.verification_evidence["official_link"]
    assert ev["page_host"] == "www.mofa.gov.qa"
    assert ev["matched_host"] == "hayya.qa"
    assert ev["retrieved_at"]


def test_identity_gate_accepts_official_link_evidence_only_when_real(db):
    """The identity gate passes a non-government host ONLY with matching
    recorded evidence — and still fails when the build's hostname differs
    from the one the government page vouched for."""
    from app.global_routes.release_gates import official_identity_ok
    fam = _family(db, fid="olv-gate")
    fam_ok, hosts_ok = official_identity_ok(fam, ["hayya.qa"])
    assert not fam_ok and not hosts_ok            # nothing verified yet

    verify_live.verify_family_official_link(
        db, "olv-gate", "https://www.mofa.gov.qa/en",
        fetch=lambda url: '<a href="https://hayya.qa/">Hayya</a>')
    db.refresh(fam)
    fam_ok, hosts_ok = official_identity_ok(fam, ["hayya.qa"])
    assert fam_ok and hosts_ok

    # Different hostname in the build than the evidence vouches for: refused.
    fam_ok, hosts_ok = official_identity_ok(fam, ["evil-hayya.example"])
    assert fam_ok and not hosts_ok

    # And evidence whose LINKING PAGE is not a government host never counts,
    # even if someone hand-edited it into the record.
    fam.verification_evidence = dict(fam.verification_evidence,
                                     official_link={"page_host": "blog.example",
                                                    "matched_host": "hayya.qa"})
    db.commit()
    fam_ok, hosts_ok = official_identity_ok(fam, ["hayya.qa"])
    assert not hosts_ok
