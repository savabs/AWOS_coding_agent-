function badgeClass(status) {
  return `badge ${status || "modified"}`;
}

function renderSessions(sessions, activeId) {
  const el = document.getElementById("sessions");
  if (!el) return;
  if (!sessions.length) {
    el.innerHTML = `<div class="empty">No runs yet.<br>Use <strong>Agent</strong> mode in Chat.</div>`;
    return;
  }
  el.innerHTML = sessions
    .map((s) => {
      const sum = s.summary || {};
      return `
        <div class="session-item ${s.session_id === activeId ? "active" : ""}" data-id="${s.session_id}">
          <div class="goal">${escapeHtml(s.goal || s.session_id)}</div>
          <div class="sub">${s.session_id} · ${s.status || "?"} · ${sum.total_files || 0} files</div>
        </div>`;
    })
    .join("");

  el.querySelectorAll(".session-item").forEach((node) => {
    node.addEventListener("click", () => loadSession(node.dataset.id));
  });
}

function renderHeader(manifest) {
  const el = document.getElementById("header");
  if (!el) return;
  const sum = manifest.summary || {};
  el.innerHTML = `
    <h1>${escapeHtml(manifest.goal || manifest.session_id)}</h1>
    <div class="meta">${manifest.session_id} · ${manifest.status || "unknown"}</div>
    <div class="summary-bar">
      <span>${manifest.tasks_completed ?? 0} completed</span>
      <span>${manifest.tasks_failed ?? 0} failed</span>
      <span>${sum.created || 0} created</span>
      <span>${sum.modified || 0} modified</span>
      <span class="add-text">+${sum.lines_added || 0}</span>
      <span class="del-text">-${sum.lines_removed || 0}</span>
      <span>$${(manifest.total_cost_usd || 0).toFixed(4)}</span>
    </div>`;
}

function renderFileList(files, activePath) {
  const el = document.getElementById("files");
  if (!el) return;
  if (!files.length) {
    el.innerHTML = `<div class="empty">No file changes in this run.</div>`;
    return;
  }
  el.innerHTML = files
    .map(
      (f) => `
      <div class="file-item ${f.path === activePath ? "active" : ""}" data-path="${escapeAttr(f.path)}">
        <span class="${badgeClass(f.status)}">${f.status}</span>
        ${escapeHtml(f.path)}
        <div class="sub">+${f.lines_added} -${f.lines_removed}</div>
      </div>`
    )
    .join("");

  el.querySelectorAll(".file-item").forEach((node) => {
    node.addEventListener("click", () => showDiff(files, node.dataset.path));
  });
}

function showDiff(files, path) {
  const file = files.find((f) => f.path === path) || files[0];
  if (!file) return;

  document.querySelectorAll(".file-item").forEach((n) => {
    n.classList.toggle("active", n.dataset.path === file.path);
  });

  const panel = document.getElementById("diff");
  const lines = [];

  if (file.hunks && file.hunks.length) {
    for (const hunk of file.hunks) {
      for (const ln of hunk.lines) {
        lines.push(renderDiffLine(ln));
      }
    }
  } else if (file.unified_diff) {
    for (const raw of file.unified_diff.split("\n")) {
      const type =
        raw.startsWith("+") && !raw.startsWith("+++")
          ? "add"
          : raw.startsWith("-") && !raw.startsWith("---")
            ? "remove"
            : raw.startsWith("@@")
              ? "header"
              : "context";
      lines.push(`<div class="diff-line ${type}">${escapeHtml(raw)}</div>`);
    }
  } else {
    lines.push(`<div class="empty">No diff content stored for this file.</div>`);
  }

  panel.innerHTML = `
    <div class="diff-header">
      <div><span class="${badgeClass(file.status)}">${file.status}</span> <strong>${escapeHtml(file.path)}</strong></div>
      <div class="diff-stats">+${file.lines_added} additions · -${file.lines_removed} deletions</div>
    </div>
    <div class="diff-body">${lines.join("")}</div>`;
}

function renderDiffLine(ln) {
  const oldNo = ln.old_line_no != null ? String(ln.old_line_no).padStart(4) : "    ";
  const newNo = ln.new_line_no != null ? String(ln.new_line_no).padStart(4) : "    ";
  const prefix = ln.type === "add" ? "+" : ln.type === "remove" ? "-" : " ";
  return `<div class="diff-line ${ln.type}">
    <span class="line-no">${oldNo} ${newNo}</span>${prefix} ${escapeHtml(ln.content)}
  </div>`;
}

let currentFiles = [];

async function loadSession(id) {
  const manifest = await fetchJSON(`${API}/api/sessions/${id}`);
  const filesResp = await fetchJSON(`${API}/api/sessions/${id}/files`);
  currentFiles = filesResp.files || [];
  renderHeader(manifest);
  renderFileList(currentFiles, currentFiles[0]?.path);
  if (currentFiles.length) showDiff(currentFiles, currentFiles[0].path);
  else {
    const diff = document.getElementById("diff");
    if (diff) diff.innerHTML = `<div class="empty">No changes yet.</div>`;
  }
  const sessions = await fetchJSON(`${API}/api/sessions`);
  renderSessions(sessions, id);
  const params = new URLSearchParams(location.search);
  params.set("view", "runs");
  params.set("session", id);
  history.replaceState({}, "", `?${params}`);
}

async function initRuns() {
  if (getView() !== "runs") return;
  const sessions = await fetchJSON(`${API}/api/sessions`);
  const params = new URLSearchParams(location.search);
  const id = params.get("session") || sessions[0]?.session_id;
  renderSessions(sessions, id);
  if (id) await loadSession(id);
}

window.initRuns = initRuns;

if (getView() === "runs") {
  initRuns().catch((err) => {
    const el = document.getElementById("view-runs");
    if (el) el.innerHTML = `<div class="empty">Failed to load runs: ${escapeHtml(err.message)}</div>`;
  });
}
