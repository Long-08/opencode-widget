// Compact Floating UX: renderer behavior suite for the mini/compact/expanded
// model. Evals dashboard/format.js + core.js + forecast.js + app.js against a
// stub DOM (the classic scripts share one function scope, like the browser's
// shared global lexical scope) and drives real state transitions.
//   node --test tests/js/widget_modes.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
const APP = (...p) => path.resolve(ROOT, 'electron', 'app', ...p);
const FORMAT_SRC = fs.readFileSync(APP('dashboard', 'format.js'), 'utf8');
const CORE_SRC = fs.readFileSync(APP('dashboard', 'core.js'), 'utf8');
const VIEWS_SRC = fs.readFileSync(APP('dashboard', 'views.js'), 'utf8');
const FORECAST_SRC = fs.readFileSync(APP('dashboard', 'forecast.js'), 'utf8');
const APPJS_SRC = fs.readFileSync(APP('app.js'), 'utf8');
const MAINJS_SRC = fs.readFileSync(path.resolve(ROOT, 'electron', 'main.js'), 'utf8');

// ---------------------------------------------------------------------------
// stub DOM
// ---------------------------------------------------------------------------
function makeClassList() {
  const set = new Set();
  return {
    toggle(cls, on) {
      const want = on === undefined ? !set.has(cls) : !!on;
      if (want) set.add(cls); else set.delete(cls);
    },
    add(...c) { c.forEach((x) => set.add(x)); },
    remove(...c) { c.forEach((x) => set.delete(x)); },
    contains(c) { return set.has(c); },
  };
}

function makeEl(key) {
  const el = {
    _key: key,
    id: key.startsWith('#') ? key.slice(1) : key,
    textContent: '',
    innerHTML: '',
    value: '',
    title: '',
    className: '',
    style: {},
    dataset: {},
    children: [],
    classList: makeClassList(),
    handlers: {},
    disabled: false,
    appendChild(child) { el.children.push(child); return child; },
    insertAdjacentHTML(_pos, html) { el.innerHTML += html; },
    remove() {},
    querySelector() { return makeEl(el._key + '?'); },
    querySelectorAll() { return []; },
    addEventListener(type, fn) { (el.handlers[type] = el.handlers[type] || []).push(fn); },
    setAttribute(name, v) { el.dataset['attr_' + name] = String(v); },
    getAttribute(name) { return el.dataset['attr_' + name] || null; },
    closest() { return null; },
    getBoundingClientRect() { return { width: 400, height: 200, left: 0, top: 0 }; },
    focus() {},
    blur() {},
  };
  return el;
}

function makeDocument() {
  const els = new Map();
  const get = (sel) => {
    if (!els.has(sel)) els.set(sel, makeEl(sel));
    return els.get(sel);
  };
  const doc = {
    _els: els,
    handlers: {},
    getElementById: (id) => get('#' + id),
    querySelector: (sel) => get(sel),
    querySelectorAll: () => [],
    addEventListener(type, fn) { (doc.handlers[type] = doc.handlers[type] || []).push(fn); },
    documentElement: { style: { setProperty() {} } },
    body: makeEl('#body'),
    createElement: (tag) => makeEl('<' + tag + '>'),
    hidden: false,
  };
  return doc;
}

function makeStorage() {
  const m = {};
  return {
    getItem(k) { return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null; },
    setItem(k, v) { m[k] = String(v); },
    removeItem(k) { delete m[k]; },
  };
}

