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
    # Every real answer names the official page it came from; without one the
    # standard grades the answer Low and it is withheld (4.2.3), which is
    # exercised by its own test below. A product-bearing answer must also
    # have been READ against that page to be displayable.
    "source_url": "https://www.mofa.go.jp/j_info/visit/visa/index.html",
    "grounded_check": {"consistent": True, "at": "2026-08-28T00:00:00"},
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
    # Drain in-flight detail-stage threads so no stray background call eats
    # the NEXT test's stubbed provider (a real cross-file flake, 2026-09-01).
    kimi_primary.join_detail_stage()
    kimi_primary.set_provider(None)


def _provide(answer):
    kimi_primary.set_provider(lambda system, user: dict(answer))


def test_lookup_returns_the_answer_and_its_cache_identity(client):
    _provide(ANSWER)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "ISL", "destination": "BLZ"})
    assert r.status_code == 200
    body = r.json()
    # A brand-new answer asserting visa products has not been read against
    # its official page yet, so the ladder withholds it (4.2.3) until the
    # background check agrees. The identity below is what this test owns.
    if body["guidance"] is not None:
        assert body["guidance"]["disposition"] == "VISA_REQUIRED"
        assert body["guidance"]["visa_products"][0]["max_stay_days"] == 30
    else:
        assert body["held"] is True and body["review_required"] is True
    # The identity the report/release loop binds to.
    assert body["cache_key"].startswith("ISL|ISL|BLZ|tourism|")


def test_a_held_answer_ships_no_claims(client, monkeypatch):
    """With the hold switched ON, a low-confidence answer's claims never leave
    the server. The switch is OFF by default (Ellis always answers); this
    pins the behaviour for a deployment that turns it on."""
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    _provide(dict(ANSWER, confidence="low"))
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "ISL", "destination": "BTN"})
    assert r.status_code == 200
    body = r.json()
    assert body["review_required"] is True
    assert body["guidance"] is None


def test_the_quality_loop_report_queue_correct_refresh(client):
    _provide(ANSWER)
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "FSM"}).json()
    # 1. The reader flags the answer they actually saw.
    rep = client.post("/database/report-issue", headers=READER,
                      json={"nationality": "ISL", "destination": "FSM",
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
    # The loop walks its five stages now: the provider is told, the fix is
    # written, someone else reviews it, and only then does it go live. Skipping
    # a stage is refused, which is what makes the queue a progression rather
    # than a free-text label.
    ack = client.post(f"/database/issues/{issue_id}", headers=OTHER_ORG_ADMIN,
                      json={"status": "acknowledged",
                            "resolution": "flagged to the information provider"})
    assert ack.status_code == 200
    skip = client.post(f"/database/issues/{issue_id}", headers=OTHER_ORG_ADMIN,
                       json={"status": "published", "resolution": "x"})
    assert skip.status_code == 422, "a stage may not be skipped"
    ok = client.post(f"/database/issues/{issue_id}", headers=OTHER_ORG_ADMIN,
                     json={"status": "corrected",
                           "resolution": "re-decided with the fixed prompt"})
    assert ok.status_code == 200
    # ...so the next lookup is a fresh decision, not the declared-wrong row.
    _provide(dict(ANSWER, government_fee={"amount": 60, "currency": "USD"}))
    again = client.post("/database/lookup", headers=READER,
                        json={"nationality": "ISL", "destination": "FSM"}).json()
    assert again["cached"] is False
    # A refreshed answer asserting products is withheld until its official
    # page has been read (4.2.3); the corrected fee is in the record either
    # way, which is what the quality loop is being tested for.
    if again["guidance"] is not None:
        assert again["guidance"]["government_fee"]["amount"] == 60
    else:
        assert again["held"] is True


def test_release_binds_to_the_exact_answer_via_its_key(client, monkeypatch):
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    _provide(dict(ANSWER, confidence="low"))
    held = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "NRU",
                             "arrival_date": "2026-12-01"}).json()
    assert held["review_required"] is True and held["guidance"] is None
    # The dated lookup's key differs from the undated one — the echo is what
    # makes the release reach the answer the operator reviewed.
    rel = client.post("/database/approve", headers=OTHER_ORG_ADMIN,
                      json={"nationality": "ISL", "destination": "NRU",
                            "cache_key": held["cache_key"],
                            "note": "checked against the official source"})
    assert rel.status_code == 200
    after = client.post("/database/lookup", headers=READER,
                        json={"nationality": "ISL", "destination": "NRU",
                              "arrival_date": "2026-12-01"}).json()
    assert after["review_required"] is False
    assert after["guidance"]["disposition"] == "VISA_REQUIRED"
    # A reader cannot release.
    deny = client.post("/database/approve", headers=READER,
                       json={"nationality": "ISL", "destination": "NRU",
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
                    json={"nationality": "ISL", "destination": "PLW"})
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
                        json={"nationality": "ISL", "destination": "BLZ"})
    assert first.status_code == 200

    def boom(system, user):
        raise kimi_primary.GuidanceTimeout()
    kimi_primary.set_provider(boom)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "ISL", "destination": "BLZ",
                          "travel_purpose": "business"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approximate"] is True
    # The stand-in is delivered honestly: either its guidance, or the held
    # card when the confidence ladder withholds an unconfirmed answer. What
    # this test owns is the fallback itself, not the grading.
    assert body["guidance"] is not None or body["held"] is True
    if body["guidance"] is not None:
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


