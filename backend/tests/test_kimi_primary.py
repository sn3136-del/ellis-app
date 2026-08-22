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


def test_uncertain_results_are_cached_briefly_and_replaced_when_complete(db):
    """The owner's rule: every route is answered, fast. An incomplete answer
    is still an answer — it is served, and cached BRIEFLY so a repeat reader
    is instant — while the background refresh keeps trying; a later complete
    answer replaces it for the full window."""
    _clear_cache(db)
    bad = dict(GOOD_ANSWER)
    bad.pop("permitted_stay")
    kimi_primary.set_provider(single_pass(bad))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert g["verification"] == {}                     # no decision, no claim
    assert g["label"] == "AI-generated route guidance"
    assert g["guidance"]["disposition"] == GOOD_ANSWER["disposition"]   # still an answer
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.status == "KIMI_UNCERTAIN"
    short = (row.fresh_until - row.generated_at).days
    assert short <= kimi_primary.UNCERTAIN_TTL_DAYS
    # A complete answer replaces it for the full window.
    kimi_primary.set_provider(single_pass(GOOD_ANSWER))
    g2 = kimi_primary.get_route_guidance(db, ROUTE, force_refresh=True)
    assert g2["status"] == "KIMI_PRIMARY"
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.status == "KIMI_PRIMARY"
    assert (row.fresh_until - row.generated_at).days > short


def test_an_empty_answer_is_never_cached(db):
    """validate_answer fills defaults even for {}; those defaults alone must
    never be written over a real cached answer."""
    _clear_cache(db)
    kimi_primary.set_provider(single_pass({}))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert db.query(KimiRouteGuidanceCache).count() == 0


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
    """An answer with NO verdict at all earns one retry — but never past the
    deadline. (An answer that merely has gaps is served at once, see above.)"""
    _clear_cache(db)
    import time as _time
    calls = {"n": 0}

    def slow(system, user):
        calls["n"] += 1
        assert calls["n"] == 1, "the malformed retry must not start after the deadline"
        _time.sleep(0.3)
        return {"confidence": "low"}            # no disposition: nothing to show
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
def test_an_incomplete_answer_is_served_at_once_without_a_slow_retry(db):
    """The owner's rule: fast and always answered. An answer WITH a verdict
    but gaps is served immediately as KIMI_UNCERTAIN (one call, no retry);
    the background refresh keeps trying for a complete one. The slow full
    retry used to turn a 30-second answer into a timeout."""
    _clear_cache(db)
    counter = {}
    bad = dict(GOOD_ANSWER)
    bad.pop("permitted_stay"); bad.pop("processing_time")

    def provider(system, user):
        assert "verifier" not in system
        counter["analyze"] = counter.get("analyze", 0) + 1
        return dict(bad)
    kimi_primary.set_provider(provider)
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    before_tasks = db.query(HumanReviewTask).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert counter == {"analyze": 1}                      # served at once
    assert g["status"] == "KIMI_UNCERTAIN"
    assert g["guidance"]["disposition"] == GOOD_ANSWER["disposition"]
    assert set(g["missing_fields"]) == {"permitted_stay", "processing_time"}
    # No broad research auto-started; no administrator task created.
    assert db.query(OnDemandRouteResearchJob).count() == before_jobs
    assert db.query(HumanReviewTask).count() == before_tasks


def test_no_verdict_at_all_gets_exactly_one_retry(db):
    """Only when there is nothing to show (no disposition) is a retry worth
    its time — and then exactly one."""
    _clear_cache(db)
    counter = {}

    def provider(system, user):
        counter["analyze"] = counter.get("analyze", 0) + 1
        return {"confidence": "low"} if counter["analyze"] == 1 else dict(GOOD_ANSWER)
    kimi_primary.set_provider(provider)
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert counter == {"analyze": 2}
    assert g["status"] == "KIMI_PRIMARY"


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
                "photo_requirements": "35mm", "required_documents": ["passport"]}
    route = {"passport_nationality": "CHN", "destination_country": "SGP",
             "travel_purpose": "tourism"}
    merged, prov = vo.apply(guidance, route)
    assert merged["disposition"] == "VISA_EXEMPT"
    assert merged["permitted_stay"] == "30 days"
    # Untouched fields stay the model's. (processing_time is NOT checked here:
    # this override declares the route visa-free, and a visa-free verdict
    # deliberately clears application-only leftovers — see
    # test_a_verified_visa_free_verdict_clears_application_leftovers.)
    assert merged["required_documents"] == ["passport"]
    assert merged["photo_requirements"] == "35mm"
    # and the answer can say exactly what was checked
    assert prov["fields"] == ["disposition", "permitted_stay"]
    assert prov["source_url"] == "https://www.ica.gov.sg/x"
    # the original is never mutated
    assert guidance["disposition"] == "VISA_REQUIRED"
    vo.reload()


