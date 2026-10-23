// MCP settings page: tabbed layout with status, API keys, client setup.
import { api } from "../api.js";
import { copyText, h, mount, toast } from "../dom.js";

export function renderMcp(root) {
  mount(root, h("div", { class: "mcp-page" },
    h("div", { class: "empty" }, h("span", { class: "spinner" }), " Loading\u2026")));
  _load(root);
}

async function _load(root) {
  try {
    const [status, keys, configs] = await Promise.all([api.mcpStatus(), api.mcpKeys(), api.mcpClientConfig()]);
    _configs = configs;
    _draw(root, status, keys);
  } catch (err) {
    mount(root, h("div", { class: "mcp-page" },
      h("p", { class: "empty" }, "Could not load MCP settings: " + err.message),
      h("a", { class: "btn", href: "#/" }, "Back")));
  }
}

// ── state ───────────────────────────────────────────────────────────
let _activeTab = "overview";
let _pendingKey = null;
let _configs = null;   // placeholder-key snippets from /api/mcp/client-config

const EXTENSION_URL = "/api/mcp/extension.mcpb";

function _draw(root, status, keys) {
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "keys",     label: "API Keys", count: keys.length },
    { id: "config",   label: "Client Setup" },
  ];

  const tabBar = h("div", { class: "tabs" },
    ...tabs.map(t => {
      const btn = h("button", {
        class: "tab", role: "tab",
        "aria-selected": t.id === _activeTab ? "true" : "false",
        onclick: () => { _activeTab = t.id; _draw(root, status, keys); },
      }, t.label, t.count != null ? h("span", { class: "count" }, String(t.count)) : null);
      return btn;
    }));

  const panels = {
    overview: () => overviewPanel(status),
    keys:     () => keysPanel(root, keys, status),
    config:   () => configPanel(status, _configs),
  };

  mount(root, h("div", { class: "mcp-page" },
    h("div", { class: "row spread", style: "margin-bottom:8px" },
      h("h1", { style: "margin:0" }, "MCP Integration"),
      h("a", { class: "btn ghost small", href: "#/" }, "\u2190 Back")),
    h("p", { class: "muted", style: "margin-bottom:16px" },
      "Let AI assistants analyse Replicate logs through this LogPacker server."),
    tabBar,
    panels[_activeTab](),
  ));
}

// ── Overview tab ────────────────────────────────────────────────────

function overviewPanel(status) {
  const on = status.enabled;
  return h("div", { class: "mcp-grid" },
    card("Status", [
      h("table", { class: "tbl" },
        h("tbody", {},
          kvRow("MCP endpoint", on
            ? h("span", { class: "badge good" }, "Active")
            : h("span", { class: "badge warn" }, "No API keys")),
          on ? kvRow("URL", ic(status.mcp_url)) : null,
          kvRow("API keys", String(status.keys_count)),
        )),
      !on ? h("p", { class: "callout warn", style: "margin-top:10px;font-size:.88rem" },
        "Create an API key in the ", h("strong", {}, "API Keys"), " tab to activate.") : null,
    ]),
    card("How It Works", [
      h("ol", { class: "step-list" },
        h("li", {}, h("span", {},
          "Create an ", h("strong", {}, "API key"), " and send the config snippet to the user.")),
        h("li", {}, h("span", {},
          "The user pastes the snippet into their AI assistant\u2019s config (or installs the Claude Desktop extension).")),
        h("li", {}, h("span", {},
          "The user tells the AI: ", ic("analyze the log at C:\\logs\\task.log"))),
        h("li", {}, h("span", {},
          "The AI uploads the file to this server, runs the analysis, and returns findings.")),
      ),
      h("p", { class: "muted small" },
        "No folder configuration is needed \u2014 the user provides the file path in conversation and the AI handles the upload."),
    ]),
    card("How Each Client Connects", [
      h("table", { class: "tbl" },
        h("tbody", {},
          kvRow("Cursor", h("span", {}, "Direct \u2014 ", ic("~/.cursor/mcp.json"))),
          kvRow("Claude Code", h("span", {}, "Direct \u2014 ", ic("claude mcp add"))),
          kvRow("Claude Desktop", h("span", {},
            "Local bridge \u2014 LogPacker extension (", ic(".mcpb"), ") or ", ic("mcp-remote"))),
        )),
      h("p", { class: "muted small", style: "margin-top:8px" },
        "No LogPacker install on client machines. Claude Desktop can\u2019t reach a VPN-only server by URL, " +
        "so it runs a small bridge locally; the extension also adds an ", ic("upload_log"),
        " tool because Claude Desktop has no shell for the upload."),
    ]),
  );
}

