// In-memory hand-off between views (a picked file waiting to upload, cached reports).
export const store = {
  pendingFiles: new Map(),
  reports: new Map(),
  packOptions: new Map(),
};