// capture-only timers; flush() drains the queue (nested timers included) and
// yields to the microtask queue between batches so async chains settle.
function makeTimers() {
  const timeouts = [];
  const intervals = [];
  return {
    timeouts,
    intervals,
    setTimeout(fn, ms) { timeouts.push({ fn, ms }); return timeouts.length; },
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval() {},
    clearTimeout() {},
    async flush() {
      let guard = 0;
      while (timeouts.length && guard++ < 200) {
        const batch = timeouts.splice(0, timeouts.length);
        batch.forEach((t) => t.fn());
        await new Promise((r) => setImmediate(r));
      }
    },
  };
}

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------
function stateFixture() {
  const goStat = (over) => Object.assign({
    key: 'glm-5.2|go', model: 'glm-5.2', name: 'GLM-5.2', source: 'go', group: 'go',
    count_total: 120, count_m: 40, count_w: 20, count_s: 5,
    cost_m: 9.3, cost_w: 4.1, cost_s: 0.8,
    tokens_in: 1000, tokens_out: 500, tokens_cache: 2000,
    meter_rate: 1.4212, model_quota: 60, model_used: 13.21,
    effective_remain: 4.2, remain_cnt: 420, avg_per_req: 0.01,
  }, over);
  return {
    windows: [
      { kind: 'session', used: 1.2, limit: 12, pct: 10, reset: 0, reset_text: '' },
      { kind: 'weekly', used: 7.5, limit: 30, pct: 25, reset: 0, reset_text: '' },
      { kind: 'monthly', used: 18.55, limit: 60, pct: 31, reset: 0, reset_text: '' },
    ],
    stats: [
      goStat(),
      goStat({ key: 'kimi-k3|go', model: 'kimi-k3', name: 'Kimi K3', count_m: 30, cost_m: 6.2, effective_remain: 0, remain_cnt: 0 }),
      goStat({ key: 'grok-4.5|go', model: 'grok-4.5', name: 'Grok 4.5', count_m: 20, cost_m: 3.05, effective_remain: 0, remain_cnt: 0 }),
      goStat({ key: 'glm-5.1|go', model: 'glm-5.1', name: 'GLM-5.1', count_m: 12, cost_m: 2.1, effective_remain: 0, remain_cnt: 0 }),
      goStat({ key: 'kimi-k2.6|go', model: 'kimi-k2.6', name: 'Kimi K2.6', count_m: 8, cost_m: 1.4, effective_remain: 0, remain_cnt: 0 }),
      { key: 'big-pickle|router', model: 'big-pickle', name: 'Big Pickle', source: 'router', group: 'free', count_total: 15, count_m: 8, count_w: 4, count_s: 1, tokens_in: 100, tokens_out: 60, tokens_cache: 40, used: true },
      { key: 'mimo-v2.5-free|zen', model: 'mimo-v2.5-free', name: 'MiMo-V2.5', source: 'zen', group: 'free', count_total: 0, used: false, tokens_in: 0, tokens_out: 0, tokens_cache: 0 },
    ],
    history: [],
    suppliers: { go: { tokens: 3500, cost: 18.55, count: 90, days: 3 } },
    heatmap: [],
    rows: 42,
    key: true,
    models: 3,
    server: null,
    calibration: null,
  };
}

function viewFixture(id) {
  return {
    id, label: id, scope: { all: true }, range: 'all', group: null,
    totals: { cost: 1.5, tokens: 1000, count: 10, input: 100, output: 200, cache: 700, days: 2 },
    daily: [{ date: '2026-09-28', cost: 1.5, count: 10, tokens: 1000, input: 100, output: 200, cache: 700 }],
  };
}

function makeBridge() {
  const calls = { resize: [], saveUiState: [], apiGetView: [], apiGetState: 0, exitSnap: 0 };
  const api = {
    resize: (s) => { calls.resize.push(s); return Promise.resolve(true); },
    saveUiState: (s) => { calls.saveUiState.push(s); return Promise.resolve(s); },
    openLogin: () => Promise.resolve({ opened: true }),
    grabAuth: () => Promise.resolve({ ok: true }),
    apiGetState: () => { calls.apiGetState += 1; return Promise.resolve(stateFixture()); },
    apiGetConfig: () => Promise.resolve({}),
    apiGetFormula: () => Promise.resolve({
      params: { meter: { ratio: 1.4212 }, sources: { subset_of: { gateway: 'go' } } },
    }),
    apiGetViews: () => Promise.resolve([]),
    apiGetView: (id) => { calls.apiGetView.push(id); return Promise.resolve(viewFixture(id)); },
    apiGetAgents: () => Promise.resolve([]),
    apiGetModels: () => Promise.resolve([]),
    apiGetProviders: () => Promise.resolve([]),
    apiGetSessions: () => Promise.resolve([]),
    apiGetTimeline: () => Promise.resolve([]),
    apiGetForecast: () => Promise.resolve({}),
    apiPostKey: () => Promise.resolve({ ok: true }),
    apiPostServer: () => Promise.resolve({ ok: true }),
    apiPostCalibrate: () => Promise.resolve({ ok: true }),
    apiPostSync: () => Promise.resolve({ ok: true }),
    apiGetNotificationSettings: () => Promise.resolve({ enabled: false }),
    apiSetNotificationSettings: () => Promise.resolve({ ok: true }),
    setClickThrough: () => Promise.resolve(true),
    exitSnap: () => { calls.exitSnap += 1; return Promise.resolve(true); },
    quit: () => Promise.resolve(true),
    onSnapSmall: (cb) => { api._onSnapSmall = cb; },
    onSnapRestore: (cb) => { api._onSnapRestore = cb; },
    onTrayRefresh: (cb) => { api._onTrayRefresh = cb; },
    onOpenForecast: (cb) => { api._onOpenForecast = cb; },
  };
  return { api, calls };
}

