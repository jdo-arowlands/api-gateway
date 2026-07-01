/**
 * api.js
 * Thin wrapper around fetch() for talking to the FastAPI backend.
 * Centralizing this makes it trivial to add auth headers, change the
 * base URL, or handle errors consistently in one place.
 */

const API_BASE = "";  // same-origin; set to e.g. "/api" if you mount routes there

async function apiRequest(path, { method = "GET", body = null, params = null } = {}) {
  let url = API_BASE + path;

  if (params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ).toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }

  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) opts.body = JSON.stringify(body);

  const res = await fetch(url, opts);

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errJson = await res.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch (_) { /* response wasn't JSON */ }
    throw new Error(detail || `Request failed: ${res.status}`);
  }

  if (res.status === 204) return null;
  return res.json();
}

const api = {
  get: (path, params) => apiRequest(path, { method: "GET", params }),
  post: (path, body) => apiRequest(path, { method: "POST", body }),
  put: (path, body) => apiRequest(path, { method: "PUT", body }),
  patch: (path, body) => apiRequest(path, { method: "PATCH", body }),
  delete: (path) => apiRequest(path, { method: "DELETE" }),
};

// ── Resource-specific endpoints ──────────────────────────────────────
// Adjust these paths to match your actual FastAPI router prefixes.

const Api = {
  dashboard: {
    stats: () => api.get("/api/stats"),
    recentRuns: () => api.get("/api/recent-runs"),
  },
  projects: {
    list: () => api.get("/api/projects"),
    create: (data) => api.post("/api/projects", data),
    update: (id, data) => api.put(`/api/projects/${id}`, data),
    delete: (id) => api.delete(`/api/projects/${id}`),
  },
  endpoints: {
    list: () => api.get("/api/endpoints"),
    create: (data) => api.post("/api/endpoints", data),
    update: (id, data) => api.put(`/api/endpoints/${id}`, data),
    delete: (id) => api.delete(`/api/endpoints/${id}`),
  },
  officeMap: {
    list: () => api.get("/api/office-map"),
    create: (data) => api.post("/api/office-map", data),
    update: (id, data) => api.put(`/api/office-map/${id}`, data),
    delete: (id) => api.delete(`/api/office-map/${id}`),
  },
  operations: {
    list: () => api.get("/api/operations"),
    create: (data) => api.post("/api/operations", data),
    update: (id, data) => api.put(`/api/operations/${id}`, data),
    delete: (id) => api.delete(`/api/operations/${id}`),
  },
  jobs: {
    list: () => api.get("/api/jobs"),
    create: (data) => api.post("/api/jobs", data),
    delete: (id) => api.delete(`/api/jobs/${id}`),
  },
  jobRuns: {
    list: () => api.get("/api/job-runs"),
  },
  webhooks: {
    urls: () => api.get("/api/webhooks/urls"),
    events: (source) => api.get("/api/webhooks/events", { source }),
  },
  logs: {
    list: (params) => api.get("/api/logs", params),
    detail: (id) => api.get(`/api/logs/${id}`),
  },
  tools: {
    list: () => api.get("/api/tools"),
  },
  settings: {
    list: () => api.get("/api/settings"),
    save: (key, value) => api.post("/api/settings", { key, value }),
  },
};
