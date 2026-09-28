"""Phase 5B: pure-function tests for timeline.py.

No DB, no network, no clock reads: every timestamp is a fixed epoch-ms value
and the timezone is injected, so all results are deterministic.
"""
import json
from datetime import datetime, timedelta, timezone

import helpers
import observability
import timeline


TZ = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------
# local helpers
# --------------------------------------------------------------------------
def _ms(y, m, d, hour=0, minute=0):
    return int(datetime(y, m, d, hour, minute, tzinfo=TZ).timestamp() * 1000)


def _tokens(input_=100, output_=50, cache_read=0, cache_write=0):
    return {
        "input": input_,
        "output": output_,
        "cache": {"read": cache_read, "write": cache_write},
        "reasoning": 0,
    }


def _row(ts, cost=0.0, tokens=None):
    return helpers.make_row(
        ts, "model-a", cost=cost,
        tokens=tokens if tokens is not None else _tokens(),
    )


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
def test_cost_basis_constant():
    assert timeline.COST_BASIS == "opencode_message_raw"
    assert timeline.COST_BASIS == observability.COST_BASIS


def test_nice_sizes_sorted_and_unique():
    sizes = list(timeline.NICE_SIZES)
    assert sizes == sorted(sizes)
    assert len(set(sizes)) == len(sizes)
    assert sizes[0] == 1800000
    assert sizes[-1] == 31536000000


# --------------------------------------------------------------------------
# bucket_plan: fixed range sizes
# --------------------------------------------------------------------------
def test_bucket_plan_today_is_30_minutes():
    now = _ms(2026, 9, 28, 12, 0)
    plan = timeline.bucket_plan("today", "2026-09-28", now, 0, TZ)
    assert plan["start"] == _ms(2026, 9, 28, 0, 0)
    assert plan["end"] == now
    assert plan["size_ms"] == 1800000
    assert plan["unit"] == "minute"


def test_bucket_plan_7d_is_3_hours():
    now = _ms(2026, 9, 28, 12, 0)
    plan = timeline.bucket_plan("7d", "2026-09-22", now, 0, TZ)
    assert plan["start"] == _ms(2026, 9, 22, 0, 0)
    assert plan["size_ms"] == 10800000
    assert plan["unit"] == "hour"


def test_bucket_plan_30d_is_1_day():
    now = _ms(2026, 9, 28, 12, 0)
    plan = timeline.bucket_plan("30d", "2026-08-30", now, 0, TZ)
    assert plan["start"] == _ms(2026, 8, 30, 0, 0)
    assert plan["size_ms"] == 86400000
    assert plan["unit"] == "day"


def test_bucket_plan_keeps_base_when_it_fits():
    now = _ms(2026, 9, 28, 12, 0)
    plan = timeline.bucket_plan("today", "2026-09-28", now, 0, TZ)
    # 12 h / 30 m = 24 buckets, well under MAX -> base preserved.
    assert plan["size_ms"] == 1800000


# --------------------------------------------------------------------------
# bucket_plan: "all" / auto / ladder / MAX_BUCKETS
# --------------------------------------------------------------------------
def test_bucket_plan_all_starts_at_earliest():
    earliest = _ms(2026, 9, 1, 0, 0)
    plan = timeline.bucket_plan("all", None, _ms(2026, 9, 28, 12, 0), earliest, TZ)
    assert plan["start"] == earliest
    assert plan["size_ms"] in timeline.NICE_SIZES
    assert plan["unit"] in ("minute", "hour", "day")


def test_bucket_plan_all_without_earliest_trailing_24h():
    now = _ms(2026, 9, 28, 12, 0)
    plan = timeline.bucket_plan("all", None, now, 0, TZ)
    assert plan["start"] == now - 86400000
    assert plan["end"] == now


def test_bucket_plan_upgrades_ladder_to_respect_max_buckets():
    start = _ms(2019, 1, 1, 0, 0)
    now = _ms(2026, 9, 28, 12, 0)  # ~7.7 years
    plan = timeline.bucket_plan("all", None, now, start, TZ)
    assert plan["size_ms"] > 1800000
    assert plan["size_ms"] in timeline.NICE_SIZES
    buckets = timeline.build_buckets(plan["start"], plan["end"], plan["size_ms"])
    assert len(buckets) <= timeline.MAX_BUCKETS


def test_bucket_plan_always_at_least_one_bucket():
    now = _ms(2026, 9, 28, 0, 0)  # exactly local midnight
    plan = timeline.bucket_plan("today", "2026-09-28", now, 0, TZ)
    buckets = timeline.build_buckets(plan["start"], plan["end"], plan["size_ms"])
    assert len(buckets) >= 1


def test_bucket_plan_invalid_range_is_treated_as_all():
    now = _ms(2026, 9, 28, 12, 0)
    earliest = _ms(2026, 9, 27, 12, 0)
    plan = timeline.bucket_plan("bogus", None, now, earliest, TZ)
    assert plan["start"] == earliest


