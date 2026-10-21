// Upload (if a file is waiting) then poll analysis until the job is ready.
import { api } from "../api.js";
import { fmt, h, mount } from "../dom.js";
import { store } from "../store.js";

const PHASES = [
  { key: "uploading", label: "Uploading the file" },
  { key: "queued", label: "Waiting for a free worker" },
  { key: "processing", label: "Removing sensitive data and finding patterns" },
  { key: "ready", label: "Building your review" },
];

export function renderProgress(root, id) {
  let stopped = false;
  const bar = h("span", { style: "width:0%" });
  const detail = h("p", { class: "muted small" }, "Starting…");
  const phases = h("div", { class: "phases" });
  const title = h("h2", {}, "Working on your log");
  mount(root, h("section", { class: "card", style: "max-width:640px;margin:40px auto" },
    title, phases, h("div", { class: "progress", role: "progressbar", "aria-label": "Progress" }, bar), detail));

  const show = (phase, pct, text) => {
    const idx = PHASES.findIndex((p) => p.key === phase);
    mount(phases, PHASES.map((p, i) => h("div", { class: `phase ${i < idx ? "done" : i === idx ? "active" : ""}` },
      h("span", { class: "tick" }, i < idx ? "✓" : ""), p.label)));
    bar.style.width = `${Math.round(pct * 100)}%`;
    detail.textContent = text;
  };

  const fail = (message) => {
    title.textContent = "That didn't work";
    mount(detail, message, " ", h("a", { href: "#/" }, "Try another file"));
    detail.classList.remove("muted");
  };

  async function run() {
    const file = store.pendingFiles.get(id);
    if (file) {
      store.pendingFiles.delete(id);
      show("uploading", 0, `${file.name} · ${fmt.bytes(file.size)}`);
      try {
        const job = await api.upload(id, file, (p) => show("uploading", p, `${fmt.pct(p)} of ${fmt.bytes(file.size)}`));
        if (job.cached) { // same content was analysed before
          location.hash = `#/job/${job.id}/review`;
          return;
        }
      } catch (err) {
        return fail(err.message);
      }
    }
    while (!stopped) {
      let job;
      try { job = await api.status(id); } catch (err) { return fail(err.message); }
      if (job.state === "failed") return fail(job.error || "The log could not be analysed.");
      if (job.state === "ready") {
        show("ready", 1, `${fmt.num(job.lines_done)} lines analysed`);
        location.hash = `#/job/${id}/review`;
        return;
      }
      if (job.state === "created" || (job.state === "uploading" && !file)) {
        return fail("The upload was interrupted. Please upload the file again.");
      }
      const text = job.state === "queued"
        ? "Other logs are being analysed right now; yours starts as soon as a worker frees up."
        : `${fmt.num(job.lines_done)} lines read · ${fmt.pct(job.progress)}`;
      show(job.state, job.progress, text);
      await new Promise((r) => setTimeout(r, 600));
    }
  }

  run();
  return () => { stopped = true; };
}
