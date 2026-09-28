#!/usr/bin/env python3
"""Usage-timeline aggregation: pure, deterministic bucketing of usage rows.

Everything here is a pure function over already-normalized rows from
``gw.read_opencode_usage()`` (the same row shape consumed by
``observability.py``). No DB access, no network, no global mutation, no clock
reads: the caller owns the single read and injects ``now_ms`` / ``earliest_ms``
so results are reproducible in tests.

Semantics
---------
* ``COST_BASIS`` is the raw OpenCode message cost (``opencode_message_raw``),
  exactly like the other observability endpoints. No meter ratio is applied.
* A "bucket" is a half-open interval ``[start, end)`` in absolute epoch
  milliseconds. Adjacent buckets are contiguous; the final bucket is truncated
  at the window end.
* The series always emits **every** planned bucket, including empty ones
  (``requests 0``, zero tokens, ``cost 0.0``). Rows outside every bucket are
  ignored.
* Token totals are ``input + output + cache_read + cache_write``; no
  NaN/Infinity ever escapes (non-finite inputs are coerced to 0).
"""
import bisect
import math
from datetime import datetime

import observability

# Raw OpenCode message cost — identical basis to /api/agents, /api/models, ...
COST_BASIS = "opencode_message_raw"

# Hard ceiling on the number of buckets in one series.
MAX_BUCKETS = 200

_MS_PER_DAY = 86400000

# "Nice" bucket sizes (ms), ascending. The planner upgrades through this ladder
# (never past the last entry) whenever a window would exceed MAX_BUCKETS.
_NICE_SIZES = (
    1800000,      # 30 min
    3600000,      # 1 h
    10800000,     # 3 h
    21600000,     # 6 h
    43200000,     # 12 h
    86400000,     # 1 d
    259200000,    # 3 d
    604800000,    # 7 d
    2592000000,   # 30 d
    7776000000,   # 90 d
    31536000000,  # 365 d
)
NICE_SIZES = _NICE_SIZES

# Fixed base size per range; "all" has no fixed base (auto -> ladder start).
_BASE_SIZES = {
    "today": 1800000,    # 30 min
    "7d": 10800000,      # 3 h
    "30d": 86400000,     # 1 d
}

_VALID_RANGES = ("today", "7d", "30d", "all")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _tz(tz):
    """Return an explicit tz or the process-local one (never hardcoded)."""
    if tz is not None:
        return tz
    return datetime.now().astimezone().tzinfo


def _unit(size_ms):
    """Unit name for a bucket size: <1h -> minute, <1d -> hour, else day."""
    seconds = size_ms / 1000.0
    if seconds < 3600:
        return "minute"
    if seconds < 86400:
        return "hour"
    return "day"


def _parse_local_midnight(day, tz):
    """Parse a local ``YYYY-MM-DD`` day string to its local-midnight epoch ms.

    Returns ``None`` for a malformed/missing day. The timezone is injected so
    the caller (and tests) control the boundary; nothing is hardcoded.
    """
    if not day:
        return None
    try:
        y, m, d = (int(part) for part in str(day).split("-"))
        dt = datetime(y, m, d, tzinfo=_tz(tz))
    except (TypeError, ValueError):
        return None
    return int(dt.timestamp() * 1000)


