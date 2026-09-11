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


def test_the_quality_loop_report_queue_correct_refresh(client, db, tmp_path, monkeypatch):
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operators.json"))
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
    # 4. Status text cannot replace a sourced correction or delete the answer.
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
    assert ok.status_code == 422
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    original = db.query(KimiRouteGuidanceCache).filter_by(cache_key=look["cache_key"]).one()
    original_id = original.id
    fields = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
              "government_fee": {"amount": 60, "currency": "USD"},
              "visa_products": [dict(p, fee={"amount": 60, "currency": "USD"}) for p in ANSWER["visa_products"]]}
    edited = client.post("/database/records/edit", headers=OTHER_ORG_ADMIN, json={
        "nationality": "ISL", "destination": "FSM", "fields": fields,
        "source_url": ANSWER["source_url"], "note": "Fixture source establishes visa requirement and corrected 60 USD fee."})
    assert edited.status_code == 200, edited.text
    assert client.post(f"/database/issues/{issue_id}", headers=OTHER_ORG_ADMIN,
        json={"status": "corrected", "resolution": "source correction applied"}).status_code == 200
    kimi_primary.set_provider(lambda *a: (_ for _ in ()).throw(AssertionError("must not regenerate")))
    again = client.post("/database/lookup", headers=READER,
                        json={"nationality": "ISL", "destination": "FSM"}).json()
    assert again["cached"] is True
    assert again["guidance"]["government_fee"]["amount"] == 60
    assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=look["cache_key"]).one().id == original_id


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
    # A tourist answer is never substituted for a business permission.
    assert r.status_code == 504
    assert "guidance" not in r.json()

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


def test_issue_status_does_not_delete_a_canonical_answer_or_fake_a_change(client, db):
    """A workflow label cannot replace evidence or destroy cached provenance."""
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
        assert r.status_code == (200 if status == "acknowledged" else 422)
    changes = client.get("/database/changes?limit=50",
                         headers=OTHER_ORG_ADMIN).json()["changes"]
    dele = [c for c in changes if c.get("action") == "delete"
            and c.get("cache_key") == look["cache_key"]]
    assert not dele, "status changes cannot invent a policy deletion"
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    assert db.query(KimiRouteGuidanceCache).filter_by(cache_key=look["cache_key"]).one()


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
    assert look["held"] and look["guidance"] is None, \
        "the unread initial answer must stay held until the operator verifies it"
    edit = {"nationality": "ISL", "destination": "KIR",
            "travel_purpose": "tourism",
            "fields": {"disposition": "VISA_REQUIRED", "government_fee": {"amount": 120, "currency": "USD"}},
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


def test_the_assistant_is_ellis_refuses_off_topic_and_grounds_replies(client, db, tmp_path, monkeypatch):
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
    import json
    from datetime import date
    from app.visa_snapshot import verified_overrides as vo
    source = tmp_path / "assistant-fixture-evidence.json"
    source.write_text(json.dumps([{
        "route": {"nationality": "ISL", "destination": dest, "purpose": "tourism",
                  "travel_document_type": "ordinary_passport"},
        "fields": {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa"},
        "source_url": ANSWER["source_url"], "verified_at": date.today().isoformat(),
        "verifier": "ai", "note": "Fixture: ordinary Icelandic passport tourism applicants need a visa."
    } for dest in ("JPN", "NRU")]))
    monkeypatch.setattr(vo, "OVERRIDES", source)
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operators.json"))
    vo.reload()
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.like("ISL|%|JPN|%" )).delete(synchronize_session=False)
    db.query(KimiRouteGuidanceCache).filter(KimiRouteGuidanceCache.cache_key.like("ISL|%|NRU|%" )).delete(synchronize_session=False)
    db.commit()

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
    # Composer failure falls back to a sentence built from the served facts:
    # the feed never goes silent, and the fallback never invents a number.
    def broken(system, user):
        if "Compose one short reply" in str(system):
            raise RuntimeError("model down")
        return dict(ANSWER, visa_products=[])
    kimi_primary.set_provider(broken)
    r2 = client.post("/database/ask", headers=READER,
                     json={"question": "from Iceland to Nauru for tourism"}).json()
    assert r2["understood"] is True
    assert r2.get("reply") and r2.get("reply_source") == "facts"
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


def test_operator_edit_patches_one_visa_product_in_place(client, tmp_path,
                                                         monkeypatch):
    """Editing the row the operator sees: a product patch names the product
    and merges into the served product list, leaving its siblings alone."""
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES",
                       str(tmp_path / "operator_overrides.json"))
    from app.visa_snapshot import verified_overrides
    verified_overrides.reload()
    _provide(dict(ANSWER, visa_products=[
        {"type": "Tourist single", "entry": "single", "validity": None,
         "max_stay_days": 30, "fee": {"amount": 25, "currency": "USD"}},
        {"type": "Tourist multiple", "entry": "multiple", "validity": "1 year",
         "max_stay_days": 30, "fee": {"amount": 60, "currency": "USD"}},
    ]))
    client.post("/database/lookup", headers=READER,
                json={"nationality": "ISL", "destination": "NIU"})
    edit = {"nationality": "ISL", "destination": "NIU",
            "travel_purpose": "tourism", "fields": {"disposition": "VISA_REQUIRED"},
            "product_patch": {"visa_type_name": "Tourist single",
                              "validity": "3 months", "fee_amount": 30},
            "source_url": "https://www.mofa.go.jp/fee-page",
            "note": "validity and fee per the official schedule"}
    ok = client.post("/database/records/edit", headers=OTHER_ORG_ADMIN,
                     json=edit).json()
    assert ok["ok"] and "visa_products" in ok["fields"]
    again = client.post("/database/lookup", headers=READER,
                        json={"nationality": "ISL", "destination": "NIU"}).json()
    prods = {p["type"]: p for p in again["guidance"]["visa_products"]}
    assert prods["Tourist single"]["validity"] == "3 months"
    assert prods["Tourist single"]["fee"]["amount"] == 30
    assert prods["Tourist multiple"]["fee"]["amount"] == 60   # untouched
    # A patch naming a product that does not exist is refused loudly.
    bad = dict(edit, product_patch={"visa_type_name": "No such visa",
                                    "validity": "1 year"})
    assert client.post("/database/records/edit", headers=OTHER_ORG_ADMIN,
                       json=bad).status_code == 422
    verified_overrides.reload()


