"""guard-20260912 T6: registry enforcement at serve time and edit time."""
import json
from pathlib import Path

import pytest

from app.visa_snapshot import kimi_primary as kp, permission_eligibility as pe, scheme_registry as sr
from app.visa_snapshot import verified_overrides as vo

SEED = Path(__file__).resolve().parents[2] / "data" / "database_seed" / "scheme_lists.json"


def route(nat, dest, purpose="tourism", document="ordinary_passport"):
    return {"passport_nationality": nat, "destination_country": dest, "travel_purpose": purpose,
            "travel_document_type": document}


def keta_answer():
    return {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "requirement_detail": "eta_electronic_authorization",
            "visa_category": "K-ETA", "application_channel": "online_portal",
            "source_url": "https://www.k-eta.go.kr/portal/apply/index.do",
            "government_fee": {"amount": 10000, "currency": "KRW"},
            "visa_products": [{"type": "K-ETA", "fee": {"amount": 10000, "currency": "KRW"}}]}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "scheme_lists.json"
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(path))
    sr.reload()

    def write(entries):
        path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        sr.reload()
    write(json.loads(SEED.read_text(encoding="utf-8")))
    yield write
    sr.reload()


@pytest.fixture
def files(tmp_path, monkeypatch):
    seed, operator = tmp_path / "seed.json", tmp_path / "operator.json"
    seed.write_text("[]")
    monkeypatch.setattr(vo, "OVERRIDES", seed)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(operator))
    vo.reload()
    yield seed, operator
    vo.reload()


def _entry(sid):
    return next(e for e in json.loads(SEED.read_text(encoding="utf-8")) if e["id"] == sid)


def test_indonesia_korea_is_still_refused_by_whichever_path_is_live(registry):
    blanket = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free", "visa_products": []}
    # Registry path: the K-ETA list is established with Indonesia absent, so
    # the K-ETA product is refused by the list AND by the reviewed branch.
    assert pe._keta_established_without_indonesia()
    via_registry = pe.issues(keta_answer(), route("IDN", "KOR"))
    assert len(via_registry) == 2 and all(m.startswith("product eligibility:") for m in via_registry)
    assert any("IDN is absent" in m and "https://www.k-eta.go.kr/portal/guide/viewetaalification.do" in m
               for m in via_registry)
    assert any("Indonesia is not K-ETA eligible" in m for m in via_registry)
    blanket_registry = pe.issues(blanket, route("IDN", "KOR"))
    # Legacy path: with the K-ETA list gone, the reviewed branch alone refuses.
    registry([e for e in json.loads(SEED.read_text(encoding="utf-8")) if e["id"] != "kor_keta"])
    assert not pe._keta_established_without_indonesia()
    via_legacy = pe.issues(keta_answer(), route("IDN", "KOR"))
    assert via_legacy == [m for m in via_registry if "Indonesia is not K-ETA eligible" in m]
    # The blanket exemption is refused identically on both paths: that message
    # set is the reason the branch is not retired.
    assert pe.issues(blanket, route("IDN", "KOR")) == blanket_registry != []
    # Neither path refuses a diplomatic passport it never reviewed.
    registry(json.loads(SEED.read_text(encoding="utf-8")))
    assert not any("Indonesia is not K-ETA" in m for m in
                   pe.issues(blanket, route("IDN", "KOR", document="diplomatic_passport")))


@pytest.mark.parametrize("nat", ["PHL", "VNM"])
def test_philippines_and_vietnam_korea_are_now_refused(registry, files, nat):
    entry = {"route": {"nationality": nat, "destination": "KOR", "travel_purpose": "tourism"},
             "verified_at": "2026-09-12", "verified_by": "Trip.com operations (t)", "verifier": "human",
             "source_url": "https://www.k-eta.go.kr/portal/apply/index.do",
             "note": "K-ETA applies", "fields": {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
                                                  "requirement_detail": "eta_electronic_authorization",
                                                  "visa_products": [{"type": "K-ETA"}]}}
    with pytest.raises(ValueError) as err:
        vo.append_operator_entry(entry)
    assert "K-ETA" in str(err.value) and f"{nat} is absent" in str(err.value)
    # Serve time: the same answer, if it were cached, is held with the reason.
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, keta_answer(), cached=True, stale=False,
                                                 released=True), route(nat, "KOR"))
    assert out["held"] and any("K-ETA" in c for c in out["contradictions"])