def test_a_route_with_no_override_is_untouched_and_unmarked():
    """A verified answer must never lend its badge to an unverified one, so a
    route with no override comes back byte-identical and unmarked. Uses a
    route deliberately outside the shipped override set."""
    from app.visa_snapshot import verified_overrides as vo
    vo.reload()
    route = {"passport_nationality": "CHN", "destination_country": "ISL",
             "travel_purpose": "tourism"}
    assert vo.find(route) is None, "pick a route with no shipped override"
    guidance = {"disposition": "VISA_REQUIRED"}
    merged, prov = vo.apply(guidance, route)
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


def test_a_verified_visa_free_verdict_clears_application_leftovers(tmp_path, monkeypatch):
    """"No visa needed" and "processing time: 3 working days" cannot both be
    true on the same page. When a verified fact says no visa is needed, the
    model's leftovers about applying for one are dropped rather than left to
    contradict the verdict."""
    import json as _json
    from app.visa_snapshot import verified_overrides as vo
    f = tmp_path / "verified_overrides.json"
    f.write_text(_json.dumps([{
        "route": {"nationality": "CHN", "destination": "SGP"},
        "verified_at": "2026-08-22", "source_url": "https://www.ica.gov.sg/x",
        "fields": {"disposition": "VISA_EXEMPT",
                   "government_fee": {"amount": 0, "currency": None}}}]))
    monkeypatch.setattr(vo, "OVERRIDES", f)
    vo.reload()
    merged, _ = vo.apply(
        {"disposition": "VISA_REQUIRED", "processing_time": "About 3 working days",
         "forms": ["Form 14A"], "submission_process": ["Lodge at the centre"],
         "interview_required": True, "required_documents": ["passport"]},
        {"passport_nationality": "CHN", "destination_country": "SGP",
         "travel_purpose": "tourism"})
    for gone in ("processing_time", "forms", "submission_process"):
        assert gone not in merged, gone
    assert merged["interview_required"] is False
    # the override's OWN value still wins, even for an application-only field
    assert merged["government_fee"] == {"amount": 0, "currency": None}
    # and what a visa-free traveller still needs is untouched
    assert merged["required_documents"] == ["passport"]
    vo.reload()


def test_shipped_overrides_are_internally_consistent():
    """A guard for the class of mistake that shipped an eTA at the CAD 100
    visitor-visa price: when an override states BOTH a headline fee and the
    products' own fees, the headline must be one of the product prices —
    otherwise the tile and the table contradict each other on the same page.
    Also checks every shipped override still meets the module's own rules."""
    import json
    import pathlib
    from app.visa_snapshot import verified_overrides as vo
    from app.visa_snapshot.authority import hostname, is_government_host
    rows = json.loads(pathlib.Path(vo.OVERRIDES).read_text())
    assert rows, "the shipped overrides must not be empty"
    for r in rows:
        route = f"{r['route']['nationality']}->{r['route']['destination']}"
        assert is_government_host(hostname(r["source_url"])), route
        assert r.get("verified_at"), route
        f = r["fields"]
        head = (f.get("government_fee") or {}).get("amount")
        prices = [(p.get("fee") or {}).get("amount")
                  for p in (f.get("visa_products") or [])]
        prices = [p for p in prices if p is not None]
        if head is not None and prices:
            assert head in prices, (
                f"{route}: headline fee {head} is not any product price "
                f"{prices} — the tile would contradict the table")
        # A verified visa-free verdict must not also quote a positive fee.
        if str(f.get("disposition") or "") == "VISA_EXEMPT" and head:
            raise AssertionError(f"{route}: visa-free but a fee of {head}")


# ---- two-stage answering (the Database's fast path) --------------------------
def _staged_provider(core_answer, detail_answer, counter):
    """Answers the CORE and DETAIL calls differently, like the live model."""
    def provider(system, user):
        counter.setdefault("calls", []).append(
            "core" if "THIS CALL (CORE)" in system else
            "detail" if "THIS CALL (DETAIL)" in system else "full")
        if "THIS CALL (DETAIL)" in system:
            assert "ALREADY DECIDED" in system      # the verdict travels with it
            return dict(detail_answer)
        return dict(core_answer)
    return provider


