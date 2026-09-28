// Phase 6B: pure, opt-in notification policy.
//
// Plain Node module: it must NOT require('electron') and must not depend on the
// DOM so it can be unit-tested with node:test (see tests/js/notification_policy.test.js).
//
// The policy only *consumes* the `/api/forecast` payload. It never recomputes a
// burn rate or a time-to-limit: it reads the estimate the server already
// produced. Notifications are default OFF; this module is inert unless the caller
// explicitly enables settings.
'use strict';

// ---- named constants -------------------------------------------------------

const DEFAULT_SETTINGS = {
  enabled: false,
  quiet_hours: { enabled: false, start: '23:00', end: '08:00' },
};

// Estimate states that may produce a notification. Everything else
// (reset_before_limit / no_usage / insufficient_data / unavailable /
// reset_unknown) is deliberately ignored.
const TRIGGER_STATES = ['limit_before_reset', 'already_at_limit'];

// Stable window wins: pick the first rate window in preference order whose rate
// is "ok". Never pick the scariest (shortest) window.
const WINDOW_PREFERENCE = {
  session: ['window_avg', 'last_60m', 'last_30m'],
  weekly: ['last_7d', 'last_3d', 'last_24h'],
  period: ['daily_average', 'recent_3d_average', 'recent_7d_average'],
};

// Per-window priority order (lower index = more urgent).
const PRIORITY = ['already_at_limit', 'session_limit_risk', 'weekly_limit_risk', 'period_limit_risk'];

// At most one OS notification per evaluation tick.
const MAX_PER_TICK = 1;

const COOLDOWN_MS = {
  warning: 4 * 3600 * 1000,
  critical: 8 * 3600 * 1000,
};

// Simple worsening rule that is allowed to break the cooldown:
//   previous status limit_before_reset -> current already_at_limit, or
//   previous time_to_limit_hours > 2 and current <= 1.
const ESCALATION = {
  prev_ttl_over_hours: 2,
  new_ttl_at_most_hours: 1,
};

// Clicking a notification may only restore/focus the existing widget and ask the
// renderer to open the (already local) forecast tab. No external URLs, no
// destructive action, no resize.
const CLICK_ACTION = {
  restore: true,
  show: true,
  focus: true,
  open_forecast: true,
  open_external: false,
};

const WINDOW_ORDER = ['session', 'weekly', 'period'];
const WINDOW_LABELS = {
  session: '5h session',
  weekly: 'weekly',
  period: 'current period',
};

// Bounded state size: only the most recent entries are retained.
const MAX_EVENTS = 100;

const HHMM_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;
const MAX_SETTINGS_STRING = 64;

const SETTINGS_KEYS = new Set(['enabled', 'quiet_hours']);
const QUIET_KEYS = new Set(['enabled', 'start', 'end']);

// ---- small helpers ---------------------------------------------------------

function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

function isInt(v) {
  return typeof v === 'number' && Number.isInteger(v);
}

