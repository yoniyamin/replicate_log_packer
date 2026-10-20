// Minimal SVG line chart (no library). series: [{key, label, color}], points: [{...}]
const NS = "http://www.w3.org/2000/svg";

function el(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

export function lineChart(points, series, { width = 900, height = 260 } = {}) {
  const pad = { l: 44, r: 12, t: 12, b: 26 };
  const w = width - pad.l - pad.r, hgt = height - pad.t - pad.b;
  const max = Math.max(1, ...points.flatMap((p) => series.map((s) => p[s.key])));
  const x = (i) => pad.l + (points.length > 1 ? (i / (points.length - 1)) * w : w / 2);
  const y = (v) => pad.t + hgt - (v / max) * hgt;

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img",
    "aria-label": `Latency over time, peak ${max.toFixed(1)} seconds` });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    svg.append(el("line", { x1: pad.l, x2: width - pad.r, y1: y(v), y2: y(v), class: "grid-line" }));
    const label = el("text", { x: pad.l - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" });
    label.textContent = `${v >= 10 ? v.toFixed(0) : v.toFixed(1)}s`;
    svg.append(label);
  }
  for (const idx of [0, points.length - 1]) {
    if (!points[idx]) continue;
    const t = el("text", { x: x(idx), y: height - 6, "text-anchor": idx ? "end" : "start", class: "axis" });
    t.textContent = (points[idx].ts || "").replace("T", " ");
    svg.append(t);
  }
  for (const s of series) {
    const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[s.key]).toFixed(1)}`).join("");
    svg.append(el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round" }));
  }
  return svg;
}
