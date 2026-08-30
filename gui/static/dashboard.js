// Coding Agent Dashboard JavaScript

const API = window.location.origin;

// Initialize dashboard
async function initDashboard() {
  if (getView() !== "dashboard") return;
  
  try {
    const data = await fetchJSON(`${API}/api/sessions/stats`);
    renderDashboardStats(data);
    
    const runs = await fetchJSON(`${API}/api/sessions/history`);
    renderRescueList(runs);

    // Load transparent routing data
    loadRoutingPanel();
  } catch (err) {
    console.error("Failed to load dashboard:", err);
    const list = document.getElementById("rescue-list");
    if (list) {
      list.innerHTML = `
        <div class="empty-state">
          <h2>Failed to load dashboard</h2>
          <p>${escapeHtml(err.message)}</p>
        </div>
      `;
    }
  }
}

// ── Transparent Routing Panel ────────────────────────────────────────────

async function loadRoutingPanel() {
  try {
    const status = await fetchJSON(`${API}/api/routing/status`);
    renderRoutingStatus(status);

    const history = await fetchJSON(`${API}/api/routing/history`);
    renderRoutingHistory(history);
  } catch (err) {
    const el = document.getElementById("routing-model-perf");
    if (el) el.innerHTML = `<span style="color:#999;">Routing data unavailable (run AWOS first)</span>`;
  }
}

function renderRoutingStatus(data) {
  const routing = data.routing || {};

  // Learning status
  const readyEl = document.getElementById("routing-ready");
  if (readyEl) {
    readyEl.textContent = routing.linucb_ready ? "🎓 Active" : "🆕 Learning";
    readyEl.style.color = routing.linucb_ready ? "#10b981" : "#f59e0b";
  }

  const updatesEl = document.getElementById("routing-updates");
  if (updatesEl) {
    updatesEl.textContent = `${routing.linucb_updates || 0} updates`;
  }

  // Total episodes
  const perf = data.performance || {};
  const epsEl = document.getElementById("routing-episodes");
  if (epsEl) epsEl.textContent = perf.total_episodes || 0;

  const trendEl = document.getElementById("routing-trend");
  if (trendEl && perf.total_episodes > 0) {
    trendEl.textContent = "Learning active ✓";
  }

  // Cheap mode
  const cheapEl = document.getElementById("routing-cheap");
  if (cheapEl) {
    cheapEl.textContent = routing.is_cheap_only ? "💰 Cheap Only" : "🎯 Balanced";
    cheapEl.style.color = routing.is_cheap_only ? "#f59e0b" : "#10b981";
  }

  // Active providers
  const provEl = document.getElementById("routing-providers");
  if (provEl) {
    const dead = routing.dead_providers || [];
    provEl.textContent = dead.length > 0
      ? `⚠ ${dead.length} dead`
      : "All active";
    provEl.style.color = dead.length > 0 ? "#ef4444" : "#10b981";
  }

  // Per-model performance table
  const tableEl = document.getElementById("routing-model-perf");
  if (tableEl && perf.by_model) {
    const models = Object.entries(perf.by_model);
    if (models.length === 0) {
      tableEl.innerHTML = "<span style='color:#999;'>No model data yet. Run AWOS to accumulate statistics.</span>";
      return;
    }
    let html = `<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
      <tr style="border-bottom:2px solid #e5e7eb;color:#6b7280;">
        <th style="text-align:left;padding:8px;">Model</th>
        <th style="text-align:right;padding:8px;">Attempts</th>
        <th style="text-align:right;padding:8px;">Success</th>
        <th style="text-align:right;padding:8px;">Avg Cost</th>
      </tr>`;
    for (const [name, stats] of models) {
      const sr = (stats.success_rate * 100).toFixed(0);
      const color = sr >= 70 ? "#10b981" : sr >= 50 ? "#f59e0b" : "#ef4444";
      const bar = "█".repeat(Math.round(stats.success_rate * 10)) +
                  "░".repeat(10 - Math.round(stats.success_rate * 10));
      html += `<tr style="border-bottom:1px solid #f3f4f6;">
        <td style="padding:8px;font-weight:600;">${escapeHtml(name)}</td>
        <td style="text-align:right;padding:8px;">${stats.attempts}</td>
        <td style="text-align:right;padding:8px;color:${color};">
          ${bar} ${sr}%
        </td>
        <td style="text-align:right;padding:8px;color:#6b7280;">
          $${stats.avg_cost_usd.toFixed(4)}
        </td>
      </tr>`;
    }
    html += "</table>";
    tableEl.innerHTML = html;
  }
}

function renderRoutingHistory(history) {
  const el = document.getElementById("routing-history");
  if (!el) return;

  if (!history || history.length === 0) {
    el.innerHTML = "<span style='color:#999;'>No routing decisions yet.</span>";
    return;
  }

  let html = "";
  for (const item of history.slice(0, 15)) {
    const statusIcon = item.success ? "✅" : "❌";
    const statusColor = item.success ? "#10b981" : "#ef4444";
    const rewardSign = item.reward >= 0 ? "+" : "";
    const rewardColor = item.reward >= 0 ? "#10b981" : "#ef4444";
    html += `<div style="display:flex;align-items:center;padding:6px 0;border-bottom:1px solid #f3f4f6;gap:12px;">
      <span>${statusIcon}</span>
      <span style="flex:1;font-family:monospace;font-size:0.8rem;">${escapeHtml(item.task || "—")}</span>
      <span style="background:#f3f4f6;padding:2px 8px;border-radius:4px;font-size:0.75rem;">${escapeHtml(item.model)}</span>
      <span style="color:${rewardColor};font-size:0.8rem;">${rewardSign}${item.reward.toFixed(2)}</span>
      <span style="color:#9ca3af;font-size:0.75rem;">$${item.cost_usd.toFixed(4)}</span>
    </div>`;
  }
  el.innerHTML = html;
}

