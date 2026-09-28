// Phase 5.1: DOM-free dashboard view-models + display helpers.
// The `phase5a-views` block below is moved verbatim from index.html. It carries
// the Agents/Models/Sessions view-models. Loaded after format.js/core.js; the
// shared <select> filler used by the Agents/Models tabs follows the block.


/* ==== phase5a-views:start ====
   DOM-free Phase 5A view-models + display helpers for the Agents/Models/Sessions
   panels. These consume backend payloads only (no metric recomputation). Raw
   shares stay 0..1 so they can be formatted with formatPercent at render time.
   Any string that reaches innerHTML goes through esc(): dashCell()/dashStateHtml()
   escape text, while the *Html helpers only wrap already-escaped cell objects. */

// A single table cell. `text`/`title` are escaped here so callers can never inject
// remote values; `cls` is a local constant.
function dashCell(text, title, cls) {
  return {
    text: esc(dashNull(text)),
    title: title == null ? "" : esc(String(title)),
    cls: cls == null ? "" : String(cls),
  };
}

// Wrap already-escaped cell objects into <td>s.
function dashCellsHtml(cells) {
  const list = cells || [];
  let html = "";
  for (let i = 0; i < list.length; i++) {
    const c = list[i] || {};
    html += "<td" + (c.cls ? ' class="' + esc(c.cls) + '"' : "")
      + (c.title ? ' title="' + c.title + '"' : "")
      + ">" + (c.text == null ? "" : c.text) + "</td>";
  }
  return html;
}

function dashStateHtml(text, isError) {
  return '<div class="obs-state' + (isError ? " err" : "") + '">' + esc(text) + "</div>";
}

function dashErrorHtml(text, tab) {
  return '<div class="obs-state err">' + esc(text)
    + '<button class="obs-retry" type="button" data-obs-retry="' + esc(tab) + '">Retry</button></div>';
}

// reader.meta -> message ('' when ok). Pure; never renders fake zeros.
function dashReaderError(resp) {
  const state = readerState(resp && resp.reader);
  return state === "ok" ? "" : readerMessage(state);
}

function numOrNull(v) {
  if (v == null) return null;
  const n = Number(v);
  return isNaN(n) ? null : n;
}

function normQuery(q) {
  return String(q == null ? "" : q).trim().toLowerCase();
}

function dashContains(v, query) {
  return v != null && query !== "" && String(v).toLowerCase().indexOf(query) >= 0;
}

// ---- filters ---------------------------------------------------------------

function filterAgents(rows, q) {
  const query = normQuery(q);
  const list = rows || [];
  if (!query) return list.slice();
  return list.filter(function (r) {
    return !!r && (dashContains(r.agent, query) || dashContains(r.group, query));
  });
}

function filterModels(rows, q) {
  const query = normQuery(q);
  const list = rows || [];
  if (!query) return list.slice();
  return list.filter(function (r) {
    return !!r && (dashContains(r.model, query)
      || dashContains(r.provider_id, query) || dashContains(r.source, query));
  });
}

function filterSessions(rows, q) {
  const query = normQuery(q);
  const list = rows || [];
  if (!query) return list.slice();
  return list.filter(function (r) { return !!r && dashContains(r.session_id, query); });
}

// ---- provider / source options --------------------------------------------

// Composite provider identity used by both the <select> and filterByProvider.
function providerKey(r) {
  if (!r) return "";
  const pid = r.provider_id == null ? "" : String(r.provider_id);
  const src = r.source == null ? "" : String(r.source);
  return pid + "|" + src;
}

