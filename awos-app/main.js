#!/usr/bin/env node
/**
 * AWOS — Electron Main Process
 *
 * Native desktop agent: system tray, global command palette (Cmd+K),
 * minimize-to-tray, native notifications, background mode.
 */

const {
  app, BrowserWindow, Menu, Tray, dialog, shell,
  ipcMain, nativeImage, globalShortcut, Notification,
} = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const http = require('http');

// ── Config ────────────────────────────────────────────────────────────────
const PORT = 8765;
const CONTROL_PORT = Number(process.env.AWOS_CONTROL_PORT || 8766);
const isPackaged = app.isPackaged;
const ROOT = isPackaged ? path.join(process.resourcesPath) : path.resolve(__dirname, '..');
const CONTROL_TOKEN_PATH = process.env.AWOS_CONTROL_TOKEN || path.join(ROOT, '.awos', 'observer_token');
const AGENT_URL = `http://127.0.0.1:${PORT}/agent`;
const isMac = process.platform === 'darwin';

let mainWindow = null;
let tray = null;
let pythonServer = null;
let isQuitting = false;
let agentStatus = 'idle';  // idle | running | done
let controlServer = null;

// ── Tray icon (generated programmatically) ─────────────────────────────────
function createTrayIcon(color = '#86868b') {
  // 16x16 semi-transparent circle with lightning bolt
  const size = 16;
  const canvas = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;
      const cx = x - 7.5, cy = y - 7.5;
      const r = Math.sqrt(cx * cx + cy * cy);
      if (r <= 7) {
        // Filled circle
        const hex = color.replace('#', '');
        canvas[i] = parseInt(hex.substring(0, 2), 16);
        canvas[i + 1] = parseInt(hex.substring(2, 4), 16);
        canvas[i + 2] = parseInt(hex.substring(4, 6), 16);
        canvas[i + 3] = 255;
      } else {
        canvas[i] = canvas[i + 1] = canvas[i + 2] = canvas[i + 3] = 0;
      }
    }
  }
  return nativeImage.createFromBuffer(canvas, { width: size, height: size });
}

function updateTrayStatus(status) {
  agentStatus = status;
  if (!tray) return;
  // Use tray icon file instead of generated image
  const iconPath = path.join(__dirname, 'icons', 'tray-icon.png');
  try {
    tray.setImage(nativeImage.createFromPath(iconPath));
  } catch(_) {}
  tray.setToolTip(`AWOS — ${status === 'running' ? 'Working...' : status === 'done' ? 'Task complete' : 'Idle'}`);
  rebuildTrayMenu();
}

// ── Build tray ────────────────────────────────────────────────────────────
function buildTray() {
  const trayIconPath = path.join(__dirname, 'icons', 'tray-icon.png');
  tray = new Tray(trayIconPath);
  tray.setToolTip('AWOS — Idle');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show AWOS',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
    { type: 'separator' },
    {
      label: 'Status: Idle',
      enabled: false,
      id: 'status-item',
    },
    { type: 'separator' },
    {
      label: 'Quit AWOS',
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);

  // Click tray → show/hide window
  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) {
        mainWindow.hide();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    }
  });
}

// Rebuild tray menu when status changes (Electron has no getContextMenu)
function rebuildTrayMenu() {
  if (!tray || tray.isDestroyed()) return;
  const labels = { idle: 'Status: Idle', running: 'Status: Working...', done: 'Status: Task Complete' };
  const menu = Menu.buildFromTemplate([
    {
      label: 'Show AWOS',
      click: () => {
        if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
      },
    },
    { type: 'separator' },
    {
      label: labels[agentStatus] || 'Status: Idle',
      enabled: false,
    },
    { type: 'separator' },
    {
      label: 'Quit AWOS',
      click: () => { isQuitting = true; app.quit(); },
    },
  ]);
  tray.setContextMenu(menu);
}

// ── Global command palette shortcut ────────────────────────────────────────
function registerGlobalShortcuts() {
  // Cmd+K / Ctrl+K → toggle command palette
  globalShortcut.register('CmdOrCtrl+K', () => {
    if (mainWindow) {
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send('command-palette');
    }
  });

  // Cmd+Shift+K → quick chat
  globalShortcut.register('CmdOrCtrl+Shift+K', () => {
    if (mainWindow) {
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send('quick-chat');
    }
  });
}

