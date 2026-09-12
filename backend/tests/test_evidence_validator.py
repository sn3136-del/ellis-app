"""ResearchEvidenceValidator + full-route grounded-disposition regression.

Proves (with injected official-shaped pages, no network):
 - US->China grounds VISA_REQUIRED (EMBASSY_VISA_REQUIRED) from an L-visa page;
 - US->Mexico grounds VISA_EXEMPT (VISA_FREE) from a "no visa required" page;
 - multilingual (Simplified Chinese / Spanish) support;
 - deep visa pages are preferred by internal-link following;
 - Moonshot content-filtered pages do not fail the China route;
 - citations are validated; unsupported claims + wrong jurisdiction rejected;
 - missing disposition triggers the retry signal;
 - no mock portal / no admin action is involved.
"""
import pytest
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.visa_snapshot import (evidence_validator as evv, fetching, kimi_research,
                               ondemand, pipeline, source_discovery)
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import VisaRoute
from app.visa_snapshot.routekey import RouteInput, normalize_route, route_key


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESEARCH_DIR", tmp_path / "research")
    yield
    fetching.set_fetcher(None)
    fetching.set_search_provider(None)
    fetching.set_render_fetcher(None)
    kimi_research.set_extractor(None)
    source_discovery.set_proposer(None)


# --------------------------------------------------------------- unit tests ---
def test_supports_disposition_multilingual():
    assert evv.supports_disposition("must apply for a tourist (L) visa", "EMBASSY_VISA_REQUIRED")
    assert evv.supports_disposition("需要办理签证", "EMBASSY_VISA_REQUIRED")            # zh
    assert evv.supports_disposition("no necesitan visa para entrar a México", "VISA_FREE")  # es
    assert evv.supports_disposition("U.S. citizens do not require a visa", "VISA_FREE")
    # A visa-free page must NOT read as visa-required and vice-versa.
    assert not evv.supports_disposition("no visa is required", "EMBASSY_VISA_REQUIRED")
    assert not evv.supports_disposition("you must apply for a visa", "VISA_FREE")


def test_validator_rejects_nonofficial_and_wrong_jurisdiction():
    pages = [{"url": "https://us.china-embassy.gov.cn/eng/visa", "hostname": "us.china-embassy.gov.cn",
              "text": "Foreigners must apply for a tourist (L) visa."}]
    # nonofficial source
    v = evv.validate_disposition({"destination_country": "CHN"}, "EMBASSY_VISA_REQUIRED",
                                 ["https://wikipedia.org/china"], pages)
    assert not v["ok"] and any("non_official" in r for r in v["reasons"])
    # wrong jurisdiction (Mexican route cited from a Chinese domain)
    v2 = evv.validate_disposition({"destination_country": "MEX"}, "VISA_FREE",
                                  ["https://us.china-embassy.gov.cn/eng/visa"], pages)
    assert not v2["ok"] and any("jurisdiction_mismatch" in r for r in v2["reasons"])
    # citation that does not support the claim
    v3 = evv.validate_disposition({"destination_country": "CHN"}, "VISA_FREE",
                                  ["https://us.china-embassy.gov.cn/eng/visa"], pages)
    assert not v3["ok"] and any("citation_unsupported" in r for r in v3["reasons"])


def test_missing_disposition_flags_retry():
    out = evv.validate_extraction({"destination_country": "MEX"}, {"fields": {}}, [],
                                  disposition="", disposition_sources=[],
                                  required_fields=("visa_requirement", "government_fee_amount"))
    assert out["ok"] is False
    assert "disposition" in out["missing_fields"]


def test_no_evidence_is_never_visa_free():
    det = evv.detect_disposition_from_pages(
        {"destination_country": "MEX"},
        [{"url": "https://www.gob.mx/inm", "hostname": "www.gob.mx",
          "text": "Instituto Nacional de Migración. Homepage. News and services."}])
    assert det["disposition"] is None


# --------------------------------------------- full-route grounded outcomes ---
# Each test uses a UNIQUE arrival date (=> unique route_key) so the shared
# session DB never short-circuits one test on another's cached route.
def _ri(dest, day):
    return RouteInput(passport_nationality="USA", passport_issuing_country="USA",
                      travel_document_type="ordinary_passport",
                      lawful_country_of_residence="USA", destination_country=dest,
                      visa_category="tourist_visa", policy_period=day)


