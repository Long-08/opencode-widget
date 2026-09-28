// Phase 5A: Agents/Models/Sessions dashboard view-model suite.
// Since Phase 5.1 the view-models live in electron/app/dashboard/views.js,
// after the format.js/core.js files they depend on. Read all three in order,
// eval them, and assert the display/view-model contract.
//   node --test tests/js/dashboard_views.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FORMAT_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'format.js');
const CORE_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'core.js');
const VIEWS_JS = path.resolve(__dirname, '..', '..', 'electron', 'app', 'dashboard', 'views.js');
const FORMAT_SRC = fs.readFileSync(FORMAT_JS, 'utf8');
const CORE_SRC = fs.readFileSync(CORE_JS, 'utf8');
const VIEWS_SRC = fs.readFileSync(VIEWS_JS, 'utf8');

function extractMarked(src, startMarker, endMarker, label) {
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker);
  assert.ok(start >= 0, label + ':start marker not found');
  assert.ok(end > start, label + ':end marker not found');
  return src.slice(start, end + endMarker.length);
}

function extractCore(src) {
  return extractMarked(src, '/* ==== phase5a-core:start ====', '/* ==== phase5a-core:end ==== */', 'phase5a-core');
}

function extractViews(src) {
  return extractMarked(src, '/* ==== phase5a-views:start ====', '/* ==== phase5a-views:end ==== */', 'phase5a-views');
}

const VIEW_NAMES = [
  'dashCell', 'dashCellsHtml', 'dashStateHtml', 'dashErrorHtml', 'dashReaderError',
  'numOrNull', 'normQuery', 'dashContains', 'providerKey', 'providerOptions',
  'filterByProvider', 'filterAgents', 'filterModels', 'filterSessions',
  'agentRowCells', 'modelRowCells', 'sessionRowCells', 'sessionTreeRows',
  'agentDetailModel', 'modelDetailModel', 'sessionDetailModel',
  'dashSortBtn', 'dashMetricsHtml', 'dashDetailTable',
];

// eslint-disable-next-line no-eval
const v = eval(
  FORMAT_SRC
  + '\n' + extractCore(CORE_SRC)
  + '\n' + extractViews(VIEWS_SRC)
  + '\n({' + VIEW_NAMES.join(',') + '})'
);

const PAYLOADS = [
  '<img src=x onerror=alert(1)>',
  '<script>alert(1)</script>',
  '"><svg onload=alert(1)>',
];

// --- agentRowCells ----------------------------------------------------------

test('agentRowCells: raw name primary, group badge, 9 columns', () => {
  const a = {
    agent: 'build', group: 'core', requests: 4, sessions: 2, cost: 1.5,
    tokens: { total: 1000 }, cache_read_ratio: 0.25,
    avg_tokens_per_request: 250, avg_duration_per_request: 1500,
    models: [{ model: 'gpt', variant: 'x', tokens: { total: 900 } }, { model: 'small', tokens: { total: 100 } }],
  };
  const r = v.agentRowCells(a);
  assert.equal(r.cells.length, 9);
  assert.equal(r.cells[0].text, 'build'); // raw name primary
  assert.ok(r.groupBadge.includes('core'));
  assert.ok(r.groupBadge.includes('obs-badge'));
  assert.equal(r.cells[1].text, '4');
  assert.equal(r.cells[2].text, '2');
  assert.equal(r.cells[3].text, '1K');
  assert.equal(r.cells[4].text, '$1.50');
  assert.equal(r.cells[5].text, '25.0%');
  assert.equal(r.cells[6].text, '250');
  assert.equal(r.cells[7].text, '1.5s');
  assert.equal(r.cells[8].text, 'gpt'); // top model present
});

