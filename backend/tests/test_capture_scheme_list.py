"""guard-20260912 T5: the capture script reads a list, refuses a wrong host
and never writes the registry file."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import capture_scheme_list as capture  # noqa: E402

from app.visa_snapshot import fetching, scheme_registry as sr  # noqa: E402
from app.visa_snapshot.fetching import FetchResult  # noqa: E402

PAGE = ROOT / "tests" / "fixtures" / "keta_page_2026-09-12.txt"
KETA = "https://www.k-eta.go.kr/portal/guide/viewetaalification.do"
SEED = ROOT.parent / "data" / "database_seed" / "scheme_lists.json"
START, END = "Check country and period of stay.", "Applicant"


@pytest.fixture
def no_network(monkeypatch):
    def refuse(url, **kw):
        raise AssertionError("the test must not reach the network")
    fetching.set_fetcher(refuse)
    yield
    fetching.set_fetcher(None)


def test_capture_reads_the_list_from_the_page(no_network, tmp_path):
    out = tmp_path / "review.json"
    rc = capture.main(["--id", "kor_keta", "--destination", "KOR", "--url", KETA, "--start", START,
                       "--end", END, "--out", str(out), "--fixture", str(PAGE)])
    assert rc == 0
    review = json.loads(out.read_text(encoding="utf-8"))
    assert review["outcome"] == "captured"
    assert "JPN" in review["proposed_eligible_nationalities"]
    assert "IDN" not in review["proposed_eligible_nationalities"]
    assert review["quote_sha256"] == sr.quote_hash(review["list_quote"])
    assert review["list_quote"].startswith("Check Application") or "ALBANIA" in review["list_quote"][:60]


def test_capture_refuses_a_non_competent_host(tmp_path):
    body = PAGE.read_text(encoding="utf-8")
    # The fetch lands on a host the destination does not own (a redirect off
    # the destination's domain): the FINAL hostname is what is judged.
    fetching.set_fetcher(lambda url, **kw: FetchResult(
        requested_url=url, ok=True, final_url="https://travel.state.gov/keta-mirror",
        final_hostname="travel.state.gov", http_status=200, content_text=body, retrieved_at="2026-09-12T00:00:00Z"))
    try:
        out = tmp_path / "review.json"
        rc = capture.main(["--id", "kor_keta", "--destination", "KOR", "--url", KETA, "--start", START,
                           "--end", END, "--out", str(out)])
    finally:
        fetching.set_fetcher(None)
    assert rc == 2
    review = json.loads(out.read_text(encoding="utf-8"))
    assert review["outcome"] == "refused_non_competent_host"
    assert review["list_quote"] == "" and review["proposed_eligible_nationalities"] == []


def test_capture_never_writes_the_registry_file(no_network, tmp_path, monkeypatch):
    before = hashlib.sha256(SEED.read_bytes()).hexdigest()
    alt = tmp_path / "scheme_lists.json"
    alt.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(alt))
    sr.reload()
    try:
        out = tmp_path / "review.json"
        capture.main(["--id", "kor_keta", "--destination", "KOR", "--url", KETA, "--start", START,
                      "--end", END, "--out", str(out), "--fixture", str(PAGE), "--emit-entry",
                      "--compare-entry", "kor_keta"])
    finally:
        sr.reload()
    assert hashlib.sha256(SEED.read_bytes()).hexdigest() == before
    assert alt.read_text(encoding="utf-8") == "[]"
    assert not any(p.name.startswith(".capture-") for p in tmp_path.iterdir())


def test_changed_capture_is_reported_stale_and_never_written(no_network, tmp_path, monkeypatch):
    alt = tmp_path / "scheme_lists.json"
    stored = next(e for e in json.loads(SEED.read_text(encoding="utf-8")) if e["id"] == "kor_keta")
    changed = dict(stored, eligible_nationalities=[n for n in stored["eligible_nationalities"] if n != "JPN"],
                   list_quote=stored["list_quote"].replace("JAPAN\n", "").replace("| JAPAN", ""))
    changed["quote_sha256"] = sr.quote_hash(changed["list_quote"])
    alt.write_text(json.dumps([changed]), encoding="utf-8")
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(alt))
    sr.reload()
    try:
        out = tmp_path / "review.json"
        capture.main(["--id", "kor_keta", "--destination", "KOR", "--url", KETA, "--start", START,
                      "--end", END, "--out", str(out), "--fixture", str(PAGE), "--compare-entry", "kor_keta"])
        review = json.loads(out.read_text(encoding="utf-8"))
        assert review["comparison"]["result"] == "changed" and review["comparison"]["stale"] is True
        assert review["comparison"]["added"] == ["JPN"]
        # The stored list is untouched.
        sr.reload()
        assert "JPN" not in next(e for e in sr.entries() if e["id"] == "kor_keta")["eligible_nationalities"]
        assert json.loads(alt.read_text(encoding="utf-8")) == [changed]
    finally:
        sr.reload()


def test_emitted_entry_passes_the_registry_gates(no_network, tmp_path, monkeypatch, capsys):
    out = tmp_path / "review.json"
    capture.main(["--id", "kor_keta", "--destination", "KOR", "--url", KETA, "--start", START, "--end", END,
                  "--out", str(out), "--fixture", str(PAGE), "--emit-entry", "--scheme-kind", "eta",
                  "--name", "K-ETA", "--requirement-detail", "eta_electronic_authorization",
                  "--pattern", r"\bk[ -]?eta\b", "--portal-marker", "k-eta.go.kr"])
    entry = json.loads(capsys.readouterr().out)
    alt = tmp_path / "scheme_lists.json"
    alt.write_text(json.dumps([entry]), encoding="utf-8")
    monkeypatch.setenv("ELLIS_SCHEME_LISTS", str(alt))
    sr.reload()
    try:
        assert sr.store_status()["errors"] == []
        assert sr.for_destination("KOR")[0]["list_state"] == "established"
    finally:
        sr.reload()
