// The line patterns that make up most of the log (what condensing collapses).
import { api } from "../../api.js";
import { fmt, h, mount, withTokens } from "../../dom.js";
import { store } from "../../store.js";

export function patternsTab(r) {
  const rep = r.repetition;
  const total = r.stats.lines || 1;
  const top10 = rep.top.slice(0, 10).reduce((a, p) => a + p.count, 0);
  const jobId = currentJobId();
  return h("div", { class: "stack" },
    h("div", { class: "callout" }, h("span", {},
      `${fmt.num(r.stats.lines)} lines follow ${fmt.num(rep.distinct)} distinct patterns. `,
      `The 10 most frequent cover ${Math.round((top10 / total) * 100)}% of the log; the packed attachment keeps their first and last occurrences and collapses the rest.`)),
    h("div", { class: "table-wrap" }, h("table", { class: "patterns-table" },
      h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Lines"), h("th", { class: "num" }, "Share"), h("th", {}, "Component"), h("th", {}, "Pattern"))),
      h("tbody", {}, rep.top.map((p) => patternRow(p, total, jobId))))),
  );
}

function patternRow(p, total, jobId) {
  const hasSamples = p.sample_lines && p.sample_lines.length > 0;
  const sampleBox = h("div", { class: "pattern-samples" });
  let loaded = false;

  const row = h("tr", { class: hasSamples ? "clickable" : "" },
    h("td", { class: "num" }, fmt.num(p.count)),
    h("td", { class: "num" }, `${((p.count / total) * 100).toFixed(1)}%`),
    h("td", { class: "pattern-component" }, p.component, " ", h("span", { class: `badge ${p.severity}` }, p.severity)),
    h("td", { class: "mono small" },
      p.pattern,
      hasSamples ? h("span", { class: "sample-toggle muted" }, " ▸") : null));

  const detailRow = h("tr", { class: "pattern-detail", style: "display:none" },
    h("td", { colspan: "4" }, sampleBox));

  if (hasSamples) {
    row.onclick = () => {
      const open = detailRow.style.display !== "none";
      detailRow.style.display = open ? "none" : "table-row";
      row.querySelector(".sample-toggle").textContent = open ? " ▸" : " ▾";
      if (!loaded) {
        loaded = true;
        loadSamples(sampleBox, p.sample_lines, jobId);
      }
    };
  }

  return [row, detailRow];
}

async function loadSamples(container, lineNos, jobId) {
  mount(container, h("span", { class: "muted small" }, "Loading…"));
  try {
    const lines = await api.lines(jobId, lineNos);
    const pre = h("pre", { class: "log" });
    for (const n of lineNos) {
      const text = lines[String(n)];
      if (text != null) {
        pre.append(h("span", {}, `${n}|`, withTokens(text)), "\n");
      }
    }
    mount(container, h("div", { class: "pattern-samples-label muted small" }, "Sample original lines:"), pre);
  } catch {
    mount(container, h("span", { class: "muted small" }, "Could not load sample lines."));
  }
}

function currentJobId() {
  const hash = location.hash || "";
  const m = hash.match(/\/job\/([^/]+)/);
  return m ? m[1] : "";
}
