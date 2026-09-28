// Phase 6B: unit tests for electron/notification_policy.js (pure, DOM/Electron free).
// Plain node:test + node:assert, no dependencies. Run from the project root:
//   node --test tests/js/notification_policy.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const p = require(path.resolve(__dirname, '..', '..', 'electron', 'notification_policy.js'));

const MS = 1700000000000;

// ---- forecast fixtures -----------------------------------------------------

function rateOk(over) {
  return Object.assign({
    status: 'ok', usage: 10, duration_hours: 1, usage_per_hour: 10,
    usage_per_day: 240, sample_points: 12, window_ms: 3600000,
    first_ts: MS, last_ts: MS,
  }, over);
}
function rateInsufficient() {
  return { status: 'insufficient_data', usage: 5, duration_hours: null, usage_per_hour: null, usage_per_day: null, sample_points: 1, window_ms: null, first_ts: MS, last_ts: MS };
}
function rateNoUsage() {
  return { status: 'no_usage', usage: 0, duration_hours: 0, usage_per_hour: null, usage_per_day: null, sample_points: 0, window_ms: null, first_ts: null, last_ts: null };
}
function est(status, over) {
  return Object.assign({
    status: status, time_to_limit_hours: null, raw_time_to_limit_hours: null,
    estimated_limit_at: null, reset_at: null,
  }, over);
}
function estLimitBefore(ttl, resetAt) {
  return est('limit_before_reset', { time_to_limit_hours: ttl, raw_time_to_limit_hours: ttl, reset_at: resetAt });
}
function estResetBefore(resetAt) {
  return est('reset_before_limit', { reset_at: resetAt });
}
function estAlready(resetAt) {
  return est('already_at_limit', { time_to_limit_hours: 0, raw_time_to_limit_hours: 0, reset_at: resetAt });
}
function officialOk(over) {
  return Object.assign({
    status: 'ok', used: 10, remaining: 90, limit: 100,
    reset_at: MS + 3600000, reset_source: 'official_reset_text',
  }, over);
}
function officialUnavailable() {
  return { status: 'unavailable', used: null, remaining: null, limit: null, reset_at: null, reset_source: 'unknown' };
}
function section(official, rates, estimates) {
  return { official: official, rates: rates, estimates: estimates };
}

// session with a stable window_avg that is usable and reset_before_limit, while a
// shorter window (last_30m) would say limit_before_reset.
function stableResetBeforeForecast() {
  return {
    type: 'estimate',
    session: section(officialOk(), {
      last_30m: rateOk(),
      last_60m: rateOk(),
      window_avg: rateOk(),
    }, {
      last_30m: estLimitBefore(0.2, MS + 7200000),
      last_60m: estLimitBefore(0.3, MS + 7200000),
      window_avg: estResetBefore(MS + 3600000),
    }),
    weekly: section(officialUnavailable(), { last_24h: rateNoUsage(), last_3d: rateNoUsage(), last_7d: rateNoUsage() }, {}),
    period: section(officialUnavailable(), { daily_average: rateNoUsage(), recent_3d_average: rateNoUsage(), recent_7d_average: rateNoUsage() }, {}),
  };
}

function sessionLimitBeforeForecast(ttl, resetAt) {
  return {
    type: 'estimate',
    session: section(officialOk(), {
      last_30m: rateOk(), last_60m: rateInsufficient(), window_avg: rateNoUsage(),
    }, {
      window_avg: est('insufficient_data'),
      last_60m: est('insufficient_data'),
      last_30m: estLimitBefore(ttl, resetAt),
    }),
    weekly: section(officialUnavailable(), {}, {}),
    period: section(officialUnavailable(), {}, {}),
  };
}

