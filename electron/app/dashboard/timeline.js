// Phase 5B: Usage Timeline visualization for the observability dashboard.
// Pure metric/view-model helpers at the top (usable from node:test) plus a DOM
// renderer driven by the already-verified /api/timeline payload. No totals,
// cost, quota or burn rate are recomputed here: bucket values are read verbatim
// and only summed for display (the same additive step the Overview already
// performs on API numbers). The renderer never triggers any policy evaluation;
// it only renders data passed in by core.js from cache.

const TIMELINE_METRICS = ["requests", "tokens", "cost"];

const TIMELINE_LOADING_TEXT = "Loading…";
const TIMELINE_EMPTY_TEXT = "No usage in this range.";
const TIMELINE_ERROR_TEXT = "Timeline unavailable.";
const TIMELINE_UNSUPPORTED_TEXT =
  "Timeline unavailable because the OpenCode usage schema is not supported.";
const TIMELINE_RAW_COST_NOTE =
  "Raw cost recorded in OpenCode message data. "
  + "It is not equivalent to official OpenCode Go quota consumption.";

const TIMELINE_METRIC_LABELS = {
  requests: "Requests",
  tokens: "Tokens",
  cost: "Raw Cost",
};

function metricLabel(metric) {
  if (metric != null && Object.prototype.hasOwnProperty.call(TIMELINE_METRIC_LABELS, metric)) {
    return TIMELINE_METRIC_LABELS[metric];
  }
  return "Requests";
}

// Explicit per-metric mapping: tokens always reads the nested total.
function bucketMetric(bucket, metric) {
  if (!bucket) return null;
  if (metric === "tokens") return dashMetric(bucket.tokens);
  if (metric === "requests") return dashMetric(bucket.requests);
  if (metric === "cost") return dashMetric(bucket.cost);
  return null;
}

// Contiguous bucket list -> plain value list (missing -> 0). The original
// buckets are never mutated.
function timelineSeries(series, metric) {
  const list = series || [];
  const out = [];
  for (let i = 0; i < list.length; i++) {
    const v = bucketMetric(list[i], metric);
    out.push(v == null ? 0 : v);
  }
  return out;
}

// Display totals across the provided buckets (API numbers only, additive).
function timelineSummary(series) {
  const list = series || [];
  let requests = 0, tokens = 0, cost = 0;
  for (let i = 0; i < list.length; i++) {
    const b = list[i] || {};
    requests += chartNumSafe(b.requests);
    tokens += chartNumSafe(dashMetric(b.tokens));
    cost += chartNumSafe(b.cost);
  }
  return { requests: requests, tokens: tokens, cost: cost };
}

function chartNumSafe(v) {
  const n = Number(v);
  return isFinite(n) ? n : 0;
}

function timelineHasUsage(series) {
  const list = series || [];
  for (let i = 0; i < list.length; i++) {
    const b = list[i] || {};
    if (chartNumSafe(b.requests) > 0) return true;
    if (chartNumSafe(dashMetric(b.tokens)) > 0) return true;
    if (chartNumSafe(b.cost) > 0) return true;
  }
  return false;
}

// Independent state resolution: loading / error / unsupported / empty / ok.
function timelineViewState(input) {
  const o = input || {};
  if (o.state === "loading") return "loading";
  if (o.error) return "error";
  if (o.reader != null) {
    const st = readerState(o.reader);
    if (st === "unsupported") return "unsupported";
    if (st !== "ok") return "error";
  }
  if (!o.series || !o.series.length) return "empty";
  if (!timelineHasUsage(o.series)) return "empty";
  return "ok";
}

function timelineStateMessage(state) {
  if (state === "loading") return TIMELINE_LOADING_TEXT;
  if (state === "empty") return TIMELINE_EMPTY_TEXT;
  if (state === "unsupported") return TIMELINE_UNSUPPORTED_TEXT;
  if (state === "error") return TIMELINE_ERROR_TEXT;
  return "";
}