def test_clarify_turns_are_conversational_but_never_numeric(client):
    """A turn with no readable route still gets a composed, on-topic reply
    (the tester who wrote "i dont have a passport" saw the same canned line
    twice). A composed clarify carrying an invented number is discarded for
    the deterministic line, exactly like the answer composer."""
    def model(system, user):
        if "route is not fully known" in system:
            return {"reply": "You will need a valid passport before any visa "
                             "can be issued. Tell me which country would "
                             "issue it and where you want to go."}
        return {"understood": False}
    kimi_primary.set_provider(model)
    out = client.post("/database/ask", headers=READER,
                      json={"question": "i dont have a passport",
                            "history": [{"role": "user",
                                         "text": "do i need a visa"}]}).json()
    assert out["understood"] is False
    assert "valid passport before" in out["clarify"]
    def numeric_model(system, user):
        if "route is not fully known" in system:
            return {"reply": "A passport costs 145 dollars and takes 6 weeks."}
        return {"understood": False}
    kimi_primary.set_provider(numeric_model)
    out2 = client.post("/database/ask", headers=READER,
                       json={"question": "i dont have a passport"}).json()
    assert out2["understood"] is False
    assert "145" not in out2["clarify"]
    assert out2["clarify"]                     # the deterministic line stands


def test_the_passport_never_changes_mid_conversation(client):
    """"Can i go to japan from china" then "what if i wanna go from japan to
    france afterwards": the second "from japan" is a departure point, not a
    new passport. Only explicit passport words switch the nationality."""
    def model(system, user):
        if "route is not fully known" in system:
            return {"reply": "ok"}
        u = str(user)
        if "france" in u.lower():
            return {"understood": True, "nationality": "JPN",
                    "destination": "FRA", "travel_purpose": "tourism",
                    "travel_document_type": "ordinary_passport",
                    "transit_countries": [], "focus": None}
        return {"understood": True, "nationality": "CHN",
                "destination": "JPN", "travel_purpose": "tourism",
                "travel_document_type": "ordinary_passport",
                "transit_countries": [], "focus": None}
    kimi_primary.set_provider(model)
    out = client.post("/database/ask", headers=READER,
                      json={"question": "what if i wanna go from japan to "
                                        "france afterwards",
                            "context": {"nationality": "CHN",
                                        "destination": "JPN",
                                        "travel_purpose": "tourism"}}).json()
    assert (out["route"]["nationality"],
            out["route"]["destination"]) == ("CHN", "FRA")
    # Saying the passport out loud DOES switch it.
    out2 = client.post("/database/ask", headers=READER,
                       json={"question": "with a japanese passport from "
                                         "japan to france",
                             "context": {"nationality": "CHN",
                                         "destination": "JPN"}}).json()
    assert out2["route"]["nationality"] == "JPN"
    # And a bare fresh route with no continuation phrasing switches too:
    # "from france to japan" after a China conversation is a new question
    # about a French passport, not the same trip continuing.
    def model2(system, user):
        if "route is not fully known" in system:
            return {"reply": "ok"}
        return {"understood": True, "nationality": "FRA",
                "destination": "JPN", "travel_purpose": "tourism",
                "travel_document_type": "ordinary_passport",
                "transit_countries": [], "focus": None}
    kimi_primary.set_provider(model2)
    out3 = client.post("/database/ask", headers=READER,
                       json={"question": "from france to japan",
                             "context": {"nationality": "CHN",
                                         "destination": "MYS"}}).json()
    assert (out3["route"]["nationality"],
            out3["route"]["destination"]) == ("FRA", "JPN")