test('agentRowCells: null group -> no badge; null metrics -> em dash', () => {
  const r = v.agentRowCells({ agent: 'ask', group: null });
  assert.equal(r.groupBadge, '');
  assert.equal(r.cells[5].text, '—'); // cache ratio
  assert.equal(r.cells[6].text, '—'); // avg tokens/request
  assert.equal(r.cells[7].text, '—'); // avg duration
  assert.equal(r.cells[8].text, '—'); // top model
  assert.equal(v.agentRowCells(null).cells[0].text, '—');
});

// --- modelRowCells ----------------------------------------------------------

test('modelRowCells: variant null -> em dash; top agent present', () => {
  const m = {
    model: 'gpt', provider_id: 'openai', source: 'go', variant: null,
    requests: 5, sessions: 3, cost: 2, tokens: { total: 2000 }, cache_read_ratio: 0.5,
    agents: [{ agent: 'ask', tokens: { total: 100 } }, { agent: 'build', tokens: { total: 900 } }],
  };
  const r = v.modelRowCells(m);
  assert.equal(r.cells.length, 10);
  assert.equal(r.cells[0].text, 'gpt');
  assert.equal(r.cells[1].text, 'openai');
  assert.equal(r.cells[2].text, 'go');
  assert.equal(r.cells[3].text, '—'); // variant null
  assert.equal(r.cells[4].text, '5');
  assert.equal(r.cells[5].text, '3');
  assert.equal(r.cells[6].text, '2K');
  assert.equal(r.cells[7].text, '$2.00');
  assert.equal(r.cells[8].text, '50.0%');
  assert.equal(r.cells[9].text, 'build'); // top agent
});

// --- detail models ----------------------------------------------------------

test('agentDetailModel keeps raw 0..1 shares and exposes providers', () => {
  const a = {
    agent: 'build', group: 'core', requests: 4, sessions: 2, cost: 2,
    tokens: { total: 400 }, cache_read_ratio: 0.5,
    avg_tokens_per_request: 100, avg_duration_per_request: 1500,
    models: [{
      model: 'm1', provider: 'p', variant: 'v', requests: 3, cost: 1.5,
      tokens: { total: 300 }, cache_read_ratio: 0.4,
      request_share: 0.75, token_share: 0.75, cost_share: 0.75,
    }],
    providers: [{ provider_id: 'p', source: 'go', requests: 4, cost: 2, tokens: { total: 400 }, cache_read_ratio: 0.5 }],
  };
  const d = v.agentDetailModel(a);
  assert.equal(d.models.length, 1);
  assert.equal(d.models[0].request_share, 0.75); // raw share, not a percentage
  assert.equal(d.models[0].token_share, 0.75);
  assert.equal(d.models[0].cost_share, 0.75);
  assert.ok(d.models[0].cost_share <= 1 && d.models[0].cost_share >= 0);
  assert.equal(d.models[0].raw_cost, 1.5);
  assert.equal(d.providers.length, 1);
  assert.equal(d.providers[0].provider_id, 'p');
  assert.equal(d.providers[0].source, 'go');
  assert.equal(d.providers[0].raw_cost, 2);
  assert.equal(d.metrics.requests, 4);
  assert.equal(d.metrics.sessions, 2);
  assert.equal(d.metrics.tokens, 400);
  assert.equal(d.metrics.raw_cost, 2);
  assert.equal(d.metrics.cache_read_ratio, 0.5);
  assert.equal(d.metrics.avg_duration_per_request, 1500);
});

test('modelDetailModel exposes agents breakdown', () => {
  const m = {
    model: 'm1', provider_id: 'p', source: 'go', variant: null,
    requests: 5, sessions: 3, cost: 3, tokens: { total: 500 }, cache_read_ratio: 0.2,
    agents: [
      { agent: 'build', group: 'core', requests: 2, cost: 1, tokens: { total: 200 }, cache_read_ratio: 0.1 },
      { agent: 'ask', group: null, requests: 3, cost: 2, tokens: { total: 300 }, cache_read_ratio: 0.3 },
    ],
  };
  const d = v.modelDetailModel(m);
  assert.equal(d.model, 'm1');
  assert.equal(d.variant, null);
  assert.equal(d.agents.length, 2);
  assert.equal(d.agents[0].agent, 'build');
  assert.equal(d.agents[0].group, 'core');
  assert.equal(d.agents[0].raw_cost, 1);
  assert.equal(d.agents[1].raw_cost, 2);
  assert.equal(d.agents[1].cache_read_ratio, 0.3);
  assert.equal(d.metrics.raw_cost, 3);
});

