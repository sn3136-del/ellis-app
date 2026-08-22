"""Kimi single-pass route guidance: ONE structured Kimi request per uncached
route under one hard deadline.

Proves: exactly one Kimi call produces the final authoritative result (no
second verification pass exists); the honest label is "Kimi route decision";
normal route resolution performs no official-source research; cached identical
routes load instantly without any provider call (and cache only complete
results); malformed answers retry exactly once; the total deadline is enforced
with the honest retry message (never a spinner); provider failures map to
precise messages; stored two-pass-era labels are normalized at serve time;
Kimi gets no secrets; irreversible actions keep applicant confirmation."""
import pytest
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.visa_snapshot import kimi_primary
from app.visa_snapshot.models import (HumanReviewTask, KimiRouteGuidanceCache,
                                      OnDemandRouteResearchJob)

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-kp", "X-User-Id": "u1"}

ROUTE = {"address_line1": "12 Harbor Lane", "address_city": "Springfield",
         "address_country": "USA",
         "passport_nationality": "USA", "passport_issuing_country": "USA",
         "travel_document_type": "ordinary_passport",
         "lawful_country_of_residence": "USA", "destination_country": "JPN",
         "visa_category": "tourist_visa", "travel_purpose": "tourism",
         "arrival_date": "2026-09-10", "departure_date": "2026-09-20",
         "age": 30, "email": "kp@example.com", "preferred_language": "en",
         "prior_refusals": "no"}

GOOD_ANSWER = {
    "disposition": "VISA_EXEMPT", "visa_category": "Temporary visitor (tourism)",
    "permitted_stay": "90 days", "permitted_stay_days": 90,
    "passport_validity": "valid for the stay",
    "passport_validity_requirement": {"kind": "valid_through_departure", "months": None},
    "required_documents": ["passport", "onward ticket"],
    "forms": ["ED card"], "application_channel": "not_required",
    "official_portal_url": None,
    "government_fee": {"amount": None, "currency": None},
    "processing_time": "none (exempt)", "biometrics_required": False,
    "interview_required": False, "appointment_required": False,
    "account_registration_steps": [], "payment_process": [],
    "submission_process": [], "exceptions": [], "uncertainty": [],
    "arrival_card": {"required": True, "name": "SG Arrival Card",
                     "submission_window": "within 3 days before arrival"},
    "health_requirements": [],
    "route_workflow_type": "visa_exempt_preparation",
    "confidence": "high",
}

# The exact label and stored artifacts from the retired two-pass era — used
# only to prove serve-time normalization strips the claim.
OLD_TWO_PASS_LABEL = ("Kimi K3 route decision — independently checked by a "
                      "second Kimi pass.")


def single_pass(answer, *, counter=None):
    """A provider for the single-pass contract: every call is an analysis call
    (a verification prompt must never be sent)."""
    def provider(system, user):
        assert "verifier" not in system, "no second verification pass may run"
        if counter is not None:
            counter["analyze"] = counter.get("analyze", 0) + 1
        return dict(answer)
    return provider


@pytest.fixture()
def db():
    create_all()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset():
    yield
    kimi_primary.set_provider(None)


def _clear_cache(db):
    for row in db.execute(select(KimiRouteGuidanceCache)).scalars().all():
        db.delete(row)
    db.commit()


# ---- one pass produces the final authoritative result -------------------------
def test_single_pass_produces_final_result_without_research(db):
    _clear_cache(db)
    counter = {}
    kimi_primary.set_provider(single_pass(GOOD_ANSWER, counter=counter))
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_PRIMARY"
    assert g["ai_generated"] is True
    assert counter == {"analyze": 1}                       # exactly ONE Kimi call
    # The honest single-pass verification shape — no verdict, no second pass.
    assert g["verification"] == {"passes": 1, "label": "Kimi route decision"}
    # The decision label claims exactly one Kimi pass and nothing more.
    assert g["label"] == kimi_primary.VERIFIED_LABEL == "Kimi route decision"
    assert "official sources" not in g["label"].lower()
    assert "second" not in g["label"].lower()
    assert "k3" not in g["label"].lower()
    assert g["guidance"]["disposition"] == "VISA_EXEMPT"
    steps = [s["step"] for s in g["workflow_plan"]]
    assert "collect_documents" in steps and "ocr_and_validate_passport" in steps
    # Visa-exempt plan: entry prep + arrival card, NO account/payment/submission.
    assert "arrival_card_preparation" in steps
    for absent in ("account_registration", "payment", "submission",
                   "appointment_booking"):
        assert absent not in steps
    # No official-source research job was created by the guidance path.
    assert db.query(OnDemandRouteResearchJob).count() == before_jobs
    assert g["elapsed_seconds"] < 60


