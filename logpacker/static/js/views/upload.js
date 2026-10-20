// Step 1: pick a log, choose privacy options, start.
import { api } from "../api.js";
import { fmt, h, icon, mount, toast } from "../dom.js";
import { store } from "../store.js";

const ACCEPT = ".log,.txt,.gz,.zip";

export function renderUpload(root) {
  const input = h("input", { type: "file", accept: ACCEPT, hidden: true, onchange: () => input.files[0] && start(input.files[0]) });
  const sanitize = h("input", { type: "checkbox", checked: true });
  const objects = h("input", { type: "checkbox" });
  const terms = h("textarea", { rows: 3, placeholder: "e.g. Contoso\nPRODDB01\nfinance_dwh" });

  const zone = h("div", {
    class: "dropzone", role: "button", tabindex: 0, "aria-label": "Choose a log file",
    onclick: () => input.click(),
    onkeydown: (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.click()),
    ondragover: (e) => { e.preventDefault(); zone.classList.add("over"); },
    ondragleave: () => zone.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); zone.classList.remove("over"); e.dataTransfer.files[0] && start(e.dataTransfer.files[0]); },
  },
    h("div", { class: "big-icon" }, icon("upload", 28)),
    h("strong", {}, "Drop a Replicate task log here"),
    h("span", { class: "muted" }, "or click to browse · .log, .gz or .zip (diagnostics bundles work too)"),
    input,
  );

  async function start(file) {
    try {
      const custom = terms.value.split(/\r?\n|,/).map((t) => t.trim()).filter(Boolean);
      const job = await api.create({
        filename: file.name, size: file.size,
        sanitize: { enabled: sanitize.checked, mask_objects: objects.checked, custom_terms: custom },
      });
      store.pendingFiles.set(job.id, file);
      location.hash = `#/job/${job.id}`;
    } catch (err) {
      toast(err.message);
    }
  }

  const privacy = h("section", { class: "card" },
    h("div", { class: "card-title" }, h("h2", {}, "Privacy"), h("span", { class: "chip" }, icon("shield", 14), "Stays on this computer")),
    h("label", { class: "check" }, sanitize, h("span", {},
      h("b", {}, "Remove personal and sensitive data"),
      h("span", { class: "help" }, "Hosts, IPs, users, emails, paths, passwords and license names become consistent placeholders like host_01."))),
    h("label", { class: "check" }, objects, h("span", {},
      h("b", {}, "Also hide schema and table names"),
      h("span", { class: "help" }, "Turn on if table names are confidential. The AI will see obj_01 instead."))),
    h("label", { class: "field" }, h("span", { class: "label" }, "Other words to hide"), terms,
      h("span", { class: "help" }, "One per line: customer, project or server names the rules might not recognise.")),
  );

  const recent = h("section", { class: "card" }, h("div", { class: "card-title" }, h("h2", {}, "Recent logs")),
    h("p", { class: "muted small" }, "Loading…"));

  mount(root,
    h("div", { class: "hero" },
      h("h1", {}, "Get a Replicate log ready for AI"),
      h("p", {}, "LogPacker cleans out sensitive data, finds the patterns that matter and packs everything into a prompt plus a compact attachment you can share with any AI assistant.")),
    zone,
    h("div", { class: "flow" },
      flowItem(1, "Clean", "Sensitive values are replaced before anything is analysed."),
      flowItem(2, "Find patterns", "Errors, latency and known Replicate behaviours are counted for you."),
      flowItem(3, "Pack", "Copy a ready prompt and attach a condensed log sized for the model.")),
    h("div", { class: "grid grid-2", style: "margin-top:20px" }, privacy, recent),
  );

  loadRecent(recent);
}

function flowItem(n, title, text) {
  return h("div", { class: "flow-item" }, h("span", { class: "flow-num" }, n), h("span", {}, h("b", {}, title), h("span", { class: "muted" }, text)));
}

async function loadRecent(card) {
  const list = await api.recent().catch(() => []);
  const body = list.length
    ? list.map((job) => h("div", { class: "recent-item" },
        h("div", {}, h("div", {}, h("b", {}, job.filename)),
          h("div", { class: "muted small" }, `${fmt.num(job.lines_done)} lines · ${new Date(job.created_at * 1000).toLocaleString()}`)),
        h("div", { class: "row" },
          h("a", { class: "btn small", href: `#/job/${job.id}/review` }, "Open"),
          h("button", { class: "btn small ghost", type: "button", "aria-label": `Delete ${job.filename}`,
            onclick: async () => { await api.remove(job.id); loadRecent(card); } }, icon("trash", 15)))))
    : [h("p", { class: "muted small" }, "Nothing yet. Logs are kept for 3 days on this computer, then deleted.")];
  mount(card, h("div", { class: "card-title" }, h("h2", {}, "Recent logs")), body);
}
