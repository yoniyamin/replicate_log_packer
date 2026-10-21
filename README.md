# LogPacker

Turn a Qlik Replicate task log into something you can safely hand to an AI assistant:

1. **Clean** – personal and sensitive values (hosts, IPs, users, emails, paths, passwords,
   license names, your own custom terms) are replaced with consistent placeholders such as
   `host_01` before anything else happens.
2. **Find patterns** – errors and warnings are grouped, latency is summarised, known Replicate
   behaviours (one-by-one apply, reconnects, memory pressure, Full Load…) are counted, and every
   line is matched to a repeating pattern.
3. **Pack** – you get a ready-to-paste prompt, a condensed attachment sized for the model, and
   `findings.json` with all computed facts.

Everything runs locally. Only sanitized data is kept after analysis; the original upload is deleted.

## Run it

```bash
uv run logpacker
```

The browser opens at <http://127.0.0.1:8765>. Use `--port` to change the port and
`--no-browser` to skip opening a tab.

To share it with colleagues without a repo checkout:

```bash
uv build
```

then they can run `uvx --from logpacker-0.1.0-py3-none-any.whl logpacker`. The only runtime
dependencies are FastAPI and Uvicorn.

## Reports

All reports are built in the same single pass over the log (no line cap, no re-reading):

| Tab | What it shows |
|---|---|
| Overview | Health score 1–5 with reasons, task properties, Full Load / CDC status, key events, logger levels, Replicate behaviours, and why any other report is unavailable |
| Errors & warnings | Grouped patterns with counts, codes, tables, up to 5 excerpts with context, and a timeline strip |
| Performance cockpit | Prioritized recommendations, source-vs-apply bottleneck (overall and per period), latency profile (p50/p90/p95/p99) and chart, spikes, plateaus, errors during high latency, batch behaviour with close reasons and RPS buckets, pain tables, MERGE use, CDC pipeline and source health, Oracle redo reads |
| Bulk apply | Insights, close-reason mix with the setting behind each reason, filterable batch list, per-table operations, one-by-one episodes |
| Full Load | Per-table and per-segment lifecycle, reload attempts, row mismatches, slow segments, unload→load gaps |
| File operations | Staging-file compress/upload times and throughput, overhead insights |

Thresholds follow the earlier log_analyzer reports. The rule-based findings also go into the prompt.

## Caching, retention and sessions

- Uploading the same file again (same privacy options) reuses the saved analysis. The browser
  sends a quick fingerprint first, so a repeat upload doesn't even transfer the file; the server
  also hashes the full content to catch renamed copies.
- An analysis is kept for 7 days after it was last opened, then swept (checked hourly).
- Each browser gets its own session cookie and sees only its own logs. Analysis and packing run
  in a pool of worker processes (`LOGPACKER_WORKERS`, default up to 10), so 10 people can work
  in parallel; extra jobs wait in a queue and the progress screen says so.

## How the attachment is condensed

The condensed log always keeps:

- the first lines of the log (task settings and endpoints live there),
- every error and warning with a few lines of context before and after,
- the first and last occurrences of every line pattern, so rare lines are never lost.

Long runs of repetitive lines are replaced by a marker such as
`~~ 1,532 similar lines omitted · mostly P0012 ×1,500 ~~`. `P0012` is listed in
`findings.json` and in the prompt. Every kept line starts with its original line number
(`1234|…`), so answers can refer back to the real log. If the result is still too big, the
rules tighten step by step (the strictest steps keep only the first occurrences of each error
pattern plus the last), and as a last resort the end is cut with a clear note.

Attachment sizes are token budgets: about 50k, 150k or 400k tokens, or the full sanitized log.

## Layout

```
logpacker/
  ingest/      sources.py (.log/.gz/.zip streams) · lines.py (prefix parser)
  sanitize/    rules.py (what is redacted) · pseudonyms.py · sanitizer.py
  analysis/    one collector per concern: stats, metadata, runs, issues, latency, signals,
               apply (+ apply_report), fileops, fullload (+ fullload_report), health, oracle, templates
               cockpit.py and manifest.py derive recommendations, health and tab availability
               pipeline.py runs them all in a single pass
  pack/        condense.py · evidence.py / evidence_perf.py (prompt sections) · prompt.py · focus.py · bundle.py
  web/         app.py · routes.py · jobs.py (registry, retention) · worker.py (process pool) · cache.py · assets.py
  static/      index.html, css/, js/ (ES modules, no build step; served under a versioned /assets/ path)
tools/make_sample_log.py   synthetic log for demos and tests
```

To recognise a new Replicate behaviour, add one `Signal(...)` entry in
`logpacker/analysis/signals.py`. To redact a new kind of value, add one `Rule(...)` in
`logpacker/sanitize/rules.py`.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LOGPACKER_DATA_DIR` | `~/.logpacker` | Where jobs are stored |
| `LOGPACKER_PORT` | `8765` | Port |
| `LOGPACKER_MAX_UPLOAD_MB` | `4096` | Largest upload |
| `LOGPACKER_MAX_UNCOMPRESSED_MB` | `16384` | Cap for .gz/.zip contents |
| `LOGPACKER_JOB_TTL_HOURS` | `168` | Delete an analysis this long after it was last used |
| `LOGPACKER_WORKERS` | `min(10, CPUs)` | Parallel analysis/packing processes |
| `LOGPACKER_SWEEP_MINUTES` | `60` | How often expired analyses are removed |

## Tests

```bash
uv run pytest
```

## Limits to know

- Redaction is rule-based. It catches the common Replicate cases, but it is not a guarantee.
  Check the **What was hidden** tab, and add customer or server names under *Other words to hide*.
- Token counts are estimates (about 4 characters per token).
- Run detection relies on the `Task Server Log` header and `Task '…' running … mode` lines.
