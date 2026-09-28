// Phase 5B: Usage Timeline suite.
// Loads format.js -> core block -> charts.js -> timeline.js in one scope and
// asserts the pure metric/state/scale contract. timeline.js is DOM-free at load
// (its window/document setup is guarded), so eval works with no DOM.
//   node --test tests/js/timeline.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const D = (...p) => path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', ...p);
const FORMAT_SRC = fs.readFileSync(D('format.js'), 'utf8');
const CORE_SRC = fs.readFileSync(D('core.js'), 'utf8');
const CHARTS_SRC = fs.readFileSync(D('charts.js'), 'utf8');
const TIMELINE_SRC = fs.readFileSync(D('timeline.js'), 'utf8');

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
  'TIMELINE_METRICS', 'TIMELINE_EMPTY_TEXT', 'TIMELINE_ERROR_TEXT',
  'TIMELINE_UNSUPPORTED_TEXT', 'TIMELINE_LOADING_TEXT', 'TIMELINE_RAW_COST_NOTE',
  'metricLabel', 'bucketMetric', 'timelineSeries', 'timelineSummary',
  'timelineHasUsage', 'timelineViewState', 'timelineStateMessage',
  'timelineMetricSwitch', 'timelineFormatValue', 'timelineTickLabel',
  'niceMax', 'scalePoints', 'linePath', 'areaPath',
];

// eslint-disable-next-line no-eval
const t = eval(
  FORMAT_SRC
  + '\n' + CORE_BLOCK
  + '\n' + CHARTS_SRC
  + '\n' + TIMELINE_SRC
  + '\n({' + NAMES.join(',') + '})'
);

const MS = 1700000000000;

function bucket(start, over) {
  return Object.assign({
    start: start, end: start + 3600000,
    requests: 0,
    tokens: { total: 0, input: 0, output: 0, cache_read: 0, cache_write: 0 },
    cost: 0,
  }, over || {});
}

const SERIES = [
  bucket(MS, { requests: 5, tokens: { total: 100 }, cost: 0.5 }),
  bucket(MS + 3600000, { requests: 0, tokens: { total: 0 }, cost: 0 }),
  bucket(MS + 7200000, { requests: 3, tokens: { total: 50 }, cost: 0.25 }),
];

// --- metrics ----------------------------------------------------------------

test('TIMELINE_METRICS has requests/tokens/cost', () => {
  assert.deepEqual(t.TIMELINE_METRICS, ['requests', 'tokens', 'cost']);
});

test('metricLabel maps Requests/Tokens/Raw Cost', () => {
  assert.equal(t.metricLabel('requests'), 'Requests');
  assert.equal(t.metricLabel('tokens'), 'Tokens');
  assert.equal(t.metricLabel('cost'), 'Raw Cost');
  assert.equal(t.metricLabel('nope'), 'Requests');
});

test('bucketMetric reads requests, tokens.total and cost', () => {
  const b = bucket(MS, { requests: 7, tokens: { total: 42, input: 1, output: 2 }, cost: 0.75 });
  assert.equal(t.bucketMetric(b, 'requests'), 7);
  assert.equal(t.bucketMetric(b, 'tokens'), 42);
  assert.equal(t.bucketMetric(b, 'cost'), 0.75);
  assert.equal(t.bucketMetric(null, 'tokens'), null);
  assert.equal(t.bucketMetric(b, 'nope'), null);
});

test('timelineSeries maps the metric with missing -> 0 and keeps length', () => {
  assert.deepEqual(t.timelineSeries(SERIES, 'requests'), [5, 0, 3]);
  assert.deepEqual(t.timelineSeries(SERIES, 'tokens'), [100, 0, 50]);
  assert.deepEqual(t.timelineSeries(SERIES, 'cost'), [0.5, 0, 0.25]);
  assert.deepEqual(t.timelineSeries([], 'tokens'), []);
  assert.deepEqual(t.timelineSeries(null, 'tokens'), []);
});

test('timelineSummary sums API-provided numbers', () => {
  const s = t.timelineSummary(SERIES);
  assert.equal(s.requests, 8);
  assert.equal(s.tokens, 150);
  assert.equal(s.cost, 0.75);
  const empty = t.timelineSummary(null);
  assert.deepEqual(empty, { requests: 0, tokens: 0, cost: 0 });
});

test('timelineFormatValue uses shared display formatters', () => {
  assert.equal(t.timelineFormatValue(12400, 'tokens'), '12.4K');
  assert.equal(t.timelineFormatValue(0.5, 'cost'), '$0.500');
  assert.equal(t.timelineFormatValue(1234, 'requests'), '1,234');
});

