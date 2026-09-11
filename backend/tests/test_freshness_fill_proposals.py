"""The monitor proposes values for empty cells.

A contradiction check shows the model only the stored non-empty fields, so a
page that states a value the record LACKS was invisible: 196 served rows read
"Not publicly available" for a cell the bound official page states (owner
example: Australia to Russia validity). The sweep now names the empty
required cells in the same model call, validates each stated value the way a
dispute is validated, files one fill issue per record and page, and never
applies it. The value reaches the record only when an operator accepts the
issue, and that acceptance is stored as the monitor's reading, never as a
human verification and never as a public edit.
"""
import importlib.util
import json
import threading
import time
from pathlib import Path

from app.visa_snapshot import fetching, freshness, tstation
from app.visa_snapshot.fetching import FetchResult
from app.visa_snapshot.models import DatabaseIssueReport, KimiRouteGuidanceCache
from .test_freshness import db, _clean, _seed, ROUTE, STALE_JPN  # noqa: F401 - shared fixtures
from .test_freshness_comparison_reuse import h, seed as seed_reuse, URL as REUSE_URL, TEXT as REUSE_TEXT  # noqa: F401

URL = "https://www.mofa.go.jp/j_info/visit/visa/index.html"
PRODUCT = "Single-entry Temporary Visitor"
VALIDITY_QUOTE = ("The single-entry temporary visitor visa is valid for 3 months "
                  "from the date of issue.")
PAGE = FetchResult(
    requested_url=URL, ok=True, final_url=URL, final_hostname="www.mofa.go.jp",
    http_status=200,
    content_text=("Chinese nationals must obtain a visa for tourism in Japan. "
                  "Visa fee: single entry 200 CNY. " + VALIDITY_QUOTE + " "
                  "Single-entry temporary visitor visas for tourism permit a stay of 90 days."),
    content_hash="fill-page", retrieved_at="2026-09-11T00:00:00Z")

# The stored answer is complete except the product's validity.
GAPPED = dict(STALE_JPN, visa_products=[dict(STALE_JPN["visa_products"][0], validity=None)])
STATED = [{"field": "validity", "product_type": PRODUCT, "value": "3 months",
           "quote": VALIDITY_QUOTE}]
ADMIN = {"authorization": "Bearer admin-token", "x-org-id": "org-b", "x-user-id": "op-1"}


def _answer(stated):
    return {"page_relevant": True, "page_is_nationality_specific": True, "consistent": True,
            "corrected_fields": {}, "evidence": {}, "stated_fields": stated, "note": "consistent"}


def _capture(answer):
    calls = []

    def provider(system, user):
        calls.append((system, json.loads(user)))
        return dict(answer)
    freshness.set_provider(provider)
    return calls


def _fills(db):
    return [i for i in db.query(DatabaseIssueReport).all()
            if (i.proposal or {}).get("kind") == "fill"]


def _fill_issue(db, row):
    issue = DatabaseIssueReport(
        org_id="platform", cache_key=row.cache_key, route=dict(ROUTE), field="validity",
        note=f"Automatic source check against {URL}: validity: page says \"3 months\" (quote: {VALIDITY_QUOTE})",
        reported_by="freshness_monitor", status="open",
        proposal={"kind": "fill", "outcome": "checked", "source_url": URL,
                  "checked_at": "2026-09-11T00:00:00Z", "product_type": PRODUCT,
                  "fields": {"validity": {"page_says": "3 months", "record_holds": None,
                                          "quote": VALIDITY_QUOTE}}})
    db.add(issue)
    db.commit()
    return issue


def test_a_page_stating_a_validity_the_product_lacks_files_one_fill_issue(db):
    _seed(db, GAPPED)
    fetching.set_fetcher(lambda url, timeout_seconds=0: PAGE)
    calls = _capture(_answer(STATED))
    out = freshness.recheck_route(db, ROUTE)
    assert out["fill_proposed"] == 1 and out["fill_rejected"] == 0
    system, payload = calls[0]
    assert payload["empty_fields"] == [{"field": "validity", "product_type": PRODUCT}]
    assert system == freshness._SYSTEM + freshness._FILL_SUPPLEMENT
    issues = _fills(db)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.reported_by == "freshness_monitor" and issue.status == "open"
    assert issue.field == "validity"
    assert issue.proposal["product_type"] == PRODUCT
    assert issue.proposal["source_url"] == URL
    assert issue.proposal["fields"] == {"validity": {"page_says": "3 months", "record_holds": None,
                                                     "quote": VALIDITY_QUOTE}}
    # Never applied: the stored product still lacks the validity.
    row = db.query(KimiRouteGuidanceCache).one()
    assert row.guidance["visa_products"][0]["validity"] is None
    # A fill is not a dispute: it neither grades the answer down nor blocks renewal.
    assert freshness.active_disputed_fields(db, row.cache_key) == []


