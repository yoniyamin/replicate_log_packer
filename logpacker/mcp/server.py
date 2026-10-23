"""MCP server for LogPacker.

Creates an :class:`MCPServer` (MCP SDK v2) with tools, prompts and resources
that let an AI assistant analyse Qlik Replicate task logs, explore the
structured reports and drill into specific log lines — all without needing
the full log in the AI's context window.

Log files live on the **client machine**.  The AI uploads them to the
LogPacker server via the existing streaming HTTP endpoint, then queries
the analysis results through MCP tools.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time

from mcp.server.mcpserver import MCPServer

from logpacker import __version__
from logpacker.analysis.pipeline import ANALYSIS_VERSION
from logpacker.config import Settings, get_settings
from logpacker.kb.related import related_for_job
from logpacker.mcp.config import MCPConfig
from logpacker.pack.bundle import build_package
from logpacker.pack.presets import PackOptions
from logpacker.web.jobs import LEGACY_OWNER, JobStore

_SEARCH_TIMEOUT = 5
_KEY_LINES_MAX = 20
# Some lines are whole SQL statements (thousands of chars); clip them so a few
# matches don't flood the assistant's context.
_LINE_MAX = 2000
_SEARCH_LINE_MAX = 500


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… [+{len(text) - limit:,} chars]"

log = logging.getLogger("logpacker.mcp")


def _read_lines(path, wanted: set[int]) -> dict[int, str]:
    """Return {line_no: text} for the wanted line numbers of a text file (1-based)."""
    result: dict[int, str] = {}
    if not wanted:
        return result
    last = max(wanted)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, text in enumerate(fh, 1):
            if line_no in wanted:
                result[line_no] = _clip(text.rstrip("\n"), _LINE_MAX)
            if line_no >= last:
                break
    return result


_CITED_LINE_RE = re.compile(r"\bL(\d{1,9})\b|\bline (\d{1,9})\b")


def _key_line_numbers(report: dict, prompt_text: str) -> list[int]:
    """Lines worth quoting: first occurrence of each error/warning pattern, then lines the prompt cites."""
    picked: list[int] = []
    groups = sorted(report["issues"]["groups"], key=lambda g: not str(g.get("id", "")).startswith("E"))
    for group in groups:
        excerpts = group.get("excerpts") or []
        if excerpts and excerpts[0].get("line"):
            picked.append(int(excerpts[0]["line"]))
    for match in _CITED_LINE_RE.finditer(prompt_text):
        picked.append(int(match.group(1) or match.group(2)))
    return list(dict.fromkeys(picked))[:_KEY_LINES_MAX]


_ANALYSIS_RULES = (
    "How to work: (1) prompt_text is a starting point, not the answer. Before writing, work through "
    "`investigation`: make each suggested call and follow up on anything surprising (search_log for the "
    "same table or time window, get_lines with context). "
    "(2) The attachments the prompt mentions are NOT in this conversation; the sanitised log lives on the "
    "LogPacker server. key_lines holds the real text of the most important lines. Whenever you cite a line "
    "as evidence (e.g. 'W001 at L127'), quote its text; for any other line, call get_lines first. Never cite "
    "a line whose text you have not seen. "
    "(3) If you have a web search or fetch tool, use it for each item in `web_checks` and for any setting "
    "name, error text or behaviour you rely on — prefer help.qlik.com and community.qlik.com for the task's "
    "Replicate version — and list the pages under **Sources**. Only if you have no such tool, say which "
    "points are unverified. "
    "(4) kb_articles, when present, are Qlik KB articles matched to the findings by keyword. They are "
    "leads, not conclusions: weigh each one against the evidence (read it if you can fetch URLs), use it "
    "only if it describes the same error, symptom or endpoint behaviour, and silently ignore the rest. "
    "List the articles you actually relied on under **Sources**. "
    "(5) Then answer in the format at the end of prompt_text."
)


def _kb_brief(result: dict) -> list[dict]:
    """kb_articles for pack: what the model needs to judge relevance, without the per-term detail."""
    return [{"title": a["title"], "url": a["url"], "description": a["description"],
             "confidence": a["confidence"], "relevance": a["relevance"],
             "matched_on": [m["label"] for m in a["matched_on"]]}
            for a in result.get("articles", [])]

_INVESTIGATION_MAX = 12


def _investigation(report: dict) -> tuple[list[dict], list[str]]:
    """Concrete follow-up calls for the open points in a report, plus web searches worth running."""
    steps: list[dict] = []
    web: list[str] = []
    version = (report.get("metadata") or {}).get("version") or ""

    groups = sorted(report["issues"]["groups"], key=lambda g: not str(g.get("id", "")).startswith("E"))
    for g in groups[:5]:
        steps.append({"check": f"{g['id']} ×{g['count']}: what triggers it, which tables and time window it "
                               f"affects, and whether it lines up with the latency or apply problems",
                      "tool": "get_error_detail", "args": {"pattern_id": g["id"]}})
        text = str(g.get("pattern", ""))
        if len(text) > 90:
            text = text[:90].rsplit(" ", 1)[0]
        web.append(f'Qlik Replicate "{text}"')

    # Signals that fire thousands of times are routine trace output (e.g. every redo read), not events.
    events = [s for s in (report.get("signals") or {}).get("signals", []) if s.get("count", 0) <= 1000]
    for sig in events[:5]:
        if sig.get("first_line"):
            steps.append({"check": f"Signal '{sig['label']}' ×{sig['count']}: read what happened around its first "
                                   f"occurrence (L{sig['first_line']}) — the lines before it usually show the cause",
                          "tool": "get_lines", "args": {"line_numbers": [sig["first_line"]], "context": 10}})
            if sig.get("area") in ("Target", "Apply", "Source", "Pipeline"):
                web.append(f"Qlik Replicate {sig['label']}")

    lat = report.get("latency") or {}
    peak = lat.get("peak_target") or (lat.get("target") or {}).get("peak_target")
    if peak and peak.get("line"):
        steps.append({"check": f"Peak target latency ({peak.get('seconds')}s at L{peak['line']}): what the apply "
                               "thread was doing just before it",
                      "tool": "get_lines", "args": {"line_numbers": [peak["line"]], "context": 15}})

    for t in (report.get("apply") or {}).get("pain_tables", [])[:3]:
        if t.get("has_pk") is not True:
            name = str(t["table"]).replace("'", "").split(".")[-1]
            known = "has no primary key" if t.get("has_pk") is False else "primary key is unknown from the log"
            table = re.escape(name)
            steps.append({"check": f"Table {t['table']} ({known}): look for its key / index / column definition "
                                   "and for apply errors on it",
                          "tool": "search_log",
                          "args": {"pattern": f"{table}.*(key|index|unique|column|error)|"
                                              f"(key|index|unique|column|error).*{table}",
                                   "max_results": 15}})
            web.append("Qlik Replicate batch optimized apply table without primary key performance")

    if (report.get("apply") or {}).get("one_by_one"):
        web.append("Qlik Replicate batch optimized apply switches to one-by-one mode")
    if version:
        major = ".".join(str(version).split(".")[:2])  # 2025.11.0.285 -> 2025.11
        web = [f"{q} {major}" if q.startswith("Qlik Replicate batch") else q for q in web]
    return steps[:_INVESTIGATION_MAX], list(dict.fromkeys(web))[:8]


def _outcome(result) -> str:
    """'ok', or the job state for results that carry one (check_analysis: 'processing 47%', 'ready')."""
    if not isinstance(result, str) or not result.startswith("{") or len(result) > 4096:
        return "ok"
    try:
        body = json.loads(result)
    except ValueError:
        return "ok"
    state = body.get("state")
    if not state:
        return "ok"
    if state != "ready" and body.get("progress") is not None:
        return f"{state} {round(body['progress'] * 100)}%"
    return state


def _fmt_args(kwargs: dict) -> str:
    parts = []
    for name, value in kwargs.items():
        if value is None or value == "" or value == []:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        parts.append(f"{name}={text[:60] + '…' if len(text) > 60 else text}")
    return ", ".join(parts)


def create_mcp_server(
    job_store: JobStore | None = None,
    settings: Settings | None = None,
    mcp_config: MCPConfig | None = None,
    base_url: str | None = None,
) -> MCPServer:
    """Build a fully configured :class:`MCPServer`.

    Parameters
    ----------
    job_store:
        Shared with the web server when embedded (SSE).  Created
        automatically in stdio mode.
    settings:
        Application settings.  Falls back to :func:`get_settings`.
    mcp_config:
        MCP-specific settings (keys, concurrency).
    base_url:
        Root URL of the LogPacker web UI for ``web_url`` fields.
    """
    settings = settings or get_settings()
    mcp_config = mcp_config or MCPConfig.load(settings.data_dir)

    own_store = job_store is None
    if own_store:
        job_store = JobStore(settings)

    _base = base_url or f"http://{settings.host}:{settings.port}"

    mcp = MCPServer(
        name="LogPacker",
        description=(
            "Analyse Qlik Replicate task logs — sanitise, find patterns, "
            "generate AI-ready prompts and explore structured reports."
        ),
        version=__version__,
        instructions=(
            "To analyse a log:\n"
            "If an analyze_log tool is available (Claude Desktop extension), call it with the "
            "file path instead of steps 1-3: it uploads, waits and summarises in one call.\n"
            "1. Call create_analysis with the filename and file size.\n"
            "2. Upload the file to the returned upload_url using HTTP PUT "
            "(e.g. curl --data-binary @file.log -H 'Authorization: Bearer <key>' <url>).\n"
            "3. Call check_analysis with the job_id to wait for results.\n"
            "4. Call pack to get the structured AI prompt with all evidence. Follow its "
            "analysis_rules: work through its investigation calls, verify Replicate behaviour on "
            "the web when you can, and quote lines (key_lines / get_lines) before citing them.\n"
            "5. Use get_errors, get_performance, etc. for follow-up questions. get_kb_articles lists "
            "Qlik KB articles that may relate to the findings; use only those that match the evidence.\n"
            "6. Use get_lines and search_log to look at specific sanitised log lines."
        ),
    )

    # ── helpers ───────────────────────────────────────────────────────

    def tool(description: str):
        """``mcp.tool`` plus one log line per call: arguments, outcome and duration."""
        def register(fn):
            @functools.wraps(fn)
            async def logged(*args, **kwargs):
                start = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                except Exception:
                    log.exception("MCP %s(%s) crashed", fn.__name__, _fmt_args(kwargs))
                    raise
                took = time.monotonic() - start
                if isinstance(result, str) and result.startswith('{"error"'):
                    log.warning("MCP %s(%s) -> error in %.2fs: %s", fn.__name__, _fmt_args(kwargs), took,
                                json.loads(result)["error"])
                else:
                    log.info("MCP %s(%s) -> %s in %.2fs", fn.__name__, _fmt_args(kwargs), _outcome(result), took)
                return result
            # Tools return ready-made JSON text. Without structured_output=False the SDK also wraps it
            # as structuredContent {"result": "<escaped JSON>"}, which clients such as Claude Code show instead.
            return mcp.tool(description=description, structured_output=False)(logged)
        return register

    def _ok(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)

    def _err(message: str) -> str:
        return json.dumps({"error": message})

    def _ready_job(job_id: str):
        job = job_store._jobs.get(job_id)
        if job is None:
            return None, _err(f"Analysis '{job_id}' not found. Use list_analyses to see available ones.")
        if job.state != "ready":
            return None, _err(f"Analysis is still in state '{job.state}'. Please wait and try again.")
        job_store.touch(job)
        return job, None

    def _report(job_id: str):
        job, error = _ready_job(job_id)
        if error:
            return None, error
        path = job_store.files(job_id).report
        if not path.exists():
            return None, _err("Report file not found.")
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("quality", {}).get("analysis_version") != ANALYSIS_VERSION:
            return None, _err(
                "This analysis was made with an older version of LogPacker. "
                "Re-upload the log to re-analyse."
            )
        return report, None

    def _web(job_id: str, view: str = "review", tab: str | None = None) -> str:
        url = f"{_base}/#/job/{job_id}/{view}"
        if tab:
            url += f"?tab={tab}"
        return url

    # ── tier 1: upload & analysis lifecycle ────────────────────────────

    @tool(description=(
        "Step 1: Create an analysis job and get an upload URL.\n\n"
        "If an analyze_log tool is available, use it instead — it handles the whole "
        "upload and analysis.  This tool is for clients that can run shell commands.\n\n"
        "The log file lives on YOUR machine, not on the server.  After calling "
        "this tool, upload the file to the returned upload_url using an HTTP PUT "
        "request with the file as the raw request body.\n\n"
        "Example:\n"
        "  curl -X PUT --data-binary @\"task.log\" "
        "-H \"Authorization: Bearer $KEY\" \"<upload_url>\"\n\n"
        "Then call check_analysis(job_id) to wait for results."
    ))
    async def create_analysis(
        filename: str,
        size: int = 0,
        sanitize_enabled: bool = True,
        mask_objects: bool = False,
        custom_terms: list[str] | None = None,
    ) -> str:
        if size > settings.max_upload_bytes:
            limit_mb = settings.max_upload_bytes // (1024 * 1024)
            return _err(f"File is {size // (1024 * 1024):,} MB, limit is {limit_mb:,} MB.")

        terms = [t.strip() for t in (custom_terms or []) if len(t.strip()) >= 2]
        san_dict = {"enabled": sanitize_enabled, "mask_objects": mask_objects, "custom_terms": terms}

        job = job_store.create(filename, size or 0, san_dict, LEGACY_OWNER)

        upload_url = f"{_base}/api/jobs/{job.id}/content"
        return _ok({
            "job_id": job.id,
            "upload_url": upload_url,
            "upload_command": (
                f'curl -X PUT --data-binary @"{filename}" '
                f'-H "Authorization: Bearer <your-api-key>" '
                f'"{upload_url}"'
            ),
            "next_step": "Upload the file to upload_url, then call check_analysis(job_id).",
        })

    @tool(description=(
        "Step 3: Check whether the analysis is complete and get results.\n\n"
        "Call this after uploading the file.  Returns the health score, "
        "error/warning counts, time range and available report tabs once ready.  "
        "If still processing, returns the current state."
    ))
    async def check_analysis(job_id: str) -> str:
        job = job_store._jobs.get(job_id)
        if job is None:
            return _err(f"Job '{job_id}' not found.")

        if job.state == "ready":
            return _analyze_result(job)

        if job.state == "failed":
            return _err(f"Analysis failed: {job.error}")

        job_store.live_status(job)
        return _ok({
            "job_id": job.id,
            "state": job.state,
            "progress": round(job.bytes_done / job.size, 3) if job.size else 0,
            "message": f"Analysis is {job.state}. Call check_analysis again in a few seconds.",
        })

    def _analyze_result(job) -> str:
        report = json.loads(job_store.files(job.id).report.read_text(encoding="utf-8"))
        health = report.get("manifest", {}).get("health", {})
        tabs = [t["id"] for t in report.get("manifest", {}).get("tabs", []) if t["available"]]
        return _ok({
            "job_id": job.id,
            "filename": job.filename,
            "state": "ready",
            "health": health,
            "stats": {
                "lines": report["stats"]["lines"],
                "errors": report["stats"]["errors"],
                "warnings": report["stats"]["warnings"],
            },
            "time_range": {"first": report["stats"]["first_ts"], "last": report["stats"]["last_ts"]},
            "available_tabs": tabs,
            "analysis_version": ANALYSIS_VERSION,
            "web_url": _web(job.id),
        })

    @tool(description=(
        "Generate the structured AI analysis prompt for an analysed log.  "
        "The prompt contains all computed evidence (error patterns with exact counts, "
        "latency statistics, recommendations) and instructions for answering.  "
        "Read it carefully — the facts come from a deterministic parser over the full log."
    ))
    async def pack(
        job_id: str,
        focus: str = "auto",
        detail: str = "standard",
        attachment_size: str = "medium",
        question: str = "",
    ) -> str:
        job, error = _ready_job(job_id)
        if error:
            return error
        try:
            opts = PackOptions(focus=focus, detail=detail, attachment=attachment_size, question=question)
        except ValueError as exc:
            return _err(str(exc))
        try:
            import asyncio
            result = await asyncio.to_thread(build_package, job_store.files(job_id), opts)
        except Exception as exc:
            return _err(f"Pack failed: {exc}")
        prompt_text = result["prompt"]["text"]
        key_lines: dict[str, str] = {}
        investigation: list[dict] = []
        web_checks: list[str] = []
        report, _ = _report(job_id)
        sanitized = job_store.files(job_id).sanitized
        if report:
            investigation, web_checks = _investigation(report)
            for step in investigation:
                step["args"] = {"job_id": job_id, **step["args"]}
        if report and sanitized.exists():
            wanted = _key_line_numbers(report, prompt_text)
            found = await asyncio.to_thread(_read_lines, sanitized, set(wanted))
            key_lines = {f"L{n}": found[n] for n in wanted if n in found}
        kb: dict = {}
        if report:
            try:
                kb = await asyncio.to_thread(related_for_job, job_store.files(job_id))
            except Exception:  # the KB is optional; never fail a pack over it
                log.exception("KB lookup failed for %s", job_id)
        return _ok({
            "analysis_rules": _ANALYSIS_RULES,
            "investigation": investigation,
            "web_checks": web_checks,
            "key_lines": key_lines,
            "kb_articles": _kb_brief(kb),
            "kb_note": kb.get("note", ""),
            "prompt_text": prompt_text,
            "focus": result["prompt"]["focus"],
            "focus_label": result["prompt"]["focus_label"],
            "focus_reason": result["prompt"]["focus_reason"],
            "prompt_tokens": result["prompt"]["tokens"],
            "sections_included": result["prompt"]["sections"],
            "sections_dropped": result["prompt"]["dropped"],
            "attachment_tokens": result["attachment"]["tokens"],
            "findings_bytes": result["findings_bytes"],
            "web_url": _web(job_id, "pack"),
        })

    @tool(description="List previously analysed logs with their health score and job_id.")
    async def list_analyses() -> str:
        jobs = sorted(
            (j for j in job_store._jobs.values() if j.state == "ready"),
            key=lambda j: -j.last_access,
        )[:50]
        items = []
        for job in jobs:
            rp = job_store.files(job.id).report
            health = None
            if rp.exists():
                try:
                    r = json.loads(rp.read_text(encoding="utf-8"))
                    health = r.get("manifest", {}).get("health")
                except Exception:
                    pass
            items.append({
                "job_id": job.id,
                "filename": job.filename,
                "lines": job.lines_done,
                "health": health,
                "analyzed_at": job.finished_at,
                "web_url": _web(job.id),
            })
        return _ok({"analyses": items, "count": len(items)})

    # ── tier 2: report exploration ────────────────────────────────────

    @tool(description=(
        "Task overview: health score, task properties, run mode, "
        "time range, logger levels and available report tabs."
    ))
    async def get_overview(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        meta, stats = report["metadata"], report["stats"]
        tabs = report.get("manifest", {}).get("tabs", [])
        return _ok({
            "health": report.get("manifest", {}).get("health"),
            "task": meta.get("task"), "version": meta.get("version"),
            "source_endpoint": meta.get("source_endpoint"),
            "target_endpoint": meta.get("target_endpoint"),
            "mode": meta.get("mode"),
            "time_range": {"first": stats["first_ts"], "last": stats["last_ts"]},
            "lines": stats["lines"], "errors": stats["errors"], "warnings": stats["warnings"],
            "tables_seen": meta.get("tables_seen", 0),
            "full_load_completed": meta.get("full_load_completed", False),
            "cdc_started": meta.get("cdc_started", False),
            "verbose_loggers": meta.get("verbose_loggers", []),
            "settings": meta.get("settings"),
            "available_tabs": [t["id"] for t in tabs if t["available"]],
            "web_url": _web(job_id, tab="overview"),
        })

    @tool(description=(
        "All error and warning patterns with counts, codes, affected tables "
        "and up to 5 representative excerpts with context lines each."
    ))
    async def get_errors(job_id: str, max_results: int = 20) -> str:
        report, error = _report(job_id)
        if error:
            return error
        groups = report["issues"]["groups"]
        total = len(groups)
        return _ok({
            "total_groups": total, "showing": min(total, max_results),
            "error_count": report["stats"]["errors"],
            "warning_count": report["stats"]["warnings"],
            "groups": groups[:max_results],
            "truncated": total > max_results,
            "web_url": _web(job_id, tab="issues"),
        })

    @tool(description=(
        "Deep dive into one error/warning pattern by its ID (e.g. 'E001', 'W003').  "
        "Returns all excerpts with context, every affected table and line numbers."
    ))
    async def get_error_detail(job_id: str, pattern_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        group = next((g for g in report["issues"]["groups"] if g["id"] == pattern_id.upper()), None)
        if group is None:
            ids = [g["id"] for g in report["issues"]["groups"]]
            return _err(f"Pattern '{pattern_id}' not found. Available: {ids}")
        return _ok(group)

    @tool(description=(
        "Performance cockpit: prioritised recommendations, source-vs-apply "
        "bottleneck, latency profile (p50/p90/p95/p99), spikes, plateaus "
        "and error correlation."
    ))
    async def get_performance(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        if not report["cockpit"]["available"]:
            return _ok({
                "available": False,
                "reason": "No latency samples or batch data. Set the PERFORMANCE logger to TRACE.",
            })
        lat = report["latency"]
        return _ok({
            "available": True,
            "recommendations": report["cockpit"]["recommendations"],
            "error_correlation": report["cockpit"]["error_correlation"],
            "top_contributors": report["cockpit"]["top_contributors"],
            "latency": {
                "target": lat.get("target"), "source": lat.get("source"),
                "bottleneck": lat.get("bottleneck"),
                "spikes": lat.get("spikes"), "plateaus": lat.get("plateaus"),
                "samples_count": lat.get("samples", 0),
            },
            "web_url": _web(job_id, tab="cockpit"),
        })

    @tool(description=(
        "Bulk apply details: batch closure reasons (and the setting behind each), "
        "per-table operations and RPS, one-by-one episodes, MERGE use."
    ))
    async def get_batch_details(job_id: str, max_batches: int = 30) -> str:
        report, error = _report(job_id)
        if error:
            return error
        apply = report["apply"]
        batches = apply["batches"]
        return _ok({
            "batch_count": batches["count"],
            "closure_reasons": batches["closure_reasons"],
            "issues": batches["issues"],
            "rps_buckets": batches.get("rps_buckets"),
            "pain_tables": apply["pain_tables"][:10],
            "one_by_one": apply["one_by_one"],
            "merge": apply["merge"],
            "table_count": apply["table_count"],
            "batches_sample": batches.get("batches", [])[:max_batches],
            "web_url": _web(job_id, tab="bulk"),
        })

    @tool(description=(
        "Full Load details: per-table and per-segment lifecycle, reload "
        "attempts, row mismatches, slow segments and unload->load gaps."
    ))
    async def get_fullload(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        fl = report["full_load"]
        if not fl["available"]:
            return _ok({"available": False, "reason": fl.get("reason", "No Full Load activity in this log.")})
        return _ok({**{k: v for k, v in fl.items() if k != "available"}, "available": True,
                     "web_url": _web(job_id, tab="fullload")})

    @tool(description=(
        "Staging-file operations for cloud targets: compress/upload times, "
        "throughput per file, overhead insights."
    ))
    async def get_file_operations(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        fo = report["file_ops"]
        if not fo["count"]:
            return _ok({"available": False,
                         "reason": "No staging-file uploads. These appear only for cloud targets."})
        return _ok({**fo, "available": True, "web_url": _web(job_id, tab="fileops")})

    @tool(description=(
        "Detected Replicate behaviours: one-by-one apply, reconnects, "
        "memory pressure, batch close reasons, missing logstream files etc."
    ))
    async def get_signals(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        sigs = report["signals"]["signals"]
        return _ok({"signals": sigs, "count": len(sigs)})

    @tool(description="Most repeated line patterns in the log with counts and first/last occurrences.")
    async def get_patterns(job_id: str, max_results: int = 20) -> str:
        report, error = _report(job_id)
        if error:
            return error
        rep = report["repetition"]
        top = rep["top"][:max_results]
        return _ok({"distinct_patterns": rep["distinct"], "showing": len(top), "top": top,
                     "web_url": _web(job_id, tab="patterns")})

    @tool(description=(
        "Sanitisation summary: what categories were redacted, how many "
        "replacements and distinct values per category."
    ))
    async def get_privacy(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        p = report["privacy"]
        return _ok({"enabled": p["enabled"], "lines_changed": p["lines_changed"],
                     "categories": p["categories"],
                     "web_url": _web(job_id, tab="privacy")})

    @tool(description=(
        "Qlik KB support articles (title, url, description) that may relate to this log's errors, "
        "warnings and signals, ranked by keyword relevance, with what each matched on.  These are "
        "candidates found by keyword search, not verified answers: use an article only if it describes "
        "the same error or symptom as the evidence, and ignore the rest.  Returns an empty list when "
        "nothing matched closely."
    ))
    async def get_kb_articles(job_id: str) -> str:
        _, error = _ready_job(job_id)
        if error:
            return error
        import asyncio
        result = await asyncio.to_thread(related_for_job, job_store.files(job_id))
        return _ok({**result, "web_url": _web(job_id, tab="kb")})

    # ── tier 3: drill-down ────────────────────────────────────────────

    @tool(description=(
        "Fetch specific lines from the sanitised log by line number.  "
        "Use this to look at the actual log content at line numbers "
        "referenced in error patterns, signals or other findings.  Max 20 line numbers; "
        "set context (0-25) to also get that many lines before and after each one — "
        "the lines before a warning or mode switch usually show its cause.  Max 300 lines returned."
    ))
    async def get_lines(job_id: str, line_numbers: list[int], context: int = 0) -> str:
        if not line_numbers or len(line_numbers) > 20:
            return _err("Provide between 1 and 20 line numbers.")
        _, error = _ready_job(job_id)
        if error:
            return error
        path = job_store.files(job_id).sanitized
        if not path.exists():
            return _err("Sanitised log not found.")
        context = max(0, min(int(context), 25))
        wanted = sorted({n + d for n in line_numbers for d in range(-context, context + 1) if n + d > 0})[:300]
        import asyncio
        lines = await asyncio.to_thread(_read_lines, path, set(wanted))
        return _ok({"lines": {str(n): text for n, text in sorted(lines.items())}})

    @tool(description=(
        "Search the sanitised log using a regex pattern.  Returns matching "
        "lines with their line numbers.  Useful for finding all occurrences "
        "of a table name, error code or other pattern.  Max 50 results."
    ))
    async def search_log(job_id: str, pattern: str, max_results: int = 50) -> str:
        _, error = _ready_job(job_id)
        if error:
            return error
        max_results = min(max_results, 50)
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return _err(f"Invalid regex: {exc}")
        path = job_store.files(job_id).sanitized
        if not path.exists():
            return _err("Sanitised log not found.")

        import asyncio

        def _search():
            matches: list[dict] = []
            start = time.monotonic()
            with path.open(encoding="utf-8") as fh:
                for line_no, text in enumerate(fh, 1):
                    line = text.rstrip("\n")
                    try:
                        if compiled.search(line):
                            matches.append({"line": line_no, "text": _clip(line, _SEARCH_LINE_MAX)})
                            if len(matches) >= max_results:
                                break
                    except Exception:
                        continue
                    if time.monotonic() - start > _SEARCH_TIMEOUT:
                        return matches, True, "Search timed out after 5 seconds."
            return matches, len(matches) >= max_results, None

        matches, truncated, reason = await asyncio.to_thread(_search)
        result = {"pattern": pattern, "matches": matches, "count": len(matches), "truncated": truncated}
        if reason:
            result["reason"] = reason
        return _ok(result)

    @tool(description=(
        "Full findings.json: every error/warning pattern, latency statistic, "
        "signal and repeated-line pattern found by the parser."
    ))
    async def get_findings(job_id: str) -> str:
        _, error = _ready_job(job_id)
        if error:
            return error
        findings_path = job_store.files(job_id).root / "pack" / "findings.json"
        if findings_path.exists():
            return findings_path.read_text(encoding="utf-8")
        report = json.loads(job_store.files(job_id).report.read_text(encoding="utf-8"))
        from logpacker.pack.bundle import _findings
        return json.dumps(_findings(report), indent=1, ensure_ascii=False, default=str)

    # ── tier 4: navigation ────────────────────────────────────────────

    @tool(description=(
        "Get the web URL to view the interactive report in a browser.  "
        "Optionally specify a tab: overview, issues, cockpit, bulk, "
        "fullload, fileops, patterns, kb, privacy."
    ))
    async def open_report(job_id: str, tab: str | None = None) -> str:
        _, error = _ready_job(job_id)
        if error:
            return error
        return _ok({"web_url": _web(job_id, tab=tab),
                     "message": "Open this URL in a browser to view the interactive report."})

    # ── prompts ───────────────────────────────────────────────────────

    _PROMPT_TAIL = (
        "\n\nIf an analyze_log tool is available, call it with the file path instead of steps 1-3.\n"
        "Depth: pack returns analysis_rules, an investigation list and web_checks. Work through the "
        "investigation calls before answering, and if you can search the web, verify Replicate behaviour "
        "and settings on help.qlik.com / community.qlik.com and list your Sources.\n"
        "Evidence: when you cite a log line as important (e.g. 'W001 at L127'), quote its text — "
        "from pack's key_lines, or fetch it with get_lines first. Never cite a line you have not seen.\n"
        "KB: pack's kb_articles (also get_kb_articles) are Qlik KB articles matched by keyword. Use one only "
        "if it describes the same error or symptom as the evidence, list it under Sources, and ignore the rest."
    )

    @mcp.prompt(description="Analyse a Qlik Replicate task log and provide expert findings.")
    async def analyze_replicate_log(file_path: str, question: str = "") -> str:
        q = f"\n\nSpecific question: {question}" if question else ""
        return (
            f"Analyse the Qlik Replicate task log at {file_path}.{q}\n\n"
            "Steps:\n"
            "1. Call create_analysis with the filename\n"
            "2. Upload the file to the returned upload_url using HTTP PUT\n"
            "3. Call check_analysis with the job_id to wait for results\n"
            "4. Call pack with the job_id to get the structured analysis prompt\n"
            "5. Read the prompt carefully — it contains computed facts from the full log\n"
            "6. Answer using the format in the prompt: Summary, Key findings, Root cause & fixes, "
            "Performance, What else to collect\n"
            "7. Share the web_url so the user can explore the interactive reports"
            + _PROMPT_TAIL
        )

    @mcp.prompt(description="Focus on finding and explaining errors in a Replicate task log.")
    async def troubleshoot_errors(file_path: str) -> str:
        return (
            f"Analyse the Qlik Replicate task log at {file_path} with focus on errors.\n\n"
            "Steps:\n"
            "1. Call create_analysis with the filename\n"
            "2. Upload the file to the returned upload_url\n"
            "3. Call check_analysis to wait for completion\n"
            '4. Call pack with focus="errors" to get error-focused evidence\n'
            "5. Call get_errors to see all error/warning patterns with excerpts\n"
            "6. For each significant pattern, use get_error_detail for full context\n"
            "7. If needed, use search_log to find related occurrences\n"
            "8. Explain each error's root cause and provide concrete fixes"
            + _PROMPT_TAIL
        )

    @mcp.prompt(description="Review the performance of a Replicate task from its log.")
    async def performance_review(file_path: str) -> str:
        return (
            f"Analyse the Qlik Replicate task log at {file_path} with focus on performance.\n\n"
            "Steps:\n"
            "1. Call create_analysis with the filename\n"
            "2. Upload the file to the returned upload_url\n"
            "3. Call check_analysis to wait for completion\n"
            '4. Call pack with focus="performance" to get performance-focused evidence\n'
            "5. Call get_performance to see the full cockpit\n"
            "6. Call get_batch_details to understand the apply behaviour\n"
            "7. Explain where the latency comes from and what settings to tune"
            + _PROMPT_TAIL
        )

    # ── resources ─────────────────────────────────────────────────────

    @mcp.resource("logpacker://jobs", description="List of all analysed logs.")
    async def resource_jobs() -> str:
        return await list_analyses()

    @mcp.resource("logpacker://jobs/{job_id}/report", description="Full analysis report JSON.",
                  mime_type="application/json")
    async def resource_report(job_id: str) -> str:
        path = job_store.files(job_id).report
        if not path.exists():
            return _err("Report not found.")
        return path.read_text(encoding="utf-8")

    @mcp.resource("logpacker://jobs/{job_id}/prompt", description="Generated PROMPT.md.",
                  mime_type="text/markdown")
    async def resource_prompt(job_id: str) -> str:
        path = job_store.files(job_id).root / "pack" / "PROMPT.md"
        if not path.exists():
            return _err("Prompt not generated yet. Call the pack tool first.")
        return path.read_text(encoding="utf-8")

    @mcp.resource("logpacker://jobs/{job_id}/findings", description="findings.json.",
                  mime_type="application/json")
    async def resource_findings(job_id: str) -> str:
        path = job_store.files(job_id).root / "pack" / "findings.json"
        if not path.exists():
            return _err("Findings not generated yet. Call the pack tool first.")
        return path.read_text(encoding="utf-8")

    @mcp.resource("logpacker://jobs/{job_id}/kb", description="Qlik KB articles that may relate to the findings.",
                  mime_type="application/json")
    async def resource_kb(job_id: str) -> str:
        return await get_kb_articles(job_id)

    return mcp
