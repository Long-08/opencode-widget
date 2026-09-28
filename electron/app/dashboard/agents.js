// Phase 5.1: Agents tab rendering for the observability dashboard.
// Moved verbatim from index.html; mutable state lives on OCW.agents and the
// payload is received from core.js (no cache/API logic here).

function obsRenderAgents(body, data) {
  const resp = (data && data.agents) || {};
  const msg = dashReaderError(resp);
  if (msg) { body.innerHTML = dashStateHtml(msg); return; }
  const agents = resp.agents || [];
  const provRows = [];
  for (let i = 0; i < agents.length; i++) {
    const ps = (agents[i] && agents[i].providers) || [];
    for (let j = 0; j < ps.length; j++) provRows.push(ps[j]);
  }
  OCW.agents.provider = obsFillProviderSelect(
    document.getElementById("obsAgentProvider"), providerOptions(provRows), OCW.agents.provider);
  const filtered = filterAgents(filterByProvider(agents, OCW.agents.provider), OCW.agents.query);
  if (!filtered.length) {
    OCW.agents.rows = [];
    body.innerHTML = dashStateHtml("No Agent usage in this range.");
    return;
  }
  const norm = filtered.map(function (a) {
    return {
      agent: a.agent, group: a.group,
      requests: a.requests || 0,
      sessions: a.sessions || 0,
      tokens: dashMetric(a.tokens) || 0,
      raw_cost: a.cost || 0,
      _raw: a,
    };
  });
  const sorted = dashSortRows(norm, OCW.agents.sort, "desc", "agent");
  OCW.agents.rows = sorted;
  let html = '<div class="obs-split"><div class="obs-main"><div class="obs-table-card"><table class="obs-table"><thead><tr>';
  html += "<th>Agent</th>";
  html += "<th>" + dashSortBtn("agents", "requests", "Requests", OCW.agents.sort) + "</th>";
  html += "<th>" + dashSortBtn("agents", "tokens", "Tokens", OCW.agents.sort) + "</th>";
  html += "<th>" + dashSortBtn("agents", "raw_cost", "Raw Cost", OCW.agents.sort) + "</th>";
  html += "<th>" + dashSortBtn("agents", "sessions", "Sessions", OCW.agents.sort) + "</th>";
  html += "<th>Cache Ratio</th><th>Avg Tokens/Request</th><th>Avg Duration</th><th>Top Model</th>";
  html += "</tr></thead><tbody>";
  for (let i = 0; i < sorted.length; i++) {
    const row = sorted[i];
    const built = agentRowCells(row._raw);
    built.cells[0].text += built.groupBadge;
    const sel = OCW.agents.selected != null && row.agent === OCW.agents.selected ? " sel" : "";
    html += '<tr class="obs-row-link' + sel + '" data-obs-agent-row="' + i + '">'
      + dashCellsHtml(built.cells) + "</tr>";
  }
  html += "</tbody></table></div></div>";
  html += obsAgentDrawerHtml();
  html += "</div>";
  body.innerHTML = html;
}

function obsAgentDrawerHtml() {
  if (OCW.agents.selected == null) return "";
  let raw = null;
  for (let i = 0; i < OCW.agents.rows.length; i++) {
    if (OCW.agents.rows[i].agent === OCW.agents.selected) { raw = OCW.agents.rows[i]._raw; break; }
  }
  if (!raw) return "";
  const d = agentDetailModel(raw);
  let html = '<aside class="obs-drawer"><h4>' + esc(dashNull(d.agent)) + dashGroupBadge(d.group) + "</h4>";
  html += dashMetricsHtml([
    ["Requests", formatInteger(d.metrics.requests)],
    ["Sessions", formatInteger(d.metrics.sessions)],
    ["Tokens", formatTokens(d.metrics.tokens)],
    ["Raw Cost", formatCost(d.metrics.raw_cost)],
    ["Cache Ratio", formatPercent(d.metrics.cache_read_ratio)],
    ["Avg Tokens/Request", formatTokens(d.metrics.avg_tokens_per_request)],
    ["Avg Duration", formatDuration(d.metrics.avg_duration_per_request)],
  ]);
  html += dashDetailTable("Models",
    ["Model", "Provider", "Variant", "Requests", "Tokens", "Raw Cost", "Request Share", "Token Share", "Cost Share"],
    d.models.map(function (m) {
      return [
        dashCell(m.model), dashCell(m.provider), dashCell(m.variant),
        dashCell(formatInteger(m.requests)), dashCell(formatTokens(m.tokens)), dashCell(formatCost(m.raw_cost)),
        dashCell(formatPercent(m.request_share)), dashCell(formatPercent(m.token_share)), dashCell(formatPercent(m.cost_share)),
      ];
    }));
  html += dashDetailTable("Providers",
    ["Provider", "Source", "Requests", "Tokens", "Raw Cost", "Cache Ratio"],
    d.providers.map(function (p) {
      return [
        dashCell(p.provider_id), dashCell(p.source),
        dashCell(formatInteger(p.requests)), dashCell(formatTokens(p.tokens)),
        dashCell(formatCost(p.raw_cost)), dashCell(formatPercent(p.cache_read_ratio)),
      ];
    }));
  html += "</aside>";
  return html;
}

function obsSelectAgent(i) {
  const r = OCW.agents.rows[i];
  OCW.agents.selected = r ? r.agent : null;
  OCW.obsRenderActiveView("agents");
}

// ---- tab state + registration ----------------------------------------------
OCW.agents = { sort: "tokens", query: "", provider: "", selected: null, rows: [] };

OCW.registerTab("agents", {
  target: function () { return document.getElementById("obsAgentsView"); },
  render: function (body, data) { obsRenderAgents(body, data); },
  reset: function () { OCW.agents.selected = null; },
});

document.addEventListener("click", function (e) {
  if (!e.target || !e.target.closest) return;
  const agentRow = e.target.closest("[data-obs-agent-row]");
  if (agentRow) { obsSelectAgent(parseInt(agentRow.dataset.obsAgentRow, 10)); return; }
});
document.addEventListener("input", function (e) {
  if (!e.target) return;
  if (e.target.id === "obsAgentSearch") { OCW.agents.query = e.target.value; OCW.obsRenderActiveView("agents"); }
});
document.addEventListener("change", function (e) {
  if (!e.target) return;
  if (e.target.id === "obsAgentProvider") { OCW.agents.provider = e.target.value; OCW.obsRenderActiveView("agents"); }
});