// Distinct {key,label} discovered from loaded data (dynamic; no hardcoding).
function providerOptions(rows) {
  const seen = {};
  const out = [];
  const list = rows || [];
  for (let i = 0; i < list.length; i++) {
    const r = list[i];
    if (!r) continue;
    const key = providerKey(r);
    if (Object.prototype.hasOwnProperty.call(seen, key)) continue;
    seen[key] = true;
    const pid = r.provider_id == null ? "" : String(r.provider_id);
    const src = r.source == null ? "" : String(r.source);
    let label;
    if (src && pid) label = src + " · " + pid;
    else if (src) label = src;
    else if (pid) label = pid;
    else label = "—";
    out.push({ key: key, label: label });
  }
  return out;
}

// '' or null => no filtering. Matches a direct provider identity, or (for agent
// rows) a nested providers[] entry.
function filterByProvider(rows, key) {
  const list = rows || [];
  if (key == null || key === "") return list.slice();
  return list.filter(function (r) {
    if (!r) return false;
    if (providerKey(r) === key) return true;
    const provs = r.providers;
    if (provs && provs.length) {
      for (let i = 0; i < provs.length; i++) {
        if (providerKey(provs[i]) === key) return true;
      }
    }
    return false;
  });
}

// ---- row cells (Agents / Models / Sessions) --------------------------------

// Agent row: raw agent name primary (group is a separate badge), then display
// metrics. Returns {cells, groupBadge}.
function agentRowCells(a) {
  a = a || {};
  const tm = topModelOfAgent(a);
  const cells = [
    dashCell(a.agent, a.agent, "obs-cell-agent"),
    dashCell(formatInteger(a.requests), null, ""),
    dashCell(formatInteger(a.sessions), null, ""),
    dashCell(formatTokens(dashMetric(a.tokens)), null, ""),
    dashCell(formatCost(a.cost), null, ""),
    dashCell(formatPercent(a.cache_read_ratio), null, ""),
    dashCell(formatTokens(dashMetric(a.avg_tokens_per_request)), null, ""),
    dashCell(formatDuration(a.avg_duration_per_request), null, ""),
    dashCell(tm ? tm.model : null,
      tm ? dashNull(tm.model) + (tm.variant ? " · " + tm.variant : "") : "", ""),
  ];
  return { cells: cells, groupBadge: dashGroupBadge(a.group) };
}

// Model row: variant null -> '—'; Top Agent = topAgentOf(m).agent.
function modelRowCells(m) {
  m = m || {};
  const ta = topAgentOf(m);
  const cells = [
    dashCell(m.model, m.model, "obs-cell-model"),
    dashCell(m.provider_id, null, ""),
    dashCell(m.source, null, ""),
    dashCell(m.variant, null, ""),
    dashCell(formatInteger(m.requests), null, ""),
    dashCell(formatInteger(m.sessions), null, ""),
    dashCell(formatTokens(dashMetric(m.tokens)), null, ""),
    dashCell(formatCost(m.cost), null, ""),
    dashCell(formatPercent(m.cache_read_ratio), null, ""),
    dashCell(ta ? ta.agent : null, null, ""),
  ];
  return { cells: cells };
}

// One tree row -> display cells (session_id shortened, full id in the title).
function sessionRowCells(row) {
  row = row || {};
  return {
    cells: [
      dashCell(shortSessionId(row.session_id), row.session_id, "obs-cell-session"),
      dashCell(formatInteger(row.depth), null, "obs-cell-depth"),
      dashCell(formatInteger(row.requests), null, ""),
      dashCell(formatTokens(row.tokens), null, ""),
      dashCell(formatCost(row.raw_cost), null, ""),
      dashCell(formatInteger(row.agents_count), null, ""),
      dashCell(formatInteger(row.children_count), null, ""),
    ],
  };
}