def test_bucket_plan_local_midnight_uses_injected_tz_not_hardcoded():
    now = _ms(2026, 9, 28, 12, 0)
    east = timeline.bucket_plan("today", "2026-09-28", now, 0, TZ)
    assert east["start"] == _ms(2026, 9, 28, 0, 0)
    west_tz = timezone(timedelta(hours=-5))
    west_start = int(datetime(2026, 9, 28, tzinfo=west_tz).timestamp() * 1000)
    west = timeline.bucket_plan("today", "2026-09-28", now, 0, west_tz)
    assert west["start"] == west_start
    assert west["start"] != east["start"]


# --------------------------------------------------------------------------
# unit naming
# --------------------------------------------------------------------------
def test_unit_naming_thresholds():
    assert timeline._unit(1800000) == "minute"      # 30 m
    assert timeline._unit(3600000) == "hour"        # 1 h (not < 1 h)
    assert timeline._unit(43200000) == "hour"       # 12 h
    assert timeline._unit(86400000) == "day"        # 1 d (not < 1 d)
    assert timeline._unit(2592000000) == "day"      # 30 d


# --------------------------------------------------------------------------
# build_buckets
# --------------------------------------------------------------------------
def test_build_buckets_contiguous_half_open():
    assert timeline.build_buckets(1000, 4000, 1000) == [
        {"start": 1000, "end": 2000},
        {"start": 2000, "end": 3000},
        {"start": 3000, "end": 4000},
    ]


def test_build_buckets_truncates_last_at_end():
    buckets = timeline.build_buckets(0, 2500, 1000)
    assert [b["end"] for b in buckets] == [1000, 2000, 2500]
    assert buckets[-1] == {"start": 2000, "end": 2500}


def test_build_buckets_exact_multiple_has_no_extra_empty_bucket():
    buckets = timeline.build_buckets(0, 3000, 1000)
    assert len(buckets) == 3
    assert buckets[-1]["end"] == 3000


def test_build_buckets_degenerate_returns_one():
    assert timeline.build_buckets(500, 500, 1000) == [{"start": 500, "end": 500}]


# --------------------------------------------------------------------------
# aggregate_timeline
# --------------------------------------------------------------------------
def test_aggregate_emits_every_empty_bucket():
    buckets = timeline.build_buckets(0, 3000, 1000)
    series = timeline.aggregate_timeline([], buckets)
    assert len(series) == 3
    for entry in series:
        assert entry["requests"] == 0
        assert entry["cost"] == 0.0
        assert entry["tokens"] == {
            "total": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        }


def test_aggregate_sums_requests_tokens_and_cost():
    buckets = timeline.build_buckets(0, 3000, 1000)
    rows = [
        _row(100, cost=1.5, tokens=_tokens(10, 20, 5, 1)),
        _row(200, cost=2.5, tokens=_tokens(1, 2, 3, 4)),
        _row(2500, cost=0.5, tokens=_tokens(7, 8)),
    ]
    series = timeline.aggregate_timeline(rows, buckets)
    assert series[0]["requests"] == 2
    assert series[0]["cost"] == 4.0
    assert series[0]["tokens"] == {
        "total": 46, "input": 11, "output": 22, "cache_read": 8, "cache_write": 5,
    }
    assert series[1]["requests"] == 0
    assert series[2]["requests"] == 1
    assert series[2]["cost"] == 0.5
    assert series[2]["tokens"]["total"] == 15


def test_aggregate_ignores_out_of_range_rows():
    buckets = timeline.build_buckets(1000, 2000, 1000)
    series = timeline.aggregate_timeline([_row(999), _row(2000), _row(5000)], buckets)
    assert series[0]["requests"] == 0


def test_aggregate_bucket_membership_start_inclusive_end_exclusive():
    buckets = timeline.build_buckets(0, 2000, 1000)
    series = timeline.aggregate_timeline([_row(999), _row(1000), _row(2000)], buckets)
    assert series[0]["requests"] == 1   # 999
    assert series[1]["requests"] == 1   # 1000; 2000 is outside the series


def test_aggregate_output_is_json_safe_no_nan_infinity():
    buckets = timeline.build_buckets(0, 2000, 1000)
    rows = [
        _row(100, cost=float("nan")),
        _row(200, cost=float("inf")),
        _row(300, cost=1.25),
        _row(1500, tokens=_tokens(float("nan"), 1)),
    ]
    series = timeline.aggregate_timeline(rows, buckets)
    text = json.dumps({"series": series}, allow_nan=False)
    assert "NaN" not in text
    assert "Infinity" not in text
    assert series[0]["cost"] == 1.25


def test_aggregate_is_deterministic_with_fixed_epoch_ms():
    buckets = timeline.build_buckets(0, 2000, 1000)
    rows = [_row(100, cost=1.0), _row(1500, cost=2.0)]
    assert (timeline.aggregate_timeline(rows, buckets)
            == timeline.aggregate_timeline(rows, buckets))


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------
def test_build_timeline_plan_buckets_and_count_consistency():
    now = _ms(2026, 9, 28, 12, 0)
    for rng, cutoff in (("today", "2026-09-28"), ("7d", "2026-09-22"),
                        ("30d", "2026-08-30")):
        plan, buckets, series = timeline.build_timeline([], rng, cutoff, now, 0, TZ)
        assert len(series) == len(buckets)
        assert len(buckets) <= timeline.MAX_BUCKETS
        assert all(buckets[i]["end"] == buckets[i + 1]["start"]
                   for i in range(len(buckets) - 1))
        assert len(buckets) >= 1
