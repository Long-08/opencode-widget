// Phase 5.1: remaining renderer logic (moved verbatim from index.html).
// Small/mid/large state, quota UI, provider tabs, curves, heatmap, modals, IPC
// wiring, snap/click-through and the bootstrap timers. Only the two dashboard
// call sites were namespace-qualified (OCW.obsEnsure(OCW.getActiveTab())).

const $ = (s) => document.querySelector(s);



// 本地 API 由主进程代理（window.widgetAPI.apiGet*/apiPost*），token 不下发渲染层；
// 各调用点沿用原有 try/catch 与默认值兜底。
const money = (v) => v == null ? "—" : (v >= 1 ? "$" + v.toFixed(2) : (v >= 0.01 ? "$" + v.toFixed(3) : "$" + v.toFixed(4)));
const countdown = (ms) => {
  if (ms <= 0) return "已重置";
  const s = Math.floor(ms / 1000);
  if (s >= 86400) { const d = Math.floor(s/86400), h = Math.floor(s%86400/3600); return d + "天" + h + "时"; }
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60);
  return h + "时" + m + "分";
};
const PERIODS = [["5小时","session"],["本周","weekly"],["本月","monthly"]];const RING_COLORS = ["#6366f1", "#a855f7", "#22d3ee"];
const RING_KEYS = ["session", "weekly", "monthly"];
const RING_R = [28, 41, 52];
const COMPACT_H = 480, EXPAND_W = 960, EXPAND_H = 720, SMALL_W = 540, SMALL_H = 260;
// 账单→官方计量器口径系数（≈官方计费 ÷ 本地账单），总量/总用量显示统一乘以该系数；
// 明细（模型/供应商行、每日曲线与 tooltip）保持原始账单。
let RATIO = 1.4212;
// 计量器口径: 仅「总量/全部」乘系数。全部供应商 或 go(go 账单=全部账单, 其余供应商免费)→ ×RATIO;
// 子集/明细(gateway 是 go 的子集 等)→ 保持原始账单, 避免虚高。
const meterRatio = (sel) => (sel === "go" || !sel) ? RATIO : 1;

// 供应商动态自动发现：固定顺序优先，其余按数据里出现的 src 追加（新接入的 provider 自动出现）
const SUPPLIER_ORDER = ["go", "zen", "kilo", "router"];
const SUPPLIER_NAMES = { go: "OpenCode Go", zen: "OpenCode Zen", kilo: "Kilo", router: "OpenRouter",
                         gateway: "AI Gateway", deepseek: "DeepSeek 官方", openai: "OpenAI" };
// 模型列表条目的供应商前缀 (简写 tag)
const SUPPLIER_TAG = { go: "go", zen: "zen", kilo: "kilo", router: "rt", gateway: "gw" };
// 子集来源: 其用量已含于另一供应商 (gateway 是 go 配额的子集), 「全部」聚合时排除
let SUBSET_SRCS = ["gateway"];
// 大窗供应商 tab 轮播: 「全部」固定, 具体供应商窗口槽位数
const SUP_TABS_MAX = 5;
let supOffset = 0;
function supName(s) {
  return SUPPLIER_NAMES[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
}
function supTag(s) {
  return SUPPLIER_TAG[s] || s;
}
function supplierList() {
  const have = state.suppliers || {};
  const known = SUPPLIER_ORDER.filter(s => have[s]);
  const extra = Object.keys(have).filter(s => s !== "all" && !SUPPLIER_ORDER.includes(s) && have[s]);
  return [...known, ...extra];
}

let state = { windows: [], stats: [], history: [], suppliers: {}, heatmap: [], mode: "overview", rows: 0, keyOk: false, models: 0, lastRefresh: 0, server: null, server_error: null };
let lastCal = null;
let uiState = "small";   // small | mid | large（Mini 是默认常驻态）
let selModel = null;
let selSupplier = "go"; // null = 全部
let heatRange = "30";    // 热力图范围: 7 | 30 | all
let timeRange = "7"; // 小屏总量时间范围: today | 7 | all（默认近7天，避免刚跨天时显示 0；按 X 循环切换）
let lastHover = null;    // 图表最后悬停坐标 {cx, cy}, 切模型后用于刷新 tooltip
let mFilter = "all";
let mUnit = "count";
let FORMULA = null;

async function loadFormula(force = false) {
  try {
    const f = await window.widgetAPI.apiGetFormula(force);
    FORMULA = f;
    if (f.params?.meter?.ratio) RATIO = Number(f.params.meter.ratio);
    SUBSET_SRCS = Object.keys(f.params?.sources?.subset_of || {});
  } catch (e) {
    console.warn("loadFormula failed", e);
  }
}

async function fetchView(id) {
  if (!state._viewCache) state._viewCache = {};
  if (state._viewCache[id]) return state._viewCache[id];
  try {
    const data = await window.widgetAPI.apiGetView(id);
    state._viewCache[id] = data;
    return data;
  } catch (e) { return null; }
}

function fmtTokens(n) {
  if (n == null || isNaN(n)) return "0";
  if (n >= 1e9) return (n / 1e9).toFixed(2).replace(/\.?0+$/, "") + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.?0$/, "") + "K";
  return String(Math.round(n));
}
function fmtCost(v) {
  if (v == null || !isFinite(v)) return "—";
  return "$" + (v >= 1 ? v.toFixed(2) : (v >= 0.01 ? v.toFixed(3) : v.toFixed(4)));
}
function modelValue(x, key) {
  if (mUnit === "token") return (x.tokens_in || 0) + (x.tokens_out || 0) + (x.tokens_cache || 0);
  return x[key] || 0;
}
function windowTokens(x, mode) {
  if (mode === "weekly") return (x.tokens_in_w||0) + (x.tokens_out_w||0) + (x.tokens_cache_w||0);
  if (mode === "monthly") return (x.tokens_in_m||0) + (x.tokens_out_m||0) + (x.tokens_cache_m||0);
  return (x.tokens_in_s||0) + (x.tokens_out_s||0) + (x.tokens_cache_s||0);
}
function fmtPct(v) { return (v || 0).toFixed(1) + "%"; }

function displayWindows() {
  if (state.server && state.server.length >= 3) {
    const keys = ["session", "weekly", "monthly"];
    return state.server.map((w, i) => {
      const local = state.windows.find(x => x.kind === keys[i]);
      return { kind: keys[i], used: local ? local.used : null, limit: local ? local.limit : [12,30,60][i], pct: local ? local.pct : w.pct, reset: (local || {}).reset || 0, reset_text: w.reset_text || "", deduct: (local || {}).deduct || 0, gross_used: (local || {}).gross_used || 0, server: true };
    });
  }
  return state.windows;
}
function win(kind) { return displayWindows().find(x => x.kind === kind); }
function circ(r) { return 2 * Math.PI * r; }
function setRing(i, pct) {
  const c = circ(RING_R[i]);
  ["ring" + i, "sring" + i].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.strokeDasharray = c;
    el.style.strokeDashoffset = c * (1 - Math.min(100, pct) / 100);
  });
}
function ringColor(pct) { return pct >= 95 ? "#ef4444" : pct >= 70 ? "#f59e0b" : RING_COLORS[0]; }
function periodName(kind) { return PERIODS[RING_KEYS.indexOf(kind)][0]; }
function modelKey() { return { overview: "count_m", session: "count_s", weekly: "count_w", monthly: "count_m" }[state.mode] || "count_m"; }

function render() {
  try {
  const wins = displayWindows();
  const s = wins.find(x => x.kind === "session"), w = wins.find(x => x.kind === "weekly"), m = wins.find(x => x.kind === "monthly");
  if (!s || !w || !m) return;
  const cl = $("#cLabel"), ca = $("#cAmount"), cs = $("#cSub");
  const fitAmount = () => {
    const scale = Math.max(0.7, Math.min(1.4, (window.innerWidth || 560) / 560));
    const t = ca.textContent || "";
    const len = t.length;
    const base = len >= 7 ? 10 : len === 6 ? 11 : len === 5 ? 12 : len === 4 ? 13 : 15;
    ca.style.fontSize = Math.round(base * scale) + "px";
  };
  if (state.mode === "overview") {
    cl.textContent = "总用量";
    // 总用量 = 官方月计量器（×1.4212 口径，封顶 70）；不再把三个窗口进度相加
    const total = (m && m.used) || 0;
    ca.textContent = total ? money(total) : "—";
    const hot = [s, w, m].reduce((a, b) => a.pct > b.pct ? a : b);
    cs.textContent = "最高 " + periodName(hot.kind) + " " + Math.round(hot.pct) + "%";
  } else {
    const cur = win(state.mode);
    cl.textContent = periodName(state.mode);
    ca.textContent = Math.round(cur.pct) + "%";
    const resetPart = cur.reset_text || (typeof cur.reset === "number" && cur.reset ? "重置 " + countdown(cur.reset - Date.now()) : "");
    cs.textContent = (cur.used != null ? money(cur.used) + "/" + money(cur.limit) : "") + (resetPart ? " · " + resetPart : "");
  }
  fitAmount();
  setRing(0, s.pct); setRing(1, w.pct); setRing(2, m.pct);
if (!selModel && state.stats.length) {
let cands = rankSortedStats();
// free 模式不归供应商: 初选不按供应商过滤
if (selSupplier && mFilter !== "free") cands = cands.filter(x => (x.source || "") === selSupplier);
const withHist = cands.find(x => state.history.some(h => h.key === x.key));
selModel = (withHist || cands[0] || state.stats[0]).key;
}
  renderMini();
  // 分模式渲染: Mini 只画状态条; Compact 画摘要; 图表/热力图等重内容仅 Expanded
  if (uiState !== "small") { renderSide(); renderTabs(); renderModels(); }
  if (uiState === "large") { renderChart(); renderLarge(); }
  const ago = state.lastRefresh ? Math.round((Date.now() - state.lastRefresh) / 1000) : 0;
  const mtotal = (m && m.used) || 0;
  const cUpd = $("#cUpdated");
  if (cUpd) cUpd.textContent = ago > 0 ? "更新于 " + ago + "s 前" : "";
  $("#footLeft").textContent = `月消费 ${mtotal ? money(mtotal) : "—"} · ${state.rows} 条 · ${ago}s前`;
  if (state.server) {
    const cred = state.credits || 0;
    $("#calBadge").textContent = "服务端 ✓" + (cred > 0 ? ` · +$${(state.credits_dollars || 0).toFixed(0)}信用` : "");
    $("#footRight").textContent = "";
  } else {
    $("#calBadge").textContent = lastCal ? "已校准 ✓ " : "";
    $("#footRight").textContent = lastCal ? "" : "右键菜单";
  }
  $("#keyText").textContent = state.keyOk ? `Key ✓ ${state.models}` : "Key ✗";
  $("#keyDot").className = "dot " + (state.keyOk ? "ok" : "err");
  } catch (e) { try { document.getElementById("footRight").textContent = "ERR:" + e.message; } catch (_) {} }
}

