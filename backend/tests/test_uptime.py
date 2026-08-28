"""The availability record must be readable where the acceptance runs: the
probe cron writes monthly CSVs, /health/uptime reads them back."""
from fastapi.testclient import TestClient

from app.main import app


def test_uptime_reads_the_probe_log(tmp_path, monkeypatch):
    (tmp_path / "2026-08.csv").write_text(
        "timestamp,code,ms\n"
        "2026-08-28T00:00:00Z,200,64\n"
        "2026-08-28T00:01:00Z,200,70\n"
        "2026-08-28T00:02:00Z,503,10002\n",
        encoding="utf-8")
    (tmp_path / "incidents.log").write_text(
        "2026-08-28T00:02:00Z public=503 local=200 action=none\n",
        encoding="utf-8")
    monkeypatch.setenv("ELLIS_UPTIME_DIR", str(tmp_path))
    r = TestClient(app).get("/health/uptime")
    assert r.status_code == 200
    body = r.json()
    assert body["probe_interval_seconds"] == 60
    assert body["incidents"] == 1
    month = body["months"][0]
    assert month["month"] == "2026-08"
    assert month["probes"] == 3 and month["ok"] == 2
    assert month["availability_pct"] == 66.6667
    assert month["median_latency_ms"] == 70


def test_uptime_with_no_log_is_empty_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ELLIS_UPTIME_DIR", str(tmp_path / "nowhere"))
    r = TestClient(app).get("/health/uptime")
    assert r.status_code == 200
    assert r.json()["months"] == []
