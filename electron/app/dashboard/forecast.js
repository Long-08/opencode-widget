// Phase 4: Forecast tab for the observability dashboard.
// DOM-free at load so node:test can eval the file: every helper below is pure
// and the DOM is only touched from inside the registration callbacks, which the
// guard below skips entirely when OCW is not defined. Nothing here calls the
// API — core.js obsEnsure() stays the only entry point, and it fetches lazily
// for the active tab only. Forecast is range-independent (single cache entry).

// User-facing labels stay tentative: forecasts are estimates.
const FORECAST_DISCLAIMER =
  "Forecasts are estimates based on recent observed usage and official quota state. "
  + "They are not guaranteed future outcomes.";

const FORECAST_STATUS_LABELS = {
  unavailable: "Unavailable",
  insufficient_data: "Insufficient data",
  no_usage: "No usage",
  already_at_limit: "Already at limit",
  reset_unknown: "Reset time unknown",
  reset_before_limit: "Estimated to stay under limit (resets first)",
  limit_before_reset: "Estimated to reach limit before reset",
  ok: "OK",
};

function forecastStatusLabel(status) {
  if (status != null && Object.prototype.hasOwnProperty.call(FORECAST_STATUS_LABELS, status)) {
    return FORECAST_STATUS_LABELS[status];
  }
  return "Unknown";
}

const FORECAST_RESET_SOURCE_LABELS = {
  official_reset_text: "Official reset text",
  local_window_estimate: "Local window estimate",
  unknown: "Unknown",
};

function forecastResetSourceLabel(source) {
  if (source == null || source === "") return "—";
  return Object.prototype.hasOwnProperty.call(FORECAST_RESET_SOURCE_LABELS, source)
    ? FORECAST_RESET_SOURCE_LABELS[source]
    : "Unknown";
}

// `type`/`basis` come from the backend enums; unknown values fall back to the
// raw string (escaped at render time), never a fabricated label.
const FORECAST_TYPE_LABELS = { estimate: "Estimate" };
const FORECAST_BASIS_LABELS = { official_quota_usage_rate: "Official quota usage rate" };

function forecastTypeLabel(t) {
  if (t == null || t === "") return "—";
  return Object.prototype.hasOwnProperty.call(FORECAST_TYPE_LABELS, t) ? FORECAST_TYPE_LABELS[t] : String(t);
}

function forecastBasisLabel(b) {
  if (b == null || b === "") return "—";
  return Object.prototype.hasOwnProperty.call(FORECAST_BASIS_LABELS, b) ? FORECAST_BASIS_LABELS[b] : String(b);
}

function forecastNum(v) {
  if (v == null) return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
}