function sessionAlreadyAtLimitForecast(resetAt) {
  return {
    type: 'estimate',
    session: section(officialOk({ remaining: 0, used: 100 }), {
      last_30m: rateOk(), last_60m: rateOk(), window_avg: rateOk(),
    }, {
      last_30m: estAlready(resetAt), last_60m: estAlready(resetAt), window_avg: estAlready(resetAt),
    }),
    weekly: section(officialUnavailable(), {}, {}),
    period: section(officialUnavailable(), {}, {}),
  };
}

function settingsOn(over) {
  return Object.assign({ enabled: true, quiet_hours: { enabled: false, start: '23:00', end: '08:00' } }, over);
}
function newState(over) {
  return Object.assign({ settings: settingsOn(), baseline_done: true, events: [], updated_at: 0 }, over);
}

// ---- constants / defaults --------------------------------------------------

test('default settings are opt-in and quiet hours are off', () => {
  assert.deepEqual(p.DEFAULT_SETTINGS, {
    enabled: false,
    quiet_hours: { enabled: false, start: '23:00', end: '08:00' },
  });
});

test('trigger states + clamps are the documented constants', () => {
  assert.deepEqual(p.TRIGGER_STATES, ['limit_before_reset', 'already_at_limit']);
  assert.equal(p.MAX_PER_TICK, 1);
  assert.equal(p.COOLDOWN_MS.warning, 4 * 3600 * 1000);
  assert.equal(p.COOLDOWN_MS.critical, 8 * 3600 * 1000);
  assert.equal(p.ESCALATION.prev_ttl_over_hours, 2);
  assert.equal(p.ESCALATION.new_ttl_at_most_hours, 1);
  assert.deepEqual(p.PRIORITY, ['already_at_limit', 'session_limit_risk', 'weekly_limit_risk', 'period_limit_risk']);
});

test('CLICK_ACTION allows focus/open-forecast only and forbids external URLs', () => {
  assert.equal(p.CLICK_ACTION.open_external, false);
  assert.equal(p.CLICK_ACTION.restore, true);
  assert.equal(p.CLICK_ACTION.show, true);
  assert.equal(p.CLICK_ACTION.focus, true);
  assert.equal(p.CLICK_ACTION.open_forecast, true);
});

// ---- time helpers ----------------------------------------------------------

test('parseHHMM accepts 00:00..23:59 and rejects everything else', () => {
  assert.equal(p.parseHHMM('00:00'), 0);
  assert.equal(p.parseHHMM('23:59'), 1439);
  assert.equal(p.parseHHMM('08:30'), 510);
  assert.equal(p.parseHHMM('24:00'), null);
  assert.equal(p.parseHHMM('12:60'), null);
  assert.equal(p.parseHHMM('8:00'), null);
  assert.equal(p.parseHHMM('0800'), null);
  assert.equal(p.parseHHMM(''), null);
  assert.equal(p.parseHHMM(510), null);
  assert.equal(p.parseHHMM(null), null);
});

test('inQuietHours handles an ordinary same-day window', () => {
  assert.equal(p.inQuietHours(60, 0, 120), true);
  assert.equal(p.inQuietHours(300, 0, 120), false);
  assert.equal(p.inQuietHours(120, 0, 120), false); // end exclusive
});

test('inQuietHours wraps across midnight', () => {
  const start = p.parseHHMM('23:00');
  const end = p.parseHHMM('08:00');
  assert.equal(p.inQuietHours(p.parseHHMM('23:30'), start, end), true);
  assert.equal(p.inQuietHours(p.parseHHMM('01:00'), start, end), true);
  assert.equal(p.inQuietHours(p.parseHHMM('07:59'), start, end), true);
  assert.equal(p.inQuietHours(p.parseHHMM('08:00'), start, end), false);
  assert.equal(p.inQuietHours(p.parseHHMM('12:00'), start, end), false);
  assert.equal(p.inQuietHours(p.parseHHMM('22:59'), start, end), false);
});

