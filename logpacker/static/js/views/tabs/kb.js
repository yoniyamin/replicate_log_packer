// Qlik KB articles that may relate to the findings (keyword match, loaded on demand).
import { api } from "../../api.js";
import { h, mount } from "../../dom.js";
import { store } from "../../store.js";

const TONE = { high: "good", medium: "warn", low: "" };

/** Shared by the tab and its count badge, so the lookup runs once per log. */
export function loadKb(id) {
  if (!store.kb.has(id)) store.kb.set(id, api.kb(id).catch((err) => { store.kb.delete(id); throw err; }));
  return store.kb.get(id);
}

export function kbTab(id) {
  const root = h("div", { class: "stack" }, h("p", { class: "muted" }, h("span", { class: "spinner" }), " Searching the KB…"));
  loadKb(id).then((data) => draw(root, data)).catch((err) => mount(root, h("p", {}, err.message)));
  return root;
}

function draw(root, data) {
  if (!data.available) {
    mount(root, h("div", { class: "callout warn" }, data.reason));
    return;
  }
  const intro = h("div", { class: "callout" }, h("span", {},
    "Found by keyword match against the errors, warnings and signals above, from ",
    h("b", {}, `${data.index.articles.toLocaleString("en-US")} Qlik support articles`),
    ". Treat them as leads: check that an article describes the same symptom before relying on it."));
  if (!data.articles.length) {
    mount(root, intro, h("div", { class: "empty" }, "No KB article matched the findings closely enough."));
    return;
  }
  mount(root, intro, h("div", { class: "kb-list" }, data.articles.map(card)));
}

function card(a) {
  return h("article", { class: "kb-card" },
    h("div", { class: "kb-head" },
      h("a", { class: "kb-title", href: a.url, target: "_blank", rel: "noopener noreferrer" }, a.title),
      h("span", { class: `pill ${TONE[a.confidence] || ""}`, title: `Relevance ${Math.round(a.relevance * 100)}%` },
        `${a.confidence[0].toUpperCase()}${a.confidence.slice(1)} match`)),
    a.description ? h("p", { class: "kb-desc" }, a.description) : null,
    h("div", { class: "kb-matched" },
      h("span", { class: "muted small" }, "Matched on"),
      a.matched_on.map((m) => h("span", { class: "chip", title: `Shared words: ${m.terms.join(", ")}` }, m.label))));
}
