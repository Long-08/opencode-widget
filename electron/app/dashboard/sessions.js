// Phase 5.1: Sessions tab rendering for the observability dashboard.
// Moved verbatim from index.html; mutable state lives on OCW.sessions and the
// payload is received from core.js (no cache/API logic here).

function obsRenderSessions(body, data) {
  const resp = (data && data.sessions) || {};
  const msg = dashReaderError(resp);
  if (msg) { body.innerHTML = dashStateHtml(msg); return; }
  const sessions = resp.sessions || [];
  const tree = resp.tree || null;
  OCW.sessions.data = sessions;
  OCW.sessions.tree = tree;
  const nodes = (tree && tree.nodes) || [];
  if (!nodes.length) {
    OCW.sessions.rows = [];
    body.innerHTML = dashStateHtml("No Session usage in this range.");
    return;
  }
  if (OCW.sessions.expanded == null) {
    // DEFAULT: roots expanded, deeper levels collapsed.
    OCW.sessions.expanded = new Set();
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i] || {};
      if (n.session_id == null) continue;
      const parentless = n.parent_session_id == null || n.parent_session_id === n.session_id;
      if (n.is_root === true || parentless) OCW.sessions.expanded.add(String(n.session_id));
    }
  }
  const rows = sessionTreeRows(sessions, tree, OCW.sessions.expanded);
  OCW.sessions.rows = rows;
  if (!rows.length) { body.innerHTML = dashStateHtml("No Session usage in this range."); return; }
  let html = '<div class="obs-split"><div class="obs-main"><div class="obs-table-card"><table class="obs-table"><thead><tr>';
  html += "<th>Session ID</th><th>Depth</th><th>Requests</th><th>Tokens</th><th>Raw Cost</th><th>Agents</th><th>Children</th>";
  html += "</tr></thead><tbody>";
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const cells = sessionRowCells(row).cells;
    const toggle = row.has_children
      ? '<span class="obs-tree-toggle" data-obs-session-toggle="' + i + '">' + (row.expanded ? "−" : "+") + "</span>"
      : '<span class="obs-tree-toggle leaf">·</span>';
    const indent = '<span class="obs-indent" style="width:' + (Math.max(0, row.depth || 0) * 14) + 'px"></span>';
    cells[0].text = toggle + indent + cells[0].text;
    const sel = OCW.sessions.selected != null && row.session_id === OCW.sessions.selected ? " sel" : "";
    html += '<tr class="obs-row-link' + sel + '" data-obs-session-row="' + i + '">'
      + dashCellsHtml(cells) + "</tr>";
  }
  html += "</tbody></table></div></div>";
  html += obsSessionDrawerHtml();
  html += "</div>";
  body.innerHTML = html;
}

function obsSessionDrawerHtml() {
  if (OCW.sessions.selected == null) return "";
  let row = null;
  for (let i = 0; i < OCW.sessions.rows.length; i++) {
    if (OCW.sessions.rows[i].session_id === OCW.sessions.selected) { row = OCW.sessions.rows[i]; break; }
  }
  if (!row) return "";
  let s = null;
  for (let i = 0; i < OCW.sessions.data.length; i++) {
    if (OCW.sessions.data[i].session_id === OCW.sessions.selected) { s = OCW.sessions.data[i]; break; }
  }
  const d = sessionDetailModel(s);
  let html = '<aside class="obs-drawer"><h4>Session</h4>';
  html += dashMetricsHtml([
    ["Session ID", dashNull(d.session_id)],
    ["Parent Session ID", dashNull(d.parent_session_id)],
    ["Depth", formatInteger(row.depth)],
    ["Start", formatDateTime(d.start_ts)],
    ["End", formatDateTime(d.end_ts)],
    ["Duration", formatDuration(d.duration_ms)],
    ["Requests", formatInteger(d.requests)],
    ["Tokens", formatTokens(d.tokens)],
    ["Raw Cost", formatCost(d.raw_cost)],
  ]);
  html += dashDetailTable("Agents", ["Agent", "Group", "Requests", "Tokens", "Raw Cost"],
    d.agents.map(function (x) {
      return [dashCell(x.agent), dashCell(x.group), dashCell(formatInteger(x.requests)),
        dashCell(formatTokens(x.tokens)), dashCell(formatCost(x.raw_cost))];
    }));
  html += dashDetailTable("Models", ["Model", "Provider", "Variant", "Requests", "Tokens", "Raw Cost"],
    d.models.map(function (x) {
      return [dashCell(x.model), dashCell(x.provider_id), dashCell(x.variant),
        dashCell(formatInteger(x.requests)), dashCell(formatTokens(x.tokens)), dashCell(formatCost(x.raw_cost))];
    }));
  html += dashDetailTable("Providers", ["Provider", "Source", "Requests", "Tokens", "Raw Cost"],
    d.providers.map(function (x) {
      return [dashCell(x.provider_id), dashCell(x.source), dashCell(formatInteger(x.requests)),
        dashCell(formatTokens(x.tokens)), dashCell(formatCost(x.raw_cost))];
    }));
  html += "</aside>";
  return html;
}

function obsSelectSession(i) {
  const r = OCW.sessions.rows[i];
  OCW.sessions.selected = r ? r.session_id : null;
  OCW.obsRenderActiveView("sessions");
}

function obsToggleSession(i) {
  const r = OCW.sessions.rows[i];
  if (!r || !r.has_children) return;
  if (!OCW.sessions.expanded) OCW.sessions.expanded = new Set();
  const id = r.session_id;
  if (OCW.sessions.expanded.has(id)) OCW.sessions.expanded.delete(id);
  else OCW.sessions.expanded.add(id);
  OCW.obsRenderActiveView("sessions");
}

function obsExpandAllSessions() {
  const nodes = (OCW.sessions.tree && OCW.sessions.tree.nodes) || [];
  OCW.sessions.expanded = new Set();
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i] || {};
    if (n.session_id != null) OCW.sessions.expanded.add(String(n.session_id));
  }
  OCW.obsRenderActiveView("sessions");
}

function obsCollapseAllSessions() {
  OCW.sessions.expanded = new Set();
  OCW.obsRenderActiveView("sessions");
}

// ---- tab state + registration ----------------------------------------------
OCW.sessions = { expanded: null, rows: [], data: [], tree: null, selected: null };

OCW.registerTab("sessions", {
  target: function () { return document.getElementById("obsSessionsView"); },
  render: function (body, data) { obsRenderSessions(body, data); },
  reset: function () { OCW.sessions.expanded = null; OCW.sessions.selected = null; },
});

document.addEventListener("click", function (e) {
  if (!e.target.closest) return;
  const expandAll = e.target.closest("[data-obs-expand]");
  if (expandAll) { obsExpandAllSessions(); return; }
  const collapseAll = e.target.closest("[data-obs-collapse]");
  if (collapseAll) { obsCollapseAllSessions(); return; }
  const toggle = e.target.closest("[data-obs-session-toggle]");
  if (toggle) {
    e.stopPropagation();
    obsToggleSession(parseInt(toggle.dataset.obsSessionToggle, 10));
    return;
  }
  const sessionRow = e.target.closest("[data-obs-session-row]");
  if (sessionRow) { obsSelectSession(parseInt(sessionRow.dataset.obsSessionRow, 10)); return; }
});
