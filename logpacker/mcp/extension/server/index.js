#!/usr/bin/env node
// LogPacker bridge for Claude Desktop.
//
// Relays MCP JSON-RPC between Claude Desktop (stdio) and the LogPacker server's
// streamable HTTP endpoint (/mcp), and adds local tools that read files on this
// computer — Claude Desktop has no shell to run the curl upload that
// create_analysis describes:
//   analyze_log  upload + wait for the analysis + summary, with progress notifications
//   upload_log   upload only (returns a job_id for check_analysis)
// It also serves the prompt templates declared in manifest.json (prompts/list, prompts/get)
// instead of the server's, which are written for clients that upload with curl.
//
// No dependencies: runs on the Node that ships with Claude Desktop (fetch, streams).
"use strict";

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { Readable, Transform, pipeline } = require("stream");

// analyze_log waits at most this long in total, so the call stays well under
// client request timeouts; past it, Claude polls check_analysis itself.
const WAIT_BUDGET_MS = 45000;
const POLL_MS = 2000;

const BASE = (process.env.LOGPACKER_URL || "").trim().replace(/\/+$/, "");
const KEY = (process.env.LOGPACKER_API_KEY || "").trim();
const ENDPOINT = BASE + "/mcp";
let MANIFEST = {};
try { MANIFEST = require("../manifest.json"); } catch { /* manifest not next to us */ }
const BRIDGE_VERSION = MANIFEST.version || "?";

let sessionId = null;
let protocolVersion = null;
let initRequest = null;  // replayed when the server forgets our session (e.g. after a restart)
let localSeq = 0;

const FILE_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string", description: "Absolute path of the log file (.log, .txt, .zip, .gz) on this computer" },
    sanitize_enabled: { type: "boolean", description: "Redact names, hosts, IPs and secrets (default true)" },
    mask_objects: { type: "boolean", description: "Also mask schema/table names (default false)" },
    custom_terms: { type: "array", items: { type: "string" }, description: "Extra terms to redact" },
  },
  required: ["path"],
};

const ANALYZE_TOOL = {
  name: "analyze_log",
  description:
    "Analyze a Qlik Replicate task log that is on THIS computer. Use this whenever the user asks to " +
    "analyze, review, check, troubleshoot or explain a log file and gives a path such as " +
    "C:\\logs\\reptask_X.log. You cannot read local files yourself, but this tool can: it uploads the " +
    "file to the company LogPacker server, waits for the analysis and returns the health score, " +
    "error/warning counts, top recommendations and Qlik KB articles that may relate to the findings " +
    "(keyword-matched candidates: use only those that match the evidence). Then call pack(job_id) for " +
    "the full evidence prompt, and get_lines to quote specific log lines.",
  inputSchema: FILE_SCHEMA,
};

const UPLOAD_TOOL = {
  name: "upload_log",
  description:
    "Upload a log file from THIS computer to LogPacker without waiting for the analysis. " +
    "Prefer analyze_log; use this only to start several uploads at once. " +
    "Returns a job_id for check_analysis.",
  inputSchema: FILE_SCHEMA,
};

const LOCAL_TOOLS = [ANALYZE_TOOL, UPLOAD_TOOL];

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
  // initialize always opens a fresh session: Claude Desktop may initialize
  // this process more than once, and a repeat on a live session ends it.
  if (sessionId && msg.method !== "initialize") headers["Mcp-Session-Id"] = sessionId;
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

let reinit = null;  // single in-flight re-initialisation shared by concurrent requests

function reinitialise(staleId) {
  if (!reinit) {
    log("session expired, re-initialising");
    reinit = (async () => {
      if (sessionId === staleId) sessionId = null;
      await post(initRequest);
      await post({ jsonrpc: "2.0", method: "notifications/initialized" });
    })().finally(() => { reinit = null; });
  }
  return reinit;
}

async function forward(msg) {
  if (reinit && msg.method !== "initialize") await reinit.catch(() => {});
  const usedId = sessionId;
  try {
    return await post(msg);
  } catch (err) {
    if (err.status !== 404 || !usedId || !initRequest || msg.method === "initialize") throw err;
    // Another request may already have replaced the session; only re-init if not.
    if (sessionId === usedId) await reinitialise(usedId);
    return await post(msg);
  }
}

