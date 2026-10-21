"""The report collectors on the synthetic log (see tools/make_sample_log.py)."""

import shutil

import pytest

from logpacker.analysis.latency_patterns import plateaus, spikes
from logpacker.analysis.pipeline import JobFiles, analyze
from logpacker.sanitize import SanitizeOptions


@pytest.fixture()
def report(tmp_path, sample_log):
    files = JobFiles(tmp_path / "job")
    files.root.mkdir()
    shutil.copyfile(sample_log, files.upload)
    return analyze(files, sample_log.name, SanitizeOptions(), 1 << 30)


def test_batches_follow_reason_first_ordering(report):
    b = report["apply"]["batches"]
    assert b["count"] == 400  # one per generated block
    assert set(b["closure_reasons"]) == {"TIM", "MEM"}
    assert b["closure_reasons"]["MEM"] == 134  # every third block
    assert all(x["statements"] == 1 and x["changes"] > 0 for x in b["list"])
    assert b["rps"]["measured_batches"] + b["rps"]["unknown_duration"] == 400


def test_tables_one_by_one_and_insights(report):
    a = report["apply"]
    orders = next(t for t in a["tables"] if t["table"] == "SALES.ORDERS")
    assert orders["insert"] == report["apply"]["batches"]["size_stats"]["total_changes"]
    assert len(a["one_by_one"]) == 9 and all(e["end_line"] for e in a["one_by_one"])
    titles = {i["title"] for i in a["insights"]}
    assert "Fell back to one-by-one apply" in titles and "Memory pressure closes batches" in titles


def test_full_load_lifecycle(report):
    fl = report["full_load"]
    s = fl["summary"]
    assert fl["available"] and s["completed"] and s["tables_total"] == 2
    assert s["tables_loaded"] == 2 and s["reload_events"] == 1 and s["tables_reloaded"] == 1
    orders = next(t for t in fl["tables"] if t["table"] == "SALES.ORDERS")
    assert orders["attempts"] == 2 and orders["rows_received"] == 14000
    assert {i["title"] for i in fl["insights"]} >= {"Full Load reload loops"}


def test_file_operations(report):
    f = report["file_ops"]
    assert f["count"] == 400 and f["total_bytes"] > 0
    assert all(x["file"].startswith("cdc") for x in f["files"])


def test_overview_cockpit_and_manifest(report):
    m = report["metadata"]
    assert m["log_closed"] and m["cdc_started"] and m["full_load_completed"]
    assert m["verbose_loggers"] == ["TARGET_APPLY"]
    assert any(e["event"] == "CDC started" for e in m["key_events"])
    tabs = {t["id"]: t["available"] for t in report["manifest"]["tabs"]}
    assert tabs == {"overview": True, "issues": True, "cockpit": True, "bulk": True, "fullload": True,
                    "fileops": True, "patterns": True, "privacy": True}
    recs = {r["title"] for r in report["cockpit"]["recommendations"]}
    assert any("one-by-one" in t for t in recs)
    assert 1 <= report["manifest"]["health"]["score"] <= 4


def _samples(values):
    return [(f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}", i + 1, 0.1 * v, v, 0.9 * v, float(i)) for i, v in enumerate(values)]


def test_spike_and_plateau_detection():
    flat = [1.0] * 20
    assert spikes(_samples(flat))["count"] == 0
    result = spikes(_samples(flat + [10.0] + flat))
    assert result["count"] == 1 and result["top"][0]["multiplier"] == 10.0
    plateau = plateaus(_samples([1.0] * 30 + [5.0] * 15 + [1.0] * 30))
    assert plateau["count"] == 1 and plateau["top"][0]["points"] >= 10
