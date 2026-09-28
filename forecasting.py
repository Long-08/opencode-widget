#!/usr/bin/env python3
"""Phase 4: deterministic, explainable, reset-aware usage forecasting.

Pure functions only: no DB, no network, no module-level mutable state. All
timestamps are UTC epoch milliseconds (int).

Design principles (enforced by tests):
  * No ML/statistics beyond transparent linear rate extrapolation.
  * Never mix bases: the official quota state is the first fact source for
    used/remaining/limit; rated raw usage is only ever used to estimate a
    *rate* of consumption.
  * Every output produced here is an estimate and is typed as such by
    ``build_forecast`` (``type: "estimate"``); it is never official/guaranteed.
  * No ``Infinity``/``NaN`` is ever emitted. Zero usage -> burn rate 0.
"""
import re

# ---- named window constants (ms) -----------------------------------------
HOUR_MS = 3600000
DAY_MS = 86400000
THIRTY_DAYS_MS = 30 * DAY_MS

SESSION_WINDOWS = {"last_30m": 1800000, "last_60m": 3600000, "window_avg": 18000000}
WEEKLY_WINDOWS = {"last_24h": 86400000, "last_3d": 259200000, "last_7d": 604800000}
PERIOD_WINDOWS = {"daily_average": None, "recent_3d_average": 259200000,
                  "recent_7d_average": 604800000}

# minimum evidence required before a rate may be reported as "ok"
SAMPLING = {
    "last_30m": {"min_coverage_ms": 600000, "min_points": 2},
    "last_60m": {"min_coverage_ms": 1200000, "min_points": 2},
    "window_avg": {"min_coverage_ms": 1800000, "min_points": 2},
    "last_24h": {"min_coverage_ms": 21600000, "min_points": 2},
    "last_3d": {"min_coverage_ms": 86400000, "min_points": 3},
    "last_7d": {"min_coverage_ms": 172800000, "min_points": 3},
    "daily_average": {"min_coverage_ms": 86400000, "min_points": 2},
    "recent_3d_average": {"min_coverage_ms": 86400000, "min_points": 3},
    "recent_7d_average": {"min_coverage_ms": 172800000, "min_points": 3},
}

# ---- parse_reset_text ----------------------------------------------------
_RESET_UNIT_MS = {
    "s": 1000, "sec": 1000, "secs": 1000, "second": 1000, "seconds": 1000,
    "m": 60000, "min": 60000, "mins": 60000, "minute": 60000, "minutes": 60000,
    "h": HOUR_MS, "hr": HOUR_MS, "hrs": HOUR_MS, "hour": HOUR_MS, "hours": HOUR_MS,
    "d": DAY_MS, "day": DAY_MS, "days": DAY_MS,
}
_RESET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")


def parse_reset_text(text):
    """Parse a human reset string into a total millisecond offset.

    ``"5 hours 0 minutes"`` -> 5h, ``"2 days 3 hours"`` -> 51h,
    ``"45 minutes"`` -> 45m, ``"now"`` -> 0. Empty/unparsable -> None.
    """
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s:
        return None
    total = 0.0
    found = False
    for num, unit in _RESET_RE.findall(s):
        ms = _RESET_UNIT_MS.get(unit)
        if ms is None:
            continue
        total += float(num) * ms
        found = True
    if not found:
        return 0 if "now" in s.split() or s == "now" else None
    return int(round(total))


# ---- compute_rate --------------------------------------------------------
def compute_rate(records, start_ms, end_ms, min_coverage_ms, min_points):
    """Transparent linear rate over ``[start_ms, end_ms]`` (inclusive).

    ``records`` is ``[{"ts": ms, "usage": float}]``. Returns a fixed-shape
    dict; ``usage_per_hour`` is 0.0 for ``no_usage``, None for
    ``insufficient_data`` and a finite float for ``ok``.
    """
    in_win = []
    for r in records or []:
        try:
            ts = int(r.get("ts"))
        except Exception:
            continue
        if start_ms is not None and ts < start_ms:
            continue
        if end_ms is not None and ts > end_ms:
            continue
        try:
            usage = float(r.get("usage") or 0.0)
        except Exception:
            usage = 0.0
        in_win.append((ts, usage))
    in_win.sort(key=lambda x: x[0])
    points = len(in_win)
    usage_total = sum(u for _, u in in_win)
    first_ts = in_win[0][0] if points else None
    last_ts = in_win[-1][0] if points else None

    if points and end_ms is not None:
        anchor = first_ts if start_ms is None else max(start_ms, first_ts)
        coverage_ms = max(0, end_ms - anchor)
    else:
        coverage_ms = 0
    duration_hours = coverage_ms / HOUR_MS
    window_ms = (int(end_ms) - int(start_ms)) if (
        start_ms is not None and end_ms is not None) else None

    out = {
        "status": None,
        "usage": usage_total,
        "duration_hours": duration_hours,
        "usage_per_hour": None,
        "usage_per_day": None,
        "sample_points": points,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "window_ms": window_ms,
    }
    if usage_total == 0:
        out["status"] = "no_usage"
        out["usage_per_hour"] = 0.0
        out["usage_per_day"] = 0.0
        return out
    if points < min_points or coverage_ms < min_coverage_ms or duration_hours <= 0:
        out["status"] = "insufficient_data"
        return out
    out["status"] = "ok"
    out["usage_per_hour"] = usage_total / duration_hours
    out["usage_per_day"] = out["usage_per_hour"] * 24
    return out


