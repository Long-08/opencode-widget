// Phase 6B: main-process notification manager.
//
// Plain Node module with all Electron touch-points injected, so it is testable
// without launching Electron (see tests/js/notification_manager.test.js). main.js
// supplies the real fetch / OS notification / window helpers.
//
// Ownership: this layer only *consumes* the forecast payload; all burn-rate and
// time-to-limit computation stays in the server's `/api/forecast`.
'use strict';

const fs = require('fs');
const path = require('path');
const policy = require('./notification_policy');

const MIN_INTERVAL_MS = 15 * 60 * 1000;
const DEFAULT_INTERVAL_MS = 900 * 1000; // 15 minutes

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function toMs(value) {
  if (value instanceof Date) return value.getTime();
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now();
}

function localMinutesFrom(ms) {
  const d = new Date(ms);
  return d.getHours() * 60 + d.getMinutes();
}

function createNotificationManager(deps) {
  const d = deps || {};
  const statePath = d.statePath;
  const fetchForecast = typeof d.fetchForecast === 'function' ? d.fetchForecast : async () => null;
  const showNotification = typeof d.showNotification === 'function' ? d.showNotification : () => false;
  const log = typeof d.log === 'function' ? d.log : () => {};

  const intervalMs = Math.max(
    MIN_INTERVAL_MS,
    typeof d.intervalMs === 'number' && Number.isFinite(d.intervalMs) && d.intervalMs > 0
      ? Math.round(d.intervalMs)
      : DEFAULT_INTERVAL_MS
  );
  const now = typeof d.now === 'function' ? d.now : () => Date.now();

  let state = null;
  let timer = null;
  let started = false;
  let running = false;
  let shuttingDown = false;

  function defaultState() {
    return {
      settings: policy.normalizeSettings(policy.DEFAULT_SETTINGS, {}),
      baseline_done: false,
      events: [],
      updated_at: null,
    };
  }

  function loadState() {
    if (state) return state;
    let loaded = null;
    try {
      const raw = fs.readFileSync(statePath, 'utf8');
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) loaded = parsed;
    } catch (_) {
      loaded = null; // missing or corrupt -> defaults
    }
    state = defaultState();
    if (loaded) {
      const validated = policy.validateSettingsPayload(loaded.settings || {});
      if (validated.ok) state.settings = policy.normalizeSettings(policy.DEFAULT_SETTINGS, validated.value);
      state.baseline_done = loaded.baseline_done === true;
      state.events = Array.isArray(loaded.events) ? loaded.events : [];
      state.updated_at = typeof loaded.updated_at === 'number' ? loaded.updated_at : null;
    }
    return state;
  }

  function saveState() {
    const serialized = JSON.stringify(state);
    const tmp = statePath + '.tmp';
    try {
      fs.mkdirSync(path.dirname(statePath), { recursive: true });
    } catch (_) { /* directory already exists */ }
    fs.writeFileSync(tmp, serialized, 'utf8');
    fs.renameSync(tmp, statePath); // atomic replace
  }

  async function evaluateOnce() {
    if (shuttingDown) {
      return { decision: 'error', reason: 'shutting_down', notifications: [], events: [], baseline: false };
    }
    const current = loadState();
    const nowMs = toMs(now());
    const localMinutes = localMinutesFrom(nowMs);

    if (!current.settings.enabled) {
      return policy.planTick({ forecast: null, settings: current.settings, state: current, nowMs, localMinutes });
    }

    let forecast = null;
    try {
      forecast = await fetchForecast();
    } catch (_) {
      forecast = null; // server/forecast errors are skipped silently
    }
    if (!forecast || typeof forecast !== 'object') {
      log('forecast unavailable; skipping tick');
      return {
        decision: 'error',
        reason: 'forecast_unavailable',
        notifications: [],
        events: [],
        baseline: current.baseline_done === true,
      };
    }

    const plan = policy.planTick({
      forecast: forecast,
      settings: current.settings,
      state: current,
      nowMs: nowMs,
      localMinutes: localMinutes,
    });

    for (const event of plan.notifications) {
      let result;
      try {
        const raw = showNotification(event);
        result = raw === 'unsupported' ? 'unsupported' : (raw ? 'shown' : 'failed');
      } catch (_) {
        result = 'failed';
      }
      policy.applyResult(current, Object.assign({}, event, { nowMs: nowMs }), result);
      log('notification ' + policy.dedupeKey(event) + ' -> ' + result);
    }

    current.updated_at = nowMs;
    saveState();
    return plan;
  }

  async function start() {
    if (started) return false;
    started = true;
    running = true;
    shuttingDown = false;
    loadState();
    try {
      await evaluateOnce();
    } catch (_) {
      // evaluation failures must never take down the main process
    }
    if (!shuttingDown && !timer && running) {
      timer = setInterval(() => {
        evaluateOnce().catch(() => { /* never throw from the timer */ });
      }, intervalMs);
      // Do not let the notification timer by itself keep the process alive;
      // Electron's app lifecycle owns the main process.
      if (timer && typeof timer.unref === 'function') timer.unref();
    }
    return true;
  }

  function stop() {
    shuttingDown = true;
    started = false;
    running = false;
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  function isRunning() {
    return running;
  }

  function getSettings() {
    return clone(loadState().settings);
  }

  function setSettings(patch) {
    const current = loadState();
    const validated = policy.validateSettingsPayload(patch);
    if (!validated.ok) return { ok: false, error: validated.error };
    const wasEnabled = current.settings.enabled === true;
    current.settings = policy.normalizeSettings(current.settings, validated.value);
    // Turning notifications on (re)establishes the baseline so existing
    // conditions never fire an immediate burst.
    if (!wasEnabled && current.settings.enabled) current.baseline_done = false;
    try {
      saveState();
    } catch (_) {
      return { ok: false, error: 'persist_failed' };
    }
    return { ok: true, settings: clone(current.settings) };
  }

  return {
    start: start,
    stop: stop,
    evaluateOnce: evaluateOnce,
    getSettings: getSettings,
    setSettings: setSettings,
    isRunning: isRunning,
    intervalMs: intervalMs,
    statePath: statePath,
    // exposed for tests / diagnostics only
    loadState: loadState,
    saveState: saveState,
  };
}

module.exports = {
  createNotificationManager,
  MIN_INTERVAL_MS,
  DEFAULT_INTERVAL_MS,
};