function renderSide() {
  const side = $("#cSide");
  side.innerHTML = "";
  const wins = displayWindows();
  RING_KEYS.forEach((k, i) => {
    const w = wins.find(x => x.kind === k);
    if (!w) return;
    const div = document.createElement("div");
    div.className = "c-item" + (state.mode === k ? " active" : "");
    const resetPart = w.reset_text || (typeof w.reset === "number" && w.reset ? countdown(w.reset - Date.now()) : "");
    div.innerHTML = `<span class="sw" style="background:${RING_COLORS[i]}"></span><span class="nm">${periodName(k)}</span><span class="bar"><i style="width:${Math.min(100, w.pct)}%;background:${ringColor(w.pct)}"></i></span><span class="pv">${Math.round(w.pct)}%</span><span class="rs">${esc(resetPart)}</span>`;
    div.onclick = () => { state.mode = k; render(); };
    side.appendChild(div);
  });
  const rw = win(state.mode);
  if (rw && (rw.reset_text || rw.reset)) {
    const resetDiv = document.createElement("div");
    resetDiv.className = "c-reset";
    resetDiv.innerHTML = '<span class="c-reset-dot"></span>' + esc(rw.reset_text || (typeof rw.reset === "number" && rw.reset ? countdown(rw.reset - Date.now()) : ""));
    side.appendChild(resetDiv);
  }
}

function renderTabs() {
  const tb = $("#cTabs");
  tb.innerHTML = "";
  [["总览","overview"], ...PERIODS].forEach(([label, mode]) => {
    const d = document.createElement("div");
    d.className = "tab" + (state.mode === mode ? " active" : "");
    d.textContent = label;
    d.onclick = () => { state.mode = mode; render(); };
    tb.appendChild(d);
  });
}

function filteredStats() {
    let st = state.stats;
    // 供应商过滤仅作用于订阅模型(go): free 模型散落各来源(router/zen/kilo/known), 不归任一供应商
    if (selSupplier && mFilter !== "free") st = st.filter(x => (x.source || "") === selSupplier);
    if (mFilter !== "all") st = st.filter(x => x.group === mFilter);
    return st;
}

// 模型列表/上下键共用的排序: 外层供应商固定顺序, 内层使用量
// (Token 模式统一按 token; 默认: 订阅组按费用=配额消耗, free 组按次数)
function rankSortedStats() {
  const stats = filteredStats();
  const key = modelKey();
  const mode = state.mode === "overview" ? "monthly" : state.mode;
  const costOf = x => mode === "weekly" ? (x.cost_w || 0) : mode === "monthly" ? (x.cost_m || 0) : (x.cost_s || 0);
  const tokOf = x => (x.tokens_in || 0) + (x.tokens_out || 0) + (x.tokens_cache || 0);
  const rankVal = x => mUnit === "token" ? tokOf(x) : (x.group === "go" ? costOf(x) : (x[key] || 0));
  const supplierRank = s => { const i = SUPPLIER_ORDER.indexOf(s); return i >= 0 ? i : 100; };
  return [...stats].sort((a, b) => {
    const ra = supplierRank(a.source), rb = supplierRank(b.source);
    if (ra !== rb) return ra - rb;
    return rankVal(b) - rankVal(a);
  });
}

// Compact 快看: 模型列表最多 Top 3 (完整列表在 Expanded); 排序不变, 只截断显示
const COMPACT_MODEL_LIMIT = 3;

function fillModelList(el, key, limit = 0) {
  el.innerHTML = "";
  const stats = filteredStats();
  const mode = state.mode === "overview" ? "monthly" : state.mode;
  const quotaWin = win(mode);
  const quotaLimit = (quotaWin && quotaWin.limit) || 60;
  const costOf = x => mode === "weekly" ? (x.cost_w || 0) : mode === "monthly" ? (x.cost_m || 0) : (x.cost_s || 0);
  const tokOf = x => (x.tokens_in || 0) + (x.tokens_out || 0) + (x.tokens_cache || 0);
  // 排序两层: 外层供应商固定顺序, 内层使用量 (Token 模式统一按 token; 默认: 订阅组按费用=配额消耗, free 组按次数)
  const rankVal = x => mUnit === "token" ? tokOf(x) : (x.group === "go" ? costOf(x) : (x[key] || 0));
  const sorted = rankSortedStats();
  const total = sorted.reduce((a, x) => a + rankVal(x), 0) || 1;
  const shown = limit > 0 ? sorted.slice(0, limit) : sorted;
  // go 配额是共享费用池: 消耗统一取官方窗口 used(server 权威, 已含网关调用), 避免条目 cost 重复计
  const goStats = stats.filter(x => x.group === "go");
  const usedAll = (quotaWin && quotaWin.used != null && quotaWin.used > 0)
    ? quotaWin.used
    : goStats.reduce((a, x) => a + costOf(x) * (x.meter_rate || 1), 0);
  const remainAll = Math.max(0, quotaLimit - usedAll);
  shown.forEach(x => {
    const div = document.createElement("div");
    const mKey = x.key || x.model;
    const isTok = mUnit === "token";
    const isFree = x.group === "free";
    const unused = isFree && x.used === false;
    div.className = "m-item" + (selModel === mKey ? " sel" : "") + (isFree ? " free" : "") + (unused ? " unused" : "");
    const val = modelValue(x, key);
    const isQuota = x.group === "go" && !isTok;
    const srcTag = (x.source && x.source !== "known" && x.source !== "?") ? `[${esc(supTag(x.source))}] ` : "";
    let main = isTok ? fmtTokens(val) : val + "次";
    let barPct = 0, extra = "", remText = "";
    if (isQuota) {
      // go 次数模式: 次数 · 消费/模型配额 · 剩余次数
      // 有效剩余 / 次均费用 / 剩余次数 均由后端计算，前端直接读取
      const c = costOf(x);
      const usedCnt = x[key] || 0;
      const avgPerReq = x.avg_per_req != null ? x.avg_per_req : ((c > 0 && usedCnt > 0) ? c / usedCnt : (x.est_req_cost || 0));
      const modelQuota = (x.model_quota != null) ? x.model_quota : quotaLimit;
      const mRate = x.meter_rate != null ? x.meter_rate : meterRatio(x.source);
      const modelUsed = (x.model_used != null ? x.model_used : c) * mRate;
      const modelRemain = Math.max(0, modelQuota - modelUsed);
      const globalRemain = Math.max(0, quotaLimit - usedAll);
      const effectiveRemain = x.effective_remain != null ? x.effective_remain : Math.min(modelRemain, globalRemain);
      const remainCnt = x.remain_cnt != null ? x.remain_cnt : ((avgPerReq > 0) ? effectiveRemain / avgPerReq : 0);
      const totalCnt = usedCnt + remainCnt;
      const pct = totalCnt > 0 ? Math.min(100, usedCnt / totalCnt * 100) : 0;
      main = usedCnt + "次";
      barPct = pct;
      extra = `${fmtCost(c * mRate)}/${fmtCost(modelQuota)}`;
      if (effectiveRemain > 0 && remainCnt > 0) remText = "剩" + Math.round(remainCnt) + "次";
    } else if (x.group === "go") {
      // go token 模式: 已使用 · 百分比 · 该模型总量(已用 + 剩余共享费用按本模型均价折算)
      const usedTok = windowTokens(x, mode);
      const totalTok = x.token_quota_reverse != null ? x.token_quota_reverse : usedTok;
      const pct = totalTok > 0 ? Math.min(100, usedTok / totalTok * 100) : 0;
      main = fmtTokens(usedTok);
      barPct = pct;
      extra = pct > 0 ? `${fmtPct(pct)} · ${fmtTokens(totalTok)} tok` : "";
    } else if (isFree) {
      // free 模型无配额: 跟随时间周期, 进度条为模型间相对占比, 不显示百分比/配额数字
      // 总览模式: 用本地历史全量 (count_total / tokens 全量), 不按本月
      const isOverview = state.mode === "overview";
      const fv = isTok
        ? (isOverview ? ((x.tokens_in || 0) + (x.tokens_out || 0) + (x.tokens_cache || 0))
                      : windowTokens(x, mode))
        : (isOverview ? (x.count_total || 0) : (x[key] || 0));
      main = isTok ? fmtTokens(fv) : fv + "次";
      barPct = total > 0 ? Math.min(100, fv / total * 100) : 0;
      extra = unused ? "未使用" : "";
    } else {
      barPct = Math.min(100, val / total * 100);
      extra = fmtTokens((x.tokens_in || 0) + (x.tokens_out || 0) + (x.tokens_cache || 0)) + " tok";
    }
    const barCol = isQuota ? (barPct >= 100 ? "background:#ef4444" : "") : "";
    div.innerHTML = `<span class="nm" title="${esc(x.model)}">${srcTag}${esc(x.name)}</span><span class="bar"><i style="width:${barPct}%${barCol ? ";" + barCol : ""}"></i></span><span class="cnt">${esc(main)}<span class="sub">${esc(extra)}</span></span>${remText ? '<span class="rem">' + esc(remText) + '</span>' : ""}`;
div.onclick = () => {
if (selModel === mKey) selModel = null;
else {
selModel = mKey;
// 点击条目联动: 曲线/热力图切到该条目所属供应商 (free 模式不切, 来源未知或不存在则不切)
if (mFilter !== "free" && x.source && x.source !== "known" && x.source !== "?" && supplierList().includes(x.source)) {
selSupplier = x.source;
}
}
render();
};
    el.appendChild(div);
  });
}

