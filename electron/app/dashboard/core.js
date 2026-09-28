// Phase 5.1: DOM-free dashboard core + shared observability state.
// The `phase5a-core` block below is moved verbatim from index.html (the display
// formatters now live in format.js). The orchestration section after the block
// owns the OCW namespace, the in-memory response cache (key tab|range) and the
// lazy per-tab loader. Nothing here calls the API at load time.


/* ==== phase5a-core:start ====
   DOM-free shared dashboard core (Phase 5A). Pure display/sort/fetch helpers;
   no business metrics are recomputed here. Extractable for node:test.
   The only front-end aggregate is summing API-provided Overview numbers. */
const DASH_TABS = ['overview', 'agents', 'models', 'sessions', 'forecast'];
const DASH_RANGES = ['today', '7d', '30d', 'all'];
const DASH_ENDPOINTS = {
  overview: ['agents', 'sessions'],
  agents: ['agents'],
  models: ['models'],
  sessions: ['sessions'],
  forecast: ['forecast'],
};

// 规范化 range: 仅接受 DASH_RANGES, 其余回退 'all' (与后端 OBS_RANGES 对齐)
function dashRangeValue(r) {
  return DASH_RANGES.indexOf(r) >= 0 ? r : 'all';
}

function dashCacheKey(tab, range) {
  // Forecast is range-independent: a single cache entry regardless of range.
  if (tab === 'forecast') return 'forecast';
  return tab + '|' + dashRangeValue(range);
}

function dashEndpointsFor(tab) {
  return (DASH_ENDPOINTS[tab] || []).slice();
}

function dashShouldFetch(tab, range, cache) {
  if (!cache) return true;
  return !Object.prototype.hasOwnProperty.call(cache, dashCacheKey(tab, range));
}

// Lazy loader: 命中缓存且非 force 时直接返回; 缓存键为 dashCacheKey。
// 任一 fetch 被 reject 时不写入缓存并向上抛出 (只更新本次涉及的键)。
async function dashLoad(tab, range, cache, fetchTab, opts) {
  opts = opts || {};
  const key = dashCacheKey(tab, range);
  const endpoints = dashEndpointsFor(tab);
  if (!opts.force && cache && Object.prototype.hasOwnProperty.call(cache, key)) {
    return { key: key, fromCache: true, data: cache[key], fetched: [] };
  }
  const results = await Promise.all(endpoints.map(function (name) {
    return Promise.resolve().then(function () { return fetchTab(name, range); });
  }));
  const data = {};
  endpoints.forEach(function (name, i) { data[name] = results[i]; });
  if (cache) cache[key] = data;
  return { key: key, fromCache: false, data: data, fetched: endpoints.slice() };
}

// 通用升序比较: null/undefined/'' 视为最小值; 数字按数值, 其余按字符串。
function dashCompare(a, b) {
  const an = a == null || a === '';
  const bn = b == null || b === '';
  if (an && bn) return 0;
  if (an) return -1;
  if (bn) return 1;
  if (typeof a === 'number' && typeof b === 'number') {
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  }
  const as = String(a);
  const bs = String(b);
  if (as < bs) return -1;
  if (as > bs) return 1;
  return 0;
}

// 稳定排序: 主字段 desc/asc, 再按 secondary (默认 name) 升序, 最后按原序兜底。
function dashSortRows(rows, field, dir, secondary) {
  const mul = dir === 'asc' ? 1 : -1;
  const sec = secondary || 'name';
  return (rows || []).map(function (row, i) { return { row: row, i: i }; })
    .sort(function (a, b) {
      const p = dashCompare(a.row ? a.row[field] : null, b.row ? b.row[field] : null);
      if (p !== 0) return p * mul;
      const s = dashCompare(a.row ? a.row[sec] : null, b.row ? b.row[sec] : null);
      if (s !== 0) return s;
      return a.i - b.i;
    })
    .map(function (x) { return x.row; });
}

// reader.meta -> 'ok' | 'unsupported' | 'missing' | 'error' (status 优先, schema 兜底)
function readerState(meta) {
  if (!meta || typeof meta !== 'object') return 'error';
  const status = meta.status;
  if (status === 'ok') return 'ok';
  if (status === 'unsupported_schema') return 'unsupported';
  if (status === 'missing_db') return 'missing';
  if (status === 'error') return 'error';
  if (status != null) return 'error';
  const schema = meta.schema;
  if (schema === 'current' || schema === 'legacy') return 'ok';
  if (schema === 'unsupported') return 'unsupported';
  if (schema === 'missing') return 'missing';
  return 'error';
}

function readerMessage(state) {
  if (state === 'ok') return '';
  if (state === 'unsupported') return 'OpenCode usage database schema is not supported.';
  if (state === 'missing') return 'OpenCode usage database was not found.';
  return 'OpenCode usage data could not be read.';
}

// tokens 对象取 total; 数字原样; 其它转数字。无效 -> null。
function dashMetric(v) {
  if (v == null) return null;
  if (typeof v === 'number') return isNaN(v) ? null : v;
  if (typeof v === 'object') {
    const t = v.total;
    if (t == null) return null;
    const tn = Number(t);
    return isNaN(tn) ? null : tn;
  }
  const n = Number(v);
  return isNaN(n) ? null : n;
}