// ---------------------------------------------------------------------------
// loader
// ---------------------------------------------------------------------------
function loadWidgetApp() {
  const doc = makeDocument();
  const timers = makeTimers();
  const store = makeStorage();
  const { api, calls } = makeBridge();
  const win = { widgetAPI: api, addEventListener() {}, innerWidth: 560 };
  const src = FORMAT_SRC + '\n' + CORE_SRC + '\n' + VIEWS_SRC + '\n' + FORECAST_SRC + '\n' + APPJS_SRC;
  const factory = new Function(
    'document', 'window', 'localStorage',
    'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
    src +
    '\n;return { setUiState, render, refresh, fillModelList, rankSortedStats,'
    + ' state, OCW, getUiState: function () { return uiState; },'
    + ' getSelSupplier: function () { return selSupplier; } };'
  );
  const exposed = factory(doc, win, store, timers.setTimeout, timers.setInterval,
    timers.clearTimeout, timers.clearInterval);
  return Object.assign({ doc, timers, calls, api, els: doc._els, src: APPJS_SRC }, exposed);
}

function el(app, sel) {
  const found = app.els.get(sel);
  assert.ok(found, 'stub element missing: ' + sel);
  return found;
}

async function bootApp() {
  const app = loadWidgetApp();
  await app.timers.flush();
  return app;
}

function assertNoRenderError(app) {
  const foot = app.els.get('#footRight');
  const msg = foot ? String(foot.textContent) : '';
  assert.ok(!msg.startsWith('ERR'), 'render() swallowed an error: ' + msg);
}

function dispatchKeydown(app, event) {
  const handlers = app.doc.handlers.keydown || [];
  assert.ok(handlers.length, 'no document keydown handler registered');
  handlers.forEach((fn) => fn(Object.assign({ preventDefault() {} }, event)));
}

function dispatchKeydownPrevented(app, event) {
  const handlers = app.doc.handlers.keydown || [];
  assert.ok(handlers.length, 'no document keydown handler registered');
  let prevented = false;
  handlers.forEach((fn) => fn(Object.assign(
    { preventDefault() { prevented = true; } }, event)));
  return prevented;
}

// --- 6. keyboard reachability (Tab must keep native focus traversal) ---------
test('Tab is not globally prevented and no longer cycles suppliers', async () => {
  const app = await bootApp();
  app.setUiState('large');
  await app.timers.flush();
  const before = app.getSelSupplier();
  const prevented = dispatchKeydownPrevented(app, { key: 'Tab' });
  assert.ok(!prevented, 'Tab must keep native focus traversal in expanded');
  assert.equal(app.getSelSupplier(), before, 'Tab must not cycle suppliers');

  app.setUiState('mid');
  await app.timers.flush();
  assert.ok(!dispatchKeydownPrevented(app, { key: 'Tab' }),
    'Tab must keep native focus traversal in compact');

  app.setUiState('small');
  await app.timers.flush();
  assert.ok(!dispatchKeydownPrevented(app, { key: 'Tab' }),
    'Tab must keep native focus traversal in mini');
});

test('Shift+Tab keeps native reverse navigation', async () => {
  const app = await bootApp();
  app.setUiState('large');
  await app.timers.flush();
  const before = app.getSelSupplier();
  assert.ok(!dispatchKeydownPrevented(app, { key: 'Tab', shiftKey: true }),
    'Shift+Tab must keep native reverse traversal');
  assert.equal(app.getSelSupplier(), before, 'Shift+Tab must not cycle suppliers');
});

test('Tab is native inside inputs and while modals are open', async () => {
  const app = await bootApp();
  const preventedInput = dispatchKeydownPrevented(app,
    { key: 'Tab', target: { closest: () => ({ tagName: 'INPUT' }) } });
  assert.ok(!preventedInput, 'input context must not hijack Tab');

  el(app, '#calMask').classList.add('show');
  const preventedModal = dispatchKeydownPrevented(app, { key: 'Tab' });
  assert.ok(!preventedModal, 'modal context must not hijack Tab');
});

test('Enter and Space activate the mini bar (Space prevents page side effects)', async () => {
  const app = await bootApp();
  const bar = el(app, '#miniBar');
  const handlers = bar.handlers.keydown || [];
  assert.ok(handlers.length, 'mini bar must have a keydown handler');

  handlers.forEach((fn) => fn({ key: 'Enter', preventDefault() {} }));
  assert.equal(app.getUiState(), 'mid', 'Enter must open compact from mini');

  app.setUiState('small');
  await app.timers.flush();
  let spacePrevented = false;
  handlers.forEach((fn) => fn({ key: ' ', preventDefault() { spacePrevented = true; } }));
  assert.equal(app.getUiState(), 'mid', 'Space must open compact from mini');
  assert.ok(spacePrevented, 'Space activation must preventDefault the page side effect');
});