def _answers(dest, day):
    return {"passport_nationality": "USA", "passport_issuing_country": "USA",
            "travel_document_type": "ordinary_passport", "lawful_country_of_residence": "USA",
            "destination_country": dest, "visa_category": "tourist_visa",
            "travel_purpose": "tourism", "arrival_date": day, "departure_date": day,
            "age": 34, "email": "a@example.com", "preferred_language": "en"}

# Deep official pages (the shape research reaches after link-following/seeds).
_CHN_VISA_PAGE = ("Chinese Visa Application. U.S. citizens traveling to China for "
                  "tourism must apply for a tourist (L) visa at the Chinese Embassy "
                  "or Consulate-General before travel. 需要办理旅游（L字）签证。 "
                  "Required: valid passport, application form, photo. Fingerprints "
                  "are collected at the Visa Application Service Center.")
_MEX_VISA_PAGE = ("Los nacionales de los siguientes países no necesitan visa para "
                  "entrar a México como turistas. United States citizens do not "
                  "require a visa for tourism and may stay up to 180 days with a "
                  "valid passport and the Forma Migratoria Multiple (FMM).")


def _fetch_returning(mapping, links=None):
    def _f(url, timeout_seconds=20):
        text = mapping.get(url)
        if text is None:
            return FetchResult(requested_url=url, ok=False, error="http 404")
        host = url.split("/")[2]
        return FetchResult(requested_url=url, ok=True, final_url=url, final_hostname=host,
                           http_status=200, content_text=text, content_hash=str(hash(url)),
                           page_language="zh" if any(ord(c) > 0x4e00 for c in text) else "en",
                           retrieved_at="2026-07-24T00:00:00Z", links=links or [])
    return _f


def test_us_to_china_grounds_visa_required(db):
    url = "https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/"
    source_discovery.set_proposer(lambda q: [url])
    fetching.set_fetcher(_fetch_returning({url: _CHN_VISA_PAGE}))
    # No Kimi extractor -> the deterministic detector must ground it from text.
    kimi_research.set_extractor(lambda s, u: {"fields": {}, "conflicts": []})
    job = ondemand.create_job(db, org_id="rc", user_id="u", answers=_answers('CN','2026-08-01'),
                              normalized=normalize_route(_ri('CN','2026-08-01')), key=route_key(_ri('CN','2026-08-01')))
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete", (job.status, job.progress[-4:])
    assert job.counters["disposition"] == "EMBASSY_VISA_REQUIRED"
    de = job.counters["disposition_evidence"]
    assert de["validated"] and "china-embassy.gov.cn" in de["supporting_url"]
    head = db.execute(select(VisaRoute).where(VisaRoute.route_key == job.route_key)).scalars().one()
    assert head.disposition == "EMBASSY_VISA_REQUIRED" and head.research_status == "verified"


def test_us_to_mexico_grounds_visa_exempt(db):
    url = "https://www.inm.gob.mx/gobmx/word/index.php/paises-no-requieren-visa-para-mexico/"
    source_discovery.set_proposer(lambda q: [url])
    fetching.set_fetcher(_fetch_returning({url: _MEX_VISA_PAGE}))
    kimi_research.set_extractor(lambda s, u: {"fields": {}, "conflicts": []})
    job = ondemand.create_job(db, org_id="rm", user_id="u", answers=_answers('MX','2026-08-02'),
                              normalized=normalize_route(_ri('MX','2026-08-02')), key=route_key(_ri('MX','2026-08-02')))
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete", (job.status, job.progress[-4:])
    assert job.counters["disposition"] == "VISA_FREE"
    de = job.counters["disposition_evidence"]
    assert de["validated"] and "gob.mx" in de["supporting_url"]


def test_china_content_filtered_page_does_not_fail_route(db):
    """One Moonshot-filtered page (extractor raises on it) must not sink the
    route: the deterministic detector still grounds from the visa page's text."""
    good = "https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/"
    political = "https://www.mfa.gov.cn/eng/"
    source_discovery.set_proposer(lambda q: [good, political])
    fetching.set_fetcher(_fetch_returning({
        good: _CHN_VISA_PAGE,
        political: "Foreign Ministry political news and statements. 外交部新闻。"}))

    def filter_extractor(system, user):
        # Simulate the provider content-filter 400 on the political-page batch.
        if "mfa.gov.cn/eng" in user and "china-embassy" not in user:
            raise RuntimeError("400 content_filter")
        return {"fields": {}, "conflicts": []}
    kimi_research.set_extractor(filter_extractor)
    job = ondemand.create_job(db, org_id="rf", user_id="u", answers=_answers('CN','2026-08-03'),
                              normalized=normalize_route(_ri('CN','2026-08-03')), key=route_key(_ri('CN','2026-08-03')))
    job = ondemand.run_job(db, job.id)
    assert job.status == "complete"
    assert job.counters["disposition"] == "EMBASSY_VISA_REQUIRED"