// "23:00" -> 1380 minutes; null for anything that is not a valid HH:MM string.
function parseHHMM(s) {
  if (typeof s !== 'string') return null;
  const m = HHMM_RE.exec(s);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

// Cross-midnight safe: start > end means the quiet window wraps past midnight.
function inQuietHours(nowMinutes, start, end) {
  const s = typeof start === 'string' ? parseHHMM(start) : start;
  const e = typeof end === 'string' ? parseHHMM(end) : end;
  if (!isInt(nowMinutes) || !isInt(s) || !isInt(e)) return false;
  if (s === e) return false; // zero-length window is never quiet
  if (s < e) return nowMinutes >= s && nowMinutes < e;
  return nowMinutes >= s || nowMinutes < e;
}

function windowLabel(window) {
  return WINDOW_LABELS[window] || 'quota';
}

function formatHours(hours) {
  if (!isNum(hours)) return 'an unknown time';
  if (hours < 1) return Math.max(1, Math.round(hours * 60)) + ' minutes';
  return hours.toFixed(1) + ' hours';
}

// ---- forecast signal selection --------------------------------------------

// Pick the first rate window in preference order whose rate status is "ok".
// The estimate for that same key supplies the status/time-to-limit. When the
// stable window is usable but its estimate is not a trigger the caller must
// return no event (we do NOT fall through to a shorter, scarier window).
function selectSignal(forecast, window) {
  const empty = {
    window: window,
    rate_key: null,
    status: null,
    time_to_limit_hours: null,
    reset_at: null,
    usable: false,
  };
  if (!forecast || typeof forecast !== 'object') return empty;
  const section = forecast[window];
  if (!section || typeof section !== 'object') return empty;
  const rates = section.rates && typeof section.rates === 'object' ? section.rates : {};
  const estimates = section.estimates && typeof section.estimates === 'object' ? section.estimates : {};
  const prefs = WINDOW_PREFERENCE[window] || [];
  for (const key of prefs) {
    const rate = rates[key];
    if (!rate || rate.status !== 'ok') continue;
    const est = estimates[key];
    return {
      window: window,
      rate_key: key,
      status: est && typeof est.status === 'string' ? est.status : null,
      time_to_limit_hours: est && isNum(est.time_to_limit_hours) ? est.time_to_limit_hours : null,
      reset_at: est && isNum(est.reset_at) ? est.reset_at : null,
      usable: true,
    };
  }
  return empty;
}

// Candidate events for the three quota windows. `already_at_limit` is driven by
// the official remaining quota (<= 0) for any window; `limit_before_reset` only
// applies to session/weekly.
function evaluateForecast(forecast) {
  const out = [];
  if (!forecast || typeof forecast !== 'object') return out;
  for (const window of WINDOW_ORDER) {
    const section = forecast[window];
    if (!section || typeof section !== 'object') continue;
    const official = section.official;
    if (official && official.status === 'ok' && isNum(official.remaining) && official.remaining <= 0) {
      out.push({
        event_type: 'already_at_limit',
        window: window,
        status: 'already_at_limit',
        severity: 'critical',
        time_to_limit_hours: 0,
        reset_at: isNum(official.reset_at) ? official.reset_at : null,
      });
    }
    if (window === 'session' || window === 'weekly') {
      const signal = selectSignal(forecast, window);
      if (signal.usable && signal.status === 'limit_before_reset') {
        out.push({
          event_type: 'limit_before_reset',
          window: window,
          status: 'limit_before_reset',
          severity: 'warning',
          time_to_limit_hours: isNum(signal.time_to_limit_hours) ? signal.time_to_limit_hours : null,
          reset_at: isNum(signal.reset_at) ? signal.reset_at : null,
        });
      }
    }
  }
  return out;
}

function dedupeKey(event) {
  const reset = event && event.reset_at != null ? String(event.reset_at) : 'noreset';
  return (event ? event.event_type : '') + '|' + (event ? event.window : '') + '|' + reset;
}

// Reset-aware pruning: drop entries whose reset_at is in the past, then bound
// the retained history to the most recent MAX_EVENTS.
function pruneEvents(events, nowMs) {
  if (!Array.isArray(events)) return [];
  const kept = [];
  for (const e of events) {
    if (!e || typeof e !== 'object') continue;
    if (e.reset_at != null && Number(e.reset_at) < nowMs) continue;
    kept.push(e);
  }
  return kept.slice(-MAX_EVENTS);
}

// ---- state / results -------------------------------------------------------

function ensureEvents(state) {
  if (!Array.isArray(state.events)) state.events = [];
  return state.events;
}

function findEntry(state, key) {
  const events = ensureEvents(state);
  for (const e of events) {
    if (dedupeKey(e) === key) return e;
  }
  return null;
}

function copyEventFields(entry, event, nowMs) {
  entry.event_type = event.event_type;
  entry.window = event.window;
  entry.status = event.status;
  entry.severity = event.severity;
  entry.time_to_limit_hours = event.time_to_limit_hours;
  entry.reset_at = event.reset_at;
  entry.updated_at = nowMs;
}

function recordEvents(state, events, nowMs) {
  ensureEvents(state);
  for (const event of events) {
    const key = dedupeKey(event);
    let entry = findEntry(state, key);
    if (!entry) {
      entry = {
        event_type: event.event_type,
        window: event.window,
        status: event.status,
        severity: event.severity,
        time_to_limit_hours: event.time_to_limit_hours,
        reset_at: event.reset_at,
        key: key,
        first_seen_at: nowMs,
        updated_at: nowMs,
        attempts: 0,
        last_result: null,
        last_notified_at: null,
        suppressed_at: null,
      };
      state.events.push(entry);
    } else {
      copyEventFields(entry, event, nowMs);
    }
  }
}

function withNow(event, nowMs) {
  return Object.assign({}, event, { nowMs: nowMs });
}

// Only `shown` marks the event notified. Every other outcome records an attempt
// (and, for quiet/cooldown suppression, a suppressed_at timestamp) but leaves
// last_notified_at untouched so the event can be retried later.
function applyResult(state, event, result) {
  ensureEvents(state);
  const key = dedupeKey(event);
  let entry = findEntry(state, key);
  const at = event && isNum(event.nowMs) ? event.nowMs : Date.now();
  if (!entry) {
    entry = {
      event_type: event.event_type,
      window: event.window,
      status: event.status,
      severity: event.severity,
      time_to_limit_hours: event.time_to_limit_hours,
      reset_at: event.reset_at,
      key: key,
      first_seen_at: at,
      updated_at: at,
      attempts: 0,
      last_result: null,
      last_notified_at: null,
      suppressed_at: null,
    };
    state.events.push(entry);
  }
  if (result === 'shown') {
    entry.last_notified_at = at;
    entry.last_result = 'shown';
  } else {
    entry.attempts = (entry.attempts || 0) + 1;
    entry.last_result = result;
    if (result === 'suppressed' || result === 'cooldown') entry.suppressed_at = at;
  }
  entry.updated_at = at;
  return entry;
}

// ---- tick planning ---------------------------------------------------------

function priorityRank(event) {
  if (event.event_type === 'already_at_limit') return PRIORITY.indexOf('already_at_limit');
  const tag = event.window + '_limit_risk';
  const i = PRIORITY.indexOf(tag);
  return i < 0 ? PRIORITY.length : i;
}

function previousNotifiedForWindow(state, window, nowMs) {
  let best = null;
  for (const e of ensureEvents(state)) {
    if (!e || e.window !== window) continue;
    if (!isNum(e.last_notified_at)) continue;
    if (e.last_notified_at > nowMs) continue;
    if (!best || e.last_notified_at > best.last_notified_at) best = e;
  }
  return best;
}

// Blocked when the same window was notified within the severity cooldown and the
// event is not a clear escalation of the previously notified one.
function isBlocked(state, event, nowMs) {
  const prev = previousNotifiedForWindow(state, event.window, nowMs);
  if (!prev) return false;
  const cooldown = isNum(COOLDOWN_MS[event.severity]) ? COOLDOWN_MS[event.severity] : COOLDOWN_MS.warning;
  if (nowMs - prev.last_notified_at >= cooldown) return false;
  const escalated = (prev.status === 'limit_before_reset' && event.status === 'already_at_limit')
    || (isNum(prev.time_to_limit_hours) && prev.time_to_limit_hours > ESCALATION.prev_ttl_over_hours
      && isNum(event.time_to_limit_hours) && event.time_to_limit_hours <= ESCALATION.new_ttl_at_most_hours);
  return !escalated;
}

function quietActive(settings, localMinutes) {
  const qh = settings && settings.quiet_hours;
  if (!qh || qh.enabled !== true) return false;
  return inQuietHours(localMinutes, qh.start, qh.end);
}

// The single entry point per tick. Mutates `state.events` so the caller can
// persist it. Never returns a notification when disabled / baseline / quiet /
// cooling down.
function planTick(input) {
  const args = input || {};
  const settings = normalizeSettings(DEFAULT_SETTINGS, args.settings || {});
  const state = args.state;
  const nowMs = isNum(args.nowMs) ? args.nowMs : Date.now();
  const localMinutes = isInt(args.localMinutes) ? args.localMinutes : null;

  if (!settings.enabled) {
    return { decision: 'disabled', reason: 'notifications_disabled', notifications: [], events: [], baseline: false };
  }
  if (!state || typeof state !== 'object') {
    return { decision: 'error', reason: 'invalid_state', notifications: [], events: [], baseline: false };
  }
  if (!args.forecast || typeof args.forecast !== 'object') {
    return { decision: 'error', reason: 'invalid_forecast', notifications: [], events: [], baseline: false };
  }

  const events = pruneEvents(evaluateForecast(args.forecast), nowMs);

  if (state.baseline_done !== true) {
    ensureEvents(state);
    state.events = pruneEvents(state.events, nowMs);
    recordEvents(state, events, nowMs);
    state.baseline_done = true;
    state.updated_at = nowMs;
    return { decision: 'baseline', reason: 'baseline_recorded', notifications: [], events: events, baseline: true };
  }

  ensureEvents(state);
  state.events = pruneEvents(state.events, nowMs);

  if (events.length === 0) {
    state.updated_at = nowMs;
    return { decision: 'no_event', reason: 'no_candidate_events', notifications: [], events: events, baseline: false };
  }

  if (quietActive(settings, localMinutes)) {
    recordEvents(state, events, nowMs);
    for (const event of events) applyResult(state, withNow(event, nowMs), 'suppressed');
    state.updated_at = nowMs;
    return { decision: 'suppressed', reason: 'quiet_hours', notifications: [], events: events, baseline: false };
  }

  const candidates = events.slice().sort((a, b) => priorityRank(a) - priorityRank(b));
  const notifications = [];
  const blocked = [];
  for (const event of candidates) {
    if (notifications.length >= MAX_PER_TICK) break;
    if (isBlocked(state, event, nowMs)) blocked.push(event);
    else notifications.push(event);
  }

  recordEvents(state, events, nowMs);
  for (const event of blocked) applyResult(state, withNow(event, nowMs), 'cooldown');
  state.updated_at = nowMs;

  if (notifications.length > 0) {
    return { decision: 'notify', reason: 'notification_ready', notifications: notifications, events: events, baseline: false };
  }
  return { decision: 'cooldown', reason: 'cooldown_active', notifications: [], events: events, baseline: false };
}

// ---- notification wording --------------------------------------------------

// Hedged wording only. Never includes a session id, agent, model, workspace or
// prompt, and never asserts certainty.
function buildNotification(event, opts) {
  const options = opts || {};
  const ev = event || {};
  const label = windowLabel(ev.window);
  if (ev.event_type === 'already_at_limit' || ev.severity === 'critical') {
    return {
      title: 'OpenCode usage warning',
      body: label + ': Current quota state indicates the limit has been reached.',
    };
  }
  const ttl = isNum(options.ttlHours) ? options.ttlHours : ev.time_to_limit_hours;
  return {
    title: 'OpenCode Widget',
    body: 'Estimated: your ' + label + ' quota may reach its limit in about '
      + formatHours(ttl) + ' (based on recent usage).',
  };
}

// ---- settings validation ---------------------------------------------------

function validateSettingsPayload(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return { ok: false, error: 'invalid_payload' };
  }
  const value = {};
  for (const key of Object.keys(payload)) {
    if (!SETTINGS_KEYS.has(key)) return { ok: false, error: 'unknown_field:' + key };
  }
  if ('enabled' in payload) {
    if (typeof payload.enabled !== 'boolean') return { ok: false, error: 'invalid_enabled' };
    value.enabled = payload.enabled;
  }
  if ('quiet_hours' in payload) {
    const q = payload.quiet_hours;
    if (!q || typeof q !== 'object' || Array.isArray(q)) {
      return { ok: false, error: 'invalid_quiet_hours' };
    }
    for (const key of Object.keys(q)) {
      if (!QUIET_KEYS.has(key)) return { ok: false, error: 'unknown_field:quiet_hours.' + key };
    }
    const qv = {};
    if ('enabled' in q) {
      if (typeof q.enabled !== 'boolean') return { ok: false, error: 'invalid_quiet_hours.enabled' };
      qv.enabled = q.enabled;
    }
    for (const key of ['start', 'end']) {
      if (!(key in q)) continue;
      const s = q[key];
      if (typeof s !== 'string') return { ok: false, error: 'invalid_quiet_hours.' + key };
      if (s.length > MAX_SETTINGS_STRING) return { ok: false, error: 'too_long' };
      if (parseHHMM(s) === null) return { ok: false, error: 'invalid_hhmm' };
      qv[key] = s;
    }
    value.quiet_hours = qv;
  }
  return { ok: true, value: value };
}

