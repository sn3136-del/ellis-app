"""guard-20260912 T2: the three surface projections are extractions, not
rewrites. Each is proved against the code it was lifted from."""
import json
import re
from pathlib import Path

from app import main as served
from app.visa_snapshot import assistant, kimi_primary, row_projection

from tests import _guard_fixtures as gf

APP = Path(__file__).resolve().parents[1] / "app"


def _sources():
    for path in APP.rglob("*.py"):
        yield path, path.read_text(encoding="utf-8")


def test_reader_projection_is_the_only_reader_composition():
    """The composition apply_portal_fallback(apply_verified_overrides(...))
    exists once, inside kimi_primary.reader_projection. Every reader path
    (cached, fresh, staged detail, nearest approximation) calls it."""
    hits = []
    for path, text in _sources():
        for m in re.finditer(r"apply_portal_fallback\(apply_verified_overrides\(", text):
            hits.append((path.name, text[:m.start()].count("\n") + 1))
    assert len(hits) == 1 and hits[0][0] == "kimi_primary.py", hits
    src = (APP / "visa_snapshot" / "kimi_primary.py").read_text(encoding="utf-8")
    body = src[src.index("def reader_projection("):src.index("def nearest_cached_answer(")]
    assert "apply_portal_fallback(apply_verified_overrides(_result(" in body
    assert "health_context.apply" in body
    # The four reader paths route through it.
    for name in ("def nearest_cached_answer", "def _get_route_guidance_locked"):
        block = src[src.index(name):]
        assert "reader_projection(" in block[:12000], name
    assert src.count("reader_projection(") >= 6


def test_records_projection_is_byte_identical_for_twelve_seeded_routes(db, monkeypatch, tmp_path):
    """The records builder output for twelve real routes, captured at HEAD
    7010800 before the body was lifted into row_projection, is reproduced
    exactly through app.main._build_tstation_rows after the extraction.

    Golden history (every later task that deliberately moves a fixture row
    regenerates the file and records the delta here):
      T2  captured at 7010800, byte identical after the extraction
      T6  HKG to KOR (No visa needed): held, Low, withheld, one contradiction
          naming the K-ETA list (rule 3 of permission_eligibility)
      T7  HKG to KOR gains the second contradiction: an unconditional
          exemption cited on the K-ETA portal (serve_time_invariants)
      Review repair: HKG -> KOR returns to the original T2 snapshot because
          membership and URL alone do not establish a mandatory authorization.
          Every other fixture row remains identical."""
    from app.visa_snapshot import verified_overrides as vo
    monkeypatch.setenv("ELLIS_OPERATOR_OVERRIDES", str(tmp_path / "operator.json"))
    vo.reload()
    try:
        golden = json.loads(gf.GOLDEN.read_text(encoding="utf-8"))
        with gf.seeded(db) as keys:
            rows = [r for r in served._build_tstation_rows(db) if r["_cache_key"] in keys]
        got = gf.normalised(rows)
        assert len(got) == len(golden) == 18
        by_key = lambda items: sorted(items, key=lambda r: (r["_cache_key"], str(r.get("_product_index")), str(r.get("visa_type_name"))))
        for a, b in zip(by_key(got), by_key(golden)):
            assert a == b, (a["_cache_key"], a.get("visa_type_name"),
                            {k: (a.get(k), b.get(k)) for k in a if a.get(k) != b.get(k)})
    finally:
        vo.reload()


def test_records_projection_is_what_main_calls():
    src = (APP / "main.py").read_text(encoding="utf-8")
    block = src[src.index("def _collect_tstation_rows("):src.index("def _dedupe_dataset_rows(")]
    assert "records_projection(db, r, route)" in block
    # The lifted body no longer lives in main.
    assert "tstation.records_for_route(" not in block
    assert "_source_check" not in block


def test_fact_payload_extracted():
    src = (APP / "visa_snapshot" / "assistant.py").read_text(encoding="utf-8")
    block = src[src.index("def compose_reply_ex("):]
    assert "facts = fact_payload(out)" in block
    out = {"guidance": {"disposition": "VISA_REQUIRED", "government_fee": {"amount": 25, "currency": "USD"},
                        "visa_products": [{"type": "e-visa"}], "unrelated": "x", "confidence": "high"},
           "route": {"nationality": "HKG", "destination": "VNM"},
           "special_policies": [{"id": "p"}]}
    facts = assistant.fact_payload(out)
    assert facts["disposition"] == "VISA_REQUIRED" and facts["government_fee"]["amount"] == 25
    assert facts["route"] == out["route"] and facts["special_policies"] == [{"id": "p"}]
    assert "unrelated" not in facts and "confidence" not in facts
    held = assistant.fact_payload(dict(out, held=True))
    assert set(held) == {"route", "held"}
