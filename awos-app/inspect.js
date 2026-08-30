#!/usr/bin/env node
/**
 * AWOS Visual Inspector — extracts DOM state, layout, colors, and text
 * from the running Agent Console so the AI can "see" what's rendered.
 *
 * Usage: npx electron inspect.js [--wait 3]
 */
const { app, BrowserWindow } = require('electron');
const fs = require('fs');

const WAIT = parseInt(process.argv[process.argv.indexOf('--wait') + 1]) || 3;

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 800,
    show: false,
    backgroundColor: '#1a1a1e',
    webPreferences: { nodeIntegration: true, contextIsolation: false }
  });

  await win.loadURL('http://127.0.0.1:8765/agent');
  await new Promise(r => setTimeout(r, WAIT * 1000));

  // ── Extract visual state from the DOM ────────────────────────────────
  const state = await win.webContents.executeJavaScript(`
    (function() {
      function getStyle(el, prop) {
        try { return window.getComputedStyle(el).getPropertyValue(prop); } catch(_) { return ''; }
      }
      function rectDesc(el) {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 ? 
          { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) } : null;
      }

      const report = {};

      // Titlebar
      const tb = document.getElementById('titlebar');
      report.titlebar = {
        visible: !!tb && tb.offsetParent !== null,
        rect: rectDesc(tb),
        text: tb ? tb.innerText.trim().split('\\n').filter(Boolean) : [],
        bg: getStyle(tb, 'background-color'),
      };

      // Sidebar stats
      const statTasks = document.getElementById('stat-tasks');
      const statModels = document.getElementById('stat-models');
      const statCost = document.getElementById('stat-cost');
      report.sidebar = {
        stats: {
          tasks: statTasks ? statTasks.textContent : 'missing',
          models: statModels ? statModels.textContent : 'missing',
          cost: statCost ? statCost.textContent : 'missing',
        }
      };

      // History
      const historyList = document.getElementById('history-list');
      const historyItems = historyList ? historyList.querySelectorAll('.history-item') : [];
      report.sidebar.history = Array.from(historyItems).slice(0, 5).map(item => ({
        goal: (item.querySelector('.goal') || {}).textContent || '',
        status: (item.querySelector('.status-badge') || {}).textContent || '',
        meta: (item.querySelector('.meta') || {}).textContent || '',
      }));

      // Session header
      report.session = {
        title: (document.getElementById('session-title') || {}).textContent || '',
        subtitle: (document.getElementById('session-subtitle') || {}).textContent || '',
      };

      // Progress
      const progFill = document.getElementById('progress-fill');
      const progLabel = document.getElementById('progress-label');
      report.progress = {
        width: progFill ? progFill.style.width : '0%',
        label: progLabel ? progLabel.textContent : '',
      };

      // Reasoning sheet
      const reasoningSheet = document.getElementById('reasoning-sheet');
      const thoughtCount = document.getElementById('thought-count');
      const reasoningBody = document.getElementById('reasoning-body');
      report.reasoning = {
        visible: reasoningSheet ? !reasoningSheet.classList.contains('collapsed') : false,
        thought_count: thoughtCount ? thoughtCount.textContent : '0',
        entries: reasoningBody ? 
          Array.from(reasoningBody.querySelectorAll('.thought-entry')).map(e => e.textContent.trim().slice(0, 100)) : [],
        empty_text: reasoningBody && reasoningBody.querySelector('.reasoning-empty') ? 
          reasoningBody.querySelector('.reasoning-empty').textContent : null,
      };

      // Event stream
      const stream = document.getElementById('stream');
      const cards = stream ? stream.querySelectorAll('.event-card') : [];
      report.stream = {
        card_count: cards.length,
        cards: Array.from(cards).slice(-8).map(card => ({
          type: Array.from(card.classList).find(c => c.startsWith('type-')) || '',
          time: (card.querySelector('.event-time') || {}).textContent || '',
          body: (card.querySelector('.event-body') || {}).textContent?.trim().slice(0, 150) || '',
        })),
        empty_state: stream && stream.querySelector('#empty-state') ? true : false,
      };

      // Command bar
      const cmdInput = document.getElementById('command-input');
      const cmdBtn = document.getElementById('command-run');
      report.command = {
        input_value: cmdInput ? cmdInput.value : '',
        input_disabled: cmdInput ? cmdInput.disabled : false,
        input_placeholder: cmdInput ? cmdInput.placeholder : '',
        btn_text: cmdBtn ? cmdBtn.textContent.trim() : '',
        btn_disabled: cmdBtn ? cmdBtn.disabled : false,
      };

      // JS state variables (if they exist)
      report.js_state = {
        liveSessionId: typeof liveSessionId !== 'undefined' ? liveSessionId : 'undefined',
        tasksTotal: typeof tasksTotal !== 'undefined' ? tasksTotal : 'undefined',
        tasksDone: typeof tasksDone !== 'undefined' ? tasksDone : 'undefined',
        sessionCost: typeof sessionCost !== 'undefined' ? sessionCost : 'undefined',
        thinkingCount: typeof thinkingCount !== 'undefined' ? thinkingCount : 'undefined',
        usedModels: typeof usedModels !== 'undefined' ? (usedModels ? Array.from(usedModels) : []) : 'undefined',
        running: typeof running !== 'undefined' ? running : 'undefined',
      };

      // SSE connection
      report.sse = {
        eventSource: typeof eventSource !== 'undefined' ? (eventSource ? 'connected' : 'disconnected') : 'undefined',
      };

      // Palette state
      const palette = document.getElementById('palette-overlay');
      report.palette = {
        visible: palette ? palette.style.display !== 'none' : false,
        recent_count: typeof getRecentCmds !== 'undefined' ? getRecentCmds().length : 'undefined',
      };

      // Titlebar status
      report.titlebar_status = {
        status_dot: (document.getElementById('titlebar-status') || {}).className || '',
      };

      return report;
    })();
  `);

  // ── Output ─────────────────────────────────────────────────────────────
  console.log(JSON.stringify(state, null, 2));

  // Also save to file
  fs.writeFileSync('/tmp/awos-state.json', JSON.stringify(state, null, 2));
  console.log('\nSaved: /tmp/awos-state.json');

  app.quit();
});