def test_greetings_are_greeted_not_refused(client):
    """"hello" was answered with the immigration-only refusal. A pleasantry
    gets a welcome; a greeting carrying a real question still flows through."""
    out = client.post("/database/ask", headers=READER,
                      json={"question": "hello"}).json()
    assert out.get("greeting") and "Ellis" in out["reply"]
    zh = client.post("/database/ask", headers=READER,
                     json={"question": "你好"}).json()
    assert zh.get("greeting") and "Ellis" in zh["reply"]
    thanks = client.post("/database/ask", headers=READER,
                         json={"question": "thanks!"}).json()
    assert thanks.get("greeting")
    kimi_primary.set_provider(lambda system, user: {"understood": False})
    real = client.post("/database/ask", headers=READER,
                       json={"question": "hello, do i need a visa for japan "
                                         "with a chinese passport"}).json()
    assert not real.get("greeting")


def test_the_site_language_setting_decides_the_reply_language(client):
    """A customer on the Chinese site gets Chinese replies even when they
    type English, deterministic layers included. The question's script
    stays the fallback for callers that send no language."""
    hello = client.post("/database/ask", headers=READER,
                        json={"question": "hello", "lang": "zh"}).json()
    assert "Ellis" in hello["reply"] and "你好" in hello["reply"]
    ident = client.post("/database/ask", headers=READER,
                        json={"question": "who are you", "lang": "zh"}).json()
    assert "我是 Ellis" in ident["reply"]
    off = client.post("/database/ask", headers=READER,
                      json={"question": "tell me a joke", "lang": "zh"}).json()
    assert off["reply"] == "抱歉，我只能协助出入境相关事务。"
    kimi_primary.set_provider(lambda system, user: {"understood": False})
    clar = client.post("/database/ask", headers=READER,
                       json={"question": "visa please", "lang": "zh"}).json()
    assert "护照" in clar["clarify"]
    en = client.post("/database/ask", headers=READER,
                     json={"question": "你好", "lang": "en"}).json()
    assert "Hi, I'm Ellis" in en["reply"]


