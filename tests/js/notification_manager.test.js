// Phase 6B: unit tests for electron/notification_manager.js.
// All Electron touch-points are injected fakes, so no Electron is required.
//   node --test tests/js/notification_manager.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const nm = require(path.resolve(__dirname, '..', '..', 'electron', 'notification_manager.js'));

const MS = 1700000000000;

function makeTempStatePath(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'widget-notify-mgr-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return path.join(dir, 'notification_state.json');
}

function rateOk() {
  return { status: 'ok', usage: 10, duration_hours: 1, usage_per_hour: 10, usage_per_day: 240, sample_points: 12, window_ms: 3600000 };
}
function rateNoUsage() {
  return { status: 'no_usage', usage: 0, duration_hours: 0, usage_per_hour: null, usage_per_day: null, sample_points: 0, window_ms: null };
}
function officialUnavailable() {
  return { status: 'unavailable', used: null, remaining: null, limit: null, reset_at: null, reset_source: 'unknown' };
}
function sessionForecast(ttl, resetAt) {
  return {
    type: 'estimate',
    session: {
      official: officialUnavailable(),
      rates: { window_avg: rateNoUsage(), last_60m: rateNoUsage(), last_30m: rateOk() },
      estimates: {
        window_avg: { status: 'no_usage' },
        last_60m: { status: 'no_usage' },
        last_30m: { status: 'limit_before_reset', time_to_limit_hours: ttl, reset_at: resetAt },
      },
    },
    weekly: { official: officialUnavailable(), rates: {}, estimates: {} },
    period: { official: officialUnavailable(), rates: {}, estimates: {} },
  };
}

function makeManager(t, overrides) {
  const statePath = makeTempStatePath(t);
  const counters = { fetch: 0, shown: [] };
  const deps = Object.assign({
    statePath: statePath,
    intervalMs: 900000,
    now: () => MS,
    fetchForecast: async () => { counters.fetch += 1; return sessionForecast(1, MS + 4 * 3600000); },
    showNotification: (event) => { counters.shown.push(event); return true; },
    log: () => {},
  }, overrides || {});
  const manager = nm.createNotificationManager(deps);
  return { manager, counters, statePath };
}

test('start establishes the baseline once and creates a single timer', async (t) => {
  const { manager, counters } = makeManager(t);
  manager.setSettings({ enabled: true });
  assert.equal(await manager.start(), true);
  assert.equal(counters.fetch, 1); // baseline evaluation
  assert.equal(counters.shown.length, 0);
  assert.equal(manager.isRunning(), true);
});

test('a repeated start does not evaluate again or create a second timer', async (t) => {
  const { manager, counters } = makeManager(t);
  manager.setSettings({ enabled: true });
  await manager.start();
  const fetches = counters.fetch;
  assert.equal(await manager.start(), false);
  assert.equal(counters.fetch, fetches);
  assert.equal(manager.isRunning(), true);
  manager.stop();
});

test('the interval is clamped to at least 15 minutes', (t) => {
  const { manager } = makeManager(t, { intervalMs: 1000 });
  assert.ok(manager.intervalMs >= nm.MIN_INTERVAL_MS, manager.intervalMs);
  assert.equal(nm.MIN_INTERVAL_MS, 15 * 60 * 1000);
});

test('stop clears the timer and blocks any further evaluation', async (t) => {
  const { manager, counters } = makeManager(t);
  manager.setSettings({ enabled: true });
  await manager.start();
  manager.stop();
  assert.equal(manager.isRunning(), false);
  const fetches = counters.fetch;
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'error');
  assert.equal(plan.reason, 'shutting_down');
  assert.equal(counters.fetch, fetches);
});

test('disabled settings never fetch and never notify', async (t) => {
  const { manager, counters } = makeManager(t);
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'disabled');
  assert.equal(counters.fetch, 0);
  assert.equal(counters.shown.length, 0);
});

test('the first enabled tick records a baseline without notifying', async (t) => {
  const { manager, counters } = makeManager(t);
  manager.setSettings({ enabled: true });
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'baseline');
  assert.equal(counters.shown.length, 0);
  assert.equal(manager.loadState().events.length, 1);
});

test('a second tick for a new reset window notifies exactly once', async (t) => {
  const statePath = makeTempStatePath(t);
  const shown = [];
  let forecast = sessionForecast(1, MS + 4 * 3600000);
  const manager = nm.createNotificationManager({
    statePath: statePath, intervalMs: 900000, now: () => MS,
    fetchForecast: async () => forecast,
    showNotification: (e) => { shown.push(e); return true; },
  });
  manager.setSettings({ enabled: true });
  await manager.evaluateOnce(); // baseline (reset MS+4h)
  assert.equal(shown.length, 0);

  forecast = sessionForecast(1, MS + 5 * 3600000); // new reset window -> new dedupe key
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'notify');
  assert.equal(shown.length, 1);
});