def test_visa_required_plan_keeps_irreversible_confirmations(db):
    _clear_cache(db)
    ans = dict(GOOD_ANSWER, disposition="VISA_REQUIRED",
               visa_category="Tourist L visa", application_channel="embassy",
               government_fee={"amount": 140, "currency": "USD"},
               official_portal_url="https://cova.mfa.gov.cn/", forms=["V.2013"],
               appointment_required=True, route_workflow_type="embassy_submission",
               # A visa-required answer must name its products: "only the
               # 3-month single was listed" was Trip.com's first complaint,
               # so an empty product list is now a contradiction.
               visa_products=[{"type": "Single-entry tourist L", "entry": "single",
                               "validity": "3 months", "max_stay_days": 30,
                               "fee": {"amount": 140, "currency": "USD"},
                               "notes": None}],
               arrival_card=None)
    kimi_primary.set_provider(single_pass(ans))
    g = kimi_primary.get_route_guidance(db, dict(ROUTE, destination_country="CHN"))
    assert g["status"] == "KIMI_PRIMARY"
    assert g["irreversible_requires_confirmation"] is True
    plan = {s["step"]: s for s in g["workflow_plan"]}
    assert plan["prepare_forms"]["reversible"] is True
    assert plan["generate_route_adapter"]["reversible"] is True
    for irrev in ("account_registration", "appointment_booking", "payment",
                  "final_review_and_signature", "submission"):
        assert plan[irrev]["reversible"] is False
        assert plan[irrev]["requires_applicant_confirmation"] is True


def test_appointment_stages_only_when_appointment_required(db):
    _clear_cache(db)
    ans = dict(GOOD_ANSWER, disposition="VISA_REQUIRED",
               application_channel="online_portal", appointment_required=False,
               government_fee={"amount": 25, "currency": "USD"},
               route_workflow_type="evisa_portal", arrival_card=None)
    kimi_primary.set_provider(single_pass(ans))
    g = kimi_primary.get_route_guidance(db, dict(ROUTE, destination_country="VNM"))
    steps = [s["step"] for s in g["workflow_plan"]]
    assert "appointment_search" not in steps and "appointment_booking" not in steps


# ---- cache -------------------------------------------------------------------
def test_cached_identical_route_loads_immediately(db):
    _clear_cache(db)
    counter = {}
    kimi_primary.set_provider(single_pass(GOOD_ANSWER, counter=counter))
    g1 = kimi_primary.get_route_guidance(db, ROUTE)
    g2 = kimi_primary.get_route_guidance(db, ROUTE)
    assert counter == {"analyze": 1}      # cache hit made NO provider call
    assert g1["cached"] is False and g2["cached"] is True
    # The cached row keeps the honest single-pass shape and label.
    assert g2["verification"] == {"passes": 1, "label": "Kimi route decision"}
    assert g2["label"] == kimi_primary.VERIFIED_LABEL
    # Same policy month + same route dimensions => same cache key.
    assert kimi_primary.cache_key(ROUTE) == kimi_primary.cache_key(
        dict(ROUTE, arrival_date="2026-09-25"))
    assert kimi_primary.cache_key(ROUTE) != kimi_primary.cache_key(
        dict(ROUTE, destination_country="KOR"))
    # The schema version is part of the key, so rows written under an older
    # answer schema can never serve again. Bumped to v5 when visa_products,
    # the honest channel, the requirement subcategory and the transit answer
    # joined the contract (Trip.com feedback, 2026-08).
    # Pin the RULE, not a literal: the version is part of the key so rows
    # written under an older answer schema can never serve again. Asserting a
    # frozen string here just breaks on every legitimate bump.
    assert kimi_primary.CACHE_VERSION.startswith("v")
    assert kimi_primary.CACHE_VERSION in kimi_primary.cache_key(ROUTE)
    # A plain route (ordinary passport, no stopover) ends at the version:
    # the transit / document suffixes are appended only when they apply, so
    # the shipped warm cache keeps its keys.
    assert kimi_primary.cache_key(ROUTE).endswith("|" + kimi_primary.CACHE_VERSION)


