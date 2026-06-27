const API = "";

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `${res.status} ${url}`);
  }
  return res.json();
}

/** POST + read Server-Sent Events (event/data lines). */
async function postSSE(url, body, handlers = {}, options = {}) {
  const { signal } = options;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `${res.status} ${url}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  let result = null;

  function processLine(line) {
    if (line.startsWith("event:")) {
      currentEvent = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      const raw = line.slice(5).trim();
      let data = {};
      try {
        data = JSON.parse(raw);
      } catch {
        data = { content: raw };
      }
      if (currentEvent === "token" && handlers.onToken) handlers.onToken(data.content || "");
      else if (currentEvent === "usage" && handlers.onUsage) handlers.onUsage(data);
      else if (currentEvent === "status" && handlers.onStatus) handlers.onStatus(data);
      else if (currentEvent === "progress" && handlers.onProgress) handlers.onProgress(data);
      else if (currentEvent === "start" && handlers.onStart) handlers.onStart(data);
      else if (currentEvent === "done" || currentEvent === "cancelled") result = data;
      else if (currentEvent === "error") throw new Error(data.error || "stream error");
    } else if (line === "") {
      currentEvent = "message";
    }
  }

  try {
    while (true) {
      if (signal?.aborted) {
        const err = new Error("Stopped by user");
        err.name = "AbortError";
        throw err;
      }
      const { done, value } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";
        for (const line of parts) processLine(line);
      }
      if (done) {
        buffer += decoder.decode();
        for (const line of buffer.split("\n")) {
          if (line.trim()) processLine(line);
        }
        break;
      }
    }
  } catch (err) {
    if (signal?.aborted || err.name === "AbortError") {
      const abortErr = new Error("Stopped by user");
      abortErr.name = "AbortError";
      throw abortErr;
    }
    throw err;
  } finally {
    reader.cancel().catch(() => {});
  }
  return result;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

function formatTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

function formatTokenMeta(meta) {
  if (!meta) return "";
  if (meta.cache_hit) {
    const saved = meta.tokens_saved_est || 0;
    return saved ? `cached · ~${formatTokens(saved)} saved` : "cached · 0 tokens";
  }

  const parts = [];

  // Speed
  const lat = meta.latency_sec || (meta.total_ms ? meta.total_ms / 1000 : 0);
  if (lat > 0) {
    let speed = `${lat.toFixed(1)}s`;
    if (meta.ttfb_ms > 0) speed += ` (ttfb ${Math.round(meta.ttfb_ms)}ms)`;
    if (meta.gen_tokens_per_sec > 0) speed += ` · ${meta.gen_tokens_per_sec.toFixed(0)} tok/s`;
    parts.push(speed);
  }

  // Tokens — show context vs output (the tricky split)
  const outTok = meta.completion_tokens || 0;
  const ctxTok = meta.context_tokens_est || 0;
  const inTok = meta.prompt_tokens || 0;
  if (ctxTok || outTok || inTok) {
    const tokParts = [];
    if (ctxTok) tokParts.push(`${formatTokens(ctxTok)} ctx`);
    if (outTok) tokParts.push(`${formatTokens(outTok)} out`);
    else if (inTok) tokParts.push(`${formatTokens(inTok)} in`);
    parts.push(tokParts.join(" · "));
  } else if (meta.total_tokens) {
    parts.push(`${formatTokens(meta.total_tokens)} tokens`);
  }

  // Cost (derived from tokens)
  if (meta.cost_usd > 0) {
    let cost = `$${meta.cost_usd.toFixed(4)}`;
    if (meta.savings_vs_sonnet_pct > 0) {
      cost += ` (${Math.round(meta.savings_vs_sonnet_pct)}% vs Sonnet)`;
    }
    parts.push(cost);
  }

  return parts.join(" · ");
}

function renderMarkdownLite(text) {
  let html = escapeHtml(text);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" class="inline-link">$1</a>'
  );
  html = html.replace(/\n/g, "<br>");
  return html;
}

function getView() {
  return new URLSearchParams(location.search).get("view") || "chat";
}

function setView(view) {
  const params = new URLSearchParams(location.search);
  params.set("view", view);
  history.replaceState({}, "", `?${params}`);
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === `view-${view}`);
  });
  document.querySelectorAll(".tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
  });
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    setView(tab.dataset.view);
    if (tab.dataset.view === "runs" && window.initRuns) window.initRuns();
  });
});

setView(getView());