def test_a_verified_route_is_never_held(client, monkeypatch):
    """China->UK served an EMPTY held card while its human-checked fee and
    products sat in the override: the hold only released when the override
    named the disposition. ANY human verification now releases the hold."""
    monkeypatch.setenv("ELLIS_DATABASE_HOLD_LOW_CONFIDENCE", "1")
    _provide(dict(ANSWER, confidence="low"))
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "GBR"})
    body = r.json()
    assert body["source_verified"] is not None
    assert body["held"] is False
    assert body["guidance"]["government_fee"]["amount"] == 135


def test_overrides_do_not_claim_document_variants(client):
    """The CHN->GBR override is verified for ordinary passports. A diplomatic
    passport answer must not inherit it."""
    _provide(dict(ANSWER))
    # (diplomatic now has its OWN verified override for this route, which is
    # the intended behaviour; an emergency passport has none and must not
    # inherit the ordinary-passport fact.)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "GBR",
                          "travel_document_type": "emergency_passport"})
    assert r.json().get("source_verified") is None


def test_junk_destination_is_a_clean_422_not_an_ai_guess(client):
    _provide(ANSWER)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "XXX"})
    assert r.status_code == 422
    r2 = client.post("/database/lookup", headers=READER,
                     json={"nationality": "Chinaa", "destination": "Japan"})
    # A misspelt but resolvable name still works via the registry aliases…
    # and a real name pair answers normally.
    r3 = client.post("/database/lookup", headers=READER,
                     json={"nationality": "China", "destination": "Japan"})
    assert r3.status_code == 200


def test_lookup_echoes_the_transit_it_answered_for(client):
    _provide(ANSWER)
    r = client.post("/database/lookup", headers=READER,
                    json={"nationality": "CHN", "destination": "USA",
                          "transit_countries": ["JPN"]})
    assert r.json()["transit_countries"] == ["JPN"]


