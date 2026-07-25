"""Kimi two-pass route guidance: primary analysis + independent verification
under one hard deadline.

Proves: two Kimi passes produce the final authoritative result (ACCEPT keeps
pass 1, REVISE applies the verifier's corrected JSON); normal route resolution
performs no official-source research; cached identical routes load instantly
(and cache only complete results); malformed answers retry exactly once; the
total deadline is enforced with the honest retry message (never a spinner);
provider failures map to precise messages; Kimi gets no secrets; irreversible
actions keep applicant confirmation."""
import pytest
from sqlalchemy import select

from app.db import SessionLocal, create_all
from app.visa_snapshot import kimi_primary
from app.visa_snapshot.models import (HumanReviewTask, KimiRouteGuidanceCache,
                                      OnDemandRouteResearchJob)

H = {"Authorization": "Bearer dev-token", "X-Org-Id": "org-kp", "X-User-Id": "u1"}

ROUTE = {"passport_nationality": "USA", "passport_issuing_country": "USA",
         "travel_document_type": "ordinary_passport",
         "lawful_country_of_residence": "USA", "destination_country": "JPN",
         "visa_category": "tourist_visa", "travel_purpose": "tourism",
         "arrival_date": "2026-09-10", "departure_date": "2026-09-20",
         "age": 30, "email": "kp@example.com", "preferred_language": "en"}

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


def is_verify_call(system: str) -> bool:
    return "verifier" in system


def two_pass(answer, *, verdict="ACCEPT", issues=None, corrected=None, counter=None):
    """A provider serving both passes: the analysis answer for pass 1, the
    verification verdict for pass 2. `counter` tallies calls per pass."""
    def provider(system, user):
        if counter is not None:
            counter["verify" if is_verify_call(system) else "analyze"] = \
                counter.get("verify" if is_verify_call(system) else "analyze", 0) + 1
        if is_verify_call(system):
            return {"verdict": verdict, "issues": issues or [], "corrected": corrected}
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


# ---- two passes produce the final authoritative result -----------------------
def test_two_passes_produce_final_result_without_research(db):
    _clear_cache(db)
    counter = {}
    kimi_primary.set_provider(two_pass(GOOD_ANSWER, counter=counter))
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_PRIMARY"
    assert g["ai_generated"] is True
    assert counter == {"analyze": 1, "verify": 1}          # exactly two passes
    assert g["verification"]["verdict"] == "ACCEPT"
    assert g["verification"]["passes"] == 2
    # The verified label replaces every official-source claim.
    assert g["label"] == kimi_primary.VERIFIED_LABEL
    assert "official sources" not in g["label"].lower()
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


def test_revise_verdict_applies_the_corrected_json(db):
    _clear_cache(db)
    wrong = dict(GOOD_ANSWER, disposition="VISA_REQUIRED",
                 route_workflow_type="embassy_submission")
    corrected = dict(GOOD_ANSWER)   # the verifier corrects back to exempt
    kimi_primary.set_provider(two_pass(wrong, verdict="REVISE",
                                       issues=["USA passports are visa-exempt for JPN tourism"],
                                       corrected=corrected))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_PRIMARY"
    assert g["verification"]["verdict"] == "REVISE"
    assert g["verification"]["applied"] is True
    assert g["guidance"]["disposition"] == "VISA_EXEMPT"   # the FINAL Kimi result


def test_visa_required_plan_keeps_irreversible_confirmations(db):
    _clear_cache(db)
    ans = dict(GOOD_ANSWER, disposition="VISA_REQUIRED",
               visa_category="Tourist L visa", application_channel="embassy",
               government_fee={"amount": 140, "currency": "USD"},
               official_portal_url="https://cova.mfa.gov.cn/", forms=["V.2013"],
               appointment_required=True, route_workflow_type="embassy_submission",
               arrival_card=None)
    kimi_primary.set_provider(two_pass(ans))
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
    kimi_primary.set_provider(two_pass(ans))
    g = kimi_primary.get_route_guidance(db, dict(ROUTE, destination_country="VNM"))
    steps = [s["step"] for s in g["workflow_plan"]]
    assert "appointment_search" not in steps and "appointment_booking" not in steps


