"""The Database endpoints' contract, pinned.

The screen depends on these shapes and on three promises the code makes:
an answer names the cached row it came from; a held (low-confidence) answer's
claims never leave the server; and the operator loop (report -> queue ->
corrected) really changes what the next reader is served.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.visa_snapshot import kimi_primary

READER = {"authorization": "Bearer dev-token", "x-org-id": "org-a",
          "x-user-id": "reader-1"}
OTHER_ORG_ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "org-b",
                   "x-user-id": "operator-1"}

ANSWER = {
    "disposition": "VISA_REQUIRED", "visa_category": "Tourist visa",
    "permitted_stay": "30 days", "passport_validity": "6 months",
    "required_documents": ["passport"], "application_channel": "embassy",
    "government_fee": {"amount": 100, "currency": "USD"},
    "processing_time": "5 days", "confidence": "high",
    "visa_products": [{"type": "Single-entry tourist", "entry": "single",
                       "validity": "3 months", "max_stay_days": 30,
                       "fee": {"amount": 100, "currency": "USD"},
                       "notes": None}],
}


@pytest.fixture()
def client():
    # A PLAIN client, exactly like conftest's: entering TestClient as a context
    # manager runs the app's startup AND shutdown, and that shutdown leaves the
    # portal queue stopped for every test file that runs afterwards.
    c = TestClient(app)
    yield c
    kimi_primary.set_provider(None)


def _provide(answer):
    kimi_primary.set_provider(lambda system, user: dict(answer))


def test_lookup_returns_the_answer_and_its_cache_identity(client):
    _provide(ANSWER)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "KHM"})
    assert r.status_code == 200
    body = r.json()
    assert body["guidance"]["disposition"] == "VISA_REQUIRED"
    assert body["guidance"]["visa_products"][0]["max_stay_days"] == 30
    # The identity the report/release loop binds to.
    assert body["cache_key"].startswith("CHN|CHN|KHM|tourism|")


def test_a_held_answer_ships_no_claims(client, monkeypatch):
    """With the hold switched ON, a low-confidence answer's claims never leave
    the server. The switch is OFF by default (Ellis always answers); this
    pins the behaviour for a deployment that turns it on."""
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    _provide(dict(ANSWER, confidence="low"))
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "BTN"})
    assert r.status_code == 200
    body = r.json()
    assert body["review_required"] is True
    assert body["guidance"] is None


def test_the_quality_loop_report_queue_correct_refresh(client):
    _provide(ANSWER)
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "CHN", "destination": "LAO"}).json()
    # 1. The reader flags the answer they actually saw.
    rep = client.post("/database/report-issue", headers=READER,
                      json={"nationality": "CHN", "destination": "LAO",
                            "field": "government_fee", "note": "fee looks wrong",
                            "cache_key": look["cache_key"]})
    assert rep.status_code == 200
    issue_id = rep.json()["id"]
    # 2. The queue is NOT scoped to the admin's org: reports come from
    #    readers, whose org is never the operator's.
    q = client.get("/database/issues", headers=OTHER_ORG_ADMIN).json()["issues"]
    assert any(i["id"] == issue_id for i in q)
    # A reader cannot read the queue.
    assert client.get("/database/issues", headers=READER).status_code == 403
    # 3. Closing without a reason is refused.
    bad = client.post(f"/database/issues/{issue_id}", headers=OTHER_ORG_ADMIN,
                      json={"status": "corrected"})
    assert bad.status_code == 422
    # 4. Corrected (with a reason) expires the cached answer...
    ok = client.post(f"/database/issues/{issue_id}", headers=OTHER_ORG_ADMIN,
                     json={"status": "corrected",
                           "resolution": "re-decided with the fixed prompt"})
    assert ok.status_code == 200
    # ...so the next lookup is a fresh decision, not the declared-wrong row.
    _provide(dict(ANSWER, government_fee={"amount": 60, "currency": "USD"}))
    again = client.post("/database/lookup", headers=READER,
                        json={"nationality": "CHN", "destination": "LAO"}).json()
    assert again["cached"] is False
    assert again["guidance"]["government_fee"]["amount"] == 60


def test_release_binds_to_the_exact_answer_via_its_key(client, monkeypatch):
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    _provide(dict(ANSWER, confidence="low"))
    held = client.post("/database/lookup", headers=READER,
                       json={"nationality": "CHN", "destination": "NPL",
                             "arrival_date": "2026-12-01"}).json()
    assert held["review_required"] is True and held["guidance"] is None
    # The dated lookup's key differs from the undated one — the echo is what
    # makes the release reach the answer the operator reviewed.
    rel = client.post("/database/approve", headers=OTHER_ORG_ADMIN,
                      json={"nationality": "CHN", "destination": "NPL",
                            "cache_key": held["cache_key"],
                            "note": "checked against the official source"})
    assert rel.status_code == 200
    after = client.post("/database/lookup", headers=READER,
                        json={"nationality": "CHN", "destination": "NPL",
                              "arrival_date": "2026-12-01"}).json()
    assert after["review_required"] is False
    assert after["guidance"]["disposition"] == "VISA_REQUIRED"
    # A reader cannot release.
    deny = client.post("/database/approve", headers=READER,
                       json={"nationality": "CHN", "destination": "NPL",
                             "cache_key": held["cache_key"]})
    assert deny.status_code == 403


def test_ask_refuses_to_guess_an_unnamed_route(client):
    kimi_primary.set_provider(lambda system, user: {
        "nationality": None, "destination": None,
        "travel_purpose": None, "travel_document_type": "ordinary_passport"})
    r = client.post("/database/ask", headers=READER,
                    json={"question": "do i need a visa"})
    assert r.status_code == 200
    assert r.json()["understood"] is False



def test_by_default_a_low_confidence_answer_is_held_but_never_blank(client):
    """Trip.com's acceptance standard: low-confidence content is blocked from
    readers until operations confirms it. The reader still gets a RESPONSE —
    a held card, never an error — and the operator queue sees the flag."""
    _provide(dict(ANSWER, confidence="low"))
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "MNG"})
    assert r.status_code == 200                  # a response, always
    body = r.json()
    assert body["review_required"] is True      # flagged for operators
    assert body["held"] is True                  # withheld from the reader
    assert body.get("guidance") in (None, {})    # claims not shown


def test_the_hold_can_be_switched_off(client, monkeypatch):
    """The owner can revert to always-serve with the env switch."""
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "0")
    _provide(dict(ANSWER, confidence="low"))
    # A pair no verified override touches, so the engine's answer serves.
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "ISL", "destination": "BTN"})
    body = r.json()
    assert body["held"] is False
    assert body["guidance"]["disposition"] == "VISA_REQUIRED"


def test_should_reground_asks_for_a_fresh_page_check_when_due():
    """The owner's rule: a route someone asks about now gets checked against
    the most recent official data — never grounded means due, an old
    grounding means due, a fresh one does not."""
    import datetime
    from app.main import should_reground
    g = {"guidance": {"disposition": "VISA_REQUIRED"}}
    assert should_reground(dict(g)) is True                       # never grounded
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=10)).isoformat()
    assert should_reground(dict(g, grounded_check={"at": old})) is True
    fresh = datetime.datetime.now(datetime.timezone.utc).isoformat()
    assert should_reground(dict(g, grounded_check={"at": fresh})) is False
    assert should_reground({"guidance": None}) is False           # nothing to check


def test_the_database_answers_even_when_the_engine_fails(client):
    """The owner's rule: Ellis always answers. A timeout or provider outage on
    one variant must not leave a reader with nothing — the closest real answer
    for the SAME passport and destination is served, marked approximate."""
    _provide(ANSWER)
    first = client.post("/database/lookup", headers=READER,
                        json={"nationality": "CHN", "destination": "KHM"})
    assert first.status_code == 200

    def boom(system, user):
        raise kimi_primary.GuidanceTimeout()
    kimi_primary.set_provider(boom)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "KHM",
                          "travel_purpose": "business"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approximate"] is True
    assert body["guidance"]["disposition"] == "VISA_REQUIRED"
    assert body["approximate_reason"]
    assert body["approximate_for"]["asked"]["travel_purpose"] == "business"


def test_a_route_we_hold_nothing_for_still_fails_honestly(client):
    """The fallback never crosses to a different route: with nothing at all
    for the pair, the honest retry message surfaces rather than another
    country's answer."""
    def boom(system, user):
        raise kimi_primary.GuidanceTimeout()
    kimi_primary.set_provider(boom)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "ISL", "destination": "TUV"})
    assert r.status_code == 504
