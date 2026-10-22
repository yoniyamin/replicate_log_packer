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

import json
import re
import time

from mcp.server.mcpserver import MCPServer

from logpacker import __version__
from logpacker.analysis.pipeline import ANALYSIS_VERSION
from logpacker.config import Settings, get_settings
from logpacker.mcp.config import MCPConfig
from logpacker.pack.bundle import build_package
from logpacker.pack.presets import PackOptions
from logpacker.web.jobs import LEGACY_OWNER, JobStore

_SEARCH_TIMEOUT = 5


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
            "If an upload_log tool is available (Claude Desktop extension), call it with the "
            "file path instead of steps 1-2.\n"
            "1. Call create_analysis with the filename and file size.\n"
            "2. Upload the file to the returned upload_url using HTTP PUT "
            "(e.g. curl --data-binary @file.log -H 'Authorization: Bearer <key>' <url>).\n"
            "3. Call check_analysis with the job_id to wait for results.\n"
            "4. Call pack to get the structured AI prompt with all evidence.\n"
            "5. Use get_errors, get_performance, etc. for follow-up questions.\n"
            "6. Use get_lines and search_log to look at specific sanitised log lines."
        ),
    )

    # ── helpers ───────────────────────────────────────────────────────

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

    @mcp.tool(description=(
        "Step 1: Create an analysis job and get an upload URL.\n\n"
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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
        return _ok({
            "prompt_text": result["prompt"]["text"],
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

    @mcp.tool(description="List previously analysed logs with their health score and job_id.")
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
        "Detected Replicate behaviours: one-by-one apply, reconnects, "
        "memory pressure, batch close reasons, missing logstream files etc."
    ))
    async def get_signals(job_id: str) -> str:
        report, error = _report(job_id)
        if error:
            return error
        sigs = report["signals"]["signals"]
        return _ok({"signals": sigs, "count": len(sigs)})

    @mcp.tool(description="Most repeated line patterns in the log with counts and first/last occurrences.")
    async def get_patterns(job_id: str, max_results: int = 20) -> str:
        report, error = _report(job_id)
        if error:
            return error
        rep = report["repetition"]
        top = rep["top"][:max_results]
        return _ok({"distinct_patterns": rep["distinct"], "showing": len(top), "top": top,
                     "web_url": _web(job_id, tab="patterns")})

    @mcp.tool(description=(
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

    # ── tier 3: drill-down ────────────────────────────────────────────

    @mcp.tool(description=(
        "Fetch specific lines from the sanitised log by line number.  "
        "Use this to look at the actual log content at line numbers "
        "referenced in error patterns, signals or other findings.  Max 20 lines."
    ))
    async def get_lines(job_id: str, line_numbers: list[int]) -> str:
        if not line_numbers or len(line_numbers) > 20:
            return _err("Provide between 1 and 20 line numbers.")
        _, error = _ready_job(job_id)
        if error:
            return error
        path = job_store.files(job_id).sanitized
        if not path.exists():
            return _err("Sanitised log not found.")
        wanted = set(line_numbers)
        result: dict[str, str] = {}
        max_wanted = max(wanted)
        with path.open(encoding="utf-8") as fh:
            for line_no, text in enumerate(fh, 1):
                if line_no in wanted:
                    result[str(line_no)] = text.rstrip("\n")
                if line_no >= max_wanted:
                    break
        return _ok({"lines": result})

    @mcp.tool(description=(
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
                            matches.append({"line": line_no, "text": line})
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

    @mcp.tool(description=(
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

    @mcp.tool(description=(
        "Get the web URL to view the interactive report in a browser.  "
        "Optionally specify a tab: overview, issues, cockpit, bulk, "
        "fullload, fileops, patterns, privacy."
    ))
    async def open_report(job_id: str, tab: str | None = None) -> str:
        _, error = _ready_job(job_id)
        if error:
            return error
        return _ok({"web_url": _web(job_id, tab=tab),
                     "message": "Open this URL in a browser to view the interactive report."})

    # ── prompts ───────────────────────────────────────────────────────

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

    return mcp