// ── Utility: wait for Python server ───────────────────────────────────────
function waitForServer(url, maxRetries = 30, interval = 200) {
  return new Promise((resolve, reject) => {
    let retries = 0;
    const check = () => {
      http.get(url, (res) => {
        if (res.statusCode === 200) resolve();
        else if (++retries < maxRetries) setTimeout(check, interval);
        else reject(new Error('Server returned non-200'));
      }).on('error', () => {
        if (++retries < maxRetries) setTimeout(check, interval);
        else reject(new Error('Server did not start'));
      });
    };
    check();
  });
}

// ── Start Python server with port fallback ────────────────────────────────
function startPythonServer() {
  const serverPath = path.join(ROOT, 'gui', 'server.py');
  let port = PORT;
  const maxTries = 10;

  function tryPort(currentPort) {
    pythonServer = spawn('python3', [serverPath, '--port', String(currentPort)], {
      cwd: ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    pythonServer.stdout.on('data', (data) => {
      console.log(`[server] ${data.toString().trim()}`);
    });

    pythonServer.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      console.error(`[server] ${msg}`);
      // If port in use, kill and retry
      if (msg.includes('Address already in use') && currentPort - PORT < maxTries) {
        pythonServer.kill();
        const nextPort = currentPort + 1;
        console.log(`[server] Port ${currentPort} busy, trying ${nextPort}...`);
        tryPort(nextPort);
      }
    });

    pythonServer.on('close', (code) => {
      if (code !== 0 && currentPort - PORT < maxTries) {
        // Retry on unexpected exit
        const nextPort = currentPort + 1;
        console.log(`[server] Restarting on port ${nextPort}...`);
        tryPort(nextPort);
      } else {
        pythonServer = null;
      }
    });
  }

  tryPort(port);
  return pythonServer;
}

// ── Local control bridge ───────────────────────────────────────────────────
// This controls the real BrowserWindow from Electron's main process. It is
// deliberately localhost-only, token-protected, and limited to safe actions.
function emitControlObserverEvent(type, payload) {
  return new Promise((resolve) => {
    const body = JSON.stringify({ type, payload });
    const request = http.request({
      hostname: '127.0.0.1',
      port: PORT,
      path: '/api/observer/ui-event',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: 1500,
    });
    request.on('response', (response) => {
      response.resume();
      response.on('end', resolve);
    });
    request.on('error', resolve);
    request.on('timeout', () => request.destroy());
    request.write(body);
    request.end();
  });
}

function readControlToken() {
  try {
    const token = JSON.parse(fs.readFileSync(CONTROL_TOKEN_PATH, 'utf8'));
    if (!token.token || !token.expires_at) return null;
    if (Date.now() >= Date.parse(token.expires_at)) return null;
    return token.token;
  } catch (_) {
    return null;
  }
}

function appendControlAudit(entry) {
  try {
    const dir = path.join(ROOT, '.awos', 'control_log');
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, 'control_events.jsonl'), `${JSON.stringify(entry)}\\n`);
  } catch (error) {
    console.error('[control] audit failed:', error.message);
  }
}

function controlStatus() {
  return {
    window_exists: Boolean(mainWindow && !mainWindow.isDestroyed()),
    visible: Boolean(mainWindow && mainWindow.isVisible()),
    focused: Boolean(mainWindow && mainWindow.isFocused()),
    loading: Boolean(mainWindow && mainWindow.webContents.isLoading()),
    url: mainWindow ? mainWindow.webContents.getURL() : null,
    agent_status: agentStatus,
  };
}