# ---- compute_time_to_limit ----------------------------------------------
def _valid_ms(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        f = float(value)
    except Exception:
        return False
    if f != f or f in (float("inf"), float("-inf")):
        return False
    return f >= 0


def _est(status, ttl, raw, limit_at, reset_at):
    return {
        "status": status,
        "time_to_limit_hours": ttl,
        "raw_time_to_limit_hours": raw,
        "estimated_limit_at": limit_at,
        "reset_at": reset_at,
    }


def compute_time_to_limit(now_ms, remaining, reset_at, usage_per_hour):
    """Reset-aware estimate of when the remaining quota would run out.

    Never returns ``Infinity``: a zero/negative burn rate yields ``no_usage``
    with a null time-to-limit.
    """
    if remaining is None:
        return _est("unavailable", None, None, None, None)

    reset_valid = _valid_ms(reset_at)
    reset_out = int(reset_at) if reset_valid else None

    if remaining <= 0:
        return _est("already_at_limit", 0.0, 0.0, int(now_ms), reset_out)
    if usage_per_hour is None:
        return _est("insufficient_data", None, None, None, reset_out)
    if usage_per_hour <= 0:
        return _est("no_usage", None, None, None, reset_out)

    ttl = remaining / usage_per_hour
    limit_at = now_ms + ttl * HOUR_MS
    if not reset_valid:
        return _est("reset_unknown", None, ttl, None, None)
    if limit_at >= reset_at:
        return _est("reset_before_limit", None, ttl, None, reset_out)
    return _est("limit_before_reset", ttl, ttl, limit_at, reset_out)


# ---- compute_projection --------------------------------------------------
def compute_projection(now_ms, end_ms, usage_per_hour, official_used, official_limit):
    """Project official usage forward to ``end_ms`` at the estimated rate."""
    out = {
        "status": "insufficient_data",
        "projected_usage_at_end": None,
        "projected_remaining_at_end": None,
        "would_exceed_limit": False,
    }
    if (official_used is None or usage_per_hour is None or official_limit is None
            or now_ms is None or end_ms is None):
        return out
    try:
        used = float(official_used)
        limit = float(official_limit)
        rate = float(usage_per_hour)
    except Exception:
        return out
    if rate == 0:
        out["status"] = "no_usage"
        out["projected_usage_at_end"] = used
        out["projected_remaining_at_end"] = limit - used
        out["would_exceed_limit"] = False
        return out
    hours = max(0.0, float(end_ms) - float(now_ms)) / HOUR_MS
    projected = used + rate * hours
    # projected_remaining is deliberately NOT clamped: negative values are the
    # honest signal that the limit would be exceeded before the reset.
    out["status"] = "ok"
    out["projected_usage_at_end"] = projected
    out["projected_remaining_at_end"] = limit - projected
    out["would_exceed_limit"] = projected > limit
    return out


# ---- builders ------------------------------------------------------------
def _estimate(now_ms, official, rate):
    return compute_time_to_limit(
        now_ms, official.get("remaining"), official.get("reset_at"),
        rate.get("usage_per_hour"))


def _with_projection(est, now_ms, official, rate):
    proj = compute_projection(now_ms, official.get("reset_at"),
                              rate.get("usage_per_hour"),
                              official.get("used"), official.get("limit"))
    est["projected_usage_at_reset"] = proj["projected_usage_at_end"]
    est["projected_remaining_at_reset"] = proj["projected_remaining_at_end"]
    est["would_exceed_limit"] = proj["would_exceed_limit"]
    return est


def build_session_forecast(records, now_ms, official):
    rates = {}
    estimates = {}
    for key, window_ms in SESSION_WINDOWS.items():
        s = SAMPLING[key]
        rate = compute_rate(records, now_ms - window_ms, now_ms,
                            s["min_coverage_ms"], s["min_points"])
        rates[key] = rate
        estimates[key] = _estimate(now_ms, official, rate)
    return {"official": official, "rates": rates, "estimates": estimates}


def build_weekly_forecast(records, now_ms, official):
    rates = {}
    estimates = {}
    for key, window_ms in WEEKLY_WINDOWS.items():
        s = SAMPLING[key]
        rate = compute_rate(records, now_ms - window_ms, now_ms,
                            s["min_coverage_ms"], s["min_points"])
        rates[key] = rate
        estimates[key] = _with_projection(_estimate(now_ms, official, rate),
                                          now_ms, official, rate)
    return {"official": official, "rates": rates, "estimates": estimates}


def build_period_forecast(records, now_ms, official, period_start_ms=None):
    rates = {}
    estimates = {}
    for key, window_ms in PERIOD_WINDOWS.items():
        s = SAMPLING[key]
        if key == "daily_average":
            start = period_start_ms if period_start_ms else now_ms - DAY_MS
            rate = compute_rate(records, start, now_ms,
                                s["min_coverage_ms"], s["min_points"])
            # the period window is variable (subscription-anchored), so the
            # nominal fixed window is None.
            rate["window_ms"] = None
        else:
            rate = compute_rate(records, now_ms - window_ms, now_ms,
                                s["min_coverage_ms"], s["min_points"])
        rates[key] = rate
        estimates[key] = _with_projection(_estimate(now_ms, official, rate),
                                          now_ms, official, rate)
    return {"official": official, "rates": rates, "estimates": estimates}


def build_forecast(records, now_ms, session_official, weekly_official,
                   period_official, period_start_ms=None):
    """Top-level estimate envelope. Always ``type: "estimate"``."""
    return {
        "type": "estimate",
        "generated_at": now_ms,
        "basis": "official_quota_usage_rate",
        "session": build_session_forecast(records, now_ms, session_official),
        "weekly": build_weekly_forecast(records, now_ms, weekly_official),
        "period": build_period_forecast(records, now_ms, period_official,
                                        period_start_ms),
    }