def test_stale_cache_returns_instantly_flagged_for_refresh(db):
    _clear_cache(db)
    kimi_primary.set_provider(single_pass(GOOD_ANSWER))
    kimi_primary.get_route_guidance(db, ROUTE)
    row = db.execute(select(KimiRouteGuidanceCache)).scalars().first()
    from datetime import datetime, timedelta, timezone
    row.fresh_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    counter = {}
    kimi_primary.set_provider(single_pass(GOOD_ANSWER, counter=counter))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["cached"] is True and g["stale"] is True   # instant, non-blocking
    assert counter == {}                                # refresh is async, not inline


def test_uncertain_results_are_never_cached(db):
    _clear_cache(db)
    bad = dict(GOOD_ANSWER)
    bad.pop("permitted_stay")
    kimi_primary.set_provider(single_pass(bad))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert g["verification"] == {}                     # no decision, no claim
    assert g["label"] == "AI-generated route guidance"
    assert db.query(KimiRouteGuidanceCache).count() == 0   # retry is always possible


# ---- deadline ----------------------------------------------------------------
def test_deadline_exceeded_is_honest_retryable_error(db, monkeypatch):
    _clear_cache(db)
    kimi_primary.set_provider(single_pass(GOOD_ANSWER))
    # A deadline below the minimum call budget trips before any Kimi call.
    monkeypatch.setenv("ELLIS_GUIDANCE_DEADLINE_SECONDS", "1")
    with pytest.raises(kimi_primary.GuidanceTimeout) as exc:
        kimi_primary.get_route_guidance(db, ROUTE)
    assert str(exc.value) == kimi_primary.TIMEOUT_MESSAGE
    # The message must NOT promise a specific duration: the deadline is
    # configurable (and is 90s by default), so naming "one minute" in the
    # text was a promise the code did not keep.
    assert "minute" not in str(exc.value)
    assert "try again" in str(exc.value).lower()
    assert db.query(KimiRouteGuidanceCache).count() == 0   # never cached


def test_slow_malformed_analysis_exhausts_budget_before_retry(db, monkeypatch):
    _clear_cache(db)
    import time as _time
    calls = {"n": 0}
    incomplete = dict(GOOD_ANSWER)
    incomplete.pop("permitted_stay")

    def slow(system, user):
        calls["n"] += 1
        assert calls["n"] == 1, "the malformed retry must not start after the deadline"
        _time.sleep(0.3)
        return dict(incomplete)
    kimi_primary.set_provider(slow)
    monkeypatch.setenv("ELLIS_GUIDANCE_DEADLINE_SECONDS", "5.2")  # 5.2-0.3 < 5 min budget
    with pytest.raises(kimi_primary.GuidanceTimeout):
        kimi_primary.get_route_guidance(db, ROUTE)


def test_default_deadline_is_ninety_seconds():
    # 90s, raised from 60s: the richer answer (every visa product, each with
    # its own stay and fee) legitimately takes the model longer, and a cold
    # route timing out is worse for the reader than waiting.
    assert kimi_primary.DEFAULT_DEADLINE_SECONDS == 90
    assert kimi_primary._deadline_seconds() == 90


# ---- honest failure ----------------------------------------------------------
def test_missing_fields_retry_once_then_honest_uncertain(db):
    _clear_cache(db)
    counter = {}
    bad = dict(GOOD_ANSWER)
    bad.pop("permitted_stay"); bad.pop("processing_time")

    def flaky(system, user):
        assert "verifier" not in system
        counter["analyze"] = counter.get("analyze", 0) + 1
        if counter["analyze"] > 1:
            assert "permitted_stay" in user and "processing_time" in user  # targeted retry
        return dict(bad)
    kimi_primary.set_provider(flaky)
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    before_tasks = db.query(HumanReviewTask).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert counter == {"analyze": 2}                      # exactly one retry, no verify
    assert g["status"] == "KIMI_UNCERTAIN"
    assert set(g["missing_fields"]) == {"permitted_stay", "processing_time"}
    # No broad research auto-started; no administrator task created.
    assert db.query(OnDemandRouteResearchJob).count() == before_jobs
    assert db.query(HumanReviewTask).count() == before_tasks


