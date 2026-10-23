"""Turn a report's findings into KB queries and rank the articles they match.

Each error/warning pattern, error code, fatal error and notable signal becomes one
query. An article is kept only when it covers most of a query's distinctive words
(IDF-weighted), so loose matches on common words like "table" or "apply" drop out.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass

from logpacker.analysis.pipeline import JobFiles
from logpacker.kb.index import KBIndex, get_index, tokenize

KB_VERSION = "1"  # bump when query building or scoring changes, to refresh cached results
MAX_ARTICLES = 8
MAX_GROUPS = 8
MIN_COVERAGE = 0.5
MIN_RELEVANCE = 0.5

NOTE = ("Candidate Qlik KB articles found by keyword match against this log's findings. They can be "
        "wrong or only loosely related. Use an article only if its symptom, error text or endpoint "
        "matches the evidence in this log (read it first if you can); otherwise ignore it. Never cite "
        "an article as the cause just because it is listed here.")

# Routine trace output, not events worth a KB lookup.
_ROUTINE_SIGNALS = {"redo_read", "batch_close_reason", "fl_table_done", "fl_completed", "file_upload", "bulk_back"}

_NOISE_RES = (
    re.compile(r"RetCode:\s*\w+|SqlState:\s*\w+|NativeError:\s*-?\w+|Message:", re.I),  # ODBC boilerplate
    re.compile(r"\([\w./\\-]+\.(?:c|cpp|h):[^)]*\)"),  # (ar_odbc_stmt.c:123)
    re.compile(r"<[a-z]+>"),                            # normalizer placeholders <n> <s> <id>
    re.compile(r"\[\d{6,8}\]"),                         # generic Replicate return codes [1020100]
    re.compile(r"(?:[A-Za-z]:)?[/\\][\w./\\-]+"),       # file paths
    re.compile(r"\b[a-z]+_\d{2,}\b"),                   # sanitizer tokens host_01, user_02
)
_GENERIC_CODE_RE = re.compile(r"^\[\d+\]$|^SQL_")
# Wrapper lines Replicate logs after the real error as it unwinds; they match far too many articles.
_CASCADE_RE = re.compile(
    r"^(?:Error executing (?:command|source loop|data handler)|Error forwarding bulk|"
    r"Stream component .*(?:terminated|failed at subtask)|Task .* encountered a fatal error|"
    r"Error (?:in|while) .*(?:handling|processing) (?:the )?(?:task|subtask))",
    re.I)
_CODE_TERM_RE = re.compile(r"ORA-\d{5}|SqlState:(\w+)|MySQL:(\d+)|SQLSTATE:(\w+)|Native:(-?\d+)", re.I)


def code_term(code: str) -> str | None:
    """The one token a KB article must contain for an error code to count as matched."""
    m = _CODE_TERM_RE.match(code)
    if not m:
        return None
    return (next((g for g in m.groups() if g), None) or m.group(0)).lower()


def is_cascade(text: str) -> bool:
    return bool(_CASCADE_RE.match(re.sub(r"\s+", " ", text).strip()))


@dataclass
class Query:
    ref: str        # E001, ORA-01555, signal id ...
    label: str
    terms: list[str]
    weight: float
    exact: bool = False  # a code: every term must match


def clean(text: str) -> str:
    for rx in _NOISE_RES:
        text = rx.sub(" ", text)
    return text


def build_queries(report: dict) -> list[Query]:
    queries: list[Query] = []
    seen_codes: set[str] = set()
    groups = sorted(report["issues"]["groups"], key=lambda g: (g["severity"] != "E", -g["count"]))
    for g in groups[:MAX_GROUPS]:
        is_error = g["severity"] == "E"
        terms = tokenize(clean(g["pattern"]))
        if len(terms) >= 2 and not is_cascade(g["pattern"]):
            queries.append(Query(g["id"], f"{g['id']} · {_short(g['pattern'])}", terms, 1.0 if is_error else 0.75))
        code = g.get("code")
        term = code_term(code) if code and not _GENERIC_CODE_RE.match(code) else None
        if term and term not in seen_codes:
            seen_codes.add(term)
            queries.append(Query(code, f"Error code {code}", [term], 0.95 if is_error else 0.7, exact=True))

    fatal = (report["metadata"] or {}).get("fatal")
    if fatal and fatal.get("text") and not is_cascade(fatal["text"]):
        terms = tokenize(clean(fatal["text"]))
        if len(terms) >= 2:
            queries.append(Query("fatal", f"Fatal error · {_short(fatal['text'])}", terms, 0.9))

    for s in (report.get("signals") or {}).get("signals", []):
        if s["id"] in _ROUTINE_SIGNALS or s.get("count", 0) > 1000:
            continue
        queries.append(Query(s["id"], f"Signal · {s['label']}", tokenize(f"{s['label']} {s.get('hint', '')}"), 0.6))
    return queries


def endpoint_terms(report: dict) -> set[str]:
    meta = report["metadata"] or {}
    names = [meta.get(k) for k in ("source_type", "target_type", "source_endpoint", "target_endpoint")]
    return {t for n in names if n and not n.startswith("<") for t in tokenize(n)}


def find_related(report: dict, index: KBIndex | None = None) -> dict:
    index = index or get_index()
    if index is None:
        return {"available": False, "reason": "No KB index is bundled with this LogPacker install.",
                "articles": [], "note": NOTE}

    queries = build_queries(report)
    endpoints = endpoint_terms(report)
    found: dict[int, dict] = {}
    for q in queries:
        for hit in index.search(q.terms, limit=5):
            if q.exact and hit.coverage < 1:
                continue
            if not q.exact and (hit.coverage < MIN_COVERAGE or len(hit.matched) < 2):
                continue
            entry = found.setdefault(hit.index, {"best": 0.0, "matches": []})
            entry["best"] = max(entry["best"], q.weight * hit.coverage * (0.6 + 0.4 * hit.phrase))
            entry["matches"].append({"ref": q.ref, "label": q.label, "terms": list(hit.matched)})

    articles = []
    for i, entry in found.items():
        a = index.articles[i]
        extra = 0.05 * (len({m["ref"] for m in entry["matches"]}) - 1)
        on_endpoint = bool(endpoints & set(tokenize(a["title"])))
        relevance = min(1.0, entry["best"] + extra + (0.1 if on_endpoint else 0))
        if relevance < MIN_RELEVANCE:
            continue
        articles.append({
            "id": a["id"], "title": a["title"], "url": a["url"], "description": a["description"],
            "relevance": round(relevance, 2),
            "confidence": "high" if relevance >= 0.75 else "medium" if relevance >= 0.55 else "low",
            "matched_on": entry["matches"],
        })
    articles.sort(key=lambda x: -x["relevance"])
    return {
        "available": True,
        "kb_version": KB_VERSION,
        "index": {"articles": len(index), "built_at": index.built_at},
        "queries": len(queries),
        "articles": articles[:MAX_ARTICLES],
        "note": NOTE,
    }


def related_for_job(files: JobFiles) -> dict:
    """find_related for a job, cached in its folder until the KB index or scoring changes."""
    cache = files.root / "kb.json"
    index = get_index()
    stamp = f"{KB_VERSION}|{index.built_at if index else None}"
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return cached["result"]
    report = json.loads(files.report.read_text(encoding="utf-8"))
    result = find_related(report, index)
    tmp = cache.with_name(f"kb.{os.getpid()}.{threading.get_ident()}.tmp")  # the web tab and MCP may ask at once
    tmp.write_text(json.dumps({"stamp": stamp, "result": result}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache)
    return result


def _short(text: str, limit: int = 90) -> str:
    """Display label: the message without source-file refs and return codes."""
    text = _NOISE_RES[1].sub(" ", _NOISE_RES[3].sub(" ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
