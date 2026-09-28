// Phase 5A: shared dashboard core regression/unit suite.
// Since Phase 5.1 the DOM-free core lives in electron/app/dashboard/core.js
// (the esc()/formatter helpers it depends on live in dashboard/format.js).
// Read both files in dependency order, eval them, and assert the
// display/sort/fetch contract.
//   node --test tests/js/dashboard_core.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FORMAT_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'format.js');
const CORE_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'core.js');
const FORMAT_SRC = fs.readFileSync(FORMAT_JS, 'utf8');
const CORE_SRC = fs.readFileSync(CORE_JS, 'utf8');

function extractCore(src) {
  const startMarker = '/* ==== phase5a-core:start ====';
  const endMarker = '/* ==== phase5a-core:end ==== */';
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker);
  assert.ok(start >= 0, 'phase5a-core:start marker not found');
  assert.ok(end > start, 'phase5a-core:end marker not found');
  return src.slice(start, end + endMarker.length);
}

const CORE_NAMES = [
  'DASH_TABS', 'DASH_RANGES', 'DASH_ENDPOINTS',
  'dashRangeValue', 'dashCacheKey', 'dashEndpointsFor', 'dashShouldFetch', 'dashLoad',
  'dashSortRows', 'dashNull', 'formatInteger', 'formatTokens', 'formatCost',
  'formatPercent', 'formatDuration', 'formatDateTime', 'shortSessionId',
  'readerState', 'readerMessage', 'topBy', 'topAgentOf', 'topModelOfAgent',
  'overviewSummary', 'topModelsFromAgents', 'dashHtmlText', 'dashGroupBadge',
];

// eslint-disable-next-line no-eval
const core = eval(
  FORMAT_SRC
  + '\n' + extractCore(CORE_SRC)
  + '\n({' + CORE_NAMES.join(',') + '})'
);

function recorder() {
  const calls = [];
  const fn = (name, range) => {
    calls.push([name, range]);
    return Promise.resolve({ endpoint: name, range: range, agents: [], sessions: [], models: [] });
  };
  return { calls, fn };
}

// --- range / endpoints / cache key -----------------------------------------

test('dashRangeValue only accepts DASH_RANGES, else all', () => {
  for (const r of ['today', '7d', '30d', 'all']) assert.equal(core.dashRangeValue(r), r);
  for (const bad of ['1', '7', 'Today', '', null, undefined, 42, {}]) {
    assert.equal(core.dashRangeValue(bad), 'all', String(bad));
  }
});

test('dashCacheKey is tab|range with normalization', () => {
  assert.equal(core.dashCacheKey('overview', 'today'), 'overview|today');
  assert.equal(core.dashCacheKey('agents', '30d'), 'agents|30d');
  assert.equal(core.dashCacheKey('agents', 'bogus'), 'agents|all');
});

test('dashEndpointsFor returns a defensive copy', () => {
  assert.deepEqual(core.dashEndpointsFor('overview'), ['agents', 'sessions']);
  assert.deepEqual(core.dashEndpointsFor('agents'), ['agents']);
  assert.deepEqual(core.dashEndpointsFor('models'), ['models']);
  assert.deepEqual(core.dashEndpointsFor('sessions'), ['sessions']);
  assert.deepEqual(core.dashEndpointsFor('nope'), []);
  const copy = core.dashEndpointsFor('overview');
  copy.push('x');
  assert.deepEqual(core.dashEndpointsFor('overview'), ['agents', 'sessions']);
});

test('dashShouldFetch reflects cache presence', () => {
  const cache = {};
  assert.equal(core.dashShouldFetch('overview', 'today', cache), true);
  cache['overview|today'] = {};
  assert.equal(core.dashShouldFetch('overview', 'today', cache), false);
  assert.equal(core.dashShouldFetch('overview', '7d', cache), true);
  assert.equal(core.dashShouldFetch('agents', 'today', cache), true);
  assert.equal(core.dashShouldFetch('overview', 'today', null), true);
});

// --- lazy loading / cache ---------------------------------------------------

test('dashLoad requested endpoint sets per tab', async () => {
  const expected = {
    overview: ['agents', 'sessions'],
    agents: ['agents'],
    models: ['models'],
    sessions: ['sessions'],
  };
  for (const tab of Object.keys(expected)) {
    const cache = {};
    const rec = recorder();
    const res = await core.dashLoad(tab, 'today', cache, rec.fn, {});
    assert.equal(res.fromCache, false, tab);
    assert.equal(res.key, tab + '|today', tab);
    assert.deepEqual(res.fetched, expected[tab], tab);
    assert.deepEqual(rec.calls.map((c) => c[0]), expected[tab], tab);
    assert.ok(cache[tab + '|today'], tab + ' cache populated');
    assert.deepEqual(Object.keys(res.data).sort(), expected[tab].slice().sort(), tab);
  }
});

