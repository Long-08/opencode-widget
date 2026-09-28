// Phase 5B: Agent x Model matrix suite.
// Loads format.js -> core block -> charts.js -> timeline.js -> matrix.js and
// asserts the pure matrix view-model. matrix.js is DOM-free at load (guarded).
//   node --test tests/js/matrix.test.js
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
const MATRIX_SRC = fs.readFileSync(D('matrix.js'), 'utf8');

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
  'MATRIX_METRICS', 'MATRIX_TOP_N', 'matrixLabel', 'matrixColKey',
  'matrixCellValue', 'intensity', 'topN', 'matrixTruncationNote',
  'matrixMetricSwitch', 'matrixFromAgents',
];

// eslint-disable-next-line no-eval
const m = eval(
  FORMAT_SRC
  + '\n' + CORE_BLOCK
  + '\n' + CHARTS_SRC
  + '\n' + TIMELINE_SRC
  + '\n' + MATRIX_SRC
  + '\n({' + NAMES.join(',') + '})'
);

const M1 = 'm1|p|v1';
const M2 = 'm2|p|';

function model(over) {
  return Object.assign({ model: 'm1', provider: 'p', variant: 'v1', requests: 1, tokens: { total: 1 }, cost: 0.1 }, over);
}

const AGENTS = [
  { agent: 'alpha', group: 'core', models: [
    model({ requests: 3, tokens: { total: 30 }, cost: 1.5 }),
    model({ model: 'm2', variant: null, requests: 1, tokens: { total: 5 }, cost: 0.5 }),
  ] },
  { agent: 'unknown', group: null, models: [
    model({ requests: 2, tokens: { total: 10 }, cost: 0.25 }),
  ] },
];

test('MATRIX_METRICS has requests/tokens/cost', () => {
  assert.deepEqual(m.MATRIX_METRICS, ['requests', 'tokens', 'cost']);
});

test('matrixCellValue reads the requested metric', () => {
  const e = model({ requests: 3, tokens: { total: 30 }, cost: 1.5 });
  assert.equal(m.matrixCellValue(e, 'requests'), 3);
  assert.equal(m.matrixCellValue(e, 'tokens'), 30);
  assert.equal(m.matrixCellValue(e, 'cost'), 1.5);
  assert.equal(m.matrixCellValue(null, 'tokens'), 0);
  assert.equal(m.matrixCellValue({}, 'tokens'), 0);
});

test('matrixColKey keys on model+provider+variant', () => {
  assert.equal(m.matrixColKey({ model: 'm1', provider: 'p', variant: 'v1' }), M1);
  assert.equal(m.matrixColKey({ model: 'm2', provider: 'p', variant: null }), M2);
  assert.equal(m.matrixColKey({ model: 'm1', provider: 'q', variant: 'v1' }), 'm1|q|v1');
  assert.equal(m.matrixColKey(null), '');
});

test('matrixFromAgents preserves raw agent names including unknown', () => {
  const mx = m.matrixFromAgents(AGENTS, 'tokens');
  assert.deepEqual(mx.rows.map((r) => r.agent), ['alpha', 'unknown']);
  assert.equal(mx.rows[1].agent, 'unknown');
});

test('matrixFromAgents keeps group as a secondary field', () => {
  const mx = m.matrixFromAgents(AGENTS, 'tokens');
  const alpha = mx.rows.find((r) => r.agent === 'alpha');
  assert.equal(alpha.group, 'core');
  const unknown = mx.rows.find((r) => r.agent === 'unknown');
  assert.equal(unknown.group, null);
});

test('matrixFromAgents model identity includes provider and variant', () => {
  const mx = m.matrixFromAgents(AGENTS, 'tokens');
  assert.deepEqual(mx.cols.map((c) => c.key).sort(), [M1, M2].sort());
  const m1 = mx.cols.find((c) => c.key === M1);
  assert.equal(m1.model, 'm1');
  assert.equal(m1.provider, 'p');
  assert.equal(m1.variant, 'v1');
  const m2 = mx.cols.find((c) => c.key === M2);
  assert.equal(m2.variant, '');
});

