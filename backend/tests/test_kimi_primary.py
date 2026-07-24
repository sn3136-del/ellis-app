"""Kimi-primary route guidance: immediate decision, cache, honest failure.

Proves: guidance immediately drives the applicant flow (no research job needed);
cached identical routes load instantly; malformed/uncertain answers retry once
then fail honestly (no broad research auto-start, no admin task); Kimi gets no
secrets; irreversible actions keep applicant confirmation; no mocks reach real
modes (no provider -> honest 503-style unavailability, never fabricated)."""
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
    "permitted_stay": "90 days", "passport_validity": "valid for the stay",
    "required_documents": ["passport", "onward ticket"],
    "forms": ["ED card"], "application_channel": "not_required",
    "official_portal_url": None,
    "government_fee": {"amount": None, "currency": None},
    "processing_time": "none (exempt)", "biometrics_required": False,
    "interview_required": False, "appointment_required": False,
    "account_registration_steps": [], "payment_process": [],
    "submission_process": [], "exceptions": [], "uncertainty": [],
    "confidence": "high",
}


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


# ---- immediate drive ---------------------------------------------------------
def test_guidance_immediately_drives_flow_without_research(db):
    _clear_cache(db)
    calls = {"n": 0}

    def provider(system, user):
        calls["n"] += 1
        return dict(GOOD_ANSWER)
    kimi_primary.set_provider(provider)
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_PRIMARY"
    assert g["ai_generated"] is True and "AI-generated" in g["label"]
    assert g["guidance"]["disposition"] == "VISA_EXEMPT"
    # The answer immediately yields the next workflow steps (deterministic).
    steps = [s["step"] for s in g["workflow_plan"]]
    assert "collect_documents" in steps and "ocr_and_validate_passport" in steps
    # No broad research job was created or needed by the guidance path.
    assert db.query(OnDemandRouteResearchJob).count() == before_jobs
    assert calls["n"] == 1


def test_visa_required_plan_keeps_irreversible_confirmations(db):
    _clear_cache(db)
    ans = dict(GOOD_ANSWER, disposition="VISA_REQUIRED",
               visa_category="Tourist L visa", application_channel="embassy",
               government_fee={"amount": 140, "currency": "USD"},
               official_portal_url="https://cova.mfa.gov.cn/", forms=["V.2013"],
               appointment_required=True)
    kimi_primary.set_provider(lambda s, u: ans)
    g = kimi_primary.get_route_guidance(db, dict(ROUTE, destination_country="CHN"))
    assert g["status"] == "KIMI_PRIMARY"
    assert g["irreversible_requires_confirmation"] is True
    plan = {s["step"]: s for s in g["workflow_plan"]}
    # Reversible preparation is drivable; irreversible steps require the applicant.
    assert plan["prepare_forms"]["reversible"] is True
    assert plan["generate_route_adapter"]["reversible"] is True
    for irrev in ("account_registration", "appointment_booking", "payment",
                  "final_review_and_signature", "submission"):
        assert plan[irrev]["reversible"] is False
        assert plan[irrev]["requires_applicant_confirmation"] is True


# ---- cache -------------------------------------------------------------------
def test_cached_identical_route_loads_immediately(db):
    _clear_cache(db)
    calls = {"n": 0}

    def provider(system, user):
        calls["n"] += 1
        return dict(GOOD_ANSWER)
    kimi_primary.set_provider(provider)
    g1 = kimi_primary.get_route_guidance(db, ROUTE)
    g2 = kimi_primary.get_route_guidance(db, ROUTE)
    assert calls["n"] == 1                      # second hit answered from cache
    assert g1["cached"] is False and g2["cached"] is True
    # Same policy month + same route dimensions => same cache key.
    assert kimi_primary.cache_key(ROUTE) == kimi_primary.cache_key(
        dict(ROUTE, arrival_date="2026-09-25"))
    # A different destination is a different key.
    assert kimi_primary.cache_key(ROUTE) != kimi_primary.cache_key(
        dict(ROUTE, destination_country="KOR"))