function renderModels() {
  const key = modelKey();
  if (uiState !== "small") fillModelList($("#cList"), key, COMPACT_MODEL_LIMIT);
  if (uiState === "large") fillModelList($("#dList"), key);
  const n = filteredStats().length;
  const freeAll = state.stats.filter(x => x.group === "free").length;
  const freeUsed = state.stats.filter(x => x.group === "free" && x.used !== false).length;
  let label = n + " 个模型";
  if (freeAll > 0) label += ` · free ${freeUsed}/${freeAll}`;
  $("#cCount").textContent = label + " · Top " + COMPACT_MODEL_LIMIT;
  $("#dCount").textContent = label;
  document.querySelectorAll(".unit-btn").forEach(b => {
    b.classList.toggle("on", mUnit === "token");
    b.textContent = mUnit === "token" ? "Token" : "次数";
  });
  document.querySelectorAll(".m-filters").forEach(f => {
    [...f.children].forEach(btn => btn.classList.toggle("on", btn.dataset.f === mFilter));
  });
}

function renderLimits() {
  const dl = $("#dLimits");
  dl.innerHTML = "";
  const wins = displayWindows();
  RING_KEYS.forEach((k, i) => {
    const w = wins.find(x => x.kind === k);
    if (!w) return;
    const div = document.createElement("div");
    div.className = "l-card" + (state.mode === k ? " active" : "");
    const resetPart = w.reset_text || (typeof w.reset === "number" && w.reset ? countdown(w.reset - Date.now()) : "");
    div.innerHTML = `<div class="l-top"><span class="l-name">${periodName(k)}</span><span class="l-pct" style="color:${ringColor(w.pct)}">${Math.round(w.pct)}%</span></div><div class="l-bar"><i style="width:${Math.min(100, w.pct)}%;background:${ringColor(w.pct)}"></i></div><div class="l-sub"><span>${w.used != null ? money(w.used) + "/" + money(w.limit) : ""}</span><span>${esc(resetPart)}</span></div>`;
    div.onclick = () => { state.mode = k; render(); };
    dl.appendChild(div);
  });
}

// 当前供应商(或全部)聚合历史 series: 所有模型按日期求和
// free 模式: 聚合所有来源的 free 模型历史(不按供应商); 否则按当前供应商(或全部, 排除 gateway 子集)
function totalSeries() {
const src = selSupplier;
const isFree = mFilter === "free";
const items = state.history.filter(h => {
if (isFree) return h.is_free;
return src ? h.source === src : (!SUBSET_SRCS.includes(h.source) || h.account === "other");
});
const byDate = {};
    items.forEach(h => (h.series || []).forEach(p => {
        const b = byDate[p.date] || (byDate[p.date] = { cost: 0, tokens: 0, count: 0 });
        b.cost += p.cost || 0;
        b.tokens += (p.tokens_in || 0) + (p.tokens_out || 0) + (p.tokens_cache || 0);
        b.count += p.count || 0;
    }));
    return Object.entries(byDate).map(([date, v]) => ({ date, ...v }));
}

