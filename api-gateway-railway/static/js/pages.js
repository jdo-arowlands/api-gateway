// In-memory caches so modals (project/endpoint dropdowns etc.) don't
// need a fresh fetch every time they open.
const state = {
  projects: [],
  endpoints: [],
  operations: [],
};

// ── Sidebar badge counts ─────────────────────────────────────────────
function updateBadge(id, count) {
  const el = document.getElementById(id);
  if (el) el.textContent = count ?? "—";
}

// ── DASHBOARD ─────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const stats = await Api.dashboard.stats();
    console.log(stats)

    document.getElementById("stat-total").textContent = stats.calls.total.toLocaleString() ?? "—";
    document.getElementById("stat-success").textContent = stats.calls.success.toLocaleString() ?? "—";
    document.getElementById("stat-failed").textContent = stats.calls.failed.toLocaleString() ?? "—";
    document.getElementById("stat-avg-rt").textContent = stats.calls.avg_response_ms ?? "—";
    document.getElementById("stat-jobs").textContent = stats.jobs.active + '/' + stats.jobs.total ?? "—";
    document.getElementById("stat-webhooks").textContent = stats.webhooks.total.toLocaleString() ?? "—";
    document.getElementById('badge-endpoints').textContent = '—';
    document.getElementById('badge-jobs').textContent = stats.jobs.total ?? "—";
    document.getElementById('badge-webhooks').textContent = stats.webhooks.total ?? "—";

    const pct = stats.calls.total > 0 ? Math.round(stats.calls.success / stats.calls.total * 100) + '%' : '—';
    document.getElementById("stat-success-pct").textContent = pct + ' success rate';

    // Recent Job Runs
    const runs = stats.recent_job_runs;
    const body = document.getElementById("recent-runs-body");
    if (!runs.length) {
      body.innerHTML = emptyRow(4, "No job runs yet");
      return;
    }
    body.innerHTML = runs.map((r) => `
      <tr>
        <td>${r.job_name}</td>
        <td class="muted">${r.triggered_by ?? "—"}</td>
        <td>${statusPill(r.success)}</td>
        <td class="muted">${formatTime(r.created_at)}</td>
      </tr>
    `).join("");
  } catch (e) {
    showToast(`Failed to load stats: ${e.message}`, "error");
  }
}

// ── PROJECTS ─────────────────────────────────────────────────────────
async function loadProjects() {
  try {
    state.projects = await Api.projects.list();
    updateBadge("badge-projects", state.projects.length);
    renderProjects();
    populateProjectDropdowns();
  } catch (e) {
    document.getElementById("projects-body").innerHTML = errorRow(5);
  }
}

function renderProjects() {
  const body = document.getElementById("projects-body");
  if (!state.projects.length) {
    body.innerHTML = emptyRow(5, "No projects yet");
    return;
  }
  body.innerHTML = state.projects.map((p) => `
    <tr>
      <td><span class="dot" style="background:${p.color || "#2f81f7"};display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px"></span>${p.name}</td>
      <td class="muted">${p.description ?? "—"}</td>
      <td class="muted">${p.endpoint_count ?? 0}</td>
      <td class="muted">${formatTime(p.created_at)}</td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="openProjectModal('${p.id}')">Edit</button>
        <button class="btn btn-danger btn-sm" onclick="deleteProject('${p.id}')">Delete</button>
      </td>
    </tr>
  `).join("");
}

function populateProjectDropdowns() {
  const opts = state.projects.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
  ["ep-project", "office-project", "endpoint-project-filter"].forEach((id) => {
    const select = document.getElementById(id);
    if (!select) return;
    const placeholder = select.id === "endpoint-project-filter"
      ? `<option value="">All projects</option>`
      : `<option value="">— No project —</option>`;
    select.innerHTML = placeholder + opts;
  });
}

function openProjectModal(id = null) {
  const project = id ? state.projects.find((p) => p.id === id) : null;
  document.getElementById("project-modal-title").textContent = project ? "Edit Project" : "New Project";
  document.getElementById("proj-id").value = project?.id ?? "";
  document.getElementById("proj-name").value = project?.name ?? "";
  document.getElementById("proj-desc").value = project?.description ?? "";
  document.getElementById("proj-color").value = project?.color ?? "#2f81f7";
  document.getElementById("proj-subkey-header").value = project?.subscription_key_header ?? "";
  document.getElementById("proj-subkey-value").value = "";
  document.getElementById("proj-subkey-hint").style.display = project?.has_subscription_key ? "block" : "none";
  openModal("project-modal");
}