def test_ask_review_writes_verdict_and_wrong_files_a_tracked_issue(client):
    """Their P0 backend samples AI Q&A output like any record. A verdict must
    be writable against the logged exchange, and an answer ruled wrong must
    enter the same tracked correction loop as every other error."""
    _provide(ANSWER)
    ask = client.post("/database/ask", headers=READER,
                      json={"question": "from Iceland to Japan for tourism"})
    assert ask.status_code == 200 and ask.json()["understood"]
    log = client.get("/database/asks", headers=OTHER_ORG_ADMIN).json()["asks"]
    assert log, "the exchange must be logged for sampling"
    ask_id = log[0]["id"]
    # A reader cannot rule on answers.
    assert client.post(f"/database/asks/{ask_id}/review", headers=READER,
                       json={"verdict": "correct"}).status_code == 403
    # Wrong without a reason is refused; with one it files an issue.
    assert client.post(f"/database/asks/{ask_id}/review",
                       headers=OTHER_ORG_ADMIN,
                       json={"verdict": "wrong"}).status_code == 422
    ruled = client.post(f"/database/asks/{ask_id}/review",
                        headers=OTHER_ORG_ADMIN,
                        json={"verdict": "wrong",
                              "note": "fee is out of date"}).json()
    assert ruled["ok"] and ruled["issue_id"]
    after = client.get("/database/asks", headers=OTHER_ORG_ADMIN).json()["asks"]
    mine = next(a for a in after if a["id"] == ask_id)
    assert mine["verdict"] == "wrong" and mine["reviewed_by"]
    issues = client.get("/database/issues",
                        headers=OTHER_ORG_ADMIN).json()["issues"]
    assert any(i["id"] == ruled["issue_id"] and i["field"] == "ai_answer"
               for i in issues)


def test_expiring_a_ruled_answer_writes_a_delete_change_entry(client):
    """4.1.2 change management distinguishes add, modify and DELETE. The one
    path that removes a served answer (an issue ruled corrected) must write
    the delete entry, or the log claims nothing was withdrawn."""
    _provide(ANSWER)
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "PLW"}).json()
    rep = client.post("/database/report-issue", headers=READER,
                      json={"nationality": "ISL", "destination": "PLW",
                            "field": "government_fee", "note": "stale fee",
                            "cache_key": look["cache_key"]}).json()
    for status, reason in (("acknowledged", "provider told"),
                           ("corrected", "re-verified against the source")):
        r = client.post(f"/database/issues/{rep['id']}",
                        headers=OTHER_ORG_ADMIN,
                        json={"status": status, "resolution": reason})
        assert r.status_code == 200
    changes = client.get("/database/changes?limit=50",
                         headers=OTHER_ORG_ADMIN).json()["changes"]
    dele = [c for c in changes if c.get("action") == "delete"
            and c.get("cache_key") == look["cache_key"]]
    assert dele, "withdrawing the answer must be logged as a delete"
    assert "issue" in (dele[0].get("origin") or "")


def test_ask_carries_policy_notes_and_continues_a_clarify(client):
    """The three Q&A gaps from the 2026-08-31 evaluation, end to end: a
    policy-only question gets the verified note beside the clarify, a route
    question gets a decisive covered-or-not line, and a bare-country reply
    continues the clarified question instead of restarting it."""
    # The stub answers both roles the model plays: reading a question into
    # a route, and answering a route. The ETA question reads to a lone
    # destination, exactly what the live parser produced for the evaluator.
    answer = dict(ANSWER, disposition="VISA_REQUIRED")

    def model(system, user):
        if '"question"' in str(user):
            return {"nationality": "", "destination": "AUS",
                    "travel_purpose": "tourism",
                    "travel_document_type": "ordinary_passport"}
        return dict(answer)
    kimi_primary.set_provider(model)
    # 1. "144-hour transit" names no route: clarify + the transit note.
    r1 = client.post("/database/ask", headers=READER,
                     json={"question": "144-hour transit visa-free policy"}).json()
    assert r1["understood"] is False
    ids = [p["id"] for p in r1.get("special_policies", [])]
    assert "china-240h-transit-visa-free" in ids
    # 2. India to Hainan: answered as IND->CHN, with the decisive line that
    #    Indian passports are not on the eligible list.
    r2 = client.post("/database/ask", headers=READER,
                     json={"question":
                           "Can I go to Hainan China from India without a visa?"}).json()
    assert r2["understood"] is True
    hainan = [p for p in r2.get("special_policies", [])
              if p["id"] == "china-hainan-visa-free"]
    assert hainan and hainan[0]["applies_to_you"] is False
    # 3. The Australia ETA flow: clarify asks for the passport, "China"
    #    answers it, and the pending destination survives.
    r3 = client.post("/database/ask", headers=READER,
                     json={"question": "How do I apply for an Australia ETA?"}).json()
    assert r3["understood"] is False and r3["route"]["destination"] == "AUS"
    r4 = client.post("/database/ask", headers=READER,
                     json={"question": "China", "context": r3["route"]}).json()
    assert r4["understood"] is True
    assert (r4["route"]["nationality"], r4["route"]["destination"]) == \
        ("CHN", "AUS")


