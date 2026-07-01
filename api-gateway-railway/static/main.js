const API = '';  // Same origin — set to 'http://localhost:8000' for dev

// ── Navigation ────────────────────────────────────────────────────────────────
function showPage(id) {
    document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
    document.getElementById('page-' + id).style.display = 'block';
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    event.currentTarget.classList.add('active');
    if (id === 'dashboard') loadDashboard();
    if (id === 'projects') loadProjects();
    if (id === 'endpoints') loadEndpoints();
    if (id === 'office-map') loadOfficeMap();
    if (id === 'operations') loadOperations();
    if (id === 'jobs') loadJobs();
    if (id === 'webhooks-page') { loadWebhookEvents(''); setWebhookUrls(); }
    if (id === 'call-logs') loadLogs(0);
    if (id === 'job-runs') loadJobRuns();
    if (id === 'tools') loadTools();
    if (id === 'settings') loadSettings();
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(path, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(API + path, opts);
    if (r.status === 401) {
        // Session expired or logged out — send back to login
        window.location.href = '/login';
        throw new Error('Session expired');
    }
    if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail || r.statusText);
    }
    return r.json();
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = 'success') {
    const t = document.getElementById('toast');
    t.textContent = (type === 'success' ? '✓ ' : '✗ ') + msg;
    t.className = 'show ' + type;
    setTimeout(() => t.className = '', 3000);
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
    try {
        const s = await api('/api/stats');
        document.getElementById('stat-total').textContent = s.calls.total.toLocaleString();
        document.getElementById('stat-success').textContent = s.calls.success.toLocaleString();
        document.getElementById('stat-failed').textContent = s.calls.failed.toLocaleString();
        document.getElementById('stat-avg-rt').textContent = s.calls.avg_response_ms;
        document.getElementById('stat-jobs').textContent = s.jobs.active + '/' + s.jobs.total;
        document.getElementById('stat-webhooks').textContent = s.webhooks.total.toLocaleString();
        document.getElementById('badge-endpoints').textContent = '—';
        document.getElementById('badge-jobs').textContent = s.jobs.total;
        document.getElementById('badge-webhooks').textContent = s.webhooks.total;

        const pct = s.calls.total > 0
            ? Math.round(s.calls.success / s.calls.total * 100) + '%' : '—';
        document.getElementById('stat-success-pct').textContent = pct + ' success rate';

        const tbody = document.getElementById('recent-runs-body');
        if (!s.recent_job_runs.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="muted" style="text-align:center;padding:30px">No job runs yet</td></tr>';
            return;
        }
        tbody.innerHTML = s.recent_job_runs.map(r => `
      <tr>
        <td class="mono">${r.job_name}</td>
        <td><span class="pill info">${r.triggered_by}</span></td>
        <td>${r.success ? '<span class="pill success"><span class="dot"></span>Success</span>' : '<span class="pill fail"><span class="dot"></span>Failed</span>'}</td>
        <td class="muted mono">${fmtTime(r.started_at)}</td>
      </tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

// ── Projects ──────────────────────────────────────────────────────────────────
let _projectsCache = [];

async function loadProjects() {
    try {
        const projects = await api('/api/projects');
        _projectsCache = projects;
        document.getElementById('badge-projects').textContent = projects.length;
        const tbody = document.getElementById('projects-body');
        if (!projects.length) {
            tbody.innerHTML = '<tr><td colspan="5"><div class="empty"><div class="icon">📁</div><p>No projects yet. Create one to group your endpoints.</p></div></td></tr>';
            return;
        }
        tbody.innerHTML = projects.map(p => `
      <tr>
        <td>
          <span style="display:inline-flex;align-items:center;gap:8px">
            <span style="width:10px;height:10px;border-radius:3px;background:${p.color || '#2f81f7'}"></span>
            <strong>${p.name}</strong>
          </span>
        </td>
        <td class="muted">${p.description || '—'}</td>
        <td><span class="pill info">${p.endpoint_count} endpoint${p.endpoint_count === 1 ? '' : 's'}</span></td>
        <td class="muted mono" style="font-size:11px">${fmtTime(p.created_at)}</td>
        <td>
          <div style="display:flex;gap:4px">
            <button class="btn btn-ghost btn-sm" onclick='editProject(${JSON.stringify(p)})'>Edit</button>
            <button class="btn btn-danger btn-sm" onclick="deleteProject(${p.id}, '${p.name.replace(/'/g, "\\'")}')">✕</button>
          </div>
        </td>
      </tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

function openProjectModal() {
    document.getElementById('project-modal-title').textContent = 'New Project';
    document.getElementById('proj-id').value = '';
    document.getElementById('proj-name').value = '';
    document.getElementById('proj-desc').value = '';
    document.getElementById('proj-color').value = '#2f81f7';
    document.getElementById('proj-subkey-header').value = '';
    document.getElementById('proj-subkey-value').value = '';
    document.getElementById('proj-subkey-value').placeholder = '••••••••';
    document.getElementById('proj-subkey-hint').style.display = 'none';
    document.getElementById('project-modal').classList.add('open');
}

function editProject(p) {
    document.getElementById('project-modal-title').textContent = 'Edit Project';
    document.getElementById('proj-id').value = p.id;
    document.getElementById('proj-name').value = p.name;
    document.getElementById('proj-desc').value = p.description || '';
    document.getElementById('proj-color').value = p.color || '#2f81f7';
    document.getElementById('proj-subkey-header').value = p.sub_key_header || '';
    // Key value is never returned — show a hint if one is set.
    document.getElementById('proj-subkey-value').value = '';
    document.getElementById('proj-subkey-value').placeholder = p.has_sub_key ? '•••••• (unchanged)' : '••••••••';
    document.getElementById('proj-subkey-hint').style.display = p.has_sub_key ? 'block' : 'none';
    document.getElementById('project-modal').classList.add('open');
}

async function saveProject() {
    try {
        const id = document.getElementById('proj-id').value;
        const subVal = document.getElementById('proj-subkey-value').value;
        const payload = {
            name: document.getElementById('proj-name').value,
            description: document.getElementById('proj-desc').value || null,
            color: document.getElementById('proj-color').value,
            sub_key_header: document.getElementById('proj-subkey-header').value || null,
        };
        // Only send the key value if the user typed one (blank = keep existing on edit).
        if (subVal) payload.sub_key_value = subVal;
        if (!payload.name) return toast('Project name required', 'error');
        if (id) {
            await api(`/api/projects/${id}`, 'PATCH', payload);
        } else {
            if (subVal) payload.sub_key_value = subVal;
            await api('/api/projects', 'POST', payload);
        }
        closeModal('project-modal'); toast('Project saved'); loadProjects();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteProject(id, name) {
    if (!confirm(`Delete project "${name}"? Its endpoints will be kept but ungrouped.`)) return;
    await api(`/api/projects/${id}`, 'DELETE');
    toast('Project deleted'); loadProjects();
}

// ── Office Phone Map ──────────────────────────────────────────────────────────
let _officeCache = [];

async function loadOfficeMap() {
    try {
        [_officeCache, _projectsCache] = await Promise.all([
            api('/api/office-map'),
            api('/api/projects'),
        ]);
        document.getElementById('badge-officemap').textContent = _officeCache.length;
        const tbody = document.getElementById('office-map-body');
        if (!_officeCache.length) {
            tbody.innerHTML = '<tr><td colspan="6"><div class="empty"><div class="icon">☎️</div><p>No office mappings yet. Add one so the agent can resolve the office from the dialed number.</p></div></td></tr>';
            return;
        }
        tbody.innerHTML = _officeCache.map(o => `
      <tr>
        <td class="mono"><strong>${o.phone_number}</strong></td>
        <td>${o.office_name || '—'}</td>
        <td class="mono">${o.office_id}</td>
        <td>${projName(o.project_id)}</td>
        <td><div class="status-dot ${o.is_active ? 'online' : 'offline'}"></div></td>
        <td>
          <div style="display:flex;gap:4px">
            <button class="btn btn-ghost btn-sm" onclick='editOffice(${JSON.stringify(o)})'>Edit</button>
            <button class="btn btn-danger btn-sm" onclick="deleteOffice(${o.id})">✕</button>
          </div>
        </td>
      </tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

function projName(pid) {
    if (!pid) return '<span class="muted">—</span>';
    const p = _projectsCache.find(x => x.id === pid);
    return p ? `<span class="pill" style="background:${p.color}22;color:${p.color}">${p.name}</span>` : '—';
}

function _fillOfficeProjectDropdown() {
    const sel = document.getElementById('office-project');
    const current = sel.value;
    sel.innerHTML = '<option value="">— No project —</option>'
        + _projectsCache.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
    sel.value = current;
}

async function openOfficeModal() {
    try { if (!_projectsCache.length) _projectsCache = await api('/api/projects'); } catch (e) { }
    document.getElementById('office-modal-title').textContent = 'Add Mapping';
    document.getElementById('office-id-field').value = '';
    document.getElementById('office-phone').value = '';
    document.getElementById('office-name').value = '';
    document.getElementById('office-denticon-id').value = '';
    _fillOfficeProjectDropdown();
    document.getElementById('office-project').value = '';
    document.getElementById('office-modal').classList.add('open');
}

function editOffice(o) {
    document.getElementById('office-modal-title').textContent = 'Edit Mapping';
    document.getElementById('office-id-field').value = o.id;
    document.getElementById('office-phone').value = o.phone_number;
    document.getElementById('office-name').value = o.office_name || '';
    document.getElementById('office-denticon-id').value = o.office_id;
    _fillOfficeProjectDropdown();
    document.getElementById('office-project').value = o.project_id || '';
    document.getElementById('office-modal').classList.add('open');
}

async function saveOffice() {
    try {
        const id = document.getElementById('office-id-field').value;
        const projVal = document.getElementById('office-project').value;
        const payload = {
            phone_number: document.getElementById('office-phone').value,
            office_name: document.getElementById('office-name').value || null,
            office_id: document.getElementById('office-denticon-id').value,
            project_id: projVal ? +projVal : null,
        };
        if (!payload.phone_number || !payload.office_id) {
            return toast('Phone number and Denticon Office ID are required', 'error');
        }
        if (id) await api(`/api/office-map/${id}`, 'PATCH', payload);
        else await api('/api/office-map', 'POST', payload);
        closeModal('office-modal'); toast('Mapping saved'); loadOfficeMap();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteOffice(id) {
    if (!confirm('Delete this office mapping?')) return;
    await api(`/api/office-map/${id}`, 'DELETE');
    toast('Mapping deleted'); loadOfficeMap();
}

// ── Endpoints ─────────────────────────────────────────────────────────────────
let _endpointsCache = [];

async function loadEndpoints() {
    try {
        // Fetch projects too so the filter + modal dropdown stay current
        [_endpointsCache, _projectsCache] = await Promise.all([
            api('/api/endpoints'),
            api('/api/projects'),
        ]);
        document.getElementById('badge-endpoints').textContent = _endpointsCache.length;
        document.getElementById('badge-projects').textContent = _projectsCache.length;
        populateProjectFilter();
        renderEndpoints();
    } catch (e) { toast(e.message, 'error'); }
}

function populateProjectFilter() {
    const sel = document.getElementById('endpoint-project-filter');
    const current = sel.value;
    sel.innerHTML = '<option value="">All projects</option>'
        + '<option value="__none__">— Ungrouped —</option>'
        + _projectsCache.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
    sel.value = current;
}

function renderEndpoints() {
    const tbody = document.getElementById('endpoints-body');
    const search = (document.getElementById('endpoint-search')?.value || '').toLowerCase();
    const filter = document.getElementById('endpoint-project-filter')?.value || '';

    let eps = _endpointsCache.filter(e => {
        const matchSearch = !search || e.name.toLowerCase().includes(search)
            || (e.base_url || '').toLowerCase().includes(search);
        const matchProj = !filter
            || (filter === '__none__' && !e.project_id)
            || (String(e.project_id) === filter);
        return matchSearch && matchProj;
    });

    if (!eps.length) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty"><div class="icon">🔌</div><p>No endpoints match. Add one or adjust the filter.</p></div></td></tr>';
        return;
    }

    // Group rows by project, with a group header row per project
    const groups = {};
    eps.forEach(e => {
        const key = e.project_name || 'Ungrouped';
        (groups[key] = groups[key] || []).push(e);
    });

    let html = '';
    Object.keys(groups).sort((a, b) => a === 'Ungrouped' ? 1 : b === 'Ungrouped' ? -1 : a.localeCompare(b)).forEach(group => {
        const color = groups[group][0].project_color || '#7d8590';
        html += `<tr style="background:var(--surface2)">
      <td colspan="7" style="padding:8px 14px">
        <span style="display:inline-flex;align-items:center;gap:8px;font-size:11px;font-weight:600;letter-spacing:0.4px;text-transform:uppercase;color:var(--muted)">
          <span style="width:8px;height:8px;border-radius:2px;background:${color}"></span>${group}
          <span style="color:var(--muted);font-weight:400">· ${groups[group].length}</span>
        </span>
      </td></tr>`;
        html += groups[group].map(e => `
      <tr>
        <td><strong>${e.name}</strong></td>
        <td>${e.project_name
                ? `<span class="pill" style="background:${e.project_color}22;color:${e.project_color}">${e.project_name}</span>`
                : '<span class="muted">—</span>'}</td>
        <td class="mono muted" style="max-width:180px;overflow:hidden;text-overflow:ellipsis">${e.base_url}</td>
        <td><span class="pill info">${e.auth_type}</span></td>
        <td class="mono muted">${e.token_expires_at ? fmtTime(e.token_expires_at) : '—'}</td>
        <td><div class="status-dot ${e.is_active ? 'online' : 'offline'}"></div></td>
        <td>
          <div style="display:flex;gap:4px">
            <button class="btn btn-ghost btn-sm" onclick="editEndpoint(${e.id})">Edit</button>
            <button class="btn btn-ghost btn-sm" onclick="testEndpoint(${e.id}, '${e.name}')">Test</button>
            <button class="btn btn-ghost btn-sm" onclick="reassignEndpoint(${e.id})">Move</button>
            <button class="btn btn-ghost btn-sm" onclick="deleteEndpoint(${e.id})">✕</button>
          </div>
        </td>
      </tr>`).join('');
    });
    tbody.innerHTML = html;
}

async function reassignEndpoint(id) {
    const ep = _endpointsCache.find(e => e.id === id);
    const opts = _projectsCache.map(p => `${p.id}: ${p.name}`).join('\n');
    const choice = prompt(
        `Move "${ep.name}" to which project?\n\nEnter a project ID, or 0 for none:\n\n${opts}`,
        ep.project_id || '0'
    );
    if (choice === null) return;
    const pid = parseInt(choice) || null;
    await api(`/api/endpoints/${id}`, 'PATCH', { project_id: pid === 0 ? null : pid });
    toast('Endpoint moved'); loadEndpoints();
}

async function testEndpoint(id, name) {
    try {
        const r = await api(`/api/endpoints/${id}/test`, 'POST');
        toast(`${name}: ${r.success ? 'Token OK ✓' : 'Failed - ' + (r.error || 'check config')}`,
            r.success ? 'success' : 'error');
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteEndpoint(id) {
    if (!confirm('Delete this endpoint?')) return;
    await api(`/api/endpoints/${id}`, 'DELETE');
    toast('Endpoint deleted'); loadEndpoints();
}

function _fillProjectDropdown() {
    const sel = document.getElementById('ep-project');
    const current = sel.value;
    sel.innerHTML = '<option value="">— No project —</option>'
        + _projectsCache.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
    sel.value = current;
}

function _resetEndpointForm() {
    document.getElementById('ep-id').value = '';
    document.getElementById('ep-name').value = '';
    document.getElementById('ep-url').value = '';
    document.getElementById('ep-auth').value = 'bearer';
    document.getElementById('ep-token-url').value = '';
    document.getElementById('ep-scope').value = '';
    document.getElementById('ep-client-id').value = '';
    document.getElementById('ep-client-secret').value = '';
    document.getElementById('ep-api-key').value = '';
    document.getElementById('ep-api-key-header').value = 'X-API-Key';
    document.getElementById('ep-timeout').value = '30';
    document.getElementById('ep-headers').value = '{}';
}

async function openEndpointModal() {
    try {
        if (!_projectsCache.length) _projectsCache = await api('/api/projects');
    } catch (e) { }
    _resetEndpointForm();
    _fillProjectDropdown();
    // Create mode UI
    document.getElementById('endpoint-modal-title').textContent = 'New Endpoint';
    document.getElementById('ep-name').disabled = false;
    document.getElementById('ep-secret-hint').style.display = 'none';
    document.getElementById('ep-apikey-hint').style.display = 'none';
    document.getElementById('ep-client-secret').placeholder = '••••••••';
    document.getElementById('ep-api-key').placeholder = '';
    toggleAuthFields();
    document.getElementById('endpoint-modal').classList.add('open');
}

async function editEndpoint(id) {
    try {
        if (!_projectsCache.length) _projectsCache = await api('/api/projects');
    } catch (e) { }
    // We have the endpoint in cache (minus the secret, which the API never returns)
    const e = _endpointsCache.find(x => x.id === id);
    if (!e) return toast('Endpoint not found', 'error');

    _resetEndpointForm();
    _fillProjectDropdown();

    document.getElementById('endpoint-modal-title').textContent = 'Edit Endpoint';
    document.getElementById('ep-id').value = e.id;
    document.getElementById('ep-name').value = e.name;
    document.getElementById('ep-name').disabled = false;          // name is editable
    document.getElementById('ep-url').value = e.base_url || '';
    document.getElementById('ep-auth').value = e.auth_type || 'bearer';
    document.getElementById('ep-project').value = e.project_id || '';
    document.getElementById('ep-token-url').value = e.token_url || '';
    document.getElementById('ep-client-id').value = e.client_id || '';
    document.getElementById('ep-api-key-header').value = e.api_key_header || 'X-API-Key';
    document.getElementById('ep-timeout').value = e.default_timeout || 30;
    document.getElementById('ep-headers').value = JSON.stringify(e.extra_headers || {}, null, 0);

    // Secrets are never returned by the API. Show a hint that blank = keep existing.
    document.getElementById('ep-client-secret').value = '';
    document.getElementById('ep-client-secret').placeholder = '•••••• (unchanged)';
    document.getElementById('ep-secret-hint').style.display = 'block';
    document.getElementById('ep-api-key').value = '';
    document.getElementById('ep-api-key').placeholder = '•••••• (unchanged)';
    document.getElementById('ep-apikey-hint').style.display = 'block';

    toggleAuthFields();
    document.getElementById('endpoint-modal').classList.add('open');
}

async function saveEndpoint() {
    try {
        let extra = {};
        try { extra = JSON.parse(document.getElementById('ep-headers').value || '{}'); }
        catch (e) { return toast('Extra Headers must be valid JSON', 'error'); }

        const id = document.getElementById('ep-id').value;
        const projVal = document.getElementById('ep-project').value;
        const secret = document.getElementById('ep-client-secret').value;
        const apikey = document.getElementById('ep-api-key').value;

        const payload = {
            name: document.getElementById('ep-name').value,
            base_url: document.getElementById('ep-url').value,
            auth_type: document.getElementById('ep-auth').value,
            project_id: projVal ? +projVal : null,
            token_url: document.getElementById('ep-token-url').value || null,
            client_id: document.getElementById('ep-client-id').value || null,
            token_scope: document.getElementById('ep-scope').value || null,
            api_key_header: document.getElementById('ep-api-key-header').value,
            default_timeout: +document.getElementById('ep-timeout').value,
            extra_headers: extra,
        };

        if (id) {
            // EDIT: only send secret/api_key if the user typed a new one,
            // so a blank field keeps the stored value instead of wiping it.
            if (secret) payload.client_secret = secret;
            if (apikey) payload.api_key = apikey;
            await api(`/api/endpoints/${id}`, 'PATCH', payload);
            toast('Endpoint updated');
        } else {
            // CREATE: send secrets as-is (null if blank)
            payload.client_secret = secret || null;
            payload.api_key = apikey || null;
            await api('/api/endpoints', 'POST', payload);
            toast('Endpoint created');
        }
        closeModal('endpoint-modal');
        loadEndpoints();
    } catch (e) { toast(e.message, 'error'); }
}

function toggleAuthFields() {
    const v = document.getElementById('ep-auth').value;
    document.getElementById('oauth-fields').style.display = v === 'bearer' ? 'grid' : 'none';
    document.getElementById('apikey-fields').style.display = v === 'api_key' ? 'grid' : 'none';
}


// ── Jobs ──────────────────────────────────────────────────────────────────────
async function loadJobs() {
    try {
        const jobs = await api('/api/jobs');
        document.getElementById('badge-jobs').textContent = jobs.length;
        const tbody = document.getElementById('jobs-body');
        if (!jobs.length) {
            tbody.innerHTML = '<tr><td colspan="8"><div class="empty"><div class="icon">⏱</div><p>No jobs yet. Create one to automate API calls.</p></div></td></tr>';
            return;
        }
        tbody.innerHTML = jobs.map(j => `
      <tr>
        <td><strong>${j.name}</strong><br><span class="muted" style="font-size:11px">${j.description || ''}</span></td>
        <td><span class="pill ${j.job_type === 'webhook' ? 'purple' : 'info'}">${j.job_type}</span></td>
        <td class="mono" style="font-size:11px">${j.schedule || j.job_type === 'webhook' ? (j.schedule || 'event') : (j.run_at ? fmtTime(j.run_at) : '—')}</td>
        <td class="mono" style="font-size:11px">${j.action}</td>
        <td class="muted mono" style="font-size:11px">${j.last_run_at ? fmtTime(j.last_run_at) : 'Never'}</td>
        <td class="mono">${j.run_count || 0} <span class="muted">(${j.fail_count || 0} ✗)</span></td>
        <td><div class="status-dot ${j.is_active ? 'online' : 'offline'}"></div></td>
        <td>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <button class="btn btn-success btn-sm" onclick="runJobNow(${j.id}, '${j.name}')">▶ Run</button>
            <button class="btn btn-ghost btn-sm" onclick="toggleJob(${j.id}, ${!j.is_active})">${j.is_active ? 'Pause' : 'Resume'}</button>
            <button class="btn btn-danger btn-sm" onclick="deleteJob(${j.id})">✕</button>
          </div>
        </td>
      </tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

async function runJobNow(id, name) {
    try {
        const r = await api(`/api/jobs/${id}/run`, 'POST');
        toast(`${name}: ${r.success ? 'Completed ✓' : 'Failed: ' + (r.error || 'unknown')}`,
            r.success ? 'success' : 'error');
        loadJobs();
    } catch (e) { toast(e.message, 'error'); }
}

async function toggleJob(id, activate) {
    await api(`/api/jobs/${id}`, 'PATCH', { is_active: activate });
    loadJobs();
}

async function deleteJob(id) {
    if (!confirm('Delete this job?')) return;
    await api(`/api/jobs/${id}`, 'DELETE');
    toast('Job deleted'); loadJobs();
}

async function openJobModal() {
    document.getElementById('job-modal').classList.add('open');
    // Load available actions
    const r = await api('/api/actions');
    const sel = document.getElementById('job-action');
    sel.innerHTML = r.actions.map(a => `<option value="${a}">${a}</option>`).join('');
    toggleJobFields();
}

function toggleJobFields() {
    const t = document.getElementById('job-type').value;
    document.getElementById('job-schedule-wrap').style.display = t === 'cron' ? 'flex' : 'none';
    document.getElementById('job-interval-wrap').style.display = t === 'interval' ? 'flex' : 'none';
    document.getElementById('job-runat-wrap').style.display = t === 'onetime' ? 'flex' : 'none';
    document.getElementById('job-webhook-hint').style.display = t === 'webhook' ? 'flex' : 'none';
}

async function saveJob() {
    try {
        const t = document.getElementById('job-type').value;
        let params = {};
        try { params = JSON.parse(document.getElementById('job-params').value || '{}'); } catch (e) { }
        await api('/api/jobs', 'POST', {
            name: document.getElementById('job-name').value,
            description: document.getElementById('job-desc').value || null,
            job_type: t,
            schedule: t === 'cron' ? document.getElementById('job-schedule').value :
                t === 'interval' ? document.getElementById('job-interval').value : null,
            run_at: t === 'onetime' ? document.getElementById('job-run-at').value : null,
            action: document.getElementById('job-action').value,
            action_params: params,
        });
        closeModal('job-modal'); toast('Job created'); loadJobs();
    } catch (e) { toast(e.message, 'error'); }
}

// ── Webhooks ──────────────────────────────────────────────────────────────────
function setWebhookUrls() {
    const base = window.location.origin;
    document.getElementById('retell-url').textContent = base + '/webhooks/retell';
    document.getElementById('form-url').textContent = base + '/webhooks/form/{form_id}';
    document.getElementById('generic-url').textContent = base + '/webhooks/trigger/{job_name}';
}

async function loadWebhookEvents(source = '') {
    try {
        const r = await api(`/api/webhook-events?limit=50${source ? '&source=' + source : ''}`);
        document.getElementById('badge-webhooks').textContent = r.total;
        const tbody = document.getElementById('webhook-events-body');
        if (!r.items.length) {
            tbody.innerHTML = '<tr><td colspan="5"><div class="empty"><div class="icon">🪝</div><p>No webhook events received yet.</p></div></td></tr>';
            return;
        }
        tbody.innerHTML = r.items.map(e => `
      <tr>
        <td class="mono muted">${fmtTime(e.created_at)}</td>
        <td><span class="pill ${e.source === 'retell' ? 'purple' : 'info'}">${e.source}</span></td>
        <td class="mono" style="font-size:11px">${e.source_id || '—'}</td>
        <td class="mono" style="font-size:11px">${e.job_triggered || '—'}</td>
        <td>${e.job_run_success === null ? '<span class="pill pending">no job</span>' : e.job_run_success ? '<span class="pill success">OK</span>' : '<span class="pill fail">Failed</span>'}</td>
      </tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

// ── Call Logs ─────────────────────────────────────────────────────────────────
let logsOffset = 0;
async function loadLogs(offset = 0) {
    logsOffset = offset;
    const search = document.getElementById('log-search')?.value || '';
    const status = document.getElementById('log-status-filter')?.value;
    let url = `/api/logs?limit=50&offset=${offset}`;
    if (status !== '') url += `&success=${status}`;
    if (search) url += `&endpoint_name=${encodeURIComponent(search)}`;
    try {
        const r = await api(url);
        const tbody = document.getElementById('call-logs-body');
        if (!r.items.length) {
            tbody.innerHTML = '<tr><td colspan="8"><div class="empty"><div class="icon">📋</div><p>No calls logged yet.</p></div></td></tr>';
            document.getElementById('logs-pagination').innerHTML = '';
            return;
        }
        tbody.innerHTML = r.items.map(l => `
      <tr>
        <td class="mono muted">${fmtTime(l.created_at)}</td>
        <td><strong>${l.endpoint_name || '—'}</strong></td>
        <td><span class="pill info">${l.method || '—'}</span></td>
        <td class="mono" style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis">${shortUrl(l.url)}</td>
        <td><span class="pill ${httpColor(l.status_code)}">${l.status_code || 'ERR'}</span></td>
        <td class="mono">${l.response_time_ms || '—'}</td>
        <td class="muted" style="font-size:11px">${l.triggered_by || '—'}</td>
        <td><button class="btn btn-ghost btn-sm" onclick="showLogDetail(${l.id})">View</button></td>
      </tr>`).join('');

        const pages = Math.ceil(r.total / 50);
        const cur = Math.floor(offset / 50);
        let pag = `<span>${r.total.toLocaleString()} total</span><div class="pages">`;
        for (let i = 0; i < Math.min(pages, 8); i++) {
            pag += `<button class="page-btn ${i === cur ? 'active' : ''}" onclick="loadLogs(${i * 50})">${i + 1}</button>`;
        }
        pag += '</div>';
        document.getElementById('logs-pagination').innerHTML = pag;
    } catch (e) { toast(e.message, 'error'); }
}

async function showLogDetail(id) {
    try {
        const l = await api(`/api/logs/${id}`);
        document.getElementById('drawer-title').textContent = `Call #${l.id} — ${l.endpoint_name}`;
        document.getElementById('drawer-body').innerHTML = `
      <div class="detail-row"><span class="detail-label">Time</span><span class="mono">${fmtTime(l.created_at)}</span></div>
      <div class="detail-row"><span class="detail-label">Endpoint</span><span>${l.endpoint_name}</span></div>
      <div class="detail-row"><span class="detail-label">Method</span><span class="pill info">${l.method}</span></div>
      <div class="detail-row"><span class="detail-label">URL</span><span class="mono" style="font-size:11px;word-break:break-all">${l.url}</span></div>
      <div class="detail-row"><span class="detail-label">Status</span><span class="pill ${httpColor(l.status_code)}">${l.status_code || 'ERR'}</span></div>
      <div class="detail-row"><span class="detail-label">Response Time</span><span class="mono">${l.response_time_ms}ms</span></div>
      <div class="detail-row"><span class="detail-label">Triggered By</span><span>${l.triggered_by || '—'}</span></div>
      <div class="detail-row"><span class="detail-label">Token Refreshed</span><span>${l.token_refreshed ? '✓ Yes' : 'No'}</span></div>
      ${l.error_message ? `<div class="detail-row"><span class="detail-label">Error</span><span style="color:var(--red)">${l.error_message}</span></div>` : ''}
      <div style="margin-top:16px">
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:6px">REQUEST HEADERS</label>
        <pre>${JSON.stringify(l.request_headers, null, 2)}</pre>
      </div>
      ${l.request_body ? `<div style="margin-top:12px"><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:6px">REQUEST BODY</label><pre>${tryPretty(l.request_body)}</pre></div>` : ''}
      <div style="margin-top:12px">
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:6px">RESPONSE BODY</label>
        <pre>${tryPretty(l.response_body)}</pre>
      </div>
    `;
        document.getElementById('drawer-overlay').classList.add('open');
        document.getElementById('detail-drawer').classList.add('open');
    } catch (e) { toast(e.message, 'error'); }
}

// ── Job Runs ──────────────────────────────────────────────────────────────────
async function loadJobRuns() {
    try {
        const r = await api('/api/job-runs?limit=100');
        const tbody = document.getElementById('job-runs-body');
        if (!r.items.length) {
            tbody.innerHTML = '<tr><td colspan="6"><div class="empty"><div class="icon">🏃</div><p>No job runs yet.</p></div></td></tr>';
            return;
        }
        tbody.innerHTML = r.items.map(j => `
      <tr>
        <td class="mono muted">${fmtTime(j.started_at)}</td>
        <td><strong>${j.job_name}</strong></td>
        <td><span class="pill ${j.triggered_by?.startsWith('retell') ? 'purple' : 'info'}">${j.triggered_by}</span></td>
        <td class="mono">${j.duration_ms}ms</td>
        <td>${j.success ? '<span class="pill success"><span class="dot"></span>Success</span>' : '<span class="pill fail"><span class="dot"></span>Failed</span>'}</td>
        <td><button class="btn btn-ghost btn-sm" onclick="showRunDetail(${j.id})">View</button></td>
      </tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

async function showRunDetail(id) {
    const r = await api(`/api/job-runs?limit=200`);
    const j = r.items.find(x => x.id === id);
    if (!j) return;
    document.getElementById('drawer-title').textContent = `Run — ${j.job_name}`;
    document.getElementById('drawer-body').innerHTML = `
    <div class="detail-row"><span class="detail-label">Job</span><span>${j.job_name}</span></div>
    <div class="detail-row"><span class="detail-label">Triggered By</span><span>${j.triggered_by}</span></div>
    <div class="detail-row"><span class="detail-label">Started</span><span class="mono">${fmtTime(j.started_at)}</span></div>
    <div class="detail-row"><span class="detail-label">Duration</span><span class="mono">${j.duration_ms}ms</span></div>
    <div class="detail-row"><span class="detail-label">Result</span><span class="pill ${j.success ? 'success' : 'fail'}">${j.success ? 'Success' : 'Failed'}</span></div>
    ${j.error ? `<div class="detail-row"><span class="detail-label">Error</span><span style="color:var(--red)">${j.error}</span></div>` : ''}
    ${j.result ? `<div style="margin-top:12px"><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:6px">RESULT</label><pre>${tryPretty(j.result)}</pre></div>` : ''}
    ${j.context ? `<div style="margin-top:12px"><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:6px">CONTEXT / PAYLOAD</label><pre>${JSON.stringify(j.context, null, 2)}</pre></div>` : ''}
  `;
    document.getElementById('drawer-overlay').classList.add('open');
    document.getElementById('detail-drawer').classList.add('open');
}

// ── Admin Tools ───────────────────────────────────────────────────────────────
async function loadTools() {
    try {
        const r = await api('/api/tools');
        const tools = r.tools || [];
        const container = document.getElementById('tools-container');
        if (!tools.length) {
            container.innerHTML = '<div class="empty"><div class="icon">🛠</div><p>No tools registered.</p></div>';
            return;
        }
        // Group by category
        const groups = {};
        tools.forEach(t => { (groups[t.category] = groups[t.category] || []).push(t); });

        let html = '';
        Object.keys(groups).sort().forEach(cat => {
            html += `<div style="margin-bottom:24px">
        <div style="font-size:11px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;color:var(--muted);margin-bottom:12px">${cat}</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px">`;
            groups[cat].forEach(t => {
                const fields = (t.params || []).map(p => `
          <div class="form-group" style="margin-top:8px">
            <label>${p.label}${p.required ? ' *' : ''}</label>
            <input id="tool-${t.key}-${p.name}" placeholder="${p.placeholder || ''}">
          </div>`).join('');
                html += `
          <div class="stat-card" style="display:flex;flex-direction:column;gap:8px">
            <div style="font-size:14px;font-weight:600">${t.label}</div>
            <div style="font-size:12px;color:var(--muted);line-height:1.5">${t.description}</div>
            ${fields}
            <div style="margin-top:10px;display:flex;align-items:center;gap:8px">
              <button class="btn btn-primary btn-sm" id="tool-btn-${t.key}" onclick='runTool(${JSON.stringify(t)})'>Run</button>
              <span id="tool-status-${t.key}" style="font-size:12px;color:var(--muted)"></span>
            </div>
            <div id="tool-result-${t.key}" style="display:none;margin-top:4px"></div>
          </div>`;
            });
            html += `</div></div>`;
        });
        container.innerHTML = html;
    } catch (e) { toast(e.message, 'error'); }
}

async function runTool(t) {
    // Collect params
    const params = {};
    let missing = false;
    (t.params || []).forEach(p => {
        const v = document.getElementById(`tool-${t.key}-${p.name}`)?.value?.trim() || '';
        if (p.required && !v) missing = true;
        if (v) params[p.name] = v;
    });
    if (missing) return toast('Please fill in the required fields', 'error');
    if (t.confirm && !confirm(`Run "${t.label}"?`)) return;

    const btn = document.getElementById(`tool-btn-${t.key}`);
    const status = document.getElementById(`tool-status-${t.key}`);
    const resultEl = document.getElementById(`tool-result-${t.key}`);
    btn.disabled = true; status.textContent = 'Running...';
    resultEl.style.display = 'none';

    try {
        const r = await api(`/api/tools/${t.key}/run`, 'POST', { params });
        status.textContent = '';
        const ok = r.ok !== false;
        let html = `<div class="pill ${ok ? 'success' : 'fail'}" style="margin-bottom:6px">${ok ? '✓' : '✗'} ${r.summary || (ok ? 'Done' : 'Failed')}</div>`;
        if (Array.isArray(r.detail) && r.detail.length) {
            html += `<pre style="margin-top:4px">${r.detail.join('\n')}</pre>`;
        }
        resultEl.innerHTML = html;
        resultEl.style.display = 'block';
    } catch (e) {
        status.textContent = '';
        resultEl.innerHTML = `<div class="pill fail">✗ ${e.message}</div>`;
        resultEl.style.display = 'block';
    } finally {
        btn.disabled = false;
    }
}

// ── Operations ──────────────────────────────────────────────────────
let _operations = [];
let _operationEndpoints = [];

async function loadOperations() {
    try {
        const [ops, eps] = await Promise.all([
            api('/api/operations'),
            api('/api/endpoints'),
        ]);
        _operations = ops || [];
        _operationEndpoints = eps || [];
        document.getElementById('badge-operations').textContent = _operations.length;
        renderOperations();
    } catch (e) { toast(e.message, 'error'); }
}

function renderOperations() {
    const tbody = document.getElementById('operations-body');
    if (!tbody) return;
    if (!_operations.length) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty"><div class="icon">⚙️</div><p>No operations configured yet. Add one to get started.</p></div></td></tr>';
        return;
    }
    tbody.innerHTML = _operations.map(op => `
    <tr>
      <td><span class="mono" style="font-size:12px"><strong>${op.name}</strong></span>${op.label ? '<br><span class="muted" style="font-size:11px">' + op.label + '</span>' : ''}</td>
      <td><span class="pill info">${op.endpoint_name}</span></td>
      <td><span class="pill info">${op.method}</span></td>
      <td class="mono muted" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${op.path}</td>
      <td class="muted" style="font-size:11px">${op.default_params && Object.keys(op.default_params).length ? JSON.stringify(op.default_params) : '—'}</td>
      <td><div class="status-dot ${op.is_active ? 'online' : 'offline'}"></div></td>
      <td>
        <div style="display:flex;gap:4px">
          <button class="btn btn-ghost btn-sm" onclick="editOperation(${op.id})">Edit</button>
          <button class="btn btn-danger btn-sm" onclick="deleteOperation(${op.id}, '${op.name}')">✕</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function openOperationModal(op) {
    document.getElementById('op-id').value = op ? op.id : '';
    document.getElementById('op-name').value = op ? op.name : '';
    document.getElementById('op-label').value = op ? (op.label || '') : '';
    document.getElementById('op-path').value = op ? op.path : '';
    document.getElementById('op-method').value = op ? op.method : 'GET';
    document.getElementById('op-active').value = op ? String(op.is_active) : 'true';
    document.getElementById('op-params').value = op ? JSON.stringify(op.default_params || {}, null, 2) : '{}';
    document.getElementById('op-desc').value = op ? (op.description || '') : '';
    document.getElementById('op-tags').value = op ? (op.tags || []).join(', ') : '';
    document.getElementById('operation-modal-title').textContent = op ? 'Edit Operation' : 'Add Operation';
    const sel = document.getElementById('op-endpoint');
    sel.innerHTML = _operationEndpoints.map(e =>
        `<option value="${e.name}" ${op && op.endpoint_name === e.name ? 'selected' : ''}>${e.name}</option>`
    ).join('');
    document.getElementById('operation-modal').classList.add('open');
}

function editOperation(id) {
    const op = _operations.find(o => o.id === id);
    if (op) openOperationModal(op);
}

async function saveOperation() {
    const id = document.getElementById('op-id').value;
    let defaultParams = {};
    try { defaultParams = JSON.parse(document.getElementById('op-params').value || '{}'); }
    catch { return toast('Default Params must be valid JSON', 'error'); }
    const tags = document.getElementById('op-tags').value.split(',').map(t => t.trim()).filter(Boolean);
    const payload = {
        name: document.getElementById('op-name').value.trim(),
        label: document.getElementById('op-label').value.trim(),
        endpoint_name: document.getElementById('op-endpoint').value,
        method: document.getElementById('op-method').value,
        path: document.getElementById('op-path').value.trim(),
        default_params: defaultParams,
        description: document.getElementById('op-desc').value.trim(),
        tags,
        is_active: document.getElementById('op-active').value === 'true',
    };
    if (!payload.name || !payload.path) return toast('Name and Path are required', 'error');
    try {
        if (id) { await api(`/api/operations/${id}`, 'PUT', payload); toast('Operation updated'); }
        else { await api('/api/operations', 'POST', payload); toast('Operation created'); }
        closeModal('operation-modal'); loadOperations();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteOperation(id, name) {
    if (!confirm(`Delete operation "${name}"?`)) return;
    await api(`/api/operations/${id}`, 'DELETE');
    toast('Operation deleted'); loadOperations();
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
    try {
        const s = await api('/api/settings');
        const el = document.getElementById('settings-list');
        const keys = Object.keys(s);
        if (!keys.length) { el.innerHTML = '<p class="muted">No settings saved yet.</p>'; return; }
        el.innerHTML = keys.map(k => `
      <div style="display:flex;align-items:center;gap:8px">
        <span class="mono" style="flex:1;font-size:12px;color:var(--muted)">${k}</span>
        <input value="${s[k].value || ''}" id="setting-${k}" style="flex:2">
        <button class="btn btn-ghost btn-sm" onclick="updateSetting('${k}')">Save</button>
      </div>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}

async function updateSetting(key) {
    const val = document.getElementById('setting-' + key)?.value;
    await api(`/api/settings/${key}`, 'PUT', { value: val });
    toast('Saved'); loadSettings();
}

async function saveSetting() {
    const k = document.getElementById('new-key').value.trim();
    const v = document.getElementById('new-val').value;
    if (!k) return toast('Key required', 'error');
    await api(`/api/settings/${k}`, 'PUT', { value: v });
    document.getElementById('new-key').value = '';
    document.getElementById('new-val').value = '';
    toast('Setting saved'); loadSettings();
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
function closeDrawer() {
    document.getElementById('drawer-overlay').classList.remove('open');
    document.getElementById('detail-drawer').classList.remove('open');
}

function fmtTime(ts) {
    if (!ts) return '—';
    return new Date(ts).toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
    });
}

function shortUrl(url) {
    if (!url) return '—';
    try { const u = new URL(url); return u.pathname + u.search; } catch { return url; }
}

function httpColor(code) {
    if (!code) return 'fail';
    if (code < 300) return 'success';
    if (code < 400) return 'pending';
    return 'fail';
}

function tryPretty(str) {
    if (!str) return '—';
    try { return JSON.stringify(JSON.parse(str), null, 2); } catch { return str; }
}

function filterTable(tbodyId, query, cols) {
    const rows = document.getElementById(tbodyId).querySelectorAll('tr');
    const q = query.toLowerCase();
    rows.forEach(row => {
        const text = cols.map(c => row.cells[c]?.textContent.toLowerCase()).join(' ');
        row.style.display = text.includes(q) ? '' : 'none';
    });
}

function copyText(elId) {
    navigator.clipboard.writeText(document.getElementById(elId).textContent);
    toast('Copied to clipboard');
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadDashboard();
setInterval(loadDashboard, 30000);