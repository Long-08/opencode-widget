// Phase 5B: shared, load-safe SVG chart helpers (classic script, no framework).
// Pure geometry/scale helpers plus one shared tooltip singleton. The only DOM
// work lives in makeSvg()/tooltipFor()/svgNode(), which are called from render
// paths only and are guarded by a document check, so this file is safe to eval
// in node:test with no DOM. No API calls and no aggregation happen here: values
// are consumed verbatim from the already-verified backend payloads.

const CHART_NS = "http://www.w3.org/2000/svg";

function chartNum(v) {
  const n = Number(v);
  return isFinite(n) ? n : 0;
}

function chartRound(v) {
  return Math.round(chartNum(v) * 100) / 100;
}

// Nice upper bound for an axis: {1,2,2.5,5,10} * 10^k. 0/negative/non-finite -> 0.
function niceMax(v) {
  const n = chartNum(v);
  if (n <= 0) return 0;
  const mag = Math.pow(10, Math.floor(Math.log10(n)));
  const norm = n / mag;
  let nice;
  if (norm <= 1) nice = 1;
  else if (norm <= 2) nice = 2;
  else if (norm <= 2.5) nice = 2.5;
  else if (norm <= 5) nice = 5;
  else nice = 10;
  return nice * mag;
}

// Map a value array onto an (x spread, y from max) point list. Values are
// clamped for display only; the original values stay untouched. max<=0 pins
// every point to the baseline.
function scalePoints(values, max, w, h) {
  const vals = values || [];
  const n = vals.length;
  const width = chartNum(w);
  const height = chartNum(h);
  const mx = chartNum(max);
  const out = [];
  for (let i = 0; i < n; i++) {
    const v = chartNum(vals[i]);
    const x = n <= 1 ? width / 2 : (i / (n - 1)) * width;
    const y = mx > 0 ? height - (Math.max(0, Math.min(v, mx)) / mx) * height : height;
    out.push({ x: x, y: y, v: v });
  }
  return out;
}

function linePath(points) {
  const pts = points || [];
  if (!pts.length) return "";
  let d = "";
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i] || {};
    d += (i === 0 ? "M" : " L") + chartRound(p.x) + "," + chartRound(p.y);
  }
  return d;
}

function areaPath(points, baseY) {
  const pts = points || [];
  if (!pts.length) return "";
  const first = pts[0] || {};
  const last = pts[pts.length - 1] || {};
  return linePath(pts)
    + " L" + chartRound(last.x) + "," + chartRound(baseY)
    + " L" + chartRound(first.x) + "," + chartRound(baseY) + " Z";
}

// svg root element. Returns null off-DOM (node:test) instead of throwing.
function makeSvg(w, h) {
  if (typeof document === "undefined" || !document.createElementNS) return null;
  const svg = document.createElementNS(CHART_NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + chartNum(w) + " " + chartNum(h));
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "100%");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  return svg;
}

// Create an SVG child element with attributes. Null off-DOM.
function svgNode(tag, attrs) {
  if (typeof document === "undefined" || !document.createElementNS) return null;
  const el = document.createElementNS(CHART_NS, tag);
  if (attrs) {
    const keys = Object.keys(attrs);
    for (let i = 0; i < keys.length; i++) el.setAttribute(keys[i], String(attrs[keys[i]]));
  }
  return el;
}

// Text content is always assigned through textContent (never raw markup).
function svgText(tag, attrs, text) {
  const el = svgNode(tag, attrs);
  if (el) el.textContent = text == null ? "" : String(text);
  return el;
}

// One tooltip node per container: created once, then reused on every render.
// Repeated calls never append a second node (no per-render growth).
function tooltipFor(container) {
  if (!container || typeof document === "undefined") return null;
  let tip = null;
  try { tip = container.querySelector(".obs-chart-tip"); } catch (_) { return null; }
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "obs-chart-tip";
    tip.setAttribute("role", "tooltip");
    tip.style.position = "absolute";
    tip.style.display = "none";
    tip.style.pointerEvents = "none";
    tip.style.whiteSpace = "pre-line";
    tip.style.zIndex = "5";
    tip.style.background = "rgba(11,14,20,0.96)";
    tip.style.border = "1px solid rgba(255,255,255,0.12)";
    tip.style.borderRadius = "6px";
    tip.style.padding = "6px 8px";
    tip.style.fontSize = "12px";
    tip.style.color = "#e6e9f2";
    tip.style.maxWidth = "260px";
    container.appendChild(tip);
  }
  return tip;
}