function renderChart() {
const svg = $("#dChartSvg");
const note = $("#chartNote");
const title = $("#dChartTitle");
const detail = $("#dModelDetail");
if (!selModel || selModel === "__total__") {
// 未选模型(或总量模式): 曲线显示当前供应商(或全部)的聚合总量, Y 轴统一 Token
const aggSeries = totalSeries();
const totalName = selSupplier ? supName(selSupplier) : "全部供应商";
if (detail) detail.innerHTML = "";
if (!aggSeries.length) {
svg.innerHTML = ""; title.textContent = "暂无历史数据"; if (note) note.textContent = "";
return;
}
const item = { series: aggSeries, is_free: true, name: totalName + " · 全部模型" };
const selKey = "";
const statItem = null;
const days = heatRange === "1" ? 1 : heatRange === "7" ? 7 : heatRange === "30" ? 0 : 0;
  let pts = item.series;
  if (days > 0) {
    const cut = new Date();
    cut.setUTCHours(0, 0, 0, 0);
    if (days > 1) cut.setUTCDate(cut.getUTCDate() - (days - 1));
    const cutoffStr = cut.toISOString().slice(0, 10);
    pts = pts.filter(p => String(p.date).length === 10 && p.date >= cutoffStr);
  } else if (heatRange === "30") {
    const cutoffStr = periodStartStr();
    pts = pts.filter(p => String(p.date).length === 10 && p.date >= cutoffStr);
  }
if (!pts.length) { svg.innerHTML = ""; title.textContent = "该范围暂无数据"; if (note) note.textContent = ""; return; }
const rngLabel = heatRange === "all" ? "全部" : heatRange === "30" ? "本期" : `近 ${heatRange} 天`;
if (note) note.textContent = `${rngLabel} Token(含缓存)`;
const W = 400, H = 180, PAD_L = 42, PAD_B = 18, PAD_T = 10, PAD_R = 12;
const vals = pts.map(p => (p.tokens || 0));
const maxV = Math.max(...vals, 1);
const fmtV = (v) => fmtTokens(v);
const yr = new Date().getFullYear();
const parseD = (s) => { s = String(s); return s.length === 10 ? new Date(s) : new Date(yr, +s.slice(0, 2) - 1, +s.slice(3, 5)); };
pts = [...pts].sort((a, b) => parseD(a.date) - parseD(b.date));
const t0 = parseD(pts[0].date), t1 = parseD(pts[pts.length - 1].date);
const dates = [];
for (let t = new Date(t0); t <= t1; t.setDate(t.getDate() + 1)) {
dates.push(t.toISOString().slice(0, 10));
}
const xSpan = W - PAD_L - PAD_R;
const xOf = (d) => PAD_L + (dates.indexOf(d) / Math.max(1, dates.length - 1)) * xSpan;
const y = (v) => H - PAD_B - (v / maxV) * (H - PAD_T - PAD_B);
let inner = "";
for (let g = 0; g <= 4; g++) {
const gy = PAD_T + (H - PAD_T - PAD_B) * g / 4;
inner += `<line x1="${PAD_L}" y1="${gy}" x2="${W-PAD_R}" y2="${gy}" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>`;
inner += `<text x="${PAD_L-5}" y="${gy+3}" fill="#8a93a6" font-size="7.5" text-anchor="end">${fmtV(maxV*(1-g/4))}</text>`;
}
const valOf = (p) => (p.tokens || 0);
const pxs = pts.map(p => ({ x: xOf(p.date), y: y(valOf(p)) }));
let line = "";
for (let i = 0; i < pxs.length; i++) {
if (i === 0) { line += `M${pxs[i].x.toFixed(1)},${pxs[i].y.toFixed(1)}`; continue; }
const p0 = pxs[i - 1], p1 = pxs[i];
const cp1x = p0.x + (p1.x - p0.x) / 2, cp1y = p0.y;
const cp2x = p1.x - (p1.x - p0.x) / 2, cp2y = p1.y;
line += ` C${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
}
const area = line + ` L${pxs[pxs.length-1].x.toFixed(1)},${H-PAD_B} L${pxs[0].x.toFixed(1)},${H-PAD_B} Z`;
const gradId = "gfillT";
inner += `<defs><linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6366f1" stop-opacity="0.55"/><stop offset="1" stop-color="#6366f1" stop-opacity="0"/></linearGradient></defs>`;
inner += `<path d="${area}" fill="url(#${gradId})"/>`;
  inner += `<path d="${line}" fill="none" stroke="#818cf8" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  const svgRect = svg.getBoundingClientRect();
  const actualW = svgRect.width || W;
  const minPxPerLabel = 36;
  const labelStep = Math.max(1, Math.ceil(minPxPerLabel * dates.length / actualW));
  dates.forEach((d, i) => {
    if (i % labelStep === 0) {
      inner += `<text x="${xOf(d).toFixed(1)}" y="${H-4}" fill="#8a93a6" font-size="7.5" text-anchor="middle">${esc(d.slice(5))}</text>`;
    } else if (i === dates.length - 1 && (i - 1) % labelStep !== 0) {
      inner += `<text x="${xOf(d).toFixed(1)}" y="${H-4}" fill="#8a93a6" font-size="7.5" text-anchor="middle">${esc(d.slice(5))}</text>`;
    }
  });
  pts.forEach((p) => {
const px = xOf(p.date), py = y(valOf(p));
if (pts.length <= 60) {
inner += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2" fill="#a5b4fc" opacity="0.9"></circle>`;
} else {
inner += `<rect x="${(px-2).toFixed(1)}" y="${(py-2).toFixed(1)}" width="4" height="4" fill="#a5b4fc" opacity="0.7"></rect>`;
}
});
svg.innerHTML = inner;
const total = vals.reduce((a, v) => a + v, 0);
const maxP = Math.max(...vals);
const costTotal = pts.reduce((a, p) => a + (p.cost || 0), 0);
const costMax = Math.max(...pts.map(p => p.cost || 0), 0);
// 聚合分支: history series 后端已按模型折算率折算, 直接展示
const costStr = costTotal > 0 ? ` · $${costTotal.toFixed(2)} · 峰值 $${costMax.toFixed(2)}` : "";
title.textContent = `${totalName} · ${rngLabel} ${fmtTokens(total)} · 峰值 ${fmtV(maxP)}${costStr}`;
const chartBox = $("#dChart");
let tipEl = $("#chartTip");
if (!tipEl) {
tipEl = document.createElement("div");
tipEl.id = "chartTip";
tipEl.className = "chart-tip";
chartBox.appendChild(tipEl);
}
const byDateMap = {};
pts.forEach(p => { byDateMap[p.date] = p; });
const showTip = (cx, cy) => {
const svgRect = svg.getBoundingClientRect();
const scaleX = svgRect.width / W;
const mx = (cx - svgRect.left) / scaleX;
if (mx < PAD_L - 4 || mx > W - PAD_R + 4) { tipEl.style.display = "none"; return; }
const di = Math.round((mx - PAD_L) / xSpan * (dates.length - 1));
const d = dates[Math.max(0, Math.min(dates.length - 1, di))];
const b = byDateMap[d];
if (!b) { tipEl.style.display = "none"; return; }
tipEl.style.display = "block";
tipEl.innerHTML = `${esc(d)}<br><b>${fmtTokens(b.tokens || 0)}</b> tok<br>${(b.count || 0)} 次`;
tipEl.style.left = "0px";
const tipRect = tipEl.getBoundingClientRect();
const boxRectT = chartBox.getBoundingClientRect();
const pxT = cx - boxRectT.left;
// 溢出右缘时翻到鼠标左侧(钳制左边界), 否则右侧跟随
const lx = (pxT + 14 + tipRect.width > boxRectT.width ? pxT - tipRect.width - 10 : pxT + 14);
tipEl.style.left = Math.max(2, Math.min(lx, boxRectT.width - tipRect.width - 2)) + "px";
// 总量模式: top 跟随鼠标 Y, 但钳制在标题区下方进入曲线区域
const pyT = cy - boxRectT.top;
tipEl.style.top = Math.max(24, Math.min(pyT + 10, boxRectT.height - tipRect.height - 4)) + "px";
};
svg.onmousemove = (e) => { lastHover = { cx: e.clientX, cy: e.clientY }; showTip(e.clientX, e.clientY); };
svg.onmouseleave = () => { lastHover = null; if (tipEl) tipEl.style.display = "none"; };
if (lastHover) showTip(lastHover.cx, lastHover.cy);
return;
}
const selKey = String(selModel);
// 曲线跟随供应商: 选中具体供应商时只取该来源的桶 (free 模式/free 模型不归供应商, 按 key 精确匹配)
const item = state.history.find(h => h.key === selKey
&& (mFilter === "free" || !selSupplier || h.source === selSupplier)) ||
state.history.find(h => h.key === selKey);
  // 模型详情条
  const statItem = state.stats.find(x => x.key === selKey);
  if (detail && statItem) {
    const s = statItem;
    const tot = (s.tokens_in || 0) + (s.tokens_out || 0) + (s.tokens_cache || 0);
    const cells = [
      ["总Token", fmtTokens(tot)],
      ["费用", money((s.cost_total || 0) * (s.meter_rate || 1))],
      ["输入", fmtTokens(s.tokens_in || 0)],
      ["输出", fmtTokens(s.tokens_out || 0)],
      ["缓存", fmtTokens(s.tokens_cache || 0)],
      ["缓存命中", (s.cache_hit || 0).toFixed(1) + "%"],
      ["速率", (s.rate || 0) > 0 ? (s.rate || 0).toFixed(1) + " tok/s" : "—"],
      ["会话", (s.sessions || 0) + " 个"],
      ["请求", (s.count_total || 0) + " 次"],
      ["提示词", (s.prompts || 0) + " 条"],
      ["活跃", (s.days || 0) + " 天"],
    ];
    detail.innerHTML = `<div class="d-detail-name">${(s.source && s.source !== "known" && s.source !== "?") ? `[${esc(supTag(s.source))}] ` : ""}${esc(s.name || s.model)}</div>` +
      cells.map(([k, v]) => `<span class="d-detail-cell"><b>${k}</b>${v}</span>`).join("");
  }
  if (!item || !item.series.length) { svg.innerHTML = ""; title.textContent = "暂无历史数据"; if (note) note.textContent = ""; return; }
  const isFree = !!item.is_free;
  // 跟随右上角天数: 1(今天)/7/30/all, 与热力图语义一致 (UTC 当天 0 点起)
  const days = heatRange === "1" ? 1 : heatRange === "7" ? 7 : heatRange === "30" ? 0 : 0;
  let pts = item.series;
  if (days > 0) {
    const cut = new Date();
    cut.setUTCHours(0, 0, 0, 0);
    if (days > 1) cut.setUTCDate(cut.getUTCDate() - (days - 1));
    const cutoffStr = cut.toISOString().slice(0, 10);
    pts = pts.filter(p => String(p.date).length === 10 && p.date >= cutoffStr);
  } else if (heatRange === "30") {
    const cutoffStr = periodStartStr();
    pts = pts.filter(p => String(p.date).length === 10 && p.date >= cutoffStr);
  }
  if (!pts.length) { svg.innerHTML = ""; title.textContent = "该范围暂无数据"; if (note) note.textContent = ""; return; }
  const rngLabel = heatRange === "all" ? "全部" : heatRange === "30" ? "本期" : `近 ${heatRange} 天`;
  if (note) note.textContent = isFree ? `${rngLabel} Token(含缓存)` : `${rngLabel} Token(含缓存) · 费用(USD)`;
  const W = 400, H = 180, PAD_L = 42, PAD_B = 18, PAD_T = 10, PAD_R = 12;
  const vals = pts.map(p => isFree ? (p.tokens_in + p.tokens_out + p.tokens_cache) : p.cost);
  const maxV = Math.max(...vals, isFree ? 1 : 0.0001);
  const fmtV = (v) => isFree ? fmtTokens(v) : (v >= 1 ? "$" + v.toFixed(2) : "$" + v.toFixed(3));
  // 连续时间轴：从最小日期到最大日期逐日生成，x 按真实日期线性分布
  const yr = new Date().getFullYear();
  const parseD = (s) => { s = String(s); return s.length === 10 ? new Date(s) : new Date(yr, +s.slice(0, 2) - 1, +s.slice(3, 5)); };
  pts = [...pts].sort((a, b) => parseD(a.date) - parseD(b.date));
  const t0 = parseD(pts[0].date), t1 = parseD(pts[pts.length - 1].date);
  const dates = [];
  for (let t = new Date(t0); t <= t1; t.setDate(t.getDate() + 1)) {
    dates.push(t.toISOString().slice(0, 10));
  }
  const xSpan = W - PAD_L - PAD_R;
  const xOf = (d) => PAD_L + (dates.indexOf(d) / Math.max(1, dates.length - 1)) * xSpan;
  const y = (v) => H - PAD_B - (v / maxV) * (H - PAD_T - PAD_B);
  let inner = "";
  // 网格 + 刻度
  for (let g = 0; g <= 4; g++) {
    const gy = PAD_T + (H - PAD_T - PAD_B) * g / 4;
    inner += `<line x1="${PAD_L}" y1="${gy}" x2="${W-PAD_R}" y2="${gy}" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>`;
    inner += `<text x="${PAD_L-5}" y="${gy+3}" fill="#8a93a6" font-size="7.5" text-anchor="end">${fmtV(maxV*(1-g/4))}</text>`;
  }
  // 平滑曲线 (Catmull-Rom -> Bezier)，x 按真实日期
  const valOf = (p) => isFree ? (p.tokens_in + p.tokens_out + p.tokens_cache) : p.cost;
  const pxs = pts.map(p => ({ x: xOf(p.date), y: y(valOf(p)) }));
  let line = "";
  for (let i = 0; i < pxs.length; i++) {
    if (i === 0) { line += `M${pxs[i].x.toFixed(1)},${pxs[i].y.toFixed(1)}`; continue; }
    const p0 = pxs[i - 1], p1 = pxs[i];
    const cp1x = p0.x + (p1.x - p0.x) / 2, cp1y = p0.y;
    const cp2x = p1.x - (p1.x - p0.x) / 2, cp2y = p1.y;
    line += ` C${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
  }
  const area = line + ` L${pxs[pxs.length-1].x.toFixed(1)},${H-PAD_B} L${pxs[0].x.toFixed(1)},${H-PAD_B} Z`;
  const gradId = "gfill" + (isFree ? "f" : "c");
  inner += `<defs><linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6366f1" stop-opacity="0.55"/><stop offset="1" stop-color="#6366f1" stop-opacity="0"/></linearGradient></defs>`;
  inner += `<path d="${area}" fill="url(#${gradId})"/>`;
  inner += `<path d="${line}" fill="none" stroke="#818cf8" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  const svgRect2 = svg.getBoundingClientRect();
  const actualW2 = svgRect2.width || W;
  const labelStep2 = Math.max(1, Math.ceil(36 * dates.length / actualW2));
  dates.forEach((d, i) => {
    if (i % labelStep2 === 0) {
      inner += `<text x="${xOf(d).toFixed(1)}" y="${H-4}" fill="#8a93a6" font-size="7.5" text-anchor="middle">${esc(d.slice(5))}</text>`;
    } else if (i === dates.length - 1 && (i - 1) % labelStep2 !== 0) {
      inner += `<text x="${xOf(d).toFixed(1)}" y="${H-4}" fill="#8a93a6" font-size="7.5" text-anchor="middle">${esc(d.slice(5))}</text>`;
    }
  });
  // 数据点（hover 详情由自定义 tooltip 提供）
  pts.forEach((p) => {
    const px = xOf(p.date), py = y(valOf(p));
    if (pts.length <= 60) {
      inner += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2" fill="#a5b4fc" opacity="0.9"></circle>`;
    } else {
      inner += `<rect x="${(px-2).toFixed(1)}" y="${(py-2).toFixed(1)}" width="4" height="4" fill="#a5b4fc" opacity="0.7"></rect>`;
    }
  });
  svg.innerHTML = inner;
  const total = vals.reduce((a, v) => a + v, 0);
  const maxP = Math.max(...vals);
  // 双指标: token 主值 + 金额副值 (free 无金额不显示)
  const tokTotal = pts.reduce((a, p) => a + (p.tokens_in || 0) + (p.tokens_out || 0) + (p.tokens_cache || 0), 0);
  const tokMax = Math.max(...pts.map(p => (p.tokens_in || 0) + (p.tokens_out || 0) + (p.tokens_cache || 0)), 0);
  const costTotal = pts.reduce((a, p) => a + (p.cost || 0), 0);
  const costMax = Math.max(...pts.map(p => p.cost || 0), 0);
  const costStr = isFree || costTotal <= 0 ? "" : ` · $${costTotal.toFixed(2)} · 峰值 $${costMax.toFixed(2)}`;
  title.textContent = `${item.name} · ${rngLabel} ${fmtTokens(tokTotal)} tok · 峰值 ${fmtTokens(tokMax)} tok${costStr}`;

  // 自定义 hover tooltip：划过任意日期显示当天 Token/次数/费用
  try {
    const chartBox = $("#dChart");
    let tipEl = $("#chartTip");
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.id = "chartTip";
      tipEl.className = "chart-tip";
      chartBox.appendChild(tipEl);
    }
    const byDateMap = {};
    pts.forEach(p => { byDateMap[p.date] = p; });
    const showTip = (cx, cy) => {
      const svgRect = svg.getBoundingClientRect();
      const scaleX = svgRect.width / W;
      const mx = (cx - svgRect.left) / scaleX;
      if (mx < PAD_L - 4 || mx > W - PAD_R + 4) { tipEl.style.display = "none"; return; }
      const ratio = Math.max(0, Math.min(1, (mx - PAD_L) / xSpan));
      const idx = Math.round(ratio * (dates.length - 1));
      const d = dates[idx];
      const p = byDateMap[d];
      let html = `<div class="ct-date">${esc(d)}</div>`;
      if (p) {
        const tok = (p.tokens_in || 0) + (p.tokens_out || 0) + (p.tokens_cache || 0);
        html += `<div class="ct-row"><span>Token</span><b>${fmtTokens(tok)}</b></div>`;
        html += `<div class="ct-row"><span>次数</span><b>${p.count || 0}</b></div>`;
        html += `<div class="ct-row"><span>费用</span><b>${money(p.cost || 0)}</b></div>`;
      } else {
        html += `<div class="ct-empty">无使用</div>`;
      }
      tipEl.innerHTML = html;
      tipEl.style.display = "block";
      const boxRect = chartBox.getBoundingClientRect();
      const px = cx - boxRect.left;
      const ty = cy - boxRect.top + 12;
      // 溢出右缘时翻到鼠标左侧(钳制左边界), 否则右侧跟随
      tipEl.style.left = "0px";
      const tw = tipEl.getBoundingClientRect().width;
      const lx2 = (px + 12 + tw > boxRect.width ? px - tw - 8 : px + 12);
      tipEl.style.left = Math.max(2, Math.min(lx2, boxRect.width - tw - 2)) + "px";
      tipEl.style.top = Math.min(ty, boxRect.height - 96) + "px";
    };
    svg.onmousemove = (e) => { lastHover = { cx: e.clientX, cy: e.clientY }; showTip(e.clientX, e.clientY); };
    svg.onmouseleave = () => { lastHover = null; if (tipEl) tipEl.style.display = "none"; };
    if (lastHover) showTip(lastHover.cx, lastHover.cy);
  } catch (_) { /* hover tooltip 失败不影响图表 */ }
}

