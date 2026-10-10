let currentChatId = null;
let currentMode = "qa";
let modes = [];
let sending = false;
let activeAbort = null;
let stopRequested = false;

function emitObserverUIEvent(type, payload = {}, chatId = currentChatId, traceId = null) {
  // Observer telemetry is best-effort and intentionally excludes raw input.
  fetch(`${API}/api/observer/ui-event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, payload, chat_id: chatId, trace_id: traceId }),
    keepalive: true,
  }).catch(() => {});
}

const MODE_ICONS = { qa: "?", plan: "≡", agent: "⚙" };

async function loadModes() {
  const data = await fetchJSON(`${API}/api/modes`);
  modes = Array.isArray(data) ? data : (data.modes || []);
  if (!modes.length) {
    throw new Error("No chat modes returned from /api/modes");
  }
  renderModePicker();
  renderModeCards();
}

function renderModePicker() {
  const el = document.getElementById("mode-picker");
  el.innerHTML = modes
    .map(
      (m) => `
    <label class="mode-option ${m.id === currentMode ? "active" : ""}">
      <input type="radio" name="mode" value="${m.id}" ${m.id === currentMode ? "checked" : ""} />
      <span class="mode-icon">${MODE_ICONS[m.id] || "·"}</span>
      <span class="mode-label">${escapeHtml(m.label)}</span>
    </label>`
    )
    .join("");

  el.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      currentMode = input.value;
      emitObserverUIEvent("view_changed", { view: "chat", mode: currentMode });
      el.querySelectorAll(".mode-option").forEach((n) => {
        n.classList.toggle("active", n.querySelector("input").value === currentMode);
      });
      updatePlaceholder();
    });
  });
}

function renderModeCards() {
  const el = document.getElementById("mode-cards");
  if (!el) return;
  el.innerHTML = modes
    .map(
      (m) => `
    <div class="mode-card" data-mode="${m.id}">
      <div class="mode-card-title">${MODE_ICONS[m.id] || ""} ${escapeHtml(m.label)}</div>
      <div class="mode-card-desc">${escapeHtml(m.description)}</div>
    </div>`
    )
    .join("");
  el.querySelectorAll(".mode-card").forEach((card) => {
    card.addEventListener("click", () => {
      currentMode = card.dataset.mode;
      document.querySelectorAll("#mode-picker input").forEach((i) => {
        i.checked = i.value === currentMode;
      });
      renderModePicker();
      updatePlaceholder();
      document.getElementById("chat-input").focus();
    });
  });
}

function updatePlaceholder() {
  const placeholders = {
    qa: "Explain this repo, how does cheap-only mode work, what's the architecture…",
    plan: "Plan: add rate limiting to the API, refactor worker prompts…",
    agent: "In scaffold/agent/worker.py add a comment above _try_cheap_fallback…",
  };
  document.getElementById("chat-input").placeholder =
    placeholders[currentMode] || "Type your message…";
}

function renderChatSessions(sessions, activeId) {
  const el = document.getElementById("chat-sessions");
  const clearBtn = document.getElementById("btn-clear-chats");
  if (clearBtn) clearBtn.style.display = sessions.length ? "" : "none";

  if (!sessions.length) {
    el.innerHTML = `<div class="empty">No chats yet.</div>`;
    return;
  }
  el.innerHTML = sessions
    .map(
      (s) => `
    <div class="session-item ${s.chat_id === activeId ? "active" : ""}" data-id="${s.chat_id}">
      <div class="session-item-row">
        <div class="goal">${escapeHtml(s.title)}</div>
        <button type="button" class="btn-delete-chat" data-id="${escapeAttr(s.chat_id)}" title="Delete chat" aria-label="Delete chat">×</button>
      </div>
      <div class="sub">${s.message_count || 0} msgs${s.total_tokens ? ` · ${formatTokens(s.total_tokens)} tok` : ""} · ${(s.updated_at || "").slice(0, 16)}</div>
    </div>`
    )
    .join("");

  el.querySelectorAll(".session-item").forEach((node) => {
    node.addEventListener("click", () => loadChat(node.dataset.id));
  });
  el.querySelectorAll(".btn-delete-chat").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteChat(btn.dataset.id);
    });
  });
}

function renderMetaBadge(meta) {
  if (!meta) return "";
  const parts = [];
  const tok = formatTokenMeta(meta);
  if (tok) parts.push(tok);
  if (meta.memory_turns > 0) parts.push(`memory: ${meta.memory_turns} turns`);
  if (meta.model && meta.model !== "cache") parts.push(meta.model);
  if (meta.cost_usd != null && !meta.cache_hit && meta.cost_usd > 0) {
    parts.push(`$${meta.cost_usd.toFixed(4)}`);
  }
  if (!parts.length) return "";
  return `<span class="sub meta-badge">${escapeHtml(parts.join(" · "))}</span>`;
}

function renderPromptTrace(meta) {
  const trace = meta?.prompt_trace;
  if (!trace) return "";
  return `
    <details class="prompt-trace">
      <summary>Request trace</summary>
      <pre>${escapeHtml(JSON.stringify(trace, null, 2))}</pre>
    </details>`;
}

function updateSessionTokenBar(session) {
  const el = document.getElementById("session-token-bar");
  if (!el) return;
  const stats = session?.token_stats || {};
  const total = stats.total_tokens || 0;
  if (!total) {
    el.textContent = "";
    el.classList.remove("visible");
    return;
  }
  const msgs = session.messages || [];
  let ctxSum = 0;
  let costSum = 0;
  for (const m of msgs) {
    if (m.role === "assistant" && m.meta) {
      ctxSum += m.meta.context_tokens_est || 0;
      costSum += m.meta.cost_usd || 0;
    }
  }
  let line = `Session: ${formatTokens(total)} tok (${formatTokens(stats.completion_tokens || 0)} gen)`;
  if (ctxSum) line += ` · ~${formatTokens(ctxSum)} context loaded`;
  if (costSum > 0) line += ` · $${costSum.toFixed(4)}`;
  el.textContent = line;
  el.classList.add("visible");
}

function updateLiveTokenMeta(meta, streamText) {
  const el = document.getElementById("live-token-meta");
  if (!el) return;
  if (meta) {
    el.textContent = formatTokenMeta(meta);
    return;
  }
  if (streamText) {
    const est = Math.max(1, Math.floor(streamText.length / 4));
    el.textContent = `~${formatTokens(est)} out (streaming…)`;
  }
}

function renderMessages(messages) {
  const el = document.getElementById("chat-messages");
  if (!messages.length) {
    el.innerHTML = `
      <div class="empty chat-welcome">
        <h2>AWOS Chat</h2>
        <p>Choose a mode, then ask anything about this project.</p>
        <div class="mode-cards" id="mode-cards"></div>
      </div>`;
    renderModeCards();
    return;
  }

  el.innerHTML = messages
    .map((m) => {
      const isUser = m.role === "user";
      const modeBadge =
        isUser && m.mode
          ? `<span class="badge mode-badge">${escapeHtml(m.mode)}</span>`
          : "";
      const meta = m.meta || {};
      let extra = "";
      if (!isUser && m.mode === "agent" && meta.run_session_id) {
        extra = `<div class="msg-actions">
          <a href="?view=runs&session=${escapeAttr(meta.run_session_id)}" class="btn-link">View diffs →</a>
          <span class="sub">${meta.tasks_completed || 0}/${meta.total_tasks || 0} tasks · $${(meta.cost_usd || 0).toFixed(4)}</span>
        </div>`;
      }
      if (!isUser && m.mode === "plan" && meta.tasks) {
        extra = `<div class="msg-actions sub">${meta.total_tasks} planned task(s) — switch to Agent to run</div>`;
      }
      if (!isUser) {
        if (meta.cancelled) {
          const stopNote = meta.agent_may_continue
            ? "Ended early — agent may still be running"
            : "Stopped — agent run cancelled";
          extra += `<div class="msg-actions sub stopped-label">${escapeHtml(stopNote)}</div>`;
        }
        extra += renderPromptTrace(meta);
        extra += renderMetaBadge(meta);
      }
      const stoppedClass = !isUser && meta.cancelled ? " stopped" : "";
      return `
      <div class="chat-msg ${isUser ? "user" : "assistant"}${stoppedClass}">
        <div class="msg-header">${isUser ? "You" : "AWOS"} ${modeBadge}</div>
        <div class="msg-body">${renderMarkdownLite(m.content)}</div>
        ${extra}
      </div>`;
    })
    .join("");

  el.scrollTop = el.scrollHeight;
}

async function waitForStreamEnd() {
  if (!sending) return;
  stopChat();
  const deadline = Date.now() + 5000;
  while (sending && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 50));
  }
}

async function loadChat(chatId) {
  await waitForStreamEnd();
  const session = await fetchJSON(`${API}/api/chat/sessions/${chatId}`);
  currentChatId = chatId;
  emitObserverUIEvent("session_selected", { selected: true }, chatId);
  renderMessages(session.messages || []);
  updateSessionTokenBar(session);
  const sessions = await fetchJSON(`${API}/api/chat/sessions`);
  renderChatSessions(sessions, chatId);
}

function ensureStreamingBubble() {
  const preview = document.getElementById("chat-messages");
  let bubble = document.getElementById("pending-msg");
  if (!bubble) {
    preview.insertAdjacentHTML(
      "beforeend",
      `<div class="chat-msg assistant pending" id="pending-msg">
        <div class="msg-header">AWOS</div>
        <div class="stream-log" id="stream-log"></div>
        <div class="msg-body streaming" id="stream-body"></div>
      </div>`
    );
    bubble = document.getElementById("pending-msg");
  }
  return bubble;
}

function appendStreamToken(token) {
  const body = document.getElementById("stream-body");
  if (!body) return;
  body.textContent += token;
  const preview = document.getElementById("chat-messages");
  preview.scrollTop = preview.scrollHeight;
}

function setComposerRunning(running) {
  sending = running;
  const btn = document.getElementById("btn-send");
  const input = document.getElementById("chat-input");
  if (!btn) return;
  btn.textContent = running ? "Stop" : "Send";
  btn.classList.toggle("btn-stop", running);
  btn.title = running ? "Stop response" : "Send";
  btn.disabled = false;
  if (input) input.disabled = running;
}

function finishStreamUI() {
  setComposerRunning(false);
  activeAbort = null;
  stopRequested = false;
  updateLiveTokenMeta(null, "");
}

async function stopChat() {
  if (!sending) return;
  stopRequested = true;
  emitObserverUIEvent("task_control", { action: "stop" });
  const btn = document.getElementById("btn-send");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Stopping…";
  }
  if (currentChatId) {
    try {
      await fetchJSON(`${API}/api/chat/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: currentChatId }),
      });
    } catch {
      /* server may still be saving partial reply */
    }
  }
  activeAbort?.abort();
}