def test_a_quote_not_on_the_page_is_dropped_and_counted(db):
    _seed(db, GAPPED)
    fetching.set_fetcher(lambda url, timeout_seconds=0: PAGE)
    _capture(_answer([dict(STATED[0], quote="The visa is valid for 3 months, said no page.")]))
    out = freshness.recheck_route(db, ROUTE)
    assert out["fill_rejected"] == 1 and out["fill_proposed"] == 0
    assert _fills(db) == []


def test_a_value_the_quote_does_not_state_is_dropped_and_counted(db):
    """The quote is on the page and says 3 months; the value says 6."""
    _seed(db, GAPPED)
    fetching.set_fetcher(lambda url, timeout_seconds=0: PAGE)
    _capture(_answer([dict(STATED[0], value="6 months")]))
    out = freshness.recheck_route(db, ROUTE)
    assert out["fill_rejected"] == 1 and out["fill_proposed"] == 0
    assert _fills(db) == []


def test_a_field_the_record_holds_cannot_be_filled(db):
    """The model may only answer the listed empty cells: the product's fee is
    stored, so a stated fee is dropped even with a good quote."""
    _seed(db, GAPPED)
    fetching.set_fetcher(lambda url, timeout_seconds=0: PAGE)
    _capture(_answer(STATED + [{"field": "fee", "product_type": PRODUCT,
                                "value": {"amount": 200, "currency": "CNY"},
                                "quote": "Visa fee: single entry 200 CNY."}]))
    out = freshness.recheck_route(db, ROUTE)
    assert out["fill_proposed"] == 1 and out["fill_rejected"] == 1
    assert [i.field for i in _fills(db)] == ["validity"]


def test_a_second_run_does_not_duplicate_the_fill(db):
    row = _seed(db, GAPPED)
    fetching.set_fetcher(lambda url, timeout_seconds=0: PAGE)
    _capture(_answer(STATED))
    first = freshness.recheck_row(db, row, today="2026-09-11T08:00:00+00:00")
    second = freshness.recheck_row(db, row, today="2026-09-12T08:00:00+00:00")
    assert first["fill_proposed"] == 1
    # The model was asked again on the new day and the same fill was found
    # already waiting, so nothing new was filed.
    assert second["model_comparisons"] == 1 and second["fill_proposed"] == 0
    assert len(_fills(db)) == 1
    # A dismissed identical fill is not filed again either.
    _fills(db)[0].status = "dismissed"
    db.commit()
    third = freshness.recheck_row(db, row, today="2026-09-13T08:00:00+00:00")
    assert third["fill_proposed"] == 0
    assert len(_fills(db)) == 1 and _fills(db)[0].status == "dismissed"


def test_a_record_with_no_empty_cells_sends_the_unchanged_prompt(db):
    """The contradiction behaviour is provably untouched: a complete record
    sends the original system prompt byte for byte and no empty_fields, and
    a stray stated_fields answer files nothing."""
    _seed(db, STALE_JPN)
    fetching.set_fetcher(lambda url, timeout_seconds=0: PAGE)
    calls = _capture(_answer(STATED))
    out = freshness.recheck_route(db, ROUTE)
    system, payload = calls[0]
    assert "empty_fields" not in payload
    assert system == freshness._SYSTEM
    assert "stated_fields" not in system and "EMPTY CELLS" not in system
    assert system.endswith("say\npage_is_nationality_specific false and correct nothing nationality-specific.")
    assert out["fill_proposed"] == 0 and out["fill_rejected"] == 0
    assert _fills(db) == []


def test_a_visa_free_route_is_never_asked_for_a_visa_validity(db):
    exempt = {"disposition": "VISA_EXEMPT", "requirement_detail": "unconditional_visa_free",
              "permitted_stay": None, "permitted_stay_days": None,
              "application_channel": "not_required", "source_url": URL}
    empties = freshness._empty_fields(dict(ROUTE), exempt, None)
    names = {e["field"] for e in empties}
    assert {"permitted_stay_days", "permitted_stay"} <= names
    assert not names & {"validity", "entry", "fee", "government_fee", "application_channel"}
    assert all("product_type" not in e for e in empties)


