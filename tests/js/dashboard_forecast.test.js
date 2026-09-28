// Phase 4: Forecast dashboard tab suite.
// The file loads format.js -> core.js (DOM-free phase5a-core block) -> views.js
// -> forecast.js, evals them in one scope, and asserts the pure view-model
// contract. forecast.js is DOM-free at load, so eval works without a DOM; we
// inject a stub OCW to observe tab registration.
//   node --test tests/js/dashboard_forecast.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FORMAT_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'format.js');
const CORE_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'core.js');
const VIEWS_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'views.js');
const FORECAST_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'forecast.js');
const FORMAT_SRC = fs.readFileSync(FORMAT_JS, 'utf8');
const CORE_SRC = fs.readFileSync(CORE_JS, 'utf8');
const VIEWS_SRC = fs.readFileSync(VIEWS_JS, 'utf8');
const FORECAST_SRC = fs.readFileSync(FORECAST_JS, 'utf8');

function extractMarked(src, startMarker, endMarker, label) {
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker);
  assert.ok(start >= 0, label + ':start marker not found');
  assert.ok(end > start, label + ':end marker not found');
  return src.slice(start, end + endMarker.length);
}

const CORE_BLOCK = extractMarked(
  CORE_SRC, '/* ==== phase5a-core:start ====', '/* ==== phase5a-core:end ==== */', 'phase5a-core');

const registered = {};
const OCW = {
  tabs: registered,
  registerTab: function (name, api) { registered[name] = api; },
};

const NAMES = [
  'DASH_TABS', 'DASH_ENDPOINTS', 'dashCacheKey', 'dashEndpointsFor',
  'FORECAST_DISCLAIMER', 'FORECAST_STATUS_LABELS',
  'forecastStatusLabel', 'forecastResetSourceLabel', 'forecastRateModel',
  'forecastNum', 'forecastFormatNumber', 'forecastEstimateModel',
  'forecastOfficialModel', 'forecastSections', 'forecastSectionHtml',
  'forecastHtml', 'obsRenderForecast', 'formatDateTime', 'formatDuration',
];

// eslint-disable-next-line no-eval
const f = eval(
  FORMAT_SRC
  + '\n' + CORE_BLOCK
  + '\n' + VIEWS_SRC
  + '\n' + FORECAST_SRC
  + '\n({' + NAMES.join(',') + '})'
);

const MS = 1700000000000;

function rateOk(o) {
  return {
    status: 'ok', usage: o.usage, duration_hours: o.duration_hours,
    usage_per_hour: o.usage_per_hour, usage_per_day: o.usage_per_day,
    sample_points: o.sample_points, window_ms: 1800000, first_ts: MS, last_ts: MS,
  };
}
function rateInsufficient() {
  return {
    status: 'insufficient_data', usage: 5, duration_hours: null,
    usage_per_hour: null, usage_per_day: null, sample_points: 1,
    window_ms: null, first_ts: MS, last_ts: MS,
  };
}
function rateNoUsage() {
  return {
    status: 'no_usage', usage: 0, duration_hours: 0, usage_per_hour: null,
    usage_per_day: null, sample_points: 0, window_ms: null, first_ts: null, last_ts: null,
  };
}
function estResetBefore() {
  return {
    status: 'reset_before_limit', time_to_limit_hours: null, raw_time_to_limit_hours: null,
    estimated_limit_at: null, reset_at: MS + 3600000, projected_usage_at_reset: 30,
    projected_remaining_at_reset: 70, would_exceed_limit: false,
  };
}
function estLimitBefore() {
  return {
    status: 'limit_before_reset', time_to_limit_hours: 0.5, raw_time_to_limit_hours: 0.6,
    estimated_limit_at: MS + 1800000, reset_at: MS + 10800000,
    projected_usage_at_reset: 130, projected_remaining_at_reset: -30, would_exceed_limit: true,
  };
}
function estInsufficient() {
  return {
    status: 'insufficient_data', time_to_limit_hours: null, raw_time_to_limit_hours: null,
    estimated_limit_at: null, reset_at: null, projected_usage_at_reset: null,
    projected_remaining_at_reset: null, would_exceed_limit: null,
  };
}
function official(over) {
  return Object.assign({
    status: 'ok', used: 42.5, remaining: 57.5, limit: 100,
    reset_at: MS + 3600000, reset_source: 'official_reset_text',
  }, over);
}

