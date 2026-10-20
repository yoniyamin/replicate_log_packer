"""Assemble the package: condensed attachment + prompt + findings.json, zipped."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from logpacker.analysis.pipeline import JobFiles
from logpacker.pack.condense import condense
from logpacker.pack.focus import resolve_focus
from logpacker.pack.presets import ATTACHMENT_SIZES, PackOptions, estimate_tokens
from logpacker.pack.prompt import build_prompt


def build_package(files: JobFiles, options: PackOptions) -> dict:
    report = json.loads(files.report.read_text(encoding="utf-8"))
    pack_dir = files.root / "pack"
    pack_dir.mkdir(exist_ok=True)

    name = _attachment_name(report)
    attachment = _cached_attachment(files, pack_dir / options.attachment, name, options.attachment)
    attachment_path = pack_dir / attachment["file"]

    focus, reason = resolve_focus(report, options.focus)
    prompt = build_prompt(report, attachment, options, focus, reason)

    (pack_dir / "PROMPT.md").write_text(prompt["text"], encoding="utf-8")
    findings = _findings(report) if options.include_findings else None
    if findings is not None:
        (pack_dir / "findings.json").write_text(json.dumps(findings, indent=1, ensure_ascii=False), encoding="utf-8")

    zip_path = files.root / "package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(pack_dir / "PROMPT.md", "PROMPT.md")
        zf.write(attachment_path, name)
        if findings is not None:
            zf.write(pack_dir / "findings.json", "findings.json")

    return {
        "prompt": prompt,
        "attachment": attachment,
        "findings_bytes": (pack_dir / "findings.json").stat().st_size if findings is not None else 0,
        "zip_bytes": zip_path.stat().st_size,
    }


def _cached_attachment(files: JobFiles, folder: Path, name: str, size: str) -> dict:
    """The attachment depends only on the size choice, so build it once per size."""
    meta_path = folder / "attachment.json"
    if meta_path.exists() and (folder / name).exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    templates = json.loads(files.templates.read_text(encoding="utf-8"))
    attachment = condense(files.sanitized, templates, folder / name, ATTACHMENT_SIZES[size])
    attachment |= {"name": name, "file": f"{size}/{name}", "tokens": estimate_tokens(attachment["bytes"])}
    meta_path.write_text(json.dumps(attachment), encoding="utf-8")
    return attachment


def _attachment_name(report: dict) -> str:
    task = report["metadata"].get("task") or Path(report["input"]["filename"]).stem
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", task).strip("_") or "task"
    return f"{safe}_sanitized.log"


def _findings(report: dict) -> dict:
    """Everything the parser found (latency series is already downsampled)."""
    return {
        "generated_by": "LogPacker",
        "note": "Computed by a deterministic parser over the full sanitized log.",
        "input": report["input"],
        "task": report["metadata"],
        "stats": report["stats"],
        "runs": report["runs"],
        "issues": report["issues"],
        "latency": report["latency"],
        "signals": report["signals"]["signals"],
        "repeated_patterns": report["repetition"],
        "sanitization": report["privacy"],
        "data_quality": report["quality"],
    }
