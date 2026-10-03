#!/usr/bin/env node
/**
 * AWOS development supervisor.
 *
 * This process owns the Electron child in development mode. It deliberately
 * watches a fixed source list rather than accepting paths or commands from
 * callers.
 */

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const AWOS_APP_ROOT = __dirname;
const REPO_ROOT = path.resolve(AWOS_APP_ROOT, '..');
const RESTART_DEBOUNCE_MS = 250;

const WATCH_FILES = Object.freeze([
  path.join(AWOS_APP_ROOT, 'main.js'),
  path.join(AWOS_APP_ROOT, 'preload.js'),
  path.join(AWOS_APP_ROOT, 'package.json'),
  path.join(REPO_ROOT, 'gui', 'server.py'),
  path.join(REPO_ROOT, 'scaffold', 'agent', 'observer_protocol.py'),
  path.join(REPO_ROOT, 'scaffold', 'agent', 'gui_events.py'),
]);

function electronExecutable() {
  // The local Electron package exposes the installed executable path when it
  // is required from a normal Node process. No shell command is involved.
  return require('electron');
}

function existingWatchFiles(files = WATCH_FILES, exists = fs.existsSync) {
  return files.filter((file) => exists(file));
}

function createSupervisor({
  spawnProcess = spawn,
  terminateProcess = (processHandle, signal) => {
    if (process.platform !== 'win32') {
      try {
        process.kill(-processHandle.pid, signal);
        return;
      } catch (_) {
        // Fall back to the direct child if the process group already exited.
      }
    }
    processHandle.kill(signal);
  },
  watchFile = (file, listener) => fs.watch(file, listener),
  exists = fs.existsSync,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  log = console,
  debounceMs = RESTART_DEBOUNCE_MS,
  electronPath = electronExecutable,
  appRoot = AWOS_APP_ROOT,
  watchFiles = WATCH_FILES,
} = {}) {
  let child = null;
  let stopping = false;
  let restarting = false;
  let restartTimer = null;
  let forceKillTimer = null;
  let restartQueued = false;
  const watchers = [];

  function startChild() {
    if (stopping) return null;
    const executable = typeof electronPath === 'function' ? electronPath() : electronPath;
    child = spawnProcess(executable, ['.'], {
      cwd: appRoot,
      stdio: 'inherit',
      env: process.env,
      detached: true,
    });
    child.once('error', (error) => {
      log.error(`[dev] Electron failed to start: ${error.message}`);
    });
    child.once('close', (code, signal) => {
      if (forceKillTimer !== null) {
        clearTimer(forceKillTimer);
        forceKillTimer = null;
      }
      const wasRestart = restarting;
      child = null;
      if (stopping) return;
      if (wasRestart) {
        restarting = false;
        if (restartQueued) {
          restartQueued = false;
          scheduleRestart();
        } else {
          startChild();
        }
        return;
      }
      log.error(`[dev] Electron exited (code=${code}, signal=${signal || 'none'}); stopping supervisor`);
      stop();
    });
    log.log(`[dev] Electron started (pid=${child.pid})`);
    return child;
  }

  function scheduleRestart() {
    if (stopping) return;
    if (restarting) {
      restartQueued = true;
      return;
    }
    // Coalesce all events in the debounce window into one restart.
    if (restartTimer !== null) return;
    restartTimer = setTimer(() => {
      restartTimer = null;
      restartChild();
    }, debounceMs);
  }

  function restartChild() {
    if (stopping) return;
    if (!child) {
      startChild();
      return;
    }
    restarting = true;
    const childToRestart = child;
    log.log('[dev] Source change detected; restarting Electron');
    terminateProcess(childToRestart, 'SIGTERM');
    // Electron may hide to tray instead of exiting on SIGTERM. Since this
    // process owns the development child, use a bounded escalation so a
    // stale renderer cannot block the new source from becoming live.
    forceKillTimer = setTimer(() => {
      forceKillTimer = null;
      if (child === childToRestart && restarting) {
        log.warn('[dev] Electron did not exit after SIGTERM; forcing shutdown');
        terminateProcess(childToRestart, 'SIGKILL');
      }
    }, 2000);
  }

  function watchSources() {
    const files = existingWatchFiles(watchFiles, exists);
    for (const file of files) {
      try {
        const watcher = watchFile(file, () => scheduleRestart());
        watchers.push(watcher);
      } catch (error) {
        log.error(`[dev] Could not watch ${path.relative(REPO_ROOT, file)}: ${error.message}`);
      }
    }
    const missing = watchFiles.filter((file) => !files.includes(file));
    if (missing.length > 0) {
      log.warn(`[dev] Skipping ${missing.length} missing watch path(s)`);
    }
  }

  function stop() {
    if (stopping) return;
    stopping = true;
    if (restartTimer !== null) {
      clearTimer(restartTimer);
      restartTimer = null;
    }
    if (forceKillTimer !== null) {
      clearTimer(forceKillTimer);
      forceKillTimer = null;
    }
    for (const watcher of watchers.splice(0)) watcher.close();
    if (child) {
      const currentChild = child;
      child = null;
      restarting = false;
      terminateProcess(currentChild, 'SIGTERM');
      setTimer(() => terminateProcess(currentChild, 'SIGKILL'), 2000);
    }
  }

  function start() {
    watchSources();
    startChild();
    return { stop };
  }

  return {
    start,
    stop,
    scheduleRestart,
    get child() { return child; },
    get stopping() { return stopping; },
    watchFiles: existingWatchFiles(watchFiles, exists),
  };
}

if (require.main === module) {
  const supervisor = createSupervisor();
  supervisor.start();
  const shutdown = () => supervisor.stop();
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

module.exports = {
  AWOS_APP_ROOT,
  REPO_ROOT,
  WATCH_FILES,
  RESTART_DEBOUNCE_MS,
  existingWatchFiles,
  createSupervisor,
};