function topBy(rows, field) {
  if (!rows || !rows.length) return null;
  let best = null;
  let bestV = null;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (!row) continue;
    const v = dashMetric(row[field]);
    if (v == null) continue;
    if (bestV == null || v > bestV) { best = row; bestV = v; }
  }
  return best;
}

function topAgentOf(modelItem) {
  if (!modelItem || !modelItem.agents) return null;
  return topBy(modelItem.agents, 'tokens');
}

function topModelOfAgent(agentItem) {
  if (!agentItem || !agentItem.models) return null;
  return topBy(agentItem.models, 'tokens');
}

// Overview 唯一允许的前端聚合: 对 API 已提供的数字求和。
function overviewSummary(agents, sessions) {
  const a = agents || [];
  const s = sessions || [];
  let requests = 0, tokens = 0, rawCost = 0, cacheRead = 0, input = 0;
  const modelKeys = {};
  for (let i = 0; i < a.length; i++) {
    const item = a[i] || {};
    requests += item.requests || 0;
    const t = item.tokens || {};
    tokens += t.total || 0;
    cacheRead += t.cache_read || 0;
    input += t.input || 0;
    rawCost += item.cost || 0;
    const models = item.models || [];
    for (let j = 0; j < models.length; j++) {
      const m = models[j] || {};
      modelKeys[m.model + '|' + m.provider + '|' + m.variant] = true;
    }
  }
  const denom = input + cacheRead;
  return {
    requests: requests,
    tokens: tokens,
    raw_cost: rawCost,
    cache_read_ratio: denom ? cacheRead / denom : null,
    active_agents: a.length,
    active_models: Object.keys(modelKeys).length,
    sessions: s.length,
  };
}

// 将各 agent 的 models[] 按 (model, provider, variant) 汇总; top_agent = tokens 最高者。
function topModelsFromAgents(agents) {
  const a = agents || [];
  const map = {};
  for (let i = 0; i < a.length; i++) {
    const agent = a[i] || {};
    const models = agent.models || [];
    for (let j = 0; j < models.length; j++) {
      const m = models[j] || {};
      const key = m.model + '|' + m.provider + '|' + m.variant;
      let entry = map[key];
      if (!entry) {
        entry = map[key] = {
          model: m.model, provider: m.provider, variant: m.variant,
          requests: 0, tokens: 0, raw_cost: 0,
          top_agent: null, _top_tokens: -1,
        };
      }
      entry.requests += m.requests || 0;
      const mt = dashMetric(m.tokens) || 0;
      entry.tokens += mt;
      entry.raw_cost += m.cost || 0;
      if (mt > entry._top_tokens) { entry._top_tokens = mt; entry.top_agent = agent.agent; }
    }
  }
  const out = Object.keys(map).map(function (k) { return map[k]; });
  out.forEach(function (e) { delete e._top_tokens; });
  out.sort(function (x, y) { return y.tokens - x.tokens; });
  return out;
}

// markup helpers 内部一律 esc(); 仅返回文本的 helper 不含任何标签。
function dashHtmlText(v) {
  return esc(dashNull(v));
}

function dashGroupBadge(group) {
  if (group == null || group === '') return '';
  return '<span class="obs-badge">' + esc(String(group)) + '</span>';
}
/* ==== phase5a-core:end ==== */

// ---- shared dashboard state + orchestration (Phase 5.1) --------------------
// All mutable observability state lives on the OCW namespace: the response
// cache (keyed tab|range), the active tab/range, per-tab panel status and the
// registered per-tab renderers. Requests stay lazy: obsEnsure() is the only
// entry point that calls the API, and only for the active tab + range.
const OCW = (window.OCW = window.OCW || {});
OCW.dashCache = {};                                        // key: dashCacheKey(tab, range)
OCW.state = { tab: "overview", range: "today", panels: {} }; // panels: tab -> { status, data }
OCW.tabs = {};                                             // tab -> registered descriptor

// Each view module calls this once at load with { target, render, reset }.
OCW.registerTab = function (name, api) { OCW.tabs[name] = api || {}; };
OCW.getActiveTab = function () { return OCW.state.tab; };

function obsApi(name, range) {
  const r = dashRangeValue(range);
  if (name === "agents") return window.widgetAPI.apiGetAgents(r);
  if (name === "models") return window.widgetAPI.apiGetModels(r);
  if (name === "sessions") return window.widgetAPI.apiGetSessions(r);
  // Forecast is range-independent: the bridge method takes no argument.
  if (name === "forecast") return window.widgetAPI.apiGetForecast();
  return Promise.reject(new Error("unknown endpoint"));
}

