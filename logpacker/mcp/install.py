"""Interactive MCP server installer (server-side).

``logpacker mcp install`` generates the first API key and prints
connection instructions.  Further keys can be created from the web UI.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

from logpacker.config import get_settings
from logpacker.mcp.config import MCPConfig


# ── tiny TUI helpers ──────────────────────────────────────────────────

def _input(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    result = input(f"  {prompt}{suffix}: ").strip()
    return result or default


def _confirm(prompt: str, default: bool = True) -> bool:
    yn = "Y/n" if default else "y/N"
    result = input(f"  {prompt} [{yn}]: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


# ── main installer ────────────────────────────────────────────────────

def run_install() -> None:
    settings = get_settings()
    mcp_config = MCPConfig.load(settings.data_dir)

    print()
    print("  +---------------------------------------+")
    print("  |  LogPacker MCP Server Setup            |")
    print("  +---------------------------------------+")
    print()

    # ── 1. Generate API key ───────────────────────────────────────────

    if not mcp_config.api_keys:
        print("  Generating an API key for MCP clients...")
        owner = _input("Key label (e.g. user name)", "user")
        key = mcp_config.generate_api_key(owner)
        print(f"  KEY: {key}")
        print("  (save this -- it cannot be shown again)")
    else:
        print(f"  {len(mcp_config.api_keys)} API key(s) already configured.")
        if _confirm("Generate an additional key?", default=False):
            owner = _input("Key label", "user")
            key = mcp_config.generate_api_key(owner)
            print(f"  KEY: {key}")
        else:
            key = None
    print()

    # ── 2. Save ───────────────────────────────────────────────────────

    mcp_config.save(settings.data_dir)
    print(f"  SAVED: {settings.data_dir / 'mcp_config.json'}")
    print()

    # ── 3. Next steps ─────────────────────────────────────────────────

    base = settings.base_url
    print("  Next steps:")
    print()
    print("  1. Start (or restart) LogPacker:")
    print(f"       logpacker --port {settings.port}")
    print(f"     The MCP endpoint will be at {base}/mcp")
    print()
    print("  2. Give users a connection config:")
    print(f"       logpacker mcp client-config --url {base}")
    print("     This prints the JSON they paste into Cursor (--client claude for the")
    print("     Claude Desktop mcp-remote bridge, --client claude-code for Claude Code).")
    print("     Claude Desktop users can instead install the extension:")
    print(f"       logpacker mcp extension --url {base}   (or download it from the web UI)")
    print()
    print("  3. Or manage keys and configs from the web UI:")
    print(f"       {base}/#/mcp")
    print()
    print("  Log files stay on the client machine -- the AI assistant uploads")
    print("  them to this server via HTTP for analysis.")
    print()


# ── helpers shared with __main__.py ───────────────────────────────────

def _merge_mcp_entry(config_path: Path, server_entry: dict) -> Path:
    """Read *config_path*, insert/replace the ``logpacker`` MCP entry and write back."""
    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["logpacker"] = server_entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return config_path


def _cursor_mcp_path() -> Path | None:
    """Global Cursor MCP config: ``~/.cursor/mcp.json``."""
    return Path.home() / ".cursor" / "mcp.json"


def _claude_desktop_config_path() -> Path | None:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        if not base:
            return None
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