function renderStats() {
  const ds = $("#dStats");
  ds.innerHTML = "";
  const key = modelKey();
  const st = filteredStats();
  const isOverview = state.mode === "overview";
  const mode = state.mode === "weekly" ? "weekly" : state.mode === "monthly" ? "monthly" : "session";
  const costKey = mode === "weekly" ? "cost_w" : mode === "monthly" ? "cost_m" : "cost_s";
  const periodLabel = isOverview ? "累计" : (mode === "weekly" ? "周" : mode === "monthly" ? "月" : "5h");
  const reqOf = x => (x.group === "free" && isOverview) ? (x.count_total || 0) : (x[key] || 0);
  const totalReq = st.reduce((a, x) => a + reqOf(x), 0);
  const totalCost = st.reduce((a, x) => a + (x[costKey] || 0) * (x.meter_rate || 1), 0);
  const totalTok = st.reduce((a, x) => a + (x.tokens_in || 0) + (x.tokens_out || 0) + (x.tokens_cache || 0), 0);
  [["请求数", totalReq + " 次"], [periodLabel + "费用", money(totalCost)], ["模型数", st.length + " 个"]].forEach(([k, v]) => {
    const div = document.createElement("div");
    div.className = "s-chip";
    div.innerHTML = `<div class="s-v">${v}</div><div class="s-k">${k}</div>`;
    ds.appendChild(div);
  });
  const tokDiv = document.createElement("div");
  tokDiv.className = "s-chip";
  tokDiv.innerHTML = `<div class="s-v">${fmtTokens(totalTok)}</div><div class="s-k">Token</div>`;
  ds.appendChild(tokDiv);
}

  function activeSuppliers() {
    const list = supplierList();
    return list.length ? list : SUPPLIER_ORDER.slice(0, 1);
  }

const TIME_RANGE_ORDER = ["today", "7", "all"];
const VIEW_RANGE = { today: "today", "7": "7d", "30": "30d", all: "all" };
const viewIdFor = (src, range) => `${src || "all"}_${VIEW_RANGE[range] || range}`;

// 本期起始日期: 订阅周期起点 (state.period_start), 未知时回退字面30天
function periodStartStr() {
  if (state.period_start) return state.period_start;
  const c = new Date();
  c.setUTCHours(0, 0, 0, 0);
  c.setUTCDate(c.getUTCDate() - 29);
  return c.toISOString().slice(0, 10);
}

// 大屏: 具体模型 + 时间范围 (heatRange: 1|7|30|all) 的聚合, 供统计卡/热力图跟随模型选择
function modelAggForRange(key, range) {
  const agg = { tokens: 0, cost: 0, count: 0, input: 0, output: 0, cache: 0, days: 0 };
  const h = state.history.find(x => x.key === key);
  if (!h) return agg;
  const daySet = new Set();
  const cut = (() => {
    if (range === "all") return "";
    if (range === "30") return periodStartStr();
    const c = new Date();
    c.setUTCHours(0, 0, 0, 0);
    if (range === "7") c.setUTCDate(c.getUTCDate() - 6);
    return c.toISOString().slice(0, 10);
  })();
  (h.series || []).forEach(p => {
    const d = String(p.date);
    if (cut && (d.length !== 10 || d < cut)) return;
    agg.tokens += (p.tokens_in || 0) + (p.tokens_out || 0) + (p.tokens_cache || 0);
    agg.cost += p.cost || 0;
    agg.count += p.count || 0;
    agg.input += p.tokens_in || 0;
    agg.output += p.tokens_out || 0;
    agg.cache += p.tokens_cache || 0;
    daySet.add(d);
  });
  agg.days = daySet.size;
  return agg;
}

function modelHeatmapByDate(key) {
  const byDate = {};
  const h = state.history.find(x => x.key === key);
  (h?.series || []).forEach(p => {
    const d = String(p.date);
    const b = byDate[d] || (byDate[d] = { count: 0, tokens: 0, cost: 0 });
    b.count += p.count || 0;
    b.tokens += (p.tokens_in || 0) + (p.tokens_out || 0) + (p.tokens_cache || 0);
    b.cost += p.cost || 0;
  });
  return byDate;
}