function normalizeSettings(current, patch) {
  const base = current && typeof current === 'object' ? current : {};
  const baseQuiet = base.quiet_hours && typeof base.quiet_hours === 'object' ? base.quiet_hours : {};
  const merged = {
    enabled: base.enabled === true,
    quiet_hours: {
      enabled: baseQuiet.enabled === true,
      start: typeof baseQuiet.start === 'string' ? baseQuiet.start : DEFAULT_SETTINGS.quiet_hours.start,
      end: typeof baseQuiet.end === 'string' ? baseQuiet.end : DEFAULT_SETTINGS.quiet_hours.end,
    },
  };
  const p = patch && typeof patch === 'object' ? patch : {};
  if (typeof p.enabled === 'boolean') merged.enabled = p.enabled;
  if (p.quiet_hours && typeof p.quiet_hours === 'object') {
    if (typeof p.quiet_hours.enabled === 'boolean') merged.quiet_hours.enabled = p.quiet_hours.enabled;
    if (typeof p.quiet_hours.start === 'string') merged.quiet_hours.start = p.quiet_hours.start;
    if (typeof p.quiet_hours.end === 'string') merged.quiet_hours.end = p.quiet_hours.end;
  }
  return merged;
}

module.exports = {
  DEFAULT_SETTINGS,
  TRIGGER_STATES,
  WINDOW_PREFERENCE,
  PRIORITY,
  MAX_PER_TICK,
  COOLDOWN_MS,
  ESCALATION,
  CLICK_ACTION,
  MAX_EVENTS,
  WINDOW_ORDER,
  WINDOW_LABELS,
  parseHHMM,
  inQuietHours,
  selectSignal,
  evaluateForecast,
  dedupeKey,
  pruneEvents,
  planTick,
  applyResult,
  buildNotification,
  validateSettingsPayload,
  normalizeSettings,
};