@pytest.mark.parametrize("nat", ["JPN", "GBR", "USA", "AUS"])
def test_listed_nationality_is_allowed(registry, nat):
    assert pe.issues(keta_answer(), route(nat, "KOR")) == []
    out = kp.apply_verified_overrides(kp._result(kp.STATUS_PRIMARY, keta_answer(), cached=True, stale=False,
                                                 released=True), route(nat, "KOR"))
    assert not any("product eligibility" in c for c in out["contradictions"])


def test_eligibility_list_does_not_establish_an_obligation_to_apply(registry):
    free = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
            "permitted_stay_days": 30, "visa_products": []}
    # Russia's unified e-visa list is not established: silent.
    assert pe.issues(free, route("MYS", "RUS")) == []
    assert pe.unstated_lists(free, route("MYS", "RUS")) == ["rus_unified_evisa"]
    annotated = pe.annotate(free, route("MYS", "RUS"))
    # Reported, never written into the served guidance.
    assert annotated == free and "scheme_list_unstated" not in annotated
    assert "_permission_eligibility_issues" not in annotated
    # A list can establish eligibility for an optional product; it does not
    # establish whether the traveler separately qualifies for visa-free entry.
    quote = "Malaysia\nChina\nIndia\nThailand"
    rus = dict(_entry("rus_unified_evisa"), list_state="established", eligible_nationalities=["MYS", "CHN", "IND", "THA"],
               source_url="https://evisa.kdmid.ru/", list_quote=quote, quote_sha256=sr.quote_hash(quote),
               checked_at="2026-09-12")
    registry([e for e in json.loads(SEED.read_text(encoding="utf-8")) if e["id"] != "rus_unified_evisa"] + [rus])
    assert pe.issues(free, route("MYS", "RUS")) == []
    assert kp.serve_time_invariants(pe.annotate(free, route("MYS", "RUS"))) == []
    # Malaysia -> Russia's actual rule remains pinned by its reviewed route
    # regression; this synthetic list is deliberately insufficient evidence.
    assert pe.issues(dict(free, requirement_detail="conditional_visa_free"), route("MYS", "RUS")) == []
    assert pe.issues({"disposition": "VISA_REQUIRED", "requirement_detail": "evisa"}, route("MYS", "RUS")) == []
    # K-ETA's captured page explicitly lists application exemptions. The
    # registry's membership list cannot decide those exemptions or their dates.
    assert pe.issues(free, route("JPN", "KOR")) == []
    assert pe.issues(dict(free, visa_category="K-ETA exempt during the applicable policy interval"),
                     route("JPN", "KOR")) == []


def test_not_established_list_refuses_nothing_and_releases_nothing(registry):
    esta = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "requirement_detail": "eta_electronic_authorization",
            "visa_category": "ESTA", "visa_products": [{"type": "ESTA (Visa Waiver Program)"}]}
    # Taiwan is served ESTA today; the VWP list could not be read (bot challenge).
    assert pe.issues(esta, route("TWN", "USA")) == []
    assert pe.issues(esta, route("CHN", "USA")) == []
    assert pe.unstated_lists(esta, route("TWN", "USA")) == ["usa_esta_vwp"]
    annotated = pe.annotate(esta, route("CHN", "USA"))
    assert pe.unstated_lists(esta, route("CHN", "USA")) == ["usa_esta_vwp"]
    assert "scheme_list_unstated" not in annotated
    # A stale key from an earlier build is removed, never trusted.
    assert "scheme_list_unstated" not in pe.annotate(dict(esta, scheme_list_unstated=["x"]), route("CHN", "USA"))
    # Not established means no covers() either: nothing is released on its account.
    assert not sr.covers(next(e for e in sr.entries() if e["id"] == "usa_esta_vwp"), "TWN")
    assert kp.serve_time_invariants(annotated) == []


def test_evisitor_651_is_now_checked(registry):
    evisitor = {"disposition": "VISA_REQUIRED", "requirement_detail": "evisa", "visa_category": "eVisitor (subclass 651)",
                "visa_products": [{"type": "eVisitor (subclass 651)", "fee": None}]}
    assert pe.issues(evisitor, route("DEU", "AUS")) == []
    assert pe.issues(evisitor, route("GBR", "AUS")) == []
    problems = pe.issues(evisitor, route("THA", "AUS"))
    assert problems and "eVisitor" in problems[0] and "THA is absent" in problems[0]
    # Keyed by program_id when the name is in another language.
    foreign = dict(evisitor, visa_products=[{"type": "电子访客签证", "program_id": "aus_evisitor_651"}],
                   visa_category="visitor")
    assert pe.issues(foreign, route("THA", "AUS"))
    assert pe.issues(foreign, route("FRA", "AUS")) == []


