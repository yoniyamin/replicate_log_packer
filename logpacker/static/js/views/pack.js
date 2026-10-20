// Step 3: choose focus and sizes, then copy the prompt and download the attachment.
import { api } from "../api.js";
import { copyText, fmt, h, icon, mount, withTokens } from "../dom.js";
import { store } from "../store.js";
import { loadReport } from "./review.js";

const FOCUS = [["auto", "Auto"], ["errors", "Errors"], ["performance", "Latency"], ["configuration", "Settings"], ["general", "Health"]];
const DETAIL = [["concise", "Concise"], ["standard", "Standard"], ["detailed", "Detailed"]];
const SIZE = [["small", "~50k tokens"], ["medium", "~150k"], ["large", "~400k"], ["full", "Full log"]];
const DEFAULTS = { focus: "auto", detail: "standard", attachment: "medium", question: "", include_findings: true };

export function renderPack(root, id) {
  const opts = { ...DEFAULTS, ...(store.packOptions.get(id) || {}) };
  const result = h("div", { class: "stack" }, h("div", { class: "card empty" },
    h("p", {}, h("span", { class: "spinner" }), " Condensing the log…"),
    h("p", { class: "small" }, "Large logs take a few seconds the first time for each attachment size.")));
  let timer = null, seq = 0;

  const build = async () => {
    const mine = ++seq;
    store.packOptions.set(id, { ...opts });
    result.style.opacity = ".55";
    try {
      const data = await api.pack(id, opts);
      if (mine === seq) drawResult(result, id, data);
    } catch (err) {
      if (mine === seq) mount(result, h("div", { class: "card callout warn" }, err.message));
    } finally {
      if (mine === seq) result.style.opacity = "1";
    }
  };
  const schedule = (delay = 250) => { clearTimeout(timer); timer = setTimeout(build, delay); };

  const question = h("textarea", { rows: 4, placeholder: "e.g. Since Monday the task stops every night with ORA-01555. Why, and how do we prevent it?",
    oninput: () => { opts.question = question.value; schedule(900); } });
  question.value = opts.question;
  const findings = h("input", { type: "checkbox", checked: opts.include_findings,
    onchange: () => { opts.include_findings = findings.checked; schedule(); } });

  const controls = h("aside", { class: "card stack" },
    h("h2", {}, "Package options"),
    h("label", { class: "field" }, h("span", { class: "label" }, "What do you want to know?"), question,
      h("span", { class: "help" }, "Optional. Your words go first in the prompt; a good question gets a much better answer.")),
    segmented("Focus", "focus", FOCUS, opts, schedule, "Auto picks one from what was found."),
    segmented("Prompt detail", "detail", DETAIL, opts, schedule, "How many tables and excerpts go into the prompt itself."),
    segmented("Attachment size", "attachment", SIZE, opts, schedule, "Pick what your AI model can read. The log is condensed to fit; errors and their context are kept first."),
    h("label", { class: "check" }, findings, h("span", {}, h("b", {}, "Include findings.json"),
      h("span", { class: "help" }, "Every pattern and count as data the AI can look things up in."))),
  );

  mount(root,
    h("div", { class: "row spread", style: "margin-bottom:16px" },
      h("div", {}, h("h1", {}, "Pack for AI"), h("p", { class: "muted", style: "margin:0" }, "Everything below is already sanitized.")),
      h("a", { class: "btn ghost", href: `#/job/${id}/review` }, icon("back", 16), "Back to review")),
    h("div", { class: "grid grid-pack" }, controls, result),
  );
  loadReport(id).then(build).catch((err) => mount(result, h("div", { class: "card callout warn" }, err.message)));
  return () => clearTimeout(timer);
}

function segmented(label, key, choices, opts, onChange, help) {
  const group = h("div", { class: "segmented", role: "group", "aria-label": label });
  const draw = () => mount(group, choices.map(([value, text]) => h("button", {
    type: "button", "aria-pressed": String(opts[key] === value),
    onclick: () => { opts[key] = value; draw(); onChange(); },
  }, text)));
  draw();
  return h("div", { class: "field" }, h("span", { class: "label" }, label), group, h("span", { class: "help" }, help));
}