// Unit-less number formatting in the spirit of formatCost()/formatTokens()
// thresholds. Usage is not USD, so no currency symbol is added here.
function forecastFormatNumber(v) {
  if (v == null || v === "") return "—";
  const num = Number(v);
  if (!isFinite(num)) return "—";
  if (num === 0) return "0";
  const abs = Math.abs(num);
  if (abs < 0.001) return "< 0.001";
  if (abs < 0.01) return num.toFixed(4);
  if (abs < 1) return num.toFixed(3);
  if (abs < 1000) return String(Math.round(num * 100) / 100);
  return num.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function forecastTrimNumber(v) {
  const num = forecastNum(v);
  if (num == null) return "—";
  return String(Math.round(num * 100) / 100);
}

function forecastSampleText(points, hours) {
  const n = forecastNum(points);
  const h = forecastNum(hours);
  return "Based on " + (n == null ? "—" : String(Math.round(n)))
    + " usage records over " + (h == null ? "—" : forecastTrimNumber(h)) + " hours.";
}

// Explicit units are mandatory: /h and /day, never a bare rate.
function forecastRateText(rate) {
  rate = rate || {};
  const perHour = forecastNum(rate.usage_per_hour);
  const perDay = forecastNum(rate.usage_per_day);
  const parts = [];
  if (perHour != null) parts.push(forecastFormatNumber(perHour) + " /h");
  if (perDay != null) parts.push(forecastFormatNumber(perDay) + " /day");
  return parts.length ? parts.join(" · ") : "—";
}

function forecastRateModel(rate) {
  rate = rate || {};
  return {
    status: rate.status == null ? null : String(rate.status),
    statusLabel: forecastStatusLabel(rate.status),
    usage: forecastNum(rate.usage),
    usageText: forecastFormatNumber(rate.usage),
    durationHours: forecastNum(rate.duration_hours),
    rateText: forecastRateText(rate),
    samplePoints: forecastNum(rate.sample_points),
    sampleText: forecastSampleText(rate.sample_points, rate.duration_hours),
    windowMs: forecastNum(rate.window_ms),
  };
}

// Estimates may be keyed for a reset (session/weekly) or a period end.
function forecastEstimateValue(est, resetKey, endKey) {
  if (est == null) return null;
  if (est[resetKey] != null) return est[resetKey];
  return est[endKey] == null ? null : est[endKey];
}

function forecastEstimateModel(est, generatedAt) {
  est = est || {};
  const ttlHours = forecastNum(est.time_to_limit_hours);
  const resetAt = forecastNum(forecastEstimateValue(est, "reset_at", "period_end_at"));
  const gen = forecastNum(generatedAt);
  const resetInMs = (resetAt != null && gen != null) ? (resetAt - gen) : null;
  const wouldExceed = est.would_exceed_limit == null ? null : !!est.would_exceed_limit;
  return {
    status: est.status == null ? null : String(est.status),
    statusLabel: forecastStatusLabel(est.status),
    timeToLimitHours: ttlHours,
    timeToLimitText: ttlHours == null ? "—" : formatDuration(ttlHours * 3600000),
    rawTimeToLimitText: forecastFormatNumber(est.raw_time_to_limit_hours),
    estimatedLimitAt: forecastNum(est.estimated_limit_at),
    estimatedLimitAtText: formatDateTime(est.estimated_limit_at),
    resetAt: resetAt,
    resetAtText: formatDateTime(resetAt),
    resetInText: resetInMs == null ? "—" : formatDuration(resetInMs),
    projectedUsageText: forecastFormatNumber(
      forecastEstimateValue(est, "projected_usage_at_reset", "projected_usage_at_end")),
    projectedRemainingText: forecastFormatNumber(
      forecastEstimateValue(est, "projected_remaining_at_reset", "projected_remaining_at_end")),
    wouldExceedLimit: wouldExceed,
    wouldExceedText: wouldExceed == null ? "—" : (wouldExceed ? "Yes" : "No"),
  };
}

const FORECAST_SECTION_DEFS = [
  {
    key: "session", title: "5h Session", flowKey: "session",
    rateKeys: [["last_30m", "Last 30m"], ["last_60m", "Last 60m"], ["window_avg", "Window average"]],
  },
  {
    key: "weekly", title: "Weekly", flowKey: "weekly",
    rateKeys: [["last_24h", "Last 24h"], ["last_3d", "Last 3d"], ["last_7d", "Last 7d"]],
  },
  {
    key: "period", title: "Current Period", flowKey: "period",
    rateKeys: [["daily_average", "Daily average"], ["recent_3d_average", "Recent 3d average"], ["recent_7d_average", "Recent 7d average"]],
  },
];

function forecastOfficialModel(official) {
  official = official || {};
  return {
    status: official.status == null ? null : String(official.status),
    statusLabel: forecastStatusLabel(official.status),
    used: forecastNum(official.used),
    usedText: forecastFormatNumber(official.used),
    remaining: forecastNum(official.remaining),
    remainingText: forecastFormatNumber(official.remaining),
    limit: forecastNum(official.limit),
    limitText: forecastFormatNumber(official.limit),
    resetAt: forecastNum(official.reset_at),
    resetText: formatDateTime(official.reset_at),
    resetSource: official.reset_source == null ? null : String(official.reset_source),
    resetSourceText: forecastResetSourceLabel(official.reset_source),
  };
}

// View models for the three fixed sections. ALL three rate/estimate rows are
// always emitted, in contract order — callers never pick a single "best" one.
function forecastSections(data) {
  data = data || {};
  const generatedAt = forecastNum(data.generated_at);
  return FORECAST_SECTION_DEFS.map(function (def) {
    const flow = data[def.flowKey] || {};
    const rates = flow.rates || {};
    const estimates = flow.estimates || {};
    return {
      key: def.key,
      title: def.title,
      official: forecastOfficialModel(flow.official),
      rates: def.rateKeys.map(function (pair) {
        const m = forecastRateModel(rates[pair[0]]);
        m.key = pair[0];
        m.label = pair[1];
        return m;
      }),
      estimates: def.rateKeys.map(function (pair) {
        const m = forecastEstimateModel(estimates[pair[0]], generatedAt);
        m.key = pair[0];
        m.label = pair[1];
        return m;
      }),
    };
  });
}

// ---- DOM render (only reached through OCW.registerTab below) ---------------

function forecastMetricHtml(label, value) {
  return '<span class="obs-detail-item">' + esc(label) + "<b>" + esc(dashNull(value)) + "</b></span>";
}

function forecastOfficialHtml(official) {
  let html = '<div class="obs-detail-metrics forecast-official">';
  html += forecastMetricHtml("Official status", official.statusLabel);
  html += forecastMetricHtml("Used", official.usedText);
  html += forecastMetricHtml("Remaining", official.remainingText);
  html += forecastMetricHtml("Limit", official.limitText);
  html += forecastMetricHtml("Reset", official.resetText);
  html += forecastMetricHtml("Reset source", official.resetSourceText);
  return html + "</div>";
}

function forecastSectionHtml(sec) {
  let html = '<div class="obs-table-card forecast-section" data-forecast-section="' + esc(sec.key) + '">';
  html += "<h4>" + esc(sec.title) + "</h4>";
  html += forecastOfficialHtml(sec.official);
  html += '<table class="obs-table forecast-rates"><thead><tr>'
    + "<th>Window</th><th>Status</th><th>Usage</th><th>Rate</th><th>Samples</th>"
    + "</tr></thead><tbody>";
  sec.rates.forEach(function (r) {
    html += "<tr><td>" + esc(r.label) + "</td><td>" + esc(r.statusLabel) + "</td><td>" + esc(r.usageText)
      + "</td><td>" + esc(r.rateText) + "</td><td>" + esc(r.sampleText) + "</td></tr>";
  });
  html += "</tbody></table>";
  html += '<table class="obs-table forecast-estimates"><thead><tr>'
    + "<th>Window</th><th>Status</th><th>Time to limit</th><th>Projected usage</th><th>Projected remaining</th><th>Would exceed</th>"
    + "</tr></thead><tbody>";
  sec.estimates.forEach(function (e) {
    html += "<tr><td>" + esc(e.label) + "</td><td>" + esc(e.statusLabel) + "</td><td>" + esc(e.timeToLimitText)
      + "</td><td>" + esc(e.projectedUsageText) + "</td><td>" + esc(e.projectedRemainingText)
      + "</td><td>" + esc(e.wouldExceedText) + "</td></tr>";
  });
  html += "</tbody></table></div>";
  return html;
}

function forecastHeaderHtml(data) {
  data = data || {};
  let html = '<div class="obs-detail-metrics forecast-meta">';
  html += forecastMetricHtml("Type", forecastTypeLabel(data.type));
  html += forecastMetricHtml("Basis", forecastBasisLabel(data.basis));
  html += forecastMetricHtml("Generated", formatDateTime(data.generated_at));
  return html + "</div>";
}

// Escaped forecast HTML (disclaimer + meta + three sections). Pure: takes the
// payload, returns a string; no DOM access.
function forecastHtml(data) {
  const resp = (data && data.forecast) || data || {};
  let html = '<div class="forecast-disclaimer" role="note">' + esc(FORECAST_DISCLAIMER) + "</div>";
  html += forecastHeaderHtml(resp);
  forecastSections(resp).forEach(function (sec) { html += forecastSectionHtml(sec); });
  return html;
}

function obsRenderForecast(body, data) {
  if (!body) return;
  const resp = (data && data.forecast) || data || {};
  if (resp.reader != null) {
    const msg = dashReaderError(resp);
    if (msg) { body.innerHTML = dashStateHtml(msg); return; }
  }
  if (!resp.session && !resp.weekly && !resp.period) {
    body.innerHTML = '<div class="forecast-disclaimer" role="note">' + esc(FORECAST_DISCLAIMER) + "</div>"
      + dashStateHtml("No forecast data.");
    return;
  }
  body.innerHTML = forecastHtml(resp);
}

// No API call at load: registration only wires the renderer into core.js.
if (typeof OCW !== "undefined" && OCW && typeof OCW.registerTab === "function") {
  OCW.registerTab("forecast", {
    target: function () { return document.getElementById("obsForecastView"); },
    render: function (body, data) { obsRenderForecast(body, data); },
    reset: function () {},
  });
}
