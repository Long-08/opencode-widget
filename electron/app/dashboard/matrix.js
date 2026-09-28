// Phase 5B: Agent x Model matrix for the observability dashboard.
// Pure view-model helpers first (node:test friendly), then a DOM renderer. The
// matrix only reads values the backend already produced: agent/model identity is
// preserved verbatim (raw agent name, provider, variant) and no cost/quota/burn
// rate is derived. Cells are summed for display only. The renderer never
// evaluates policy and never fetches: a metric switch re-renders from
// the agents payload already in memory.

const MATRIX_METRICS = ["requests", "tokens", "cost"];
const MATRIX_TOP_N = 20;

const MATRIX_METRIC_LABELS = { requests: "Requests", tokens: "Tokens", cost: "Raw Cost" };

function matrixLabel(metric) {
  if (typeof metricLabel === "function") return metricLabel(metric);
  if (metric != null && Object.prototype.hasOwnProperty.call(MATRIX_METRIC_LABELS, metric)) {
    return MATRIX_METRIC_LABELS[metric];
  }
  return "Requests";
}

// Column identity is model + provider + variant (never just the model name).
function matrixColKey(modelEntry) {
  if (!modelEntry) return "";
  const model = modelEntry.model == null ? "" : String(modelEntry.model);
  const provider = modelEntry.provider == null ? "" : String(modelEntry.provider);
  const variant = modelEntry.variant == null ? "" : String(modelEntry.variant);
  return model + "|" + provider + "|" + variant;
}

// One agent model entry -> the selected metric (missing -> 0).
function matrixCellValue(modelEntry, metric) {
  if (!modelEntry) return 0;
  if (metric === "tokens") return dashMetric(modelEntry.tokens) || 0;
  if (metric === "cost") return dashMetric(modelEntry.cost) || 0;
  return dashMetric(modelEntry.requests) || 0;
}

// Visual-only intensity in [0,1]; monotonic, bounded, and it never mutates the
// raw value it is derived from. log1p keeps small-but-nonzero cells visible.
function intensity(value, max) {
  const v = Number(value);
  const mx = Number(max);
  if (!isFinite(v) || !isFinite(mx) || mx <= 0 || v <= 0) return 0;
  return Math.min(1, Math.log1p(v) / Math.log1p(mx));
}

function topN(list, n) {
  const src = list || [];
  const limit = (typeof n === "number" && isFinite(n) && n >= 0) ? Math.floor(n) : src.length;
  return src.slice(0, limit);
}

// "" when nothing is dropped; otherwise a visible, explicit note.
function matrixTruncationNote(rowCount, colCount, metric, limit) {
  const n = (typeof limit === "number" && limit > 0) ? limit : MATRIX_TOP_N;
  const rowCut = rowCount > n;
  const colCut = colCount > n;
  if (!rowCut && !colCut) return "";
  const label = matrixLabel(metric);
  let scope;
  if (rowCut && colCut) scope = " (agents and models)";
  else if (rowCut) scope = " (agents)";
  else scope = " (models)";
  return "Showing top " + n + " by " + label + scope + ".";
}

// Pure switch decision: re-render from memory, never refetch.
function matrixMetricSwitch(current, clicked) {
  const metric = MATRIX_METRICS.indexOf(clicked) >= 0 ? clicked : current;
  return { metric: metric, refetch: false };
}

// Build {rows, cols} from the (already filtered) agents payload. Rows keep the
// raw agent name (including "unknown"); cols key on model+provider+variant.
// Both are sorted by the selected metric total, descending.
function matrixFromAgents(agents, metric) {
  const m = MATRIX_METRICS.indexOf(metric) >= 0 ? metric : "tokens";
  const colMap = {};
  const colOrder = [];
  const rows = [];
  const list = agents || [];
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (!a) continue;
    const cells = {};
    const models = a.models || [];
    for (let j = 0; j < models.length; j++) {
      const md = models[j];
      if (!md) continue;
      const key = matrixColKey(md);
      if (!key) continue;
      if (!Object.prototype.hasOwnProperty.call(cells, key)) cells[key] = 0;
      cells[key] += matrixCellValue(md, m);
      if (!Object.prototype.hasOwnProperty.call(colMap, key)) {
        colMap[key] = {
          key: key,
          model: md.model == null ? "" : String(md.model),
          provider: md.provider == null ? "" : String(md.provider),
          variant: md.variant == null ? "" : String(md.variant),
          total: 0,
        };
        colOrder.push(key);
      }
      colMap[key].total += matrixCellValue(md, m);
    }
    let total = 0;
    const keys = Object.keys(cells);
    for (let k = 0; k < keys.length; k++) total += cells[keys[k]];
    rows.push({ agent: a.agent, group: a.group, total: total, cells: cells });
  }
  rows.sort(function (x, y) { return (y.total - x.total) || dashCompare(x.agent, y.agent); });
  const cols = colOrder.map(function (k) { return colMap[k]; });
  cols.sort(function (x, y) { return (y.total - x.total) || dashCompare(x.key, y.key); });
  return { rows: rows, cols: cols };
}

