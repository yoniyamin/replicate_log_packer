"""The report collectors on the synthetic log (see tools/make_sample_log.py)."""

import shutil

import pytest

from logpacker.analysis.apply import ApplyCollector
from logpacker.analysis.latency_patterns import plateaus, spikes
from logpacker.analysis.pipeline import JobFiles, analyze
from logpacker.ingest.lines import LineParser
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


def test_primary_key_logged_under_renamed_target_table():
    lines = [
        "00304021: 2026-09-28T17:56:41 [TARGET_APPLY    ]T:  Primary key ID used for table 'ODS.T_COPY'  (oracle_endpoint_table.c:768)",
        "00304021: 2026-09-28T17:58:37 [TARGET_APPLY    ]T:  Going to start applying bulk changes  (bulk_apply.c:1)",
        "00304021: 2026-09-28T17:58:37 [TARGET_APPLY    ]T:  Start applying of 'UPDATE (3)' events for table 'COLVIR'.'T_SRC' (1).  (bulk_apply.c:2929)",
        "00304021: 2026-09-28T17:58:37 [TARGET_APPLY    ]T:  Going to run update statement, from seq 1 to seq 5, statement "
        "'UPDATE  /*+ PARALLEL(tempview) */  ( SELECT /*+ PARALLEL(\"user_02\".\"attrep_changes1\") PARALLEL(\"ODS\".\"T_COPY\") */ ...'  (x.c:1)",
        "00304021: 2026-09-28T17:58:40 [TARGET_APPLY    ]T:  Finished applying of 5 'UPDATE (3)' events for table 'COLVIR'.'T_SRC' (1).  (bulk_apply.c:1)",
    ]
    parser, collector = LineParser(), ApplyCollector()
    for n, text in enumerate(lines, 1):
        collector.feed(parser.parse(text, n, "a"))
    table = next(t for t in collector.result()["tables"] if t["table"] == "COLVIR.T_SRC")
    assert (table["has_pk"], table["pk_columns"], table["target"]) == (True, "ID", "ODS.T_COPY")


def _reload_cycle(start: int) -> list[tuple[int, str]]:
    """One cycle of a Full Load reload loop: segment 5 times out ~250 s in, segment 4 finishes ~1.4 h later."""
    tm, su, tl, so = "[TASK_MANAGER    ]I:", "[SOURCE_UNLOAD   ]", "[TARGET_LOAD     ]I:", "[SORTER          ]I:"
    t = "'dbo'.'big' (Id = 1)"
    return [
        (start, f"{tm}  Start initializing segmented table {t} by subtask 1. Start load timestamp 0  (r.c:1)"),
        (start, f"{tm}  Start loading segment #4 of 5 of table {t} by subtask 4. Start load timestamp 0  (r.c:1)"),
        (start, f"{tm}  Start loading segment #5 of 5 of table {t} by subtask 5. Start load timestamp 0  (r.c:1)"),
        (start + 250, f"{su}E:  RetCode: SQL_ERROR  SqlState: HYT00 NativeError: 30149 Message: The command has timed out. [1022502]  (a.c:1)"),
        (start + 250, f"{su}E:  Error executing source loop [1022506]  (s.c:1)"),
        (start + 250, f"{tm}  Reloading table 1 because subtask #5 that was loading segment 5 finished with error, and some data "
                      "from the segment was already loaded to the target  (r.c:2863)"),
        (start + 5000, f"{su}I:  Unload finished for segment #4 of segmented table {t}. 1000 rows sent.  (s.c:1)"),
        (start + 5000, f"{tl}  Load finished for segment #4 of segmented table {t}. 1000 rows received. 0 rows skipped. "
                       "Volume transferred 5000000.  (e.c:1)"),
        (start + 5000, f"{so}  Reload for table Id 1 is requested  (sorter_transaction.c:2519)"),
    ]


def test_full_load_reload_loop():
    from datetime import datetime, timedelta

    from logpacker.analysis.fullload import FullLoadCollector

    base = datetime(2026, 7, 23, 12, 0, 0)
    lines = [(s, text) for k in range(3) for s, text in _reload_cycle(k * 5000)]
    parser, collector = LineParser(), FullLoadCollector()
    for n, (s, text) in enumerate(lines, 1):
        ts = (base + timedelta(seconds=s)).isoformat()
        collector.feed(parser.parse(f"00011256: {ts} {text}", n, "a"))
    fl = collector.result()
    table = fl["tables"][0]
    loop = table["reload_loop"]
    assert (loop["reloads"], loop["failing_segment"], loop["failing_segment_reloads"]) == (3, 5, 3)
    assert loop["seconds_to_failure"] == {"min": 250, "median": 250, "max": 250} and loop["constant_failure_time"]
    assert "HYT00" in loop["cause"] and loop["cause_line"] == 4  # the first error, not the unwind after it
    assert loop["largest_redone_segment"] == {"segment": 4, "times": 3, "rows": 1000, "volume": 5000000}
    assert loop["executed_by_sorter"] == 3 and loop["wait_seconds"] == {"min": 4750, "max": 4750}
    first = fl["reloads"][0]
    assert (first["executed_line"], first["wait_seconds"]) == (9, 4750) and "_t" not in first
    insight = next(i for i in fl["insights"] if i["title"] == "Reload loop on dbo.big")
    assert "near-constant" in insight["message"] and "not a separate trigger" in insight["message"]


def _samples(values):
    return [(f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}", i + 1, 0.1 * v, v, 0.9 * v, float(i)) for i, v in enumerate(values)]


def test_spike_and_plateau_detection():
    flat = [1.0] * 20
    assert spikes(_samples(flat))["count"] == 0
    result = spikes(_samples(flat + [10.0] + flat))
    assert result["count"] == 1 and result["top"][0]["multiplier"] == 10.0
    plateau = plateaus(_samples([1.0] * 30 + [5.0] * 15 + [1.0] * 30))
    assert plateau["count"] == 1 and plateau["top"][0]["points"] >= 10