def test_deep_links_are_followed_from_landing_page(db):
    """A landing page that only LINKS to the visa page: link-following must reach
    the deep page and ground the disposition."""
    landing = "https://us.china-embassy.gov.cn/eng/"
    deep = "https://us.china-embassy.gov.cn/eng/visainfo/tourist-L-apply"
    source_discovery.set_proposer(lambda q: [landing])
    fetching.set_fetcher(_fetch_returning(
        {landing: "Welcome to the Embassy. Consular services and visa information.",
         deep: _CHN_VISA_PAGE},
        links=[deep]))
    kimi_research.set_extractor(lambda s, u: {"fields": {}, "conflicts": []})
    job = ondemand.create_job(db, org_id="rd", user_id="u", answers=_answers('CN','2026-08-04'),
                              normalized=normalize_route(_ri('CN','2026-08-04')), key=route_key(_ri('CN','2026-08-04')))
    job = ondemand.run_job(db, job.id)
    assert job.counters.get("deep_links_followed", 0) >= 1
    assert job.counters["disposition"] == "EMBASSY_VISA_REQUIRED"


def test_render_fallback_used_for_challenge_page(db, monkeypatch):
    """An anti-bot challenge shell (200 but tiny) routes to the render fallback,
    which returns the real official text -> disposition grounded."""
    url = "https://www.inm.gob.mx/gobmx/word/index.php/paises-no-requieren-visa-para-mexico/"
    source_discovery.set_proposer(lambda q: [url])
    fetching.set_fetcher(None)   # exercise fetch() -> _default_fetch -> render fallback
    monkeypatch.setattr(fetching, "_default_fetch", lambda u, timeout_seconds=20.0: FetchResult(
        requested_url=u, ok=False, http_status=200, content_text="Verifying your browser...",
        challenge=True, error="anti-bot/JS challenge shell", final_hostname="www.inm.gob.mx"))
    fetching.set_render_fetcher(lambda u, timeout_seconds=20.0: FetchResult(
        requested_url=u, ok=True, final_url=u, final_hostname="www.inm.gob.mx",
        http_status=200, content_text=_MEX_VISA_PAGE, content_hash="r", page_language="es",
        retrieved_at="2026-07-24T00:00:00Z"))
    kimi_research.set_extractor(lambda s, uu: {"fields": {}, "conflicts": []})
    job = ondemand.create_job(db, org_id="rr", user_id="u", answers=_answers('MX','2026-08-05'),
                              normalized=normalize_route(_ri('MX','2026-08-05')), key=route_key(_ri('MX','2026-08-05')))
    job = ondemand.run_job(db, job.id)
    assert job.counters["disposition"] == "VISA_FREE"


def test_conflicting_official_sources_flagged(db):
    """Two official pages that disagree (visa-free vs visa-required) -> material
    conflict, never a silent pick."""
    a = "https://www.inm.gob.mx/a"
    b = "https://embamex.sre.gob.mx/b"
    source_discovery.set_proposer(lambda q: [a, b])
    fetching.set_fetcher(_fetch_returning({
        a: "U.S. citizens do not require a visa for tourism.",
        b: "U.S. citizens must apply for a tourist visa before travel."}))
    kimi_research.set_extractor(lambda s, u: {"fields": {}, "conflicts": []})
    job = ondemand.create_job(db, org_id="rx", user_id="u", answers=_answers('MX','2026-08-06'),
                              normalized=normalize_route(_ri('MX','2026-08-06')), key=route_key(_ri('MX','2026-08-06')))
    job = ondemand.run_job(db, job.id)
    assert job.counters.get("material_conflict") is True
    assert job.status in ("conflicted", "research_incomplete")