test('dashLoad reuses cache without calling fetchTab again', async () => {
  const cache = {};
  const rec = recorder();
  await core.dashLoad('overview', 'today', cache, rec.fn, {});
  const callsAfterFirst = rec.calls.length;
  const res = await core.dashLoad('overview', 'today', cache, rec.fn, {});
  assert.equal(res.fromCache, true);
  assert.deepEqual(res.fetched, []);
  assert.equal(rec.calls.length, callsAfterFirst);
});

test('dashLoad force bypasses cache and updates only that key', async () => {
  const cache = {};
  const rec = recorder();
  await core.dashLoad('overview', 'today', cache, rec.fn, {});
  await core.dashLoad('agents', 'all', cache, rec.fn, {});
  const overviewBefore = cache['overview|today'];
  const agentsBefore = cache['agents|all'];
  const res = await core.dashLoad('overview', 'today', cache, rec.fn, { force: true });
  assert.equal(res.fromCache, false);
  assert.deepEqual(res.fetched, ['agents', 'sessions']);
  assert.notStrictEqual(cache['overview|today'], overviewBefore);
  assert.strictEqual(cache['agents|all'], agentsBefore, 'other keys untouched');
});

test('rejected fetch does not populate cache and propagates', async () => {
  const cache = {};
  const failing = (name) => (name === 'agents'
    ? Promise.reject(new Error('boom'))
    : Promise.resolve({ ok: true }));
  await assert.rejects(() => core.dashLoad('agents', 'today', cache, failing, {}), /boom/);
  assert.equal(cache['agents|today'], undefined);
  // a later healthy call must fetch again (nothing was cached)
  const rec = recorder();
  const res = await core.dashLoad('agents', 'today', cache, rec.fn, {});
  assert.equal(res.fromCache, false);
  assert.deepEqual(rec.calls.map((c) => c[0]), ['agents']);
});

test('overview rejects when one endpoint rejects, cache stays empty', async () => {
  const cache = {};
  const failing = (name) => (name === 'sessions'
    ? Promise.reject(new Error('nope'))
    : Promise.resolve({ agents: [] }));
  await assert.rejects(() => core.dashLoad('overview', 'all', cache, failing, {}));
  assert.equal(cache['overview|all'], undefined);
});

// --- sorting ----------------------------------------------------------------

test('dashSortRows is stable on equal primary + secondary', () => {
  const rows = [
    { name: 'b', tokens: 5, id: 1 },
    { name: 'a', tokens: 5, id: 2 },
    { name: 'a', tokens: 5, id: 3 },
    { name: 'c', tokens: 9, id: 4 },
  ];
  const out = core.dashSortRows(rows, 'tokens', 'desc', 'name');
  assert.deepEqual(out.map((r) => r.id), [4, 2, 3, 1]);
  // input order must not be mutated
  assert.deepEqual(rows.map((r) => r.id), [1, 2, 3, 4]);
});

test('dashSortRows secondary name ascending and asc direction', () => {
  const asc = core.dashSortRows(
    [{ name: 'b', tokens: 2 }, { name: 'a', tokens: 1 }], 'tokens', 'asc', 'name');
  assert.deepEqual(asc.map((r) => r.name), ['a', 'b']);
  const samePrimary = core.dashSortRows(
    [{ name: 'z', tokens: 1 }, { name: 'a', tokens: 1 }], 'tokens', 'desc', 'name');
  assert.deepEqual(samePrimary.map((r) => r.name), ['a', 'z']);
});

test('dashSortRows places null primary last on desc, first on asc', () => {
  const rows = [{ name: 'x', tokens: null }, { name: 'y', tokens: 3 }];
  assert.deepEqual(core.dashSortRows(rows, 'tokens', 'desc', 'name').map((r) => r.name), ['y', 'x']);
  assert.deepEqual(core.dashSortRows(rows, 'tokens', 'asc', 'name').map((r) => r.name), ['x', 'y']);
});

// --- formatters -------------------------------------------------------------

test('formatInteger', () => {
  assert.equal(core.formatInteger(0), '0');
  assert.equal(core.formatInteger(1234567), '1,234,567');
  assert.equal(core.formatInteger(12.6), '13');
  assert.equal(core.formatInteger(null), '—');
  assert.equal(core.formatInteger(undefined), '—');
});