function click(app, sel) {
  const target = el(app, sel);
  const handlers = (target.handlers.click || []).slice();
  if (typeof target.onclick === 'function') handlers.push(target.onclick);
  assert.ok(handlers.length, 'no click handler on ' + sel);
  handlers.forEach((fn) => fn({ target }));
}

// ---------------------------------------------------------------------------
// sizes contract (parsed from main.js)
// ---------------------------------------------------------------------------
function parseSizes(src) {
  const m = /const\s+SIZES\s*=\s*\{[^}]*small:\s*\[(\d+),\s*(\d+)\][^}]*mid:\s*\[(\d+),\s*(\d+)\][^}]*large:\s*\[(\d+),\s*(\d+)\]/.exec(src);
  assert.ok(m, 'SIZES block not found in main.js');
  return {
    small: [Number(m[1]), Number(m[2])],
    mid: [Number(m[3]), Number(m[4])],
    large: [Number(m[5]), Number(m[6])],
  };
}

// --- 1. default launch -------------------------------------------------------
test('cold start settles on mini: smallView shown, resize+save report small', async () => {
  const app = await bootApp();
  assertNoRenderError(app);
  assert.equal(app.getUiState(), 'small');
  assert.ok(el(app, '#smallView').classList.contains('on'), 'smallView must have .on');
  assert.equal(el(app, '#compactView').style.display, 'none');
  assert.ok(!el(app, '#dashView').classList.contains('on'), 'dashView must be off');
  assert.equal(app.calls.resize.length, 0,
    'cold start must not resize: the window is born mini-sized (anti-flicker)');
  assert.equal(app.calls.saveUiState[app.calls.saveUiState.length - 1], 'small');
});

test('window sizes: mini is a slim bar, ordering small < mid < large', () => {
  const sizes = parseSizes(MAINJS_SRC);
  assert.ok(sizes.small[0] < sizes.mid[0], 'mini narrower than compact');
  assert.ok(sizes.small[1] < sizes.mid[1], 'mini shorter than compact');
  assert.ok(sizes.mid[0] < sizes.large[0] && sizes.mid[1] < sizes.large[1], 'compact < expanded');
  assert.ok(sizes.small[1] <= 120, 'mini must be a slim bar (height <= 120), got ' + sizes.small[1]);
  assert.deepEqual(sizes.large, [960, 720], 'expanded keeps the large dashboard size');
});

