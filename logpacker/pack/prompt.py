"""Render the prompt that goes with the attachment, within a token budget."""

from __future__ import annotations

from logpacker.pack import evidence as ev
from logpacker.pack import evidence_perf as perf
from logpacker.pack.evidence import Section

# Order in the prompt. Priority inside each Section decides what is dropped first.
BUILDERS = (ev.overview, perf.recommendations, ev.issues, perf.latency, perf.apply_profile, perf.full_load,
            ev.signals, ev.settings, ev.runs, ev.excerpts, ev.repetition)
from logpacker.pack.focus import FOCUS_ASKS, FOCUS_LABELS
from logpacker.pack.presets import PROMPT_DETAIL, PackOptions, estimate_tokens

ROLE = (
    "You are a senior Qlik Replicate support engineer. You know CDC pipelines, source capture "
    "(redo/WAL/journal readers), the sorter, batch-optimized and transactional apply, Full Load, "
    "ODBC errors and endpoint-specific behaviour."
)

ANSWER_FORMAT = """## How to answer

Use these sections as `##` headings, in this order:

1. **Summary** – health score X/5 (1 = critical, 5 = healthy) and the main cause, in at most 3 sentences.
2. **Key findings** – bullets, each led by the exact count from the tables above.
3. **Root cause & fixes** – for each issue: *Evidence* (quote line numbers like `L1234` or pattern IDs), \
*What it means*, *What to do* (concrete settings or actions).
4. **Performance** – only if latency or apply data is present; otherwise one sentence saying it is missing.
5. **What else to collect** – logs, logging levels or settings that would confirm the diagnosis.
6. **Sources** – the documentation or community pages you checked, with URLs. Omit if you checked none.

Rules:
- Counts, averages and line numbers above were computed by a parser over the full log. Use them as given; do not recount.
- The findings above are a starting point, not the answer. Investigate each open point before concluding \
(what triggered a fallback, whether a table has a primary key, what the task was doing at a latency peak): \
read the log lines around it and state what you checked and what is still unknown.
- Cite line numbers only from the attachment or the excerpts above. If something is not in the evidence, say so.
- Tokens like `host_01`, `user_02`, `ip_03` are consistent placeholders for redacted values. Do not guess the originals.
- If you can search the web, verify Replicate behaviour, setting names and error/warning texts on help.qlik.com \
and community.qlik.com for this Replicate version, and list the pages under **Sources**. Only if you cannot, \
say which points are unverified.
- Do not invent parameter names, KB articles or URLs. If you mention a Replicate setting, say where it is set.
"""


def build_prompt(report: dict, attachment: dict, options: PackOptions, focus: str, focus_reason: str) -> dict:
    detail = PROMPT_DETAIL[options.detail]
    sections = [s for build in BUILDERS if (s := build(report, detail, focus)) is not None]
    head = _head(options, focus, attachment)
    tail = ANSWER_FORMAT
    sections, dropped = _fit(sections, detail.max_tokens - estimate_tokens(head + tail))

    text = "\n\n".join([head, *(s.text for s in sections), tail]).strip() + "\n"
    return {
        "text": text,
        "tokens": estimate_tokens(text),
        "focus": focus,
        "focus_label": FOCUS_LABELS[focus],
        "focus_reason": focus_reason,
        "sections": [s.key for s in sections],
        "dropped": dropped,
    }


def _head(options: PackOptions, focus: str, attachment: dict) -> str:
    ask = options.question.strip() or FOCUS_ASKS[focus]
    files = [f"- `{attachment['name']}` – {_attachment_note(attachment)}"]
    if options.include_findings:
        files.append("- `findings.json` – every error/warning pattern, detected signal, latency statistic and "
                     "repeated-line pattern (IDs like `E001`, `W004`, `P0012`) found by the parser.")
    return "\n\n".join([
        "# Qlik Replicate task log review",
        ROLE,
        f"## What I need\n\n{ask}",
        "## Attached files\n\n" + "\n".join(files)
        + "\n\nThe log was sanitized before sharing: names, hosts, IPs, users and secrets are replaced "
          "with consistent placeholders.",
    ])


def _attachment_note(attachment: dict) -> str:
    if attachment["mode"] == "full":
        return "the full sanitized task log."
    cap = attachment.get("issues_capped")
    kept = (f"Up to {cap} occurrences of each error/warning pattern (plus the last one) are kept with context"
            if cap else "Every error and warning is kept with context")
    note = (f"the sanitized task log, condensed from {attachment['lines_in']:,} to "
            f"{attachment['lines_kept']:,} lines. Each line starts with its original line number (`1234|...`). "
            f"{kept}; repetitive lines are collapsed into "
            "`~~ N similar lines omitted · mostly P0012 ×N ~~` markers.")
    if attachment.get("truncated"):
        note += " The end of the file was cut to fit the size limit."
    return note


def _fit(sections: list[Section], budget: int) -> tuple[list[Section], list[str]]:
    """Drop whole sections, highest priority number first, until the prompt fits."""
    kept = list(sections)
    dropped: list[str] = []
    while sum(estimate_tokens(s.text) for s in kept) > budget:
        candidates = [s for s in kept if s.priority > 1]
        if not candidates:
            break
        victim = max(candidates, key=lambda s: (s.priority, estimate_tokens(s.text)))
        kept.remove(victim)
        dropped.append(victim.key)
    return kept, dropped