def _clean_num(value):
    """Coerce a numeric value to a JSON-safe number (non-finite -> 0)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(f):
        return 0
    if f.is_integer():
        return int(f)
    return f


# --------------------------------------------------------------------------
# bucket planning
# --------------------------------------------------------------------------
def bucket_plan(range, cutoff, now_ms, earliest_ms, tz=None):
    """Plan the bucket window for a range.

    ``cutoff`` is the local-tz ``YYYY-MM-DD`` string produced by the formula
    engine (``engine._cutoff(range)``) or ``None``. Returns
    ``{"start", "end", "size_ms", "unit"}`` where ``end = now_ms``.

    * cutoff set -> ``start`` = local midnight of ``cutoff`` (parsed with ``tz``)
    * otherwise (``all`` / no cutoff) -> ``earliest_ms`` when truthy, else a
      trailing 24 h window (``end - 86400000``).

    The base size is fixed per fixed range (today 30 m, 7d 3 h, 30d 1 d) and
    auto (ladder start) for ``all``. It is then upgraded through ``NICE_SIZES``
    until the window fits in ``MAX_BUCKETS``; the plan always yields at least
    one bucket and never exceeds the ladder maximum.
    """
    rng = (range or "all").lower() if isinstance(range, str) else "all"
    if rng not in _VALID_RANGES:
        rng = "all"

    end = int(now_ms)
    start = _range_start(cutoff, end, earliest_ms, tz)

    size = _BASE_SIZES.get(rng)
    if size is None:
        size = _NICE_SIZES[0]
    idx = _NICE_SIZES.index(size) if size in _NICE_SIZES else 0
    while (end - start) / size > MAX_BUCKETS and idx < len(_NICE_SIZES) - 1:
        idx += 1
        size = _NICE_SIZES[idx]
    return {"start": start, "end": end, "size_ms": size, "unit": _unit(size)}


def _range_start(cutoff, end, earliest_ms, tz):
    midnight = _parse_local_midnight(cutoff, tz)
    if midnight is not None:
        return midnight
    if earliest_ms:
        return int(earliest_ms)
    return end - _MS_PER_DAY


def build_buckets(start, end, size_ms):
    """Build contiguous ``[start, end)`` buckets; the last is truncated at end.

    Always returns at least one bucket (a degenerate ``[start, end]`` window
    yields a single bucket). ``size_ms`` must be positive.
    """
    start = int(start)
    end = int(end)
    size_ms = int(size_ms)
    if size_ms <= 0 or end <= start:
        return [{"start": start, "end": end}]
    out = []
    cursor = start
    while cursor < end:
        stop = min(cursor + size_ms, end)
        out.append({"start": cursor, "end": stop})
        cursor = stop
    return out


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def _empty_series_entry(bucket):
    return {
        "start": bucket["start"],
        "end": bucket["end"],
        "requests": 0,
        "tokens": {
            "total": 0,
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
        },
        "cost": 0.0,
    }


def aggregate_timeline(rows, buckets, tz=None):
    """Bucket rows into ``buckets`` and sum requests/tokens/cost per bucket.

    A row belongs to the first bucket whose ``[start, end)`` contains its
    ``ts`` (epoch ms); out-of-range rows are ignored. Every bucket is emitted,
    empty or not. ``tz`` is accepted for signature symmetry; bucket membership
    is decided on absolute epoch milliseconds, so it does not affect results.
    """
    starts = [b["start"] for b in buckets]
    series = [_empty_series_entry(b) for b in buckets]
    for r in rows:
        try:
            ts = int(r.get("ts"))
        except (TypeError, ValueError):
            continue
        i = bisect.bisect_right(starts, ts) - 1
        if i < 0:
            continue
        bucket = buckets[i]
        if ts >= bucket["end"]:
            continue
        inp, out, cache_read, cache_write = observability._token_parts(r)
        inp = _clean_num(inp)
        out = _clean_num(out)
        cache_read = _clean_num(cache_read)
        cache_write = _clean_num(cache_write)
        cost = _clean_num(r.get("cost"))
        entry = series[i]
        entry["requests"] += 1
        tok = entry["tokens"]
        tok["input"] += inp
        tok["output"] += out
        tok["cache_read"] += cache_read
        tok["cache_write"] += cache_write
        tok["total"] = (tok["input"] + tok["output"]
                        + tok["cache_read"] + tok["cache_write"])
        entry["cost"] += cost
    return series


def build_timeline(rows, range, cutoff, now_ms, earliest_ms, tz=None):
    """Convenience composition: plan -> buckets -> summed series.

    Returns ``(plan, buckets, series)`` so callers can surface the bucket
    metadata alongside the series without re-deriving it.
    """
    plan = bucket_plan(range, cutoff, now_ms, earliest_ms, tz)
    buckets = build_buckets(plan["start"], plan["end"], plan["size_ms"])
    series = aggregate_timeline(rows, buckets, tz)
    return plan, buckets, series
