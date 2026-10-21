// Quick file fingerprint so a repeat upload of the same log can skip sending it.
// SHA-256 over name, size, modified time and the first and last MB.
const SAMPLE = 1024 * 1024;

export async function fingerprint(file) {
  if (!globalThis.crypto?.subtle) return null; // only available on localhost / https
  try {
    const head = await file.slice(0, SAMPLE).arrayBuffer();
    const tail = file.size > SAMPLE ? await file.slice(-SAMPLE).arrayBuffer() : new ArrayBuffer(0);
    const meta = new TextEncoder().encode(`${file.name}|${file.size}|${file.lastModified}|`);
    const all = new Uint8Array(meta.byteLength + head.byteLength + tail.byteLength);
    all.set(meta, 0);
    all.set(new Uint8Array(head), meta.byteLength);
    all.set(new Uint8Array(tail), meta.byteLength + head.byteLength);
    const digest = await crypto.subtle.digest("SHA-256", all);
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}