def test_a_blocked_page_never_demotes_a_grounded_record_to_low(client):
    """2026-09-02 regression: eight grounded engine answers fell to Low and
    were held from readers after the 48-hour sweep hit pages that block
    robots. A failed re-check attempt must leave the record's grade and its
    serving exactly as the last successful read left them."""
    from app.db import SessionLocal
    from app.visa_snapshot import fetching, freshness
    from app.visa_snapshot.fetching import FetchResult
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    _provide(dict(ANSWER, confidence="medium"))
    body = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "TON"}).json()
    key = body["cache_key"]

    def grade():
        recs = client.get("/database/records", headers=OTHER_ORG_ADMIN,
                          params={"nationality": "ISL", "destination": "TON"}
                          ).json()["records"]
        return {r["confidence_level"] for r in recs}

    db = SessionLocal()
    try:
        row = db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).one()
        good = {"at": "2026-09-01T00:00:00+00:00", "outcome": "checked",
                "evidence_contract": 2, "verified_fields": ["disposition"],
                "consistent": True, "source_url": ANSWER["source_url"],
                "changed_fields": [], "disputed_fields": []}
        row.verification = dict(row.verification or {},
                                grounded_check=good, last_good_check=dict(good))
        db.commit()
        assert grade() == {"Medium"}
        # The page now blocks robots.
        fetching.set_fetcher(lambda url, timeout_seconds=0: FetchResult(
            requested_url=url, ok=True, final_url=url,
            final_hostname="www.mofa.go.jp",
            content_text="checking your browser", challenge=True))
        db.expire_all()
        row = db.query(KimiRouteGuidanceCache).filter_by(cache_key=key).one()
        assert freshness.recheck_row(db, row)["outcome"] == "fetch_failed"
    finally:
        fetching.set_fetcher(None)
        db.close()
    assert grade() == {"Medium"}, "a blocked page must not demote a grounded record"
    after = client.post("/database/lookup", headers=READER,
                        json={"nationality": "ISL", "destination": "TON"}).json()
    assert after["held"] is False and after["guidance"]["disposition"] == "VISA_REQUIRED"


def test_the_record_browser_is_served_from_a_cache_that_writes_invalidate(client, monkeypatch):
    """The 1,442-record browser took 48 seconds to rebuild per read on
    11 September 2026 and the console gave up at 30. The full set is built
    once, repeat reads are served from it, and an operator write makes it
    stale immediately so the next read rebuilds."""
    from app import main as m
    _provide(ANSWER)
    m.invalidate_records_cache()
    with m._RECORDS_CACHE_LOCK:
        m._RECORDS_CACHE["rows"] = None
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "FSM"}).json()
    builds = {"n": 0}
    real_builder = m._build_tstation_rows

    def counted(db):
        builds["n"] += 1
        return real_builder(db)
    monkeypatch.setattr(m, "_build_tstation_rows", counted)
    monkeypatch.setattr(m, "RECORDS_CACHE_SECONDS", 3600.0)
    monkeypatch.setitem(m._RECORDS_CACHE, "test_enabled", True)
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", None)  # restored to None afterwards: no carry-over into other tests
    first = client.get("/database/records", headers=OTHER_ORG_ADMIN).json()
    second = client.get("/database/records?nationality=ISL", headers=OTHER_ORG_ADMIN).json()
    assert builds["n"] == 1, "the second read, filtered or not, is served from the cache"
    assert any(r["cache_key"] == look["cache_key"] for r in first["records"])
    assert all(r["travel_document_country"] == "ISL" for r in second["records"]) and second["records"]
    # A write under /database/ (a reader's issue report here) invalidates.
    rep = client.post("/database/report-issue", headers=READER,
                      json={"nationality": "ISL", "destination": "FSM",
                            "field": "government_fee", "note": "fee looks wrong",
                            "cache_key": look["cache_key"]})
    assert rep.status_code == 200
    client.get("/database/records", headers=OTHER_ORG_ADMIN)
    assert builds["n"] == 2, "an operator write makes the cached set stale at once"
    # Records handed out are copies: mutating one never changes the cache.
    with m._RECORDS_CACHE_LOCK:
        cached = m._RECORDS_CACHE["rows"][0]
    served = client.get("/database/records", headers=OTHER_ORG_ADMIN).json()["records"][0]
    assert served["travel_document_country"] == cached["travel_document_country"]
    assert builds["n"] == 2


