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
             "text": ("For U.S. citizens: the documents required for the tourist "
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