def test_accepting_a_fill_writes_a_product_override_the_record_shows_as_high(db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.visa_snapshot import verified_overrides as vo
    client = TestClient(app)
    row = _seed(db, GAPPED)
    # The route's verdict, channel and documents are already verified by a person.
    vo.append_operator_entry({
        "route": {"nationality": "CHN", "destination": "JPN", "travel_purpose": "tourism"},
        "verified_at": "2026-09-01", "verified_by": "Trip.com operations (op-0)",
        "verifier": "human", "source_url": URL,
        "note": ("Quote: Chinese nationals must obtain a visa for tourism in Japan. "
                 "Applications must be lodged through an accredited travel agency. "
                 "Required documents: passport."),
        "fields": {"disposition": "VISA_REQUIRED", "requirement_detail": "paper_visa",
                   "application_channel": "authorised_agent",
                   "required_documents": ["passport"]}}, guidance=row.guidance)
    issue = _fill_issue(db, row)
    ok = client.post(f"/database/issues/{issue.id}/accept-proposal", headers=ADMIN).json()
    assert ok["ok"] and ok["status"] == "corrected" and ok["fields"] == ["validity"]
    served, prov = vo.apply(dict(row.guidance), dict(ROUTE))
    assert served["visa_products"][0]["validity"] == "3 months"
    assert served["visa_products"][0]["fee"]["amount"] == 200          # siblings untouched
    proof = prov["field_provenance"]["visa_products"]
    assert proof["verifier"] == "ai"
    assert "freshness monitor" in proof["verified_by"] and "op-1" in proof["verified_by"]
    assert prov["field_provenance"]["disposition"]["verifier"] == "human"
    record = next(r for r in tstation.records_for_route(dict(ROUTE), served, prov)
                  if r["visa_type_name"] == PRODUCT)
    assert record["validity_duration"] == 3 and record["validity_unit"] == "Month"
    assert record["confidence_level"] == "High"
    db.refresh(issue)
    assert issue.status == "corrected" and issue.resolved_by == "op-1"
    vo.reload()


def test_a_fill_is_refused_once_the_cell_holds_a_value(db):
    """A value that arrived in the meantime makes this a difference for the
    normal stages, not a gap to fill."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    row = _seed(db, STALE_JPN)     # the product already carries "3 months"
    issue = _fill_issue(db, row)
    r = client.post(f"/database/issues/{issue.id}/accept-proposal", headers=ADMIN)
    assert r.status_code == 422 and "now holds a value" in r.json()["detail"]


def test_a_monitor_dispute_is_still_refused_by_accept_proposal(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    row = _seed(db, GAPPED)
    dispute = DatabaseIssueReport(
        org_id="platform", cache_key=row.cache_key, route=dict(ROUTE), field="government_fee",
        note="Automatic source check against " + URL, reported_by="freshness_monitor", status="open",
        proposal={"source_url": URL, "checked_at": "2026-09-11T00:00:00Z",
                  "fields": {"government_fee": {"page_says": {"amount": 715, "currency": "CNY"},
                                                "record_holds": {"amount": 200, "currency": "CNY"},
                                                "quote": "single entry 715 CNY"}}})
    db.add(dispute)
    db.commit()
    r = client.post(f"/database/issues/{dispute.id}/accept-proposal", headers=ADMIN)
    assert r.status_code == 422 and "not accepted wholesale" in r.json()["detail"]


def test_the_sweep_counts_fill_proposals(h, monkeypatch):
    import app.db
    row = seed_reuse(h)
    quote = "The visa fee is 3000 JPY per application."
    h.texts[REUSE_URL] = REUSE_TEXT + " " + quote
    h.answer["stated_fields"] = [{"field": "government_fee", "quote": quote,
                                  "value": {"amount": 3000, "currency": "JPY"}},
                                 {"field": "government_fee", "quote": "not on the page",
                                  "value": {"amount": 1, "currency": "JPY"}}]
    spec = importlib.util.spec_from_file_location("freshness_fill_sweep",
        Path(__file__).resolve().parents[1] / "scripts" / "freshness_sweep.py")
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    assert "fill_proposed" in sweep._COUNTS and "fill_rejected" in sweep._COUNTS
    monkeypatch.setattr(app.db, "SessionLocal", lambda: h.db)
    delta = sweep._check_route(row.cache_key, time.monotonic() + 30, threading.Event())
    assert delta["fill_proposed"] == 1 and delta["fill_rejected"] == 1
    assert h.calls[0]["empty_fields"][0] == {"field": "permitted_stay_days"}
