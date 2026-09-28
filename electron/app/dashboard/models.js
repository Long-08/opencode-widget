// Phase 5.1: Models tab rendering for the observability dashboard.
// Moved verbatim from index.html; mutable state lives on OCW.models and the
// payload is received from core.js (no cache/API logic here).

function obsRenderModels(body, data) {
  const resp = (data && data.models) || {};
  const msg = dashReaderError(resp);
  if (msg) { body.innerHTML = dashStateHtml(msg); return; }
  const models = resp.models || [];
  OCW.models.provider = obsFillProviderSelect(
    document.getElementById("obsModelProvider"), providerOptions(models), OCW.models.provider);
  const filtered = filterModels(filterByProvider(models, OCW.models.provider), OCW.models.query);
  if (!filtered.length) {
    OCW.models.rows = [];
    body.innerHTML = dashStateHtml("No Model usage in this range.");
    return;
  }
  const norm = filtered.map(function (m) {
    return {
      _key: [m.model, m.provider_id, m.source, m.variant].join("\u0001"),
      requests: m.requests || 0,
      sessions: m.sessions || 0,
      tokens: dashMetric(m.tokens) || 0,
      raw_cost: m.cost || 0,
      _raw: m,
    };
  });
  const sorted = dashSortRows(norm, OCW.models.sort, "desc", "_key");
  OCW.models.rows = sorted;
  let html = '<div class="obs-split"><div class="obs-main"><div class="obs-table-card"><table class="obs-table"><thead><tr>';
  html += "<th>Model</th><th>Provider</th><th>Source</th><th>Variant</th>";
  html += "<th>" + dashSortBtn("models", "requests", "Requests", OCW.models.sort) + "</th>";
  html += "<th>" + dashSortBtn("models", "tokens", "Tokens", OCW.models.sort) + "</th>";
  html += "<th>" + dashSortBtn("models", "raw_cost", "Raw Cost", OCW.models.sort) + "</th>";
  html += "<th>" + dashSortBtn("models", "sessions", "Sessions", OCW.models.sort) + "</th>";
  html += "<th>Cache Ratio</th><th>Top Agent</th>";
  html += "</tr></thead><tbody>";
  for (let i = 0; i < sorted.length; i++) {
    const row = sorted[i];
    const built = modelRowCells(row._raw);
    const sel = OCW.models.selected != null && row._key === OCW.models.selected ? " sel" : "";
    html += '<tr class="obs-row-link' + sel + '" data-obs-model-row="' + i + '">'
      + dashCellsHtml(built.cells) + "</tr>";
  }
  html += "</tbody></table></div></div>";
  html += obsModelDrawerHtml();
  html += "</div>";
  body.innerHTML = html;
}

function obsModelDrawerHtml() {
  if (OCW.models.selected == null) return "";
  let raw = null;
  for (let i = 0; i < OCW.models.rows.length; i++) {
    if (OCW.models.rows[i]._key === OCW.models.selected) { raw = OCW.models.rows[i]._raw; break; }
  }
  if (!raw) return "";
  const d = modelDetailModel(raw);
  let html = '<aside class="obs-drawer"><h4>' + esc(dashNull(d.model)) + "</h4>";
  html += dashMetricsHtml([
    ["Provider", dashNull(d.provider_id)],
    ["Source", dashNull(d.source)],
    ["Variant", dashNull(d.variant)],
    ["Requests", formatInteger(d.metrics.requests)],
    ["Sessions", formatInteger(d.metrics.sessions)],
    ["Tokens", formatTokens(d.metrics.tokens)],
    ["Raw Cost", formatCost(d.metrics.raw_cost)],
    ["Cache Ratio", formatPercent(d.metrics.cache_read_ratio)],
  ]);
  html += dashDetailTable("Agents",
    ["Agent", "Group", "Requests", "Tokens", "Raw Cost", "Cache Ratio"],
    d.agents.map(function (x) {
      return [
        dashCell(x.agent), dashCell(x.group),
        dashCell(formatInteger(x.requests)), dashCell(formatTokens(x.tokens)),
        dashCell(formatCost(x.raw_cost)), dashCell(formatPercent(x.cache_read_ratio)),
      ];
    }));
  html += "</aside>";
  return html;
}

function obsSelectModel(i) {
  const r = OCW.models.rows[i];
  OCW.models.selected = r ? r._key : null;
  OCW.obsRenderActiveView("models");
}

// ---- tab state + registration ----------------------------------------------
OCW.models = { sort: "tokens", query: "", provider: "", selected: null, rows: [] };

OCW.registerTab("models", {
  target: function () { return document.getElementById("obsModelsView"); },
  render: function (body, data) { obsRenderModels(body, data); },
  reset: function () { OCW.models.selected = null; },
});

document.addEventListener("click", function (e) {
  if (!e.target || !e.target.closest) return;
  const modelRow = e.target.closest("[data-obs-model-row]");
  if (modelRow) { obsSelectModel(parseInt(modelRow.dataset.obsModelRow, 10)); return; }
});
document.addEventListener("input", function (e) {
  if (!e.target) return;
  if (e.target.id === "obsModelSearch") { OCW.models.query = e.target.value; OCW.obsRenderActiveView("models"); }
});
document.addEventListener("change", function (e) {
  if (!e.target) return;
  if (e.target.id === "obsModelProvider") { OCW.models.provider = e.target.value; OCW.obsRenderActiveView("models"); }
});