// Consume the BACKEND tree only. Never recompute depth/cycles/missing-parent.
// Emits a row when the node and all its ancestors are expanded. Defensive when
// sessions[] and tree.nodes disagree.
function sessionTreeRows(sessions, tree, expandedSet) {
  const nodeList = (tree && tree.nodes) || [];
  const metrics = {};
  const sess = sessions || [];
  for (let i = 0; i < sess.length; i++) {
    const s = sess[i] || {};
    if (s.session_id == null) continue;
    metrics[String(s.session_id)] = s;
  }

  const nodeById = {};
  const order = [];
  for (let i = 0; i < nodeList.length; i++) {
    const n = nodeList[i] || {};
    if (n.session_id == null) continue;
    const id = String(n.session_id);
    if (Object.prototype.hasOwnProperty.call(nodeById, id)) continue;
    nodeById[id] = n;
    order.push(id);
  }

  const expanded = expandedSet && typeof expandedSet.has === "function" ? expandedSet : null;
  const isExpanded = function (id) { return expanded ? expanded.has(id) : false; };

  const childrenOf = {};
  const roots = [];
  for (let i = 0; i < order.length; i++) {
    const id = order[i];
    const n = nodeById[id];
    const p = n.parent_session_id;
    const pid = p == null ? null : String(p);
    const missingParent = pid != null && !Object.prototype.hasOwnProperty.call(nodeById, pid);
    const root = n.is_root === true || pid == null || pid === id || missingParent;
    if (root) roots.push(id);
    else (childrenOf[pid] || (childrenOf[pid] = [])).push(id);
  }

  const rows = [];
  const visited = {};
  const walk = function (id, ancestorsExpanded) {
    if (visited[id]) return;
    visited[id] = true;
    if (!ancestorsExpanded) return; // collapsed ancestor -> hide whole subtree
    const n = nodeById[id];
    const m = metrics[id];
    const found = !!m;
    const cc = numOrNull(n.children_count);
    const ex = isExpanded(id);
    rows.push({
      session_id: id,
      parent_session_id: n.parent_session_id == null ? null : String(n.parent_session_id),
      depth: numOrNull(n.depth),
      requests: found ? dashMetric(m.requests) : null,
      tokens: found ? dashMetric(m.tokens) : null,
      raw_cost: found ? dashMetric(m.cost) : null,
      agents_count: found && m.agents ? m.agents.length : null,
      children_count: cc,
      has_children: (cc || 0) > 0,
      expanded: ex,
    });
    const kids = childrenOf[id] || [];
    for (let j = 0; j < kids.length; j++) walk(kids[j], ex);
  };
  for (let i = 0; i < roots.length; i++) walk(roots[i], true);
  return rows;
}

// ---- detail view-models ----------------------------------------------------

// metrics + models rows (raw 0..1 shares preserved) + providers rows.
function agentDetailModel(a) {
  a = a || {};
  const models = (a.models || []).map(function (m) {
    m = m || {};
    return {
      model: m.model,
      provider: m.provider,
      variant: m.variant,
      requests: dashMetric(m.requests),
      tokens: dashMetric(m.tokens),
      raw_cost: dashMetric(m.cost),
      cache_read_ratio: m.cache_read_ratio,
      request_share: m.request_share,
      token_share: m.token_share,
      cost_share: m.cost_share,
    };
  });
  const providers = (a.providers || []).map(function (p) {
    p = p || {};
    return {
      provider_id: p.provider_id,
      source: p.source,
      requests: dashMetric(p.requests),
      tokens: dashMetric(p.tokens),
      raw_cost: dashMetric(p.cost),
      cache_read_ratio: p.cache_read_ratio,
    };
  });
  return {
    agent: a.agent,
    group: a.group,
    metrics: {
      requests: dashMetric(a.requests),
      sessions: dashMetric(a.sessions),
      tokens: dashMetric(a.tokens),
      raw_cost: dashMetric(a.cost),
      cache_read_ratio: a.cache_read_ratio,
      avg_tokens_per_request: a.avg_tokens_per_request,
      avg_duration_per_request: a.avg_duration_per_request,
    },
    models: models,
    providers: providers,
  };
}