function drawResult(root, id, data) {
  const { prompt, attachment } = data;
  const total = prompt.tokens + attachment.tokens;
  const condensed = attachment.mode === "condensed";
  mount(root,
    h("section", { class: "card" },
      h("div", { class: "card-title" },
        h("div", {}, h("h2", {}, "Your package is ready"),
          h("div", { class: "muted small" }, `Focus: ${prompt.focus_label} · ${prompt.focus_reason}`)),
        h("a", { class: "btn primary", href: api.downloadUrl(id, "package") }, icon("download", 16), "Download all (.zip)")),
      h("div", { class: "meter" },
        meterTile("Prompt", fmt.tokens(prompt.tokens) + " tokens", "paste into the chat"),
        meterTile("Attachment", fmt.bytes(attachment.bytes), `${fmt.tokens(attachment.tokens)} tokens`),
        meterTile("Total", fmt.tokens(total) + " tokens", "estimate, ~4 chars per token")),
      condensed ? h("p", { class: "muted small", style: "margin:12px 0 0" },
        `Condensed from ${fmt.num(attachment.lines_in)} to ${fmt.num(attachment.lines_kept)} lines; `,
        `${fmt.num(attachment.omitted)} repetitive lines collapsed into ${fmt.num(attachment.markers)} markers.`,
        attachment.issues_capped ? ` Repeated errors were thinned to ${attachment.issues_capped} per pattern to fit.` : "",
        attachment.truncated ? " The end was cut to fit; pick a larger size to keep it." : "") : null,
      prompt.dropped.length ? h("p", { class: "muted small", style: "margin:6px 0 0" },
        `To stay within the prompt budget these sections were left out: ${prompt.dropped.join(", ")}. Choose “Detailed” to include them.`) : null),
    h("section", { class: "card stack" },
      h("h3", {}, "How to use it"),
      h("ol", { class: "how" },
        h("li", {}, h("span", {}, "Open your AI assistant and ", h("b", {}, "attach "), h("code", {}, attachment.name),
          data.findings_bytes ? [" and ", h("code", {}, "findings.json")] : null, ".")),
        h("li", {}, h("span", {}, h("b", {}, "Paste the prompt"), " as your message.")),
        h("li", {}, h("span", {}, "Ask follow-ups by line number (", h("code", {}, "L1234"), ") or pattern ID (", h("code", {}, "E002"), ")."))),
      h("div", { class: "downloads" },
        dl(id, "attachment", attachment.name, fmt.bytes(attachment.bytes)),
        data.findings_bytes ? dl(id, "findings", "findings.json", fmt.bytes(data.findings_bytes)) : null,
        dl(id, "prompt", "PROMPT.md", fmt.tokens(prompt.tokens) + " tokens"))),
    h("section", { class: "card" },
      h("div", { class: "card-title" }, h("h3", {}, "Prompt"),
        h("button", { class: "btn small", type: "button", onclick: () => copyText(prompt.text) }, icon("copy", 15), "Copy prompt")),
      h("div", { class: "prompt-box" }, h("pre", {}, withTokens(prompt.text)))),
    h("section", { class: "card" },
      h("div", { class: "card-title" }, h("h3", {}, "Attachment preview"), h("span", { class: "muted small" }, "first lines")),
      preview(attachment.preview)),
  );
}

function meterTile(name, value, note) {
  return h("div", { class: "kpi" }, h("div", { class: "name" }, name), h("div", { class: "value" }, value), h("div", { class: "muted small" }, note));
}

function dl(id, kind, name, size) {
  return h("a", { class: "btn", href: api.downloadUrl(id, kind) },
    h("span", { class: "row" }, icon("download", 16), h("span", { class: "mono" }, name)), h("span", { class: "muted small" }, size));
}

function preview(lines) {
  const pre = h("pre", { class: "log" });
  for (const line of lines) {
    pre.append(line.startsWith("~~") ? h("span", { class: "omit" }, line) : withTokens(line), "\n");
  }
  return pre;
}
