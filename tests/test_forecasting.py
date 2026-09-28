"""Phase 4: pure forecasting-engine tests (deterministic, explainable).

No DB, no network: forecasting.py is pure. Every builder output is also run
through ``json.dumps(..., allow_nan=False)`` to prove no Infinity/NaN escapes.
"""
import json

import forecasting as fc

H = 3600000
D = 86400000


def _ok(obj):
    """Assert the value is JSON-serializable with NaN/Infinity forbidden."""
    json.dumps(obj, allow_nan=False)


def _recs(*pairs):
    return [{"ts": ts, "usage": usage} for ts, usage in pairs]


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
def test_constants_are_named_and_complete():
    assert fc.SESSION_WINDOWS == {"last_30m": 1800000, "last_60m": 3600000,
                                  "window_avg": 18000000}
    assert fc.WEEKLY_WINDOWS == {"last_24h": 86400000, "last_3d": 259200000,
                                 "last_7d": 604800000}
    assert fc.PERIOD_WINDOWS == {"daily_average": None,
                                 "recent_3d_average": 259200000,
                                 "recent_7d_average": 604800000}
    assert set(fc.SAMPLING) == set(fc.SESSION_WINDOWS) | set(fc.WEEKLY_WINDOWS) | set(fc.PERIOD_WINDOWS)
    for spec in fc.SAMPLING.values():
        assert spec["min_coverage_ms"] > 0
        assert spec["min_points"] >= 2


# --------------------------------------------------------------------------
# parse_reset_text
# --------------------------------------------------------------------------
def test_parse_reset_text_hours_minutes():
    assert fc.parse_reset_text("5 hours 0 minutes") == 5 * H


def test_parse_reset_text_days_hours():
    assert fc.parse_reset_text("2 days 3 hours") == 2 * D + 3 * H


def test_parse_reset_text_minutes():
    assert fc.parse_reset_text("45 minutes") == 45 * 60000


def test_parse_reset_text_now():
    assert fc.parse_reset_text("now") == 0


def test_parse_reset_text_invalid_and_empty():
    assert fc.parse_reset_text("") is None
    assert fc.parse_reset_text(None) is None
    assert fc.parse_reset_text("soon-ish") is None
    assert fc.parse_reset_text("0 minutes") == 0


# --------------------------------------------------------------------------
# compute_rate
# --------------------------------------------------------------------------
def test_rate_zero_usage_is_zero_not_infinite():
    r = fc.compute_rate(_recs((0, 0.0), (H, 0.0)), 0, H, 600000, 2)
    assert r["status"] == "no_usage"
    assert r["usage_per_hour"] == 0.0
    assert r["usage_per_day"] == 0.0
    _ok(r)


def test_rate_empty_records_no_usage():
    r = fc.compute_rate([], 0, H, 600000, 2)
    assert r["status"] == "no_usage"
    assert r["usage_per_hour"] == 0.0
    assert r["sample_points"] == 0
    assert r["first_ts"] is None and r["last_ts"] is None


def test_rate_single_point_insufficient():
    r = fc.compute_rate(_recs((0, 1.0)), 0, H, 600000, 2)
    assert r["status"] == "insufficient_data"
    assert r["usage_per_hour"] is None
    assert r["usage_per_day"] is None


def test_rate_insufficient_duration():
    r = fc.compute_rate(_recs((3200000, 1.0), (3300000, 1.0)), 0, H, 600000, 2)
    assert r["status"] == "insufficient_data"
    assert r["usage_per_hour"] is None


def test_rate_sufficient_duration_and_multiple_records():
    r = fc.compute_rate(_recs((0, 1.0), (1800000, 2.0), (H, 3.0)), 0, H, 600000, 2)
    assert r["status"] == "ok"
    assert r["usage"] == 6.0
    assert r["sample_points"] == 3
    assert r["usage_per_hour"] == 6.0
    assert r["usage_per_day"] == 144.0
    assert r["first_ts"] == 0 and r["last_ts"] == H


def test_rate_window_clipping_uses_first_point():
    # coverage runs from the first in-window point, not from window start
    r = fc.compute_rate(_recs((600000, 1.0), (1800000, 1.0)), 0, H, 600000, 2)
    assert r["status"] == "ok"
    assert r["first_ts"] == 600000
    assert r["usage_per_hour"] == 2.0 / (3000000 / H)