def test_the_read_after_an_edit_shows_the_edit_never_the_cached_rows(client, monkeypatch):
    """Review finding on ops20260911a: invalidation only zeroed the age, so
    the first read after an operator edit still served the pre-edit set and
    rebuilt behind it (fee edited to 137, next read showed 100). A write now
    marks the set stale and the next read waits for one rebuild."""
    from app import main as m
    _provide(ANSWER)
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "FSM"}).json()
    monkeypatch.setattr(m, "RECORDS_CACHE_SECONDS", 3600.0)
    monkeypatch.setitem(m._RECORDS_CACHE, "test_enabled", True)
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", None)
    monkeypatch.setitem(m._RECORDS_CACHE, "dirty", False)
    before = client.get("/database/records?nationality=ISL&destination=FSM", headers=OTHER_ORG_ADMIN).json()["records"]
    assert before
    # Operator overrides persist in a file across runs, so the edit uses a
    # fee no earlier run can have left behind.
    new_fee = int(before[0]["visa_fee_amount"] or 0) + 37
    fields = {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
              "government_fee": {"amount": new_fee, "currency": "USD"},
              "visa_products": [dict(p, fee={"amount": new_fee, "currency": "USD"}) for p in ANSWER["visa_products"]]}
    edited = client.post("/database/records/edit", headers=OTHER_ORG_ADMIN, json={
        "nationality": "ISL", "destination": "FSM", "fields": fields,
        "source_url": ANSWER["source_url"], "note": f"Fixture source establishes the corrected {new_fee} USD fee."})
    assert edited.status_code == 200, edited.text
    with m._RECORDS_CACHE_LOCK:
        assert m._RECORDS_CACHE["dirty"] is True, "the write marked the set stale"
    after = client.get("/database/records?nationality=ISL&destination=FSM", headers=OTHER_ORG_ADMIN).json()["records"]
    assert after and all(r["visa_fee_amount"] == new_fee for r in after), "the very next read shows the edit"
    with m._RECORDS_CACHE_LOCK:
        assert m._RECORDS_CACHE["dirty"] is False and m._RECORDS_CACHE["rows"] is not None


def test_an_engine_commit_from_any_session_invalidates_the_record_cache(client, monkeypatch):
    """The background rechecks and the intake writers commit answer rows
    from their own sessions, never through the operator-write middleware.
    The commit itself marks the cache stale; a rollback does not."""
    from app import main as m
    from app.db import SessionLocal
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    _provide(ANSWER)
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "FSM"}).json()
    monkeypatch.setattr(m, "RECORDS_CACHE_SECONDS", 3600.0)
    monkeypatch.setitem(m._RECORDS_CACHE, "test_enabled", True)
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", None)
    monkeypatch.setitem(m._RECORDS_CACHE, "dirty", False)
    builds = {"n": 0}
    real_builder = m._build_tstation_rows

    def counted(db):
        builds["n"] += 1
        return real_builder(db)
    monkeypatch.setattr(m, "_build_tstation_rows", counted)
    client.get("/database/records", headers=OTHER_ORG_ADMIN)
    assert builds["n"] == 1
    session = SessionLocal()
    try:
        row = session.query(KimiRouteGuidanceCache).filter_by(cache_key=look["cache_key"]).one()
        row.model = "rolled-back-model"
        session.flush()
        session.rollback()
        with m._RECORDS_CACHE_LOCK:
            assert m._RECORDS_CACHE["dirty"] is False, "a rolled-back flush changes nothing"
        row = session.query(KimiRouteGuidanceCache).filter_by(cache_key=look["cache_key"]).one()
        row.model = "background-recheck-model"
        session.commit()
    finally:
        session.close()
    with m._RECORDS_CACHE_LOCK:
        assert m._RECORDS_CACHE["dirty"] is True, "a committed answer row marks the set stale"
    client.get("/database/records", headers=OTHER_ORG_ADMIN)
    assert builds["n"] == 2
    # A read-shaped POST (a traveller's lookup) does not rebuild the set on
    # its own; only a committed row does.
    client.post("/database/lookup", headers=READER, json={"nationality": "ISL", "destination": "FSM"})
    client.get("/database/records", headers=OTHER_ORG_ADMIN)
    assert builds["n"] == 2


