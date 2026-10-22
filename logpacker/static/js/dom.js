// Tiny DOM + formatting helpers shared by every view.

/** h("div", {class: "x", onclick: fn}, child, "text", [more]) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "html") el.innerHTML = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function mount(target, ...children) {
  target.replaceChildren();
  append(target, children);
}

export const fmt = {
  num: (n) => (n ?? 0).toLocaleString("en-US"),
  bytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
  },
  tokens: (n) => (n >= 1000 ? `~${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `~${n}`),
  ts: (s) => (s ? s.replace("T", " ") : "—"),
  pct: (x) => `${Math.round((x || 0) * 100)}%`,
  /** Short timestamp for timeline axis labels (date + time, no seconds). */
  tsShort(s) {
    if (!s) return "";
    const d = new Date(s.includes("T") ? s : s.replace(" ", "T"));
    if (isNaN(d)) return s.replace("T", " ");
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  },
};

/** Parse a log timestamp string to epoch ms. Returns NaN on failure. */
export function parseLogTs(s) {
  if (!s) return NaN;
  return Date.parse(s.includes("T") ? s : s.replace(" ", "T"));
}

const TOKEN_RE = /\b((?:email|host|path|user|company|cloud|ip|obj|term|db)_\d{2,})\b|\[REDACTED\]/g;

/** Text node with redaction tokens highlighted. */
export function withTokens(text) {
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of text.matchAll(TOKEN_RE)) {
    frag.append(text.slice(last, m.index), h("span", { class: "token" }, m[0]));
    last = m.index + m[0].length;
  }
  frag.append(text.slice(last));
  return frag;
}

export function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2200);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = h("textarea", { style: "position:fixed;opacity:0" });
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("Copied to clipboard");
}

export function icon(name, size = 18) {
  const paths = {
    upload: "M12 16V4m0 0l-5 5m5-5l5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
    shield: "M12 3l8 3v6c0 4.5-3.4 8.4-8 9-4.6-.6-8-4.5-8-9V6z M9 12l2 2 4-4",
    search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M20 20l-4-4",
    box: "M4 8l8-4 8 4v8l-8 4-8-4z M4 8l8 4 8-4 M12 12v8",
    copy: "M9 9h10v10H9z M5 15V5h10",
    download: "M12 4v12m0 0l-5-5m5 5l5-5M4 20h16",
    arrow: "M5 12h14m-5-5l5 5-5 5",
    back: "M19 12H5m5-5l-5 5 5 5",
    trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
    eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    calendar: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
    community: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", paths[name] || "");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}
