"""Phase-1 baseline: credit deductions (deduct_for / period_deduct_count / windows)."""
from datetime import datetime

import pytest

import helpers


def _dt_ms(gw, *args):
    return int(datetime(*args, tzinfo=gw.LOCAL_TZ).timestamp() * 1000)


# --------------------------------------------------------------------------
# deduct_for
# --------------------------------------------------------------------------
def test_deduct_for_basic(gw):
    assert gw.deduct_for(0) == 0
    assert gw.deduct_for(1) == 5.0
    assert gw.deduct_for(3) == 15.0
    assert gw.deduct_for() == 0


def test_deduct_for_uses_credit_per_applied(gw, monkeypatch):
    monkeypatch.setattr(gw, "CREDIT_PER_APPLIED", 7.5)
    assert gw.deduct_for(2) == 15.0


# --------------------------------------------------------------------------
# period_deduct_count
# --------------------------------------------------------------------------
def test_period_deduct_count_sums_entries_since_start(gw):
    start_ms = _dt_ms(gw, 2026, 9, 1, 0, 0)
    gw.save_config(
        {
            "credit_deductions": [
                {"date": "2026-08-15", "count": 2},  # before subscription start
                {"date": "2026-09-05", "count": 1},
                {"date": "2026-09-20", "count": 3},
                {"count": 5},                         # no date -> skipped
                {"date": "2026-09-25"},               # no count -> contributes 0
            ]
        }
    )
    assert gw.period_deduct_count(start_ms) == 4


def test_period_deduct_count_empty_or_absent(gw):
    assert gw.period_deduct_count(0) == 0  # config file absent
    gw.save_config({})
    assert gw.period_deduct_count(0) == 0
    gw.save_config({"credit_deductions": []})
    assert gw.period_deduct_count(0) == 0


# --------------------------------------------------------------------------
# build_windows deduction application
# --------------------------------------------------------------------------
def test_build_windows_applies_period_deduction(gw):
    ps = _dt_ms(gw, 2026, 9, 1, 0, 0)
    gw.save_config({"credit_deductions": [{"date": "2026-09-10", "count": 2}]})
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    ts = _dt_ms(gw, 2026, 9, 20, 12, 0)
    rows = [helpers.make_row(ts, "glm-5.2", cost=10.0, src="go")]

    w = {
        x["kind"]: x
        for x in gw.build_windows(rows, now_ms, applied_credits=0, period_start=ps)
    }
    monthly = w["monthly"]
    gross = 10.0 * gw.RATE_DEFAULT
    deduct = 2 * gw.CREDIT_PER_APPLIED

    assert monthly["gross_used"] == pytest.approx(gross)
    assert monthly["deduct"] == pytest.approx(deduct)
    assert monthly["used"] == pytest.approx(gross - deduct)
    assert monthly["pct"] == pytest.approx(
        min(100.0, (gross - deduct) / gw.LIMITS["monthly"] * 100)
    )


def test_build_windows_deduction_is_idempotent(gw):
    now_ms = _dt_ms(gw, 2026, 9, 27, 12, 0)
    ts = _dt_ms(gw, 2026, 9, 20, 12, 0)
    rows = [helpers.make_row(ts, "glm-5.2", cost=10.0, src="go")]

    first = gw.build_windows(rows, now_ms, applied_credits=2)
    second = gw.build_windows(rows, now_ms, applied_credits=2)
    assert first == second