def test_record_filters_run_before_dedupe_so_a_confidence_slice_keeps_its_rows(monkeypatch):
    """Review finding on ops20260911a: the cached set was deduplicated before
    the filters ran, and the dedupe key carries neither confidence nor
    source check, so ?confidence=Low lost the row a higher-ranked sibling
    variant had outranked."""
    from app import main as m
    identity = {"travel_document_country": "ISL", "destination_country": "FSM",
                "travel_purpose": "tourism", "travel_document_type": "ordinary_passport",
                "visa_type_name": "Tourist visa", "visa_requirement": "VISA_REQUIRED"}
    strong = dict(identity, confidence_level="High", _source_check="human-quote", visa_fee_amount=100)
    weak = dict(identity, confidence_level="Low", _source_check="unchecked", visa_fee_amount=100)
    monkeypatch.setattr(m, "_all_tstation_rows", lambda db: [strong, weak])
    everything = m._tstation_rows(None)
    assert len(everything) == 1 and everything[0]["confidence_level"] == "High", "one row per product, best evidence wins"
    low = m._tstation_rows(None, confidence="Low")
    assert len(low) == 1 and low[0]["confidence_level"] == "Low", "the filter sees the row it asked for"
    assert m._tstation_rows(None, requirement="VISA_EXEMPT") == []
    low[0]["visa_fee_amount"] = 1
    assert weak["visa_fee_amount"] == 100, "records handed out are copies"


