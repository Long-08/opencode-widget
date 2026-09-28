#!/usr/bin/env python3
"""views.py — 云端公式引擎 (POC)

核心想法:
  - 云端 (Cloudflare Worker + KV) 只提供一份「公式」: params(口径/限额/来源) + views(命名查询定义)。
  - 本地数据不动, 本地解释器按公式对 rows 执行过滤/切窗/分组/求和/后处理。
  - 改云端公式 → 所有用户本地展示随之变化, 无需 git pull。

公式来源 (FormulaStore):
  1. FORMULA_URL (env 或 config["formula_url"]) 指向云端/本地 mock, 定时拉取 + 失败回退内置默认;
  2. 无 URL → 内置默认公式 (DEFAULT_FORMULA), source="default"。

范围语义 (与当前 UI 完全一致):
  - 日期分桶: 本地时区 %Y-%m-%d
  - 截止日期 (字符串比较): today/1 = UTC 今天, 7d/7 = UTC 今天-6, 30d/30 = UTC 今天-29, all = 无过滤
  - tokens = input + output + cache.read + cache.write
  - post.meterRatio: 只作用于 totals.cost (daily 保持原始), 当 scope 为 all 或 source 在 params.sources.paid 时 ×params.meter.ratio
"""
import json
import hashlib
import math
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