const FIXTURE = {
  type: 'estimate',
  generated_at: MS,
  basis: 'official_quota_usage_rate',
  reader: { status: 'ok', schema: 'current' },
  session: {
    official: official({}),
    rates: {
      last_30m: rateOk({ usage: 10, duration_hours: 0.5, usage_per_hour: 20, usage_per_day: 480, sample_points: 12 }),
      last_60m: rateInsufficient(),
      window_avg: rateNoUsage(),
    },
    estimates: { last_30m: estResetBefore(), last_60m: estLimitBefore(), window_avg: estInsufficient() },
  },
  weekly: {
    official: official({ status: 'unavailable', used: null, remaining: null, limit: null, reset_at: null, reset_source: 'unknown' }),
    rates: {
      last_24h: rateOk({ usage: 100, duration_hours: 24, usage_per_hour: 4.17, usage_per_day: 100, sample_points: 200 }),
      last_3d: rateOk({ usage: 300, duration_hours: 72, usage_per_hour: 4.17, usage_per_day: 100, sample_points: 600 }),
      last_7d: rateInsufficient(),
    },
    estimates: { last_24h: estResetBefore(), last_3d: estInsufficient(), last_7d: estInsufficient() },
  },
  period: {
    official: official({}),
    rates: {
      daily_average: rateOk({ usage: 12, duration_hours: 24, usage_per_hour: 0.5, usage_per_day: 12, sample_points: 100 }),
      recent_3d_average: rateOk({ usage: 14, duration_hours: 72, usage_per_hour: 0.19, usage_per_day: 4.67, sample_points: 300 }),
      recent_7d_average: rateNoUsage(),
    },
    estimates: {
      daily_average: {
        status: 'ok', projected_usage_at_end: 88, projected_remaining_at_end: 12,
        would_exceed_limit: false, period_end_at: MS + 86400000,
      },
      recent_3d_average: {
        status: 'no_usage', projected_usage_at_end: null, projected_remaining_at_end: null,
        would_exceed_limit: null, period_end_at: null,
      },
      recent_7d_average: {
        status: 'insufficient_data', projected_usage_at_end: null, projected_remaining_at_end: null,
        would_exceed_limit: null, period_end_at: null,
      },
    },
  },
};

// --- wiring -----------------------------------------------------------------

test('DASH_TABS has five entries including forecast', () => {
  assert.deepEqual(f.DASH_TABS, ['overview', 'agents', 'models', 'sessions', 'forecast']);
});

test('forecast endpoints + range-independent cache key', () => {
  assert.deepEqual(f.dashEndpointsFor('forecast'), ['forecast']);
  assert.equal(f.dashCacheKey('forecast', 'today'), 'forecast');
  assert.equal(f.dashCacheKey('forecast', '7d'), 'forecast');
  assert.equal(f.dashCacheKey('forecast', 'all'), 'forecast');
});

test('forecast.js registers the tab at load without calling the API', () => {
  assert.ok(registered.forecast, 'forecast tab not registered');
  assert.equal(typeof registered.forecast.target, 'function');
  assert.equal(typeof registered.forecast.render, 'function');
  // No API call at load: nothing in the source touches widgetAPI/apiGet*.
  assert.ok(!/widgetAPI|apiGetForecast/.test(FORECAST_SRC));
});

// --- labels + disclaimer ----------------------------------------------------

test('forecastStatusLabel maps every contract status', () => {
  const expected = {
    unavailable: 'Unavailable',
    insufficient_data: 'Insufficient data',
    no_usage: 'No usage',
    already_at_limit: 'Already at limit',
    reset_unknown: 'Reset time unknown',
    reset_before_limit: 'Estimated to stay under limit (resets first)',
    limit_before_reset: 'Estimated to reach limit before reset',
    ok: 'OK',
  };
  for (const key of Object.keys(expected)) {
    assert.equal(f.forecastStatusLabel(key), expected[key], key);
  }
  assert.equal(f.forecastStatusLabel('nope'), 'Unknown');
  assert.equal(f.forecastStatusLabel(null), 'Unknown');
  assert.equal(f.forecastStatusLabel(undefined), 'Unknown');
});