function buildLocalPartialReply(streamText, progressLabels) {
  const text = (streamText || "").trim();
  if (text) return text;
  if (!progressLabels.length) return "";
  return progressLabels.map((line) => `- ${line}`).join("\n");
}

function showLocalPartialMessage(content, mode) {
  const body = (content || "").trim();
  if (!body) return;
  const preview = document.getElementById("chat-messages");
  document.getElementById("pending-msg")?.remove();
  preview.insertAdjacentHTML(
    "beforeend",
    `<div class="chat-msg assistant stopped">
      <div class="msg-header">AWOS <span class="badge mode-badge">${escapeHtml(mode)}</span></div>
      <div class="msg-body">${renderMarkdownLite(body)}</div>
      <div class="msg-actions sub stopped-label">Ended early</div>
    </div>`
  );
  preview.scrollTop = preview.scrollHeight;
}

async function finalizeAfterStop(streamText, progressLabels, mode) {
  const local = buildLocalPartialReply(streamText, progressLabels);
  if (local) showLocalPartialMessage(local, mode);

  if (!currentChatId) return;

  for (let attempt = 0; attempt < 10; attempt++) {
    await new Promise((r) => setTimeout(r, 120));
    try {
      const session = await fetchJSON(`${API}/api/chat/sessions/${currentChatId}`);
      const msgs = session.messages || [];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant" && (last.content || "").trim()) {
        document.getElementById("pending-msg")?.remove();
        renderMessages(msgs);
        updateSessionTokenBar(session);
        const sessions = await fetchJSON(`${API}/api/chat/sessions`);
        renderChatSessions(sessions, currentChatId);
        return;
      }
    } catch {
      /* retry */
    }
  }
}
function appendProgress(label) {
  const log = document.getElementById("stream-log");
  if (!log || !label) return;
  const line = document.createElement("div");
  line.className = "stream-log-line";
  line.textContent = label;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

async function sendMessage() {
  if (sending) return;
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;

  activeAbort = new AbortController();
  setComposerRunning(true);

  const preview = document.getElementById("chat-messages");
  if (currentChatId) {
    const existing = preview.querySelectorAll(".chat-msg").length;
    if (existing === 0) renderMessages([]);
  }
  preview.insertAdjacentHTML(
    "beforeend",
    `<div class="chat-msg user"><div class="msg-header">You <span class="badge mode-badge">${escapeHtml(currentMode)}</span></div><div class="msg-body">${renderMarkdownLite(message)}</div></div>`
  );
  ensureStreamingBubble();
  updateLiveTokenMeta(null, "");
  preview.scrollTop = preview.scrollHeight;
  input.value = "";

  let streamText = "";
  let streamProgress = [];
  let lastUsage = null;
  let result = null;

  try {
    result = await postSSE(
      `${API}/api/chat/stream`,
      { message, mode: currentMode, chat_id: currentChatId },
      {
        onStart(data) {
          if (data.chat_id) currentChatId = data.chat_id;
          emitObserverUIEvent(
            "chat_submitted",
            { mode: currentMode, has_message: true, message_chars: message.length },
            data.chat_id || currentChatId,
            data.trace_id || null
          );
        },
        onStatus(data) {
          if (data.message) appendProgress(data.message);
        },
        onProgress(data) {
          if (data.label) {
            streamProgress.push(data.label);
            appendProgress(data.label);
          }
        },
        onUsage(data) {
          lastUsage = data;
          updateLiveTokenMeta(data, streamText);
        },
        onToken(token) {
          streamText += token;
          appendStreamToken(token);
          if (!lastUsage) updateLiveTokenMeta(null, streamText);
        },
      },
      { signal: activeAbort.signal }
    );

    if (!result && streamText) {
      result = {
        chat_id: currentChatId,
        session: {
          messages: [
            { role: "user", content: message, mode: currentMode },
            { role: "assistant", content: streamText, mode: currentMode, meta: {} },
          ],
        },
      };
    }
    document.getElementById("pending-msg")?.remove();
    const session = result?.session || { messages: [] };
    renderMessages(session.messages || []);
    updateSessionTokenBar(session);

    const sessions = await fetchJSON(`${API}/api/chat/sessions`);
    renderChatSessions(sessions, currentChatId);

    const meta = result?.meta || lastUsage || {};
    const navParts = [];
    const tok = formatTokenMeta(meta);
    if (tok) navParts.push(tok);
    if (meta.memory_turns > 0) navParts.push(`${meta.memory_turns} turns memory`);
    if (session.token_stats?.total_tokens) {
      navParts.push(`session ${formatTokens(session.token_stats.total_tokens)} tok`);
    }
    if (currentMode === "agent" && meta.run_session_id) {
      navParts.unshift(`run ${meta.run_session_id}`);
    }
    if (navParts.length) {
      document.getElementById("nav-meta").textContent = navParts.join(" · ");
    }
  } catch (err) {
    if (err.name === "AbortError" || stopRequested || result?.cancelled) {
      await finalizeAfterStop(streamText, streamProgress, currentMode);
    } else {
      document.getElementById("pending-msg")?.remove();
      preview.insertAdjacentHTML(
        "beforeend",
        `<div class="chat-msg assistant error"><div class="msg-header">Error</div><div class="msg-body">${escapeHtml(err.message)}</div></div>`
      );
    }
  } finally {
    finishStreamUI();
  }
}

function newChat() {
  if (sending) {
    stopChat();
  }
  currentChatId = null;
  renderMessages([]);
  updateSessionTokenBar(null);
  updateLiveTokenMeta(null, "");
  document.getElementById("nav-meta").textContent = "";
  document.getElementById("chat-input").focus();
  fetchJSON(`${API}/api/chat/sessions`)
    .then((s) => renderChatSessions(s, null))
    .catch(() => {});
}

async function deleteChat(chatId) {
  if (!chatId) return;
  if (!confirm("Delete this chat? This cannot be undone.")) return;

  try {
    await fetchJSON(`${API}/api/chat/sessions/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId }),
    });
    if (currentChatId === chatId) newChat();
    const sessions = await fetchJSON(`${API}/api/chat/sessions`);
    renderChatSessions(sessions, currentChatId);
  } catch (err) {
    alert(err.message || "Failed to delete chat");
  }
}

async function clearAllChats() {
  const sessions = await fetchJSON(`${API}/api/chat/sessions`).catch(() => []);
  if (!sessions.length) return;
  if (!confirm(`Delete all ${sessions.length} chat(s)? This cannot be undone.`)) return;

  try {
    await fetchJSON(`${API}/api/chat/sessions/clear`, { method: "POST" });
    newChat();
    renderChatSessions([], null);
  } catch (err) {
    alert(err.message || "Failed to clear chats");
  }
}

async function initChat() {
  emitObserverUIEvent("app_opened", { view: "chat" }, null, null);
  try {
    await loadModes();
  } catch (err) {
    console.error("loadModes failed:", err);
  }
  updatePlaceholder();

  document.getElementById("btn-send").addEventListener("click", () => {
    if (sending) stopChat();
    else sendMessage();
  });
  document.getElementById("btn-new-chat").addEventListener("click", newChat);
  document.getElementById("btn-clear-chats")?.addEventListener("click", clearAllChats);
  document.getElementById("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (sending) stopChat();
      else sendMessage();
    }
  });

  const sessions = await fetchJSON(`${API}/api/chat/sessions`);
  renderChatSessions(sessions, null);

  const params = new URLSearchParams(location.search);
  const chatId = params.get("chat");
  if (chatId) await loadChat(chatId);
}

initChat().catch((err) => {
  console.error(err);
});