// --- states -----------------------------------------------------------------

test('timelineViewState distinguishes ok/empty/error/unsupported/loading', () => {
  assert.equal(t.timelineViewState({ series: SERIES, reader: { status: 'ok' } }), 'ok');
  assert.equal(t.timelineViewState({ state: 'loading' }), 'loading');
  assert.equal(t.timelineViewState({ series: [], reader: { status: 'ok' } }), 'empty');
  assert.equal(t.timelineViewState({ series: [bucket(MS)] , reader: { status: 'ok' } }), 'empty');
  assert.equal(t.timelineViewState({ series: SERIES, error: true }), 'error');
  assert.equal(t.timelineViewState({ series: SERIES, reader: { status: 'unsupported_schema' } }), 'unsupported');
  assert.equal(t.timelineViewState({ series: SERIES, reader: { status: 'missing_db' } }), 'error');
});

test('timelineStateMessage returns the exact contracted strings', () => {
  assert.equal(t.timelineStateMessage('empty'), 'No usage in this range.');
  assert.equal(t.timelineStateMessage('error'), 'Timeline unavailable.');
  assert.equal(
    t.timelineStateMessage('unsupported'),
    'Timeline unavailable because the OpenCode usage schema is not supported.');
  assert.equal(t.timelineStateMessage('loading'), 'Loading…');
  assert.equal(t.timelineStateMessage('ok'), '');
});

test('timelineHasUsage ignores all-zero buckets', () => {
  assert.equal(t.timelineHasUsage(SERIES), true);
  assert.equal(t.timelineHasUsage([bucket(MS), bucket(MS + 1)]), false);
});

// --- metric switch ----------------------------------------------------------

test('timelineMetricSwitch re-renders without refetching', () => {
  assert.deepEqual(t.timelineMetricSwitch('tokens', 'cost'), { metric: 'cost', refetch: false });
  assert.deepEqual(t.timelineMetricSwitch('tokens', 'bogus'), { metric: 'tokens', refetch: false });
  assert.equal(t.timelineMetricSwitch('tokens', 'requests').refetch, false);
});

// --- chart helpers ----------------------------------------------------------

test('niceMax returns a nice upper bound', () => {
  assert.equal(t.niceMax(9), 10);
  assert.equal(t.niceMax(23), 25);
  assert.equal(t.niceMax(100), 100);
  assert.equal(t.niceMax(0.5), 0.5);
  assert.equal(t.niceMax(0), 0);
  assert.equal(t.niceMax(-5), 0);
});

test('scalePoints spreads x, inverts y and clamps display only', () => {
  const pts = t.scalePoints([0, 10], 10, 100, 50);
  assert.equal(pts.length, 2);
  assert.equal(pts[0].x, 0);
  assert.equal(pts[1].x, 100);
  assert.equal(pts[0].y, 50);
  assert.equal(pts[1].y, 0);
  assert.equal(pts[1].v, 10);
  const over = t.scalePoints([25], 10, 100, 50);
  assert.equal(over[0].y, 0); // clamped for drawing
  assert.equal(over[0].v, 25); // raw value preserved
  const zero = t.scalePoints([0, 0], 0, 100, 50);
  assert.equal(zero[0].y, 50);
  assert.deepEqual(t.scalePoints([], 10, 100, 50), []);
});

test('linePath and areaPath build SVG path strings', () => {
  const pts = [{ x: 0, y: 50 }, { x: 100, y: 0 }];
  assert.equal(t.linePath(pts), 'M0,50 L100,0');
  assert.equal(t.linePath([]), '');
  assert.equal(t.areaPath(pts, 50), 'M0,50 L100,0 L100,50 L0,50 Z');
  assert.equal(t.areaPath([], 50), '');
});

test('timelineTickLabel formats per bucket unit', () => {
  const d = new Date(2026, 0, 2, 3, 4, 0, 0);
  assert.equal(t.timelineTickLabel(d.getTime(), 'hour'), '03:04');
  assert.equal(t.timelineTickLabel(d.getTime(), 'day'), '01-02');
  assert.equal(t.timelineTickLabel(null, 'hour'), '');
});

test('raw cost note states it is not official quota consumption', () => {
  assert.ok(t.TIMELINE_RAW_COST_NOTE.includes('Raw cost recorded in OpenCode message data.'));
  assert.ok(t.TIMELINE_RAW_COST_NOTE.includes('not equivalent to official OpenCode Go quota consumption'));
});