def test_noisy_headline_page_skipped_and_hong_kong_evisa_ignored():
    """A dense embassy headline page (mentions Hong Kong 'e-Visa' AND a visa-free
    arrangement) is NOISE -> skipped; a clean L-visa page grounds the route."""
    noisy = {"url": "https://us.china-embassy.gov.cn/eng/lsfw/zj/qz2021/",
             "hostname": "us.china-embassy.gov.cn",
             "text": ("Embassy of China in the United States. Notices: Hong Kong "
                      "e-Visa Arrangement. Visa-free transit policy update. "
                      "Tourist visa application documents simplified.")}
    clean = {"url": "https://us.china-embassy.gov.cn/eng/notice.htm",
             "hostname": "us.china-embassy.gov.cn",
             "text": ("U.S. citizens must obtain a visa. The documents required for the tourist "
                      "visa (L-Visa) will be simplified. Apply at the Embassy.")}
    # The noisy page attests >1 disposition -> excluded; clean page grounds it.
    det = evv.detect_disposition_from_pages(
        {"destination_country": "CHN", "passport_nationality": "USA"}, [noisy, clean])
    assert det["disposition"] == "EMBASSY_VISA_REQUIRED"
    assert not det["conflict"]
    assert "china-embassy.gov.cn/eng/notice" in det["supporting_url"]
    # The Hong Kong e-visa mention must NOT ground EVISA for mainland China.
    assert "EVISA_REQUIRED" not in det["attested"]


def test_mission_self_reference_never_anchors():
    """'Embassy of X in the United States' is the MISSION's location, not the
    applicant's nationality — a visa-exemption page (about other countries)
    with only that header must not ground VISA_FREE for a US applicant."""
    text = ("Visa Exemption Eligibility_Embassy of the People's Republic of "
            "China in the United States of America. Citizens of France, Germany "
            "and Italy may enter China visa-free for up to 30 days.")
    assert not evv.supports_disposition(text, "VISA_FREE", nationality="USA")
    # With a REAL applicant-nationality statement it does ground.
    text2 = ("U.S. citizens do not require a visa to enter for tourism.")
    assert evv.supports_disposition(text2, "VISA_FREE", nationality="USA")


def test_chinese_destination_mention_does_not_anchor():
    """赴美国 ('traveling TO the US') in a nearby headline is a destination
    mention, not the applicant's nationality — 免签 index links on a dense zh
    homepage must not ground VISA_FREE for a US passport holder."""
    text = ("单方面免签政策常见问题解答 FAQs on Visa-free Entry. "
            "再次提醒赴美国、加拿大、墨西哥观看世界杯足球赛的中国公民注意安全。")
    assert not evv.supports_disposition(text, "VISA_FREE", nationality="USA")
    # 美国公民 (US citizens) IS a nationality anchor.
    text2 = "美国公民来华旅游免签入境。"
    assert evv.supports_disposition(text2, "VISA_FREE", nationality="USA")


@pytest.mark.parametrize("nationality", ["HKG", "TWN", "JPN", "KOR", "USA", "THA", "SGP", "MYS", "GBR", "RUS", "AUS", "IDN", "PHL", "FRA", "VNM", "ESP", "IND", "CAN"])
def test_all_station_nationalities_have_a_deterministic_anchor(nationality):
    name = evv._NATIONALITY_NAMES[nationality][0]
    assert evv.supports_disposition(f"{name} passport holders must obtain a visa.", "VISA_REQUIRED", nationality=nationality)


def test_unknown_nationality_does_not_pass_on_universal_wording():
    assert not evv.supports_disposition("All foreign nationals must obtain a visa.", "VISA_REQUIRED", nationality="ZZZ")
    assert not evv.supports_disposition("Citizens of the following countries are visa-free.", "VISA_FREE", nationality="ZZZ")


def test_disposition_support_checks_later_matches_too():
    text = "No visa is required for this other group. " + "Useful information. " * 50 + "Hong Kong passport holders must obtain a visa."
    assert evv.supports_disposition(text, "VISA_REQUIRED", nationality="HKG")


def test_arrival_visa_is_explicit_and_not_advance_visa_evidence():
    text = "Indonesian passport holders may obtain a visa on arrival."
    assert evv.supports_disposition(text, "VISA_ON_ARRIVAL", nationality="IDN")
    assert not evv.supports_disposition("Visa on arrival is not available for Indonesian citizens.", "VISA_ON_ARRIVAL", nationality="IDN")


