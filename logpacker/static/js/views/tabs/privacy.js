// What was hidden: counts per category, before/after examples, and the local mapping.
import { api } from "../../api.js";
import { fmt, h, icon, mount, withTokens } from "../../dom.js";

export function privacyTab(id, r) {
  const root = h("div", { class: "stack" }, h("p", { class: "muted" }, "Loading…"));
  if (!r.privacy.enabled) {
    mount(root, h("div", { class: "callout warn" }, "Sanitizing was turned off for this log. Upload it again with privacy on before sharing."));
    return root;
  }
  api.privacy(id).then((data) => draw(root, data)).catch((err) => mount(root, h("p", {}, err.message)));
  return root;
}

function draw(root, { summary, mapping }) {
  const cats = summary.categories;
  if (!cats.length) {
    mount(root, h("div", { class: "callout good" }, "Nothing sensitive was recognised. If the log mentions customer or server names, add them under \"Other words to hide\" and upload again."));
    return;
  }
  const mapBox = h("div");
  let shown = false;
  const toggle = h("button", { class: "btn small", type: "button", onclick: () => {
    shown = !shown;
    toggle.lastChild.textContent = shown ? "Hide originals" : "Show originals";
    mount(mapBox, shown ? mappingTable(mapping) : null);
  } }, icon("eye", 15), h("span", {}, "Show originals"));

  mount(root,
    h("div", { class: "callout good" }, h("span", {},
      h("b", {}, `${fmt.num(summary.lines_changed)} lines were cleaned. `),
      "The same value always gets the same placeholder, so the AI can still follow a host or user across the log.")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Category"), h("th", { class: "num" }, "Replaced"), h("th", { class: "num" }, "Distinct values"), h("th", {}, "Example (after)"))),
      h("tbody", {}, cats.map((c) => h("tr", {},
        h("td", {}, h("b", {}, c.label)),
        h("td", { class: "num" }, fmt.num(c.replacements)),
        h("td", { class: "num" }, fmt.num(c.distinct)),
        h("td", { class: "mono small" }, c.examples[0] ? withTokens(c.examples[0].after.slice(0, 160)) : "")))))),
    h("div", { class: "row spread" },
      h("p", { class: "muted small", style: "margin:0" }, "Originals are shown only here, on this computer. They are never written into the package."),
      toggle),
    mapBox,
  );
}

function mappingTable(mapping) {
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Placeholder"), h("th", {}, "Original"), h("th", {}, "Category"))),
    h("tbody", {}, mapping.slice(0, 500).map((m) => h("tr", {},
      h("td", {}, h("span", { class: "token mono" }, m.token)), h("td", { class: "mono small" }, m.original), h("td", {}, m.category))))));
}
