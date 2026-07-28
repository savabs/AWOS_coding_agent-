/**
 * AWOS — Electron Preload Script
 *
 * Secure bridge: command palette, notifications, tray status, dock integration.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('awos', {
  // ── Dock & Taskbar ──────────────────────────────────────────────────
  dockBounce: () => ipcRenderer.send('dock-bounce'),
  setProgress: (progress) => ipcRenderer.send('set-progress', progress),

  // ── Tray Status ────────────────────────────────────────────────────
  setAgentStatus: (status) => ipcRenderer.send('agent-status', status),
  // status: 'idle' | 'running' | 'done'

  // ── Notifications ──────────────────────────────────────────────────
  notify: (title, body) => ipcRenderer.send('send-notification', { title, body }),

  // ── Command Palette — listen for Cmd+K ─────────────────────────────
  onCommandPalette: (callback) => {
    ipcRenderer.on('command-palette', () => callback());
  },
  onQuickChat: (callback) => {
    ipcRenderer.on('quick-chat', () => callback());
  },
  onNewTask: (callback) => {
    ipcRenderer.on('new-task', () => callback());
  },

  // ── Window Control ─────────────────────────────────────────────────
  showWindow: () => ipcRenderer.send('show-window'),

  // ── Platform ───────────────────────────────────────────────────────
  platform: process.platform,
  isMac: process.platform === 'darwin',
});