function renderMini() {
  const pctEl = $("#miniPct");
  if (!pctEl) return;
  const wins = displayWindows();
  const s = wins.find(x => x.kind === "session"), w = wins.find(x => x.kind === "weekly"), m = wins.find(x => x.kind === "monthly");
  if (!s || !w || !m) return;
  const hot = [s, w, m].reduce((a, b) => (a.pct > b.pct ? a : b));
  const sev = miniSeverity(hot.pct);
  pctEl.textContent = "本月 " + Math.round(m.pct) + "%";
  const costEl = $("#miniCost");
  costEl.textContent = m.used != null ? money(m.used) : "—";
  costEl.title = "本月官方计量 " + (m.used != null ? money(m.used) : "—") + (m.limit != null ? " / " + money(m.limit) : "");
  $("#miniState").textContent = sev.label;
  const dot = $("#miniDot");
  dot.style.background = sev.color;
  dot.style.boxShadow = "0 0 5px " + sev.color;
  const key = $("#miniKey");
  key.textContent = state.keyOk ? "Key ✓" : "Key ✗";
  key.className = "mb-item " + (state.keyOk ? "mb-ok" : "mb-err");
}

// Mini 状态阈值与 ringColor 一致: >=95 告警, >=70 注意, 其余正常
function miniSeverity(pct) {
  if (pct >= 95) return { label: "告警", color: "var(--danger)" };
  if (pct >= 70) return { label: "注意", color: "var(--warn)" };
  return { label: "正常", color: "var(--green)" };
}

const HEAT_COLORS = ["#1a2333", "#2b3a67", "#3f55a8", "#6366f1", "#8b8bf5", "#a5a5ff"];

function heatColor(v, max) {
  if (max <= 0 || v <= 0) return HEAT_COLORS[0];
  const t = v / max;
  const idx = Math.min(HEAT_COLORS.length - 1, 1 + Math.floor(t * (HEAT_COLORS.length - 2)));
  return HEAT_COLORS[idx];
}

function renderLarge() {
  const box = $("#dHeatmap");
  if (!box) return;
const list = activeSuppliers();
// free 模式聚合全部来源(不归任一供应商), 否则跟随当前供应商
const src = (mFilter === "free") ? "all" : (selSupplier && list.includes(selSupplier) ? selSupplier : "all");
const srcName = src === "all" ? "全部供应商" : supName(src);
// 选中具体模型(↑↓)时, 统计卡/热力图跟随该模型 + 时间范围
const isModelSel = !!(selModel && selModel !== "__total__");
const selName = isModelSel
  ? (state.history.find(x => x.key === selModel) || {}).name || String(selModel)
  : srcName;

  // ---- 供应商 tabs (轮播: 「全部」固定, 具体供应商窗口 5 槽位, 选中项超窗时平移) ----
  const tabs = $("#dSupTabs");
  if (tabs) {
    if (supOffset > Math.max(0, list.length - SUP_TABS_MAX)) supOffset = Math.max(0, list.length - SUP_TABS_MAX);
    const winS = list.slice(supOffset, supOffset + SUP_TABS_MAX);
    tabs.innerHTML = [["all", "全部"], ...winS.map(s => [s, supName(s)])]
      .map(([s, n]) => `<button class="sup-tab${src === s ? " on" : ""}" data-sup="${esc(s)}">${esc(n)}</button>`).join("");
tabs.querySelectorAll(".sup-tab").forEach(b => {
b.onclick = () => {
selSupplier = b.dataset.sup === "all" ? null : b.dataset.sup;
mFilter = "all"; // 切供应商默认回"全部"分类
selModel = "__total__"; // 切供应商默认显示总量曲线(不出现在模型明细)
render();
};
});
  }

  // ---- 时间范围 ----
  const range = $("#dRange");
  if (range) {
    range.innerHTML = [["1", "今天"], ["7", "7天"], ["30", "本期"], ["all", "全部"]]
      .map(([v, n]) => `<button class="r-btn${heatRange === v ? " on" : ""}" data-r="${v}">${n}</button>`).join("");
    range.querySelectorAll(".r-btn").forEach(b => {
      b.onclick = () => { heatRange = b.dataset.r; render(); };
    });
  }

  // ---- 统计卡（跟随供应商 + 时间范围）----
  const hmRows = state.heatmap.filter(x => src === "all"
    ? (!SUBSET_SRCS.includes(x.src) || x.account === "other")
    : x.src === src);
  const nowD = new Date();
  let startD = new Date(nowD);
  if (heatRange === "1") startD.setUTCHours(0, 0, 0, 0);
  else if (heatRange === "7") startD.setUTCDate(startD.getUTCDate() - 6);
  else if (heatRange === "30") {
    // 本期 = 当前订阅周期起始 (state.period_start), 订阅日未知时回退字面30天
    if (state.period_start) startD = new Date(state.period_start + "T00:00:00Z");
    else startD.setUTCDate(startD.getUTCDate() - 29);
  }
  const startDate = heatRange === "all" ? "" : startD.toISOString().slice(0, 10);
  let agg;
  if (isModelSel) {
    agg = modelAggForRange(selModel, heatRange);
  } else {
    // "本期"(30d): 优先用后端 view(go_30d/all_30d) totals — 仅登录账号官方口径, 已减抵扣
    // (后端对 range!=all 过滤 account=other, 保证进度口径纯净)
    const pv = (heatRange === "30" && (src === "all" || src === "go"))
      ? (state._viewCache && state._viewCache[viewIdFor(src, "30")])
      : null;
    if (pv && pv.totals) {
      agg = { tokens: pv.totals.tokens || 0, cost: pv.totals.cost || 0, count: pv.totals.count || 0,
              input: pv.totals.input || 0, output: pv.totals.output || 0, cache: pv.totals.cache || 0,
              days: pv.totals.days || 0 };
    } else {
      agg = { tokens: 0, cost: 0, count: 0, input: 0, output: 0, cache: 0, days: new Set() };
      hmRows.forEach(x => {
        if (startDate && x.date < startDate) return;
        agg.tokens += x.tokens || 0;
        agg.cost += x.cost || 0;
        agg.count += x.count || 0;
        agg.input += x.input || 0;
        agg.output += x.output || 0;
        agg.cache += x.cache || 0;
        agg.days.add(x.date);
      });
      agg.days = agg.days.size;
      // 回退路径: 本期减本期抵扣; 全部=全览统计不减
      if ((src === "all" || src === "go") && heatRange === "30") {
        agg.cost = Math.max(0, agg.cost - (state.period_deduct || 0));
      }
    }
  }
  const row = $("#dStatsRow");
  if (row) {
    const cards = [
      ["总Token", fmtTokens(agg.tokens)],
      ["总费用", money(agg.cost)],
      ["请求数", (agg.count || 0) + " 次"],
      ["输入", fmtTokens(agg.input)],
      ["输出", fmtTokens(agg.output)],
      ["缓存", fmtTokens(agg.cache)],
      ["活跃天数", (agg.days || 0) + " 天"],
    ];
    row.innerHTML = cards.map(([k, v]) =>
      `<div class="stat-card"><div class="sk">${k}</div><div class="sv">${v}</div></div>`).join("");
  }

  // ---- GitHub 风格热力图 (周列日历) ----
  const rows = hmRows;
  const byDate = isModelSel ? modelHeatmapByDate(selModel) : (() => {
    const m = {};
    rows.forEach(x => { m[x.date] = { count: x.count, tokens: x.tokens, cost: x.cost }; });
    return m;
  })();
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  let start = new Date();
  if (heatRange === "1") start.setHours(0, 0, 0, 0);
  else if (heatRange === "7") start.setDate(start.getDate() - 6);
  else if (heatRange === "30") start.setDate(start.getDate() - 29);
  else {
    const dates = Object.keys(byDate).sort();
    start = dates.length ? new Date(dates[0]) : new Date();
  }
  // 对齐到周日 (周起始)
  start.setDate(start.getDate() - start.getDay());
  const days = [];
  for (let d = new Date(start); d <= now; d.setDate(d.getDate() + 1)) {
    days.push(d.toISOString().slice(0, 10));
  }
  // 分组成周列
  const weeks = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));
  const maxV = Math.max(1, ...Object.values(byDate).map(v => v.tokens || 0));
  const DOW = ["日", "一", "二", "三", "四", "五", "六"];
  let h = `<div class="heat-head"><span class="heat-title">${esc(selName)} · 使用热力图</span><span class="heat-legend">少 <i style="background:${HEAT_COLORS[0]}"></i><i style="background:${HEAT_COLORS[1]}"></i><i style="background:${HEAT_COLORS[2]}"></i><i style="background:${HEAT_COLORS[3]}"></i><i style="background:${HEAT_COLORS[4]}"></i><i style="background:${HEAT_COLORS[5]}"></i> 多</span></div>`;
  h += `<div class="heat-wrap"><div class="heat-dow">${DOW.map(d => `<span>${d}</span>`).join("")}</div>`;
  weeks.forEach(week => {
    h += `<div class="heat-col">`;
    for (let d = 0; d < 7; d++) {
      const date = week[d];
      if (!date) { h += `<div class="heat-cell" style="background:transparent"></div>`; continue; }
      const v = byDate[date];
      const col = v ? heatColor(v.tokens, maxV) : HEAT_COLORS[0];
      const tip = v ? `${date} · ${v.count}次 · ${fmtTokens(v.tokens)} tok · $${(v.cost || 0).toFixed(4)}` : date + " · 无使用";
      h += `<div class="heat-cell" title="${esc(tip)}" style="background:${col}"></div>`;
    }
    h += `</div>`;
  });
  h += `</div>`;
  box.innerHTML = h;
}