test('cold start window is born at the mini size', () => {
  const m = /function\s+createWindow\(\)\s*\{[\s\S]*?width:\s*SIZES\.(\w+)\[[^\]]*\][\s\S]*?height:\s*SIZES\.(\w+)\[/.exec(MAINJS_SRC);
  assert.ok(m, 'createWindow must size the window from a SIZES entry');
  assert.equal(m[1], 'small');
  assert.equal(m[2], 'small');
});

// --- 2. transitions ----------------------------------------------------------
test('mini -> compact: clicking the mini bar opens the quick view without refetching', async () => {
  const app = await bootApp();
  const before = app.calls.apiGetState;
  click(app, '#smallView');
  await app.timers.flush();
  assertNoRenderError(app);
  assert.equal(app.getUiState(), 'mid');
  assert.equal(el(app, '#compactView').style.display, 'flex');
  assert.ok(!el(app, '#smallView').classList.contains('on'));
  assert.equal(app.calls.apiGetState, before, 'mode switch must not refetch state');
});

test('compact -> expanded: the full-panel button opens the dashboard', async () => {
  const app = await bootApp();
  click(app, '#smallView');
  await app.timers.flush();
  click(app, '#btnOpenFull');
  await app.timers.flush();
  assertNoRenderError(app);
  assert.equal(app.getUiState(), 'large');
  assert.ok(el(app, '#dashView').classList.contains('on'));
  assert.ok(app.calls.resize.includes('large'));
});

test('expanded -> mini: the collapse button lands on the mini bar', async () => {
  const app = await bootApp();
  app.setUiState('large');
  await app.timers.flush();
  click(app, '#btnMin');
  await app.timers.flush();
  assert.equal(app.getUiState(), 'small');
  assert.ok(el(app, '#smallView').classList.contains('on'));
  assert.ok(!el(app, '#dashView').classList.contains('on'));
  assert.ok(app.calls.resize.includes('small'));
});

test('entering expanded fetches missing views once; re-expanding hits the cache', async () => {
  const app = await bootApp();
  const viewsAtMini = app.calls.apiGetView.length;
  assert.equal(viewsAtMini, 0,
    'mini/compact must not prefetch any views (only /api/state + formula)');
  app.setUiState('large');
  await app.timers.flush();
  const viewsAtLarge = app.calls.apiGetView.length;
  assert.ok(viewsAtLarge > viewsAtMini, 'expanding must fetch the dashboard views');
  app.setUiState('small');
  await app.timers.flush();
  app.setUiState('large');
  await app.timers.flush();
  assert.equal(app.calls.apiGetView.length, viewsAtLarge,
    're-expanding must serve from cache, no refetch');
});

test('Esc does not collapse the window beneath an open modal or context menu', async () => {
  const app = await bootApp();
  app.setUiState('large');
  await app.timers.flush();
  el(app, '#keyMask').classList.add('show');
  dispatchKeydown(app, { key: 'Escape' });
  assert.equal(app.getUiState(), 'large', 'Esc must not shrink the window under a modal');
  el(app, '#keyMask').classList.remove('show');
  el(app, '#ctx').classList.add('show');
  dispatchKeydown(app, { key: 'Escape' });
  assert.equal(app.getUiState(), 'large', 'Esc must not shrink the window under the context menu');
  el(app, '#ctx').classList.remove('show');
  dispatchKeydown(app, { key: 'Escape' });
  assert.equal(app.getUiState(), 'small', 'Esc collapses once overlays are closed');
});

test('Esc collapses compact/expanded to mini but is ignored while typing', async () => {
  const app = await bootApp();
  app.setUiState('mid');
  await app.timers.flush();
  dispatchKeydown(app, { key: 'Escape' });
  assert.equal(app.getUiState(), 'small', 'Esc in compact returns to mini');

  app.setUiState('large');
  await app.timers.flush();
  dispatchKeydown(app, { key: 'Escape', target: { closest: () => ({ tagName: 'INPUT' }) } });
  assert.equal(app.getUiState(), 'large', 'Esc must be ignored inside inputs');
  dispatchKeydown(app, { key: 'Escape' });
  assert.equal(app.getUiState(), 'small', 'Esc in expanded returns to mini');
});

// --- 3. compact content policy ----------------------------------------------
test('compact model list shows at most Top 3; expanded shows all', async () => {
  const app = await bootApp();
  click(app, '#smallView');
  await app.timers.flush();
  assertNoRenderError(app);
  const compactItems = el(app, '#cList').children
    .filter((c) => String(c.className).includes('m-item'));
  assert.equal(compactItems.length, 3, 'compact list must be capped at Top 3, got ' + compactItems.length);

  app.setUiState('large');
  await app.timers.flush();
  const dashItems = el(app, '#dList').children
    .filter((c) => String(c.className).includes('m-item'));
  assert.equal(dashItems.length, 5, 'expanded list must show every supplier model, got ' + dashItems.length);
});

// --- 4. text rendering --------------------------------------------------------
test('remaining-count badge renders as styled text, not escaped markup', async () => {
  const app = await bootApp();
  click(app, '#smallView');
  await app.timers.flush();
  const html = el(app, '#cList').children.map((c) => String(c.innerHTML)).join('');
  assert.ok(html.includes('<span class="rem">剩420次</span>'),
    'rem badge must render as a styled span, got: ' + html.slice(0, 400));
  assert.ok(!html.includes('&lt;span'), 'no escaped markup may leak into the list');
});

// --- 5. notification / snap contracts ----------------------------------------
test('notification click still reaches expanded mode with the forecast tab', async () => {
  const app = await bootApp();
  assert.equal(typeof app.api._onOpenForecast, 'function', 'onOpenForecast must be wired');
  app.api._onOpenForecast();
  await app.timers.flush();
  assert.equal(app.getUiState(), 'large');
  assert.ok(el(app, '#dashView').classList.contains('on'));
  assert.equal(app.OCW.state.tab, 'forecast');
});

test('snap restore re-applies the reported state without resizing from the renderer', async () => {
  const app = await bootApp();
  assert.equal(typeof app.api._onSnapRestore, 'function');
  app.setUiState('large');
  await app.timers.flush();
  const resizesBefore = app.calls.resize.length;
  app.api._onSnapRestore('small');
  await app.timers.flush();
  assert.equal(app.getUiState(), 'small');
  assert.equal(app.calls.resize.length, resizesBefore,
    'snap restore is resized by main, renderer must not resize again');
});