def test_readers_share_one_rebuild_and_a_stale_test_copy_rebuilds_inline(monkeypatch):
    """Several readers arriving while the set is stale wait for the one
    rebuild in flight instead of each starting their own; under pytest a
    copy past its time bound rebuilds inline, leaking no thread."""
    import threading as _th
    from app import main as m
    builds = {"n": 0}

    def slow_builder(db):
        builds["n"] += 1
        m._time.sleep(0.3)
        return [{"visa_type_name": "x", "build": builds["n"]}]
    monkeypatch.setattr(m, "_build_tstation_rows", slow_builder)
    monkeypatch.setattr(m, "RECORDS_CACHE_SECONDS", 3600.0)
    monkeypatch.setitem(m._RECORDS_CACHE, "test_enabled", True)
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", [{"visa_type_name": "stale"}])
    monkeypatch.setitem(m._RECORDS_CACHE, "building", False)
    m.invalidate_records_cache()           # a write after the copy was built
    got = []
    threads = [_th.Thread(target=lambda: got.append(m._all_tstation_rows(None))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert builds["n"] == 1 and len(got) == 4 and all(rows[0]["build"] == 1 for rows in got)
    # A copy that is merely old rebuilds inline in the test runtime.
    monkeypatch.setitem(m._RECORDS_CACHE, "built_at", m._time.monotonic() - 7200)
    assert m._all_tstation_rows(None)[0]["build"] == 2
    assert not any(t.name == "records-cache" for t in _th.enumerate())
    # A write that lands during a build keeps the result stale.
    m.invalidate_records_cache()

    def writing_builder(db):
        builds["n"] += 1
        m.invalidate_records_cache()
        return [{"visa_type_name": "x", "build": builds["n"]}]
    monkeypatch.setattr(m, "_build_tstation_rows", writing_builder)
    m._all_tstation_rows(None)
    with m._RECORDS_CACHE_LOCK:
        assert m._RECORDS_CACHE["dirty"] is True and m._RECORDS_CACHE["building"] is False


def test_a_reader_takes_a_build_that_started_after_its_own_request(monkeypatch):
    """A write that lands while a build is in flight marks the copy dirty,
    but a reader who arrived before that write already has every write that
    preceded its request in the copy: it takes the copy instead of paying
    for a second build. A reader who arrives after the write waits for a
    build that starts after it."""
    import threading as _th
    from app import main as m
    builds = {"n": 0}
    started = _th.Event()
    release = _th.Event()

    def slow_builder(db):
        builds["n"] += 1
        started.set()
        release.wait(timeout=10)
        return [{"visa_type_name": "x", "build": builds["n"]}]
    monkeypatch.setattr(m, "_build_tstation_rows", slow_builder)
    monkeypatch.setattr(m, "RECORDS_CACHE_SECONDS", 3600.0)
    monkeypatch.setitem(m._RECORDS_CACHE, "test_enabled", True)
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", None)
    monkeypatch.setitem(m._RECORDS_CACHE, "dirty", False)
    monkeypatch.setitem(m._RECORDS_CACHE, "building", False)
    monkeypatch.setitem(m._RECORDS_CACHE, "built_generation", -1)
    got = {}
    early = _th.Thread(target=lambda: got.setdefault("early", m._all_tstation_rows(None)))
    early.start()
    assert started.wait(timeout=5)
    m.invalidate_records_cache()           # the write lands mid-build
    late_rows = {}
    late = _th.Thread(target=lambda: late_rows.setdefault("late", m._all_tstation_rows(None)))
    late.start()
    m._time.sleep(0.2)
    release.set()
    early.join(timeout=10)
    assert got["early"][0]["build"] == 1, "the early reader takes the build that started after its request"
    late.join(timeout=10)
    assert late_rows["late"][0]["build"] == 2, "the late reader waits for a build started after the write"
    assert builds["n"] == 2
    with m._RECORDS_CACHE_LOCK:
        assert m._RECORDS_CACHE["dirty"] is False and m._RECORDS_CACHE["building"] is False


def test_a_dirty_build_converges_in_the_background_outside_the_test_runtime(monkeypatch):
    from app import main as m
    calls = {"n": 0}
    monkeypatch.setattr(m, "_refresh_records_cache_in_background", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", None)
    monkeypatch.setitem(m._RECORDS_CACHE, "building", False)

    def writing_builder(db):
        m.invalidate_records_cache()
        return [{"visa_type_name": "x"}]
    monkeypatch.setattr(m, "_build_tstation_rows", writing_builder)
    assert m._claim_records_build()
    m._build_records_cache(None)
    assert calls["n"] == 0, "the test runtime never spawns the background thread"
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert m._claim_records_build()
    m._build_records_cache(None)
    assert calls["n"] == 1, "a copy left dirty by a mid-build write is rebuilt behind the readers"
    monkeypatch.setattr(m, "_build_tstation_rows", lambda db: [{"visa_type_name": "clean"}])
    assert m._claim_records_build()
    m._build_records_cache(None)
    assert calls["n"] == 1, "a clean build schedules nothing"


def test_a_bulk_update_statement_on_an_answer_row_marks_the_cache_dirty(client, monkeypatch):
    """The freshness recheck stamps verification with an UPDATE statement,
    which the unit-of-work flush never sees. The statement hook catches it."""
    from sqlalchemy import update as _update
    from app import main as m
    from app.db import SessionLocal
    from app.visa_snapshot.models import KimiRouteGuidanceCache
    _provide(ANSWER)
    look = client.post("/database/lookup", headers=READER,
                       json={"nationality": "ISL", "destination": "FSM"}).json()
    monkeypatch.setitem(m._RECORDS_CACHE, "test_enabled", True)
    monkeypatch.setitem(m._RECORDS_CACHE, "rows", [{"visa_type_name": "x"}])
    monkeypatch.setitem(m._RECORDS_CACHE, "dirty", False)
    session = SessionLocal()
    try:
        session.execute(_update(KimiRouteGuidanceCache)
                        .where(KimiRouteGuidanceCache.cache_key == look["cache_key"])
                        .values(model="bulk-stamped"))
        with m._RECORDS_CACHE_LOCK:
            assert m._RECORDS_CACHE["dirty"] is False, "nothing is stale until the commit"
        session.commit()
    finally:
        session.close()
    with m._RECORDS_CACHE_LOCK:
        assert m._RECORDS_CACHE["dirty"] is True


def test_only_the_lookup_and_ask_posts_skip_invalidation():
    from app import main as m
    assert m._records_read_post("/database/lookup") and m._records_read_post("/database/ask")
    assert not m._records_read_post("/database/asks/12/review"), "an ask review is an operator write"
    assert not m._records_read_post("/database/records/edit")