// metrics + agents breakdown rows.
function modelDetailModel(m) {
  m = m || {};
  const agents = (m.agents || []).map(function (x) {
    x = x || {};
    return {
      agent: x.agent,
      group: x.group,
      requests: dashMetric(x.requests),
      tokens: dashMetric(x.tokens),
      raw_cost: dashMetric(x.cost),
      cache_read_ratio: x.cache_read_ratio,
    };
  });
  return {
    model: m.model,
    provider_id: m.provider_id,
    source: m.source,
    variant: m.variant,
    metrics: {
      requests: dashMetric(m.requests),
      sessions: dashMetric(m.sessions),
      tokens: dashMetric(m.tokens),
      raw_cost: dashMetric(m.cost),
      cache_read_ratio: m.cache_read_ratio,
    },
    agents: agents,
  };
}

// Session metadata + agents/models/providers rows. Deliberately excludes any
// content/prompt/response/reasoning/tool/path/title field.
function sessionDetailModel(s) {
  s = s || {};
  const mapAgent = function (x) {
    x = x || {};
    return {
      agent: x.agent, group: x.group,
      requests: dashMetric(x.requests),
      tokens: dashMetric(x.tokens),
      raw_cost: dashMetric(x.cost),
    };
  };
  const mapModel = function (x) {
    x = x || {};
    return {
      model: x.model, provider_id: x.provider_id, variant: x.variant,
      requests: dashMetric(x.requests),
      tokens: dashMetric(x.tokens),
      raw_cost: dashMetric(x.cost),
    };
  };
  const mapProvider = function (x) {
    x = x || {};
    return {
      provider_id: x.provider_id, source: x.source,
      requests: dashMetric(x.requests),
      tokens: dashMetric(x.tokens),
      raw_cost: dashMetric(x.cost),
    };
  };
  return {
    session_id: s.session_id,
    parent_session_id: s.parent_session_id,
    start_ts: s.start_ts,
    end_ts: s.end_ts,
    duration_ms: s.duration_ms,
    requests: dashMetric(s.requests),
    tokens: dashMetric(s.tokens),
    raw_cost: dashMetric(s.cost),
    agents: (s.agents || []).map(mapAgent),
    models: (s.models || []).map(mapModel),
    providers: (s.providers || []).map(mapProvider),
  };
}

// ---- small display helpers -------------------------------------------------

function dashSortBtn(tab, field, label, activeField) {
  return '<button class="obs-th-btn' + (activeField === field ? " on" : "")
    + '" type="button" data-obs-tab="' + esc(tab) + '" data-obs-sort="' + esc(field) + '">'
    + esc(label) + "</button>";
}

function dashMetricsHtml(items) {
  let html = '<div class="obs-detail-metrics">';
  const list = items || [];
  for (let i = 0; i < list.length; i++) {
    html += '<span class="obs-detail-item">' + esc(list[i][0]) + "<b>" + esc(dashNull(list[i][1])) + "</b></span>";
  }
  return html + "</div>";
}

function dashDetailTable(title, headers, rows) {
  let html = '<div class="obs-table-card"><h4>' + esc(title) + '</h4><table class="obs-table"><thead><tr>';
  const hs = headers || [];
  for (let i = 0; i < hs.length; i++) html += "<th>" + esc(hs[i]) + "</th>";
  html += "</tr></thead><tbody>";
  const rs = rows || [];
  for (let i = 0; i < rs.length; i++) html += "<tr>" + dashCellsHtml(rs[i]) + "</tr>";
  return html + "</tbody></table></div>";
}
/* ==== phase5a-views:end ==== */

function obsFillProviderSelect(select, options, selected) {
  if (!select) return selected || "";
  const valid = {};
  let html = '<option value="">All providers</option>';
  const list = options || [];
  for (let i = 0; i < list.length; i++) {
    const o = list[i] || {};
    valid[o.key] = true;
    html += '<option value="' + esc(o.key) + '">' + esc(o.label) + "</option>";
  }
  select.innerHTML = html;
  if (selected && valid[selected]) { select.value = selected; return selected; }
  select.value = "";
  return "";
}