def test_rate_records_outside_window_ignored():
    r = fc.compute_rate(
        _recs((-1, 100.0), (0, 1.0), (1800000, 1.0), (H + 1, 100.0)),
        0, H, 600000, 2)
    assert r["usage"] == 2.0
    assert r["sample_points"] == 2


def test_rate_window_ms_defaults_to_span():
    r = fc.compute_rate(_recs((0, 1.0), (H, 1.0)), 0, H, 600000, 2)
    assert r["window_ms"] == H


def test_rate_status_transitions():
    base = _recs((0, 1.0), (H, 1.0))
    assert fc.compute_rate(base, 0, H, 600000, 2)["status"] == "ok"
    assert fc.compute_rate(base, 0, H, H + 1, 2)["status"] == "insufficient_data"
    assert fc.compute_rate(base, 0, H, 600000, 3)["status"] == "insufficient_data"
    assert fc.compute_rate(_recs((0, 0.0), (H, 0.0)), 0, H, 600000, 2)["status"] == "no_usage"


# --------------------------------------------------------------------------
# compute_time_to_limit
# --------------------------------------------------------------------------
def test_ttl_remaining_none_unavailable():
    out = fc.compute_time_to_limit(1000, None, 2000, 5)
    assert out["status"] == "unavailable"
    assert out["time_to_limit_hours"] is None
    assert out["raw_time_to_limit_hours"] is None
    assert out["estimated_limit_at"] is None
    assert out["reset_at"] is None


def test_ttl_remaining_zero_already_at_limit():
    out = fc.compute_time_to_limit(1000, 0, 2000, 5)
    assert out["status"] == "already_at_limit"
    assert out["time_to_limit_hours"] == 0.0
    assert out["estimated_limit_at"] == 1000
    assert out["raw_time_to_limit_hours"] == 0.0


def test_ttl_zero_burn_no_usage_never_infinite():
    out = fc.compute_time_to_limit(1000, 10, 1000 + H, 0)
    assert out["status"] == "no_usage"
    assert out["time_to_limit_hours"] is None
    assert out["raw_time_to_limit_hours"] is None
    _ok(out)


def test_ttl_none_burn_insufficient_data():
    out = fc.compute_time_to_limit(1000, 10, 1000 + H, None)
    assert out["status"] == "insufficient_data"
    assert out["time_to_limit_hours"] is None


def test_ttl_limit_before_reset():
    now = 1000
    out = fc.compute_time_to_limit(now, 10, now + H, 20)  # ttl = 0.5h
    assert out["status"] == "limit_before_reset"
    assert out["time_to_limit_hours"] == 0.5
    assert out["estimated_limit_at"] == now + 0.5 * H
    assert out["reset_at"] == now + H


def test_ttl_reset_before_limit():
    now = 1000
    out = fc.compute_time_to_limit(now, 10, now + H, 5)  # ttl = 2h > reset
    assert out["status"] == "reset_before_limit"
    assert out["time_to_limit_hours"] is None
    assert out["estimated_limit_at"] is None
    assert out["raw_time_to_limit_hours"] == 2.0


def test_ttl_exactly_at_reset_is_reset_before_limit():
    now = 1000
    out = fc.compute_time_to_limit(now, 10, now + 2 * H, 5)  # limit_at == reset
    assert out["status"] == "reset_before_limit"
    assert out["time_to_limit_hours"] is None


def test_ttl_reset_unknown_when_missing():
    out = fc.compute_time_to_limit(1000, 10, None, 5)
    assert out["status"] == "reset_unknown"
    assert out["time_to_limit_hours"] is None
    assert out["raw_time_to_limit_hours"] == 2.0
    assert out["reset_at"] is None


def test_ttl_reset_unknown_when_invalid():
    out = fc.compute_time_to_limit(1000, 10, "not-a-time", 5)
    assert out["status"] == "reset_unknown"
    assert out["estimated_limit_at"] is None