def test_core_first_serves_the_verdict_then_fills_detail_consistently(db):
    """Stage 1 answers with the core verdict; stage 2 fills the detail told
    the verdict it must respect; the row ends complete and un-pending."""
    _clear_cache(db)
    counter = {}
    core = {k: v for k, v in GOOD_ANSWER.items() if k in kimi_primary.CORE_FIELDS}
    detail = {"visa_products": [{"type": "Tourist", "entry": "single",
                                 "validity": "3 months", "max_stay_days": 30,
                                 "fee": {"amount": 10, "currency": "USD"}}],
              "forms": ["Arrival card"], "exceptions": ["None"]}
    kimi_primary.set_provider(_staged_provider(core, detail, counter))
    g = kimi_primary.get_route_guidance(db, ROUTE, stage="core")
    assert counter["calls"] == ["core", "detail"]
    assert g["detail_pending"] is False               # inline with an injected provider
    row = db.query(KimiRouteGuidanceCache).one()
    assert "detail_pending" not in (row.verification or {})
    assert row.guidance["disposition"] == GOOD_ANSWER["disposition"]
    if GOOD_ANSWER["disposition"] == "VISA_EXEMPT":
        # The verdict wins: a visa-exempt route keeps no visa products.
        assert row.guidance.get("visa_products") == []
    else:
        assert row.guidance["visa_products"][0]["type"] == "Tourist"
    assert row.guidance.get("exceptions") == ["None"]


def test_detail_stage_failure_leaves_the_core_answer_served_and_unpending(db):
    _clear_cache(db)
    core = {k: v for k, v in GOOD_ANSWER.items() if k in kimi_primary.CORE_FIELDS}

    def provider(system, user):
        if "THIS CALL (DETAIL)" in system:
            raise RuntimeError("detail model down")
        return dict(core)
    kimi_primary.set_provider(provider)
    g = kimi_primary.get_route_guidance(db, ROUTE, stage="core")
    assert g["guidance"]["disposition"] == GOOD_ANSWER["disposition"]
    row = db.query(KimiRouteGuidanceCache).one()
    assert "detail_pending" not in (row.verification or {})   # readers stop polling
    assert row.guidance["disposition"] == GOOD_ANSWER["disposition"]


def test_full_stage_is_unchanged_for_the_applicant_journey(db):
    _clear_cache(db)
    counter = {}
    kimi_primary.set_provider(single_pass(GOOD_ANSWER, counter=counter))
    g = kimi_primary.get_route_guidance(db, ROUTE)          # default stage
    assert counter == {"analyze": 1}
    assert g.get("detail_pending") in (None, False)


# ---- the ask box reads plain questions without a model call ------------------
@pytest.mark.parametrize("question, nat, dest, purpose", [
    ("wanna go from france to china", "FRA", "CHN", "tourism"),
    ("What visa do I need for tourism in Japan with a Chinese passport?", "CHN", "JPN", "tourism"),
    ("持中国护照去日本旅游需要什么签证？", "CHN", "JPN", "tourism"),
    ("持中國護照去日本旅遊需要什麼簽證？", "CHN", "JPN", "tourism"),
    ("I'm American, business trip to Vietnam", "USA", "VNM", "business"),
    ("UK passport, studying in South Korea", "GBR", "KOR", "study"),
    ("hong kong to dubai", "HKG", "ARE", "tourism"),
    ("from germany to new zealand for work", "DEU", "NZL", "work"),
])
def test_plain_questions_are_read_deterministically(question, nat, dest, purpose):
    kimi_primary.set_provider(lambda system, user: (_ for _ in ()).throw(
        AssertionError("a plain question must not need the model")))
    r = kimi_primary.parse_question(question)
    assert (r["understood"], r["nationality"], r["destination"], r["travel_purpose"]) \
        == (True, nat, dest, purpose), r


def test_a_question_naming_one_place_falls_back_to_the_model():
    kimi_primary.set_provider(lambda system, user: {
        "nationality": None, "destination": "JPN", "travel_purpose": None,
        "travel_document_type": "ordinary_passport"})
    r = kimi_primary.parse_question("do i need a visa for japan")
    assert r["understood"] is False                 # one place is not a route
