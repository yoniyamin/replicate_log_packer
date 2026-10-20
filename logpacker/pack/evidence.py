"""Evidence sections for the prompt: pre-computed facts the model should not recompute.

Each builder returns a Section or None when there is nothing to say.
Lower `priority` numbers are dropped last when the prompt is over budget.
"""

from __future__ import annotations

from dataclasses import dataclass

from logpacker.pack.presets import PromptDetail


@dataclass
class Section:
    key: str
    text: str
    priority: int


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def overview(report: dict, _: PromptDetail, focus: str) -> Section:
    meta, stats, runs = report["metadata"], report["stats"], report["runs"]
    rows = [
        ["Task", meta.get("task") or "not found"],
        ["Replicate version", meta.get("version") or "not found"],
        ["Source endpoint", meta.get("source_endpoint") or "not found"],
        ["Target endpoint", meta.get("target_endpoint") or "not found"],
        ["Run mode", " / ".join(x for x in (meta.get("mode"), meta.get("start_mode")) if x) or "not found"],
        ["Time range", f"{stats['first_ts']} → {stats['last_ts']}"],
        ["Lines", f"{stats['lines']:,}"],
        ["Errors / warnings", f"{stats['errors']:,} / {stats['warnings']:,}"],
        ["Task runs in log", runs["count"]],
    ]
    text = "## Task overview (computed)\n\n" + _table(["Fact", "Value"], rows)
    return Section("overview", text, 1)


def issues(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    groups = report["issues"]["groups"]
    if not groups or focus == "configuration":
        return None
    rows = [[g["id"], g["severity"], f"{g['count']:,}", g["component"], g["code"] or "",
             f"{g['first_line']}–{g['last_line']}", g["pattern"][:140]] for g in groups[:detail.issue_rows]]
    text = ("## Error & warning patterns (computed — counts are exact)\n\n"
            + _table(["ID", "Sev", "Count", "Component", "Code", "Lines", "Pattern"], rows))
    if len(groups) > detail.issue_rows:
        text += f"\n\n{len(groups) - detail.issue_rows} more patterns are listed in findings.json."
    return Section("issues", text, 1)


def excerpts(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    groups = [g for g in report["issues"]["groups"] if g["excerpts"]][:detail.excerpts]
    if not groups or focus in ("configuration", "performance"):
        return None
    parts = ["## Representative excerpts"]
    for g in groups:
        ex = g["excerpts"][0]
        body = [f"{n}|{t}" for n, t in ex["before"]] + [f"{ex['line']}|{ex['text']}"]
        body += [f"{n}|{t}" for n, t in ex["after"]]
        parts.append(f"**{g['id']}** ({g['count']:,}×)\n```\n" + "\n".join(body) + "\n```")
    return Section("excerpts", "\n\n".join(parts), 4)


def latency(report: dict, _: PromptDetail, focus: str) -> Section | None:
    lat = report["latency"]
    if not lat.get("samples"):
        return None
    rows = [[name.title(), lat[name]["avg"], lat[name]["p95"], lat[name]["max"], lat[name]["last"]]
            for name in ("source", "handling", "target")]
    peak = lat["peak_target"]
    text = (f"## Latency (computed from {lat['samples']:,} PERFORMANCE samples, seconds)\n\n"
            + _table(["Latency", "Avg", "P95", "Max", "Last"], rows)
            + f"\n\nPeak target latency {peak['seconds']}s at line {peak['line']} ({peak['ts']}). "
              f"Heuristic bottleneck: **{lat['bottleneck']}**.")
    return Section("latency", text, 2 if focus == "performance" else 3)


def signals(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    hits = report["signals"]["signals"][:detail.signal_rows]
    if not hits:
        return None
    rows = []
    for s in hits:
        values = ", ".join(f"{k} ×{v}" for k, v in list(s["values"].items())[:3])
        rows.append([s["label"], f"{s['count']:,}", s["first_line"], values])
    text = "## Replicate behaviours detected (computed)\n\n" + _table(["Signal", "Count", "First line", "Top values"], rows)
    return Section("signals", text, 2)


def settings(report: dict, _: PromptDetail, focus: str) -> Section | None:
    found = report["metadata"]["settings"]
    if not found:
        return None
    rows = [[s["label"], s["value"], s["line"]] for s in found.values()]
    text = "## Task settings seen in the log\n\n" + _table(["Setting", "Value", "Line"], rows)
    return Section("settings", text, 2 if focus == "configuration" else 3)


def repetition(report: dict, detail: PromptDetail, focus: str) -> Section | None:
    rows = [[r["id"], f"{r['count']:,}", r["component"], r["severity"], r["pattern"][:120]]
            for r in report["repetition"]["top"][:detail.repeat_rows]]
    if not rows:
        return None
    text = ("## Most repeated line patterns (computed)\n\n"
            "These dominate the log; the attachment collapses them into `~~ omitted ~~` markers.\n\n"
            + _table(["ID", "Lines", "Component", "Sev", "Pattern"], rows))
    return Section("repetition", text, 5)


def runs(report: dict, _: PromptDetail, focus: str) -> Section | None:
    items = report["runs"]["runs"]
    if len(items) < 2:
        return None
    rows = [[r["run"], r["start_ts"], r["start_line"], r["end_ts"] or "", (r["stop_reason"] or "")[:80]]
            for r in items[:15]]
    text = "## Task runs (start/stop cycles)\n\n" + _table(["Run", "Start", "Line", "End", "Stop reason"], rows)
    return Section("runs", text, 3)


BUILDERS = (overview, issues, signals, latency, settings, runs, excerpts, repetition)