// ── Existing dashboard functions ─────────────────────────────────────────

function renderDashboardStats(data) {
  // Total runs
  document.getElementById("stat-rescues").textContent = data.total_runs || data.total_rescues || 0;
  
  // Tasks completed
  document.getElementById("stat-tests").textContent = data.total_tasks_completed || data.total_tests_fixed || 0;
  
  // Total cost
  const cost = data.total_cost_usd || 0;
  document.getElementById("stat-cost").textContent = `$${cost.toFixed(2)}`;
  
  // Savings vs always-premium routing baseline (rough 3x estimate)
  const heavyBaselineCost = cost * 3;
  const savings = heavyBaselineCost - cost;
  const savingsPct = heavyBaselineCost > 0 ? ((savings / heavyBaselineCost) * 100).toFixed(0) : 0;
  document.getElementById("stat-savings").textContent = `$${savings.toFixed(2)} (${savingsPct}%)`;
  
  // Trends
  if (data.trend_rescues) {
    document.getElementById("trend-rescues").textContent = data.trend_rescues;
  }
  if (data.trend_tests) {
    document.getElementById("trend-tests").textContent = data.trend_tests;
  }
  if (data.trend_cost) {
    const trendEl = document.getElementById("trend-cost");
    trendEl.textContent = data.trend_cost;
    trendEl.className = data.trend_cost.includes("-") ? "trend positive" : "trend";
  }
  if (data.trend_savings) {
    document.getElementById("trend-savings").textContent = data.trend_savings;
  }
}

function renderRescueList(rescues) {
  const list = document.getElementById("rescue-list");
  if (!list) return;
  
  if (!rescues || rescues.length === 0) {
    list.innerHTML = `
      <div class="empty-state">
        <h2>No agent runs yet</h2>
        <p>Run <code>awos worker start "fix the auth bug"</code> or <code>awos fix-ci</code> for a CI repair benchmark to get started.</p>
        <button class="btn-primary" onclick="showInstructions()">Show Instructions</button>
      </div>
    `;
    return;
  }
  
  list.innerHTML = rescues.map(rescue => {
    const status = rescue.success ? "success" : rescue.tasks_failed > 0 ? "warning" : "error";
    const statusText = rescue.success ? "Complete" : "Partial";
    const date = new Date(rescue.timestamp).toLocaleDateString();
    const time = new Date(rescue.timestamp).toLocaleTimeString();
    
    return `
      <div class="rescue-item" onclick="viewRescue('${rescue.session_id}')">
        <div class="rescue-header">
          <div class="rescue-title">${escapeHtml(rescue.goal || "Coding Agent Run")}</div>
          <span class="badge badge-${status}">${statusText}</span>
        </div>
        <div class="rescue-meta">
          <span>${date} ${time}</span>
          <span>${rescue.session_id}</span>
        </div>
        <div class="rescue-stats">
          <div class="rescue-stat">
            <strong>${rescue.tasks_completed || 0}</strong> tasks completed
          </div>
          <div class="rescue-stat">
            <strong>${rescue.tasks_failed || 0}</strong> failed
          </div>
          <div class="rescue-stat">
            <strong>$${(rescue.total_cost_usd || 0).toFixed(4)}</strong> cost
          </div>
          <div class="rescue-stat">
            <strong>${rescue.tests_fixed || 0}</strong> tests fixed
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function viewRescue(sessionId) {
  // Navigate to runs view with this session
  const params = new URLSearchParams(location.search);
  params.set("view", "runs");
  params.set("session", sessionId);
  window.location.href = `?${params}`;
}

function showInstructions() {
  const list = document.getElementById("rescue-list");
  if (!list) return;
  
  list.innerHTML = `
    <div style="padding: 2rem;">
      <h2 style="margin-bottom: 1rem;">How to Use the Coding Agent</h2>
      <ol style="line-height: 1.8; margin-bottom: 2rem;">
        <li>Navigate to your repository</li>
        <li>Run <code style="background: #f3f4f6; padding: 0.25rem 0.5rem; border-radius: 4px;">awos worker start "fix the auth bug"</code> for general coding work</li>
        <li>Or run <code style="background: #f3f4f6; padding: 0.25rem 0.5rem; border-radius: 4px;">awos fix-ci</code> for CI repair</li>
        <li>Review the changes and merge when ready</li>
      </ol>
      
      <h3 style="margin-bottom: 0.5rem;">Example:</h3>
      <pre style="background: #1f2937; color: #f3f4f6; padding: 1rem; border-radius: 6px; overflow-x: auto;"><code>cd my-project
awos fix-ci

# Output:
# [AGENT] Goal accepted
# [AGENT] Planning coding work...
# [AGENT] Running tasks with transparent routing
# [AGENT] Verifying changes
# ...
# [AGENT] Run complete
# [AGENT] Tasks complete · Cost: $0.0056</code></pre>
      
      <button class="btn-primary" onclick="initDashboard()" style="margin-top: 1rem;">Back to Dashboard</button>
    </div>
  `;
}

// Export for use in index.html
window.initDashboard = initDashboard;
window.viewRescue = viewRescue;
window.showInstructions = showInstructions;

// Auto-initialize if on dashboard view
if (getView() === "dashboard") {
  initDashboard();
}
