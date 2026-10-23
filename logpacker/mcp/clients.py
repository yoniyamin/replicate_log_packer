"""Client connection configs for the LogPacker MCP endpoint.

One place builds every snippet the CLI and web UI hand out:

* **Cursor / VS Code** — native remote MCP: ``url`` + ``headers``.
* **Claude Code** — native remote MCP via ``claude mcp add --transport http``.
* **Claude Desktop** — ``claude_desktop_config.json`` only accepts local
  (stdio) servers, and its custom connectors are reached from Anthropic's
  cloud, which cannot see a VPN-only server.  So Desktop needs a local bridge:

  - the **LogPacker extension** (``.mcpb``): a dependency-free Node bridge that
    runs on Claude Desktop's built-in Node and adds a local ``upload_log`` tool
    (Desktop has no shell to run the curl upload), or
  - **mcp-remote** via ``npx`` (needs Node.js on the client).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

EXTENSION_DIR = Path(__file__).parent / "extension"
KEY_PLACEHOLDER = "<your-api-key>"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def mcp_endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/mcp"


def url_entry(base_url: str, key: str = KEY_PLACEHOLDER) -> dict:
    """Native remote entry for Cursor, VS Code and other url-capable clients."""
    return {"url": mcp_endpoint(base_url), "headers": {"Authorization": f"Bearer {key}"}}


def mcp_remote_entry(base_url: str, key: str = KEY_PLACEHOLDER) -> dict:
    """Claude Desktop entry that bridges stdio to the HTTP endpoint with ``mcp-remote``.

    The header goes through an env var so the space in ``Bearer <key>`` never
    lands in ``args`` (npx on Windows splits arguments on spaces).
    """
    url = mcp_endpoint(base_url)
    args = ["-y", "mcp-remote", url, "--header", "Authorization:${LOGPACKER_AUTH}"]
    parsed = urlparse(url)
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        args.append("--allow-http")  # mcp-remote refuses plain http to non-local hosts otherwise
    return {"command": "npx", "args": args, "env": {"LOGPACKER_AUTH": f"Bearer {key}"}}


def claude_code_command(base_url: str, key: str = KEY_PLACEHOLDER) -> str:
    return (f'claude mcp add --transport http logpacker {mcp_endpoint(base_url)} '
            f'--header "Authorization: Bearer {key}"')


def mcp_servers(entry: dict) -> dict:
    return {"mcpServers": {"logpacker": entry}}


def all_configs(base_url: str, key: str = KEY_PLACEHOLDER) -> dict:
    """Every client snippet, keyed the way the web UI renders them."""
    return {
        "cursor": mcp_servers(url_entry(base_url, key)),
        "claude_code": claude_code_command(base_url, key),
        "claude_desktop": mcp_servers(mcp_remote_entry(base_url, key)),
    }


def build_mcpb(base_url: str) -> bytes:
    """Zip the Claude Desktop extension, pre-filling *base_url* as the server URL.

    The extension carries its own ``version`` in ``extension/manifest.json`` —
    bump it whenever ``server/index.js`` or the manifest changes, or Claude
    Desktop treats the new file as the build it already has installed.
    """
    manifest = json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    manifest["user_config"]["server_url"]["default"] = base_url.rstrip("/")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.write(EXTENSION_DIR / "server" / "index.js", "server/index.js")
    return buf.getvalue()