def test_quote_matching_requires_full_normalized_text():
    assert evv.quote_in_text("SINGLE   entry 25 USD", "Single entry\n25 USD")
    assert not evv.quote_in_text("Single entry 25 USD for all applicants", "Single entry 25 USD")
    assert not evv.quote_in_text("", "anything")


@pytest.mark.parametrize("separator", [". ", "; ", "\n", " but "])
def test_other_nationality_rule_cannot_use_nearby_hong_kong_name(separator):
    text = "Hong Kong citizens must obtain a visa" + separator + "Chinese citizens are visa-free."
    assert not evv.supports_disposition(text, "VISA_FREE", nationality="HKG")
    assert evv.supports_disposition(text, "VISA_REQUIRED", nationality="HKG")


def test_destination_mention_cannot_ground_applicant_nationality():
    text = "British citizens do not need a visa to China."
    assert not evv.supports_disposition(text, "VISA_FREE", nationality="CHN")


@pytest.mark.parametrize("text", ["中国签证申请材料清单", "Chinese citizens: documents required for a tourist visa", "Chinese visa information and application forms"])
def test_application_documents_or_visa_title_do_not_prove_required(text):
    assert not evv.supports_disposition(text, "VISA_REQUIRED", nationality="CHN")


def test_authorization_program_name_alone_does_not_prove_requirement():
    assert not evv.supports_disposition("Hong Kong citizens: Electronic travel authorization ETA information", "ELECTRONIC_AUTHORIZATION_REQUIRED", nationality="HKG")
    assert evv.supports_disposition("Hong Kong passport holders must obtain an ETA.", "ELECTRONIC_AUTHORIZATION_REQUIRED", nationality="HKG")



def test_excerpt_uses_the_same_later_nationality_supported_statement():
    text = "Canadian passport holders must obtain a visa. " + "Unrelated page text. " * 30 + "Hong Kong passport holders must obtain a visa."
    quote = evv.find_supporting_excerpt(text, "VISA_REQUIRED", nationality="HKG")
    assert quote == "Hong Kong passport holders must obtain a visa"


@pytest.mark.parametrize('name,value,text', [
    ('government_fee', {'amount': 999, 'currency': 'CAD'}, 'The fee is 7 CAD.'),
    ('government_fee', {'amount': 999, 'currency': 'CAD'}, 'The fee is 7 CAD and the stay is 999 days.'),
    ('biometrics_required', True, 'Page navigation.'),
    ('biometrics_required', True, 'Biometrics are not required.'),
    ('processing_time', '30 days', 'The permitted stay is 30 days.'),
    ('permitted_stay_days', 30, 'Processing takes 30 days.'),
])
def test_field_evidence_matches_entire_value_and_its_subject(name, value, text):
    assert not evv.field_value_supported(name, value, text)


def test_eu_common_visa_law_competence_is_narrow():
    # guard-20260912 T3: Union law is competent only when the citation names
    # an instrument from the closed list; a bare eur-lex host is not a law.
    law = 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02018R1806-20251230'
    assert evv.jurisdiction_matches(law, 'FRA')
    assert evv.jurisdiction_matches(law, 'ESP')
    assert evv.jurisdiction_matches(law, 'ITA')
    assert not evv.jurisdiction_matches(law, 'GBR')
    assert not evv.jurisdiction_matches('https://eur-lex.europa.eu/legal-content/EN/TXT/', 'FRA')


def test_curated_uae_embassy_has_exact_uae_jurisdiction():
    url = 'https://www.uae-embassy.org/visas-services/visas-for-non-us-citizens'
    assert evv.source_is_official(url)
    assert evv.jurisdiction_matches(url, 'ARE')
    # The embassy's physical location does not make it a US authority, and
    # a curated non-government suffix must not become ownerless elsewhere.
    for destination in ('USA', 'HKG', 'TGO'):
        assert not evv.jurisdiction_matches(url, destination)
    for spoof in ('https://uae-embassy.org.example.com/visa',
                  'https://fake-uae-embassy.org/visa'):
        assert not evv.jurisdiction_matches(spoof, 'ARE')


def test_detector_includes_explicit_visa_on_arrival():
    route = {'passport_nationality': 'IDN', 'destination_country': 'THA', 'travel_purpose': 'tourism'}
    result = evv.detect_disposition_from_pages(route, [{'url': 'https://consular.mfa.go.th/visa',
        'text': 'Indonesian citizens can obtain a visa on arrival for tourism.'}])
    assert result['disposition'] == 'VISA_ON_ARRIVAL' and not result['conflict']