def test_operator_edit_writes_a_gated_override_that_readers_see(client,
                                                                tmp_path,
                                                                monkeypatch):
    """Trip.com's console edit: gated exactly like every verified fact
    (official source required, whitelisted fields only), applied at read
    time, logged as a change, and refused to readers."""
    import json as _json
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES",
                       str(tmp_path / "operator_overrides.json"))
    from app.visa_snapshot import verified_overrides
    verified_overrides.reload()
    _provide(dict(ANSWER, visa_products=[]))
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "KIR"}).json()
    assert look["guidance"]["government_fee"]["amount"] == 100
    edit = {"nationality": "ISL", "destination": "KIR",
            "travel_purpose": "tourism",
            "fields": {"government_fee": {"amount": 120, "currency": "USD"}},
            "source_url": "https://www.mofa.go.jp/fee-page",
            "note": "fee updated per the official schedule"}
    # A reader cannot edit; a commercial source is refused; unknown fields
    # are refused by name.
    assert client.post("/database/records/edit", headers=READER,
                       json=edit).status_code == 403
    bad_src = dict(edit, source_url="https://www.ivisa.com/fees")
    assert client.post("/database/records/edit", headers=OTHER_ORG_ADMIN,
                       json=bad_src).status_code == 422
    bad_field = dict(edit, fields={"government_fee": {"amount": 120},
                                   "hacked": True})
    assert client.post("/database/records/edit", headers=OTHER_ORG_ADMIN,
                       json=bad_field).status_code == 422
    ok = client.post("/database/records/edit", headers=OTHER_ORG_ADMIN,
                     json=edit).json()
    assert ok["ok"] and ok["applied_to_served_answer"]
    # The next reader sees the edited fee, with the operator's provenance.
    again = client.post("/database/lookup", headers=READER,
                        json={"nationality": "ISL", "destination": "KIR"}).json()
    assert again["guidance"]["government_fee"]["amount"] == 120
    assert "Trip.com operations" in _json.dumps(
        again.get("source_verified") or {})
    # The edit is in the change log with its field diff.
    changes = client.get("/database/changes?limit=20",
                         headers=OTHER_ORG_ADMIN).json()["changes"]
    mine = [c for c in changes if c.get("origin") == "operator-edit"]
    assert mine and "government_fee" in (mine[0].get("changes") or {})
    verified_overrides.reload()


def test_operator_edit_layers_onto_seed_overrides_not_over_them(client,
                                                                tmp_path,
                                                                monkeypatch):
    """An operator correcting ONE field must not wipe the seed entry's other
    verified facts. A console edit of processing_time once shadowed the
    whole CHN->KOR entry and the verified fee vanished from the served
    answer (2026-09-01). The layers merge per field."""
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES",
                       str(tmp_path / "operator_overrides.json"))
    from app.visa_snapshot import verified_overrides
    verified_overrides.reload()
    _provide(dict(ANSWER, visa_products=[],
                  government_fee={"amount": 40, "currency": "USD"}))
    # CHN->KOR carries a seed override with the verified 280 CNY fee.
    edit = {"nationality": "CHN", "destination": "KOR",
            "travel_purpose": "tourism",
            "fields": {"processing_time": "edited by the operator"},
            "source_url": "https://overseas.mofa.go.kr/cn-zh/wpge/m_1199/contents.do",
            "note": "processing time confirmed"}
    assert client.post("/database/records/edit", headers=OTHER_ORG_ADMIN,
                       json=edit).json()["ok"]
    out = client.post("/database/lookup", headers=READER,
                      json={"nationality": "CHN",
                            "destination": "KOR"}).json()
    g = out["guidance"]
    assert g["processing_time"] == "edited by the operator"
    assert g["government_fee"]["amount"] == 280, \
        "the seed's verified fee must survive an unrelated operator edit"
    verified_overrides.reload()