test('inQuietHours treats a zero-length window as never quiet and bad input as false', () => {
  assert.equal(p.inQuietHours(600, 600, 600), false);
  assert.equal(p.inQuietHours(null, 0, 100), false);
  assert.equal(p.inQuietHours(600, null, 100), false);
});

// ---- selectSignal ----------------------------------------------------------

test('selectSignal prefers the stable window (window_avg) over the scariest one', () => {
  const sig = p.selectSignal(stableResetBeforeForecast(), 'session');
  assert.equal(sig.window, 'session');
  assert.equal(sig.rate_key, 'window_avg');
  assert.equal(sig.usable, true);
  assert.equal(sig.status, 'reset_before_limit');
});

test('selectSignal falls back when the stable window is not usable', () => {
  const forecast = {
    session: section(officialOk(), {
      window_avg: rateInsufficient(),
      last_60m: rateOk(),
      last_30m: rateOk(),
    }, {
      window_avg: est('insufficient_data'),
      last_60m: estLimitBefore(1, MS + 7200000),
      last_30m: estLimitBefore(0.5, MS + 7200000),
    }),
  };
  const sig = p.selectSignal(forecast, 'session');
  assert.equal(sig.rate_key, 'last_60m'); // next in preference order, not last_30m
  assert.equal(sig.usable, true);
  assert.equal(sig.status, 'limit_before_reset');
});

test('selectSignal reports unusable when no preference window is ok', () => {
  const forecast = {
    weekly: section(officialOk(), {
      last_7d: rateInsufficient(), last_3d: rateNoUsage(), last_24h: rateInsufficient(),
    }, {}),
  };
  const sig = p.selectSignal(forecast, 'weekly');
  assert.equal(sig.usable, false);
  assert.equal(sig.rate_key, null);
  assert.equal(sig.status, null);
});

// ---- evaluateForecast ------------------------------------------------------

test('evaluateForecast does not emit for a stable usable reset_before_limit window', () => {
  const events = p.evaluateForecast(stableResetBeforeForecast());
  assert.deepEqual(events, []);
});

test('evaluateForecast emits a warning for session limit_before_reset', () => {
  const events = p.evaluateForecast(sessionLimitBeforeForecast(1.5, MS + 3 * 3600000));
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    event_type: 'limit_before_reset',
    window: 'session',
    status: 'limit_before_reset',
    severity: 'warning',
    time_to_limit_hours: 1.5,
    reset_at: MS + 3 * 3600000,
  });
});

test('evaluateForecast emits a critical already_at_limit from official remaining <= 0', () => {
  const events = p.evaluateForecast(sessionAlreadyAtLimitForecast(MS + 3600000));
  assert.equal(events.length, 1);
  assert.equal(events[0].event_type, 'already_at_limit');
  assert.equal(events[0].severity, 'critical');
  assert.equal(events[0].window, 'session');
});

test('evaluateForecast ignores reset_before_limit/no_usage/insufficient_data/unavailable/reset_unknown', () => {
  const forecast = {
    session: section(officialUnavailable(), {
      window_avg: rateOk(), last_60m: rateNoUsage(), last_30m: rateInsufficient(),
    }, {
      window_avg: estResetBefore(MS + 3600000),
      last_60m: est('no_usage'),
      last_30m: est('insufficient_data'),
    }),
    weekly: section(officialUnavailable(), { last_7d: rateOk() }, { last_7d: est('reset_unknown') }),
    period: section(officialUnavailable(), { daily_average: rateOk() }, { daily_average: est('unavailable') }),
  };
  assert.deepEqual(p.evaluateForecast(forecast), []);
});

// ---- dedupe / prune --------------------------------------------------------

test('dedupeKey is event_type|window|reset (or noreset)', () => {
  assert.equal(p.dedupeKey({ event_type: 'limit_before_reset', window: 'session', reset_at: 123 }), 'limit_before_reset|session|123');
  assert.equal(p.dedupeKey({ event_type: 'already_at_limit', window: 'weekly', reset_at: null }), 'already_at_limit|weekly|noreset');
});