# ---- cache -------------------------------------------------------------------
def test_cached_identical_route_loads_immediately(db):
    _clear_cache(db)
    counter = {}
    kimi_primary.set_provider(two_pass(GOOD_ANSWER, counter=counter))
    g1 = kimi_primary.get_route_guidance(db, ROUTE)
    g2 = kimi_primary.get_route_guidance(db, ROUTE)
    assert counter == {"analyze": 1, "verify": 1}   # second hit answered from cache
    assert g1["cached"] is False and g2["cached"] is True
    # The cached row keeps its two-pass verification.
    assert g2["verification"]["verdict"] == "ACCEPT"
    assert g2["label"] == kimi_primary.VERIFIED_LABEL
    # Same policy month + same route dimensions => same cache key.
    assert kimi_primary.cache_key(ROUTE) == kimi_primary.cache_key(
        dict(ROUTE, arrival_date="2026-09-25"))
    assert kimi_primary.cache_key(ROUTE) != kimi_primary.cache_key(
        dict(ROUTE, destination_country="KOR"))
    # The two-pass schema version is part of the key (old rows never reused).
    assert kimi_primary.CACHE_VERSION in kimi_primary.cache_key(ROUTE)


def test_stale_cache_returns_instantly_flagged_for_refresh(db):
    _clear_cache(db)
    kimi_primary.set_provider(two_pass(GOOD_ANSWER))
    kimi_primary.get_route_guidance(db, ROUTE)
    row = db.execute(select(KimiRouteGuidanceCache)).scalars().first()
    from datetime import datetime, timedelta, timezone
    row.fresh_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    counter = {}
    kimi_primary.set_provider(two_pass(GOOD_ANSWER, counter=counter))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["cached"] is True and g["stale"] is True   # instant, non-blocking
    assert counter == {}                                # refresh is async, not inline


def test_uncertain_results_are_never_cached(db):
    _clear_cache(db)
    bad = dict(GOOD_ANSWER)
    bad.pop("permitted_stay")
    kimi_primary.set_provider(two_pass(bad))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert db.query(KimiRouteGuidanceCache).count() == 0   # retry is always possible


# ---- deadline ----------------------------------------------------------------
def test_deadline_exceeded_is_honest_retryable_error(db, monkeypatch):
    _clear_cache(db)
    kimi_primary.set_provider(two_pass(GOOD_ANSWER))
    # A deadline below the minimum call budget trips before any Kimi call.
    monkeypatch.setenv("ELLIS_GUIDANCE_DEADLINE_SECONDS", "1")
    with pytest.raises(kimi_primary.GuidanceTimeout) as exc:
        kimi_primary.get_route_guidance(db, ROUTE)
    assert str(exc.value) == kimi_primary.TIMEOUT_MESSAGE
    assert "one minute" in str(exc.value)
    assert db.query(KimiRouteGuidanceCache).count() == 0   # never cached


def test_slow_pass_one_exhausts_budget_before_verification(db, monkeypatch):
    _clear_cache(db)
    import time as _time

    def slow(system, user):
        assert not is_verify_call(system), "verification must not start after the deadline"
        _time.sleep(0.3)
        return dict(GOOD_ANSWER)
    kimi_primary.set_provider(slow)
    monkeypatch.setenv("ELLIS_GUIDANCE_DEADLINE_SECONDS", "5.2")  # 5.2-0.3 < 5 min budget
    with pytest.raises(kimi_primary.GuidanceTimeout):
        kimi_primary.get_route_guidance(db, ROUTE)


def test_default_deadline_is_sixty_seconds():
    assert kimi_primary.DEFAULT_DEADLINE_SECONDS == 60
    assert kimi_primary._deadline_seconds() == 60