def test_stale_cache_returns_instantly_flagged_for_refresh(db):
    _clear_cache(db)
    kimi_primary.set_provider(lambda s, u: dict(GOOD_ANSWER))
    kimi_primary.get_route_guidance(db, ROUTE)
    row = db.execute(select(KimiRouteGuidanceCache)).scalars().first()
    from datetime import datetime, timedelta, timezone
    row.fresh_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    calls = {"n": 0}
    kimi_primary.set_provider(lambda s, u: calls.__setitem__("n", calls["n"] + 1) or dict(GOOD_ANSWER))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["cached"] is True and g["stale"] is True   # instant, non-blocking
    assert calls["n"] == 0                              # refresh is async, not inline


# ---- honest failure ----------------------------------------------------------
def test_missing_fields_retry_once_then_honest_uncertain(db):
    _clear_cache(db)
    calls = {"n": 0}

    def flaky(system, user):
        calls["n"] += 1
        bad = dict(GOOD_ANSWER)
        bad.pop("permitted_stay"); bad.pop("processing_time")
        if calls["n"] == 1:
            return bad
        assert "permitted_stay" in user and "processing_time" in user  # targeted retry
        return bad                                        # still incomplete
    kimi_primary.set_provider(flaky)
    before_jobs = db.query(OnDemandRouteResearchJob).count()
    before_tasks = db.query(HumanReviewTask).count()
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert calls["n"] == 2                                # exactly one retry
    assert g["status"] == "KIMI_UNCERTAIN"
    assert set(g["missing_fields"]) == {"permitted_stay", "processing_time"}
    # No broad research auto-started; no administrator task created.
    assert db.query(OnDemandRouteResearchJob).count() == before_jobs
    assert db.query(HumanReviewTask).count() == before_tasks


def test_contradictory_answer_flagged_precisely(db):
    _clear_cache(db)
    bad = dict(GOOD_ANSWER, disposition="VISA_EXEMPT",
               forms=["Visa application form DS-123"])
    kimi_primary.set_provider(lambda s, u: bad)
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN"
    assert any("visa application" in c for c in g["contradictions"])


def test_malformed_disposition_rejected(db):
    _clear_cache(db)
    kimi_primary.set_provider(lambda s, u: dict(GOOD_ANSWER, disposition="MAYBE"))
    g = kimi_primary.get_route_guidance(db, ROUTE)
    assert g["status"] == "KIMI_UNCERTAIN" and "disposition" in g["missing_fields"]


# ---- security ----------------------------------------------------------------
def test_prompt_contains_route_facts_only_and_no_secret_access():
    p = kimi_primary.build_prompt(dict(ROUTE, password="SHOULD-NEVER-APPEAR",
                                       otp="123456", cookie="session=abc"))
    low = p.lower()
    for leak in ("password", "should-never-appear", "otp", "123456", "cookie", "session="):
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


# ---- API flow ----------------------------------------------------------------
def test_guidance_endpoint_drives_intake_flow(client, db):
    _clear_cache(db)
    kimi_primary.set_provider(lambda s, u: dict(GOOD_ANSWER))
    r = client.post("/intake", headers=H, json={"answers": ROUTE})
    iid = r.json()["id"]
    rr = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr.status_code == 200
    # New route: resolve reports guidance pending (provider injected => available).
    assert rr.json().get("kimi_guidance_pending") is True
    g = client.post(f"/intake/{iid}/guidance", headers=H)
    assert g.status_code == 200
    body = g.json()
    assert body["status"] == "KIMI_PRIMARY" and body["ai_generated"] is True
    assert body["intake_id"] == iid
    # Second resolve now attaches the cached guidance instantly.
    rr2 = client.post(f"/intake/{iid}/resolve", headers=H)
    assert rr2.json().get("kimi_guidance", {}).get("cached") is True