test('status labels never imply certainty', () => {
  const statuses = Object.keys(f.FORECAST_STATUS_LABELS).concat(['nope', null, undefined, 42]);
  for (const s of statuses) {
    const label = f.forecastStatusLabel(s);
    assert.ok(!/certain|guaranteed|必然/i.test(label), String(s) + ' -> ' + label);
    assert.ok(!/official prediction/i.test(label), String(s) + ' -> ' + label);
  }
});

test('FORECAST_DISCLAIMER states estimates / not guaranteed', () => {
  assert.ok(/estimate/i.test(f.FORECAST_DISCLAIMER), f.FORECAST_DISCLAIMER);
  assert.ok(/not guaranteed/i.test(f.FORECAST_DISCLAIMER), f.FORECAST_DISCLAIMER);
  assert.ok(!/official prediction/i.test(FORECAST_SRC));
});

// --- rate model -------------------------------------------------------------

test('forecastRateModel formats usage, explicit units and sample text', () => {
  const m = f.forecastRateModel({
    status: 'ok', usage: 100, duration_hours: 2,
    usage_per_hour: 50, usage_per_day: 1200, sample_points: 12,
  });
  assert.equal(m.statusLabel, 'OK');
  assert.equal(m.usageText, '100');
  assert.equal(m.rateText, '50 /h · 1,200 /day');
  assert.ok(m.rateText.includes('/h'));
  assert.ok(m.rateText.includes('/day'));
  assert.equal(m.sampleText, 'Based on 12 usage records over 2 hours.');
});

test('forecastRateModel renders — for nulls and still labels the row', () => {
  const m = f.forecastRateModel({
    status: 'no_usage', usage: null, duration_hours: null,
    usage_per_hour: null, usage_per_day: null, sample_points: null,
  });
  assert.equal(m.statusLabel, 'No usage');
  assert.equal(m.usageText, '—');
  assert.equal(m.rateText, '—');
  assert.equal(m.sampleText, 'Based on — usage records over — hours.');
});

// --- sections ---------------------------------------------------------------

test('forecastSections returns three fixed sections in contract order', () => {
  const sections = f.forecastSections(FIXTURE);
  assert.equal(sections.length, 3);
  assert.deepEqual(sections.map((s) => s.key), ['session', 'weekly', 'period']);
  assert.deepEqual(sections.map((s) => s.title), ['5h Session', 'Weekly', 'Current Period']);
  for (const s of sections) {
    assert.equal(s.rates.length, 3, s.key + ' must show all three rates');
    assert.equal(s.estimates.length, 3, s.key + ' must show all three estimates');
  }
});

test('forecastSections exposes official used/remaining/reset', () => {
  const officialModel = f.forecastSections(FIXTURE)[0].official;
  assert.equal(officialModel.statusLabel, 'OK');
  assert.equal(officialModel.usedText, '42.5');
  assert.equal(officialModel.remainingText, '57.5');
  assert.equal(officialModel.limitText, '100');
  assert.equal(officialModel.resetText, f.formatDateTime(MS + 3600000));
  assert.equal(officialModel.resetSourceText, 'Official reset text');
});

test('forecastSections rate rows carry label, status, usage and explicit units', () => {
  const session = f.forecastSections(FIXTURE)[0];
  assert.deepEqual(session.rates.map((r) => r.label), ['Last 30m', 'Last 60m', 'Window average']);
  assert.deepEqual(session.rates.map((r) => r.statusLabel), ['OK', 'Insufficient data', 'No usage']);
  assert.equal(session.rates[0].usageText, '10');
  assert.equal(session.rates[0].rateText, '20 /h · 480 /day');
  assert.equal(session.rates[0].sampleText, 'Based on 12 usage records over 0.5 hours.');
  // insufficient / no_usage still surface a row rather than being dropped
  assert.equal(session.rates[1].statusLabel, 'Insufficient data');
  assert.equal(session.rates[2].statusLabel, 'No usage');
});