// ── API Keys tab ────────────────────────────────────────────────────

function keysPanel(root, keys, status) {
  const input = h("input", { type: "text", placeholder: "label (e.g. user name or team)" });
  const btn = h("button", { class: "btn primary small", type: "button",
    onclick: () => _createKey(root, input) }, "+ New API key");

  return h("div", {},
    _pendingKey ? newKeyBanner(_pendingKey.owner, _pendingKey.result) : null,
    h("section", { class: "card" },
      h("h2", {}, "API Keys"),
      h("p", { class: "muted small" },
        "Each key lets one MCP client connect. The key is shown ", h("strong", {}, "once"),
        " when created \u2014 copy it immediately."),
      keys.length
        ? h("table", { class: "tbl" },
            h("thead", {}, h("tr", {}, h("th", {}, "Label"), h("th", {}, "Key (hash)"), h("th", {}))),
            h("tbody", {}, keys.map(k => h("tr", {},
              h("td", {}, k.owner),
              h("td", {}, ic(k.key_hash_prefix)),
              h("td", { style: "text-align:right" },
                h("button", { class: "btn ghost small danger", type: "button",
                  onclick: () => _revokeKey(root, k.key_hash_prefix) }, "Revoke")),
            ))))
        : h("p", { class: "callout" }, "No API keys yet. Create one to enable the MCP endpoint."),
      h("div", { class: "add-row" }, input, btn),
    ),
  );
}

function newKeyBanner(owner, result) {
  return h("section", { class: "card", style: "border-color:var(--accent);background:var(--accent-soft)" },
    h("div", { class: "row spread", style: "margin-bottom:8px" },
      h("h2", { style: "margin:0" }, "New key for \u201c" + owner + "\u201d"),
      h("button", { class: "btn small", type: "button",
        onclick: () => { _pendingKey = null; toast("Banner dismissed"); } }, "Dismiss")),
    h("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:10px" },
      h("code", { class: "inline-code",
        style: "flex:1;display:block;word-break:break-all;padding:8px 10px;font-size:.82rem" },
        result.key),
      h("button", { class: "btn primary small", type: "button",
        onclick: () => { copyText(result.key); toast("Key copied to clipboard"); } }, "Copy key")),
    h("p", { class: "muted small", style: "margin:0 0 10px" },
      "Save this key now \u2014 it cannot be shown again."),
    keySnippet("Cursor \u2014 ~/.cursor/mcp.json", result.configs.cursor),
    keySnippet("Claude Code \u2014 run in a terminal", result.configs.claude_code),
    keySnippet("Claude Desktop \u2014 mcp-remote (needs Node.js)", result.configs.claude_desktop),
    h("p", { class: "muted small", style: "margin:10px 0 0" },
      "Claude Desktop users can instead install the ",
      h("a", { href: EXTENSION_URL, download: "logpacker.mcpb" }, "LogPacker extension"),
      " and paste this key when prompted."),
  );
}

function keySnippet(title, value) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return h("details", {},
    h("summary", { style: "cursor:pointer;font-size:.88rem;font-weight:600" }, title),
    h("pre", { class: "config-pre" }, text),
    h("button", { class: "btn ghost small", style: "margin-top:6px", type: "button",
      onclick: () => { copyText(text); toast("Copied"); } }, "Copy"));
}