test('a new reset window produces a new dedupe key', () => {
  const a = p.dedupeKey({ event_type: 'limit_before_reset', window: 'session', reset_at: 100 });
  const b = p.dedupeKey({ event_type: 'limit_before_reset', window: 'session', reset_at: 200 });
  assert.notEqual(a, b);
});

test('pruneEvents drops past resets, keeps null/future, and is bounded', () => {
  const kept = p.pruneEvents([
    { reset_at: 100 },
    { reset_at: 200 },
    { reset_at: null },
  ], 150);
  assert.deepEqual(kept, [{ reset_at: 200 }, { reset_at: null }]);
  const many = [];
  for (let i = 0; i < p.MAX_EVENTS + 20; i++) many.push({ reset_at: null, i: i });
  const bounded = p.pruneEvents(many, 150);
  assert.equal(bounded.length, p.MAX_EVENTS);
  assert.equal(bounded[bounded.length - 1].i, p.MAX_EVENTS + 19);
});

// ---- planTick decisions ----------------------------------------------------

test('planTick returns disabled (and never fetches) when notifications are off', () => {
  const plan = p.planTick({ forecast: sessionLimitBeforeForecast(1, MS), settings: p.DEFAULT_SETTINGS, state: newState(), nowMs: MS, localMinutes: 600 });
  assert.equal(plan.decision, 'disabled');
  assert.deepEqual(plan.notifications, []);
});

test('first tick records the baseline and emits nothing', () => {
  const state = newState({ baseline_done: false });
  const plan = p.planTick({ forecast: sessionLimitBeforeForecast(1, MS + 3600000), settings: settingsOn(), state: state, nowMs: MS, localMinutes: 600 });
  assert.equal(plan.decision, 'baseline');
  assert.deepEqual(plan.notifications, []);
  assert.equal(state.baseline_done, true);
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].last_notified_at, null);
});

test('no-event forecast yields no_event', () => {
  const plan = p.planTick({ forecast: stableResetBeforeForecast(), settings: settingsOn(), state: newState(), nowMs: MS, localMinutes: 600 });
  assert.equal(plan.decision, 'no_event');
  assert.deepEqual(plan.notifications, []);
});

test('planTick emits at most one notification, most urgent first', () => {
  const forecast = {
    session: section(officialUnavailable(), { window_avg: rateOk() }, { window_avg: estLimitBefore(2, MS + 7200000) }),
    weekly: section(officialOk({ remaining: 0, used: 100 }), { last_7d: rateOk() }, { last_7d: estAlready(MS + 7200000) }),
    period: section(officialUnavailable(), {}, {}),
  };
  const plan = p.planTick({ forecast: forecast, settings: settingsOn(), state: newState(), nowMs: MS, localMinutes: 600 });
  assert.equal(plan.decision, 'notify');
  assert.equal(plan.notifications.length, 1);
  assert.equal(plan.notifications[0].event_type, 'already_at_limit'); // rank 0
  assert.equal(plan.notifications[0].window, 'weekly');
});

test('quiet hours suppress notifications but record the events', () => {
  const state = newState();
  const plan = p.planTick({
    forecast: sessionLimitBeforeForecast(1, MS + 3600000),
    settings: settingsOn({ quiet_hours: { enabled: true, start: '00:00', end: '12:00' } }),
    state: state, nowMs: MS, localMinutes: 600,
  });
  assert.equal(plan.decision, 'suppressed');
  assert.deepEqual(plan.notifications, []);
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].suppressed_at, MS);
  assert.equal(state.events[0].last_notified_at, null);
});

