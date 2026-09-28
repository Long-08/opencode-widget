// Phase 5.1: DOM-free formatting helpers for the observability dashboard.
// `esc()` plus the display formatters extracted verbatim from the phase5a-core
// block that used to live inline in index.html. Classic script (no ESM); these
// top-level declarations share the global lexical scope with the files loaded
// after it (core.js, views.js, the tab modules and app.js).

// HTML 转义：所有来自 API/DB/formula/配置的数据插入 innerHTML 前必须经过
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function dashNull(v) {
  return v == null ? '—' : v;
}

function formatInteger(n) {
  if (n == null) return '—';
  const num = Number(n);
  if (!isFinite(num)) return '—';
  return Math.round(num).toLocaleString('en-US');
}

function formatTokens(n) {
  if (n == null) return '—';
  const num = Number(n);
  if (!isFinite(num)) return '—';
  const abs = Math.abs(num);
  const trim = function (v) {
    const r = Math.round(v * 10) / 10;
    return Number.isInteger(r) ? String(r) : r.toFixed(1);
  };
  if (abs >= 1e9) return trim(num / 1e9) + 'B';
  if (abs >= 1e6) return trim(num / 1e6) + 'M';
  if (abs >= 1e3) return trim(num / 1e3) + 'K';
  return String(Math.round(num));
}

function formatCost(n) {
  if (n == null) return '—';
  const num = Number(n);
  if (!isFinite(num)) return '—';
  if (num === 0) return '$0.00';
  const abs = Math.abs(num);
  if (abs < 0.001) return '< $0.001';
  if (abs < 0.01) return '$' + num.toFixed(4);
  if (abs < 1) return '$' + num.toFixed(3);
  return '$' + num.toFixed(2);
}

function formatPercent(r) {
  if (r == null) return '—';
  const num = Number(r);
  if (!isFinite(num)) return '—';
  return (num * 100).toFixed(1) + '%';
}

function formatDuration(ms) {
  if (ms == null) return '—';
  const num = Number(ms);
  if (!isFinite(num)) return '—';
  const abs = Math.abs(num);
  if (abs < 1000) return Math.round(num) + 'ms';
  const s = num / 1000;
  if (Math.abs(s) < 60) return (Math.round(s * 10) / 10) + 's';
  const m = s / 60;
  if (Math.abs(m) < 60) return (Math.round(m * 10) / 10) + 'm';
  const h = m / 60;
  if (Math.abs(h) < 24) return (Math.round(h * 10) / 10) + 'h';
  return (Math.round((h / 24) * 10) / 10) + 'd';
}

function formatDateTime(ms) {
  if (ms == null) return '—';
  const num = Number(ms);
  if (!isFinite(num)) return '—';
  const d = new Date(num);
  if (isNaN(d.getTime())) return '—';
  const p = function (x) { return x < 10 ? '0' + x : String(x); };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
    + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function shortSessionId(id) {
  if (id == null) return '—';
  const s = String(id);
  if (s.length <= 12) return s;
  return s.slice(0, 6) + '…' + s.slice(-3);
}