// --- sessionTreeRows --------------------------------------------------------

function sampleTree() {
  return {
    nodes: [
      { session_id: 'r', parent_session_id: null, depth: 0, children_count: 1, is_root: true },
      { session_id: 'c', parent_session_id: 'r', depth: 1, children_count: 1, is_root: false },
      { session_id: 'g', parent_session_id: 'c', depth: 2, children_count: 0, is_root: false },
    ],
    roots: ['r'],
    diagnostics: {},
  };
}

const SAMPLE_SESSIONS = [
  { session_id: 'r', requests: 1, cost: 0.1, tokens: { total: 10 }, agents: [{}] },
  { session_id: 'c', requests: 2, cost: 0.2, tokens: { total: 20 }, agents: [{}, {}] },
  { session_id: 'g', requests: 3, cost: 0.3, tokens: { total: 30 }, agents: [] },
];

test('sessionTreeRows: 3-level nesting, backend depth/children_count preserved', () => {
  const rows = v.sessionTreeRows(SAMPLE_SESSIONS, sampleTree(), new Set(['r', 'c']));
  assert.deepEqual(rows.map((r) => r.session_id), ['r', 'c', 'g']);
  assert.deepEqual(rows.map((r) => r.depth), [0, 1, 2]);
  assert.equal(rows[0].has_children, true);
  assert.equal(rows[0].children_count, 1);
  assert.equal(rows[0].expanded, true);
  assert.equal(rows[1].expanded, true);
  assert.equal(rows[2].has_children, false);
  assert.equal(rows[2].children_count, 0);
  assert.equal(rows[2].expanded, false);
  assert.equal(rows[0].agents_count, 1);
  assert.equal(rows[1].agents_count, 2);
  assert.equal(rows[2].agents_count, 0);
  assert.equal(rows[1].requests, 2);
  assert.equal(rows[1].tokens, 20);
  assert.equal(rows[1].raw_cost, 0.2);
});

test('sessionTreeRows: collapsed root hides descendants', () => {
  const collapsed = v.sessionTreeRows(SAMPLE_SESSIONS, sampleTree(), new Set());
  assert.deepEqual(collapsed.map((r) => r.session_id), ['r']);
  const oneLevel = v.sessionTreeRows(SAMPLE_SESSIONS, sampleTree(), new Set(['r']));
  assert.deepEqual(oneLevel.map((r) => r.session_id), ['r', 'c']); // 'g' hidden
  assert.equal(oneLevel[1].expanded, false);
});

test('sessionTreeRows: tolerates tree/session mismatch and never crashes', () => {
  const tree = {
    nodes: [
      { session_id: 'r', parent_session_id: null, depth: 0, children_count: 2, is_root: true },
      { session_id: 'x', parent_session_id: 'r', depth: 1, children_count: 0, is_root: false }, // no metrics
      { session_id: 'y', parent_session_id: 'r', depth: 1, children_count: 0, is_root: false },
    ],
    roots: ['r'],
  };
  const sessions = [
    { session_id: 'r', requests: 1, cost: 1, tokens: { total: 1 }, agents: [] },
    { session_id: 'y', requests: 2, cost: 2, tokens: { total: 2 }, agents: [] },
    { session_id: 'extra', requests: 9, cost: 9, tokens: { total: 9 }, agents: [] }, // not in tree
  ];
  const rows = v.sessionTreeRows(sessions, tree, new Set(['r']));
  assert.deepEqual(rows.map((r) => r.session_id), ['r', 'x', 'y']);
  const x = rows.find((r) => r.session_id === 'x');
  assert.equal(x.requests, null);
  assert.equal(x.tokens, null);
  assert.equal(x.raw_cost, null);
  assert.equal(x.agents_count, null);
  assert.equal(rows.find((r) => r.session_id === 'extra'), undefined);
});