def test_thailand_australia_eta_601_is_still_refused(registry):
    eta = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED", "requirement_detail": "eta_electronic_authorization",
           "visa_category": "ETA (subclass 601)", "government_fee": {"amount": 20, "currency": "AUD"},
           "source_url": pe.PROGRAMS[0].source_url}
    problems = pe.issues(eta, route("THA", "AUS"))
    assert problems and "Electronic Travel Authority (subclass 601)" in problems[0]
    assert "THA is absent" in problems[0] and "list checked 2026-09-12" in problems[0]
    # Registry gone: the legacy Australian list still refuses.
    registry([])
    assert pe.PROGRAMS[0].id == "aus_eta_601" and len(pe.PROGRAMS[0].eligible_nationalities) == 33
    legacy = pe.issues(eta, route("THA", "AUS"))
    assert legacy and "THA is absent" in legacy[0] and "list checked 2026-09-09" in legacy[0]


def test_programs_is_a_view_over_the_registry(registry):
    ids = [p.id for p in pe.PROGRAMS]
    assert ids[0] == "aus_eta_601" and set(ids) == {"aus_eta_601", "aus_evisitor_651", "kor_keta"}
    assert len(pe.PROGRAMS) == 3
    registry([_entry("kor_keta")])
    assert [p.id for p in pe.PROGRAMS] == ["aus_eta_601", "kor_keta"]
    assert pe.PROGRAMS[0].checked_at == "2026-09-09"  # the legacy list stood in


def test_no_programme_is_selected_by_name_alone(registry):
    # A Korean tourist visa named like nothing in the registry resolves to no entry.
    assert pe.issues({"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
                      "visa_products": [{"type": "C-3-9 tourist visa"}]}, route("CHN", "KOR")) == []
    # A generic "ETA" label to Korea resolves through the eta family (one programme) and is checked.
    assert pe.issues({"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
                      "requirement_detail": "eta_electronic_authorization"}, route("CHN", "KOR"))


@pytest.mark.parametrize("program_id", ["arbitrary_unknown_program", "kor_keta"])
def test_invalid_program_id_cannot_bypass_known_thailand_eta_eligibility(registry, program_id):
    g = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
         "requirement_detail": "eta_electronic_authorization", "visa_category": "ETA subclass 601",
         "program_id": program_id}
    assert any("THA is absent" in p for p in pe.issues(g, route("THA", "AUS")))
    product = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
               "visa_category": "Visitor visa", "visa_products": [{"type": "ETA subclass 601", "program_id": program_id}]}
    assert any("THA is absent" in p for p in pe.issues(product, route("THA", "AUS")))


@pytest.mark.parametrize("program_id", ["aus_eta_601", " aus_eta_601 "])
def test_recognized_program_id_cannot_mask_an_ineligible_evisitor(registry, program_id):
    # Hong Kong is on ETA 601's list and absent from eVisitor 651's list.
    # Relabeling the id must not let that ineligible eVisitor be recommended.
    g = {"disposition": "VISA_REQUIRED", "requirement_detail": "evisa",
         "program_id": program_id, "visa_category": "eVisitor (subclass 651)"}
    for answer in (g, dict(g, visa_category="Visitor visa",
                          official_portal_url="https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/evisitor-651")):
        assert any("product identity:" in p and "aus_evisitor_651" in p
                   for p in pe.issues(answer, route("HKG", "AUS")))


def test_generic_ministry_portal_and_ambiguous_label_do_not_create_an_identity_conflict(registry):
    g = {"disposition": "ELECTRONIC_AUTHORIZATION_REQUIRED",
         "requirement_detail": "eta_electronic_authorization", "program_id": "aus_eta_601",
         "visa_category": "Electronic travel permission", "official_portal_url": "https://immi.homeaffairs.gov.au/"}
    assert pe.issues(g, route("HKG", "AUS")) == []
    assert sr.identity_conflicts(dict(g, visa_category="ETA or eVisitor"), "AUS") == []
