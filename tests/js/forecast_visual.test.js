// Phase 5B: compact forecast-bar suite.
// Loads format.js -> core block -> views.js -> forecast.js and asserts the pure
// forecastBars view-model plus the escaped bar markup. forecast.js stays
// DOM-free at load, so eval works with no DOM.
//   node --test tests/js/forecast_visual.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const D = (...p) => path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', ...p);
const FORMAT_SRC = fs.readFileSync(D('format.js'), 'utf8');
const CORE_SRC = fs.readFileSync(D('core.js'), 'utf8');
const VIEWS_SRC = fs.readFileSync(D('views.js'), 'utf8');
const FORECAST_SRC = fs.readFileSync(D('forecast.js'), 'utf8');

function extractMarked(src, startMarker, endMarker, label) {
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker);
  assert.ok(start >= 0, label + ':start marker not found');
  assert.ok(end > start, label + ':end marker not found');
  return src.slice(start, end + endMarker.length);
}
const CORE_BLOCK = extractMarked(
  CORE_SRC, '/* ==== phase5a-core:start ====', '/* ==== phase5a-core:end ==== */', 'phase5a-core');

const NAMES = [
  'FORECAST_DISCLAIMER', 'FORECAST_SEVERITY_COLORS', 'forecastStatusSeverity',
  'forecastBars', 'forecastBarHtml', 'forecastHtml', 'FORECAST_SECTION_DEFS',
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

function official(over) {
  return Object.assign({
    status: 'ok', used: 40, remaining: 60, limit: 100,
    reset_at: MS + 3600000, reset_source: 'official_reset_text',
  }, over);
}

function est(over) {
  return Object.assign({
    status: 'ok', time_to_limit_hours: 3, estimated_limit_at: MS + 7200000,
    projected_usage_at_reset: 50, projected_remaining_at_reset: 50, would_exceed_limit: false,
  }, over);
}

function flow(officialOver, estimate, estKey) {
  const estimates = {};
  estimates[estKey || 'last_30m'] = est(estimate || {});
  return { official: official(officialOver || {}), rates: {}, estimates };
}

function forecast(over) {
  return Object.assign({
    type: 'estimate', basis: 'official_quota_usage_rate', generated_at: MS,
    session: flow(),
    weekly: flow(),
    period: { official: official({}), rates: {}, estimates: { daily_average: est() } },
  }, over || {});
}

function barFor(data, key) {
  return f.forecastBars(data).find((b) => b.key === key);
}

// --- shape ------------------------------------------------------------------

test('forecastBars returns all three windows in contract order', () => {
  const bars = f.forecastBars(forecast());
  assert.deepEqual(bars.map((b) => b.key), ['session', 'weekly', 'period']);
  assert.equal(bars.length, 3);
});

test('forecastBars is null-safe', () => {
  const bars = f.forecastBars(null);
  assert.equal(bars.length, 3);
  assert.equal(bars[0].status, null);
  assert.equal(bars[0].officialUnavailable, false);
});

// --- severity / statuses ----------------------------------------------------

test('forecastStatusSeverity maps the notification vocabulary', () => {
  assert.equal(f.forecastStatusSeverity('already_at_limit'), 'critical');
  assert.equal(f.forecastStatusSeverity('limit_before_reset'), 'warning');
  assert.equal(f.forecastStatusSeverity('reset_before_limit'), 'neutral');
  assert.equal(f.forecastStatusSeverity('insufficient_data'), 'neutral');
  assert.equal(f.forecastStatusSeverity('unavailable'), 'neutral');
  assert.equal(f.forecastStatusSeverity(null), 'neutral');
  assert.equal(f.FORECAST_SEVERITY_COLORS.critical, '#ef4444');
  assert.equal(f.FORECAST_SEVERITY_COLORS.warning, '#f59e0b');
});

test('limit_before_reset bar is warning with projected %', () => {
  const data = forecast({ session: flow({}, { status: 'limit_before_reset', projected_usage_at_reset: 130 }) });
  const bar = barFor(data, 'session');
  assert.equal(bar.status, 'limit_before_reset');
  assert.equal(bar.severity, 'warning');
  assert.equal(bar.projectedPct, 130);
});

test('reset_before_limit bar is neutral', () => {
  const data = forecast({ session: flow({}, { status: 'reset_before_limit', projected_usage_at_reset: 30 }) });
  const bar = barFor(data, 'session');
  assert.equal(bar.status, 'reset_before_limit');
  assert.equal(bar.severity, 'neutral');
  assert.equal(bar.projectedPct, 30);
});

test('already_at_limit bar is critical', () => {
  const data = forecast({ weekly: flow({}, { status: 'already_at_limit', projected_usage_at_reset: 140 }, 'last_24h') });
  const bar = barFor(data, 'weekly');
  assert.equal(bar.status, 'already_at_limit');
  assert.equal(bar.severity, 'critical');
});

test('insufficient_data bar is neutral', () => {
  const data = forecast({ period: { official: official({}), rates: {}, estimates: { daily_average: est({ status: 'insufficient_data', projected_usage_at_reset: null, projected_usage_at_end: null }) } } });
  const bar = barFor(data, 'period');
  assert.equal(bar.status, 'insufficient_data');
  assert.equal(bar.severity, 'neutral');
  assert.equal(bar.projectedPct, null);
});

test('official unavailable shows the message, never a fake 0%', () => {
  const data = forecast({ session: flow({ status: 'unavailable', used: null, remaining: null, limit: null, reset_at: null }) });
  const bar = barFor(data, 'session');
  assert.equal(bar.officialUnavailable, true);
  assert.equal(bar.severity, 'neutral');
  assert.equal(bar.usedPct, null);
  const html = f.forecastBarHtml(bar);
  assert.ok(html.includes('Official quota state unavailable'));
  assert.ok(!html.includes('0%'));
});

// --- projection not clamped -------------------------------------------------

test('projected > limit is not clamped', () => {
  const data = forecast({ session: flow({}, { status: 'limit_before_reset', projected_usage_at_reset: 175 }) });
  const bar = barFor(data, 'session');
  assert.equal(bar.projectedPct, 175);
  assert.ok(bar.projectedPct > 100);
  const html = f.forecastBarHtml(bar);
  assert.ok(html.includes('175%') === false); // width is clamped in markup only
  assert.ok(html.includes('width:100%'));
});

// --- markup -----------------------------------------------------------------

test('forecastHtml keeps the disclaimer and adds a bar per section', () => {
  const html = f.forecastHtml(forecast());
  assert.ok(html.includes(f.FORECAST_DISCLAIMER));
  assert.equal((html.match(/data-forecast-bar=/g) || []).length, 3);
  assert.ok(html.includes('data-forecast-bar="session"'));
  assert.ok(html.includes('data-forecast-bar="period"'));
  assert.ok(html.includes('Estimate:'));
});

test('forecastBarHtml escapes dynamic labels', () => {
  const evil = {
    key: 'session', severity: 'critical', statusLabel: '<img src=x onerror=alert(1)>',
    usedPct: 10, projectedPct: 20,
    labels: { used: '<script>alert(1)</script>', limit: '"><svg onload=alert(1)>', reset: 'x', projected: 'y' },
  };
  const html = f.forecastBarHtml(evil);
  assert.ok(!/<img/i.test(html), 'raw <img> survived');
  assert.ok(!/<svg/i.test(html), 'raw <svg> survived');
  assert.ok(!/<script/i.test(html), 'raw <script> survived');
  assert.ok(html.includes('&lt;'), 'no escaped < found');
});