def test_contradictory_answer_flagged_precisely(db):
    _clear_cache(db)
    bad = dict(GOOD_ANSWER, disposition="VISA_EXEMPT",
               forms=["Visa application form DS-123"])
    kimi_primary.set_provider(single_pass(bad))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert any("visa application" in c for c in g["contradictions"])


def test_malformed_disposition_rejected(db):
    _clear_cache(db)
    kimi_primary.set_provider(single_pass(dict(GOOD_ANSWER, disposition="MAYBE")))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN" and "disposition" in g["missing_fields"]


# ---- serve-time label normalization ------------------------------------------
def test_stored_two_pass_label_is_normalized_at_serve_time():
    stored = {
        "status": "KIMI_PRIMARY",
        "label": OLD_TWO_PASS_LABEL,
        "guidance": {"disposition": "VISA_EXEMPT"},
        "verification": {"verdict": "ACCEPT", "issues": [], "passes": 2,
                         "label": OLD_TWO_PASS_LABEL},
    }
    out = kimi_primary.normalize_guidance_label(stored)
    assert out["label"] == "Kimi route decision"
    assert out["verification"] == {"passes": 1, "label": "Kimi route decision"}
    # No second-pass claim survives anywhere in the normalized artifacts.
    assert "second" not in out["label"].lower()
    assert "second" not in str(out["verification"]).lower()
    # Pure: the stored input is never mutated (no DB migration happens).
    assert stored["label"] == OLD_TWO_PASS_LABEL
    assert stored["verification"]["passes"] == 2
    # Untouched pass-through for already-honest or non-dict values.
    fresh = {"status": "KIMI_PRIMARY", "label": "Kimi route decision",
             "verification": {"passes": 1, "label": "Kimi route decision"},
             "guidance": {}}
    assert kimi_primary.normalize_guidance_label(dict(fresh)) == fresh
    assert kimi_primary.normalize_guidance_label(None) is None


# ---- deterministic arithmetic (never the model) ------------------------------
def test_trip_duration_vs_permitted_stay_advisory(db):
    _clear_cache(db)
    short = dict(GOOD_ANSWER, permitted_stay="30 days", permitted_stay_days=30)
    kimi_primary.set_provider(single_pass(short))
    long_trip = dict(ROUTE, arrival_date="2026-09-01", departure_date="2026-11-15")
    g = kimi_primary.get_route_guidance(db, long_trip)
    assert any("exceeds the permitted stay" in a for a in g["advisories"])


def test_passport_expiry_advisory_is_calculated(db):
    _clear_cache(db)
    req = dict(GOOD_ANSWER,
               passport_validity_requirement={"kind": "months_after_arrival", "months": 6})
    kimi_primary.set_provider(single_pass(req))
    g = kimi_primary.get_route_guidance(
        db, dict(ROUTE, passport_expiry_date="2026-10-01"))
    # Applicant-facing advisory dates are U.S. MM/DD/YYYY, never ISO.
    assert any("must be" in a and "03/10/2027" in a for a in g["advisories"])
    assert not any("2027-03-10" in a for a in g["advisories"])


def test_impossible_dates_flagged(db):
    _clear_cache(db)
    kimi_primary.set_provider(single_pass(GOOD_ANSWER))
    g = kimi_primary.get_route_guidance(
        db, dict(ROUTE, arrival_date="2026-09-20", departure_date="2026-09-10"))
    assert any("not after arrival" in a for a in g["advisories"])


# ---- security ----------------------------------------------------------------
def test_prompt_contains_route_facts_only_and_no_secret_access():
    p = kimi_primary.build_prompt(dict(ROUTE, password="SHOULD-NEVER-APPEAR",
                                       otp="123456", cookie="session=abc",
                                       full_name="Jane Doe",
                                       passport_number="X1234567"))
    low = p.lower()
    for leak in ("password", "should-never-appear", "otp", "123456", "cookie",
                 "session=", "jane", "x1234567"):
        assert leak not in low
    # The module never imports the vault, payments, or browser-session layers —
    # structurally incapable of reaching a secret.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(kimi_primary))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported |= {a.name for a in node.names}
    for forbidden in ("vault", "payments", "browser", "esign", "docusign"):
        assert not any(forbidden in str(m).lower() for m in imported), imported


