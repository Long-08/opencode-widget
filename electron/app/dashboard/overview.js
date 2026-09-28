// Phase 5.1: Overview tab rendering for the observability dashboard.
// Moved verbatim from index.html; state lives on OCW.overview and rendering is
// driven by OCW.obsEnsure/OCW.obsRenderTab (core.js). No API calls at load.

function obsTopAgentsHtml(agents) {
  if (!agents.length) return '<div class="obs-state">' + esc("No Agent usage in this range.") + '</div>';
  const rows = agents.map(function (a) {
    return {
      name: a.agent,
      group: a.group,
      requests: a.requests || 0,
      tokens: (a.tokens && a.tokens.total) || 0,
      raw_cost: a.cost || 0,
      cache_ratio: a.cache_read_ratio,
      top_model: topModelOfAgent(a),
    };
  });
  const sorted = dashSortRows(rows, OCW.overview.sort, "desc", "name");
  const sortBtn = function (field, label) {
    return '<button class="obs-th-btn' + (OCW.overview.sort === field ? " on" : "")
      + '" type="button" data-obs-sort="' + esc(field) + '">' + esc(label) + '</button>';
  };
  let html = '<div class="obs-table-card"><h4>Top Agents</h4><table class="obs-table"><thead><tr>';
  html += '<th>Agent</th>';
  html += '<th>' + sortBtn("requests", "Requests") + '</th>';
  html += '<th>' + sortBtn("tokens", "Tokens") + '</th>';
  html += '<th>' + sortBtn("raw_cost", "Raw Cost") + '</th>';
  html += '<th>Cache Ratio</th><th>Top Model</th></tr></thead><tbody>';
  sorted.forEach(function (r) {
    const m = r.top_model;
    const topModel = m ? esc(m.model) + (m.variant ? " · " + esc(m.variant) : "") : "—";
    html += '<tr>';
    html += '<td>' + esc(dashNull(r.name)) + dashGroupBadge(r.group) + '</td>';
    html += '<td>' + esc(formatInteger(r.requests)) + '</td>';
    html += '<td>' + esc(formatTokens(r.tokens)) + '</td>';
    html += '<td>' + esc(formatCost(r.raw_cost)) + '</td>';
    html += '<td>' + esc(formatPercent(r.cache_ratio)) + '</td>';
    html += '<td>' + topModel + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function obsTopModelsHtml(agents) {
  const rows = topModelsFromAgents(agents);
  if (!rows.length) return "";
  let html = '<div class="obs-table-card"><h4>Top Models</h4><table class="obs-table"><thead><tr>';
  html += '<th>Model</th><th>Provider</th><th>Variant</th><th>Requests</th><th>Tokens</th><th>Raw Cost</th><th>Top Agent</th>';
  html += '</tr></thead><tbody>';
  rows.forEach(function (r) {
    html += '<tr>';
    html += '<td>' + esc(dashNull(r.model)) + '</td>';
    html += '<td>' + esc(dashNull(r.provider)) + '</td>';
    html += '<td>' + esc(dashNull(r.variant)) + '</td>';
    html += '<td>' + esc(formatInteger(r.requests)) + '</td>';
    html += '<td>' + esc(formatTokens(r.tokens)) + '</td>';
    html += '<td>' + esc(formatCost(r.raw_cost)) + '</td>';
    html += '<td>' + esc(dashNull(r.top_agent)) + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function obsTimelineSectionHtml() {
  return '<section class="obs-table-card obs-timeline-card" id="obsOverviewTimeline" '
    + 'aria-label="Usage timeline"></section>';
}

function obsRenderOverview(panel, data, errors) {
  errors = errors || {};
  const agentsResp = (data && data.agents) || {};
  const sessionsResp = (data && data.sessions) || {};
  const state = readerState(agentsResp.reader);
  if (errors.agents || state !== "ok") {
    panel.innerHTML = dashStateHtml(readerMessage(errors.agents ? "error" : state));
    return;
  }
  if (errors.sessions || (sessionsResp.reader && readerState(sessionsResp.reader) !== "ok")) {
    panel.innerHTML = dashStateHtml(readerMessage(errors.sessions ? "error" : readerState(sessionsResp.reader)));
    return;
  }
  const agents = agentsResp.agents || [];
  const sessions = sessionsResp.sessions || [];
  const summary = overviewSummary(agents, sessions);
  const costTip = "Raw cost recorded in OpenCode message data. It is not equivalent to official OpenCode Go quota consumption.";
  const cards = [
    ["Requests", formatInteger(summary.requests), false],
    ["Tokens", formatTokens(summary.tokens), false],
    ["Raw Cost", formatCost(summary.raw_cost), true],
    ["Cache Read Ratio", formatPercent(summary.cache_read_ratio), false],
    ["Active Agents", formatInteger(summary.active_agents), false],
    ["Active Models", formatInteger(summary.active_models), false],
    ["Sessions", formatInteger(summary.sessions), false],
  ];
  let html = '<div class="obs-cards">';
  cards.forEach(function (c) {
    const title = c[2] ? ' title="' + esc(costTip) + '"' : "";
    html += '<div class="obs-card"' + title + '><div class="ok-k">' + esc(c[0])
      + '</div><div class="ok-v">' + esc(c[1]) + '</div></div>';
  });
  html += '</div>';
  html += obsTimelineSectionHtml();
  html += obsTopAgentsHtml(agents);
  html += obsTopModelsHtml(agents);
  panel.innerHTML = html;

  const tl = panel.querySelector("#obsOverviewTimeline");
  if (tl) {
    const tlResp = (data && data.timeline) || {};
    renderTimeline(tl, tlResp.series || [], {
      reader: tlResp.reader,
      bucket: tlResp.bucket,
      error: !!errors.timeline,
    });
  }
}

// ---- tab state + registration ----------------------------------------------
OCW.overview = { sort: "tokens" };

OCW.registerTab("overview", {
  target: function () { return document.querySelector('.obs-panel[data-panel="overview"]'); },
  render: function (panel, data, errors) { obsRenderOverview(panel, data, errors); },
  reset: function () {},
});
