/**
 * main.js
 * Wires up sidebar navigation (showPage) and triggers the initial data
 * load for each page on first visit / app boot.
 */

const PAGE_LOADERS = {
  "dashboard": loadDashboard,
  "projects": loadProjects,
  "endpoints": loadEndpoints,
  "office-map": loadOfficeMap,
  "operations": loadOperations,
  "jobs": loadJobs,
  "webhooks-page": () => { loadWebhookUrls(); loadWebhookEvents(); },
  "call-logs": () => loadLogs(0),
  "job-runs": loadJobRuns,
  "tools": loadTools,
  "settings": loadSettings,
};

const loadedPages = new Set();

function showPage(pageId) {
  document.querySelectorAll(".page").forEach((el) => (el.style.display = "none"));
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));

  const page = document.getElementById(`page-${pageId}`);
  if (page) page.style.display = "block";

  const navButtons = document.querySelectorAll(".nav-item");
  navButtons.forEach((btn) => {
    if (btn.getAttribute("onclick") === `showPage('${pageId}')`) {
      btn.classList.add("active");
    }
  });

  // Lazy-load each page's data the first time it's opened; dashboard
  // and pages with live data (logs, job runs) always refresh.
  const alwaysReload = ["dashboard", "call-logs", "job-runs"];
  if (!loadedPages.has(pageId) || alwaysReload.includes(pageId)) {
    PAGE_LOADERS[pageId]?.();
    loadedPages.add(pageId);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  // Projects and endpoints are needed by several pages' dropdowns,
  // so warm the cache before showing the default page.
  await Promise.allSettled([loadProjects(), loadEndpoints()]);
  showPage("dashboard");
});
