import asyncio
import json

from logpacker.kb.index import KBIndex, get_index, tokenize
from logpacker.mcp.server import create_mcp_server
from logpacker.kb.related import build_queries, find_related

from test_api import server, upload, wait_ready


def _article(i, title, text):
    return {"id": str(i), "title": title, "url": f"https://kb.example/{i}", "description": text[:60], "text": text}


ARTICLES = [
    _article(1, "Logstream task fails with Position does not exist",
             "The task fails with: audit file already purged, position does not exist. Resume from a timestamp."),
    _article(2, "How to read a log", "Log lines show the component and severity. Error executing command is common."),
    _article(3, "ORA-01555 snapshot too old", "Oracle returns ORA-01555 when undo retention is too small."),
    _article(4, "Oracle error reference", "Many ORA errors such as ORA-00600 are listed here."),
    _article(5, "Stream component terminated", "Stream component terminated after an error executing command."),
] + [_article(10 + i, f"Unrelated article {i}", "Configure the endpoint connection and tune the sorter memory.")
     for i in range(20)]


def _report(groups, signals=(), fatal=None):
    return {"issues": {"groups": [{"count": 1, **g} for g in groups]},
            "signals": {"signals": list(signals)},
            "metadata": {"fatal": fatal, "source_type": None, "target_type": "Oracle"}}


def test_tokenize_keeps_codes_and_drops_stopwords():
    terms = tokenize("The task failed with ORA-01555: snapshot too old")
    assert "ora-01555" in terms and "snapshot" in terms
    assert "the" not in terms and "task" not in terms and "failed" not in terms


def test_specific_error_finds_its_article_and_cascades_are_ignored():
    report = _report([
        {"id": "E001", "severity": "E", "code": "[1002510]", "pattern": "Position does not exist [<n>] (at_audit_reader.c:<n>)"},
        {"id": "E002", "severity": "E", "code": "[1002510]", "pattern": "Error executing command [<n>] (streamcomponent.c:<n>)"},
        {"id": "E003", "severity": "E", "code": None, "pattern": "Stream component '<s>' terminated [<n>] (subtask.c:<n>)"},
    ])
    assert [q.ref for q in build_queries(report)] == ["E001"]  # generic [1002510] code and cascades skipped
    result = find_related(report, KBIndex(ARTICLES))
    assert result["available"]
    assert result["articles"][0]["url"] == "https://kb.example/1"
    assert result["articles"][0]["matched_on"][0]["ref"] == "E001"
    assert all(a["url"] != "https://kb.example/5" for a in result["articles"])


def test_error_code_must_match_whole():
    report = _report([{"id": "E001", "severity": "E", "code": "ORA-01555", "pattern": "ORA-01555: snapshot too old"}])
    urls = [a["url"] for a in find_related(report, KBIndex(ARTICLES))["articles"]]
    assert urls[0] == "https://kb.example/3"
    assert "https://kb.example/4" not in urls  # mentions other ORA codes only


def test_nothing_matches_gives_empty_list():
    report = _report([{"id": "W001", "severity": "W", "code": None, "pattern": "Completely unknown wording zebra quokka"}])
    result = find_related(report, KBIndex(ARTICLES))
    assert result["available"] and result["articles"] == []
    assert "ignore" in result["note"]


def test_bundled_index_loads():
    index = get_index()
    assert index is not None and len(index) > 100
    assert all(a["url"].startswith("https://") for a in index.articles[:20])


def test_kb_route(tmp_path, sample_log):
    with server(tmp_path) as (_, client):
        job = upload(client, sample_log)
        assert wait_ready(client, job["id"])["state"] == "ready"
        first = client.get(f"/api/jobs/{job['id']}/kb")
        assert first.status_code == 200
        body = first.json()
        assert body["available"] and body["index"]["articles"] > 100
        for a in body["articles"]:
            assert {"title", "url", "description", "relevance", "confidence", "matched_on"} <= a.keys()
        assert client.get(f"/api/jobs/{job['id']}/kb").json() == body  # served from the per-job cache
        assert client.get("/api/jobs/nope/kb").status_code == 404


def test_kb_over_mcp(tmp_path, sample_log):
    with server(tmp_path) as (app, client):
        job = upload(client, sample_log)
        assert wait_ready(client, job["id"])["state"] == "ready"
        mcp = create_mcp_server(job_store=app.state.jobs, settings=app.state.settings)

        async def call(name, args):
            return json.loads((await mcp.call_tool(name, args)).content[0].text)

        kb = asyncio.run(call("get_kb_articles", {"job_id": job["id"]}))
        assert kb["available"] and kb["web_url"].endswith("?tab=kb") and "ignore" in kb["note"]
        packed = asyncio.run(call("pack", {"job_id": job["id"]}))
        assert [a["url"] for a in packed["kb_articles"]] == [a["url"] for a in kb["articles"]]
        assert packed["kb_note"] == kb["note"] and "kb_articles" in packed["analysis_rules"]
        assert "error" in asyncio.run(call("get_kb_articles", {"job_id": "nope"}))


def test_mcp_tools_return_plain_json():
    """No structuredContent wrapper: clients that prefer it would get {"result": "<escaped JSON>"}."""
    mcp = create_mcp_server()

    async def check():
        tools = await mcp.list_tools()
        assert tools and not any(t.output_schema for t in tools)
        result = await mcp.call_tool("list_analyses", {})
        assert result.structured_content is None
        assert "analyses" in json.loads(result.content[0].text)

    asyncio.run(check())
