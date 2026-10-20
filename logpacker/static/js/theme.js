// Light/dark toggle. Follows the OS until the user picks one.
export function initTheme() {
  const root = document.documentElement;
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("lp-theme", root.dataset.theme); } catch { /* private mode */ }
  });
}