# ---------------------------------------------------------------------------
# guard-20260912 T3: jurisdiction_matches delegates to source_authority and
# keeps its contract over thirty live host and destination pairs sampled
# from the 11 September database copy. The one pair whose answer moved is
# the free-movement Directive 2004/38 cited for France: it is not on the
# closed visa-law instrument list, so Union competence no longer covers it.
# ---------------------------------------------------------------------------
LIVE_PAIRS = [
    ("https://france-visas.gouv.fr/en/web/france-visas/accueil", "FRA", True),
    ("https://www.uscis.gov/working-in-the-united-states/temporary-visitors-for-business/b-1-temporary-business-visitor", "USA", True),
    ("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02004L0038-20110616", "FRA", False),
    ("https://s.nia.gov.cn/mps/bszy/wlgaot/sqgowl/201903/t20190313_1002.html", "MAC", False),
    ("https://eviza.mae.ro/TypeOfVisa", "DEU", False),
    ("https://www.k-eta.go.kr/portal/board/viewboarddetail.do?bbsSn=299707", "KOR", True),
    ("https://www.immd.gov.hk/eng/press/press-releases/20231207a.html", "AGO", False),
    ("https://www.immigration.gov.bs/entry-requirements/before-your-arrival/", "BHS", True),
    ("https://www.exteriores.gob.es/", "ESP", True),
    ("https://mfa.gov.af/en/page/38999", "AFG", True),
    ("https://evisa.gov.ly/", "LBY", True),
    ("https://www.instagram.com/p/Dab9NF9IILJ/", "VNM", False),
    ("https://chong.cancilleria.gob.ar/en/entry-argentina-peoples-republic-china-passport-tourism-or-business-without-visa", "ARG", True),
    ("https://www.france-visas.gouv.fr/en/etudiant", "FRA", True),
    ("https://www.hikorea.go.kr/info/InfoDatail.pt?CAT_SEQ=161&PARENT_ID=135", "KOR", True),
    ("http://tn.china-embassy.gov.cn/lsfw/lsbhyxz/202310/t20231007_11157169.htm", "TUN", False),
    ("https://www.imi.gov.my/wp-content/uploads/2022/01/21OKT_FAQs-MYS-CHINA_LULUS.pdf", "MYS", True),
    ("https://taipei.mfa.gov.sg/consular-services/visa-information/", "SGP", True),
    ("https://my.china-embassy.gov.cn/eng/fwzc/lsyw/qz/202508/t20250801_11681401.htm", "CHN", True),
    ("https://evisa.gov.vu/", "VUT", True),
    ("https://www.anzen.mofa.go.jp/info/pcsafetymeasure_052.html", "TUR", False),
    ("https://consulatedrwest.gob.do/en/listado-de-paises-y-condiciones-de-visado", "DOM", True),
    ("https://beninembassy.us/visas-requirements/", "BEN", True),
    ("https://cancilleria.gob.bo/mre/2025/12/01/23577/", "BOL", True),
    ("https://www.mofa.go.kr/my-en/brd/m_21504/view.do?page=1&seq=46", "KOR", True),
    ("https://0404.go.kr/bbs/contsPst/MST0000000000113/13/detail", "TUR", False),
    ("https://www.exteriores.gob.es/Consulados/pekin/en/ServiciosConsulares/Paginas/Consular/Visado-de-transito-aeroportuario.aspx", "ESP", True),
    ("https://mfa.gov.ua/en/consular-affairs/entry-and-stay-foreigners-ukraine/entry-regime-ukraine-foreign-citizens", "UKR", True),
    ("https://www.gov.uk/student-visa", "GBR", True),
    ("https://www.mfa.gov.sg/travelling-overseas/travel-advisories-notices-and-visa-information/indonesia/", "IDN", False),
]


@pytest.mark.parametrize('url,destination,expected', LIVE_PAIRS)
def test_jurisdiction_matches_delegates_and_keeps_its_contract(url, destination, expected):
    from app.visa_snapshot import source_authority as sa
    assert evv.jurisdiction_matches(url, destination) is expected
    assert evv.jurisdiction_matches(url, destination) == sa.is_competent(
        url, {'destination_country': destination}, 'disposition')
    if expected:
        assert sa.classify(url, {'destination_country': destination}) in (sa.KIND_DESTINATION, sa.KIND_EU_VISA_LAW)
