// Hash router + stepper. Each route renders one view module into #app.
import { h, mount } from "./dom.js";
import { renderUpload } from "./views/upload.js";
import { renderProgress } from "./views/progress.js";
import { renderReview } from "./views/review.js";
import { renderPack } from "./views/pack.js";
import { renderMcp } from "./views/mcp.js";
import { initTheme } from "./theme.js";

const app = document.getElementById("app");
const STEPS = [
  { key: "upload", label: "Upload" },
  { key: "review", label: "Review" },
  { key: "pack", label: "Pack" },
];

function parse(hash) {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "mcp") return { view: "mcp" };
  if (parts[0] === "job" && parts[1]) return { view: parts[2] || "progress", id: parts[1] };
  return { view: "upload" };
}

function renderStepper(route) {
  if (route.view === "mcp") {
    mount(document.getElementById("stepper"), []);
    return;
  }
  const current = route.view === "progress" ? 0 : STEPS.findIndex((s) => s.key === route.view);
  const items = [];
  STEPS.forEach((step, i) => {
    if (i) items.push(h("span", { class: "step-sep", "aria-hidden": "true" }));
    const state = i === current ? "active" : i < current ? "done" : "";
    const reachable = route.id && i > 0 && route.view !== "progress";
    const href = i === 0 ? "#/" : reachable ? `#/job/${route.id}/${step.key}` : null;
    items.push(h(href ? "a" : "span", { class: `step ${state}`, href, "aria-current": i === current ? "step" : null },
      h("span", { class: "dot" }, i < current ? "✓" : i + 1), h("span", { class: "label" }, step.label)));
  });
  mount(document.getElementById("stepper"), items);
}

let cleanup = null;

function route() {
  if (cleanup) cleanup();
  cleanup = null;
  const r = parse(location.hash);
  renderStepper(r);
  const views = { upload: renderUpload, progress: renderProgress, review: renderReview, pack: renderPack, mcp: renderMcp };
  const render = views[r.view] || renderUpload;
  cleanup = render(app, r.id) || null;
  app.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
initTheme();
route();