def test_kimi_tool_registry_still_blocks_secrets():
    from app.providers.kimi import PROHIBITED_FOR_MODEL, validate_tool_call, ToolSecurityError
    assert "reveal_secret" in PROHIBITED_FOR_MODEL and "pay_fee" in PROHIBITED_FOR_MODEL
    with pytest.raises(ToolSecurityError):
        validate_tool_call("classify_document", {"doc_excerpt": "the password is x"})


# ---- honest unavailability (no mocks in real modes) --------------------------
def test_no_provider_is_honest_unavailable(db):
    _clear_cache(db)
    kimi_primary.set_provider(None)     # conftest wipes MOONSHOT_API_KEY
    assert kimi_primary.is_available() is False
    with pytest.raises(kimi_primary.GuidanceUnavailable):
        kimi_primary.get_route_guidance(db, ROUTE)


def test_provider_http_errors_map_to_precise_messages(db):
    _clear_cache(db)
    from app import provider_errors

    def failing(system, user):
        raise kimi_primary.GuidanceProviderError(
            provider_errors.user_error("kimi moonshot HTTP 402"))
    kimi_primary.set_provider(failing)
    with pytest.raises(kimi_primary.GuidanceProviderError) as exc:
        kimi_primary.get_route_guidance(db, ROUTE)
    env = exc.value.envelope
    assert env["category"] == "kimi_quota_exhausted"
    assert "Kimi K3 credits or quota are unavailable" in env["user_message"]
    assert "Moonshot" in env["user_message"]


def test_error_catalog_messages_are_exact():
    from app import provider_errors
    assert provider_errors.classify_error("kimi moonshot HTTP 401") == "kimi_auth_failed"
    assert provider_errors.classify_error("kimi moonshot HTTP 402") == "kimi_quota_exhausted"
    assert provider_errors.classify_error("kimi moonshot HTTP 429") == "kimi_rate_limited"
    assert provider_errors.classify_error("kimi moonshot HTTP 503 unavailable") == "kimi_unavailable"
    assert "Browserbase credits are unavailable" in \
        provider_errors.CATALOG["browserbase_quota_exhausted"]["user_message"]
    assert "Google Document AI quota is unavailable" in \
        provider_errors.CATALOG["documentai_quota_exhausted"]["user_message"]


# ---- API flow ----------------------------------------------------------------
def test_guidance_endpoint_drives_intake_flow(client, db):
    _clear_cache(db)
    kimi_primary.set_provider(single_pass(GOOD_ANSWER))
    r = client.post("/intake", headers=H, json={"answers": ROUTE})
    iid = r.json()["id"]
    rr = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr.status_code == 200
    # New route: resolve reports guidance pending (provider injected => available).
    assert rr.json().get("kimi_guidance_pending") is True
    # Resolve NEVER auto-starts official-source research.
    assert "research_job" not in rr.json()
    g = client.post(f"/intake/{iid}/guidance", headers=H)
    assert g.status_code == 200
    body = g.json()
    assert body["status"] == "KIMI_PRIMARY" and body["ai_generated"] is True
    assert body["label"] == "Kimi route decision"
    assert body["verification"] == {"passes": 1, "label": "Kimi route decision"}
    assert body["intake_id"] == iid
    # Second resolve now attaches the cached guidance instantly.
    rr2 = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr2.json().get("kimi_guidance", {}).get("cached") is True


def test_timeout_maps_to_504_with_retry_message(client, db, monkeypatch):
    _clear_cache(db)
    kimi_primary.set_provider(single_pass(GOOD_ANSWER))
    monkeypatch.setenv("ELLIS_GUIDANCE_DEADLINE_SECONDS", "1")
    r = client.post("/intake", headers=H,
                    json={"answers": dict(ROUTE, destination_country="KOR")})
    iid = r.json()["id"]
    client.post(f"/intake/{iid}/resolve", headers=H)
    g = client.post(f"/intake/{iid}/guidance", headers=H)
    assert g.status_code == 504
    assert g.json()["detail"]["reason"] == kimi_primary.TIMEOUT_MESSAGE


# --- cache-key separation (Trip.com Database: transit + document type) -------