async function _createKey(root, input) {
  const owner = input.value.trim() || "user";
  try {
    const result = await api.mcpCreateKey(owner);
    _pendingKey = { owner, result };
    input.value = "";
    const [status, keys] = await Promise.all([api.mcpStatus(), api.mcpKeys()]);
    _draw(root, status, keys);
  } catch (err) { toast(err.message); }
}

async function _revokeKey(root, prefix) {
  if (!confirm("Revoke this key? The client using it will be disconnected.")) return;
  try {
    await api.mcpRevokeKey(prefix);
    toast("Key revoked");
    const [status, keys] = await Promise.all([api.mcpStatus(), api.mcpKeys()]);
    _draw(root, status, keys);
  } catch (err) { toast(err.message); }
}

// ── Client Setup tab ────────────────────────────────────────────────

function configPanel(status, configs) {
  if (!status.enabled) {
    return h("div", { class: "card" },
      h("p", { class: "callout warn" },
        "Create an API key first in the ", h("strong", {}, "API Keys"), " tab."));
  }

  const json = (v) => JSON.stringify(v, null, 2);
  const keyNote = h("span", {}, ", replacing ", ic("<your-api-key>"), " with a real key.");

  return h("div", {},
    status.public_url_set ? null : h("p", { class: "callout", style: "margin-bottom:12px" },
      "Snippets use ", ic(status.base_url), ", the address this page was opened on. If clients reach " +
      "the server under another name, set ", ic("LOGPACKER_PUBLIC_URL"), " on the server and restart."),
    h("div", { class: "mcp-grid" },
      card("Claude Desktop — Extension", [
        h("span", { class: "badge good", style: "margin-bottom:8px" }, "Recommended"),
        h("ol", { class: "step-list" },
          h("li", {}, h("span", {}, "Download ", ic("logpacker.mcpb"), " (server URL is pre-filled).")),
          h("li", {}, h("span", {}, "Double-click it, or drag it into Claude Desktop ",
            h("strong", {}, "Settings → Extensions"), ".")),
          h("li", {}, h("span", {}, "Paste the API key when prompted and enable the extension.")),
        ),
        h("p", { class: "muted small" },
          "No Node.js needed — it runs on Claude Desktop’s built-in runtime and adds an ",
          ic("upload_log"), " tool so Claude can send local log files, plus ready-made prompts (",
          ic("review_log"), ", ", ic("troubleshoot_errors"), ", ", ic("performance_review"), ", ",
          ic("customer_reply"), " and more) in the ", h("strong", {}, "+"), " menu."),
        h("a", { class: "btn primary small", href: EXTENSION_URL, download: "logpacker.mcpb" },
          "Download extension"),
      ]),
      snippetCard("Claude Desktop — mcp-remote", json(configs.claude_desktop),
        h("span", {}, "Requires ", h("strong", {}, "Node.js"), ". Paste into ",
          ic("claude_desktop_config.json"), keyNote, " Restart Claude Desktop afterwards.")),
      snippetCard("Cursor", json(configs.cursor),
        h("span", {}, "Paste into ", ic("~/.cursor/mcp.json"), keyNote)),
      snippetCard("Claude Code", configs.claude_code,
        h("span", {}, "Run in a terminal", keyNote)),
    ),
  );
}

function snippetCard(client, snippet, intro) {
  return card(client, [
    h("p", { class: "muted small" }, intro),
    h("pre", { class: "config-pre" }, snippet),
    h("button", { class: "btn ghost small", type: "button",
      onclick: () => { copyText(snippet); toast("Copied " + client + " config"); } }, "Copy"),
  ]);
}

// ── Helpers ─────────────────────────────────────────────────────────

function card(title, children) {
  return h("section", { class: "card" },
    h("h2", {}, title), ...children.filter(Boolean));
}

function kvRow(label, value) {
  return h("tr", {},
    h("td", { style: "color:var(--muted);width:130px" }, label),
    h("td", {}, typeof value === "string" ? value : value));
}

function ic(text) {
  return h("code", { class: "inline-code" }, text);
}
