"""Which consulate serves an applicant is researched, never remembered.

A wrong consulate sends someone to another city for an appointment that took
months to get, and a wrong booking link is indistinguishable from the visa-scam
sites that take people's money. These pin the rules that keep the jurisdiction
table trustworthy: only official sources, only pages that are actually about
this route, and a booking host a government either owns or points at.
"""
from __future__ import annotations

import pytest

from app import consular_research as cr


GOV_PAGE = "https://www.gov.uk/standard-visitor"
GOV_TEXT = ("Apply for a Standard Visitor visa to the United Kingdom. "
            "Applicants in China attend a visa application centre.")
CAND = {"competent_post_name": "UK Visa Application Centre Beijing",
        "competent_post_kind": "visa_centre",
        "competent_post_url": "https://visa.vfsglobal.com/chn/en/gbr"}


def _verify(**over):
    kw = dict(candidate=CAND, source_url=GOV_PAGE, source_text=GOV_TEXT,
              destination="United Kingdom", residence="China",
              government_linked=True)
    kw.update(over)
    return cr.verify_candidate(kw.pop("candidate"), **kw)


def test_official_source_naming_both_countries_verifies():
    row = _verify()
    assert row["verification_status"] == "verified", row["problems"]
    assert row["competent_post_url"] == CAND["competent_post_url"]


def test_a_page_that_never_mentions_the_route_is_refused():
    """A 404 or a mis-guessed URL must never become an answer. The publishing
    government's domain settles the DESTINATION, so the refusal here rests on
    the page saying nothing about the applicant's residence — which is exactly
    what an error page says about anywhere."""
    row = _verify(source_text="Page not found. Try searching GOV.UK.")
    assert row["verification_status"] == "unverified"
    assert row["competent_post_url"] == ""
    assert any("residence" in p for p in row["problems"]), row["problems"]


def test_an_unofficial_source_is_refused():
    row = _verify(source_url="https://cheap-visas-fast.example/uk-china")
    assert row["verification_status"] == "unverified"
    assert any("not official" in p for p in row["problems"])


def test_a_contractor_host_no_government_page_links_to_is_refused():
    """Being on a contractor-shaped domain is not enough: any look-alike would
    pass. An official page for THIS route must link to it."""
    row = _verify(government_linked=False)
    assert row["verification_status"] == "unverified"
    assert any("links to" in p for p in row["problems"])


def test_a_government_booking_host_needs_no_contractor_link():
    row = _verify(candidate={**CAND,
                             "competent_post_url": "https://www.visaforkorea-sh.com/"},
                  government_linked=False)
    # a delegated operator still needs the link…
    assert row["verification_status"] == "unverified"
    row = _verify(candidate={**CAND, "competent_post_url": "https://uk.embassy.gov.uk/book"},
                  government_linked=False)
    assert row["verification_status"] == "verified", row["problems"]


def test_non_https_booking_is_refused():
    row = _verify(candidate={**CAND, "competent_post_url": "http://visa.vfsglobal.com/x"})
    assert row["verification_status"] == "unverified"


def test_a_refused_row_never_keeps_a_booking_url():
    """An unverified rule is stored as a known gap — but it must not carry a
    link, or the resolver's honesty depends on a downstream check."""
    row = _verify(source_url="https://cheap-visas-fast.example/uk-china")
    assert row["competent_post_url"] == ""


def test_unnamed_post_is_refused():
    row = _verify(candidate={**CAND, "competent_post_name": "  "})
    assert row["verification_status"] == "unverified"


# --- the work list is measured, not guessed --------------------------------

def test_coverage_reports_the_honest_gap(db):
    c = cr.coverage(db)
    for key in ("rules_total", "verified", "with_booking_url",
                "pairs_still_unrouted", "destinations_covered"):
        assert key in c
        assert isinstance(c[key], int)
    assert c["verified"] <= c["rules_total"]
    assert c["with_booking_url"] <= c["verified"]


def test_store_persists_and_updates_in_place(db):
    row = _verify()
    row["destination_country"], row["residence_jurisdiction"] = "GBR", "CHN"
    stored = cr.store(db, row)
    assert stored.verification_status == "verified"
    again = cr.store(db, dict(row, competent_post_kind="consulate"))
    assert again.id == stored.id, "same route+post updates rather than duplicating"
    assert again.competent_post_kind == "consulate"


# --- regressions from the first live run -----------------------------------

def test_iso3_returns_alpha3_not_the_index_key():
    """The registry indexes some countries by alpha-2, so trusting the key
    returned 'GB' where 'GBR' was meant and every jurisdiction check failed."""
    assert cr._iso3("United Kingdom") == "GBR"
    assert cr._iso3("Vietnam") == "VNM"
    assert cr._iso3("GBR") == "GBR"


def test_the_destination_government_host_proves_the_destination():
    """A gov.uk page says 'UK visa', never 'United Kingdom' — demanding the
    literal name rejected a correct official answer in the first live run.
    The publishing government's own domain settles it."""
    row = _verify(source_text="Go to a visa application centre in China for "
                              "your UK visa.",
                  source_url="https://www.gov.uk/find-a-visa-application-centre")
    assert row["verification_status"] == "verified", row["problems"]


def test_the_host_never_proves_the_RESIDENCE():
    """Who published the page cannot say who it is for: a gov.uk page about
    Morocco must not answer for an applicant in China."""
    row = _verify(source_text="Visa centres in Morocco.",
                  source_url="https://www.gov.uk/find-a-visa-application-centre")
    assert row["verification_status"] == "unverified"
    assert any("residence" in p for p in row["problems"])


def test_search_failure_is_reported_not_raised():
    """A rate-limited or unreachable search must never crash intake."""
    out = cr.find_post_for_applicant(destination="United Kingdom", residence="China",
                                     timeout_s=0.001)
    assert out["found"] is False
    assert out.get("reason")