def test_transit_gets_its_own_cache_key_but_plain_routes_are_unchanged():
    """A stopover can add a transit-visa requirement, so it MUST change the
    key. If it did not, a transit query would be served the cached
    non-transit answer and the transit question would never be asked.
    Plain routes must keep their existing key so the shipped warm cache
    stays valid."""
    from app.visa_snapshot.kimi_primary import cache_key
    base = {"passport_nationality": "CHN",
            "lawful_country_of_residence": "CHN",
            "destination_country": "JPN", "travel_purpose": "tourism"}
    assert cache_key({**base, "transit_countries": ["SGP"]}) != cache_key(base)
    # Order and duplicates must not produce a different key.
    assert (cache_key({**base, "transit_countries": ["SGP", "THA"]})
            == cache_key({**base, "transit_countries": ["THA", "SGP", "SGP"]}))
    # An empty transit list is the plain route.
    assert cache_key({**base, "transit_countries": []}) == cache_key(base)


def test_non_ordinary_travel_document_gets_its_own_cache_key():
    """The answer page lets a reader switch to a diplomatic/official
    passport. Those are genuinely different answers, so they must not be
    served the ordinary-passport cache entry."""
    from app.visa_snapshot.kimi_primary import cache_key
    base = {"passport_nationality": "CHN",
            "lawful_country_of_residence": "CHN",
            "destination_country": "JPN", "travel_purpose": "tourism"}
    assert (cache_key({**base, "travel_document_type": "ordinary_passport"})
            == cache_key(base))
    for doc in ("diplomatic_passport", "service_passport", "laissez_passer"):
        assert cache_key({**base, "travel_document_type": doc}) != cache_key(base)


def test_requirement_detail_vocabulary_matches_the_field_spec():
    """Trip.com's field spec fixes the subcategory vocabulary; the UI maps
    each one to a label, so the two lists must not drift apart."""
    from app.visa_snapshot.kimi_primary import REQUIREMENT_DETAILS
    assert set(REQUIREMENT_DETAILS) == {
        "unconditional_visa_free", "conditional_visa_free", "transit_visa_free",
        "evisa_on_arrival", "paper_visa_on_arrival",
        "evisa", "paper_visa", "eta_electronic_authorization"}


def test_new_answer_fields_survive_validation():
    """visa_products / channel detail / source / subcategory / transit are
    whitelisted, so a model answer carrying them is not silently stripped."""
    from app.visa_snapshot.kimi_primary import ALL_FIELDS
    for f in ("visa_products", "application_channel_detail", "source_url",
              "requirement_detail", "transit_requirement"):
        assert f in ALL_FIELDS, f


# --- information-quality gate (Trip.com requirement 4) -----------------------

def test_low_confidence_answers_are_held_until_a_person_releases_them():
    """Their requirement: low-confidence content is blocked until an operator
    confirms it. The engine's OWN doubt is the trigger, and holding means the
    reader sees nothing — showing the claims under a warning is still
    showing them."""
    from app.visa_snapshot.kimi_primary import _result
    held = _result("KIMI_PRIMARY", {"confidence": "low"},
                   cached=False, stale=False)
    assert held["review_required"] is True
    assert held["operator_released"] is False
    for ok in ("high", "medium"):
        assert _result("KIMI_PRIMARY", {"confidence": ok},
                       cached=False, stale=False)["review_required"] is False


def test_an_operator_release_lifts_the_hold_for_that_answer_only():
    from app.visa_snapshot.kimi_primary import _result
    released = _result("KIMI_PRIMARY", {"confidence": "low"},
                       cached=True, stale=False, released=True)
    assert released["review_required"] is False
    assert released["operator_released"] is True
    # An answer with no guidance at all is not "held" — it is simply absent,
    # and the unavailable/timeout messaging covers it.
    assert _result("KIMI_UNAVAILABLE", {}, cached=False,
                   stale=False)["review_required"] is False


# --- verified overrides (official-source corrections) ------------------------