test('sessionTreeRows: defensive with self/cycle nodes and empty inputs', () => {
  const cyclic = {
    nodes: [
      { session_id: 'a', parent_session_id: 'b', depth: 0, children_count: 0, is_root: false },
      { session_id: 'b', parent_session_id: 'a', depth: 0, children_count: 0, is_root: false },
    ],
    roots: [],
  };
  assert.doesNotThrow(() => v.sessionTreeRows([], cyclic, new Set(['a', 'b'])));
  assert.deepEqual(v.sessionTreeRows([], { nodes: [] }, new Set()), []);
  assert.deepEqual(v.sessionTreeRows(null, null, null), []);
});

// --- sessionDetailModel -----------------------------------------------------

test('sessionDetailModel contains no content/prompt/response/reasoning/tool/path/title', () => {
  const d = v.sessionDetailModel({
    session_id: 'ses_abc', parent_session_id: null, start_ts: 10, end_ts: 20, duration_ms: 10,
    requests: 3, cost: 1.5, tokens: { total: 100 },
    agents: [{ agent: 'build', group: 'core', requests: 3, cost: 1.5, tokens: { total: 100 } }],
    models: [{ model: 'gpt', provider_id: 'p', variant: null, requests: 3, cost: 1.5, tokens: { total: 100 } }],
    providers: [{ provider_id: 'p', source: 'go', requests: 3, cost: 1.5, tokens: { total: 100 } }],
  });
  assert.equal(d.session_id, 'ses_abc');
  assert.equal(d.raw_cost, 1.5);
  assert.equal(d.tokens, 100);
  assert.equal(d.agents.length, 1);
  assert.equal(d.models.length, 1);
  assert.equal(d.providers.length, 1);
  const json = JSON.stringify(d).toLowerCase();
  for (const forbidden of ['content', 'prompt', 'response', 'reasoning', 'tool', 'path', 'title']) {
    assert.ok(!json.includes(forbidden), 'leaked token: ' + forbidden + ' in ' + json);
  }
});

// --- filters ----------------------------------------------------------------

test('filterAgents matches agent or group, case-insensitively', () => {
  const rows = [{ agent: 'Build', group: 'Core' }, { agent: 'ask', group: 'General' }, { agent: 'x', group: null }];
  assert.deepEqual(v.filterAgents(rows, 'core').map((r) => r.agent), ['Build']);
  assert.deepEqual(v.filterAgents(rows, 'ASK').map((r) => r.agent), ['ask']);
  assert.deepEqual(v.filterAgents(rows, 'build').map((r) => r.agent), ['Build']);
  const all = v.filterAgents(rows, '');
  assert.equal(all.length, 3);
  assert.notStrictEqual(all, rows); // defensive copy on no-op
});

test('filterModels matches model/provider/source; filterSessions matches session_id', () => {
  const models = [
    { model: 'GPT', provider_id: 'openai', source: 'go' },
    { model: 'claude', provider_id: 'anthropic', source: 'zen' },
  ];
  assert.deepEqual(v.filterModels(models, 'anthropic').map((m) => m.model), ['claude']);
  assert.deepEqual(v.filterModels(models, 'go').map((m) => m.model), ['GPT']);
  assert.deepEqual(v.filterModels(models, 'claude').map((m) => m.model), ['claude']);
  assert.equal(v.filterModels(models, '').length, 2);
  const sessions = [{ session_id: 'ses_abc' }, { session_id: 'ses_xyz' }];
  assert.deepEqual(v.filterSessions(sessions, 'ABC').map((s) => s.session_id), ['ses_abc']);
  assert.equal(v.filterSessions(sessions, '').length, 2);
});