// Pure switch decision: a metric switch only re-renders from cached data and
// must never cause a refetch.
function timelineMetricSwitch(current, clicked) {
  const metric = TIMELINE_METRICS.indexOf(clicked) >= 0 ? clicked : current;
  return { metric: metric, refetch: false };
}

function timelineFormatValue(v, metric) {
  if (metric === "tokens") return formatTokens(v);
  if (metric === "cost") return formatCost(v);
  return formatInteger(v);
}

function timelineTickLabel(ms, unit) {
  if (ms == null || ms === "") return "";
  const n = Number(ms);
  if (!isFinite(n)) return "";
  const d = new Date(n);
  if (isNaN(d.getTime())) return "";
  const p = function (x) { return x < 10 ? "0" + x : String(x); };
  if (unit === "day") return p(d.getMonth() + 1) + "-" + p(d.getDate());
  return p(d.getHours()) + ":" + p(d.getMinutes());
}

function timelineRangeText(start, end) {
  return formatDateTime(start) + " – " + formatDateTime(end);
}

function timelineSummaryText(series) {
  const s = timelineSummary(series);
  return "Requests " + formatInteger(s.requests)
    + " · Tokens " + formatTokens(s.tokens)
    + " · " + formatCost(s.cost) + " raw cost";
}

// ---- module state + renderer -----------------------------------------------

const TIMELINE_STATE = { metric: "tokens", last: null, raf: null, wired: false };

function timelineEffectiveMetric(opts) {
  const o = opts || {};
  return TIMELINE_METRICS.indexOf(o.metric) >= 0 ? o.metric : TIMELINE_STATE.metric;
}

function timelineMetricButtons(active) {
  const wrap = document.createElement("div");
  wrap.className = "obs-metric-btns";
  for (let i = 0; i < TIMELINE_METRICS.length; i++) {
    const m = TIMELINE_METRICS[i];
    const b = document.createElement("button");
    b.type = "button";
    b.className = "obs-metric-btn" + (m === active ? " on" : "");
    b.setAttribute("data-timeline-metric", m);
    b.setAttribute("aria-pressed", m === active ? "true" : "false");
    b.textContent = metricLabel(m);
    wrap.appendChild(b);
  }
  return wrap;
}

function timelineClear(container) {
  while (container.firstChild) container.removeChild(container.firstChild);
}

function timelineRenderState(container, state) {
  timelineClear(container);
  const box = document.createElement("div");
  box.className = "obs-state" + (state === "error" ? " err" : "");
  box.textContent = timelineStateMessage(state);
  container.appendChild(box);
}

