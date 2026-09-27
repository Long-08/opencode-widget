"""Phase-1 baseline: week/month bounds, build_windows, model_history day buckets.

All timestamps are built from datetime objects bound to gw.LOCAL_TZ so the tests
are timezone-portable (no hardcoded epoch offsets).
"""
from datetime import datetime, timedelta

import pytest

import helpers


def _dt_ms(gw, *args):
    return int(datetime(*args, tzinfo=gw.LOCAL_TZ).timestamp() * 1000)


# --------------------------------------------------------------------------
# week_bounds
# --------------------------------------------------------------------------
def test_week_bounds_starts_monday_local(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 15, 30)  # Sunday
    ws, we = gw.week_bounds(now_ms)

    d = datetime.fromtimestamp(now_ms / 1000, gw.LOCAL_TZ)
    exp_start = datetime(d.year, d.month, d.day, tzinfo=gw.LOCAL_TZ) - timedelta(
        days=d.weekday()
    )
    exp_end = exp_start + timedelta(days=7)

    assert exp_start.weekday() == 0
    assert ws == int(exp_start.timestamp() * 1000)
    assert we == int(exp_end.timestamp() * 1000)
    assert we - ws == 7 * 24 * 3600 * 1000


# --------------------------------------------------------------------------
# month_bounds
# --------------------------------------------------------------------------
def test_month_bounds_anchored_to_subscription(gw):
    sub_ms = _dt_ms(gw, 2026, 8, 15, 9, 30)
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    ms, me = gw.month_bounds(now_ms, sub_ms)

    assert ms == _dt_ms(gw, 2026, 9, 15, 9, 30)
    assert me == _dt_ms(gw, 2026, 10, 15, 9, 30)


def test_month_bounds_day_overflow_clamps(gw):
    # Subscribe on the 31st; the current month (April) has only 30 days, so the
    # anchored start clamps to the last day of the month.
    sub_ms = _dt_ms(gw, 2026, 1, 31, 9, 30)
    now_ms = _dt_ms(gw, 2026, 4, 10, 12, 0)
    ms, me = gw.month_bounds(now_ms, sub_ms)

    assert ms == _dt_ms(gw, 2026, 3, 31, 9, 30)
    assert me == _dt_ms(gw, 2026, 4, 30, 9, 30)


# --------------------------------------------------------------------------
# build_windows
# --------------------------------------------------------------------------
def test_build_windows_session_boundaries(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    s_start = now_ms - gw.SESSION_MS
    rows = [
        helpers.make_row(s_start, "glm-5.2", cost=1.0, src="go"),      # exactly at start
        helpers.make_row(s_start - 1, "glm-5.2", cost=100.0, src="go"),  # just outside
    ]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms)}
    gross = gw.RATE_INTERCEPT + 1.0 * gw.RATE_DEFAULT
    assert w["session"]["gross_used"] == pytest.approx(gross)
    assert w["session"]["used"] == pytest.approx(gross)  # no deductions applied


def test_build_windows_weekly_boundaries(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    ws, we = gw.week_bounds(now_ms)
    rows = [
        helpers.make_row(ws, "glm-5.2", cost=1.0, src="go"),       # at week start
        helpers.make_row(ws - 1, "glm-5.2", cost=100.0, src="go"),  # previous week
        helpers.make_row(we, "glm-5.2", cost=100.0, src="go"),      # next week (end exclusive)
    ]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms)}
    assert w["weekly"]["gross_used"] == pytest.approx(
        gw.RATE_INTERCEPT + 1.0 * gw.RATE_DEFAULT
    )


def test_build_windows_monthly_anchored_start(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    sub_ms = _dt_ms(gw, 2026, 8, 15, 9, 30)
    ms, _me = gw.month_bounds(now_ms, sub_ms)
    rows = [
        helpers.make_row(sub_ms, "glm-5.2", cost=1.0, src="go"),   # before month start
        helpers.make_row(ms, "glm-5.2", cost=1.0, src="go"),       # at start -> counted
        helpers.make_row(ms - 1, "glm-5.2", cost=100.0, src="go"),  # just outside
    ]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms)}
    assert w["monthly"]["gross_used"] == pytest.approx(
        gw.RATE_INTERCEPT + 1.0 * gw.RATE_DEFAULT
    )