# --------------------------------------------------------------------------
# compute_projection
# --------------------------------------------------------------------------
def test_projection_positive_rate_below_limit():
    out = fc.compute_projection(0, H, 10.0, 10.0, 60.0)
    assert out["status"] == "ok"
    assert out["projected_usage_at_end"] == 20.0
    assert out["projected_remaining_at_end"] == 40.0
    assert out["would_exceed_limit"] is False


def test_projection_zero_rate_no_usage():
    out = fc.compute_projection(0, H, 0.0, 10.0, 60.0)
    assert out["status"] == "no_usage"
    assert out["projected_usage_at_end"] == 10.0
    assert out["would_exceed_limit"] is False


def test_projection_end_soon_is_now():
    out = fc.compute_projection(5000, 5000, 100.0, 10.0, 60.0)
    assert out["projected_usage_at_end"] == 10.0
    assert out["projected_remaining_at_end"] == 50.0


def test_projection_above_limit_true():
    out = fc.compute_projection(0, H, 100.0, 10.0, 60.0)
    assert out["would_exceed_limit"] is True


def test_projection_remaining_can_go_negative_without_clamping():
    out = fc.compute_projection(0, H, 100.0, 10.0, 60.0)
    assert out["projected_remaining_at_end"] == -50.0


def test_projection_insufficient_data():
    assert fc.compute_projection(0, H, None, 10.0, 60.0)["status"] == "insufficient_data"
    assert fc.compute_projection(0, H, 10.0, None, 60.0)["status"] == "insufficient_data"
    assert fc.compute_projection(0, None, 10.0, 10.0, 60.0)["status"] == "insufficient_data"


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------
OFFICIAL = {"status": "ok", "used": 3.0, "remaining": 9.0, "limit": 12.0,
            "reset_at": 1000 + 2 * H, "reset_source": "official_reset_text"}


def test_build_session_forecast_structure():
    out = fc.build_session_forecast(
        _recs((1000 - 600000, 1.0), (1000 - 300000, 1.0)), 1000, OFFICIAL)
    assert out["official"] == OFFICIAL
    assert set(out["rates"]) == set(fc.SESSION_WINDOWS)
    assert set(out["estimates"]) == set(fc.SESSION_WINDOWS)
    for est in out["estimates"].values():
        assert "status" in est
    _ok(out)


def test_build_weekly_forecast_has_projection_fields():
    out = fc.build_weekly_forecast([], 1000, OFFICIAL)
    assert set(out["rates"]) == set(fc.WEEKLY_WINDOWS)
    for est in out["estimates"].values():
        assert "projected_usage_at_reset" in est
        assert "projected_remaining_at_reset" in est
        assert "would_exceed_limit" in est
    _ok(out)


def test_build_weekly_forecast_reset_none_projection_insufficient():
    official = dict(OFFICIAL, reset_at=None)
    out = fc.build_weekly_forecast([], 1000, official)
    for est in out["estimates"].values():
        assert est["projected_usage_at_reset"] is None
        assert est["would_exceed_limit"] is False
    _ok(out)


def test_build_period_forecast_daily_average_window_none():
    out = fc.build_period_forecast([], 1000, OFFICIAL)
    assert set(out["rates"]) == set(fc.PERIOD_WINDOWS)
    assert out["rates"]["daily_average"]["window_ms"] is None
    assert out["rates"]["recent_3d_average"]["window_ms"] == 259200000
    _ok(out)


def test_build_forecast_type_and_basis():
    out = fc.build_forecast([], 1000, OFFICIAL, OFFICIAL, OFFICIAL)
    assert out["type"] == "estimate"
    assert out["basis"] == "official_quota_usage_rate"
    assert out["generated_at"] == 1000
    assert set(out) >= {"session", "weekly", "period"}
    assert "rates" in out["session"] and "estimates" in out["session"]
    _ok(out)


def test_build_forecast_unavailable_official_never_fabricates():
    unavail = {"status": "unavailable", "used": None, "remaining": None,
               "limit": 12.0, "reset_at": 5000,
               "reset_source": "local_window_estimate"}
    out = fc.build_forecast([], 1000, unavail, unavail, unavail)
    for section in ("session", "weekly", "period"):
        for est in out[section]["estimates"].values():
            assert est["status"] == "unavailable"
            assert est["time_to_limit_hours"] is None
    _ok(out)