# ---- honest failure ----------------------------------------------------------
def test_missing_fields_retry_once_then_honest_uncertain(db):
    _clear_cache(db)
    counter = {}
    bad = dict(GOOD_ANSWER)
    bad.pop("permitted_stay"); bad.pop("processing_time")

    def flaky(system, user):
        if is_verify_call(system):
            counter["verify"] = counter.get("verify", 0) + 1
            return {"verdict": "ACCEPT", "issues": [], "corrected": None}
        counter["analyze"] = counter.get("analyze", 0) + 1
        if counter["analyze"] > 1:
            assert "permitted_stay" in user and "processing_time" in user  # targeted retry
        return dict(bad)
    kimi_primary.set_provider(flaky)
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    before_tasks = db.query(HumanReviewTask).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert counter["analyze"] == 2                        # exactly one retry
    assert g["status"] == "KIMI_UNCERTAIN"
    assert set(g["missing_fields"]) == {"permitted_stay", "processing_time"}
    # No broad research auto-started; no administrator task created.
    assert db.query(OnDemandRouteResearchJob).count() == before_jobs
    assert db.query(HumanReviewTask).count() == before_tasks


def test_contradictory_answer_flagged_precisely(db):
    _clear_cache(db)
    bad = dict(GOOD_ANSWER, disposition="VISA_EXEMPT",
               forms=["Visa application form DS-123"])
    kimi_primary.set_provider(two_pass(bad))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert any("visa application" in c for c in g["contradictions"])


def test_malformed_disposition_rejected(db):
    _clear_cache(db)
    kimi_primary.set_provider(two_pass(dict(GOOD_ANSWER, disposition="MAYBE")))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN" and "disposition" in g["missing_fields"]


# ---- deterministic arithmetic (never the model) ------------------------------
def test_trip_duration_vs_permitted_stay_advisory(db):
    _clear_cache(db)
    short = dict(GOOD_ANSWER, permitted_stay="30 days", permitted_stay_days=30)
    kimi_primary.set_provider(two_pass(short))
    long_trip = dict(ROUTE, arrival_date="2026-09-01", departure_date="2026-11-15")
    g = kimi_primary.get_route_guidance(db, long_trip)
    assert any("exceeds the permitted stay" in a for a in g["advisories"])


def test_passport_expiry_advisory_is_calculated(db):
    _clear_cache(db)
    req = dict(GOOD_ANSWER,
               passport_validity_requirement={"kind": "months_after_arrival", "months": 6})
    kimi_primary.set_provider(two_pass(req))
    g = kimi_primary.get_route_guidance(
        db, dict(ROUTE, passport_expiry_date="2026-10-01"))
    assert any("must be" in a and "2027-03-10" in a for a in g["advisories"])


def test_impossible_dates_flagged(db):
    _clear_cache(db)
    kimi_primary.set_provider(two_pass(GOOD_ANSWER))
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
    kimi_primary.set_provider(two_pass(GOOD_ANSWER))
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
    assert body["verification"]["verdict"] == "ACCEPT"
    assert body["intake_id"] == iid
    # Second resolve now attaches the cached guidance instantly.
    rr2 = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr2.json().get("kimi_guidance", {}).get("cached") is True


def test_timeout_maps_to_504_with_retry_message(client, db, monkeypatch):
    _clear_cache(db)
    kimi_primary.set_provider(two_pass(GOOD_ANSWER))
    monkeypatch.setenv("ELLIS_GUIDANCE_DEADLINE_SECONDS", "1")
    r = client.post("/intake", headers=H,
                    json={"answers": dict(ROUTE, destination_country="KOR")})
    iid = r.json()["id"]
    client.post(f"/intake/{iid}/resolve", headers=H)
    g = client.post(f"/intake/{iid}/guidance", headers=H)
    assert g.status_code == 504
    assert g.json()["detail"]["reason"] == kimi_primary.TIMEOUT_MESSAGE
