"""Export the Qlik KB corpus from log_analyzer's Chroma store into ``data/kb_articles.json``.

Chroma keeps every chunk's text and metadata in plain SQLite, so this reads it with
the standard library; chromadb is not needed. Run it again whenever the KB is re-indexed:

    python -m logpacker.kb.build --chroma ../log_analyzer/chroma_kb
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from logpacker.kb.index import KB_FILE

COLLECTION = "qlik_replicate_kb"
DESCRIPTION_CHARS = 260
TEXT_CHARS = 8000  # enough for keyword search; keeps the bundled file small


def read_chunks(db_path: Path) -> dict[str, list[dict]]:
    """{url: [chunk metadata + text, ...]} for the KB-article chunks of the collection."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT m.id, m.key, COALESCE(m.string_value, CAST(m.int_value AS TEXT)) "
        "FROM embedding_metadata m "
        "JOIN embeddings e ON e.id = m.id "
        "JOIN segments s ON s.id = e.segment_id "
        "JOIN collections c ON c.id = s.collection "
        "WHERE c.name = ?", (COLLECTION,))
    chunks: dict[int, dict] = defaultdict(dict)
    for chunk_id, key, value in rows:
        chunks[chunk_id][key] = value
    con.close()

    by_url: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks.values():
        if chunk.get("source") == "kb_article" and chunk.get("url"):
            by_url[chunk["url"]].append(chunk)
    return by_url


def build_articles(by_url: dict[str, list[dict]]) -> list[dict]:
    articles = []
    for url, chunks in by_url.items():
        chunks.sort(key=lambda c: int(c.get("chunk_index") or 0))
        title = (chunks[0].get("title") or "").strip()
        text = _squash(" ".join(c.get("chroma:document") or "" for c in chunks))
        body = text[len(title):].strip() if title and text.startswith(title) else text
        articles.append({
            "id": chunks[0].get("article_id") or "",
            "title": title,
            "url": url,
            "description": _shorten(body, DESCRIPTION_CHARS),
            "text": body[:TEXT_CHARS],
        })
    articles.sort(key=lambda a: a["title"].lower())
    return articles


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence = cut.rfind(". ")
    if sentence > limit // 2:
        return cut[:sentence + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chroma", required=True, type=Path, help="Chroma persist directory (contains chroma.sqlite3)")
    parser.add_argument("--out", type=Path, default=KB_FILE)
    args = parser.parse_args(argv)

    db = args.chroma / "chroma.sqlite3" if args.chroma.is_dir() else args.chroma
    articles = build_articles(read_chunks(db))
    payload = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "community.qlik.com Official Support Articles (Qlik Replicate)",
        "count": len(articles),
        "articles": articles,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(articles):,} articles to {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
