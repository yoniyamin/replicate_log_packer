// Thin client for the LogPacker API.

async function request(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(readable(data) || `Request failed (${res.status})`);
  return data;
}

function readable(data) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
  return "";
}

export const api = {
  recent: () => request("GET", "/api/jobs"),
  create: (payload) => request("POST", "/api/jobs", payload),
  status: (id) => request("GET", `/api/jobs/${id}`),
  report: (id) => request("GET", `/api/jobs/${id}/report`),
  privacy: (id) => request("GET", `/api/jobs/${id}/privacy`),
  pack: (id, options) => request("POST", `/api/jobs/${id}/pack`, options),
  remove: (id) => request("DELETE", `/api/jobs/${id}`),
  kb: (id) => request("GET", `/api/jobs/${id}/kb`),
  lines: (id, lineNos) => request("GET", `/api/jobs/${id}/lines?n=${lineNos.join(",")}`),
  downloadUrl: (id, kind) => `/api/jobs/${id}/download/${kind}`,

  // MCP management
  mcpStatus: () => request("GET", "/api/mcp/status"),
  mcpKeys: () => request("GET", "/api/mcp/keys"),
  mcpCreateKey: (owner) => request("POST", "/api/mcp/keys", { owner }),
  mcpRevokeKey: (prefix) => request("DELETE", "/api/mcp/keys", { key_hash_prefix: prefix }),
  mcpClientConfig: () => request("GET", "/api/mcp/client-config"),

  /** Stream the file as the raw request body; XHR gives upload progress. */
  upload(id, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `/api/jobs/${id}/content`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch { /* keep empty */ }
        xhr.status < 300 ? resolve(data) : reject(new Error(readable(data) || `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Upload failed: the connection was interrupted."));
      xhr.send(file);
    });
  },
};