// --- provider options / filter ---------------------------------------------

test('providerOptions is dynamic and distinct on provider_id + source', () => {
  const rows = [
    { provider_id: 'p1', source: 'go' },
    { provider_id: 'p1', source: 'go' },
    { provider_id: 'p1', source: 'zen' },
    { provider_id: 'p2', source: null },
  ];
  const opts = v.providerOptions(rows);
  assert.deepEqual(opts.map((o) => o.key), ['p1|go', 'p1|zen', 'p2|']);
  assert.ok(opts[0].label.includes('go') && opts[0].label.includes('p1'));
  assert.equal(opts[2].label, 'p2');
  assert.deepEqual(v.providerOptions([]), []);
});

test('filterByProvider: empty/null no-op, key filters direct + nested providers', () => {
  const rows = [{ provider_id: 'p1', source: 'go' }, { provider_id: 'p2', source: 'zen' }];
  assert.equal(v.filterByProvider(rows, '').length, 2);
  assert.equal(v.filterByProvider(rows, null).length, 2);
  assert.notStrictEqual(v.filterByProvider(rows, ''), rows);
  assert.deepEqual(v.filterByProvider(rows, 'p1|go').map((r) => r.provider_id), ['p1']);
  const nested = [
    { agent: 'a', providers: [{ provider_id: 'p1', source: 'go' }] },
    { agent: 'b', providers: [{ provider_id: 'p2', source: 'zen' }] },
  ];
  assert.deepEqual(v.filterByProvider(nested, 'p2|zen').map((r) => r.agent), ['b']);
});

// --- escaping ---------------------------------------------------------------

test('row cells escape agent/group/model/provider/source/variant payloads', () => {
  for (const p of PAYLOADS) {
    const a = v.agentRowCells({ agent: p, group: p, models: [{ model: p, variant: p, tokens: { total: 1 } }] });
    assert.ok(!a.cells[0].text.includes('<img') && !a.cells[0].text.includes('<script') && !a.cells[0].text.includes('<svg'), a.cells[0].text);
    assert.ok(!a.groupBadge.includes('<img') && !a.groupBadge.includes('<script') && !a.groupBadge.includes('<svg'), a.groupBadge);
    assert.ok(!a.cells[0].text.includes('&amp;lt;'), 'double escaped: ' + a.cells[0].text);

    const m = v.modelRowCells({
      model: p, provider_id: p, source: p, variant: p,
      agents: [{ agent: p, tokens: { total: 1 } }],
    });
    for (const idx of [0, 1, 2, 3, 9]) {
      assert.ok(!m.cells[idx].text.includes('<img') && !m.cells[idx].text.includes('<script')
        && !m.cells[idx].text.includes('<svg'), m.cells[idx].text);
    }

    const s = v.sessionRowCells({ session_id: p });
    assert.ok(!s.cells[0].text.includes('<script') && !s.cells[0].text.includes('<img'), s.cells[0].text);
    assert.ok(!s.cells[0].title.includes('<script') && !s.cells[0].title.includes('<img'), s.cells[0].title);
  }
});

test('dashStateHtml / dashErrorHtml escape error messages and tab ids', () => {
  for (const p of PAYLOADS) {
    const html = v.dashStateHtml(p, true);
    assert.ok(html.startsWith('<div class="obs-state err">'), html);
    assert.ok(!html.includes('<img') && !html.includes('<script') && !html.includes('<svg'), html);
    assert.ok(html.includes('&lt;'), html);
    const err = v.dashErrorHtml(p, p);
    assert.ok(!err.includes('<img') && !err.includes('<script') && !err.includes('<svg'), err);
    assert.ok(err.includes('data-obs-retry='), err);
  }
});