// ---- DOM renderer ----------------------------------------------------------

const MATRIX_STATE = { metric: "tokens", last: null, wired: false };

function matrixEffectiveMetric(opts) {
  const o = opts || {};
  return MATRIX_METRICS.indexOf(o.metric) >= 0 ? o.metric : MATRIX_STATE.metric;
}

function matrixClear(container) {
  while (container.firstChild) container.removeChild(container.firstChild);
}

// Agent+model cell detail for the tooltip (all three metrics, read verbatim).
function matrixDetailMap(agents) {
  const map = {};
  const list = agents || [];
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (!a) continue;
    const models = a.models || [];
    for (let j = 0; j < models.length; j++) {
      const md = models[j];
      if (!md) continue;
      const id = String(a.agent == null ? "" : a.agent) + "\u0000" + matrixColKey(md);
      const d = map[id] || (map[id] = { requests: 0, tokens: 0, cost: 0 });
      d.requests += chartNumSafe(md.requests);
      d.tokens += chartNumSafe(dashMetric(md.tokens));
      d.cost += chartNumSafe(md.cost);
    }
  }
  return map;
}

function matrixMetricButtons(active) {
  const wrap = document.createElement("div");
  wrap.className = "obs-metric-btns";
  for (let i = 0; i < MATRIX_METRICS.length; i++) {
    const m = MATRIX_METRICS[i];
    const b = document.createElement("button");
    b.type = "button";
    b.className = "obs-metric-btn" + (m === active ? " on" : "");
    b.setAttribute("data-matrix-metric", m);
    b.setAttribute("aria-pressed", m === active ? "true" : "false");
    b.textContent = matrixLabel(m);
    wrap.appendChild(b);
  }
  return wrap;
}

function matrixCellText(td, cellValue, metric) {
  td.textContent = timelineFormatValue(cellValue, metric);
}