OCW.obsSetActiveUI = function () {
  const tabsBox = document.getElementById("obsTabs");
  if (tabsBox) {
    tabsBox.querySelectorAll(".obs-tab").forEach(function (b) {
      const on = b.dataset.tab === OCW.state.tab;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
  }
  // Forecast is range-independent: hide/disable the range control on that tab
  // and restore it for every other tab. OCW.state.range is never mutated here,
  // so e.g. Agents @ 7d -> Forecast -> Agents stays @ 7d.
  const rangeBox = document.getElementById("obsRange");
  if (rangeBox) {
    const rangeHidden = OCW.state.tab === "forecast";
    rangeBox.style.display = rangeHidden ? "none" : "";
    rangeBox.classList.toggle("obs-range-hidden", rangeHidden);
    rangeBox.setAttribute("aria-hidden", rangeHidden ? "true" : "false");
    rangeBox.querySelectorAll(".obs-r-btn").forEach(function (b) {
      b.disabled = rangeHidden;
      const on = !rangeHidden && b.dataset.range === OCW.state.range;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  document.querySelectorAll(".obs-panel").forEach(function (p) {
    p.classList.toggle("on", p.dataset.panel === OCW.state.tab);
  });
};

function obsTabDescriptor(tab) {
  return OCW.tabs[tab] || null;
}

// Re-render a loaded tab from memory only (no network).
OCW.obsRenderActiveView = function (tab) {
  const st = OCW.state.panels[tab];
  if (!st || st.status !== "ok") return;
  const desc = obsTabDescriptor(tab);
  if (!desc) return;
  const body = desc.target();
  if (!body) return;
  desc.render(body, st.data);
};

const OBS_TAB_LABELS = { agents: "Agent", models: "Model", sessions: "Session", forecast: "Forecast" };

OCW.obsRenderTab = function (tab) {
  tab = DASH_TABS.indexOf(tab) >= 0 ? tab : "overview";
  const desc = obsTabDescriptor(tab);
  if (!desc) return;
  const panel = desc.target();
  if (!panel) return;
  const st = OCW.state.panels[tab] || { status: "idle" };
  if (st.status === "loading") { panel.innerHTML = dashStateHtml("Loading…"); return; }
  if (st.status === "error") {
    panel.innerHTML = dashErrorHtml("Failed to load " + (OBS_TAB_LABELS[tab] || "Agent") + " usage.", tab);
    return;
  }
  if (st.status !== "ok") { panel.innerHTML = ""; return; }
  desc.render(panel, st.data);
};

OCW.obsEnsure = async function (tab, force) {
  tab = DASH_TABS.indexOf(tab) >= 0 ? tab : "overview";
  const key = dashCacheKey(tab, OCW.state.range);
  if (!force && OCW.dashCache[key]) {
    OCW.state.panels[tab] = { status: "ok", data: OCW.dashCache[key] };
    OCW.obsRenderTab(tab);
    return;
  }
  OCW.state.panels[tab] = { status: "loading" };
  OCW.obsRenderTab(tab);
  try {
    const res = await dashLoad(tab, OCW.state.range, OCW.dashCache, obsApi, { force: !!force });
    if (res.key !== dashCacheKey(tab, OCW.state.range)) return; // range 已变化, 丢弃过期结果
    OCW.state.panels[tab] = { status: "ok", data: res.data };
    // New data resets per-panel view state (default expansion, no selection).
    const desc = obsTabDescriptor(tab);
    if (desc && desc.reset) desc.reset();
  } catch (e) {
    OCW.state.panels[tab] = { status: "error" };
  }
  OCW.obsRenderTab(tab);
};

OCW.obsSelectTab = function (tab) {
  if (DASH_TABS.indexOf(tab) < 0) return;
  OCW.state.tab = tab;
  OCW.obsSetActiveUI();
  OCW.obsEnsure(tab);
};

OCW.obsSelectRange = function (range) {
  OCW.state.range = dashRangeValue(range);
  OCW.obsSetActiveUI();
  OCW.obsEnsure(OCW.state.tab);
};

(function initObs() {
  const tabsBox = document.getElementById("obsTabs");
  if (tabsBox) {
    tabsBox.addEventListener("click", function (e) {
      const b = e.target.closest(".obs-tab");
      if (b) OCW.obsSelectTab(b.dataset.tab);
    });
  }
  const rangeBox = document.getElementById("obsRange");
  if (rangeBox) {
    rangeBox.addEventListener("click", function (e) {
      const b = e.target.closest(".obs-r-btn");
      if (b) OCW.obsSelectRange(b.dataset.range);
    });
  }
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    const sort = e.target.closest("[data-obs-sort]");
    if (sort) {
      const tab = sort.dataset.obsTab || "overview";
      if (tab === "agents") { OCW.agents.sort = sort.dataset.obsSort; OCW.obsRenderActiveView("agents"); }
      else if (tab === "models") { OCW.models.sort = sort.dataset.obsSort; OCW.obsRenderActiveView("models"); }
      else {
        OCW.overview.sort = sort.dataset.obsSort;
        if (OCW.state.panels.overview && OCW.state.panels.overview.status === "ok") {
          OCW.tabs.overview.render(OCW.tabs.overview.target(), OCW.state.panels.overview.data);
        }
      }
      return;
    }
    const retry = e.target.closest("[data-obs-retry]");
    if (retry) OCW.obsEnsure(retry.dataset.obsRetry, true);
  });
  OCW.obsSetActiveUI();
})();