test('matrixFromAgents cells carry the selected metric only', () => {
  const mx = m.matrixFromAgents(AGENTS, 'tokens');
  const alpha = mx.rows.find((r) => r.agent === 'alpha');
  assert.equal(alpha.cells[M1], 30);
  assert.equal(alpha.cells[M2], 5);
  const req = m.matrixFromAgents(AGENTS, 'requests').rows.find((r) => r.agent === 'alpha');
  assert.equal(req.cells[M1], 3);
  assert.equal(req.cells[M2], 1);
});

test('matrixFromAgents totals and sorts rows/cols by metric desc', () => {
  const mx = m.matrixFromAgents(AGENTS, 'tokens');
  assert.deepEqual(mx.rows.map((r) => r.total), [35, 10]);
  assert.deepEqual(mx.cols.map((c) => c.total), [40, 5]);
  assert.equal(mx.cols[0].key, M1);
  const req = m.matrixFromAgents(AGENTS, 'requests');
  assert.deepEqual(req.rows.map((r) => r.total), [4, 2]);
  assert.deepEqual(req.cols.map((c) => c.total), [5, 1]);
});

test('matrixFromAgents is null-safe', () => {
  const mx = m.matrixFromAgents(null, 'tokens');
  assert.deepEqual(mx, { rows: [], cols: [] });
  const bad = m.matrixFromAgents([{ agent: 'x' }], 'tokens');
  assert.equal(bad.rows[0].agent, 'x');
  assert.equal(bad.rows[0].total, 0);
});

test('intensity is monotonic, bounded, and leaves raw values unchanged', () => {
  const max = 100;
  let prev = -1;
  for (const v of [0, 1, 5, 25, 50, 100, 1000]) {
    const i = m.intensity(v, max);
    assert.ok(i >= 0 && i <= 1, String(i));
    assert.ok(i >= prev, 'monotonic at ' + v);
    prev = i;
  }
  assert.equal(m.intensity(100, 100), 1);
  assert.equal(m.intensity(0, 100), 0);
  assert.equal(m.intensity(-5, 100), 0);
  assert.equal(m.intensity(5, 0), 0);
  const mx = m.matrixFromAgents(AGENTS, 'tokens');
  m.intensity(mx.rows[0].cells[M1], 100);
  assert.equal(m.matrixFromAgents(AGENTS, 'tokens').rows[0].cells[M1], 30);
});

test('topN slices to the limit and handles edge counts', () => {
  const list = Array.from({ length: 25 }, (_, i) => i);
  assert.equal(m.topN(list, 20).length, 20);
  assert.equal(m.topN(list, 20)[19], 19);
  assert.equal(m.topN([1, 2], 20).length, 2);
  assert.deepEqual(m.topN(null, 5), []);
  assert.equal(m.MATRIX_TOP_N, 20);
});

test('matrixTruncationNote explains any dropped rows/cols', () => {
  assert.equal(m.matrixTruncationNote(5, 5, 'tokens', 20), '');
  assert.ok(m.matrixTruncationNote(25, 3, 'tokens', 20).includes('Showing top 20 by Tokens'));
  assert.ok(m.matrixTruncationNote(25, 3, 'tokens', 20).includes('(agents)'));
  assert.ok(m.matrixTruncationNote(3, 25, 'cost', 20).includes('(models)'));
  assert.ok(m.matrixTruncationNote(3, 25, 'cost', 20).includes('Raw Cost'));
  assert.ok(m.matrixTruncationNote(25, 25, 'requests', 20).includes('(agents and models)'));
});

test('matrixMetricSwitch re-renders only (no refetch)', () => {
  assert.deepEqual(m.matrixMetricSwitch('tokens', 'requests'), { metric: 'requests', refetch: false });
  assert.deepEqual(m.matrixMetricSwitch('tokens', 'bogus'), { metric: 'tokens', refetch: false });
});

test('matrix follows the filtered agents and only their models', () => {
  const filtered = AGENTS.filter((a) => a.agent === 'alpha');
  const mx = m.matrixFromAgents(filtered, 'tokens');
  assert.deepEqual(mx.rows.map((r) => r.agent), ['alpha']);
  assert.deepEqual(mx.cols.map((c) => c.key).sort(), [M1, M2].sort());
  const byProvider = AGENTS.filter((a) => a.agent === 'unknown');
  const mx2 = m.matrixFromAgents(byProvider, 'tokens');
  assert.deepEqual(mx2.cols.map((c) => c.key), [M1]);
});
