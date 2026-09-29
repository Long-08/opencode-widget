#!/usr/bin/env python3
"""Data-only server for the Go usage widget.

Python 只负责取数/计算，前端与窗口交互交给 Electron (electron/main.js)。
Serves JSON on http://127.0.0.1:8765/api/*
"""
import atexit
import sys

# A packaged install directory may be read-only: never write __pycache__ there.
sys.dont_write_bytecode = True

import json
import os
import sqlite3
import sys
import threading
import time
import datetime
import bisect
import hmac
import importlib.util
import secrets
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

_spec = importlib.util.spec_from_file_location("gw", os.path.join(APP_DIR, "go-usage-widget.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

try:
    import server_data as sd
except Exception:
    sd = None

try:
    import usage_remote as ur
except Exception:
    ur = None

import views as vw
import formula_registry as fr
import observability
import timeline
import forecasting
import runtime_lifecycle as rl

PORT = int(os.environ.get("OPENCODE_WIDGET_PORT") or 8765)
CACHE = {"state": None, "ts": 0, "lock": threading.Lock()}

# ---- Phase 2A: runtime token + runtime.json ----
# Token is generated lazily, never logged and never returned by any endpoint.
_TOKEN = None

# ---- Phase 6A: process lifecycle ----
# Non-secret instance id: generated once per process, identifies this run in
# runtime.json. Determined lazily so tests can observe a fresh value.
_INSTANCE_ID = None

# Epoch seconds of the last successful authenticated request (any endpoint).
# None until the first auth; the idle watchdog ignores the pre-auth window.
_LAST_AUTH_SEEN = None

# Idle self-exit: exit after this many seconds without authenticated traffic.
# 0 (or negative) disables the watchdog entirely.
TIMEOUT = int(os.environ.get("OPENCODE_WIDGET_IDLE_TIMEOUT_S") or 300)
IDLE_WATCHDOG_INTERVAL_S = 15


def ensure_instance_id():
    """惰性生成非机密的实例 id (os.urandom(16).hex()), 每进程一次。"""
    global _INSTANCE_ID
    if _INSTANCE_ID is None:
        _INSTANCE_ID = rl.new_instance_id()
    return _INSTANCE_ID


def ensure_runtime_token():
    """惰性生成 >=256-bit 的运行时 token (secrets.token_urlsafe(32))。"""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = secrets.token_urlsafe(32)
    return _TOKEN


def _runtime_dir():
    return os.environ.get("OPENCODE_WIDGET_RUNTIME_DIR") or os.path.join(
        tempfile.gettempdir(), "opencode-widget")


def _write_runtime_info(port):
    """原子写入 runtime.json (tmp + os.replace), 供 Electron / 脚本读取 token。

    任何失败 (含目录创建) 都静默忽略, 绝不拖垮服务器。
    """
    try:
        directory = _runtime_dir()
        os.makedirs(directory, exist_ok=True)
        payload = {
            "token": ensure_runtime_token(),
            "port": int(port),
            "pid": os.getpid(),
            "created": int(time.time() * 1000),
            "instance_id": ensure_instance_id(),
        }
        path = os.path.join(directory, "runtime.json")
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        pass


def _cleanup_runtime_info():
    """Best-effort delete of runtime.json IFF it belongs to this process.

    Parsed ``pid`` must equal os.getpid(): a file left by another (stale or
    live) process is never touched. Missing/unreadable/corrupt file or any
    other failure -> silent no-op; must never raise.
    """
    try:
        path = os.path.join(_runtime_dir(), "runtime.json")
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict) and payload.get("pid") == os.getpid():
            os.remove(path)
    except Exception:
        pass


def _read_runtime_info():
    """Best-effort parse of runtime.json; None on any failure. Never raises."""
    try:
        path = os.path.join(_runtime_dir(), "runtime.json")
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _probe_widget_health(port, timeout=1.0):
    """Short /api/health probe for an existing instance on 127.0.0.1.<port>."""
    try:
        from urllib.request import urlopen
        url = "http://127.0.0.1:%d/api/health" % int(port)
        with urlopen(url, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(code) < 300
    except Exception:
        return False


def _existing_owned_server_running(info=None, pid_alive_fn=None, probe_fn=None):
    """True when runtime.json names ANOTHER live, healthy widget server.

    RC.2 soak finding F1: on Windows, ``ThreadingHTTPServer``'s inherited
    ``allow_reuse_address`` (SO_REUSEADDR) lets a second server silently
    double-bind the port, so the OSError guard in ``main()`` never fires there
    and the first server would be orphaned (its runtime.json clobbered, no
    auth traffic -> idle watchdog never exits). Decide ownership BEFORE
    opening the port. Never raises; always safe to call with no info.
    """
    pid_alive_fn = pid_alive_fn or rl.pid_alive
    probe_fn = probe_fn or _probe_widget_health
    if not isinstance(info, dict):
        return False
    pid = info.get("pid")
    port = info.get("port")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if pid == os.getpid():
        return False
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if port <= 0:
        return False
    try:
        if not pid_alive_fn(pid):
            return False
        return bool(probe_fn(port))
    except Exception:
        return False


def _startup_hygiene():
    """Best-effort cleanup before serving: never raises, never touches others.

    * remove a stray runtime.json.tmp
    * remove stale own cookie-temp copies (see browser_cookie)
    * optional quota_snapshot prune, ONLY when config opts in with a positive
      integer ``quota_snapshot_retention_days`` (disabled by default)
    """
    try:
        tmp = os.path.join(_runtime_dir(), "runtime.json.tmp")
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        pass
    try:
        import browser_cookie
        browser_cookie.cleanup_stale_cookie_temps()
    except Exception:
        pass
    try:
        if ur is None:
            return
        cfg = gw.load_config()
        days = cfg.get("quota_snapshot_retention_days")
        if isinstance(days, int) and not isinstance(days, bool) and days > 0:
            conn = ur.init_remote_db()
            try:
                ur.prune_quota_snapshot(conn, days)
            finally:
                conn.close()
    except Exception:
        pass


def _idle_watchdog_loop():
    """Daemon watchdog: exit cleanly once authenticated traffic goes idle."""
    while True:
        time.sleep(IDLE_WATCHDOG_INTERVAL_S)
        try:
            if rl.should_exit_idle(time.time(), _LAST_AUTH_SEEN, TIMEOUT):
                _cleanup_runtime_info()
                print("[data-server] idle timeout reached; exiting")
                os._exit(0)
        except Exception:
            pass


def _formula_cache_path():
    """Path for the persisted last-known-good formula (Phase 7).

    ``paths.formula_cache_path()`` is provided by a parallel lane. Called
    defensively so the widget still works (and never crashes) without it.
    """
    try:
        import paths
        return paths.formula_cache_path()
    except Exception:
        return os.path.join(APP_DIR, "formula_lkg.json")


_FORMULA_STORE = vw.FormulaStore(url="", cache_path=_formula_cache_path())
_FORMULA_ENGINE = None
_FORMULA_LOCK = threading.Lock()


def _formula_url():
    try:
        cfg = gw.load_config()
        return (cfg.get("formula_url") or "").strip()
    except Exception:
        return ""


def _formula_enabled():
    """公式开关: 仅显式 False 才禁用 (缺省 True)。"""
    try:
        cfg = gw.load_config()
        return cfg.get("formula_enabled", True) is not False
    except Exception:
        return True


def apply_params_to_gw(formula):
    """把云端公式 params 覆盖到 gw 模块级规则表, 使本地行为随云端公式变化。
    缺 key 保持本地默认, 保证降级一致。"""
    if not isinstance(formula, dict):
        return
    p = formula.get("params") or {}
    if not isinstance(p, dict):
        return
    limits = p.get("limits") or {}
    if isinstance(limits, dict):
        if limits.get("session"):
            gw.LIMITS["session"] = float(limits["session"])
        if limits.get("weekly"):
            gw.LIMITS["weekly"] = float(limits["weekly"])
        if limits.get("monthly"):
            gw.LIMITS["monthly"] = float(limits["monthly"])
        if limits.get("credit_per_applied"):
            gw.CREDIT_PER_APPLIED = float(limits["credit_per_applied"])
    windows = p.get("windows") or {}
    if isinstance(windows, dict):
        if windows.get("session_ms"):
            gw.SESSION_MS = int(windows["session_ms"])
        if windows.get("week_ms"):
            gw.WEEK_MS = int(windows["week_ms"])
    providers = p.get("providers") or {}
    if isinstance(providers, dict):
        if isinstance(providers.get("src"), dict):
            gw.PROVIDER_SRC = dict(providers["src"])
        if isinstance(providers.get("prefixes"), list):
            gw.PROVIDER_PREFIXES |= set(providers["prefixes"])
        if isinstance(providers.get("aliases"), dict):
            gw.MODEL_ALIASES = dict(providers["aliases"])
    free = p.get("free_models") or {}
    if isinstance(free, dict):
        if isinstance(free.get("whitelist"), list):
            gw.FREE_WHITELIST = set(free["whitelist"])
        if isinstance(free.get("suffixes"), list):
            gw.FREE_SUFFIXES = tuple(free["suffixes"])
        if isinstance(free.get("exclude"), list):
            gw.FREE_EXCLUDE = set(free["exclude"])
    if isinstance(p.get("prices"), dict):
        gw.PRICES = dict(p["prices"])
    if isinstance(p.get("req_limits"), dict):
        gw.REQ_LIMITS = dict(p["req_limits"])
    if isinstance(p.get("tokens_per_req"), dict):
        gw.TOKENS_PER_REQ = dict(p["tokens_per_req"])
    if isinstance(p.get("display_names"), dict):
        gw.DISPLAY_NAMES = dict(p["display_names"])
    if isinstance(p.get("model_quotas"), dict):
        gw.MODEL_QUOTAS = dict(p["model_quotas"])
    meter = p.get("meter") or {}
    if isinstance(meter, dict):
        if isinstance(meter.get("rates"), dict):
            gw.MODEL_RATES = dict(meter["rates"])
        if meter.get("rate_default") is not None:
            gw.RATE_DEFAULT = float(meter["rate_default"])
        if meter.get("rate_intercept") is not None:
            gw.RATE_INTERCEPT = float(meter["rate_intercept"])


def get_formula(force=False):
    """返回当前公式 (云端/默认), 并同步重建引擎 + 覆盖本地规则表。

    url 或 enabled 变化时重建 FormulaStore (禁用开关走 formula_enabled)。
    """
    global _FORMULA_STORE, _FORMULA_ENGINE
    url = _formula_url()
    enabled = _formula_enabled()
    if _FORMULA_STORE.url != vw.resolve_formula_url(url) or _FORMULA_STORE.enabled != enabled:
        _FORMULA_STORE = vw.FormulaStore(url=url, enabled=enabled,
                                         cache_path=_formula_cache_path())
    f = _FORMULA_STORE.get(force=force)
    apply_params_to_gw(f)
    fr.apply_formulas(f.get("formulas") if isinstance(f, dict) else None)
    with _FORMULA_LOCK:
        _FORMULA_ENGINE = vw.ViewEngine(f, norm=gw.norm_model,
                                        is_free=gw.is_free_model,
                                        local_tz=gw.LOCAL_TZ)
        # 动态抵扣: 抵扣次数 × $5 (账户实际已应用的 credit 数)
        _FORMULA_ENGINE.credit_deduct = gw.deduct_for(_applied_credits())
    return f


FORMULA_SYNC_INTERVAL_S = 900


_FORMULA_LAST_VERSION = None


def formula_sync_loop():
    """后台主动同步云端公式: 周期性拉取云端, 检测版本变化并应用到本地。
    与 auto_sync_loop 不同, 这里同步的是公式规则而非 server_usage.db。
    失败静默保留上一版已生效公式, 等下一轮重试。"""
    global _FORMULA_LAST_VERSION
    while True:
        try:
            f = get_formula(force=True)
            ver = f.get("version")
            if ver != _FORMULA_LAST_VERSION:
                print(f"[formula-sync] version {_FORMULA_LAST_VERSION} -> {ver} (source={_FORMULA_STORE.meta().get('source')})")
                _FORMULA_LAST_VERSION = ver
        except Exception:
            pass
        time.sleep(FORMULA_SYNC_INTERVAL_S)


def run_view(vid, rows):
    get_formula()
    with _FORMULA_LOCK:
        engine = _FORMULA_ENGINE
    return engine.execute(vid, rows)


# Phase 3: the only valid observability range values; anything else -> "all".
OBS_RANGES = ("today", "7d", "30d", "all")


def _observability_context(rng):
    """Single-read context shared by every observability endpoint.

    Performs exactly one ``gw.read_opencode_usage()`` and returns
    ``(meta, rng, filtered_rows, agent_groups)``. ``rng`` is validated here.
    Reuses the ViewEngine cutoff mechanism (no new date algorithm) and filters
    with the local timezone boundary.
    """
    rng = (rng or "all").lower()
    if rng not in OBS_RANGES:
        rng = "all"
    get_formula()
    rows, meta = gw.read_opencode_usage()
    earliest = min((r.get("ts") for r in rows if (r.get("cost") or 0) > 0), default=0)
    period_start = _subscription_start(earliest)
    with _FORMULA_LOCK:
        engine = _FORMULA_ENGINE
        if engine is not None:
            engine.period_start_ms = period_start
    cutoff = engine._cutoff(rng) if engine is not None else None
    filtered = observability.filter_rows(rows, cutoff, gw.LOCAL_TZ)
    try:
        groups = gw.load_config().get("agent_groups") or {}
    except Exception:
        groups = {}
    return meta, rng, filtered, groups


def _timeline_context(rng):
    """Single-read context for ``/api/timeline`` (Phase 5B).

    Performs exactly one ``gw.read_opencode_usage()`` and returns
    ``(meta, rng, filtered_rows, cutoff, earliest_ms)``. It mirrors
    ``_observability_context`` exactly for the range validation, formula
    engine and ``_cutoff`` semantics (no new "today" definition), and adds the
    cutoff string plus the earliest row ts (epoch ms, over all rows) needed to
    plan an "all" window.
    """
    rng = (rng or "all").lower()
    if rng not in OBS_RANGES:
        rng = "all"
    get_formula()
    rows, meta = gw.read_opencode_usage()
    earliest_paid = min((r.get("ts") for r in rows if (r.get("cost") or 0) > 0), default=0)
    period_start = _subscription_start(earliest_paid)
    with _FORMULA_LOCK:
        engine = _FORMULA_ENGINE
        if engine is not None:
            engine.period_start_ms = period_start
    cutoff = engine._cutoff(rng) if engine is not None else None
    filtered = observability.filter_rows(rows, cutoff, gw.LOCAL_TZ)
    earliest_all = min((r.get("ts") for r in rows if r.get("ts") is not None), default=0)
    return meta, rng, filtered, cutoff, earliest_all


def build_state():
    now_ms = int(time.time() * 1000)
    try:
        get_formula()
    except Exception:
        pass
    applied_credits = _applied_credits()
    try:
        rows, cost_map, reader_meta = _collect_rows()
    except Exception:
        rows, cost_map, reader_meta = [], {}, {}
    # 最早付费消费时间: 订阅日合理性校验用 (订阅后才会消费)
    earliest_ms = min((r["ts"] for r in rows if r.get("cost")), default=0)
    period_start = _subscription_start(earliest_ms)
    period_deduct = _period_deduct(period_start)
    # 引擎注入订阅周期: "本期"(30d档) 截止=订阅日, 抵扣仅本期视图
    with _FORMULA_LOCK:
        if _FORMULA_ENGINE is not None:
            _FORMULA_ENGINE.period_start_ms = period_start
            _FORMULA_ENGINE.credit_deduct = period_deduct
    try:
        key = _effective_key()
        key_ok = bool(key)
        models = 0
        all_go_models = []
        if key:
            all_go_models = gw.fetch_go_model_list(key)
            models = len(all_go_models)
    except Exception:
        key_ok = False
        models = 0
        all_go_models = []

    windows = gw.build_windows(rows, now_ms, applied_credits=applied_credits, period_start=period_start)
    _apply_calibration(windows)
    _apply_server_quota(windows, applied_credits)
    # 行级折算率: 前端聚合/明细按模型折算 (v5 口径, 与总进度对账闭合)
    for r in rows:
        r["rate"] = gw.rate_for(gw.norm_model(r.get("model") or ""), r.get("src"))
    # 其他账号(本地估算)金额合计: 进度类排除, 全部视图含
    other_cost = round(sum((r.get("cost") or 0.0) * r.get("rate", 1.0)
                           for r in rows if r.get("account") == "other"), 4)
    stats = gw.model_stats(rows, now_ms, cost_map, all_go_models)
    history = gw.model_history(rows, days=0)
    suppliers = gw.supplier_stats(rows)
    # "全部/全览"= 所有周期累计统计, 不减抵扣 (抵扣仅"本期"视图体现)
    heatmap = gw.heatmap(rows)

    cfg = {}
    try:
        cfg = gw.load_config()
    except Exception:
        pass
    cal = {k: v for k, v in (cfg.get("calibration") or {}).items() if v}
    srv_cfg = cfg.get("server") or {}
    srv = _latest_server_quota() or None

    # ---- 用注册表补充展示类数值指标 ----
    try:
        quota_limit = gw.limit_for("monthly") or 60.0
        global_limit = quota_limit
        used_all = sum((x.get("cost_total") or 0.0) for x in stats)
        # 全局剩余: 官方进度口径(折算已用−抵扣); 有官方窗口时直接用 monthly 窗口剩余
        mwin = next((w for w in windows if w.get("kind") == "monthly"), None)
        if mwin is not None:
            global_remain = max(0.0, mwin.get("limit", global_limit) - mwin.get("used", 0.0))
        else:
            used_all_rated = sum((x.get("cost_total") or 0.0) * (x.get("meter_rate") or 1.0)
                                 for x in stats if x.get("source") == "go")
            global_remain = max(0.0, global_limit - used_all_rated)
        weeks_per_month = float((_FORMULA_STORE.get({}).get("constants", {}) or {}).get("weeks_per_month", 4.345))
        periods_per_day = float((_FORMULA_STORE.get({}).get("constants", {}) or {}).get("periods_per_day", 6.0))

        for x in stats:
            mq = x.get("model_quota") or 0.0
            mu = x.get("model_used") or 0.0
            # 折算口径: used×meter_rate, 剩余 = 额度 − 折算已用 (与进度/全部口径一致)
            rate = x.get("meter_rate") or 1.0
            mu_rated = mu * rate
            mr = max(0.0, mq - mu_rated)
            x["model_used_rated"] = round(mu_rated, 4)
            x["model_remain"] = fr.compute("model_remain", model_quota=mq, model_used=mu_rated) or mr

            cq_w = fr.compute("cq_weekly", model_quota=mq, weeks_per_month=weeks_per_month)
            cq_s = fr.compute("cq_session", model_quota=mq, periods_per_day=periods_per_day)
            x["cq_weekly"] = cq_w
            x["cq_session"] = cq_s

            monthly_tok = (x.get("tokens_in_m") or 0) + (x.get("tokens_out_m") or 0) + (x.get("tokens_cache_m") or 0)
            weekly_tok = (x.get("tokens_in_w") or 0) + (x.get("tokens_out_w") or 0) + (x.get("tokens_cache_w") or 0)
            session_tok = (x.get("tokens_in_s") or 0) + (x.get("tokens_out_s") or 0) + (x.get("tokens_cache_s") or 0)

            monthly_cost = x.get("cost_m") or 0.0
            if monthly_cost > 0 and monthly_tok > 0:
                avg_monthly = monthly_cost / monthly_tok
                weekly_cost = x.get("cost_w") or 0.0
                avg_weekly = weekly_cost / weekly_tok if weekly_tok > 0 else 0.0
                session_cost = x.get("cost_s") or 0.0
                avg_session = session_cost / session_tok if session_tok > 0 else 0.0
            else:
                avg_monthly = avg_weekly = avg_session = 0.0

            x["effective_remain"] = fr.compute("effective_remain", model_remain=mr, global_remain=global_remain) or min(mr, global_remain)
            # token 配额反推: 以"实际剩余费用"(全局约束后)为准, 不用模型额度——额度用完后剩余=0
            er = x.get("effective_remain") or 0.0
            x["tq_monthly"] = fr.compute("tq_monthly", cq_monthly=er, avg_monthly_cost_per_token=avg_monthly)
            x["tq_weekly"] = fr.compute("tq_weekly", cq_weekly=min(cq_w, er), avg_weekly_cost_per_token=avg_weekly)
            x["tq_session"] = fr.compute("tq_session", cq_session=min(cq_s, er), avg_session_cost_per_token=avg_session)
            x["tp_monthly"] = fr.compute("tp_monthly", tok_monthly=monthly_tok, tq_monthly=x.get("tq_monthly") or 1)
            x["tp_weekly"] = fr.compute("tp_weekly", tok_weekly=weekly_tok, tq_weekly=x.get("tq_weekly") or 1)
            x["tp_session"] = fr.compute("tp_session", tok_session=session_tok, tq_session=x.get("tq_session") or 1)

            x["avg_per_req"] = fr.compute("avg_per_req", cost_of_period=x.get("cost_m") or 0.0, used_count=x.get("count_m") or 0, est_req_cost=x.get("est_req_cost") or 0.0)
            x["remain_cnt"] = fr.compute("remain_cnt", effective_remain=x.get("effective_remain") or 0.0, avg_per_req=x.get("avg_per_req") or 0.0)
            x["cache_hit"] = fr.compute("cache_hit", cache_read=x.get("cache_read") or 0, tokens_in=x.get("tokens_in") or 0)
            x["rate"] = fr.compute("rate", tok_sec_sum=x.get("tok_sec_sum") or 0.0, tok_sec_n=x.get("tok_sec_n") or 0)
    except Exception:
        pass

    return {
        "windows": windows,
        "stats": stats,
        "history": history,
        "suppliers": suppliers,
        "heatmap": heatmap,
        "rows": len(rows),
        "key": key_ok,
        "models": models,
        "calibration": cal if cal else None,
        "server": srv,
        "credits": applied_credits,
        "credits_dollars": round(applied_credits * gw.CREDIT_PER_APPLIED, 2),
        "period_start": datetime.datetime.fromtimestamp(period_start / 1000).strftime("%Y-%m-%d") if period_start else "",
        "period_deduct": round(period_deduct, 2),
        "subscription_source": _SUB_START.get("source", "none"),
        "subscription_fetched_at": _SUB_START.get("fetched_at", 0),
        "other_cost": other_cost,
        "server_error": None,
        "server_configured": bool(srv_cfg.get("auth_cookie")) or bool(srv),
        "reader": reader_meta,
        "ts": now_ms,
    }


def _effective_key():
    try:
        cfg = gw.load_config()
        k = (cfg.get("api_key") or "").strip()
        if k:
            return k
    except Exception:
        pass
    return gw.discover_go_key() or ""


def _applied_credits():
    """已应用的 referral credit 数：优先 usage_remote.db sync_meta，fallback 0。"""
    if ur is not None:
        try:
            return ur.read_remote_credits()
        except Exception:
            return 0
    return 0


_SUB_START = {"ms": None, "ts": 0, "last_day": "", "source": "none", "fetched_at": 0}
# 到期窗口: 距上次订阅日 ≥ 30 天后, 每天强制抓取 1 次直到抓到新订阅记录
_SUB_DAILY_WINDOW_S = 30 * 24 * 3600
_SUB_TTL_OK_S = 6 * 3600
_SUB_TTL_FAIL_S = 15 * 60


def _subscription_start(earliest_ms=0):
    """当前订阅周期起始(ms)。调度策略:
    - 正常期(距订阅日 < 30天): 成功抓取缓存 6h, 失败 15min 重试
    - 到期窗口(≥ 30天): 每天强制抓 1 次(当天去重), 直到抓到新订阅记录
    - 抓到新值→重置计时; 抓取失败→config 兜底; 合理性校验: 订阅日不晚于今天、
      且不晚于最早消费日(订阅后才会消费)"""
    global _SUB_START
    now = time.time()
    if _SUB_START["ms"] is None:
        _SUB_START["ms"] = gw.subscription_start_ms()
        if _SUB_START["ms"]:
            _SUB_START["source"] = "config"
    cur = _SUB_START["ms"]
    in_daily = bool(cur) and (now - cur / 1000.0) >= _SUB_DAILY_WINDOW_S
    today = datetime.date.today().isoformat()
    if cur is not None:
        if in_daily:
            if _SUB_START["last_day"] == today and _SUB_START["source"] == "auto":
                return _SUB_START["ms"]
        else:
            ttl = _SUB_TTL_OK_S if _SUB_START["source"] == "auto" else _SUB_TTL_FAIL_S
            if now - _SUB_START["ts"] < ttl:
                return _SUB_START["ms"]
    # 抓取最新订阅付款记录
    ms = None
    if ur is not None:
        try:
            cfg = gw.load_config()
            srv = cfg.get("server") or {}
            ms = ur.fetch_subscription_start(srv.get("auth_cookie") or "", srv.get("workspace_id") or "")
        except Exception:
            ms = None
    if ms:
        ok = ms <= int(now * 1000)
        # 订阅日不应晚于最早消费日; 容忍同日先后误差(付款/记录时区偏差)±1天
        if earliest_ms and ms > earliest_ms + 24 * 3600 * 1000:
            ok = False
        if ok:
            _SUB_START["ms"] = ms
            _SUB_START["ts"] = now
            _SUB_START["last_day"] = today
            _SUB_START["source"] = "auto"
            _SUB_START["fetched_at"] = int(now * 1000)
            print("[subscription] auto: %s (source=billing)" %
                  datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d"))
            return _SUB_START["ms"]
    # 抓取失败: 回退 config; 到期窗口内当天不再重试(明天再抓)
    fb = gw.subscription_start_ms()
    _SUB_START["ms"] = fb if fb else _SUB_START["ms"]
    _SUB_START["ts"] = now
    _SUB_START["last_day"] = today
    _SUB_START["source"] = "config" if fb else "none"
    if not fb:
        _SUB_START["source"] = "none" if not _SUB_START["ms"] else "auto_stale"
    print("[subscription] fallback: source=%s (fetch failed)" % _SUB_START["source"])
    return _SUB_START["ms"]


def _period_deduct(start_ms):
    """本期抵扣总额 = 本期抵扣次数 × $5; 无抵扣表时回退 applied_credits。"""
    cnt = gw.period_deduct_count(start_ms)
    if not cnt:
        cnt = _applied_credits()
    return gw.deduct_for(cnt)


def _apply_calibration(windows):
    try:
        cfg = gw.load_config()
        cal = cfg.get("calibration") or {}
    except Exception:
        return
    for w, key in zip(windows, ["session", "weekly", "monthly"]):
        pct = cal.get(key)
        if not pct or pct <= 0:
            continue
        limit = gw.limit_for(key)
        if not limit:
            continue
        target = limit * pct / 100.0
        if target > w["used"]:
            w["used"] = target
            w["pct"] = min(100.0, target / limit * 100)
        w["calibrated"] = True


def _latest_server_quota():
    """从 quota_snapshot 读最新一次抓取的滚动窗口百分比（官网实时值）。
    优先读 usage_remote.db，fallback 到 server_usage.db。"""
    db = None
    if ur is not None:
        db = ur.REMOTE_DB
    if not db or not os.path.exists(db):
        db = (sd.DB_PATH if sd else os.path.join(APP_DIR, "server_usage.db"))
    if not os.path.exists(db):
        return []
    try:
        conn = sqlite3.connect(db)
        rows = conn.execute("""
            SELECT kind, label, pct, reset_text, fetched_at
            FROM quota_snapshot
            WHERE fetched_at = (SELECT MAX(fetched_at) FROM quota_snapshot)
            ORDER BY CASE kind WHEN 'session' THEN 0 WHEN 'weekly' THEN 1 ELSE 2 END
        """).fetchall()
        conn.close()
        return [{"kind": r[0], "label": r[1], "pct": r[2], "reset_text": r[3],
                 "fetched_at": r[4]} for r in rows]
    except Exception:
        return []


def _apply_server_quota(windows, applied_credits=0):
    """用官网 quota 覆盖本地百分比。官方 pct 分母恒为基础额度, 直接反推 used 并同步 limit/pct。"""
    srv = _latest_server_quota()
    if not srv:
        return
    srv_map = {w["kind"]: w for w in srv}
    for w in windows:
        sw = srv_map.get(w["kind"])
        if not sw:
            continue
        base = gw.limit_for(w["kind"])          # 基础限额 12/30/60 (分母恒定, credits 不扩容)
        used = base * sw["pct"] / 100.0         # 官网 pct 反推用量
        pct = sw["pct"]                          # 直接采用官网 pct
        w["used"] = used
        w["limit"] = base
        w["pct"] = pct
        w["reset"] = sw["reset_text"]
        w["calibrated"] = True


# ---- Phase 4: /api/forecast ----
# 本地窗口估算兜底: 官方 reset_text 无法解析时才使用, 并显式标注来源。
_FORECAST_LOCAL_WINDOW_MS = 30 * 24 * 3600 * 1000


def _local_reset_estimate(kind, now_ms, period_start):
    if kind == "session":
        return now_ms + gw.SESSION_MS
    if kind == "weekly":
        return gw.week_bounds(now_ms)[1]
    if period_start:
        return gw.month_bounds(now_ms, period_start)[1]
    return now_ms + _FORECAST_LOCAL_WINDOW_MS


def _forecast_official(srv_rows, kind, now_ms, period_start):
    """官方 quota 状态 (used/remaining/limit 的第一事实来源)。

    有 snapshot: used = limit * pct/100; reset 优先官方 reset_text, 无法解析时
    回退本地窗口估算并标注 reset_source="local_window_estimate"。
    无 snapshot: status="unavailable", used/remaining 为 None (绝不臆造)。
    """
    limit = gw.limit_for(kind)
    local_reset = _local_reset_estimate(kind, now_ms, period_start)
    snap = next((r for r in (srv_rows or []) if r.get("kind") == kind), None)
    if not snap:
        return {"status": "unavailable", "used": None, "remaining": None,
                "limit": limit, "reset_at": local_reset,
                "reset_source": "local_window_estimate"}
    try:
        pct = float(snap.get("pct") or 0.0)
    except Exception:
        pct = 0.0
    used = limit * pct / 100.0
    remaining = max(0.0, limit - used)
    reset_at = None
    reset_source = "local_window_estimate"
    offset = forecasting.parse_reset_text(snap.get("reset_text"))
    fetched_at = snap.get("fetched_at")
    if offset is not None and fetched_at:
        reset_at = int(fetched_at) + offset
        reset_source = "official_reset_text"
    if reset_at is None:
        reset_at = local_reset
    return {"status": "ok", "used": used, "remaining": remaining,
            "limit": limit, "reset_at": reset_at, "reset_source": reset_source}


def build_forecast_state():
    """单次 DB 读取 + 官方 quota 第一事实来源 -> 预测响应 (加 reader meta)。"""
    get_formula()
    rows, meta = gw.read_opencode_usage()
    now_ms = int(time.time() * 1000)
    # 折算口径与 gw.build_windows 完全一致 (cost>0, 非 go 子集镜像, 非其他账号)
    records = []
    for r in rows:
        cost = r.get("cost") or 0.0
        src = r.get("src")
        if cost > 0 and src not in gw.SUBSET_SRCS and r.get("account") != "other":
            usage = cost * gw.rate_for(gw.norm_model(r.get("model") or ""), src)
            records.append({"ts": int(r.get("ts") or 0), "usage": usage})
    earliest = min((r.get("ts") for r in rows if (r.get("cost") or 0) > 0), default=0)
    period_start = _subscription_start(earliest)
    srv_rows = _latest_server_quota()
    result = forecasting.build_forecast(
        records, now_ms,
        _forecast_official(srv_rows, "session", now_ms, period_start),
        _forecast_official(srv_rows, "weekly", now_ms, period_start),
        _forecast_official(srv_rows, "monthly", now_ms, period_start),
        period_start or None,
    )
    result["reader"] = meta
    return result


# 本地-官方逐条匹配窗口(ms): 自己账号的记录应与官方同模型记录时间接近
_OWN_MATCH_MS = 120000


def _split_own_other(local_rows, remote_rows):
    """分账: 本地记录与官方逐条匹配(±120s, 一条官方最多配一条本地)。
    命中 → 自己账号(丢弃, 官方为准); 未命中 → 其他账号(保留, account="other")。
    官方索引为空(未登录)时全部本地行归 other。"""
    if not remote_rows:
        for r in local_rows:
            r["account"] = "other"
        return local_rows
    off_by_model = {}
    for r in remote_rows:
        off_by_model.setdefault(r["model"], []).append(r["ts"])
    for m in off_by_model:
        off_by_model[m].sort()
    used = {}   # model -> set(已消费官方索引)
    extra = []
    for r in local_rows:
        lst = off_by_model.get(r["model"])
        found = False
        if lst:
            used_set = used.setdefault(r["model"], set())
            i = bisect.bisect_left(lst, r["ts"])
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(lst) and j not in used_set and abs(lst[j] - r["ts"]) <= _OWN_MATCH_MS:
                    used_set.add(j)
                    found = True
                    break
        if not found:
            r["account"] = "other"
            extra.append(r)
    return extra


def _collect_rows():
    remote_rows = []
    cost_map = {}
    if ur is not None:
        try:
            remote_rows = ur.read_remote_rows()
            cost_map = ur.read_remote_cost_map()
        except Exception:
            remote_rows = []
            cost_map = {}
    reader_meta = {}
    if remote_rows:
        local_rows, reader_meta = gw.read_opencode_usage()
        extra = _split_own_other(local_rows, remote_rows)
        return remote_rows + extra, cost_map, reader_meta

    srv_rows = []
    if sd is not None:
        try:
            srv_rows = sd.read_server_rows()
        except Exception:
            srv_rows = []
    if srv_rows:
        cost_map = {}
        if sd is not None:
            try:
                cost_map = sd.read_cost_map()
            except Exception:
                pass
        local_rows, reader_meta = gw.read_opencode_usage()
        extra = _split_own_other(local_rows, srv_rows)
        return srv_rows + extra, cost_map, reader_meta
    go_rows, reader_meta = gw.read_opencode_usage()
    try:
        cfg = gw.load_config()
        last_id = cfg.get("codex_log_id") or 0
    except Exception:
        last_id = 0
    cx_rows, new_id = gw.read_codex_logs(last_id)
    if new_id > last_id:
        try:
            cfg = gw.load_config()
            cfg["codex_log_id"] = new_id
            gw.save_config(cfg)
        except Exception:
            pass
    # 未登录/无官方数据: 全本地, 全归 other
    for r in go_rows:
        r["account"] = "other"
    return go_rows + cx_rows, {}, reader_meta


def _scrape_official_windows(cookie, ws):
    """官方配额窗口: 优先新版控制台 go/status, 回退旧 SSR /workspace/{ws}/go。

    新版 (SPA) 账号通常只有 __Host-console_session、没有 auth cookie，
    旧 SSR 路径会 302 到登录页，因此优先新接口。
    """
    try:
        r = gw.fetch_go_status(cookie, ws)
        if r.get("ok"):
            return r
    except Exception:
        pass
    return gw.scrape_server_usage(cookie, ws)


def do_sync():
    try:
        cfg = gw.load_config()
    except Exception:
        cfg = {}
    srv = cfg.get("server") or {}
    cookie = srv.get("auth_cookie") or ""
    ws = srv.get("workspace_id") or ""
    if not cookie or not ws:
        return {"ok": False, "error": "缺少 auth cookie 或 workspace ID"}
    try:
        res = _scrape_official_windows(cookie, ws)
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {e}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "抓取失败")}
    # 官方配额落库与 usage/cost 同步解耦: 新版账号可能没有 auth cookie,
    # full_sync 的 RPC 会失败, 但配额快照必须写入 (供 _apply_server_quota 使用)。
    quota_n = 0
    if ur is not None and res.get("windows"):
        try:
            conn = ur.init_remote_db()
            try:
                quota_n = ur.sync_quota_snapshot(conn, res["windows"], ws)
            finally:
                conn.close()
        except Exception:
            quota_n = 0
    if ur is not None:
        try:
            r = ur.full_sync(cookie, ws, windows=res.get("windows"),
                             applied_credits=res.get("applied_credits"))
        except Exception as e:
            r = {"ok": False, "error": str(e)}
    else:
        r = {"ok": True}
    with CACHE["lock"]:
        CACHE["state"] = None
        CACHE["ts"] = 0
    return {"ok": True, "windows": res.get("windows"),
            "quota_snapshot": quota_n, "remote": r}


def _config_json():
    try:
        cfg = gw.load_config()
    except Exception:
        cfg = {}
    srv = cfg.get("server") or {}
    # Phase 2.1: non-sensitive secret-storage status (backend + status only,
    # never secret values / ciphertext / entropy).
    try:
        secret_storage = gw.secret_storage_status()
    except Exception:
        secret_storage = {"backend": "none", "status": "unavailable"}
    # Phase 2A: never expose api_key / auth_cookie. workspace_id is kept on purpose:
    # it is a non-secret identifier needed to prefill the manual server-config UI.
    return {
        "api_key_configured": bool((cfg.get("api_key") or "").strip()),
        "auth_configured": bool((srv.get("auth_cookie") or "").strip()),
        "workspace_configured": bool((srv.get("workspace_id") or "").strip()),
        "workspace_id": (srv.get("workspace_id") or "").strip(),
        "calibration": cfg.get("calibration") or {},
        "secret_storage": secret_storage,
    }


def do_grab():
    """尽力从本机浏览器 cookie 库抓取 auth。现代 Chrome app-bound 加密下返回空。"""
    try:
        cookie = gw.read_auth_cookie_from_webdata()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not cookie:
        return {"ok": False, "error": "未找到可解密的有效 cookie（现代 Chrome 使用 app-bound 加密，请用应用内登录窗口抓取）"}
    try:
        cfg = gw.load_config()
        srv = cfg.get("server") or {}
        ws = srv.get("workspace_id") or ""
        if not ws:
            # 尝试通过 /auth 重定向自动发现 workspace
            from urllib.request import Request, urlopen
            req = Request("https://opencode.ai/auth", headers={
                "User-Agent": "Mozilla/5.0",
                "Cookie": "auth=" + cookie,
            })
            try:
                urlopen(req, timeout=10)
            except Exception as e:
                loc = None
                if isinstance(e, Exception) and hasattr(e, "headers"):
                    loc = e.headers.get("Location") or ""
                m = __import__("re").search(r"/workspace/(wrk_[A-Za-z0-9]+)", str(loc))
                if m:
                    ws = m.group(1)
            if not ws:
                ws = srv.get("workspace_id") or ""
    except Exception:
        ws = ""
    if ws:
        try:
            cfg = gw.load_config()
            cfg["server"] = {"auth_cookie": cookie, "workspace_id": ws}
            gw.save_config(cfg)
        except Exception:
            # Phase 2.1: fail closed — the cookie was not persisted anywhere.
            return {"ok": False, "error": "保存失败：安全存储写入失败"}
        return {"ok": True, "auth_configured": True, "workspace_configured": True}
    # Phase 2.1: no workspace discovered -> nothing could be saved; report
    # truthfully instead of claiming the auth was configured.
    return {"ok": True, "auth_configured": False, "workspace_configured": False}


def _send_json(self, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    self.send_response(code)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self._send_cors_headers()
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # ---- Phase 2A: Host / Origin / token guards ----
    def _bound_port(self):
        return self.server.server_address[1]

    def _host_ok(self):
        """Host must be 127.0.0.1|localhost with the actual bound port."""
        host = self.headers.get("Host") or ""
        hostname, sep, port_s = host.rpartition(":")
        if not sep:
            return False
        hostname = hostname.strip().lower()
        try:
            port = int(port_s)
        except Exception:
            return False
        return hostname in ("127.0.0.1", "localhost") and port == self._bound_port()

    def _origin_allowed(self):
        """None -> no Origin; str -> allowed origin to echo; False -> forbidden."""
        origin = self.headers.get("Origin")
        if origin is None:
            return None
        if origin == "null":
            return "null"
        port = self._bound_port()
        if origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return origin
        return False

    def _auth_ok(self):
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return False
        provided = header[len("Bearer "):].strip()
        if hmac.compare_digest(provided, ensure_runtime_token()):
            # Phase 6A: any successful authenticated request counts as heartbeat.
            global _LAST_AUTH_SEEN
            _LAST_AUTH_SEEN = time.time()
            return True
        return False

    def _guard(self, require_auth=True):
        """Return True when the request may proceed; else send the 403/401 JSON."""
        if not self._host_ok():
            _send_json(self, {"ok": False, "error": "forbidden_host"}, 403)
            return False
        if self._origin_allowed() is False:
            _send_json(self, {"ok": False, "error": "forbidden_origin"}, 403)
            return False
        if require_auth and not self._auth_ok():
            _send_json(self, {"ok": False, "error": "unauthorized"}, 401)
            return False
        return True

    def _send_cors_headers(self):
        """Echo an allowlisted Origin (never '*'); omit ACAO when absent/forbidden."""
        origin = self._origin_allowed()
        if isinstance(origin, str):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")

    def _send_health(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self._send_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            # Phase 2A: health is anonymous but still Host/CORS guarded.
            if not self._guard(require_auth=False):
                return
            self._send_health()
            return
        if not self._guard():
            return
        if path == "/api/state":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            # Phase 6A: lightweight authenticated heartbeat (no state rebuild).
            if (q.get("heartbeat", [""])[0] or "") == "1":
                _send_json(self, {"ok": True, "ts": int(time.time() * 1000)})
                return
            now = time.time()
            with CACHE["lock"]:
                if CACHE["state"] is None or now - CACHE["ts"] > 30:
                    try:
                        CACHE["state"] = build_state()
                        CACHE["ts"] = now
                    except Exception as e:
                        CACHE["state"] = {"error": str(e)}
                        CACHE["ts"] = now
                body = json.dumps(CACHE["state"], ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/config":
            _send_json(self, _config_json())
        elif path == "/api/formula":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            force = "refresh" in q or "force" in q
            f = get_formula(force=force)
            meta = _FORMULA_STORE.meta()
            _send_json(self, {
                "version": f.get("version"),
                "source": meta["source"],
                "url": meta["url"],
                "error": meta["error"],
                "fetched_at": meta["fetched_at"],
                # Phase 2A: provenance fields
                "enabled": meta.get("enabled", True),
                "hash": meta.get("hash"),
                "last_updated": meta.get("last_updated", meta.get("fetched_at")),
                "fallback": meta.get("fallback", False),
                # Phase 7: trust-boundary provenance (additive keys)
                "schema_version": meta.get("schema_version"),
                "formula_version": meta.get("formula_version", f.get("version")),
                "loaded_at": meta.get("loaded_at", 0),
                "validation_status": meta.get("validation_status"),
                "integrity_status": meta.get("integrity_status", "unsigned"),
                "params": f.get("params"),
                "views": f.get("views"),
                "constants": f.get("constants"),
                "formulas": fr.FORMULA_TABLE(),
            })
        elif path == "/api/views":
            f = get_formula()
            _send_json(self, {
                "version": f.get("version"),
                "views": [{k: v.get(k) for k in ("id", "label", "scope", "range", "group")}
                          for v in (f.get("views") or [])],
            })
        elif path.startswith("/api/view/"):
            vid = urllib.parse.unquote(path[len("/api/view/"):])
            try:
                rows, _, _ = _collect_rows()
                _send_json(self, run_view(vid, rows))
            except vw.FormulaError as e:
                _send_json(self, {"ok": False, "error": str(e)}, 404)
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/agents":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            try:
                rng = (q.get("range", ["all"])[0] or "all").lower()
                meta, rng, filtered, groups = _observability_context(rng)
                _send_json(self, {
                    "reader": meta,
                    "range": rng,
                    # cost basis: raw OpenCode message cost (opencode_message_raw).
                    # NOT official OpenCode Go quota consumption: no meter ratio,
                    # no credits, no subscription deduction. Model breakdown below
                    # uses the exact same raw basis.
                    "cost_basis": observability.COST_BASIS,
                    "agents": observability.aggregate_agents(filtered, groups),
                })
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/models":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            try:
                rng = (q.get("range", ["all"])[0] or "all").lower()
                meta, rng, filtered, groups = _observability_context(rng)
                _send_json(self, {
                    "reader": meta,
                    "range": rng,
                    "cost_basis": observability.COST_BASIS,
                    "models": observability.aggregate_models(filtered, groups),
                })
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/providers":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            try:
                rng = (q.get("range", ["all"])[0] or "all").lower()
                meta, rng, filtered, groups = _observability_context(rng)
                _send_json(self, {
                    "reader": meta,
                    "range": rng,
                    "cost_basis": observability.COST_BASIS,
                    "providers": observability.aggregate_providers(
                        filtered, groups, gw.SUPPLIER_NAMES),
                })
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/sessions":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            try:
                rng = (q.get("range", ["all"])[0] or "all").lower()
                meta, rng, filtered, groups = _observability_context(rng)
                _send_json(self, {
                    "reader": meta,
                    "range": rng,
                    "cost_basis": observability.COST_BASIS,
                    "sessions": observability.aggregate_sessions(filtered, groups),
                    "tree": observability.build_session_tree(filtered),
                })
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/timeline":
            q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            try:
                rng = (q.get("range", ["all"])[0] or "all").lower()
                meta, rng, filtered, cutoff, earliest_ms = _timeline_context(rng)
                now_ms = int(time.time() * 1000)
                plan, _buckets, series = timeline.build_timeline(
                    filtered, rng, cutoff, now_ms, earliest_ms, gw.LOCAL_TZ)
                _send_json(self, {
                    "reader": meta,
                    "range": rng,
                    "cost_basis": timeline.COST_BASIS,
                    "bucket": {
                        "unit": plan["unit"],
                        "seconds": plan["size_ms"] // 1000,
                    },
                    "series": series,
                })
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        elif path == "/api/forecast":
            # Phase 4: deterministic estimate; no range param. Official quota
            # state is the first fact source (never re-derived from raw cost).
            try:
                _send_json(self, build_forecast_state())
            except Exception as e:
                _send_json(self, {"ok": False, "error": repr(e)}, 500)
        else:
            _send_json(self, {"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        data = self._read_body()
        try:
            cfg = gw.load_config()
        except Exception:
            cfg = {}
        if path == "/api/key":
            cfg["api_key"] = (data.get("key") or "").strip()
            try:
                gw.save_config(cfg)
            except Exception:
                # Phase 2.1: fail closed; surface the error instead of dying.
                _send_json(self, {"ok": False, "error": "保存失败：安全存储写入失败"})
                return
            _send_json(self, {"ok": True})
        elif path == "/api/server":
            cfg["server"] = {
                "auth_cookie": (data.get("auth_cookie") or "").strip(),
                "workspace_id": (data.get("workspace_id") or "").strip(),
            }
            try:
                gw.save_config(cfg)
            except Exception:
                # Phase 2.1: fail closed; surface the error instead of dying.
                _send_json(self, {"ok": False, "error": "保存失败：安全存储写入失败"})
                return
            _send_json(self, {"ok": True})
        elif path == "/api/calibrate":
            cal = {}
            for k in ("session", "weekly", "monthly"):
                v = data.get(k)
                if v is not None:
                    try:
                        fv = float(v)
                        if fv > 0:
                            cal[k] = min(100.0, fv)
                    except Exception:
                        pass
            cfg["calibration"] = cal
            try:
                gw.save_config(cfg)
            except Exception:
                # Phase 2.1: fail closed; surface the error instead of dying.
                _send_json(self, {"ok": False, "error": "保存失败：安全存储写入失败"})
                return
            _send_json(self, {"ok": True})
        elif path == "/api/sync":
            _send_json(self, do_sync())
        elif path == "/api/grab":
            _send_json(self, do_grab())
        else:
            _send_json(self, {"ok": False, "error": "not found"}, 404)

    def do_OPTIONS(self):
        # Phase 2A: preflight needs no token; 204 with CORS headers when the
        # Origin is allowlisted, 403 otherwise. Never ACAO "*".
        if not self._host_ok():
            _send_json(self, {"ok": False, "error": "forbidden_host"}, 403)
            return
        if self._origin_allowed() is False:
            _send_json(self, {"ok": False, "error": "forbidden_origin"}, 403)
            return
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()


def preheat():
    """启动时后台预热：提前算好 state 并写入缓存，前端首请求立即命中。"""
    try:
        with CACHE["lock"]:
            CACHE["state"] = build_state()
            CACHE["ts"] = time.time()
    except Exception:
        pass
    try:
        get_formula()
    except Exception:
        pass


AUTO_SYNC_INTERVAL_S = 1800


def auto_sync_loop():
    """后台定期同步远程 server_usage.db（启动一次 + 每 30 分钟）。
    server 数据是权威 go/zen 源，但会滞后于本地 opencode.db；
    不自动同步会导致 widget 显示旧的用量。失败静默，等下一轮。"""
    while True:
        try:
            do_sync()
        except Exception:
            pass
        time.sleep(AUTO_SYNC_INTERVAL_S)


def main():
    atexit.register(_cleanup_runtime_info)
    _startup_hygiene()
    # F1 (RC.2 soak): on Windows the OSError guard below never fires —
    # SO_REUSEADDR silently double-binds. Yield to a live, healthy owner
    # named by runtime.json BEFORE touching the port.
    if _existing_owned_server_running(info=_read_runtime_info()):
        print("[data-server] widget already running; exiting")
        return
    threading.Thread(target=preheat, daemon=True).start()
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    threading.Thread(target=formula_sync_loop, daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # Phase 6A: distinguish "our own instance is already running" from a
        # foreign program squatting on the port. Never kill the other process.
        info = _read_runtime_info()
        probe_ok = _probe_widget_health(PORT)
        if rl.classify_port_conflict(info, probe_ok) == "existing_widget":
            print("[data-server] widget already running; exiting")
            return
        sys.stderr.write(
            "[data-server] port %d is occupied by another program; exiting\n" % PORT)
        sys.exit(1)
    actual_port = srv.server_address[1]
    _write_runtime_info(actual_port)
    if TIMEOUT > 0:
        threading.Thread(target=_idle_watchdog_loop, daemon=True).start()
    print(f"[data-server] listening on http://127.0.0.1:{actual_port}/api/state")
    try:
        srv.serve_forever()
    finally:
        _cleanup_runtime_info()


if __name__ == "__main__":
    main()