async function saveProject() {
  const id = document.getElementById("proj-id").value;
  const payload = {
    name: document.getElementById("proj-name").value.trim(),
    description: document.getElementById("proj-desc").value.trim(),
    color: document.getElementById("proj-color").value,
    subscription_key_header: document.getElementById("proj-subkey-header").value.trim(),
  };
  const subkeyValue = document.getElementById("proj-subkey-value").value;
  if (subkeyValue) payload.subscription_key_value = subkeyValue;

  if (!payload.name) return showToast("Project name is required", "error");

  try {
    if (id) {
      await Api.projects.update(id, payload);
      showToast("Project updated");
    } else {
      await Api.projects.create(payload);
      showToast("Project created");
    }
    closeModal("project-modal");
    loadProjects();
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function deleteProject(id) {
  if (!confirm("Delete this project? Endpoints will be unassigned, not deleted.")) return;
  try {
    await Api.projects.delete(id);
    showToast("Project deleted");
    loadProjects();
  } catch (e) {
    showToast(e.message, "error");
  }
}

// ── ENDPOINTS ────────────────────────────────────────────────────────
async function loadEndpoints() {
  try {
    state.endpoints = await Api.endpoints.list();
    updateBadge("badge-endpoints", state.endpoints.length);
    renderEndpoints();
    populateEndpointDropdowns();
  } catch (e) {
    document.getElementById("endpoints-body").innerHTML = errorRow(7);
  }
}

function renderEndpoints() {
  const body = document.getElementById("endpoints-body");
  const search = (document.getElementById("endpoint-search")?.value ?? "").toLowerCase();
  const projectFilter = document.getElementById("endpoint-project-filter")?.value ?? "";

  let rows = state.endpoints;
  if (projectFilter) rows = rows.filter((e) => e.project_id === projectFilter);
  if (search) {
    rows = rows.filter((e) =>
      e.name.toLowerCase().includes(search) || e.base_url.toLowerCase().includes(search)
    );
  }

  if (!rows.length) {
    body.innerHTML = emptyRow(7, "No endpoints match");
    return;
  }

  body.innerHTML = rows.map((e) => {
    const project = state.projects.find((p) => p.id === e.project_id);
    return `
      <tr>
        <td class="mono">${e.name}</td>
        <td class="muted">${project ? project.name : "—"}</td>
        <td class="mono muted">${e.base_url}</td>
        <td><span class="pill info">${authLabel(e.auth_type)}</span></td>
        <td class="muted">${e.auth_type === "bearer" ? formatTime(e.token_expires_at) : "—"}</td>
        <td>${statusDot(e.status)}</td>
        <td>
          <button class="btn btn-ghost btn-sm" onclick="openEndpointModal('${e.id}')">Edit</button>
          <button class="btn btn-danger btn-sm" onclick="deleteEndpoint('${e.id}')">Delete</button>
        </td>
      </tr>
    `;
  }).join("");
}

function authLabel(type) {
  return { bearer: "OAuth2", api_key: "API Key", basic: "Basic" }[type] || type;
}

function statusDot(status) {
  const cls = status === "online" ? "online" : status === "offline" ? "offline" : "unknown";
  return `<span class="status-dot ${cls}"></span>`;
}

function populateEndpointDropdowns() {
  const opts = state.endpoints.map((e) => `<option value="${e.id}">${e.name}</option>`).join("");
  const opSelect = document.getElementById("op-endpoint");
  if (opSelect) opSelect.innerHTML = opts;
}

function toggleAuthFields() {
  const authType = document.getElementById("ep-auth").value;
  document.getElementById("oauth-fields").style.display = authType === "bearer" ? "grid" : "none";
  document.getElementById("apikey-fields").style.display = authType === "api_key" ? "grid" : "none";
}

function openEndpointModal(id = null) {
  const ep = id ? state.endpoints.find((e) => e.id === id) : null;
  document.getElementById("endpoint-modal-title").textContent = ep ? "Edit Endpoint" : "New Endpoint";
  document.getElementById("ep-id").value = ep?.id ?? "";
  document.getElementById("ep-name").value = ep?.name ?? "";
  document.getElementById("ep-project").value = ep?.project_id ?? "";
  document.getElementById("ep-auth").value = ep?.auth_type ?? "bearer";
  document.getElementById("ep-url").value = ep?.base_url ?? "";
  document.getElementById("ep-token-url").value = ep?.token_url ?? "";
  document.getElementById("ep-scope").value = ep?.scope ?? "";
  document.getElementById("ep-client-id").value = ep?.client_id ?? "";
  document.getElementById("ep-client-secret").value = "";
  document.getElementById("ep-secret-hint").style.display = ep?.has_client_secret ? "block" : "none";
  document.getElementById("ep-api-key").value = "";
  document.getElementById("ep-apikey-hint").style.display = ep?.has_api_key ? "block" : "none";
  document.getElementById("ep-api-key-header").value = ep?.api_key_header ?? "X-API-Key";
  document.getElementById("ep-timeout").value = ep?.timeout ?? 30;
  document.getElementById("ep-headers").value = JSON.stringify(ep?.extra_headers ?? {}, null, 0);
  toggleAuthFields();
  openModal("endpoint-modal");
}

async function saveEndpoint() {
  const id = document.getElementById("ep-id").value;
  const authType = document.getElementById("ep-auth").value;

  let extraHeaders;
  try {
    extraHeaders = JSON.parse(document.getElementById("ep-headers").value || "{}");
  } catch {
    return showToast("Extra Headers must be valid JSON", "error");
  }

  const payload = {
    name: document.getElementById("ep-name").value.trim(),
    project_id: document.getElementById("ep-project").value || null,
    auth_type: authType,
    base_url: document.getElementById("ep-url").value.trim(),
    timeout: Number(document.getElementById("ep-timeout").value) || 30,
    extra_headers: extraHeaders,
  };

  if (authType === "bearer") {
    payload.token_url = document.getElementById("ep-token-url").value.trim();
    payload.scope = document.getElementById("ep-scope").value.trim();
    payload.client_id = document.getElementById("ep-client-id").value.trim();
    const secret = document.getElementById("ep-client-secret").value;
    if (secret) payload.client_secret = secret;
  } else if (authType === "api_key") {
    const key = document.getElementById("ep-api-key").value;
    if (key) payload.api_key = key;
    payload.api_key_header = document.getElementById("ep-api-key-header").value.trim();
  } else if (authType === "basic") {
    payload.client_id = document.getElementById("ep-client-id").value.trim();
    const secret = document.getElementById("ep-client-secret").value;
    if (secret) payload.client_secret = secret;
  }

  if (!payload.name || !payload.base_url) {
    return showToast("Name and Base URL are required", "error");
  }

  try {
    if (id) {
      await Api.endpoints.update(id, payload);
      showToast("Endpoint updated");
    } else {
      await Api.endpoints.create(payload);
      showToast("Endpoint created");
    }
    closeModal("endpoint-modal");
    loadEndpoints();
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function deleteEndpoint(id) {
  if (!confirm("Delete this endpoint? Any operations using it will break.")) return;
  try {
    await Api.endpoints.delete(id);
    showToast("Endpoint deleted");
    loadEndpoints();
  } catch (e) {
    showToast(e.message, "error");
  }
}

// ── OFFICE MAP ───────────────────────────────────────────────────────
async function loadOfficeMap() {
  try {
    const rows = await Api.officeMap.list();
    updateBadge("badge-officemap", rows.length);
    const body = document.getElementById("office-map-body");
    if (!rows.length) {
      body.innerHTML = emptyRow(6, "No mappings yet");
      return;
    }
    body.innerHTML = rows.map((o) => {
      const project = state.projects.find((p) => p.id === o.project_id);
      return `
        <tr>
          <td class="mono">${o.phone}</td>
          <td>${o.office_name}</td>
          <td class="mono muted">${o.denticon_office_id}</td>
          <td class="muted">${project ? project.name : "—"}</td>
          <td>${statusDot(o.status)}</td>
          <td>
            <button class="btn btn-ghost btn-sm" onclick="openOfficeModal('${o.id}')">Edit</button>
            <button class="btn btn-danger btn-sm" onclick="deleteOffice('${o.id}')">Delete</button>
          </td>
        </tr>
      `;
    }).join("");
  } catch (e) {
    document.getElementById("office-map-body").innerHTML = errorRow(6);
  }
}

function openOfficeModal(id = null) {
  // id lookup requires the cached list; re-fetch lazily if needed in a real app
  const office = id ? window.__officeCache?.find((o) => o.id === id) : null;
  document.getElementById("office-modal-title").textContent = office ? "Edit Mapping" : "Add Mapping";
  document.getElementById("office-id-field").value = office?.id ?? "";
  document.getElementById("office-phone").value = office?.phone ?? "";
  document.getElementById("office-name").value = office?.office_name ?? "";
  document.getElementById("office-denticon-id").value = office?.denticon_office_id ?? "";
  document.getElementById("office-project").value = office?.project_id ?? "";
  openModal("office-modal");
}

async function saveOffice() {
  const id = document.getElementById("office-id-field").value;
  const payload = {
    phone: document.getElementById("office-phone").value.trim(),
    office_name: document.getElementById("office-name").value.trim(),
    denticon_office_id: document.getElementById("office-denticon-id").value.trim(),
    project_id: document.getElementById("office-project").value || null,
  };
  if (!payload.phone || !payload.office_name || !payload.denticon_office_id) {
    return showToast("Phone, Office Name, and Office ID are required", "error");
  }
  try {
    if (id) {
      await Api.officeMap.update(id, payload);
      showToast("Mapping updated");
    } else {
      await Api.officeMap.create(payload);
      showToast("Mapping created");
    }
    closeModal("office-modal");
    loadOfficeMap();
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function deleteOffice(id) {
  if (!confirm("Delete this office mapping?")) return;
  try {
    await Api.officeMap.delete(id);
    showToast("Mapping deleted");
    loadOfficeMap();
  } catch (e) {
    showToast(e.message, "error");
  }
}

// ── OPERATIONS ───────────────────────────────────────────────────────
async function loadOperations() {
  try {
    state.operations = await Api.operations.list();
    updateBadge("badge-operations", state.operations.length);
    const body = document.getElementById("operations-body");
    if (!state.operations.length) {
      body.innerHTML = emptyRow(7, "No operations yet");
      return;
    }
    body.innerHTML = state.operations.map((op) => {
      const endpoint = state.endpoints.find((e) => e.id === op.endpoint_id);
      return `
        <tr>
          <td class="mono">${op.name}</td>
          <td class="muted">${endpoint ? endpoint.name : "—"}</td>
          <td><span class="pill purple">${op.method}</span></td>
          <td class="mono muted">${op.path}</td>
          <td class="mono muted">${JSON.stringify(op.default_params || {})}</td>
          <td>${op.active ? '<span class="pill success">Active</span>' : '<span class="pill fail">Inactive</span>'}</td>
          <td>
            <button class="btn btn-ghost btn-sm" onclick="openOperationModal('${op.id}')">Edit</button>
            <button class="btn btn-danger btn-sm" onclick="deleteOperation('${op.id}')">Delete</button>
          </td>
        </tr>
      `;
    }).join("");
  } catch (e) {
    document.getElementById("operations-body").innerHTML = errorRow(7);
  }
}

function openOperationModal(id = null) {
  const op = id ? state.operations.find((o) => o.id === id) : null;
  document.getElementById("operation-modal-title").textContent = op ? "Edit Operation" : "Add Operation";
  document.getElementById("op-id").value = op?.id ?? "";
  document.getElementById("op-name").value = op?.name ?? "";
  document.getElementById("op-label").value = op?.label ?? "";
  document.getElementById("op-endpoint").value = op?.endpoint_id ?? "";
  document.getElementById("op-method").value = op?.method ?? "GET";
  document.getElementById("op-path").value = op?.path ?? "";
  document.getElementById("op-params").value = JSON.stringify(op?.default_params ?? {}, null, 0);
  document.getElementById("op-desc").value = op?.description ?? "";
  document.getElementById("op-tags").value = (op?.tags ?? []).join(", ");
  document.getElementById("op-active").value = String(op?.active ?? true);
  openModal("operation-modal");
}

async function saveOperation() {
  const id = document.getElementById("op-id").value;

  let defaultParams;
  try {
    defaultParams = JSON.parse(document.getElementById("op-params").value || "{}");
  } catch {
    return showToast("Default Params must be valid JSON", "error");
  }

  const payload = {
    name: document.getElementById("op-name").value.trim(),
    label: document.getElementById("op-label").value.trim(),
    endpoint_id: document.getElementById("op-endpoint").value,
    method: document.getElementById("op-method").value,
    path: document.getElementById("op-path").value.trim(),
    default_params: defaultParams,
    description: document.getElementById("op-desc").value.trim(),
    tags: document.getElementById("op-tags").value.split(",").map((t) => t.trim()).filter(Boolean),
    active: document.getElementById("op-active").value === "true",
  };

  if (!payload.name || !payload.endpoint_id || !payload.path) {
    return showToast("Name, Endpoint, and Path are required", "error");
  }

  try {
    if (id) {
      await Api.operations.update(id, payload);
      showToast("Operation updated");
    } else {
      await Api.operations.create(payload);
      showToast("Operation created");
    }
    closeModal("operation-modal");
    loadOperations();
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function deleteOperation(id) {
  if (!confirm("Delete this operation?")) return;
  try {
    await Api.operations.delete(id);
    showToast("Operation deleted");
    loadOperations();
  } catch (e) {
    showToast(e.message, "error");
  }
}

// ── JOBS ─────────────────────────────────────────────────────────────
async function loadJobs() {
  try {
    const jobs = await Api.jobs.list();
    updateBadge("badge-jobs", jobs.length);
    const body = document.getElementById("jobs-body");
    if (!jobs.length) {
      body.innerHTML = emptyRow(8, "No jobs configured");
      return;
    }
    body.innerHTML = jobs.map((j) => `
      <tr>
        <td class="mono">${j.name}</td>
        <td><span class="pill info">${j.type}</span></td>
        <td class="mono muted">${j.schedule || j.interval || j.run_at || "—"}</td>
        <td class="muted">${j.action}</td>
        <td class="muted">${formatTime(j.last_run_at)}</td>
        <td class="muted">${j.run_count ?? 0}</td>
        <td>${j.active ? '<span class="pill success">Active</span>' : '<span class="pill fail">Paused</span>'}</td>
        <td><button class="btn btn-danger btn-sm" onclick="deleteJob('${j.id}')">Delete</button></td>
      </tr>
    `).join("");
  } catch (e) {
    document.getElementById("jobs-body").innerHTML = errorRow(8);
  }
}

function toggleJobFields() {
  const type = document.getElementById("job-type").value;
  document.getElementById("job-schedule-wrap").style.display = type === "cron" ? "flex" : "none";
  document.getElementById("job-interval-wrap").style.display = type === "interval" ? "flex" : "none";
  document.getElementById("job-runat-wrap").style.display = type === "onetime" ? "flex" : "none";
  document.getElementById("job-webhook-hint").style.display = type === "webhook" ? "flex" : "none";
}

async function openJobModal() {
  document.getElementById("job-name").value = "";
  document.getElementById("job-type").value = "webhook";
  document.getElementById("job-schedule").value = "";
  document.getElementById("job-interval").value = "";
  document.getElementById("job-run-at").value = "";
  document.getElementById("job-desc").value = "";
  document.getElementById("job-params").value = "{}";
  toggleJobFields();

  // populate Action dropdown from configured operations
  const actionSelect = document.getElementById("job-action");
  if (!state.operations.length) {
    try { state.operations = await Api.operations.list(); } catch (_) { /* ignore */ }
  }
  actionSelect.innerHTML = state.operations.map((op) => `<option value="${op.name}">${op.label || op.name}</option>`).join("");

  openModal("job-modal");
}

async function saveJob() {
  let actionParams;
  try {
    actionParams = JSON.parse(document.getElementById("job-params").value || "{}");
  } catch {
    return showToast("Action Params must be valid JSON", "error");
  }

  const type = document.getElementById("job-type").value;
  const payload = {
    name: document.getElementById("job-name").value.trim(),
    type,
    action: document.getElementById("job-action").value,
    description: document.getElementById("job-desc").value.trim(),
    action_params: actionParams,
  };
  if (type === "cron") payload.schedule = document.getElementById("job-schedule").value.trim();
  if (type === "interval") payload.interval = document.getElementById("job-interval").value.trim();
  if (type === "onetime") payload.run_at = document.getElementById("job-run-at").value;

  if (!payload.name || !payload.action) {
    return showToast("Job Name and Action are required", "error");
  }

  try {
    await Api.jobs.create(payload);
    showToast("Job created");
    closeModal("job-modal");
    loadJobs();
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function deleteJob(id) {
  if (!confirm("Delete this job?")) return;
  try {
    await Api.jobs.delete(id);
    showToast("Job deleted");
    loadJobs();
  } catch (e) {
    showToast(e.message, "error");
  }
}

// ── WEBHOOKS ─────────────────────────────────────────────────────────
async function loadWebhookUrls() {
  try {
    const urls = await Api.webhooks.urls();
    document.getElementById("retell-url").textContent = urls.retell;
    document.getElementById("form-url").textContent = urls.form;
    document.getElementById("generic-url").textContent = urls.generic;
  } catch (e) {
    showToast("Failed to load webhook URLs", "error");
  }
}

async function loadWebhookEvents(source = "") {
  const body = document.getElementById("webhook-events-body");
  try {
    const events = await Api.webhooks.events(source);
    updateBadge("badge-webhooks", events.length);
    if (!events.length) {
      body.innerHTML = emptyRow(5, "No webhook events yet");
      return;
    }
    body.innerHTML = events.map((ev) => `
      <tr>
        <td class="muted">${formatTime(ev.created_at)}</td>
        <td><span class="pill info">${ev.source}</span></td>
        <td class="mono muted">${ev.event_id || ev.event_type || "—"}</td>
        <td class="muted">${ev.job_triggered || "—"}</td>
        <td>${ev.job_triggered ? statusPill(ev.success) : '<span class="muted">—</span>'}</td>
      </tr>
    `).join("");
  } catch (e) {
    body.innerHTML = errorRow(5);
  }
}

// ── CALL LOGS ────────────────────────────────────────────────────────
let logsPage = 0;
const LOGS_PAGE_SIZE = 25;

async function loadLogs(page = 0) {
  logsPage = page;
  const search = document.getElementById("log-search").value.trim();
  const status = document.getElementById("log-status-filter").value;
  const body = document.getElementById("call-logs-body");

  try {
    const data = await Api.logs.list({
      q: search,
      success: status,
      offset: page * LOGS_PAGE_SIZE,
      limit: LOGS_PAGE_SIZE,
    });
    const rows = data.items ?? data;
    const total = data.total ?? rows.length;

    if (!rows.length) {
      body.innerHTML = emptyRow(8, "No logs match");
      document.getElementById("logs-pagination").innerHTML = "";
      return;
    }

    body.innerHTML = rows.map((log) => `
      <tr>
        <td class="muted">${formatTime(log.created_at)}</td>
        <td class="mono">${log.endpoint_name}</td>
        <td><span class="pill purple">${log.method}</span></td>
        <td class="mono muted">${log.path}</td>
        <td>${statusPill(log.success)}</td>
        <td class="mono muted">${log.response_time_ms ?? "—"}</td>
        <td class="muted">${log.triggered_by ?? "—"}</td>
        <td><button class="btn btn-ghost btn-sm" onclick="viewLogDetail('${log.id}')">View</button></td>
      </tr>
    `).join("");

    renderLogsPagination(total, page);
  } catch (e) {
    body.innerHTML = errorRow(8);
  }
}

function renderLogsPagination(total, page) {
  const pages = Math.ceil(total / LOGS_PAGE_SIZE);
  const el = document.getElementById("logs-pagination");
  if (pages <= 1) { el.innerHTML = ""; return; }

  let buttons = "";
  for (let i = 0; i < pages; i++) {
    buttons += `<button class="page-btn ${i === page ? "active" : ""}" onclick="loadLogs(${i})">${i + 1}</button>`;
  }
  el.innerHTML = `
    <span>${total} total</span>
    <div class="pages">${buttons}</div>
  `;
}

async function viewLogDetail(id) {
  try {
    const log = await Api.logs.detail(id);
    openDrawer("Call Detail", `
      <div class="detail-row"><div class="detail-label">Endpoint</div><div>${log.endpoint_name}</div></div>
      <div class="detail-row"><div class="detail-label">Method</div><div>${log.method}</div></div>
      <div class="detail-row"><div class="detail-label">Path</div><div class="mono">${log.path}</div></div>
      <div class="detail-row"><div class="detail-label">Status</div><div>${statusPill(log.success)}</div></div>
      <div class="detail-row"><div class="detail-label">Response Time</div><div>${log.response_time_ms ?? "—"} ms</div></div>
      <div class="detail-row"><div class="detail-label">Triggered By</div><div>${log.triggered_by ?? "—"}</div></div>
      <div class="detail-row"><div class="detail-label">Time</div><div>${formatTime(log.created_at)}</div></div>
      <div class="detail-row"><div class="detail-label">Request</div><div><pre>${escapeHtml(JSON.stringify(log.request_payload ?? {}, null, 2))}</pre></div></div>
      <div class="detail-row"><div class="detail-label">Response</div><div><pre>${escapeHtml(JSON.stringify(log.response_payload ?? {}, null, 2))}</pre></div></div>
    `);
  } catch (e) {
    showToast("Failed to load log detail", "error");
  }
}

// ── JOB RUNS ─────────────────────────────────────────────────────────
async function loadJobRuns() {
  const body = document.getElementById("job-runs-body");
  try {
    const runs = await Api.jobRuns.list();
    if (!runs.length) {
      body.innerHTML = emptyRow(6, "No job runs yet");
      return;
    }
    body.innerHTML = runs.map((r) => `
      <tr>
        <td class="muted">${formatTime(r.created_at)}</td>
        <td class="mono">${r.job_name}</td>
        <td class="muted">${r.triggered_by ?? "—"}</td>
        <td class="mono muted">${r.duration_ms != null ? r.duration_ms + " ms" : "—"}</td>
        <td>${statusPill(r.success)}</td>
        <td><button class="btn btn-ghost btn-sm" onclick="viewJobRunDetail('${r.id}')">View</button></td>
      </tr>
    `).join("");
  } catch (e) {
    body.innerHTML = errorRow(6);
  }
}

function viewJobRunDetail(id) {
  // Hook this up to a dedicated endpoint if/when one exists.
  showToast("Job run detail endpoint not yet wired up");
}

// ── ADMIN TOOLS ──────────────────────────────────────────────────────
async function loadTools() {
  const container = document.getElementById("tools-container");
  try {
    const tools = await Api.tools.list();
    if (!tools.length) {
      container.innerHTML = `<div class="muted" style="text-align:center;padding:40px">No tools available</div>`;
      return;
    }
    container.innerHTML = tools.map((t) => `
      <div class="table-wrap" style="margin-bottom:12px">
        <div class="table-header">
          <h3>${t.name}</h3>
          <button class="btn btn-primary btn-sm" onclick="runTool('${t.id}')">Run</button>
        </div>
        <div style="padding:14px" class="muted">${t.description ?? ""}</div>
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<div class="muted" style="text-align:center;padding:40px;color:var(--red)">Failed to load tools</div>`;
  }
}

async function runTool(id) {
  showToast(`Running tool ${id}...`);
  // Wire this up to a POST /api/tools/{id}/run endpoint as needed.
}

// ── SETTINGS ─────────────────────────────────────────────────────────
async function loadSettings() {
  const container = document.getElementById("settings-list");
  try {
    const settings = await Api.settings.list();
    if (!Object.keys(settings).length) {
      container.innerHTML = `<div class="muted">No settings configured</div>`;
      return;
    }
    container.innerHTML = Object.entries(settings).map(([key, value]) => `
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center">
        <span class="mono">${key}</span>
        <span class="mono muted">${value}</span>
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<div class="muted" style="color:var(--red)">Failed to load settings</div>`;
  }
}

async function saveSetting() {
  const key = document.getElementById("new-key").value.trim();
  const value = document.getElementById("new-val").value.trim();
  if (!key) return showToast("Key is required", "error");
  try {
    await Api.settings.save(key, value);
    showToast("Setting saved");
    document.getElementById("new-key").value = "";
    document.getElementById("new-val").value = "";
    loadSettings();
  } catch (e) {
    showToast(e.message, "error");
  }
}

// ── Shared formatting helpers ────────────────────────────────────────
function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