// 当前模式需要的 view id 集合 (纯策略, 供 refresh 与 ensureExpandedViews 共用):
// 基础 summary 三项常取; Expanded 级逐日序列/模型曲线只在完整面板需要时预取。
function neededViewIds(src0) {
  const needed = new Set([viewIdFor(src0, timeRange), viewIdFor(src0, "all"), "all_all"]);
  if (uiState === "large") {
    needed.add(viewIdFor("all", heatRange));
    needed.add("all_daily");
    if (heatRange === "30" && src0 !== "all") needed.add(viewIdFor(src0, "30"));
    if (selModel && selModel !== "__total__") {
      needed.add(`model_${selModel}_${heatRange}`);
      needed.add(`model_${selModel}_daily`);
    }
  }
  return [...needed];
}

// 进入 Expanded 时补齐缺失的 view 缓存 (fetchView 命中缓存不发包, 不整段清缓存重拉)。
async function ensureExpandedViews() {
  if (uiState !== "large") return;
  const ids = neededViewIds(selSupplier || "all");
  await Promise.all(ids.map(id => fetchView(id)));
}

async function refresh() {
  try {
    const data = await window.widgetAPI.apiGetState();
    state.windows = data.windows; state.stats = data.stats; state.history = data.history || [];
    state.suppliers = data.suppliers || {};
    state.heatmap = data.heatmap || [];
    state.rows = data.rows; state.lastRefresh = Date.now();
    lastCal = data.calibration;
    state.server = data.server || null;
    state.server_error = data.server_error || null;
    state.keyOk = !!data.key; state.models = data.models || 0;
    await loadFormula(false);
    state._viewCache = {};
    await Promise.all(neededViewIds(selSupplier || "all").map(id => fetchView(id)));
    render();
    // Phase 5A: 既有刷新控件仅刷新当前可观测性 tab + range (force), 不扇形刷新其它 tab
    if (uiState === "large") OCW.obsEnsure(OCW.getActiveTab(), true);
  } catch (e) {
    $("#keyDot").className = "dot err"; $("#keyText").textContent = "后端未连接";
  }
}

let snapMode = false; // 吸顶态：顶栏可拖可点（关穿透），顶栏下点击穿透

function setUiState(v, noResize, manual) {
  // 吸顶态下手动切走小窗（吸顶时点 _ / □ 展开）→ 通知主进程退出吸顶并关闭穿透
  if (manual && snapMode && v !== "small") {
    snapMode = false;
    window.widgetAPI.exitSnap();
  }
  uiState = v;
  const onLarge = v === "large";
  // 模式化 chrome: 页脚/滑杆等按状态显隐 (CSS 侧 body.mode-*)
  document.body.classList.toggle("mode-small", v === "small");
  document.body.classList.toggle("mode-compact", v === "mid");
  document.body.classList.toggle("mode-expanded", v === "large");
  $("#btnExpand").classList.toggle("on", onLarge);
  $("#btnExpand").textContent = onLarge ? "▣" : "□";
  $("#smallView").classList.toggle("on", v === "small");
  $("#compactView").style.display = v === "mid" ? "flex" : "none";
  $("#dashView").classList.toggle("on", onLarge);
  // Phase 5A: 可观测性仪表盘仅在 large 显示; 首次显示只懒加载当前 tab
  const obsRoot = document.getElementById("obsDashboard");
  if (obsRoot) obsRoot.classList.toggle("on", onLarge);
  if (onLarge) {
    OCW.obsEnsure(OCW.getActiveTab());
    ensureExpandedViews();
  }
  if (!noResize) {
    window.widgetAPI.resize(v === "small" ? "small" : (onLarge ? "large" : "mid"));
  }
  try { localStorage.setItem("uiState", v); window.widgetAPI.saveUiState(v); } catch (_) {}
  setTimeout(() => render(), 250);
}

function showCtx(x, y) {
  const c = $("#ctx");
  c.style.visibility = "hidden";
  c.classList.add("show");
  const r = c.getBoundingClientRect();
  let nx = Math.min(x, window.innerWidth - r.width - 6);
  let ny = Math.min(y, window.innerHeight - r.height - 6);
  c.style.left = Math.max(4, nx) + "px";
  c.style.top = Math.max(4, ny) + "px";
  c.style.visibility = "visible";
}
function hideCtx() { $("#ctx").classList.remove("show"); }
function showModal(id) { $("#" + id).classList.add("show"); }
function hideModal(id) { $("#" + id).classList.remove("show"); }

function applyOpacity(v) {
  const val = Math.max(0.1, Math.min(1, v));
  document.documentElement.style.setProperty("--alpha", String(val));
  const lbl = document.getElementById("opLabel");
  if (lbl) lbl.textContent = Math.round(val * 100) + "%";
  const sl = document.getElementById("opSlider");
  if (sl) sl.value = String(Math.round(val * 100));
}
async function initOpacity() {
  try {
    const v = Number(localStorage.getItem("opacity") || 0.72);
    applyOpacity(v);
  } catch (_) { setTimeout(initOpacity, 500); }
}
document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("click", (e) => {
    if (e.target && e.target.closest && e.target.closest("button")) e.target.closest("button").blur();
  });
  const sl = document.getElementById("opSlider");
    if (sl) sl.addEventListener("input", () => {
      const v = Number(sl.value) / 100;
      applyOpacity(v);
      localStorage.setItem("opacity", String(v));
    });
});
async function initDefaultView() {
  try {
    // 冷启动恒回 Mini（窗口出生即 Mini 尺寸, noResize 避免启动闪动）。
    setUiState("small", true);
  } catch (_) {}
}

async function showServer() {
  const d = await window.widgetAPI.apiGetConfig().catch(() => ({}));
  // 不再回填任何密钥；Cookie 始终留空，占位提示是否已配置
  const cookieEl = $("#srvCookie");
  cookieEl.value = "";
  cookieEl.placeholder = d.auth_configured ? "已配置（留空则不修改）" : "未配置";
  // workspace_id 是标识符（非密钥），可回填
  $("#srvWs").value = d.workspace_id || "";
  $("#srvMsg").textContent = ""; showModal("serverMask");
}
async function showCal() {
  const d = await window.widgetAPI.apiGetConfig().catch(() => ({}));
  const c = d.calibration || {};
  $("#calS").value = c.session ?? ""; $("#calW").value = c.weekly ?? ""; $("#calM").value = c.monthly ?? "";
  const s = win("session"), w = win("weekly"), m = win("monthly");
  $("#locS").textContent = " 当前 " + (s ? Math.round(s.pct) : 0) + "%";
  $("#locW").textContent = " 当前 " + (w ? Math.round(w.pct) : 0) + "%";
  $("#locM").textContent = " 当前 " + (m ? Math.round(m.pct) : 0) + "%";
  $("#calMsg").textContent = ""; showModal("calMask");
}
async function showKey() {
  const d = await window.widgetAPI.apiGetConfig().catch(() => ({}));
  const inp = $("#keyInput");
  // 不回填密钥；空则不改动
  inp.value = "";
  inp.placeholder = d.api_key_configured ? "已配置（留空则不修改）" : "未配置";
  $("#keyMsg").textContent = ""; showModal("keyMask");
}
async function doLogin() {
  try {
    const r = await window.widgetAPI.openLogin();
    if (!r || !r.opened) { alert("无法打开浏览器：" + (r && r.error ? r.error : "未知错误")); return; }
  } catch (e) {
    alert("打开浏览器失败：" + (e.message || e));
    return;
  }
  alert("已在系统浏览器中打开 opencode.ai/auth\n\n请按以下步骤操作：\n\n1. 在浏览器中用你的账号登录 opencode.ai\n2. 登录成功后，地址栏会跳转到类似：\n   https://opencode.ai/workspace/wrk_xxxxx/go\n3. 按 F12 → Application → Cookies → 复制 auth 的值\n4. 回到悬浮窗，右键 → 服务端设置\n5. 粘贴 auth_cookie 和 workspace_id（从 URL 里拿）→ 保存\n6. 右键 → 拉取官方数值 → 完成");
}
async function doGrab() {
  // 自动抓取登录态：由主进程在应用内登录窗口捕获并直接提交本地 server，
  // 渲染层只收到配置状态，不再接触 auth_cookie / workspace_id。
  try {
    const r = await window.widgetAPI.grabAuth();
    if (!r || !r.ok) { alert("自动抓取失败：" + (r && r.error ? r.error : "未知错误")); return; }
  } catch (e) {
    alert("自动抓取失败：" + (e.message || e)); return;
  }
  await refresh();
  alert("已自动抓取登录态并同步官方数值");
}


$("#btnMin").onclick = () => setUiState(uiState === "small" ? "mid" : "small", false, true);
$("#btnExpand").onclick = () => setUiState(uiState === "large" ? "mid" : "large", false, true);
$("#btnClose").onclick = () => window.widgetAPI.quit();
// Compact 快看卡: 显式入口进入 Expanded 完整面板
$("#btnOpenFull").onclick = () => setUiState("large", false, true);