test('a repeat inside the cooldown is suppressed', async (t) => {
  const statePath = makeTempStatePath(t);
  const shown = [];
  let forecast = sessionForecast(1, MS + 4 * 3600000);
  const manager = nm.createNotificationManager({
    statePath: statePath, intervalMs: 900000, now: () => MS,
    fetchForecast: async () => forecast,
    showNotification: (e) => { shown.push(e); return true; },
  });
  manager.setSettings({ enabled: true });
  await manager.evaluateOnce(); // baseline
  const notify = await manager.evaluateOnce();
  assert.equal(notify.decision, 'notify');
  assert.equal(shown.length, 1);
  const cooling = await manager.evaluateOnce();
  assert.equal(cooling.decision, 'cooldown');
  assert.equal(shown.length, 1);
});

test('enabling re-baselines an already-configured manager', async (t) => {
  const { manager } = makeManager(t);
  manager.setSettings({ enabled: true });
  await manager.evaluateOnce();
  assert.equal(manager.loadState().baseline_done, true);
  manager.setSettings({ enabled: false });
  const on = manager.setSettings({ enabled: true });
  assert.equal(on.ok, true);
  assert.equal(manager.loadState().baseline_done, false);
});

test('disabling stops fetching and notifying', async (t) => {
  const { manager, counters } = makeManager(t);
  manager.setSettings({ enabled: true });
  await manager.evaluateOnce();
  manager.setSettings({ enabled: false });
  const fetches = counters.fetch;
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'disabled');
  assert.equal(counters.fetch, fetches);
  assert.equal(counters.shown.length, 0);
});

test('settings persist to disk and reload in a fresh manager', async (t) => {
  const { manager, statePath } = makeManager(t);
  manager.setSettings({ enabled: true, quiet_hours: { enabled: true, start: '21:00', end: '06:00' } });
  const reloaded = nm.createNotificationManager({ statePath: statePath, now: () => MS });
  assert.deepEqual(reloaded.getSettings(), {
    enabled: true,
    quiet_hours: { enabled: true, start: '21:00', end: '06:00' },
  });
});

test('a throwing showNotification is recorded as failed and is retried', async (t) => {
  const statePath = makeTempStatePath(t);
  let calls = 0;
  const manager = nm.createNotificationManager({
    statePath: statePath, intervalMs: 900000, now: () => MS,
    fetchForecast: async () => sessionForecast(1, MS + 4 * 3600000),
    showNotification: () => { calls += 1; throw new Error('boom'); },
  });
  manager.setSettings({ enabled: true });
  await manager.evaluateOnce(); // baseline
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'notify');
  assert.equal(calls, 1);
  const entry = manager.loadState().events[0];
  assert.equal(entry.last_notified_at, null);
  assert.equal(entry.last_result, 'failed');
  // not marked notified -> the next tick tries again
  const retry = await manager.evaluateOnce();
  assert.equal(retry.decision, 'notify');
  assert.equal(calls, 2);
});

test('forecast errors are skipped silently (no notification, no throw)', async (t) => {
  const shown = [];
  const manager = nm.createNotificationManager({
    statePath: makeTempStatePath(t), intervalMs: 900000, now: () => MS,
    fetchForecast: async () => { throw new Error('offline'); },
    showNotification: (e) => { shown.push(e); return true; },
  });
  manager.setSettings({ enabled: true });
  const plan = await manager.evaluateOnce();
  assert.equal(plan.decision, 'error');
  assert.equal(plan.reason, 'forecast_unavailable');
  assert.equal(shown.length, 0);
});

test('setSettings rejects invalid payloads through the policy', (t) => {
  const { manager } = makeManager(t);
  const bad = manager.setSettings({ nope: true });
  assert.equal(bad.ok, false);
  assert.ok(bad.error);
  assert.equal(manager.getSettings().enabled, false);
});

test('interval clamps to MIN_INTERVAL_MS unless the fixture seam is used', (t) => {
  const base = {
    statePath: makeTempStatePath(t),
    fetchForecast: async () => null,
    showNotification: () => false,
    focusWidget() {},
    openForecast() {},
    log() {},
    now: () => MS,
  };
  assert.equal(nm.createNotificationManager({ ...base, intervalMs: 3000 }).intervalMs, nm.MIN_INTERVAL_MS);
  assert.equal(nm.createNotificationManager({ ...base, intervalMs: 3000, minIntervalMs: 1000 }).intervalMs, 3000);
});
