"""The Claude Desktop extension: its manifest prompts and the bundle built from them."""

import io
import json
import re
import zipfile

from logpacker.mcp.clients import EXTENSION_DIR, build_mcpb

MANIFEST = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
BRIDGE = (EXTENSION_DIR / "server" / "index.js").read_text(encoding="utf-8")
PLACEHOLDER = re.compile(r"\$\{arguments\.(\w+)\}")
TOOLS = {"analyze_log", "upload_log", "check_analysis", "pack", "get_errors", "get_error_detail", "get_performance",
         "get_batch_details", "get_fullload", "get_signals", "get_overview", "get_lines", "search_log",
         "get_kb_articles", "list_analyses"}


def test_prompts_are_well_formed():
    prompts = MANIFEST["prompts"]
    assert len({p["name"] for p in prompts}) == len(prompts) >= 5
    bridge_args = set(re.findall(r"^  (\w+): \{ description:", BRIDGE, re.M))  # PROMPT_ARGS keys
    for p in prompts:
        assert p["name"] and p["description"] and p["text"]
        used = set(PLACEHOLDER.findall(p["text"]))
        assert used == set(p["arguments"]), p["name"]   # every placeholder declared, every argument used
        assert used <= bridge_args, p["name"]            # the bridge knows how to describe each argument
        assert "file_path" in used


def test_prompts_only_name_real_tools():
    known = TOOLS | {"web_checks", "key_lines", "kb_articles", "analysis_rules", "investigation", "prompt_text",
                     "job_id", "web_url", "verbose_loggers", "first_line"}
    for p in MANIFEST["prompts"]:
        for name in re.findall(r"\b([a-z]+_[a-z_]+)\b", PLACEHOLDER.sub("", p["text"])):
            assert name in known or name in {"host_01", "user_02"}, f"{p['name']}: unknown name {name}"


def test_bundle_carries_prompts():
    zf = zipfile.ZipFile(io.BytesIO(build_mcpb("http://logpacker.corp:8765")))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["prompts"] == MANIFEST["prompts"]
    assert manifest["version"] == MANIFEST["version"]
    assert manifest["user_config"]["server_url"]["default"] == "http://logpacker.corp:8765"