// Mini 常驻态: 点击状态条(或 Enter/Space)进入 Compact 快速查看; 手动切换会先退出吸顶。
$("#smallView").addEventListener("click", () => {
  if (uiState === "small") setUiState("mid", false, true);
});
$("#miniBar").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (uiState === "small") setUiState("mid", false, true);
  }
});

// 键盘导航: ←→ 切供应商(含"全部"), ↑↓ 切模型 (小窗/大窗生效)
function cycleSupplier(dir) {
  const real = activeSuppliers();
  if (!real.length) return;
  const list = [null, ...real]; // null = "全部", 与 tab 栏顺序一致
  if (!list.includes(selSupplier)) selSupplier = real[0];
  const i = list.indexOf(selSupplier);
selSupplier = list[(i + dir + list.length) % list.length];
mFilter = "all"; // 左右键切供应商同样回到"全部"分类
// 轮播: 选中项超出可见窗口时平移 (「全部」不参与平移)
const li = real.indexOf(selSupplier);
if (li >= 0) {
if (li < supOffset) supOffset = li;
else if (li >= supOffset + SUP_TABS_MAX) supOffset = li - SUP_TABS_MAX + 1;
}
selModel = "__total__"; // 切供应商默认显示总量曲线
render();
}
function cycleModel(dir) {
// 与模型列表共用同一排序, 上下键按屏幕显示顺序切换
// 总量(__total__)是虚拟首项: 向上越过第一个模型回到总量, 总量向下进入第一个模型
let list = rankSortedStats();
// 供应商 tab 选中时只在该供应商的模型间切换 (free 模型散落各来源, 不归任一供应商)
if (selSupplier && mFilter !== "free") list = list.filter(x => (x.source || "") === selSupplier);
const keys = list.map(x => x.key);
if (!keys.length) return;
if (selModel === "__total__") {
// 总量态: 向下进第一个模型, 向上保持总量(或循环到末尾)
selModel = dir > 0 ? keys[0] : keys[keys.length - 1];
render();
return;
}
if (!selModel || !keys.includes(selModel)) selModel = keys[0];
const i = keys.indexOf(selModel);
let ni = i + dir;
if (ni < 0) { selModel = "__total__"; render(); return; } // 向上越过第一个 -> 总量
selModel = keys[ni % keys.length];
render();
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    // Esc: Compact/Expanded -> Mini (输入控件内不劫持)
    const t = e.target;
    const typing = t && t.closest && t.closest("input, select, textarea");
    if (!typing && uiState !== "small") {
      setUiState("small", false, true);
      e.preventDefault();
    }
    return;
  }
  if (e.key === "Tab") {
    if (uiState !== "mid") cycleSupplier(1);
    e.preventDefault();
    return;
  }
  if (e.key === "x" || e.key === "X") {
    timeRange = TIME_RANGE_ORDER[(TIME_RANGE_ORDER.indexOf(timeRange) + 1) % TIME_RANGE_ORDER.length];
    saveViewPrefs(); render();
    e.preventDefault();
    return;
  }
  if (uiState === "mid") return;
  if (e.key === "ArrowLeft") { cycleSupplier(-1); e.preventDefault(); }
  else if (e.key === "ArrowRight") { cycleSupplier(1); e.preventDefault(); }
  else if (e.key === "ArrowUp") { cycleModel(-1); e.preventDefault(); }
  else if (e.key === "ArrowDown") { cycleModel(1); e.preventDefault(); }
});

function saveViewPrefs() {
  try { localStorage.setItem("mFilter", mFilter); localStorage.setItem("mUnit", mUnit); localStorage.setItem("timeRange", timeRange); } catch (_) {}
}
function loadViewPrefs() {
  try {
    const f = localStorage.getItem("mFilter");
    const u = localStorage.getItem("mUnit");
    const tr = localStorage.getItem("timeRange");
    if (f === "go" || f === "free") mFilter = f;
    if (u === "token") mUnit = u;
    if (tr === "today" || tr === "7" || tr === "all") timeRange = tr;
  } catch (_) {}
}
document.querySelectorAll(".m-filters").forEach(f => {
  f.addEventListener("click", (e) => {
    const btn = e.target.closest(".f-btn");
    if (!btn) return;
    mFilter = btn.dataset.f;
    saveViewPrefs(); render();
  });
});
document.querySelectorAll(".unit-btn").forEach(b => {
  b.addEventListener("click", () => {
    mUnit = mUnit === "token" ? "count" : "token";
    saveViewPrefs(); render();
  });
});

document.addEventListener("contextmenu", (e) => { e.preventDefault(); showCtx(e.clientX, e.clientY); });
document.addEventListener("click", (e) => { if (!e.target.closest(".ctx")) hideCtx(); });
$("#ctx").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const a = b.dataset.a;
  hideCtx();
  if (a === "refresh") refresh();
  if (a === "login") doLogin();
  if (a === "grab") doGrab();
  if (a === "server") showServer();
  if (a === "calibrate") showCal();
  if (a === "key") showKey();
  if (a === "quit") window.widgetAPI.quit();
});

$("#calSave").onclick = async () => {
  const v = { session: +$("#calS").value || 0, weekly: +$("#calW").value || 0, monthly: +$("#calM").value || 0 };
  const r = await window.widgetAPI.apiPostCalibrate(v).catch(() => ({ ok: false }));
  $("#calMsg").textContent = r.ok ? "已保存 ✓" : "保存失败";
  if (r.ok) { setTimeout(() => hideModal("calMask"), 900); refresh(); }
};
$("#calClear").onclick = async () => {
  await window.widgetAPI.apiPostCalibrate({ session: 0, weekly: 0, monthly: 0 }).catch(() => {});
  $("#calMsg").textContent = "已清除"; refresh();
};
$("#keySave").onclick = async () => {
  const key = $("#keyInput").value.trim();
  // 空输入 = 用户未修改，直接跳过，避免误清空已配置的 Key
  if (!key) { $("#keyMsg").textContent = "未修改"; return; }
  const r = await window.widgetAPI.apiPostKey(key).catch(() => ({ ok: false }));
  $("#keyMsg").textContent = r.ok ? "Key 已保存 ✓" : "保存失败";
  if (r.ok) { $("#keyInput").value = ""; setTimeout(() => hideModal("keyMask"), 900); refresh(); }
};
$("#keyCancel").onclick = () => hideModal("keyMask");
$("#srvGrab").onclick = async () => {
  $("#srvMsg").textContent = "正在打开应用内登录窗口…";
  try {
    const r = await window.widgetAPI.grabAuth();
    if (r && r.ok) {
      $("#srvCookie").value = "";
      $("#srvMsg").textContent = "已自动获取并保存 ✓";
      await refresh();
    } else {
      $("#srvMsg").textContent = "未获取到登录态：" + (r && r.error ? r.error : "未知错误");
    }
  } catch (e) {
    $("#srvMsg").textContent = "自动抓取失败：" + (e.message || e);
  }
};
$("#srvSave").onclick = async () => {
  const cookie = $("#srvCookie").value.trim();
  const ws = $("#srvWs").value.trim();
  // 空 Cookie = 未修改，跳过提交，避免误清空
  if (!cookie) { $("#srvMsg").textContent = "Cookie 为空，未提交"; return; }
  const r = await window.widgetAPI.apiPostServer(cookie, ws).catch(() => ({ ok: false }));
  if (!r.ok) { $("#srvMsg").textContent = "保存失败"; return; }
  $("#srvCookie").value = "";
  $("#srvMsg").textContent = "已保存，正在同步…";
  const s = await window.widgetAPI.apiPostSync().catch(() => ({ ok: false, error: "同步失败" }));
  await refresh();
  if (s.ok) { $("#srvMsg").textContent = "✓ 服务器数值已同步"; setTimeout(() => hideModal("serverMask"), 1200); }
  else $("#srvMsg").textContent = "已保存，但同步失败：" + (s.error || "未知错误");
};
$("#srvClear").onclick = async () => {
  await window.widgetAPI.apiPostServer("", "").catch(() => {});
  $("#srvMsg").textContent = "已清除"; refresh();
};
["serverMask","calMask","keyMask"].forEach(id => {
  $("#" + id).addEventListener("click", (e) => { if (e.target === $("#" + id)) $("#" + id).classList.remove("show"); });
});


window.widgetAPI.onSnapSmall(() => {
  if (uiState !== "small") setUiState("small", true);
  // 进入吸顶态：上报顶栏高度并开启点击穿透（主进程按光标位置动态开关）
  snapMode = true;
  const el = document.querySelector("header");
  const headerH = el ? Math.round(el.getBoundingClientRect().height) : 44;
  window.widgetAPI.setClickThrough(true, headerH);
});
window.widgetAPI.onSnapRestore((s) => {
  // 先关穿透恢复完整交互，再切 UI 状态（主进程已负责 resize，此处 noResize）
  snapMode = false;
  window.widgetAPI.setClickThrough(false);
  const t = s === "large" || s === "small" ? s : "mid";
  if (uiState !== t) setUiState(t, true);
});
initOpacity();
syncScale();
loadViewPrefs();
function syncScale() {
  const s = Math.max(0.7, Math.min(1.4, (window.innerWidth || 560) / 560));
  document.documentElement.style.setProperty("--scale", String(s));
}
window.addEventListener("resize", syncScale);
setTimeout(initDefaultView, 200);
setTimeout(() => loadFormula(false), 300);
setTimeout(refresh, 100);
syncScale();
setInterval(refresh, 60000);
setInterval(render, 1000);