test('forecastSections estimate rows include projected/time-to-limit/status', () => {
  const session = f.forecastSections(FIXTURE)[0];
  assert.deepEqual(session.estimates.map((e) => e.statusLabel),
    ['Estimated to stay under limit (resets first)', 'Estimated to reach limit before reset', 'Insufficient data']);
  const resetBefore = session.estimates[0];
  assert.equal(resetBefore.projectedUsageText, '30');
  assert.equal(resetBefore.projectedRemainingText, '70');
  assert.equal(resetBefore.timeToLimitText, '—'); // null -> —
  assert.equal(resetBefore.wouldExceedText, 'No');
  const limitBefore = session.estimates[1];
  assert.equal(limitBefore.timeToLimitText, '30m'); // 0.5h via formatDuration
  assert.equal(limitBefore.estimatedLimitAtText, f.formatDateTime(MS + 1800000));
  assert.equal(limitBefore.wouldExceedText, 'Yes');
  const insufficient = session.estimates[2];
  assert.equal(insufficient.projectedUsageText, '—');
  assert.equal(insufficient.projectedRemainingText, '—');
  assert.equal(insufficient.timeToLimitText, '—');
});

test('forecastSections period estimate uses period_end_at + projected_*_at_end', () => {
  const period = f.forecastSections(FIXTURE)[2];
  const first = period.estimates[0];
  assert.equal(first.statusLabel, 'OK');
  assert.equal(first.projectedUsageText, '88');
  assert.equal(first.projectedRemainingText, '12');
  assert.equal(first.wouldExceedText, 'No');
  assert.ok(first.resetAtText && first.resetAtText !== '—', first.resetAtText);
  assert.equal(period.estimates[1].projectedUsageText, '—');
});

test('forecastSections is null-safe for an empty payload', () => {
  const sections = f.forecastSections(null);
  assert.equal(sections.length, 3);
  for (const s of sections) {
    assert.equal(s.official.usedText, '—');
    assert.equal(s.official.resetText, '—');
    assert.equal(s.rates.length, 3);
    assert.equal(s.rates[0].usageText, '—');
    assert.equal(s.rates[0].rateText, '—');
    assert.equal(s.estimates[0].projectedUsageText, '—');
  }
});

// --- render / escaping ------------------------------------------------------

test('forecastHtml includes the disclaimer and all three sections', () => {
  const html = f.forecastHtml(FIXTURE);
  assert.ok(html.includes(f.FORECAST_DISCLAIMER));
  assert.ok(html.indexOf(f.FORECAST_DISCLAIMER) < html.indexOf('5h Session'), 'disclaimer must be at the top');
  assert.ok(html.includes('5h Session') && html.includes('Weekly') && html.includes('Current Period'));
  assert.ok(html.includes('data-forecast-section="session"'));
  assert.ok(html.includes('data-forecast-section="period"'));
});

test('forecastHtml escapes dynamic backend strings', () => {
  const evil = {
    type: '<img src=x onerror=alert(1)>',
    basis: '"><svg onload=alert(1)>',
    generated_at: MS,
    session: { official: official({ reset_source: '<script>alert(1)</script>' }), rates: {}, estimates: {} },
  };
  const html = f.forecastHtml(evil);
  assert.ok(!/<img/i.test(html), 'raw <img> survived');
  assert.ok(!/<svg/i.test(html), 'raw <svg> survived');
  assert.ok(!/<script/i.test(html), 'raw <script> survived');
  assert.ok(html.includes('&lt;'), 'no escaped < found');
  assert.ok(html.includes('&quot;'), 'no escaped " found');
});

test('obsRenderForecast never throws without a DOM and shows empty state', () => {
  const fake = { innerHTML: '' };
  f.obsRenderForecast(fake, {});
  assert.ok(fake.innerHTML.includes('No forecast data.'));
  assert.ok(fake.innerHTML.includes(f.FORECAST_DISCLAIMER));
  f.obsRenderForecast(null, FIXTURE); // no body -> no throw
});