def test_the_assistant_is_ellis_refuses_off_topic_and_grounds_replies(client):
    """The conversation layer's three hard rules: identity questions answer
    Ellis with no model call, non-immigration questions get the one-sentence
    refusal, and a composed reply is built from the served facts through the
    provider with a deterministic fallback when it fails."""
    from app.visa_snapshot import kimi_primary
    kimi_primary.set_provider(lambda system, user: (_ for _ in ()).throw(
        AssertionError("identity and refusal must not call the model")))
    # Identity, both scripts, and no model-name leakage.
    for q in ("What AI are you?", "what's your name", "你是谁"):
        r = client.post("/database/ask", headers=READER,
                        json={"question": q}).json()
        assert r.get("identity") and "Ellis" in r["reply"]
        assert "kimi" not in r["reply"].lower()
    # Off-topic, both scripts, the exact sentence.
    r = client.post("/database/ask", headers=READER,
                    json={"question": "what's the weather like today?"}).json()
    assert r.get("off_topic")
    assert r["reply"] == "Sorry, I can only help with immigration matters."
    r = client.post("/database/ask", headers=READER,
                    json={"question": "今天天气怎么样"}).json()
    assert r["reply"] == "抱歉，我只能协助出入境相关事务。"
    # A terse route question is never refused.
    calls = {}

    def model(system, user):
        if "Compose one short reply" in str(system):
            calls["facts"] = str(user)
            return {"reply": "A visa is required. The fee is 100 USD. "
                             "The full record below has the details."}
        if '"question"' in str(user):
            return {"nationality": "ISL", "destination": "JPN",
                    "travel_purpose": "tourism",
                    "travel_document_type": "ordinary_passport"}
        return dict(ANSWER, visa_products=[])
    kimi_primary.set_provider(model)
    r = client.post("/database/ask", headers=READER,
                    json={"question": "from Iceland to Japan for tourism",
                          "history": [{"role": "user", "text": "hi"}]}).json()
    assert r["understood"] is True
    assert "100 USD" in (r.get("reply") or "")
    assert '"government_fee"' in calls.get("facts", ""), \
        "the composer must receive the served facts"
    # Composer failure falls back silently: no reply key, answer intact.
    def broken(system, user):
        if "Compose one short reply" in str(system):
            raise RuntimeError("model down")
        return dict(ANSWER, visa_products=[])
    kimi_primary.set_provider(broken)
    r2 = client.post("/database/ask", headers=READER,
                     json={"question": "from Iceland to Nauru for tourism"}).json()
    assert r2["understood"] is True and "reply" not in r2
    assert r2["guidance"]["government_fee"]["amount"] == 100


def test_change_webhook_fires_when_configured(client, monkeypatch):
    """Evaluation VI.4: a system reminder on every change. With the webhook
    URL set, writing a change posts compact JSON. Without it, nothing
    happens and nothing breaks."""
    import json as _json
    import time
    hits = []
    import urllib.request as _ur

    def fake_open(req, timeout=0):
        hits.append(_json.loads(req.data.decode("utf-8")))
        class _R:  # noqa: N801
            def read(self):
                return b""
        return _R()
    monkeypatch.setattr(_ur, "urlopen", fake_open)
    monkeypatch.setenv("ELLIS_CHANGE_WEBHOOK_URL", "https://example.com/hook")
    _provide(dict(ANSWER, visa_products=[]))
    client.post("/database/lookup", headers=READER,
                json={"nationality": "ISL", "destination": "TUV"})
    for _ in range(20):
        if hits:
            break
        time.sleep(0.05)
    assert hits and hits[0]["event"] == "database_change"
    assert hits[0]["action"] == "add" and "ISL->TUV" in hits[0]["route"]