function renderMatrix(container, agents, opts) {
  if (!container || typeof document === "undefined") return;
  const o = opts || {};
  const metric = matrixEffectiveMetric(o);
  MATRIX_STATE.last = { container: container, agents: agents, opts: o };

  matrixClear(container);
  const card = document.createElement("div");
  card.className = "obs-table-card obs-matrix";
  card.style.position = "relative";

  const title = document.createElement("h4");
  title.textContent = "Agent × Model";
  card.appendChild(title);
  card.appendChild(matrixMetricButtons(metric));

  const matrix = matrixFromAgents(agents, metric);
  const rows = topN(matrix.rows, MATRIX_TOP_N);
  const cols = topN(matrix.cols, MATRIX_TOP_N);
  const note = matrixTruncationNote(matrix.rows.length, matrix.cols.length, metric, MATRIX_TOP_N);
  if (note) {
    const noteEl = document.createElement("div");
    noteEl.className = "obs-matrix-note";
    noteEl.textContent = note;
    card.appendChild(noteEl);
  }
  if (!rows.length || !cols.length) {
    const empty = document.createElement("div");
    empty.className = "obs-state";
    empty.textContent = "No Agent or Model usage in this range.";
    card.appendChild(empty);
    container.appendChild(card);
    return;
  }

  const detail = matrixDetailMap(agents);
  let max = 0;
  for (let i = 0; i < rows.length; i++) {
    for (let j = 0; j < cols.length; j++) {
      const v = rows[i].cells[cols[j].key] || 0;
      if (v > max) max = v;
    }
  }

  const scroller = document.createElement("div");
  scroller.className = "obs-matrix-scroll";
  scroller.style.overflow = "auto";
  scroller.style.maxHeight = "420px";

  const table = document.createElement("table");
  table.className = "obs-table obs-matrix-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const corner = document.createElement("th");
  corner.textContent = "Agent";
  corner.style.position = "sticky";
  corner.style.top = "0";
  corner.style.left = "0";
  corner.style.background = "#0b0e14";
  headRow.appendChild(corner);
  for (let j = 0; j < cols.length; j++) {
    const c = cols[j];
    const th = document.createElement("th");
    th.textContent = c.model || "—";
    th.title = (c.model || "—") + (c.provider ? " · " + c.provider : "") + (c.variant ? " · " + c.variant : "");
    th.style.position = "sticky";
    th.style.top = "0";
    th.style.background = "#0b0e14";
    th.style.whiteSpace = "nowrap";
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = dashNull(row.agent);
    th.style.position = "sticky";
    th.style.left = "0";
    th.style.background = "#0b0e14";
    th.style.whiteSpace = "nowrap";
    if (row.group != null && row.group !== "") {
      const badge = document.createElement("span");
      badge.className = "obs-badge";
      badge.textContent = String(row.group);
      th.appendChild(badge);
    }
    tr.appendChild(th);
    for (let j = 0; j < cols.length; j++) {
      const col = cols[j];
      const cellValue = row.cells[col.key] || 0;
      const td = document.createElement("td");
      td.className = "obs-matrix-cell";
      td.tabIndex = 0;
      td.setAttribute("data-mx-cell", "1");
      matrixCellText(td, cellValue, metric);
      const alpha = 0.08 + intensity(cellValue, max) * 0.82;
      td.style.background = "rgba(99,102,241," + (Math.round(alpha * 100) / 100) + ")";
      td.style.textAlign = "right";
      td.style.whiteSpace = "nowrap";
      td.setAttribute("aria-label", dashNull(row.agent) + ", " + (col.model || "—") + ", " + timelineFormatValue(cellValue, metric));
      const d = detail[String(row.agent == null ? "" : row.agent) + "\u0000" + col.key] || { requests: 0, tokens: 0, cost: 0 };
      td.setAttribute("data-mx-agent", String(row.agent == null ? "—" : row.agent));
      td.setAttribute("data-mx-group", row.group == null ? "—" : String(row.group));
      td.setAttribute("data-mx-model", col.model || "—");
      td.setAttribute("data-mx-provider", col.provider || "—");
      td.setAttribute("data-mx-variant", col.variant || "—");
      td.setAttribute("data-mx-requests", formatInteger(d.requests));
      td.setAttribute("data-mx-tokens", formatTokens(d.tokens));
      td.setAttribute("data-mx-cost", formatCost(d.cost));
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scroller.appendChild(table);
  card.appendChild(scroller);
  container.appendChild(card);
}

// Shared singleton tooltip; a document-level delegated listener is bound once.
function matrixTooltipText(cell) {
  return "Agent: " + (cell.getAttribute("data-mx-agent") || "—")
    + "\nGroup: " + (cell.getAttribute("data-mx-group") || "—")
    + "\nModel: " + (cell.getAttribute("data-mx-model") || "—")
    + "\nProvider: " + (cell.getAttribute("data-mx-provider") || "—")
    + "\nVariant: " + (cell.getAttribute("data-mx-variant") || "—")
    + "\nRequests: " + (cell.getAttribute("data-mx-requests") || "—")
    + "\nTokens: " + (cell.getAttribute("data-mx-tokens") || "—")
    + "\nRaw Cost: " + (cell.getAttribute("data-mx-cost") || "—");
}

function matrixShowTooltip(cell) {
  const card = cell.closest(".obs-matrix");
  if (!card) return;
  card.style.position = "relative";
  const tip = tooltipFor(card);
  if (!tip) return;
  tip.textContent = matrixTooltipText(cell);
  tip.style.display = "block";
  const cardRect = card.getBoundingClientRect();
  const cellRect = cell.getBoundingClientRect();
  let left = cellRect.left - cardRect.left + 8;
  const tw = tip.getBoundingClientRect().width;
  if (left + tw > cardRect.width) left = Math.max(2, cardRect.width - tw - 2);
  tip.style.left = left + "px";
  tip.style.top = Math.max(2, cellRect.bottom - cardRect.top + 6) + "px";
}

function matrixHideTooltip(cell) {
  const card = cell && cell.closest ? cell.closest(".obs-matrix") : null;
  if (!card) return;
  const tip = card.querySelector(".obs-chart-tip");
  if (tip) tip.style.display = "none";
}

(function initMatrix() {
  if (typeof window === "undefined" || !window || typeof document === "undefined") return;
  const W = window.OCW = window.OCW || {};
  W.matrix = MATRIX_STATE;
  if (MATRIX_STATE.wired) return;
  MATRIX_STATE.wired = true;
  document.addEventListener("click", function (e) {
    const t = e.target;
    if (!t || !t.closest) return;
    const b = t.closest("[data-matrix-metric]");
    if (!b) return;
    if (!MATRIX_STATE.last) return;
    const next = matrixMetricSwitch(MATRIX_STATE.metric, b.getAttribute("data-matrix-metric"));
    MATRIX_STATE.metric = next.metric;
    renderMatrix(MATRIX_STATE.last.container, MATRIX_STATE.last.agents, MATRIX_STATE.last.opts);
  });
  document.addEventListener("mouseover", function (e) {
    const t = e.target;
    if (t && t.closest) { const c = t.closest("[data-mx-cell]"); if (c) matrixShowTooltip(c); }
  });
  document.addEventListener("mouseout", function (e) {
    const t = e.target;
    if (t && t.closest) { const c = t.closest("[data-mx-cell]"); if (c) matrixHideTooltip(c); }
  });
  document.addEventListener("focusin", function (e) {
    const t = e.target;
    if (t && t.closest) { const c = t.closest("[data-mx-cell]"); if (c) matrixShowTooltip(c); }
  });
  document.addEventListener("focusout", function (e) {
    const t = e.target;
    if (t && t.closest) { const c = t.closest("[data-mx-cell]"); if (c) matrixHideTooltip(c); }
  });
})();