def test_build_windows_excludes_gateway_and_other_account(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    rows = [
        helpers.make_row(now_ms - 1000, "glm-5.2", cost=5.0, src="gateway"),
        helpers.make_row(now_ms - 2000, "glm-5.2", cost=6.0, src="go", account="other"),
    ]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms)}
    assert w["session"]["gross_used"] == pytest.approx(gw.RATE_INTERCEPT)


def test_build_windows_rate_zen_is_one_go_uses_rate_for(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    rows = [
        helpers.make_row(now_ms - 1000, "glm-5.2", cost=1.0, src="zen"),
        helpers.make_row(now_ms - 2000, "glm-5.2", cost=1.0, src="go"),
    ]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms)}
    assert w["session"]["gross_used"] == pytest.approx(
        gw.RATE_INTERCEPT + 1.0 + 1.0 * gw.RATE_DEFAULT
    )


def test_build_windows_adds_rate_intercept(gw, monkeypatch):
    monkeypatch.setattr(gw, "RATE_INTERCEPT", 0.5)
    monkeypatch.setattr(gw, "RATE_DEFAULT", 2.0)
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    rows = [helpers.make_row(now_ms - 1000, "glm-5.2", cost=1.0, src="go")]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms)}
    assert w["session"]["gross_used"] == pytest.approx(2.5)


def test_build_windows_used_never_negative(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    rows = [helpers.make_row(now_ms - 1000, "glm-5.2", cost=1.0, src="go")]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms, applied_credits=100)}
    s = w["session"]
    assert s["gross_used"] == pytest.approx(gw.RATE_INTERCEPT + gw.RATE_DEFAULT)
    assert s["deduct"] == pytest.approx(500.0)
    assert s["used"] == 0.0
    assert s["pct"] == 0.0


def test_build_windows_period_start_overrides_month_window(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    sub_ms = _dt_ms(gw, 2026, 8, 15, 9, 30)
    ps = _dt_ms(gw, 2026, 9, 20, 0, 0)
    # Sanity: the anchor derived from the earliest paid row precedes period_start.
    default_ms, _me = gw.month_bounds(now_ms, sub_ms)
    assert ps > default_ms

    rows = [
        helpers.make_row(sub_ms, "glm-5.2", cost=1.0, src="go"),
        helpers.make_row(_dt_ms(gw, 2026, 9, 18, 12, 0), "glm-5.2", cost=1.0, src="go"),
        helpers.make_row(_dt_ms(gw, 2026, 9, 22, 12, 0), "glm-5.2", cost=1.0, src="go"),
    ]
    w = {x["kind"]: x for x in gw.build_windows(rows, now_ms, period_start=ps)}
    # Only the post-period_start row falls in the monthly window.
    assert w["monthly"]["gross_used"] == pytest.approx(
        gw.RATE_INTERCEPT + 1.0 * gw.RATE_DEFAULT
    )


# --------------------------------------------------------------------------
# model_history day bucketing
# --------------------------------------------------------------------------
def test_model_history_splits_on_local_midnight(gw):
    midnight = datetime.now(gw.LOCAL_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    ts_before = int((midnight - timedelta(seconds=30)).timestamp() * 1000)
    ts_after = int((midnight + timedelta(seconds=30)).timestamp() * 1000)
    rows = [
        helpers.make_row(ts_before, "glm-5.2", cost=2.0, src="go"),
        helpers.make_row(ts_after, "glm-5.2", cost=1.0, src="go"),
    ]

    out = gw.model_history(rows)
    assert len(out) == 1
    series = {p["date"]: p for p in out[0]["series"]}

    yesterday = (midnight - timedelta(days=1)).date().isoformat()
    today = midnight.date().isoformat()
    assert set(series) == {yesterday, today}
    assert series[today]["cost"] == pytest.approx(1.0 * gw.RATE_DEFAULT, rel=1e-6)
    assert series[yesterday]["cost"] == pytest.approx(2.0 * gw.RATE_DEFAULT, rel=1e-6)