// ── local tools: analyze_log, upload_log ────────────────────────────

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

function mb(bytes) {
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Sends MCP progress notifications for one tool call (only if the client asked, via a progressToken). */
function progressReporter(msg) {
  const token = msg.params && msg.params._meta ? msg.params._meta.progressToken : undefined;
  let last = -1;
  return (percent, message) => {
    const progress = Math.min(100, Math.round(percent));
    if (token === undefined || progress <= last) return;  // progress must keep increasing
    last = progress;
    send({ jsonrpc: "2.0", method: "notifications/progress",
           params: { progressToken: token, progress, total: 100, message } });
  };
}

function resolvePath(raw) {
  return path.resolve(String(raw || "").trim().replace(/^"(.*)"$/, "$1"));
}

/**
 * Create the job and stream the file to it. onFraction(0..1) is called as bytes go out.
 * Resolves to {job_id, filename, bytes, secs, cached}.
 */
async function doUpload(file, args, onFraction = () => {}) {
  const started = Date.now();
  const stat = await fs.promises.stat(file);
  if (!stat.isFile()) throw new Error(`${file} is not a file`);
  const filename = path.basename(file);
  log(`upload: ${file} (${mb(stat.size)})`);

  const job = await callRemoteTool("create_analysis", {
    filename,
    size: stat.size,
    sanitize_enabled: args.sanitize_enabled ?? true,
    mask_objects: args.mask_objects ?? false,
    custom_terms: args.custom_terms || [],
  });
  if (job.error) throw new Error(job.error);

  let sent = 0;
  const counter = new Transform({
    transform(chunk, _enc, done) {
      sent += chunk.length;
      onFraction(stat.size ? sent / stat.size : 1);
      done(null, chunk);
    },
  });
  pipeline(fs.createReadStream(file), counter, err => { if (err) log(`read failed for ${file}: ${err.message}`); });

  // Build the URL from our own server address: the one in job.upload_url may
  // be the server's bind address rather than something this machine can reach.
  const res = await fetch(`${BASE}/api/jobs/${job.job_id}/content`, {
    method: "PUT",
    headers: authHeaders(),
    body: Readable.toWeb(counter),
    duplex: "half",
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`upload failed (HTTP ${res.status}): ${text.slice(0, 300)}`);

  // A file analysed before comes back as that earlier job, under its own id.
  let done = {};
  try { done = JSON.parse(text); } catch { /* keep the created id */ }
  const result = { job_id: done.id || job.job_id, filename, bytes: stat.size,
                   secs: (Date.now() - started) / 1000, cached: done.cached === true };
  log(`upload: ${filename} -> job ${result.job_id}, ${mb(stat.size)} in ${result.secs.toFixed(1)}s ` +
      `(${mb(stat.size / Math.max(result.secs, 0.001))}/s)${result.cached ? ", matched an earlier analysis" : ""}`);
  return result;
}

async function uploadLog(args) {
  const file = resolvePath(args.path);
  try {
    const up = await doUpload(file, args);
    return textResult({
      job_id: up.job_id,
      filename: up.filename,
      bytes: up.bytes,
      cached: up.cached,
      next_step: "Call check_analysis(job_id) until state is ready, then pack(job_id).",
    });
  } catch (err) {
    log(`upload_log FAILED for ${file}: ${err.message}`);
    return textResult({ error: err.message }, true);
  }
}

async function analyzeLog(args, report) {
  const started = Date.now();
  const file = resolvePath(args.path);
  const name = path.basename(file);
  try {
    report(1, `Uploading ${name}`);
    const up = await doUpload(file, args, f => report(1 + 39 * f, `Uploading ${name}: ${Math.round(f * 100)}%`));
    report(40, up.cached ? `${name} was analysed before; loading results` : `Uploaded ${name}; analysing`);

    let status;
    for (;;) {
      status = await callRemoteTool("check_analysis", { job_id: up.job_id });
      if (status.error) throw new Error(status.error);
      if (status.state === "ready" || Date.now() - started > WAIT_BUDGET_MS) break;
      const f = status.progress || 0;
      report(40 + 55 * f, `Analysing ${name}: ${Math.round(f * 100)}%`);
      await sleep(POLL_MS);
    }

    const secs = ((Date.now() - started) / 1000).toFixed(1);
    if (status.state !== "ready") {
      log(`analyze_log: ${name} still ${status.state} after ${secs}s (job ${up.job_id})`);
      return textResult({
        job_id: up.job_id, filename: name, state: status.state, progress: status.progress,
        next_step: "The analysis is still running. Call check_analysis(job_id) every few seconds " +
                   "until state is ready, then pack(job_id).",
      });
    }

    report(97, "Collecting recommendations and related KB articles");
    const [recommendations, kb] = await Promise.all([topRecommendations(up.job_id), kbArticles(up.job_id)]);
    report(100, `Analysis of ${name} ready`);
    const s = status.stats || {};
    log(`analyze_log: ${name} ready in ${secs}s (job ${up.job_id}, health ${(status.health || {}).score ?? "?"}/5, ` +
        `${s.errors ?? "?"} errors, ${s.warnings ?? "?"} warnings, ${kb.articles.length} KB articles)`);
    return textResult({
      ...status,
      cached: up.cached,
      top_recommendations: recommendations,
      kb_articles: kb.articles,
      kb_note: kb.note,
      next_steps: "Call pack(job_id) for the full evidence prompt and answer in its format, following its " +
                  "analysis_rules (investigate, verify on the web, quote lines). Use get_errors / get_performance / get_batch_details for detail and " +
                  "get_lines to quote specific lines. kb_articles are keyword-matched Qlik KB candidates: weigh each " +
                  "against the evidence, use only those that match, and ignore the rest. Share web_url for the interactive report.",
    });
  } catch (err) {
    log(`analyze_log FAILED for ${file}: ${err.message}`);
    return textResult({ error: err.message }, true);
  }
}

async function topRecommendations(jobId) {
  try {
    const perf = await callRemoteTool("get_performance", { job_id: jobId });
    return (perf.recommendations || []).slice(0, 5)
      .map(r => ({ priority: r.priority, area: r.area, title: r.title, detail: r.description }));
  } catch (err) {
    log(`analyze_log: recommendations unavailable: ${err.message}`);
    return [];
  }
}

/** Related KB articles, trimmed to what the model needs to judge them. Optional: [] when unavailable. */
async function kbArticles(jobId) {
  try {
    const kb = await callRemoteTool("get_kb_articles", { job_id: jobId });
    if (kb.error) throw new Error(kb.error);
    const articles = (kb.articles || []).map(a => ({
      title: a.title, url: a.url, description: a.description, confidence: a.confidence,
      relevance: a.relevance, matched_on: (a.matched_on || []).map(m => m.label),
    }));
    return { articles, note: kb.note || "" };
  } catch (err) {
    log(`analyze_log: KB articles unavailable: ${err.message}`);  // e.g. an older server without the tool
    return { articles: [], note: "" };
  }
}

const LOCAL_HANDLERS = {
  [ANALYZE_TOOL.name]: (args, report) => analyzeLog(args, report),
  [UPLOAD_TOOL.name]: args => uploadLog(args),
};

// ── prompts: the templates declared in manifest.json ────────────────
// The manifest is the single source, so what Claude Desktop shows from the
// declaration and what prompts/list returns at runtime cannot drift apart.

const LOG_ARG = "Path of the log on this computer (.log, .txt, .zip, .gz), or the job_id of an earlier analysis";
const PROMPT_ARGS = {  // the manifest lists argument names only; this is what the client shows for each
  file_path: { description: LOG_ARG, required: true },
  second_file_path: { description: "The second log: " + LOG_ARG[0].toLowerCase() + LOG_ARG.slice(1), required: true },
  question: { description: "Your question, the customer's question or other context (optional)", required: false },
  error: { description: "An error text or pattern ID such as E002 to start with (optional)", required: false },
  latency_goal: { description: "The latency you need, e.g. 'under 60 seconds' (optional)", required: false },
};
const PROMPTS = Array.isArray(MANIFEST.prompts) ? MANIFEST.prompts : [];
const PLACEHOLDER = /\$\{arguments\.(\w+)\}/g;

function listPrompts() {
  return {
    prompts: PROMPTS.map(p => ({
      name: p.name,
      description: p.description,
      arguments: (p.arguments || []).map(name => ({ name, ...(PROMPT_ARGS[name] || { required: false }) })),
    })),
  };
}

/** Fill ${arguments.x}. A line whose placeholders are all empty is dropped, so optional arguments leave no gaps. */
function renderPrompt(text, args) {
  const value = name => String(args[name] ?? "").trim();
  return text.split("\n")
    .filter(line => {
      const names = [...line.matchAll(PLACEHOLDER)].map(m => m[1]);
      return !names.length || names.some(name => value(name));
    })
    .join("\n")
    .replace(PLACEHOLDER, (_, name) => value(name));
}

function getPrompt(params) {
  const prompt = PROMPTS.find(p => p.name === params.name);
  if (!prompt) throw rpcError(-32602, `Unknown prompt '${params.name}'. Available: ${PROMPTS.map(p => p.name).join(", ")}`);
  const args = params.arguments || {};
  const missing = (prompt.arguments || []).filter(name => (PROMPT_ARGS[name] || {}).required && !String(args[name] ?? "").trim());
  if (missing.length) throw rpcError(-32602, `Prompt '${prompt.name}' needs: ${missing.join(", ")}`);
  return {
    description: prompt.description,
    messages: [{ role: "user", content: { type: "text", text: renderPrompt(prompt.text, args) } }],
  };
}

function rpcError(code, message) {
  return Object.assign(new Error(message), { rpcCode: code });
}

const LOCAL_METHODS = {
  "prompts/list": () => listPrompts(),
  "prompts/get": params => getPrompt(params || {}),
};

// ── message loop ────────────────────────────────────────────────────

/** One line per tool call: name, the job/file it was about, outcome and duration. */
function logToolResult(name, args, reply, ms) {
  const a = args || {};
  const about = ["job_id", "filename", "pattern_id", "pattern"]
    .filter(k => a[k] !== undefined).map(k => `${k}=${String(a[k]).slice(0, 60)}`).join(", ");
  const text = reply.result ? (reply.result.content || []).map(c => c.text || "").join("") : "";
  let body = null;
  try { body = JSON.parse(text); } catch { /* not JSON */ }

  let outcome = "ok";
  if (reply.error) outcome = "ERROR: " + reply.error.message;
  else if (body && body.error) outcome = "ERROR: " + body.error;
  else if (reply.result && reply.result.isError) outcome = "ERROR: " + text;
  else if (body && body.state) outcome = body.state;  // check_analysis: processing / ready
  else if (name === "get_kb_articles" && body && Array.isArray(body.articles)) outcome = `${body.articles.length} articles`;
  log(`${name}(${about}) -> ${String(outcome).slice(0, 300)} in ${ms} ms`);
}

async function handle(msg) {
  const isRequest = msg.method !== undefined && msg.id !== undefined;

  const local = msg.method === "tools/call" && msg.params ? LOCAL_HANDLERS[msg.params.name] : null;
  if (local) {
    send({ jsonrpc: "2.0", id: msg.id, result: await local(msg.params.arguments || {}, progressReporter(msg)) });
    return;
  }
  const method = isRequest ? LOCAL_METHODS[msg.method] : null;
  if (method) {
    try {
      send({ jsonrpc: "2.0", id: msg.id, result: method(msg.params) });
      if (msg.method === "prompts/get") log(`prompt ${msg.params.name}`);
    } catch (err) {
      log(`${msg.method} failed: ${err.message}`);
      send({ jsonrpc: "2.0", id: msg.id, error: { code: err.rpcCode || -32603, message: err.message } });
    }
    return;
  }
  if (msg.method === "initialize") initRequest = msg;

  const toolName = msg.method === "tools/call" && msg.params ? msg.params.name : null;
  const started = Date.now();
  try {
    for (const reply of await forward(msg)) {
      if (reply.id === msg.id && reply.result) {
        if (msg.method === "initialize") {
          protocolVersion = reply.result.protocolVersion || null;
          if (PROMPTS.length) reply.result.capabilities = { ...reply.result.capabilities, prompts: {} };  // served here
        }
        if (msg.method === "tools/list" && Array.isArray(reply.result.tools)) reply.result.tools.unshift(...LOCAL_TOOLS);
      }
      if (toolName && reply.id === msg.id) logToolResult(toolName, msg.params.arguments, reply, Date.now() - started);
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

log(`bridge ${BRIDGE_VERSION} -> ${ENDPOINT || "(no server URL)"}${KEY ? "" : " (no API key)"}`);
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
