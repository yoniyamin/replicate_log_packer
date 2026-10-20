import gzip
import json
import shutil
import zipfile
from pathlib import Path

from logpacker.analysis.pipeline import JobFiles, analyze
from logpacker.pack.bundle import build_package
from logpacker.pack.presets import PackOptions
from logpacker.sanitize import SanitizeOptions

SECRETS = ("dbprod01", "contoso", "Contoso", "repl_admin", "S3cr3t", "10.20.30.40", "jsmith", "ORAPRD", "etl_svc")


def run(tmp_path: Path, src: Path, name: str | None = None) -> tuple[JobFiles, dict]:
    files = JobFiles(tmp_path / "job")
    files.root.mkdir()
    shutil.copyfile(src, files.upload)
    return files, analyze(files, name or src.name, SanitizeOptions(), 1 << 30)


def test_analysis_finds_patterns(tmp_path, sample_log):
    _, report = run(tmp_path, sample_log)
    meta = report["metadata"]
    assert meta["task"] == "SALES_TO_SNOW" and meta["version"] == "2025.5.0.247"
    assert meta["source_endpoint"] == "Oracle" and meta["target_endpoint"] == "Snowflake"
    assert report["stats"]["errors"] > 0 and report["latency"]["samples"] > 5
    codes = {g["code"] for g in report["issues"]["groups"]}
    assert {"SqlState:57014/604", "ORA-03113"} <= codes
    signal_ids = {s["id"] for s in report["signals"]["signals"]}
    assert "one_by_one" in signal_ids
    assert report["runs"]["count"] == 1
    assert report["privacy"]["lines_changed"] > 0


def test_sanitized_outputs_contain_no_originals(tmp_path, sample_log):
    files, _ = run(tmp_path, sample_log)
    result = build_package(files, PackOptions(attachment="small"))
    with zipfile.ZipFile(files.root / "package.zip") as zf:
        blob = "\n".join(zf.read(n).decode("utf-8") for n in zf.namelist())
    blob += files.sanitized.read_text(encoding="utf-8") + files.report.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in blob, secret
    assert result["prompt"]["tokens"] <= 5200


def test_condensed_attachment_keeps_every_error(tmp_path, sample_log):
    files, report = run(tmp_path, sample_log)
    result = build_package(files, PackOptions(attachment="small"))
    att = result["attachment"]
    text = (files.root / "pack" / att["file"]).read_text(encoding="utf-8")
    assert att["mode"] == "condensed" and att["lines_kept"] < att["lines_in"]
    assert "similar lines omitted" in text
    error_lines = sum(1 for line in text.splitlines() if "]E:" in line)
    assert error_lines == report["stats"]["errors"]


def test_size_target_is_respected(tmp_path, sample_log):
    from logpacker.pack import condense as condense_mod
    files, _ = run(tmp_path, sample_log)
    templates = json.loads(files.templates.read_text(encoding="utf-8"))
    out = tmp_path / "tiny.log"
    stats = condense_mod.condense(files.sanitized, templates, out, 6_000)
    assert out.stat().st_size <= 6_200 and stats["truncated"]


def test_gzip_and_zip_inputs(tmp_path, sample_log):
    gz = tmp_path / "log.gz"
    gz.write_bytes(gzip.compress(sample_log.read_bytes()))
    (tmp_path / "a").mkdir()
    _, rep_gz = run(tmp_path / "a", gz)
    zp = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.write(sample_log, "logs/reptask_SALES_TO_SNOW.log")
        zf.writestr("readme.txt", "ignore me")
    (tmp_path / "b").mkdir()
    files, rep_zip = run(tmp_path / "b", zp)
    assert rep_gz["stats"]["errors"] == rep_zip["stats"]["errors"] > 0
    assert files.sanitized.read_text(encoding="utf-8").startswith("===== LogPacker: file")