test('cooldown blocks an identical repeat within the window', () => {
  const state = newState();
  const forecast = sessionLimitBeforeForecast(1, MS + 4 * 3600000);
  const first = p.planTick({ forecast: forecast, settings: settingsOn(), state: state, nowMs: MS, localMinutes: 600 });
  assert.equal(first.decision, 'notify');
  p.applyResult(state, Object.assign({}, first.notifications[0], { nowMs: MS }), 'shown');

  const again = p.planTick({ forecast: forecast, settings: settingsOn(), state: state, nowMs: MS + 3600000, localMinutes: 600 });
  assert.equal(again.decision, 'cooldown');
  assert.deepEqual(again.notifications, []);
});

test('critical escalation breaks the cooldown (limit_before_reset -> already_at_limit)', () => {
  const state = newState();
  const resetAt = MS + 4 * 3600000;
  const first = p.planTick({ forecast: sessionLimitBeforeForecast(1, resetAt), settings: settingsOn(), state: state, nowMs: MS, localMinutes: 600 });
  assert.equal(first.decision, 'notify');
  p.applyResult(state, Object.assign({}, first.notifications[0], { nowMs: MS }), 'shown');

  const second = p.planTick({ forecast: sessionAlreadyAtLimitForecast(resetAt), settings: settingsOn(), state: state, nowMs: MS + 3600000, localMinutes: 600 });
  assert.equal(second.decision, 'notify');
  assert.equal(second.notifications[0].event_type, 'already_at_limit');
});

test('a ttl drop (prev > 2h -> current <= 1h) breaks the cooldown', () => {
  const state = newState();
  const resetAt = MS + 6 * 3600000;
  const first = p.planTick({ forecast: sessionLimitBeforeForecast(3, resetAt), settings: settingsOn(), state: state, nowMs: MS, localMinutes: 600 });
  assert.equal(first.decision, 'notify');
  p.applyResult(state, Object.assign({}, first.notifications[0], { nowMs: MS }), 'shown');

  const second = p.planTick({ forecast: sessionLimitBeforeForecast(0.5, resetAt), settings: settingsOn(), state: state, nowMs: MS + 1800000, localMinutes: 600 });
  assert.equal(second.decision, 'notify');
  assert.equal(second.notifications[0].time_to_limit_hours, 0.5);
});

// ---- applyResult -----------------------------------------------------------

test('applyResult only marks notified for a shown result', () => {
  const state = { events: [] };
  const event = { event_type: 'limit_before_reset', window: 'session', status: 'limit_before_reset', severity: 'warning', time_to_limit_hours: 1, reset_at: MS + 3600000 };
  const shown = p.applyResult(state, Object.assign({}, event, { nowMs: MS }), 'shown');
  assert.equal(shown.last_notified_at, MS);
  assert.equal(shown.last_result, 'shown');
});

test('a failed result records an attempt but never marks notified', () => {
  const state = { events: [] };
  const event = { event_type: 'limit_before_reset', window: 'session', status: 'limit_before_reset', severity: 'warning', time_to_limit_hours: 1, reset_at: MS + 3600000 };
  const failed = p.applyResult(state, Object.assign({}, event, { nowMs: MS + 1000 }), 'failed');
  assert.equal(failed.last_notified_at, null);
  assert.equal(failed.last_result, 'failed');
  assert.equal(failed.attempts, 1);
});

test('a suppressed result records suppressed_at without marking notified', () => {
  const state = { events: [] };
  const event = { event_type: 'already_at_limit', window: 'weekly', status: 'already_at_limit', severity: 'critical', time_to_limit_hours: 0, reset_at: null };
  const entry = p.applyResult(state, Object.assign({}, event, { nowMs: MS }), 'suppressed');
  assert.equal(entry.last_notified_at, null);
  assert.equal(entry.last_result, 'suppressed');
  assert.equal(entry.suppressed_at, MS);
});

// ---- settings validation ---------------------------------------------------

test('validateSettingsPayload accepts a valid patch and returns it', () => {
  const r = p.validateSettingsPayload({ enabled: true, quiet_hours: { enabled: true, start: '22:30', end: '07:15' } });
  assert.equal(r.ok, true);
  assert.deepEqual(r.value, { enabled: true, quiet_hours: { enabled: true, start: '22:30', end: '07:15' } });
});

