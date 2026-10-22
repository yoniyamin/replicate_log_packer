"""`logpacker` / `python -m logpacker`: start the local app and open the browser.

Sub-commands::

    logpacker                   start the web server (default, includes MCP SSE)
    logpacker mcp               start the MCP server (stdio, for local use)
    logpacker mcp install       server-side setup: generate first API key
    logpacker mcp add-key       generate an API key for a new user
    logpacker mcp client-config print the JSON snippet clients paste into Cursor / Claude Desktop
    logpacker mcp extension     build the Claude Desktop extension (.mcpb)
    logpacker mcp config        show current MCP configuration
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser

import uvicorn

from logpacker.config import get_settings


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(prog="logpacker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")

    sub = parser.add_subparsers(dest="command")
    mcp_parser = sub.add_parser("mcp", help="MCP server for AI assistants")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_sub.add_parser("install", help="server-side setup wizard (generate first API key)")
    mcp_sub.add_parser("config", help="show current MCP configuration")

    key_parser = mcp_sub.add_parser("add-key", help="generate an API key for a new MCP client")
    key_parser.add_argument("--owner", default="user", help="label for the key owner (default: user)")

    cc_parser = mcp_sub.add_parser("client-config",
                                   help="print the JSON snippet for Cursor or Claude Desktop")
    cc_parser.add_argument("--url", help="server URL (default: http://host:port)")
    cc_parser.add_argument("--key", help="API key (omit to generate a new one)")
    cc_parser.add_argument("--client", choices=["cursor", "claude", "claude-code", "json"], default="json",
                           help="cursor: write ~/.cursor/mcp.json; claude: write claude_desktop_config.json "
                                "(mcp-remote bridge, needs Node.js); claude-code: print the "
                                "'claude mcp add' command; json: print the url snippet (default)")

    ext_parser = mcp_sub.add_parser("extension",
                                    help="build the Claude Desktop extension (.mcpb) for this server")
    ext_parser.add_argument("--url", help="server URL baked in as the default (default: http://host:port)")
    ext_parser.add_argument("--out", default="logpacker.mcpb", help="output file (default: logpacker.mcpb)")

    args = parser.parse_args()

    if args.command == "mcp":
        _handle_mcp(args, settings)
        return

    url = f"http://{settings.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"LogPacker running at {url}  (Ctrl+C to stop)")
    uvicorn.run("logpacker.web.app:create_app", factory=True, host=settings.host, port=args.port,
                log_level="warning")


# ── MCP sub-commands ──────────────────────────────────────────────────

def _handle_mcp(args, settings) -> None:
    if args.mcp_command == "install":
        from logpacker.mcp.install import run_install
        run_install()
    elif args.mcp_command == "config":
        _show_mcp_config(settings)
    elif args.mcp_command == "add-key":
        _add_key(settings, args.owner)
    elif args.mcp_command == "client-config":
        _client_config(settings, args.url, args.key, args.client)
    elif args.mcp_command == "extension":
        _build_extension(settings, args.url, args.out)
    else:
        _run_mcp_stdio(settings)


def _run_mcp_stdio(settings) -> None:
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        print("The 'mcp' package is required for MCP mode.  Install it with:", file=sys.stderr)
        print("  uv pip install 'mcp>=2'", file=sys.stderr)
        sys.exit(1)

    from logpacker.mcp.config import MCPConfig
    from logpacker.mcp.server import create_mcp_server
    from logpacker.web.jobs import JobStore

    mcp_config = MCPConfig.load(settings.data_dir)
    job_store = JobStore(settings)
    mcp = create_mcp_server(job_store=job_store, settings=settings, mcp_config=mcp_config)
    mcp.run(transport="stdio")


def _show_mcp_config(settings) -> None:
    from logpacker.mcp.config import MCPConfig
    config = MCPConfig.load(settings.data_dir)
    print(json.dumps({
        "data_dir": str(settings.data_dir),
        "config_file": str(settings.data_dir / "mcp_config.json"),
        "api_keys_count": len(config.api_keys),
        "max_concurrent": config.max_concurrent,
    }, indent=2))


def _add_key(settings, owner: str) -> None:
    from logpacker.mcp.config import MCPConfig
    config = MCPConfig.load(settings.data_dir)
    key = config.generate_api_key(owner)
    config.save(settings.data_dir)
    print(f"API key for '{owner}': {key}")
    print("Give this to the user.  It cannot be shown again.")


def _client_config(settings, url: str | None, key: str | None, client: str) -> None:
    from logpacker.mcp import clients
    from logpacker.mcp.config import MCPConfig
    config = MCPConfig.load(settings.data_dir)

    url = url or settings.base_url

    if not key:
        key = config.generate_api_key("client-config")
        config.save(settings.data_dir)
        print(f"Generated new API key: {key}", file=sys.stderr)
        print(file=sys.stderr)

    if client == "claude-code":
        print(clients.claude_code_command(url, key))
        return

    # Claude Desktop only accepts stdio servers in its config file, so it gets
    # the mcp-remote bridge; url-capable clients get the native entry.
    if client == "claude":
        from logpacker.mcp.install import _claude_desktop_config_path as config_path
        entry = clients.mcp_remote_entry(url, key)
    else:
        from logpacker.mcp.install import _cursor_mcp_path as config_path
        entry = clients.url_entry(url, key)

    path = config_path() if client != "json" else None
    if path:
        from logpacker.mcp.install import _merge_mcp_entry
        _merge_mcp_entry(path, entry)
        print(f"Written to {path}")
    else:
        print(json.dumps(clients.mcp_servers(entry), indent=2))


def _build_extension(settings, url: str | None, out: str) -> None:
    from pathlib import Path

    from logpacker.mcp.clients import build_mcpb
    path = Path(out)
    path.write_bytes(build_mcpb(url or settings.base_url))
    print(f"Written to {path.resolve()}")
    print("Users open it in Claude Desktop (Settings > Extensions, or double-click) and enter their API key.")


if __name__ == "__main__":
    main()
