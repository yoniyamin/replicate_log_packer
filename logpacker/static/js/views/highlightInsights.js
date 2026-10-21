// Shared insight builder used by the Review verdict strip and Overview overflow.
export function buildHighlightInsights(r, limit = 5) {
  return [
    ...r.cockpit.recommendations.slice(0, 3),
    ...["apply", "full_load", "file_ops"].flatMap((k) =>
      (r[k]?.insights || []).filter((i) => i.severity === "error")),
  ].slice(0, limit);
}
