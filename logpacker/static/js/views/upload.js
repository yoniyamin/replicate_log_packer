// Step 1: pick a log, choose privacy options, start.
import { api } from "../api.js";
import { fmt, h, icon, mount, toast } from "../dom.js";
import { store } from "../store.js";
import { fingerprint } from "../fingerprint.js";

const ACCEPT = ".log,.txt,.gz,.zip";

export function renderUpload(root) {
  const input = h("input", { type: "file", accept: ACCEPT, hidden: true, onchange: () => input.files[0] && start(input.files[0]) });
  const sanitize = h("input", { type: "checkbox", checked: true });
  const objects = h("input", { type: "checkbox" });
  const terms = h("textarea", { rows: 2, placeholder: "e.g. Contoso\nPRODDB01\nfinance_dwh" });

  const zone = h("div", {
    class: "dropzone compact", role: "button", tabindex: 0, "aria-label": "Choose a log file",
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
        filename: file.name, size: file.size, fingerprint: await fingerprint(file),
        sanitize: { enabled: sanitize.checked, mask_objects: objects.checked, custom_terms: custom },
      });
      if (job.cached && job.state === "ready") {
        toast("Already analysed — opening the saved review");
        location.hash = `#/job/${job.id}/review`;
        return;
      }
      store.pendingFiles.set(job.id, file);
      location.hash = `#/job/${job.id}`;
    } catch (err) {
      toast(err.message);
    }
  }

  mount(root,
    h("div", { class: "row spread", style: "margin-bottom:16px" },
      h("div", {},
        h("h1", { style: "margin:0" }, "Get a Replicate log ready for AI"),
        h("p", { class: "muted", style: "margin:4px 0 0" }, "Clean sensitive data, find patterns, pack for any AI assistant.")),
      h("button", { class: "btn small", type: "button", onclick: () => openRecentModal() },
        icon("back", 14), "Recent logs")),
    zone,
    h("div", { class: "flow", style: "margin:14px 0" },
      flowItem(1, "Clean", "Sensitive values are replaced before anything is analysed."),
      flowItem(2, "Find patterns", "Errors, latency and known Replicate behaviours are counted for you."),
      flowItem(3, "Pack", "Copy a ready prompt and attach a condensed log sized for the model.")),
    h("section", { class: "card compact-card" },
      h("div", { class: "card-title" }, h("h2", {}, "Privacy"), h("span", { class: "chip" }, icon("shield", 14), "Stays on this computer")),
      h("div", { class: "privacy-grid" },
        h("label", { class: "check" }, sanitize, h("span", {},
          h("b", {}, "Remove personal and sensitive data"),
          h("span", { class: "help" }, "Hosts, IPs, users, emails, paths, passwords and license names become consistent placeholders."))),
        h("label", { class: "check" }, objects, h("span", {},
          h("b", {}, "Also hide schema and table names"),
          h("span", { class: "help" }, "Turn on if table names are confidential. The AI will see obj_01 instead."))),
        h("label", { class: "field", style: "margin:0" }, h("span", { class: "label" }, "Other words to hide"), terms,
          h("span", { class: "help" }, "One per line: customer, project or server names the rules might not recognise.")))),
  );
}

function flowItem(n, title, text) {
  return h("div", { class: "flow-item" }, h("span", { class: "flow-num" }, n), h("span", {}, h("b", {}, title), h("span", { class: "muted" }, text)));
}

async function openRecentModal() {
  const list = await api.recent().catch(() => []);

  const backdrop = h("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } });
  const dialog = h("div", { class: "modal modal-wide", role: "dialog", "aria-label": "Recent logs" });

  function close() { backdrop.classList.add("closing"); setTimeout(() => backdrop.remove(), 200); }

  function render() {
    const body = list.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {},
            h("th", {}, "Filename"), h("th", { class: "num" }, "Lines"),
            h("th", {}, "Analysed"), h("th", {}))),
          h("tbody", {}, list.map((job) => h("tr", { class: "clickable", onclick: () => { close(); location.hash = `#/job/${job.id}/review`; } },
            h("td", {}, h("span", { class: "mono small" }, job.filename)),
            h("td", { class: "num" }, fmt.num(job.lines_done)),
            h("td", {}, new Date(job.created_at * 1000).toLocaleString()),
            h("td", { style: "text-align:right" },
              h("button", { class: "btn small ghost", type: "button", "aria-label": `Delete ${job.filename}`,
                onclick: (e) => { e.stopPropagation(); api.remove(job.id).then(() => { list.splice(list.indexOf(job), 1); render(); }); } }, icon("trash", 14))))))))
      : h("p", { class: "muted small" }, "Nothing yet. Analysed logs are kept for 7 days after you last open them, then deleted.");

    mount(dialog,
      h("div", { class: "modal-header" },
        h("h2", { style: "margin:0" }, "Recent logs"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: close }, "✕")),
      h("div", { class: "modal-body" },
        h("p", { class: "muted small" }, "Logs analysed in this browser. Uploading the same file reuses the saved analysis."),
        body));
  }

  render();
  backdrop.append(dialog);
  document.body.append(backdrop);
  requestAnimationFrame(() => backdrop.classList.add("open"));

  const onKey = (e) => { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
}
