const assert = require('node:assert/strict');
const test = require('node:test');
const { EventEmitter } = require('node:events');

const { createSupervisor, RESTART_DEBOUNCE_MS } = require('./dev-runner');

function makeChild(pid) {
  const child = new EventEmitter();
  child.pid = pid;
  child.killCalls = [];
  child.kill = (signal) => {
    child.killCalls.push(signal);
    queueMicrotask(() => child.emit('close', 0, signal));
    return true;
  };
  return child;
}

function makeHarness() {
  const files = ['/app/main.js', '/app/preload.js'];
  const watchers = [];
  const timers = [];
  const children = [];
  const spawnOptions = [];
  const logs = [];
  const supervisor = createSupervisor({
    watchFiles: files,
    exists: () => true,
    watchFile: (file, listener) => {
      const watcher = { file, listener, closed: false, close() { this.closed = true; } };
      watchers.push(watcher);
      return watcher;
    },
    setTimer: (callback, delay) => {
      const timer = { callback, delay };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => { timer.cleared = true; },
    spawnProcess: (_executable, _args, options) => {
      spawnOptions.push(options);
      const child = makeChild(100 + children.length);
      children.push(child);
      return child;
    },
    electronPath: '/bin/electron-test',
    appRoot: '/app',
    log: { log: (line) => logs.push(line), warn: () => {}, error: () => {} },
  });
  return { files, watchers, timers, children, spawnOptions, logs, supervisor };
}

test('uses the explicit watch list and the specified debounce interval', () => {
  const harness = makeHarness();
  harness.supervisor.start();

  assert.deepEqual(harness.watchers.map((watcher) => watcher.file), harness.files);
  assert.equal(harness.children.length, 1);
  assert.equal(harness.spawnOptions[0].detached, true);

  harness.watchers[0].listener('change');
  harness.watchers[0].listener('change');
  assert.equal(harness.timers.length, 1);
  assert.equal(harness.timers[0].delay, RESTART_DEBOUNCE_MS);
});

test('restarts the child after a debounced source change', async () => {
  const harness = makeHarness();
  harness.supervisor.start();

  harness.watchers[0].listener('change');
  harness.timers[0].callback();
  assert.deepEqual(harness.children[0].killCalls, ['SIGTERM']);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.children.length, 2);
  assert.equal(harness.children[1].pid, 101);
});

test('closes watchers and terminates the child on shutdown', () => {
  const harness = makeHarness();
  harness.supervisor.start();
  harness.supervisor.stop();

  assert.ok(harness.watchers.every((watcher) => watcher.closed));
  assert.deepEqual(harness.children[0].killCalls, ['SIGTERM']);
  assert.equal(harness.supervisor.stopping, true);
});