function timelineRenderChart(container, series, metric, unit) {
  timelineClear(container);
  container.style.position = "relative";
  container.appendChild(timelineMetricButtons(metric));

  const W = 640, H = 180, PAD_L = 48, PAD_R = 12, PAD_T = 12, PAD_B = 24;
  const values = timelineSeries(series, metric);
  const max = niceMax(Math.max.apply(null, values.concat([0])));
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const points = scalePoints(values, max, innerW, innerH);
  for (let i = 0; i < points.length; i++) { points[i].x += PAD_L; points[i].y += PAD_T; }
  const baseY = PAD_T + innerH;

  const svg = makeSvg(W, H);
  svg.style.width = "100%";
  svg.style.height = "160px";
  svg.style.display = "block";
  svg.setAttribute("aria-hidden", "true");

  const grid = svgNode("line", { x1: PAD_L, y1: baseY, x2: W - PAD_R, y2: baseY, stroke: "rgba(255,255,255,0.14)", "stroke-width": 1 });
  if (grid) svg.appendChild(grid);
  const area = svgNode("path", { d: areaPath(points, baseY), fill: "rgba(99,102,241,0.25)", stroke: "none" });
  if (area) svg.appendChild(area);
  const line = svgNode("path", { d: linePath(points), fill: "none", stroke: "#818cf8", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" });
  if (line) svg.appendChild(line);

  const maxLabel = svgText("text", { x: PAD_L - 6, y: PAD_T + 8, fill: "#8a93a6", "font-size": 10, "text-anchor": "end" }, timelineFormatValue(max, metric));
  if (maxLabel) svg.appendChild(maxLabel);

  const list = series || [];
  const labelStep = Math.max(1, Math.ceil(list.length / 8));
  for (let i = 0; i < points.length; i++) {
    if (i % labelStep !== 0 && i !== points.length - 1) continue;
    const b = list[i] || {};
    const t = svgText("text", { x: chartRound(points[i].x), y: H - 6, fill: "#8a93a6", "font-size": 10, "text-anchor": "middle" }, timelineTickLabel(b.start, unit));
    if (t) svg.appendChild(t);
  }

  const svgWrap = document.createElement("div");
  svgWrap.style.position = "relative";
  svgWrap.appendChild(svg);
  container.appendChild(svgWrap);

  const tip = tooltipFor(container);
  const hit = function (clientX) {
    if (!tip || !points.length) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const mx = ((clientX - rect.left) / rect.width) * W;
    let best = 0, bestD = Infinity;
    for (let i = 0; i < points.length; i++) {
      const d = Math.abs(points[i].x - mx);
      if (d < bestD) { bestD = d; best = i; }
    }
    const b = list[best] || {};
    const valueText = timelineFormatValue(values[best], metric);
    let text = timelineRangeText(b.start, b.end) + "\n" + valueText;
    if (metric === "cost") text += "\n" + TIMELINE_RAW_COST_NOTE;
    tip.textContent = text;
    tip.style.display = "block";
    const wrapRect = container.getBoundingClientRect();
    let left = clientX - wrapRect.left + 12;
    const tw = tip.getBoundingClientRect().width;
    if (left + tw > wrapRect.width) left = Math.max(2, wrapRect.width - tw - 2);
    tip.style.left = left + "px";
    tip.style.top = "8px";
  };

  svg.onmousemove = function (e) { hit(e.clientX); };
  svg.onmouseleave = function () { if (tip) tip.style.display = "none"; };
}

function renderTimeline(container, series, opts) {
  if (!container || typeof document === "undefined") return;
  const o = opts || {};
  const metric = timelineEffectiveMetric(o);
  const unit = (o.bucket && o.bucket.unit) ? String(o.bucket.unit) : "hour";
  const state = o.state ? o.state : timelineViewState({ series: series, reader: o.reader, error: o.error });
  TIMELINE_STATE.last = { container: container, series: series, opts: o };
  if (state !== "ok") { timelineRenderState(container, state); return; }
  timelineRenderChart(container, series, metric, unit);
  const summary = timelineSummaryText(series);
  container.setAttribute("aria-label", "Usage timeline: " + summary);
  const sr = document.createElement("div");
  sr.className = "obs-timeline-summary";
  sr.textContent = summary;
  container.appendChild(sr);
}

function timelineRedraw() {
  const last = TIMELINE_STATE.last;
  if (!last) return;
  renderTimeline(last.container, last.series, last.opts);
}

// Resize only redraws from the cached series; it never refetches.
function timelineScheduleRedraw() {
  if (TIMELINE_STATE.raf) return;
  const raf = (typeof window !== "undefined" && window.requestAnimationFrame)
    ? window.requestAnimationFrame.bind(window)
    : function (fn) { return setTimeout(fn, 100); };
  TIMELINE_STATE.raf = raf(function () { TIMELINE_STATE.raf = null; timelineRedraw(); });
}

(function initTimeline() {
  if (typeof window === "undefined" || !window || typeof document === "undefined") return;
  const W = window.OCW = window.OCW || {};
  W.timeline = TIMELINE_STATE;
  if (TIMELINE_STATE.wired) return;
  TIMELINE_STATE.wired = true;
  document.addEventListener("click", function (e) {
    const t = e.target;
    if (!t || !t.closest) return;
    const b = t.closest("[data-timeline-metric]");
    if (!b) return;
    if (!TIMELINE_STATE.last) return;
    const next = timelineMetricSwitch(TIMELINE_STATE.metric, b.getAttribute("data-timeline-metric"));
    TIMELINE_STATE.metric = next.metric;
    timelineRedraw();
  });
  window.addEventListener("resize", timelineScheduleRedraw);
})();
