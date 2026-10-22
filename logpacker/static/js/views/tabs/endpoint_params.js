// Endpoint Parameters — modal triggered from review page chips.
import { h, mount, icon } from "../../dom.js";

let _cache = null;
async function loadAll() {
  if (!_cache) { const res = await fetch("/api/endpoint-params"); _cache = await res.json(); }
  return _cache;
}

function normalise(s) { return (s || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }

function findEndpoints(all, typeStr) {
  if (!typeStr) return [];
  const key = normalise(typeStr);
  if (!key) return [];
  const candidates = all.filter((ep) => {
    const syn = normalise(ep.syntax);
    const ttl = normalise(ep.title);
    if (syn && syn === key) return true;
    if (ttl && ttl === key) return true;
    if (syn && key.length >= 3 && (syn.includes(key) || key.includes(syn))) return true;
    if (ttl && key.length >= 3 && (ttl.includes(key) || key.includes(ttl))) return true;
    return false;
  });
  if (!candidates.length) return [];
  candidates.sort((a, b) => b.params.length - a.params.length);
  return candidates;
}

function mergeEndpointParams(endpoints, roleFilter) {
  const seen = new Set();
  const merged = [];
  for (const ep of endpoints) {
    for (const p of ep.params) {
      if (roleFilter && p.role && p.role !== roleFilter) continue;
      const key = `${p.name}:${p.role || ""}`;
      if (!seen.has(key)) { seen.add(key); merged.push(p); }
    }
  }
  return merged;
}

function googleUrl(name) {
  return `https://www.google.com/search?q=${encodeURIComponent(name + " Qlik Replicate")}`;
}
function qlikUrl(name) {
  return `https://community.qlik.com/t5/Search-the-Community/bd-p/qlikSearchUnify?searchString=${encodeURIComponent(name)}&activeType=all&from=0&sortby=_score&orderBy=desc&pageNo=1&aggregations=%5B%7B%22type%22%3A%22Product_category_flat%22%2C%22filter%22%3A%5B%22Qlik+Replicate%22%5D%7D%5D&uid=cb43fd48-5178-11eb-8a9b-0242ac120002&resultsPerPage=10&exactPhrase=&withOneOrMore=&withoutTheWords=&pageSize=10&language=en&suCaseCreate=false`;
}

function roleBadge(role) {
  if (!role) return null;
  const tone = role === "SOURCE" ? "info" : role === "TARGET" ? "warn" : "";
  return h("span", { class: `badge ${tone}`, style: "font-size:0.75em" }, role);
}

function propPair(k, v) {
  return [h("dt", {}, k), h("dd", {}, v)];
}

function paramRow(p, idx, prefix) {
  const displayName = p.name || p.ui_name || "unnamed";
  const showUiName = p.ui_name && p.name && p.ui_name !== p.name;
  const searchName = p.name || p.ui_name || "";

  const detail = h("div", { class: "param-detail", style: "display:none" },
    h("dl", { class: "props compact" },
      p.type ? propPair("Type", p.type) : null,
      p.version ? propPair("Since", p.version) : null,
      p.default_value !== undefined ? propPair("Default", String(p.default_value)) : null,
      p.role ? propPair("Role", p.role) : null));

  const chevron = h("span", { class: "param-chevron" }, "▸");

  const toggle = (e) => {
    if (e.target.closest("a")) return;
    const open = detail.style.display !== "none";
    detail.style.display = open ? "none" : "block";
    chevron.textContent = open ? "▸" : "▾";
    chevron.classList.toggle("open", !open);
  };

  const subParts = [];
  if (showUiName) subParts.push(p.ui_name);
  if (p.default_value !== undefined) subParts.push(`default: ${p.default_value}`);
  const sub = subParts.length
    ? h("div", { class: "param-sub muted small" }, subParts.join(" · "))
    : null;

  return h("li", { class: "endpoint-param-item", onclick: toggle },
    h("div", { class: "param-header" },
      h("div", { class: "param-main" },
        h("div", { class: "param-name-line" },
          chevron,
          h("strong", {}, displayName),
          roleBadge(p.role)),
        sub),
      h("div", { class: "param-actions" },
        h("a", { href: googleUrl(searchName), target: "_blank", title: "Search Google", class: "icon-link" }, icon("search", 14)),
        h("a", { href: qlikUrl(searchName), target: "_blank", title: "Search Qlik Community", class: "icon-link" }, icon("community", 14)))),
    detail);
}

function renderParamList(params, prefix) {
  if (!params.length) return h("p", { class: "muted small" }, "No parameters found.");
  return h("ul", { class: "param-list" }, params.map((p, i) => paramRow(p, i, prefix)));
}

export function openEndpointParamsModal(report, initialSide = "source") {
  const meta = report.metadata;
  const sourceLabel = [meta.source_endpoint, meta.source_type].filter(Boolean).join(" · ") || "Source";
  const targetLabel = [meta.target_endpoint, meta.target_type].filter(Boolean).join(" · ") || "Target";

  const backdrop = h("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } });
  const dialog = h("div", { class: "modal modal-wide", role: "dialog", "aria-label": "Endpoint Parameters" });

  function close() {
    backdrop.classList.add("closing");
    setTimeout(() => backdrop.remove(), 200);
    document.removeEventListener("keydown", onKey);
  }

  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);

  const controlsArea = h("div", { class: "ep-modal-controls" });
  const listArea = h("div", { class: "modal-body", style: "padding-top:0" });

  mount(listArea, h("div", { class: "empty" }, h("span", { class: "spinner" }), " Loading…"));

  loadAll().then((all) => {
    const sourceEps = findEndpoints(all, meta.source_type);
    const targetEps = findEndpoints(all, meta.target_type);
    const srcParams = mergeEndpointParams(sourceEps, "SOURCE");
    const tgtParams = mergeEndpointParams(targetEps, "TARGET");

    let active = initialSide;
    const listContainer = h("div");

    const searchInput = h("input", {
      type: "text", class: "search-input",
      style: "width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:0.95em;background:var(--bg);color:var(--text)",
    });
    let debounce = null;

    function currentParams() { return active === "source" ? srcParams : tgtParams; }

    function renderList(filter) {
      let params = currentParams();
      if (filter && filter.length >= 2) {
        const needle = filter.toLowerCase();
        params = params.filter((p) =>
          (p.name || "").toLowerCase().includes(needle) || (p.ui_name || "").toLowerCase().includes(needle));
      }
      const label = active === "source" ? sourceLabel : targetLabel;
      const count = params.length;
      const total = currentParams().length;
      const note = filter && filter.length >= 2
        ? `${count} of ${total} parameters match "${filter}"`
        : `${total} parameters`;
      mount(listContainer,
        h("p", { class: "muted small", style: "margin:0 0 8px" }, `${label} — ${note}`),
        renderParamList(params, active));
    }

    const srcBtn = h("button", { class: `ep-toggle${active === "source" ? " active" : ""}`, type: "button", onclick: () => switchTo("source") },
      `Source: ${sourceLabel}`, h("span", { class: "count" }, srcParams.length));
    const tgtBtn = h("button", { class: `ep-toggle${active === "target" ? " active" : ""}`, type: "button", onclick: () => switchTo("target") },
      `Target: ${targetLabel}`, h("span", { class: "count" }, tgtParams.length));
    const toggleBar = h("div", { class: "ep-toggle-bar" }, srcBtn, tgtBtn);

    function updateToggle() {
      srcBtn.classList.toggle("active", active === "source");
      tgtBtn.classList.toggle("active", active === "target");
    }

    function switchTo(side) {
      active = side;
      searchInput.placeholder = `Search ${active === "source" ? sourceLabel : targetLabel} parameters…`;
      searchInput.value = "";
      updateToggle();
      renderList("");
    }

    searchInput.placeholder = `Search ${active === "source" ? sourceLabel : targetLabel} parameters…`;
    searchInput.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => renderList(searchInput.value.trim()), 250);
    });

    mount(controlsArea, toggleBar, searchInput);
    mount(listArea, listContainer);

    if (!sourceEps.length && !targetEps.length) {
      mount(listContainer, h("p", { class: "muted" }, "No matching endpoint parameters found for the detected source/target types."));
    } else {
      renderList("");
    }
  }).catch((err) => {
    mount(listArea, h("p", { class: "muted" }, "Could not load endpoint parameters: ", err.message));
  });

  mount(dialog,
    h("div", { class: "modal-header" },
      h("h2", { style: "margin:0" }, "Endpoint Parameters"),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: close }, "✕")),
    controlsArea,
    listArea);

  backdrop.append(dialog);
  document.body.append(backdrop);
  requestAnimationFrame(() => backdrop.classList.add("open"));
}
