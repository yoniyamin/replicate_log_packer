#!/usr/bin/env node
// LogPacker bridge for Claude Desktop.
//
// Relays MCP JSON-RPC between Claude Desktop (stdio) and the LogPacker server's
// streamable HTTP endpoint (/mcp), and adds one local tool, upload_log, that
// streams a file from this computer to the server — Claude Desktop has no shell
// to run the curl upload that create_analysis describes.
//
// No dependencies: runs on the Node that ships with Claude Desktop (fetch, streams).
"use strict";

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { Readable } = require("stream");

const BASE = (process.env.LOGPACKER_URL || "").trim().replace(/\/+$/, "");
const KEY = (process.env.LOGPACKER_API_KEY || "").trim();
const ENDPOINT = BASE + "/mcp";

let sessionId = null;
let protocolVersion = null;
let initRequest = null;  // replayed when the server forgets our session (e.g. after a restart)
let localSeq = 0;

const UPLOAD_TOOL = {
  name: "upload_log",
  description:
    "Upload a Qlik Replicate log file from THIS computer to LogPacker and start the analysis. " +
    "Use this instead of create_analysis + curl. Pass the absolute path the user gave you. " +
    "Returns a job_id; then call check_analysis(job_id) until the state is ready.",
  inputSchema: {
    type: "object",
    properties: {
      path: { type: "string", description: "Absolute path of the log file on this computer" },
      sanitize_enabled: { type: "boolean", description: "Redact names, hosts, IPs and secrets (default true)" },
      mask_objects: { type: "boolean", description: "Also mask schema/table names (default false)" },
      custom_terms: { type: "array", items: { type: "string" }, description: "Extra terms to redact" },
    },
    required: ["path"],
  },
};

function log(message) {
  process.stderr.write("[logpacker] " + message + "\n");
}

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

function authHeaders() {
  return KEY ? { Authorization: "Bearer " + KEY } : {};
}

// ── HTTP transport ──────────────────────────────────────────────────

function parseSse(text) {
  const out = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const data = block.split(/\r?\n/)
      .filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue;
    try { out.push(JSON.parse(data)); } catch { log("skipped unparsable SSE event"); }
  }
  return out;
}

/** POST one JSON-RPC message; resolves to the JSON-RPC messages the server sent back. */
async function post(msg) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    ...authHeaders(),
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;
  if (protocolVersion) headers["MCP-Protocol-Version"] = protocolVersion;

  const res = await fetch(ENDPOINT, { method: "POST", headers, body: JSON.stringify(msg) });
  const sid = res.headers.get("mcp-session-id");
  if (sid) sessionId = sid;
  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status} from ${ENDPOINT}: ${text.slice(0, 300)}`);
    err.status = res.status;
    throw err;
  }
  if (res.status === 202 || !text.trim()) return [];
  if ((res.headers.get("content-type") || "").includes("text/event-stream")) return parseSse(text);
  const body = JSON.parse(text);
  return Array.isArray(body) ? body : [body];
}

async function forward(msg) {
  try {
    return await post(msg);
  } catch (err) {
    if (err.status !== 404 || !sessionId || !initRequest || msg.method === "initialize") throw err;
    log("session expired, re-initialising");
    sessionId = null;
    await post(initRequest);
    await post({ jsonrpc: "2.0", method: "notifications/initialized" });
    return await post(msg);
  }
}

// ── local upload_log tool ───────────────────────────────────────────

function textResult(data, isError = false) {
  return { content: [{ type: "text", text: JSON.stringify(data) }], isError };
}

async function callRemoteTool(name, args) {
  const id = "logpacker-bridge-" + (++localSeq);
  const replies = await forward({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } });
  const reply = replies.find(r => r.id === id);
  if (!reply) throw new Error(`no reply to ${name}`);
  if (reply.error) throw new Error(reply.error.message);
  const text = (reply.result.content || []).map(c => c.text || "").join("");
  if (reply.result.isError) throw new Error(text);
  return JSON.parse(text);
}

async function uploadLog(args) {
  try {
    const file = path.resolve(String(args.path || "").trim().replace(/^"(.*)"$/, "$1"));
    const stat = await fs.promises.stat(file);
    if (!stat.isFile()) throw new Error(`${file} is not a file`);

    const job = await callRemoteTool("create_analysis", {
      filename: path.basename(file),
      size: stat.size,
      sanitize_enabled: args.sanitize_enabled ?? true,
      mask_objects: args.mask_objects ?? false,
      custom_terms: args.custom_terms || [],
    });
    if (job.error) throw new Error(job.error);

    // Build the URL from our own server address: the one in job.upload_url may
    // be the server's bind address rather than something this machine can reach.
    const res = await fetch(`${BASE}/api/jobs/${job.job_id}/content`, {
      method: "PUT",
      headers: authHeaders(),
      body: Readable.toWeb(fs.createReadStream(file)),
      duplex: "half",
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`upload failed (HTTP ${res.status}): ${text.slice(0, 300)}`);

    return textResult({
      job_id: job.job_id,
      filename: path.basename(file),
      bytes: stat.size,
      next_step: "Call check_analysis(job_id) until state is ready, then pack or get_errors.",
    });
  } catch (err) {
    return textResult({ error: err.message }, true);
  }
}

// ── message loop ────────────────────────────────────────────────────

async function handle(msg) {
  const isRequest = msg.method !== undefined && msg.id !== undefined;

  if (msg.method === "tools/call" && msg.params && msg.params.name === UPLOAD_TOOL.name) {
    send({ jsonrpc: "2.0", id: msg.id, result: await uploadLog(msg.params.arguments || {}) });
    return;
  }
  if (msg.method === "initialize") initRequest = msg;

  try {
    for (const reply of await forward(msg)) {
      if (reply.id === msg.id && reply.result) {
        if (msg.method === "initialize") protocolVersion = reply.result.protocolVersion || null;
        if (msg.method === "tools/list" && Array.isArray(reply.result.tools)) reply.result.tools.unshift(UPLOAD_TOOL);
      }
      send(reply);
    }
  } catch (err) {
    log(err.message);
    if (isRequest) {
      send({ jsonrpc: "2.0", id: msg.id,
             error: { code: -32603, message: `LogPacker server error at ${ENDPOINT}: ${err.message}` } });
    }
  }
}

if (!BASE) log("LOGPACKER_URL is not set — configure the extension's server URL in Claude Desktop.");
if (!KEY) log("LOGPACKER_API_KEY is not set — configure the extension's API key in Claude Desktop.");

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", line => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { log("ignored a non-JSON line from the client"); return; }
  handle(msg).catch(err => log(err.stack || err.message));
});
rl.on("close", () => process.exit(0));