test('validateSettingsPayload rejects non-HH:MM and long strings', () => {
  assert.deepEqual(p.validateSettingsPayload({ quiet_hours: { start: '25:00' } }).ok, false);
  assert.deepEqual(p.validateSettingsPayload({ quiet_hours: { start: '8:00' } }).ok, false);
  assert.deepEqual(p.validateSettingsPayload({ quiet_hours: { end: 'noon' } }).ok, false);
  assert.equal(p.validateSettingsPayload({ quiet_hours: { start: '0'.repeat(200) + ':00' } }).error, 'too_long');
});

test('validateSettingsPayload rejects unknown fields and wrong types', () => {
  assert.equal(p.validateSettingsPayload({ foo: 1 }).error, 'unknown_field:foo');
  assert.equal(p.validateSettingsPayload({ quiet_hours: { foo: 1 } }).error, 'unknown_field:quiet_hours.foo');
  assert.equal(p.validateSettingsPayload({ enabled: 1 }).error, 'invalid_enabled');
  assert.equal(p.validateSettingsPayload({ quiet_hours: { enabled: 'yes' } }).error, 'invalid_quiet_hours.enabled');
  assert.equal(p.validateSettingsPayload(null).ok, false);
  assert.equal(p.validateSettingsPayload('nope').ok, false);
  assert.equal(p.validateSettingsPayload({ enabled: false }).ok, true);
});

test('normalizeSettings merges a patch over the current settings', () => {
  const merged = p.normalizeSettings(p.DEFAULT_SETTINGS, { enabled: true, quiet_hours: { start: '21:00' } });
  assert.deepEqual(merged, {
    enabled: true,
    quiet_hours: { enabled: false, start: '21:00', end: '08:00' },
  });
  // never returns the shared default object
  assert.notEqual(merged, p.DEFAULT_SETTINGS);
});

// ---- wording ---------------------------------------------------------------

function allNotificationText() {
  const warning = p.buildNotification(
    { event_type: 'limit_before_reset', window: 'session', status: 'limit_before_reset', severity: 'warning', time_to_limit_hours: 2.5, reset_at: MS },
    { ttlHours: 2.5, resetAt: MS, nowMs: MS }
  );
  const critical = p.buildNotification(
    { event_type: 'already_at_limit', window: 'weekly', status: 'already_at_limit', severity: 'critical', time_to_limit_hours: 0, reset_at: MS },
    { ttlHours: 0, resetAt: MS, nowMs: MS }
  );
  return [warning, critical];
}

test('buildNotification hedges and names the quota window + estimate', () => {
  const [warning] = allNotificationText();
  assert.equal(warning.title, 'OpenCode Widget');
  assert.ok(/5h session/.test(warning.body), warning.body);
  assert.ok(/may reach/i.test(warning.body), warning.body);
  assert.ok(/estimated/i.test(warning.body), warning.body);
  assert.ok(/2\.5 hours/.test(warning.body), warning.body);
});

test('buildNotification critical wording states the limit is reached', () => {
  const [, critical] = allNotificationText();
  assert.equal(critical.title, 'OpenCode usage warning');
  assert.ok(/limit has been reached/i.test(critical.body), critical.body);
  assert.ok(/weekly/.test(critical.body), critical.body);
});

test('notification wording never asserts certainty and carries no identifiers', () => {
  for (const note of allNotificationText()) {
    const text = note.title + ' ' + note.body;
    assert.ok(!/will|certain|guaranteed|definitely/i.test(text), text);
    for (const bad of ['ses_', 'wrk_', 'workspace', 'agent', 'model', 'prompt']) {
      assert.ok(!text.toLowerCase().includes(bad), bad + ' in ' + text);
    }
  }
});