DEFAULT_FORMULA = {
    "version": 4,
    "source_note": "内置默认公式 (离线回退, 与云端 v4 一致)",
    "params": {
        "meter": {
            "ratio": 1.4212,
            "note": "计量器≈账单×1.42（≤限额）",
        },
        "limits": {
            "session": 12.0,
            "weekly": 30.0,
            "monthly": 60.0,
            "credit_per_applied": 5.0,
        },
        "windows": {
            "session_ms": 18000000,
            "week_ms": 604800000,
        },
        "sources": {
            "paid": ["go"],
            "subset_of": {"gateway": "go"},
            "label": {"go": "OpenCode Go", "gateway": "AI Gateway",
                      "zen": "OpenCode Zen", "kilo": "Kilo", "router": "OpenRouter"},
            "order": ["go", "zen", "kilo", "router", "gateway"],
        },
        "free_models": {
            "whitelist": ["big-pickle"],
            "suffixes": ["-free", ":free", "/free"],
            "exclude": ["openrouter/free", "kilo-auto/free", "openrouter-free"],
        },
        "providers": {
            "src": {
                "opencode": "zen",
                "opencode-go": "go",
                "openkilo": "kilo",
                "tencent-tokenhub": "zen",
                "openrouter": "router",
            },
            "prefixes": [
                "tencent", "cohere", "nvidia", "google", "inclusionai",
                "opencode", "openkilo", "openrouter", "poolside", "stepfun",
                "openai", "tencent-tokenhub", "opencode-go",
            ],
            "aliases": {
                "nemotron-3-ultra-free": "nemotron-3-ultra-550b-a55b-free",
                "nemotron-3-nano-omni-30b-a3b-reasoning-free": "nemotron-3-nano-omni-30b-a3b-reasoning-free",
            },
        },
        "prices": {
            "grok-4.5": {"in": 2.00, "out": 6.00, "cr": 0.30, "cw": None},
            "gpt-5.6-luna": {"in": 0.20, "out": 1.20, "cr": 0.02, "cw": 0.25, "in_hi": 0.40, "out_hi": 1.80, "cr_hi": 0.04, "cw_hi": 0.50, "hi_above": 272000},
            "glm-5.2": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None},
            "glm-5.1": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None},
            "kimi-k3": {"in": 3.00, "out": 15.00, "cr": 0.30, "cw": None},
            "kimi-k2.7-code": {"in": 0.95, "out": 4.00, "cr": 0.19, "cw": None},
            "kimi-k2.6": {"in": 0.95, "out": 4.00, "cr": 0.16, "cw": None},
            "mimo-v2.5": {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None},
            "mimo-v2.5-pro": {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None},
            "minimax-m3": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": None},
            "minimax-m2.7": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": 0.375},
            "qwen3.8-max": {"in": 2.00, "out": 6.00, "cr": 0.25, "cw": 2.50},
            "qwen3.7-max": {"in": 2.50, "out": 7.50, "cr": 0.50, "cw": 3.125},
            "qwen3.7-plus": {"in": 0.40, "out": 1.60, "cr": 0.04, "cw": 0.50, "in_hi": 1.20, "out_hi": 4.80, "cr_hi": 0.12, "cw_hi": 1.50, "hi_above": 256000},
            "qwen3.6-plus": {"in": 0.50, "out": 3.00, "cr": 0.05, "cw": 0.625, "in_hi": 2.00, "out_hi": 6.00, "cr_hi": 0.20, "cw_hi": 2.50, "hi_above": 256000},
            "deepseek-v4-pro": {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None},
            "deepseek-v4-flash": {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None},
            "hy3": {"in": 0.14, "out": 0.58, "cr": 0.035, "cw": None},
        },
        "model_quotas": {
            "grok-4.5": 15, "gpt-5.6-luna": 15, "glm-5.2": 60, "glm-5.1": 60,
            "kimi-k3": 15, "kimi-k2.7-code": 60, "kimi-k2.6": 60,
            "mimo-v2.5": 60, "mimo-v2.5-pro": 15,
            "minimax-m3": 60, "minimax-m2.7": 60,
            "qwen3.8-max": 15, "qwen3.7-max": 60, "qwen3.7-plus": 60, "qwen3.6-plus": 60,
            "deepseek-v4-pro": 60, "deepseek-v4-flash": 60,
            "hy3": 60,
        },
        "req_limits": {
            "grok-4.5": [120, 300, 600],
            "gpt-5.6-luna": [2050, 5100, 10250],
            "glm-5.2": [880, 2150, 4300],
            "glm-5.1": [880, 2150, 4300],
            "kimi-k3": [110, 250, 490],
            "kimi-k2.7-code": [1350, 3380, 6750],
            "kimi-k2.6": [1150, 2880, 5750],
            "mimo-v2.5": [30100, 75200, 150400],
            "mimo-v2.5-pro": [3250, 8150, 16300],
            "minimax-m3": [3200, 8000, 16000],
            "minimax-m2.7": [3400, 8500, 17000],
            "qwen3.8-max": [160, 400, 810],
            "qwen3.7-max": [340, 840, 1690],
            "qwen3.7-plus": [4300, 10800, 21600],
            "qwen3.6-plus": [3300, 8200, 16300],
            "deepseek-v4-pro": [3450, 8550, 17150],
            "deepseek-v4-flash": [31650, 79050, 158150],
            "hy3": [4300, 10750, 21500],
        },
        "tokens_per_req": {
            "grok-4.5": 72820,
            "gpt-5.6-luna": 51220,
            "glm-5.2": 52850,
            "glm-5.1": 52850,
            "kimi-k3": 77850,
            "kimi-k2.7-code": 56070,
            "kimi-k2.6": 56070,
            "mimo-v2.5": 72625,
            "mimo-v2.5-pro": 87095,
            "minimax-m3": 56700,
            "minimax-m2.7": 55425,
            "qwen3.8-max": 66620,
            "qwen3.7-max": 66620,
            "qwen3.7-plus": 57690,
            "qwen3.6-plus": 57690,
            "deepseek-v4-pro": 83040,
            "deepseek-v4-flash": 69070,
            "hy3": 72625,
        },
        "display_names": {
            "grok-4.5": "Grok 4.5", "gpt-5.6-luna": "GPT 5.6 Luna", "glm-5.2": "GLM-5.2",
            "glm-5.1": "GLM-5.1", "kimi-k3": "Kimi K3", "kimi-k2.7-code": "Kimi K2.7 Code",
            "kimi-k2.6": "Kimi K2.6", "mimo-v2.5": "MiMo-V2.5", "mimo-v2.5-pro": "MiMo-V2.5 Pro", "mimo-v2.5-free": "MiMo-V2.5",
            "minimax-m3": "MiniMax M3", "minimax-m2.7": "MiniMax M2.7", "qwen3.8-max": "Qwen3.8 Max",
            "qwen3.7-max": "Qwen3.7 Max", "qwen3.7-plus": "Qwen3.7 Plus", "qwen3.6-plus": "Qwen3.6 Plus",
            "deepseek-v4-pro": "DeepSeek V4 Pro", "deepseek-v4-flash": "DeepSeek V4 Flash", "hy3": "Hy3",
            "hy3-free": "Hy3", "hy3:free": "Hy3", "tencent/hy3:free": "Hy3",
            "ling-3.0-flash-free": "Ling 3.0 Flash", "nemotron-3-ultra-free": "Nemotron 3 Ultra",
            "cohere/north-mini-code:free": "North Mini Code", "north-mini-code-free": "North Mini Code",
            "google/lyria-3-pro-preview": "Lyria 3 Pro", "kilo-auto-free": "Kilo Auto",
            "nemotron-3-ultra-550b-a55b-free": "Nemotron 3 Ultra 550B",
            "nemotron-3-super-120b-a12b-free": "Nemotron 3 Super 120B", "gemma-4-31b-it-free": "Gemma 4 31B",
            "hy3-preview": "Hy3 Preview",
            "big-pickle": "Big Pickle",
            "nemotron-3-nano-omni-30b-a3b-reasoning-free": "Nemotron 3 Nano Omni",
            "nemotron-3.5-content-safety-free": "Nemotron 3.5 Safety",
            "nemotron-3-nano-30b-a3b-free": "Nemotron 3 Nano 30B",
            "laguna-s-2.1-free": "Laguna S 2.1",
            "laguna-xs-2.1-free": "Laguna XS 2.1",
            "step-3.7-flash-free": "Step 3.7 Flash",
            "longcat-2.0-free": "Longcat 2.0",
            "ling-3.0-tiny-free": "Ling 3.0 Tiny",
            "gemma-4-26b-a4b-it-free": "Gemma 4 26B",
            "nemotron-nano-12b-v2-vl-free": "Nemotron Nano 12B VL",
            "nemotron-nano-9b-v2-free": "Nemotron Nano 9B",
            "gpt-oss-20b-free": "GPT-OSS 20B",
            "deepseek-v4-flash-free": "DeepSeek V4 Flash",
            "mimo-v2.5-free": "MiMo-V2.5",
        },
        "refresh": {"formula_s": 900},
    },
    "constants": {
        "weeks_per_month": 4.345,
        "periods_per_day": 6.0,
        "note": "魔法常数，云端可调，本地回退默认值"
    },
    "formulas": {
        "tok_agg": {"display": "token 总量", "source": "官方 usage_records", "expr": "tokens_in + tokens_out + tokens_cache", "params": ["tokens_in", "tokens_out", "tokens_cache"], "used_by": ["go-usage-widget.py", "data_server.py"]},
        "cost_agg": {"display": "原始账单 cost", "source": "官方 API 返回原始值", "expr": "Σcost", "params": ["cost"], "used_by": ["go-usage-widget.py"]},
        "meter_total": {"display": "官方口径总用量", "source": "官方 cost_summary + 云端 params.meter", "expr": "Σcost × meter.ratio  (当前 1.4212)", "params": ["cost", "meter_ratio", "scope_all", "paid_sources", "source"], "used_by": ["views.py"]},
        "model_quota": {"display": "模型月度额度", "source": "云端 params.model_quotas", "expr": "MODEL_QUOTAS[m] (缺省 LIMITS.monthly)", "params": ["model", "model_quotas", "monthly_limit"], "used_by": ["go-usage-widget.py"]},
        "est_req_cost": {"display": "官方估算次均费用", "source": "云端 params.model_quotas + req_limits", "expr": "model_quota / req_limits[2]", "params": ["model_quota", "req_limits"], "used_by": ["go-usage-widget.py"]},
        "est_tok_cost": {"display": "官方估算每 token 费用", "source": "est_req_cost + 云端 params.tokens_per_req", "expr": "est_req_cost / tokens_per_req[m]", "params": ["est_req_cost", "tokens_per_req"], "used_by": ["go-usage-widget.py"]},
        "model_used": {"display": "模型已用费用", "source": "官方 cost_map 权威 / 本地聚合", "expr": "cost_map[m] (go优先) 或 cost_total", "params": ["cost_map", "model", "source", "cost_total"], "used_by": ["go-usage-widget.py"]},
        "model_remain": {"display": "模型剩余额度", "source": "model_quota + model_used", "expr": "max(0, model_quota - model_used)", "params": ["model_quota", "model_used"], "used_by": ["go-usage-widget.py"]},
        "global_limit": {"display": "全局限额", "source": "官方 limits + sync_meta", "expr": "monthly + applied_credits × credit_per_applied", "params": ["monthly_limit", "applied_credits", "credit_per_applied"], "used_by": ["go-usage-widget.py"]},
        "used_all": {"display": "全局已用", "source": "官方 cost_summary", "expr": "Σcost (付费来源)", "params": ["used_all"], "used_by": ["go-usage-widget.py"]},
        "cq_weekly": {"display": "周配额", "source": "model_quota + 云端 params.weeks_per_month", "expr": "model_quota / weeks_per_month  (当前 4.345)", "params": ["model_quota", "weeks_per_month"], "used_by": ["go-usage-widget.py"]},
        "cq_session": {"display": "时段配额", "source": "model_quota + 云端 params.periods_per_day", "expr": "model_quota / periods_per_day  (当前 6.0)", "params": ["model_quota", "periods_per_day"], "used_by": ["go-usage-widget.py"]},
        "tq_monthly": {"display": "token 配额反推(月)", "source": "model_quota + 实际月度均价", "expr": "cq_monthly / avg_monthly_cost_per_token", "params": ["cq_monthly", "avg_monthly_cost_per_token"], "used_by": ["go-usage-widget.py"]},
        "tq_weekly": {"display": "token 配额反推(周)", "source": "model_quota + 实际周均价", "expr": "cq_weekly / avg_weekly_cost_per_token", "params": ["cq_weekly", "avg_weekly_cost_per_token"], "used_by": ["go-usage-widget.py"]},
        "tq_session": {"display": "token 配额反推(时段)", "source": "model_quota + 实际时段均价", "expr": "cq_session / avg_session_cost_per_token", "params": ["cq_session", "avg_session_cost_per_token"], "used_by": ["go-usage-widget.py"]},
        "tp_monthly": {"display": "token 使用百分比(月)", "source": "monthly_tok + tq_m", "expr": "min(100, monthly_tok / tq_m × 100)", "params": ["tok_monthly", "tq_monthly"], "used_by": ["go-usage-widget.py"]},
        "tp_weekly": {"display": "token 使用百分比(周)", "source": "weekly_tok + tq_w", "expr": "min(100, weekly_tok / tq_w × 100)", "params": ["tok_weekly", "tq_weekly"], "used_by": ["go-usage-widget.py"]},
        "tp_session": {"display": "token 使用百分比(时段)", "source": "session_tok + tq_s", "expr": "min(100, session_tok / tq_s × 100)", "params": ["tok_session", "tq_session"], "used_by": ["go-usage-widget.py"]},
        "effective_remain": {"display": "有效剩余", "source": "model_remain + global_remain", "expr": "min(model_remain, global_remain)", "params": ["model_remain", "global_remain"], "used_by": ["electron/app/index.html"]},
        "avg_per_req": {"display": "次均费用", "source": "实际已用 或 官方估算", "expr": "有数据时 c / usedCnt，否则 est_req_cost", "params": ["cost_of_period", "used_count", "est_req_cost"], "used_by": ["electron/app/index.html"]},
        "remain_cnt": {"display": "剩余次数", "source": "effectiveRemain + avgPerReq", "expr": "effective_remain / avg_per_req", "params": ["effective_remain", "avg_per_req"], "used_by": ["electron/app/index.html"]},
        "cache_hit": {"display": "缓存命中率", "source": "本地 usage_records", "expr": "cache_read / (cache_read + tokens_in) × 100", "params": ["cache_read", "tokens_in"], "used_by": ["go-usage-widget.py", "electron/app/index.html"]},
        "rate": {"display": "速率", "source": "本地 usage_records", "expr": "tok_sec_sum / tok_sec_n  (tok/s)", "params": ["tok_sec_sum", "tok_sec_n"], "used_by": ["go-usage-widget.py", "electron/app/index.html"]},
        "pct": {"display": "百分比", "source": "计算", "expr": "min(100, part / total × 100)", "params": ["part", "total"], "used_by": ["go-usage-widget.py", "electron/app/index.html"]},
        "token_quota_reverse": {"display": "token 配额反推(前端)", "source": "model_quota + 实际月度均价", "expr": "used_tokens + remain_cost / avg_cost_per_token", "params": ["used_tokens", "remain_cost", "avg_cost_per_token"], "used_by": ["electron/app/index.html"]},
        "dedup_rule": {"display": "去重规则", "source": "remote_rows(官方) + extra(本地)", "expr": "(model, src) 联合去重，官方优先", "params": [], "used_by": ["data_server.py"]},
        "supplier_agg": {"display": "供应商聚合", "source": "本地聚合（按 src）", "expr": "Σtokens / Σcount / Σcost", "params": [], "used_by": ["data_server.py"]}
    },
    "views": [
        {"id": "all_today",  "label": "全部 · 今天",   "scope": {"all": True}, "range": "today", "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "all_7d",     "label": "全部 · 近7天",  "scope": {"all": True}, "range": "7d",    "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "all_30d",    "label": "全部 · 近30天", "scope": {"all": True}, "range": "30d",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "all_all",    "label": "全部 · 全部",   "scope": {"all": True}, "range": "all",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_today",   "label": "GO · 今天",     "scope": {"source": "go"}, "range": "today", "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_7d",      "label": "GO · 近7天",    "scope": {"source": "go"}, "range": "7d",    "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_30d",     "label": "GO · 近30天",   "scope": {"source": "go"}, "range": "30d",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "go_all",     "label": "GO · 全部",     "scope": {"source": "go"}, "range": "all",   "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": [{"op": "meterRatio", "on": "cost"}]},
        {"id": "gateway_all", "label": "AI Gateway · 全部", "scope": {"source": "gateway"}, "range": "all", "group": None,
         "agg": ["cost", "tokens", "count", "days"], "post": []},
        {"id": "all_daily",  "label": "全部 · 逐日",   "scope": {"all": True}, "range": "all", "group": "day",
         "agg": ["cost", "tokens", "count"], "post": []},
        {"id": "go_daily",   "label": "GO · 逐日",     "scope": {"source": "go"}, "range": "all", "group": "day",
         "agg": ["cost", "tokens", "count"], "post": []},
    ],
}