function startControlServer() {
  if (controlServer) return;
  controlServer = http.createServer((req, res) => {
    if (req.method !== 'POST' || req.url !== '/api/control') {
      res.writeHead(req.url === '/health' ? 200 : 404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(req.url === '/health' ? { status: 'ok' } : { error: 'not_found' }));
      return;
    }

    const expected = readControlToken();
    const supplied = req.headers['x-awos-control-token'];
    const expectedBytes = expected ? Buffer.from(String(expected)) : null;
    const suppliedBytes = supplied ? Buffer.from(String(supplied)) : null;
    const authorized = Boolean(
      expectedBytes && suppliedBytes &&
      expectedBytes.length === suppliedBytes.length &&
      crypto.timingSafeEqual(expectedBytes, suppliedBytes),
    );
    const requestId = `ctl_${crypto.randomUUID().replaceAll('-', '')}`;
    if (!authorized) {
      appendControlAudit({ request_id: requestId, action: 'unauthorized', result: 'denied', timestamp: new Date().toISOString() });
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'error', request_id: requestId, error: 'forbidden' }));
      return;
    }

    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 16 * 1024) req.destroy();
    });
    req.on('error', () => {});
    req.on('end', async () => {
      const started = Date.now();
      let payload;
      try {
        payload = JSON.parse(body || '{}');
      } catch (_) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'error', request_id: requestId, error: 'invalid_json' }));
        return;
      }

      const action = String(payload.action || '').trim();
      const audit = { request_id: requestId, action, result: 'rejected', timestamp: new Date().toISOString() };
      await emitControlObserverEvent('control_requested', {
        request_id: requestId,
        action,
        target: typeof payload.target === 'string' ? payload.target : undefined,
      });
      let result;
      try {
        if (action === 'status') {
          result = controlStatus();
        } else if (action === 'show' || action === 'focus') {
          if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_unavailable');
          mainWindow.show();
          mainWindow.focus();
          result = controlStatus();
        } else if (action === 'hide') {
          if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_unavailable');
          mainWindow.hide();
          result = controlStatus();
        } else if (action === 'reload') {
          if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_unavailable');
          mainWindow.webContents.reload();
          result = { accepted: true, state: controlStatus() };
        } else if (action === 'navigate') {
          const route = String(payload.target || '');
          const allowedRoutes = new Set(['/agent', '/renderer']);
          if (!allowedRoutes.has(route)) throw new Error('route_not_allowed');
          if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_unavailable');
          mainWindow.loadURL(`http://127.0.0.1:${PORT}${route}`);
          result = { accepted: true, route };
        } else if (action === 'click') {
          const target = String(payload.target || '');
          const clickScripts = {
            prompt_submit: "document.getElementById('prompt-btn')?.click()",
            prompt_focus: "document.getElementById('prompt-input')?.focus()",
            command_palette: "document.querySelector('#titlebar .tb-btn')?.click()",
          };
          if (!clickScripts[target]) throw new Error('target_not_allowed');
          if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_unavailable');
          await mainWindow.webContents.executeJavaScript(clickScripts[target]);
          result = { accepted: true, target };
        } else if (action === 'fill') {
          const target = String(payload.target || '');
          const value = String(payload.value || '');
          if (target !== 'prompt') throw new Error('target_not_allowed');
          if (value.length > 4000) throw new Error('value_too_large');
          if (!mainWindow || mainWindow.isDestroyed()) throw new Error('window_unavailable');
          await mainWindow.webContents.executeJavaScript(
            `(() => { const el = document.getElementById('prompt-input'); if (!el) throw new Error('target_missing'); el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('input', {bubbles:true})); el.focus(); })()`,
          );
          result = { accepted: true, target, value_chars: value.length };
        } else {
          throw new Error('unsupported_action');
        }
        audit.result = 'ok';
        audit.duration_ms = Date.now() - started;
        appendControlAudit(audit);
        await emitControlObserverEvent('control_completed', {
          request_id: requestId,
          action,
          duration_ms: audit.duration_ms,
          result: action === 'fill' ? { value_chars: result.value_chars } : { accepted: true },
        });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', request_id: requestId, action, result }));
      } catch (error) {
        audit.error = error.message;
        audit.duration_ms = Date.now() - started;
        appendControlAudit(audit);
        await emitControlObserverEvent('control_rejected', {
          request_id: requestId,
          action,
          error: error.message,
          duration_ms: audit.duration_ms,
        });
        res.writeHead(error.message === 'unsupported_action' ? 400 : 409, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'error', request_id: requestId, action, error: error.message }));
      }
    });
  });
  controlServer.on('error', (error) => console.error('[control] server error:', error.message));
  controlServer.listen(CONTROL_PORT, '127.0.0.1', () => {
    console.log(`[control] listening on 127.0.0.1:${CONTROL_PORT}`);
  });
}