def test_an_override_must_cite_an_official_government_source(tmp_path, monkeypatch):
    """An unsourced correction is just a different guess, so it is dropped.
    Only a government domain can outrank the model."""
    import json as _json
    from app.visa_snapshot import verified_overrides as vo
    good = [{"route": {"nationality": "CHN", "destination": "SGP"},
             "verified_at": "2026-08-22", "source_url": "https://www.ica.gov.sg/x",
             "fields": {"disposition": "VISA_EXEMPT"}}]
    bad = [
        # a blog is not a source of law
        {"route": {"nationality": "CHN", "destination": "THA"},
         "verified_at": "2026-08-22", "source_url": "https://someblog.example.com/x",
         "fields": {"disposition": "VISA_EXEMPT"}},
        # no date
        {"route": {"nationality": "CHN", "destination": "MYS"},
         "source_url": "https://www.imi.gov.my/x",
         "fields": {"disposition": "VISA_EXEMPT"}},
        # no fields
        {"route": {"nationality": "CHN", "destination": "VNM"},
         "verified_at": "2026-08-22", "source_url": "https://www.gov.vn/x",
         "fields": {}},
    ]
    f = tmp_path / "verified_overrides.json"
    f.write_text(_json.dumps(good + bad))
    monkeypatch.setattr(vo, "OVERRIDES", f)
    vo.reload()
    table = vo._table()
    assert set(table) == {"CHN|SGP|tourism"}
    vo.reload()


def test_an_override_replaces_only_the_fields_it_names(tmp_path, monkeypatch):
    import json as _json
    from app.visa_snapshot import verified_overrides as vo
    f = tmp_path / "verified_overrides.json"
    f.write_text(_json.dumps([{
        "route": {"nationality": "CHN", "destination": "SGP"},
        "verified_at": "2026-08-22", "verified_by": "audit",
        "source_url": "https://www.ica.gov.sg/x",
        "fields": {"disposition": "VISA_EXEMPT", "permitted_stay": "30 days"}}]))
    monkeypatch.setattr(vo, "OVERRIDES", f)
    vo.reload()
    guidance = {"disposition": "VISA_REQUIRED", "permitted_stay": "wrong",
                "processing_time": "5 days", "required_documents": ["passport"]}
    route = {"passport_nationality": "CHN", "destination_country": "SGP",
             "travel_purpose": "tourism"}
    merged, prov = vo.apply(guidance, route)
    assert merged["disposition"] == "VISA_EXEMPT"
    assert merged["permitted_stay"] == "30 days"
    # untouched fields stay the model's
    assert merged["processing_time"] == "5 days"
    assert merged["required_documents"] == ["passport"]
    # and the answer can say exactly what was checked
    assert prov["fields"] == ["disposition", "permitted_stay"]
    assert prov["source_url"] == "https://www.ica.gov.sg/x"
    # the original is never mutated
    assert guidance["disposition"] == "VISA_REQUIRED"
    vo.reload()


def test_a_route_with_no_override_is_untouched_and_unmarked():
    from app.visa_snapshot import verified_overrides as vo
    vo.reload()
    guidance = {"disposition": "VISA_REQUIRED"}
    merged, prov = vo.apply(guidance, {"passport_nationality": "CHN",
                                       "destination_country": "JPN",
                                       "travel_purpose": "tourism"})
    assert merged is guidance and prov is None


def test_verifying_a_disposition_lifts_the_low_confidence_hold(tmp_path, monkeypatch):
    """The hold exists because the engine was unsure. Once a person has
    checked the disposition against an official page, that doubt is settled."""
    import json as _json
    from app.visa_snapshot import verified_overrides as vo
    f = tmp_path / "verified_overrides.json"
    f.write_text(_json.dumps([{
        "route": {"nationality": "CHN", "destination": "TWN"},
        "verified_at": "2026-08-22", "source_url": "https://www.immigration.gov.tw/x",
        "fields": {"disposition": "CONDITIONAL"}}]))
    monkeypatch.setattr(vo, "OVERRIDES", f)
    vo.reload()
    held = kimi_primary._result("KIMI_PRIMARY", {"confidence": "low",
                                                 "disposition": "VISA_REQUIRED"},
                                cached=True, stale=False)
    assert held["review_required"] is True
    out = kimi_primary.apply_verified_overrides(
        held, {"passport_nationality": "CHN", "destination_country": "TWN",
               "travel_purpose": "tourism"})
    assert out["review_required"] is False
    assert out["guidance"]["disposition"] == "CONDITIONAL"
    assert out["source_verified"]["verified_at"] == "2026-08-22"
    vo.reload()
