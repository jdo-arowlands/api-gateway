/**
 * ui.js
 * Generic, page-agnostic UI helpers: toasts, modals, the detail drawer,
 * and the simple client-side table filter used on a couple of pages.
 */

// ── Toast ─────────────────────────────────────────────────────────────
function showToast(message, type = "success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = type === "error" ? "show error" : "show success";
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.className = "";
  }, 3000);
}

// ── Modals ────────────────────────────────────────────────────────────
function openModal(id) {
  document.getElementById(id).classList.add("open");
}

function closeModal(id) {
  document.getElementById(id).classList.remove("open");
}

// ── Detail drawer (call log / job run detail) ───────────────────────
function openDrawer(title, bodyHtml) {
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-body").innerHTML = bodyHtml;
  document.getElementById("drawer-overlay").classList.add("open");
  document.getElementById("detail-drawer").classList.add("open");
}

function closeDrawer() {
  document.getElementById("drawer-overlay").classList.remove("open");
  document.getElementById("detail-drawer").classList.remove("open");
}

// ── Copy to clipboard ────────────────────────────────────────────────
function copyText(elementId) {
  const text = document.getElementById(elementId).textContent;
  navigator.clipboard.writeText(text)
    .then(() => showToast("Copied to clipboard"))
    .catch(() => showToast("Could not copy", "error"));
}

// ── Generic client-side table filter ────────────────────────────────
// columnIndexes: which <td> columns (0-based) to search against.
function filterTable(tbodyId, query, columnIndexes) {
  const q = query.trim().toLowerCase();
  const rows = document.querySelectorAll(`#${tbodyId} tr`);
  rows.forEach((row) => {
    const cells = row.querySelectorAll("td");
    if (!cells.length) return; // skip "Loading..." / empty rows
    const haystack = columnIndexes
      .map((i) => (cells[i] ? cells[i].textContent.toLowerCase() : ""))
      .join(" ");
    row.style.display = !q || haystack.includes(q) ? "" : "none";
  });
}

// ── Small render helpers shared across pages ────────────────────────
function statusPill(success) {
  return success
    ? `<span class="pill success"><span class="dot"></span>Success</span>`
    : `<span class="pill fail"><span class="dot"></span>Failed</span>`;
}

function emptyRow(colspan, text = "No records found") {
  return `<tr><td colspan="${colspan}" class="muted" style="text-align:center;padding:40px">${text}</td></tr>`;
}

function errorRow(colspan, message = "Failed to load data") {
  return `<tr><td colspan="${colspan}" class="muted" style="text-align:center;padding:40px;color:var(--red)">${message}</td></tr>`;
}