function stopControlServer() {
  if (!controlServer) return;
  controlServer.close();
  controlServer = null;
}

// ── Create the main window ────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'AWOS',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 12, y: 12 },
    backgroundColor: '#1a1a1e',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  mainWindow.loadURL(AGENT_URL);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Open external links in browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Minimize to tray instead of closing
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      // Keep working in background
      console.log('[tray] Window hidden to tray — agent still running');
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── Application menu ──────────────────────────────────────────────────────
function buildMenu() {
  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),

    {
      label: 'Agent',
      submenu: [
        {
          label: 'Command Palette',
          accelerator: 'CmdOrCtrl+K',
          click: () => {
            if (mainWindow) {
              mainWindow.show();
              mainWindow.focus();
              mainWindow.webContents.send('command-palette');
            }
          },
        },
        {
          label: 'New Chat',
          accelerator: 'CmdOrCtrl+Shift+K',
          click: () => {
            if (mainWindow) {
              mainWindow.show();
              mainWindow.focus();
              mainWindow.webContents.send('quick-chat');
            }
          },
        },
        { type: 'separator' },
        {
          label: 'New Agent Task',
          accelerator: 'CmdOrCtrl+N',
          click: () => {
            if (mainWindow) {
              mainWindow.show();
              mainWindow.focus();
              mainWindow.webContents.send('new-task');
            }
          },
        },
      ],
    },

    { role: 'editMenu' },

    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },

    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(isMac ? [
          { type: 'separator' },
          { role: 'front' },
        ] : [{ role: 'close' }]),
      ],
    },

    {
      label: 'Help',
      submenu: [
        {
          label: 'About AWOS',
          click: () => {
            dialog.showMessageBox({
              type: 'info',
              title: 'About AWOS',
              message: 'AWOS — Self-Improving Coding Agent',
              detail: `Version ${app.getVersion()}\n\nA self-improving coding agent built on a learnable OS. It shows every decision, learns from every outcome, and compounds repo-specific intelligence.\nCmd+K anywhere to summon the agent.`,
            });
          },
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── IPC Handlers ──────────────────────────────────────────────────────────

// Dock bounce (macOS)
ipcMain.on('dock-bounce', () => {
  if (isMac && app.dock) app.dock.bounce('informational');
});

// Taskbar progress
ipcMain.on('set-progress', (_event, progress) => {
  if (mainWindow) mainWindow.setProgressBar(progress);
});

// Update tray status from renderer
ipcMain.on('agent-status', (_event, status) => {
  updateTrayStatus(status);
});

// Native notification
ipcMain.on('send-notification', (_event, { title, body }) => {
  if (Notification.isSupported()) {
    const notif = new Notification({ title, body, silent: false });
    notif.on('click', () => {
      if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
    notif.show();
  }
});

// Show window
ipcMain.on('show-window', () => {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  }
});

// ── App lifecycle ─────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  buildMenu();
  buildTray();
  registerGlobalShortcuts();

  // Prevent app from quitting when all windows are closed
  app.on('window-all-closed', (event) => {
    // Don't quit — keep running in tray
    event.preventDefault();
  });

  console.log('Starting AWOS server...');
  startPythonServer();

  try {
    console.log(`Waiting for server at ${AGENT_URL}...`);
    await waitForServer(AGENT_URL);
    console.log('Server ready.');
  } catch (err) {
    console.error('Failed to start server:', err.message);
    dialog.showErrorBox('Server Error', `Could not start AWOS server:\n${err.message}`);
    app.quit();
    return;
  }

  createWindow();
  startControlServer();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
});

app.on('will-quit', () => {
  isQuitting = true;
  globalShortcut.unregisterAll();
  stopControlServer();
  if (pythonServer) {
    pythonServer.kill('SIGTERM');
    pythonServer = null;
  }
});