test('formatTokens', () => {
  assert.equal(core.formatTokens(980), '980');
  assert.equal(core.formatTokens(12400), '12.4K');
  assert.equal(core.formatTokens(3200000), '3.2M');
  assert.equal(core.formatTokens(0), '0');
  assert.equal(core.formatTokens(null), '—');
});

test('formatCost', () => {
  assert.equal(core.formatCost(0), '$0.00');
  assert.equal(core.formatCost(0.0005), '< $0.001');
  assert.equal(core.formatCost(0.005), '$0.0050');
  assert.equal(core.formatCost(0.5), '$0.500');
  assert.equal(core.formatCost(2.5), '$2.50');
  assert.equal(core.formatCost(null), '—');
});

test('formatPercent', () => {
  assert.equal(core.formatPercent(null), '—');
  assert.equal(core.formatPercent(0), '0.0%');
  assert.equal(core.formatPercent(0.1234), '12.3%');
  assert.equal(core.formatPercent(1), '100.0%');
});

test('formatDuration', () => {
  assert.equal(core.formatDuration(null), '—');
  assert.equal(core.formatDuration(500), '500ms');
  assert.equal(core.formatDuration(1500), '1.5s');
  assert.equal(core.formatDuration(90000), '1.5m');
});

test('formatDateTime uses local YYYY-MM-DD HH:MM', () => {
  assert.equal(core.formatDateTime(null), '—');
  const local = new Date(2026, 0, 2, 3, 4, 0, 0);
  assert.equal(core.formatDateTime(local.getTime()), '2026-01-02 03:04');
});

test('dashNull', () => {
  assert.equal(core.dashNull(null), '—');
  assert.equal(core.dashNull(undefined), '—');
  assert.equal(core.dashNull(0), 0);
  assert.equal(core.dashNull('x'), 'x');
});

test('shortSessionId', () => {
  assert.equal(core.shortSessionId('abc'), 'abc');
  assert.equal(core.shortSessionId('abcdef123456789'), 'abcdef…789');
  assert.equal(core.shortSessionId(null), '—');
  assert.equal(core.shortSessionId('ses_0123456789ab'), 'ses_01…9ab'); // 16 chars -> shortened
  assert.equal(core.shortSessionId('shortid12'), 'shortid12'); // 9 chars -> unchanged
});

// --- top helpers ------------------------------------------------------------

test('topBy is null-safe and returns the highest row', () => {
  assert.equal(core.topBy([], 'tokens'), null);
  assert.equal(core.topBy([{ tokens: null }, { tokens: 5 }], 'tokens').tokens, 5);
  assert.equal(core.topBy([{ tokens: null }], 'tokens'), null);
  const rows = [{ name: 'a', tokens: { total: 3 } }, { name: 'b', tokens: { total: 9 } }];
  assert.equal(core.topBy(rows, 'tokens').name, 'b');
});

test('topAgentOf / topModelOfAgent pick highest tokens.total', () => {
  const modelItem = { agents: [{ agent: 'a', tokens: { total: 1 } }, { agent: 'b', tokens: { total: 9 } }] };
  assert.equal(core.topAgentOf(modelItem).agent, 'b');
  assert.equal(core.topAgentOf(null), null);
  assert.equal(core.topAgentOf({}), null);
  const agentItem = { models: [{ model: 'x', tokens: { total: 2 } }, { model: 'y', tokens: { total: 7 } }] };
  assert.equal(core.topModelOfAgent(agentItem).model, 'y');
  assert.equal(core.topModelOfAgent(undefined), null);
});

// --- overview aggregation ---------------------------------------------------

test('overviewSummary sums API numbers and derives cache ratio', () => {
  const agents = [
    { agent: 'a', requests: 2, cost: 1.5, tokens: { total: 100, input: 40, cache_read: 60, cache_write: 0 },
      models: [{ model: 'm1', provider: 'p', variant: 'v' }, { model: 'm2', provider: 'p', variant: null }] },
    { agent: 'b', requests: 1, cost: 0.5, tokens: { total: 50, input: 50, cache_read: 0, cache_write: 0 },
      models: [{ model: 'm1', provider: 'p', variant: 'v' }] },
  ];
  const sessions = [{ session_id: 's1' }, { session_id: 's2' }, { session_id: 's3' }];
  const s = core.overviewSummary(agents, sessions);
  assert.equal(s.requests, 3);
  assert.equal(s.tokens, 150);
  assert.equal(s.raw_cost, 2.0);
  assert.equal(s.active_agents, 2);
  assert.equal(s.active_models, 2);
  assert.equal(s.sessions, 3);
  assert.ok(Math.abs(s.cache_read_ratio - 0.4) < 1e-9, String(s.cache_read_ratio));
});