_RANGES = {"today": "today", "1": "today", "7d": "7d", "7": "7d",
           "30d": "30d", "30": "30d", "all": "all"}

DEFAULT_FORMULA_URL = "https://opencode-formula.opencode-widget.workers.dev/formula"


def resolve_formula_url(url=None):
    """解析生效的公式来源。空串/None → FORMULA_URL 环境变量 → workers.dev 默认。

    (非空 url 优先; 空串保持历史默认 workers.dev, 兼容 "formula_url": ""。)
    """
    return url or os.environ.get("FORMULA_URL") or DEFAULT_FORMULA_URL


def _sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Canonical hash of the built-in fallback formula (sort_keys + ensure_ascii=False).
_DEFAULT_CANONICAL = json.dumps(DEFAULT_FORMULA, sort_keys=True, ensure_ascii=False)
DEFAULT_FORMULA_HASH = hashlib.sha256(_DEFAULT_CANONICAL.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Phase 7: remote-formula trust boundary
# ---------------------------------------------------------------------------
# Trust model (what this module DOES and does NOT do):
#   * The payload is INERT DATA (params / views / constants / formulas). It is
#     never eval'd or exec'd; only the sections the app actually consumes are
#     interpreted.
#   * HTTPS transport is used for the production default URL. Strict
#     schema/version validation, bounded payload size and a finite fetch timeout
#     guard the boundary. On any failure the previous good formula is retained
#     (last-known-good), never cleared, and adoption/persistence is atomic
#     (tmp file + os.replace).
#   * NO signature verification is implemented here. There is no independent
#     trust root or key-distribution channel, so the payload is reported with
#     integrity_status="unsigned". It is deliberately NEVER described as
#     "signed" or "secured".
# ---------------------------------------------------------------------------
SUPPORTED_FORMULA_SCHEMA = 1
MIN_SUPPORTED_FORMULA_VERSION = 1
MAX_SUPPORTED_FORMULA_VERSION = 7
MAX_FORMULA_BYTES = 512 * 1024
FORMULA_FETCH_TIMEOUT_S = 2

# Named per-value caps (reject oversized payloads before they can hurt us).
MAX_FORMULA_STRING_LEN = 512
MAX_FORMULA_LIST_LEN = 2000
MAX_FORMULA_DICT_KEYS = 5000

# Documented, safely-ignored metadata bag + the only allowed top-level keys.
ALLOWED_FORMULA_TOP_LEVEL = frozenset({
    "version", "schema_version", "source_note", "params", "constants",
    "formulas", "views", "extensions",
})

_VALIDATION_CATEGORIES = frozenset({
    "unsupported_schema", "too_old", "too_new", "disabled",
})


class FormulaError(Exception):
    pass


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_finite(x):
    return _is_number(x) and math.isfinite(float(x))


def _oversized(value):
    """Return an error code when any string/list/dict exceeds the named caps."""
    if isinstance(value, str):
        return "oversized_string" if len(value) > MAX_FORMULA_STRING_LEN else None
    if isinstance(value, list):
        if len(value) > MAX_FORMULA_LIST_LEN:
            return "oversized_list"
        for item in value:
            err = _oversized(item)
            if err:
                return err
        return None
    if isinstance(value, dict):
        if len(value) > MAX_FORMULA_DICT_KEYS:
            return "oversized_dict"
        for key, val in value.items():
            if isinstance(key, str) and len(key) > MAX_FORMULA_STRING_LEN:
                return "oversized_string"
            err = _oversized(val)
            if err:
                return err
        return None
    return None


def _bad_str_map(mapping, code):
    if not isinstance(mapping, dict):
        return code
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return code
    return None


def _bad_str_list(items, code):
    if not isinstance(items, list):
        return code
    for value in items:
        if not isinstance(value, str):
            return code
    return None


def _bad_number_map(mapping, code, *, nonneg=False, positive=False):
    if not isinstance(mapping, dict):
        return code
    for value in mapping.values():
        if not _is_finite(value):
            return code
        number = float(value)
        if nonneg and number < 0:
            return code
        if positive and number <= 0:
            return code
    return None


def _bad_prices(prices):
    if not isinstance(prices, dict):
        return "bad_prices"
    for value in prices.values():
        if not isinstance(value, dict):
            return "bad_prices"
        for entry in value.values():
            if entry is None:
                continue
            if not _is_finite(entry) or float(entry) < 0:
                return "bad_prices"
    return None


def _bad_constants(constants):
    if not isinstance(constants, dict):
        return "bad_constants"
    for key, value in constants.items():
        # The documented "note" key is inert metadata (DEFAULT_FORMULA uses it);
        # every other entry must be a finite number.
        if key == "note" and isinstance(value, str):
            continue
        if not _is_finite(value):
            return "bad_constants"
    return None


def _bad_params(params):
    if not isinstance(params, dict):
        return "bad_params"
    if "limits" in params:
        err = _bad_number_map(params["limits"], "bad_limits", nonneg=True)
        if err:
            return err
    if "windows" in params:
        windows = params["windows"]
        if not isinstance(windows, dict):
            return "bad_windows"
        for value in windows.values():
            if not _is_int(value) or value <= 0:
                return "bad_windows"
    if "meter" in params:
        meter = params["meter"]
        if not isinstance(meter, dict):
            return "bad_meter"
        for key in ("ratio", "rate_default", "rate_intercept"):
            if key in meter and not _is_finite(meter[key]):
                return "bad_meter"
        if "rates" in meter:
            err = _bad_number_map(meter["rates"], "bad_meter")
            if err:
                return err
    if "sources" in params:
        sources = params["sources"]
        if not isinstance(sources, dict):
            return "bad_sources"
        if "paid" in sources:
            err = _bad_str_list(sources["paid"], "bad_sources")
            if err:
                return err
        if "subset_of" in sources:
            err = _bad_str_map(sources["subset_of"], "bad_sources")
            if err:
                return err
    if "providers" in params:
        providers = params["providers"]
        if not isinstance(providers, dict):
            return "bad_providers"
        if "src" in providers:
            err = _bad_str_map(providers["src"], "bad_providers")
            if err:
                return err
        if "prefixes" in providers:
            err = _bad_str_list(providers["prefixes"], "bad_providers")
            if err:
                return err
        if "aliases" in providers:
            err = _bad_str_map(providers["aliases"], "bad_providers")
            if err:
                return err
    if "free_models" in params:
        free_models = params["free_models"]
        if not isinstance(free_models, dict):
            return "bad_free_models"
        for value in free_models.values():
            err = _bad_str_list(value, "bad_free_models")
            if err:
                return err
    if "prices" in params:
        err = _bad_prices(params["prices"])
        if err:
            return err
    if "model_quotas" in params:
        quotas = params["model_quotas"]
        if not isinstance(quotas, dict):
            return "bad_model_quotas"
        for key, value in quotas.items():
            if not isinstance(key, str) or not _is_finite(value) or float(value) < 0:
                return "bad_model_quotas"
    if "req_limits" in params:
        req_limits = params["req_limits"]
        if not isinstance(req_limits, dict):
            return "bad_req_limits"
        for value in req_limits.values():
            if not isinstance(value, list):
                return "bad_req_limits"
            for entry in value:
                if not _is_finite(entry) or float(entry) < 0:
                    return "bad_req_limits"
    if "tokens_per_req" in params:
        tokens = params["tokens_per_req"]
        if not isinstance(tokens, dict):
            return "bad_tokens_per_req"
        for value in tokens.values():
            if not _is_finite(value) or float(value) <= 0:
                return "bad_tokens_per_req"
    if "display_names" in params:
        err = _bad_str_map(params["display_names"], "bad_display_names")
        if err:
            return err
    if "refresh" in params:
        refresh = params["refresh"]
        if _is_number(refresh):
            if not _is_finite(refresh) or float(refresh) <= 0:
                return "bad_refresh"
        elif isinstance(refresh, dict):
            for value in refresh.values():
                if not _is_finite(value) or float(value) <= 0:
                    return "bad_refresh"
        else:
            return "bad_refresh"
    return None


def validate_formula(f):
    """Pure validation of a remote formula document.

    Returns ``{"ok", "error", "schema_version", "formula_version"}``. It never
    raises and never mutates ``f``. ``error`` is a stable machine-readable code
    (``None`` when ok); ``schema_version``/``formula_version`` are echoed even
    on rejection where known so callers can report provenance.
    """
    result = {"ok": False, "error": None,
              "schema_version": None, "formula_version": None}
    if not isinstance(f, dict):
        result["error"] = "not_a_dict"
        return result

    # Legacy documents predate schema_version and are treated as schema 1.
    schema = f.get("schema_version", SUPPORTED_FORMULA_SCHEMA)
    if not _is_int(schema):
        result["error"] = "unsupported_schema"
        return result
    result["schema_version"] = schema
    if schema != SUPPORTED_FORMULA_SCHEMA:
        result["error"] = "unsupported_schema"
        return result

    version = f.get("version")
    if not _is_int(version):
        result["error"] = "bad_version"
        return result
    result["formula_version"] = version
    if version < MIN_SUPPORTED_FORMULA_VERSION:
        result["error"] = "too_old"
        return result
    if version > MAX_SUPPORTED_FORMULA_VERSION:
        result["error"] = "too_new"
        return result

    for key in f:
        if key not in ALLOWED_FORMULA_TOP_LEVEL:
            result["error"] = "unknown_field"
            return result

    if "params" not in f:
        result["error"] = "missing_params"
        return result
    if "views" not in f:
        result["error"] = "missing_views"
        return result

    err = _bad_params(f["params"])
    if err:
        result["error"] = err
        return result
    if "constants" in f:
        err = _bad_constants(f["constants"])
        if err:
            result["error"] = err
            return result
    if "formulas" in f and not isinstance(f["formulas"], dict):
        result["error"] = "bad_formulas"
        return result
    if not isinstance(f["views"], list):
        result["error"] = "bad_views"
        return result
    for view in f["views"]:
        if not isinstance(view, dict):
            result["error"] = "bad_views"
            return result
    if "extensions" in f and not isinstance(f["extensions"], dict):
        result["error"] = "bad_extensions"
        return result

    err = _oversized(f)
    if err:
        result["error"] = err
        return result

    result["ok"] = True
    return result


def _validation_category(error):
    """Map a granular validate_formula error code to a meta status category."""
    if error is None:
        return None
    if error in _VALIDATION_CATEGORIES:
        return error
    return "invalid"


class FormulaStore:
    """拉取/缓存/回退公式。url 可为 http(s) 或本地文件路径。

    enabled=False 时完全不联网, 直接使用内置 DEFAULT_FORMULA (source="disabled")。
    任何失败都按 last-known-good 顺序回退: 内存中已生效公式 → 持久化缓存
    → DEFAULT_FORMULA; 已生效公式绝不会被清空。

    cache_path 提供时, 校验通过的公式以
    {"schema_version","formula_version","loaded_at","integrity_status",
     "formula"} 包装原子落盘 (tmp + os.replace); 缓存内容在采用前也必须
    通过 validate_formula。残留的 ``.tmp`` 文件不影响加载。

    meta() 额外暴露 hash/version/fallback/last_updated 及 Phase 7 溯源字段
    (schema_version/formula_version/loaded_at/validation_status/
    integrity_status)。integrity_status 恒为 "unsigned": 本阶段没有独立
    信任根/密钥分发, 不做签名校验。
    """

    def __init__(self, url=None, ttl=900, enabled=True, cache_path=None):
        self.raw_url = url if url is not None else os.environ.get("FORMULA_URL")
        self.url = resolve_formula_url(self.raw_url)
        self.ttl = ttl
        self.enabled = enabled
        self.cache_path = cache_path
        # 当前生效公式的溯源状态
        self._formula = None
        self._raw = None
        self._hash = DEFAULT_FORMULA_HASH
        self._version = DEFAULT_FORMULA.get("version")
        self._schema_version = DEFAULT_FORMULA.get(
            "schema_version", SUPPORTED_FORMULA_SCHEMA)
        self._fallback = True
        self._ts = 0.0
        self._source = "default"
        self._error = None
        self._fetched_at = 0
        self._last_updated = 0
        self._loaded_at = 0
        self._validation_status = "ok"
        self._meta = {}
        # 冷启动即载入持久化 LKG (若存在且通过校验), 作为内存 last-known-good。
        if self.enabled:
            self._load_cache_into_memory()
        # Phase 7 fix: a formula adopted from the persisted cache at construction
        # is treated as fresh for one TTL. Without this, ``_ts`` stays 0 and the
        # very first ``get()`` on every launch performs a synchronous network
        # refresh (up to FORMULA_FETCH_TIMEOUT_S) before falling back to the same
        # cache — i.e. startup blocked on the network despite having a valid
        # last-known-good. The background formula-sync loop still refreshes with
        # ``force=True`` (non-blocking launch, cloud updates asynchronously), and
        # an explicit ``force=True`` here still fetches.
        if self._formula is not None:
            self._ts = time.time()
        self._rebuild_meta()

    # -- provenance -------------------------------------------------------
    def _rebuild_meta(self):
        self._meta = {
            "source": self._source, "url": self.url, "error": self._error,
            "fetched_at": self._fetched_at, "enabled": self.enabled,
            "hash": self._hash, "version": self._version,
            "last_updated": self._last_updated, "fallback": self._fallback,
            "schema_version": self._schema_version,
            "formula_version": self._version,
            "loaded_at": self._loaded_at,
            "validation_status": self._validation_status,
            "integrity_status": "unsigned",
        }

    def _set_default(self, *, source, error, validation_status, fetched_at,
                     last_updated=0):
        self._formula = DEFAULT_FORMULA
        self._raw = None
        self._hash = DEFAULT_FORMULA_HASH
        self._version = DEFAULT_FORMULA.get("version")
        self._schema_version = DEFAULT_FORMULA.get(
            "schema_version", SUPPORTED_FORMULA_SCHEMA)
        self._fallback = True
        self._source = source
        self._error = error
        self._fetched_at = fetched_at
        self._last_updated = last_updated
        self._loaded_at = 0
        self._validation_status = validation_status
        self._rebuild_meta()

    def _adopt_cached(self, formula, res, payload, error):
        self._formula = formula
        self._raw = None
        self._hash = _sha256_hex(
            json.dumps(formula, sort_keys=True, ensure_ascii=False))
        self._version = res["formula_version"]
        self._schema_version = res["schema_version"]
        self._fallback = True
        self._source = "cache"
        self._error = error
        self._loaded_at = payload.get("loaded_at") or 0
        self._validation_status = "ok"
        self._rebuild_meta()

    def _adopt_fallback(self, error, category):
        # 1) 内存中 last-known-good: 保留当前公式, 绝不清空
        if self._formula is not None:
            self._fallback = True
            self._error = error
            if category is not None:
                self._validation_status = category
            self._rebuild_meta()
            return
        # 2) 持久化缓存 (采用前已通过 validate_formula)
        cached = self._read_cache()
        if cached is not None:
            self._adopt_cached(cached[0], cached[1], cached[2], error)
            return
        # 3) 内置默认公式
        self._set_default(source="default", error=error,
                          validation_status="ok", fetched_at=0)

    def _load_cache_into_memory(self):
        cached = self._read_cache()
        if cached is None:
            return
        self._adopt_cached(cached[0], cached[1], cached[2], None)

    def _read_cache(self):
        """Read + validate the persisted cache; None on any failure.

        A leftover ``.tmp`` is never consulted (only ``cache_path`` itself).
        """
        if not self.cache_path:
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        formula = payload.get("formula")
        res = validate_formula(formula)
        if not res["ok"]:
            return None
        return formula, res, payload

    def _persist_cache(self, formula, res):
        """Atomically persist the validated formula + provenance metadata."""
        if not self.cache_path:
            return
        try:
            payload = {
                "schema_version": res["schema_version"],
                "formula_version": res["formula_version"],
                "loaded_at": int(time.time() * 1000),
                "integrity_status": "unsigned",
                "formula": formula,
            }
            directory = os.path.dirname(self.cache_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except Exception:
            # 缓存持久化是 best-effort, 失败绝不影响本次采用。
            pass

    def refresh(self, force=False):
        if not self.enabled:
            # 禁用: 绝不调用 _fetch, 也不触网; 直接用内置默认公式
            ts = int(time.time() * 1000)
            self._set_default(source="disabled", error=None,
                              validation_status="disabled", fetched_at=ts,
                              last_updated=ts)
            return False
        if self.url:
            try:
                data = self._fetch(self.url)
                f = json.loads(data)
                res = validate_formula(f)
                if not res["ok"]:
                    raise FormulaError(res["error"] or "invalid_formula")
                fetched_at = int(time.time() * 1000)
                # 校验通过后才采用 (atomic adoption): 先落盘再切换。
                self._persist_cache(f, res)
                self._formula = f
                self._raw = data
                # 成功时 hash 针对"原始拉取串", 保证与云端字节一致
                self._hash = _sha256_hex(data)
                self._version = res["formula_version"]
                self._schema_version = res["schema_version"]
                self._fallback = False
                self._source = "cloud"
                self._error = None
                self._fetched_at = fetched_at
                self._last_updated = fetched_at
                self._loaded_at = fetched_at
                self._validation_status = "ok"
                self._rebuild_meta()
                return True
            except Exception as e:
                category = _validation_category(
                    str(e) if isinstance(e, FormulaError) else None)
                self._adopt_fallback(str(e), category)
                return False
        self._set_default(source="default", error=None, validation_status="ok",
                          fetched_at=0)
        return False

    def get(self, force=False):
        if force or self._formula is None or time.time() - self._ts > self.ttl:
            self.refresh(force=force)
            self._ts = time.time()
        return self._formula

    def meta(self):
        return dict(self._meta)

    @staticmethod
    def _fetch(url):
        if url.startswith("http://") or url.startswith("https://"):
            req = urllib.request.Request(url, headers={
                "User-Agent": "opencode-widget/1.0 (+formula)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=FORMULA_FETCH_TIMEOUT_S) as resp:
                data = resp.read(MAX_FORMULA_BYTES + 1)
                if len(data) > MAX_FORMULA_BYTES:
                    raise FormulaError("formula_too_large")
                return data.decode("utf-8")
        path = url
        if url.startswith("file://"):
            path = url[len("file://"):]
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = fh.read(MAX_FORMULA_BYTES + 1)
            if len(data) > MAX_FORMULA_BYTES:
                raise FormulaError("formula_too_large")
            # 保持与旧文本模式读取一致的通用换行语义 (CRLF/CR -> LF)。
            return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        raise FormulaError(f"无法读取公式来源: {url}")

    @staticmethod
    def _validate(f):
        res = validate_formula(f)
        if not res["ok"]:
            raise FormulaError(res["error"] or "invalid_formula")
        return f


class ViewEngine:
    """按公式对 rows 执行 view 查询。norm/is_free/local_tz 注入自数据源, 保证口径一致。"""

    def __init__(self, formula, norm=None, is_free=None, local_tz=None):
        self.f = formula
        self.params = formula.get("params", {}) if isinstance(formula, dict) else {}
        self.meter = self.params.get("meter", {})
        self.sources = self.params.get("sources", {})
        self.subset = self.sources.get("subset_of", {})
        self.paid = set(self.sources.get("paid", []))
        self.ratio = float(self.meter.get("ratio", 1.0))
        # v5 分模型折算率: 官方 used = intercept + Σ(行消费 × rate)
        self.rates = dict(self.meter.get("rates") or {})
        self.rate_default = float(self.meter.get("rate_default", 1.0))
        self.rate_intercept = float(self.meter.get("rate_intercept", 0.0))
        # 动态抵扣总额 = 抵扣次数 × $5, 由宿主(data_server)按账户实际状态注入
        self.credit_deduct = 0.0
        # 当前订阅周期起始(ms): "本期"(30d档) 的截止锚点, 由宿主注入
        self.period_start_ms = 0
        self.norm = norm or (lambda m: m)
        self.is_free = is_free or (lambda m: False)
        self.local_tz = local_tz or datetime.now().astimezone().tzinfo

    def rate_for(self, model, src=None):
        if src and src not in self.paid:
            return 1.0
        return self.rates.get(self.norm(model), self.rate_default)

    def _day(self, ts):
        return datetime.fromtimestamp(ts / 1000, self.local_tz).strftime("%Y-%m-%d")

    def _cutoff(self, rng):
        r = _RANGES.get(rng or "all")
        if r is None or r == "all":
            return None
        if r == "30d":
            # "本期": 截止 = 当前订阅周期起始 (订阅日); 订阅日未知时回退字面30天
            if self.period_start_ms:
                return datetime.fromtimestamp(self.period_start_ms / 1000, timezone.utc).strftime("%Y-%m-%d")
            today = datetime.now(timezone.utc) - timedelta(days=29)
            return today.strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc)
        if r == "7d":
            today = today - timedelta(days=6)
        return today.strftime("%Y-%m-%d")

    def resolve_view(self, vid):
        views = self.f.get("views", []) if isinstance(self.f, dict) else []
        for v in views:
            if v.get("id") == vid:
                return dict(v)
        dyn = self._parse_dynamic(vid)
        if dyn is None:
            raise FormulaError(f"未知 view: {vid}")
        return dyn

    def _parse_dynamic(self, vid):
        # model:<key>_<range|daily>
        if vid.startswith("model:"):
            rest = vid[len("model:"):]
            parts = rest.rsplit("_", 1)
            tail = parts[1] if len(parts) == 2 and parts[1] in _RANGES or (len(parts) == 2 and parts[1] == "daily") else "all"
            key = parts[0]
            if len(parts) == 2 and parts[1] == "daily":
                return {"id": vid, "label": key, "scope": {"model": key},
                        "range": "all", "group": "day", "agg": [], "post": []}
            return {"id": vid, "label": key, "scope": {"model": key},
                    "range": tail, "group": None, "agg": [], "post": []}
        # <scope>_<range|daily>
        if vid.endswith("_daily"):
            head = vid[: -len("_daily")]
            scope = self._scope_from(head)
            return {"id": vid, "label": head, "scope": scope,
                    "range": "all", "group": "day", "agg": [], "post": self._post_for(scope)}
        if "_" in vid:
            head, tail = vid.rsplit("_", 1)
            if tail in _RANGES:
                scope = self._scope_from(head)
                return {"id": vid, "label": head, "scope": scope,
                        "range": tail, "group": None, "agg": [], "post": self._post_for(scope)}
        return None

    def _post_for(self, scope):
        if scope.get("all") or scope.get("source") in self.paid:
            return [{"op": "meterRatio", "on": "cost"}]
        return []

    def _scope_from(self, head):
        if head == "all":
            return {"all": True}
        if head == "free":
            return {"free": True}
        return {"source": head}

    def _scope_rows(self, rows, scope):
        if not scope or scope.get("all"):
            sub = self.subset
            # 子集来源(如 gateway)是登录账号调用的镜像, 排除防双计;
            # 其他账号(account=other)的 gateway 记录官方没有, 是独立消费, 保留
            return [r for r in rows
                    if r.get("src") not in sub or r.get("account") == "other"]
        if "source" in scope:
            s = scope["source"]
            return [r for r in rows if r.get("src") == s]
        if "free" in scope:
            return [r for r in rows if self.is_free(r.get("model"))]
        if "model" in scope:
            key = scope["model"]
            if "|" in key:
                m, src = key.split("|", 1)
                return [r for r in rows
                        if r.get("src") == src and self.norm(r.get("model")) == m]
            m = self.norm(key)
            return [r for r in rows if self.norm(r.get("model")) == m]
        return []

    def execute(self, vid, rows, now_ms=None):
        view = self.resolve_view(vid)
        cutoff = self._cutoff(view.get("range"))
        scoped = self._scope_rows(rows, view.get("scope"))
        # 分账: 本期/7天/today 只算登录账号(官方); "全部"含其他账号(本地估算)
        if view.get("range") != "all":
            scoped = [r for r in scoped if r.get("account") != "other"]
        daily = {}
        for r in scoped:
            d = self._day(r["ts"])
            if cutoff is not None and (len(d) != 10 or d < cutoff):
                continue
            tk = r.get("tokens") or {}
            ti = tk.get("input", 0) or 0
            to = tk.get("output", 0) or 0
            cache = tk.get("cache") or {}
            tc = (cache.get("read", 0) or 0) + (cache.get("write", 0) or 0)
            cost = (r.get("cost") or 0.0) * self.rate_for(r.get("model"), r.get("src"))
            b = daily.setdefault(d, [0.0, 0, 0, 0, 0])
            b[0] += cost
            b[1] += 1
            b[2] += ti
            b[3] += to
            b[4] += tc
        totals = {"cost": 0.0, "tokens": 0, "input": 0, "output": 0,
                  "cache": 0, "count": 0, "days": len(daily)}
        series = []
        for d in sorted(daily):
            cost, count, ti, to, tc = daily[d]
            totals["cost"] += cost
            totals["tokens"] += ti + to + tc
            totals["input"] += ti
            totals["output"] += to
            totals["cache"] += tc
            totals["count"] += count
            series.append({"date": d, "cost": round(cost, 4), "count": count,
                           "tokens": int(ti + to + tc), "input": int(ti),
                           "output": int(to), "cache": int(tc)})
        self._apply_post(view, totals, series)
        return {
            "id": view.get("id", vid),
            "label": view.get("label", vid),
            "scope": view.get("scope"),
            "range": view.get("range"),
            "group": view.get("group"),
            "totals": totals,
            "daily": series,
        }

    def _apply_post(self, view, totals, series):
        for op in view.get("post", []):
            if op.get("op") == "meterRatio":
                # v6: 行级已按折算率累计, 这里补截距; 仅"本期"(30d档)视图减动态抵扣(次数×$5)
                scope = view.get("scope") or {}
                hit = bool(scope.get("all")) or (scope.get("source") in self.paid)
                if hit:
                    deduct = self.credit_deduct if (view.get("range") in ("30d", "30")) else 0.0
                    totals["cost"] = round(max(0.0, totals["cost"] + self.rate_intercept - deduct), 4)
            elif op.get("op") == "round4":
                f = op.get("on", "cost")
                totals[f] = round(totals.get(f, 0), 4)
                for p in series:
                    if f in p:
                        p[f] = round(p[f], 4)