test('overviewSummary cache ratio null when denominator 0', () => {
  const s = core.overviewSummary(
    [{ agent: 'a', requests: 1, cost: 0, tokens: { total: 0, input: 0, cache_read: 0, cache_write: 0 }, models: [] }],
    []);
  assert.equal(s.cache_read_ratio, null);
  const empty = core.overviewSummary([], []);
  assert.equal(empty.cache_read_ratio, null);
  assert.equal(empty.requests, 0);
  assert.equal(empty.active_models, 0);
});

test('topModelsFromAgents rolls up by model|provider|variant and tracks top_agent', () => {
  const agents = [
    { agent: 'a', models: [
      { model: 'm1', provider: 'p', variant: 'v', requests: 2, cost: 1.0, tokens: { total: 100 } },
      { model: 'm2', provider: 'q', variant: null, requests: 1, cost: 0.2, tokens: { total: 10 } },
    ] },
    { agent: 'b', models: [
      { model: 'm1', provider: 'p', variant: 'v', requests: 3, cost: 2.0, tokens: { total: 300 } },
    ] },
  ];
  const rows = core.topModelsFromAgents(agents);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].model, 'm1');
  assert.equal(rows[0].provider, 'p');
  assert.equal(rows[0].variant, 'v');
  assert.equal(rows[0].tokens, 400);
  assert.equal(rows[0].requests, 5);
  assert.equal(rows[0].raw_cost, 3.0);
  assert.equal(rows[0].top_agent, 'b');
  assert.equal(rows[1].model, 'm2');
  assert.equal(rows[1].top_agent, 'a');
  assert.deepEqual(rows.map((r) => r.tokens), [400, 10]);
});

// --- reader state -----------------------------------------------------------

test('readerState distinguishes ok/unsupported/missing/error', () => {
  assert.equal(core.readerState({ schema: 'current', status: 'ok' }), 'ok');
  assert.equal(core.readerState({ schema: 'legacy', status: 'ok' }), 'ok');
  assert.equal(core.readerState({ schema: 'unsupported', status: 'unsupported_schema' }), 'unsupported');
  assert.equal(core.readerState({ schema: 'missing', status: 'missing_db' }), 'missing');
  assert.equal(core.readerState({ schema: null, status: 'error' }), 'error');
  assert.equal(core.readerState({ schema: 'unsupported' }), 'unsupported');
  assert.equal(core.readerState({ schema: 'missing' }), 'missing');
  assert.equal(core.readerState(null), 'error');
  assert.equal(core.readerState({}), 'error');
});

test('readerMessage returns distinct per-state text', () => {
  assert.equal(core.readerMessage('ok'), '');
  assert.equal(core.readerMessage('unsupported'), 'OpenCode usage database schema is not supported.');
  assert.equal(core.readerMessage('missing'), 'OpenCode usage database was not found.');
  assert.equal(core.readerMessage('error'), 'OpenCode usage data could not be read.');
  const messages = new Set(['unsupported', 'missing', 'error'].map((s) => core.readerMessage(s)));
  assert.equal(messages.size, 3);
});

// --- escaping ---------------------------------------------------------------

const PAYLOADS = [
  '<img src=x onerror=alert(1)>',
  '<script>alert(1)</script>',
  '"><svg onload=alert(1)>',
];

test('dashHtmlText escapes payloads (text only, no raw markup)', () => {
  for (const p of PAYLOADS) {
    const out = core.dashHtmlText(p);
    assert.ok(!/[<>"']/.test(out), `raw special chars survived: ${out}`);
    assert.ok(!out.includes('<img') && !out.includes('<script') && !out.includes('<svg'), out);
  }
});

test('dashGroupBadge escapes payloads inside its markup', () => {
  assert.equal(core.dashGroupBadge(null), '');
  assert.equal(core.dashGroupBadge(''), '');
  assert.ok(core.dashGroupBadge('Research').includes('Research'));
  for (const p of PAYLOADS) {
    const out = core.dashGroupBadge(p);
    assert.ok(!out.includes('<img'), out);
    assert.ok(!out.includes('<script'), out);
    assert.ok(!out.includes('<svg'), out);
    assert.ok(out.includes('&lt;'), out); // payload '<' escaped
    assert.ok(!out.includes('&amp;lt;'), out); // no double escaping
  }
});
