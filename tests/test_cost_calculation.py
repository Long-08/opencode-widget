"""Phase-1 baseline: gw.est_cost pricing and gw.model_stats bucketing.

Deterministic: gw.scan_free_models is stubbed to [] by the autouse fixture, so
model_stats performs no subprocess/network work.
"""
from datetime import datetime

import pytest

import helpers

MILLION = 1_000_000


def _tokens(input_=0, output_=0, cr=0, cw=0):
    return {"input": input_, "output": output_, "cache": {"read": cr, "write": cw}}


def _now_ms(gw):
    return int(datetime(2026, 9, 27, 12, 0, tzinfo=gw.LOCAL_TZ).timestamp() * 1000)


# --------------------------------------------------------------------------
# est_cost
# --------------------------------------------------------------------------
def test_est_cost_glm52_full_million(gw):
    # glm-5.2: in 1.40 + out 4.40 + cr 0.26 = 6.06 per million
    assert gw.est_cost("glm-5.2", _tokens(MILLION, MILLION, MILLION)) == pytest.approx(
        6.06, rel=1e-9
    )


def test_est_cost_mimo_v25_full_million(gw):
    # mimo-v2.5: 0.14 + 0.28 + 0.0028 = 0.4228 per million
    assert gw.est_cost("mimo-v2.5", _tokens(MILLION, MILLION, MILLION)) == pytest.approx(
        0.4228, rel=1e-9
    )


def test_est_cost_cache_write_only_when_present(gw):
    # minimax-m2.7 declares cw=0.375 -> cache writes are charged.
    assert gw.est_cost("minimax-m2.7", _tokens(0, 0, 0, MILLION)) == pytest.approx(
        0.375, rel=1e-9
    )
    # glm-5.2 declares cw=None -> cache writes contribute nothing.
    assert gw.est_cost("glm-5.2", _tokens(0, 0, 0, MILLION)) == pytest.approx(0.0)


def test_est_cost_high_context_pricing(gw):
    # gpt-5.6-luna: below threshold in=0.20/M; at/above 272000 uses in_hi=0.40/M.
    assert gw.est_cost("gpt-5.6-luna", _tokens(1000)) == pytest.approx(0.0002, rel=1e-9)
    assert gw.est_cost("gpt-5.6-luna", _tokens(300_000)) == pytest.approx(0.12, rel=1e-9)


def test_est_cost_unknown_and_free_model(gw):
    assert gw.est_cost("no-such-model", _tokens(MILLION)) is None
    # free model without a PRICES entry -> no estimate.
    assert gw.est_cost("hy3-free", _tokens(MILLION)) is None


def test_est_cost_missing_token_keys_tolerated(gw):
    assert gw.est_cost("glm-5.2", {}) == pytest.approx(0.0)
    # input only, no cache dict -> cache read/write contribute 0.
    assert gw.est_cost("glm-5.2", {"input": 1000}) == pytest.approx(0.0014, rel=1e-9)


# --------------------------------------------------------------------------
# model_stats
# --------------------------------------------------------------------------
def test_model_stats_separate_buckets_per_src(gw):
    now_ms = _now_ms(gw)
    ts = now_ms - 60_000
    rows = [
        helpers.make_row(ts, "hy3", cost=1.0, src="go"),
        helpers.make_row(ts, "hy3", cost=2.0, src="kilo"),
    ]
    out = {x["key"]: x for x in gw.model_stats(rows, now_ms)}
    assert set(out) == {"hy3|go", "hy3|kilo"}
    assert out["hy3|go"]["cost_total"] == pytest.approx(1.0)
    assert out["hy3|kilo"]["cost_total"] == pytest.approx(2.0)


def test_model_stats_group_free_vs_go(gw):
    now_ms = _now_ms(gw)
    ts = now_ms - 60_000
    rows = [
        helpers.make_row(ts, "hy3-free", cost=0.0, src="go"),   # free by suffix
        helpers.make_row(ts, "glm-5.2", cost=0.0, src="kilo"),  # src total cost <= 0 -> free
        helpers.make_row(ts, "glm-5.2", cost=2.0, src="go"),    # paid -> go
    ]
    out = {x["key"]: x for x in gw.model_stats(rows, now_ms)}
    assert out["hy3-free|go"]["group"] == "free"
    assert out["hy3-free|go"]["is_free"] is True
    assert out["glm-5.2|kilo"]["group"] == "free"
    assert out["glm-5.2|go"]["group"] == "go"
    assert out["glm-5.2|go"]["is_free"] is False


def test_model_stats_cost_map_only_overrides_go(gw):
    now_ms = _now_ms(gw)
    ts = now_ms - 60_000
    rows = [
        helpers.make_row(ts, "glm-5.2", cost=2.0, src="go"),
        helpers.make_row(ts, "glm-5.2", cost=3.0, src="kilo"),
    ]
    out = {x["key"]: x for x in gw.model_stats(rows, now_ms, cost_map={"glm-5.2": 99.0})}
    # Official cost_map replaces totals only for the go source.
    assert out["glm-5.2|go"]["cost_total"] == pytest.approx(99.0)
    assert out["glm-5.2|go"]["model_used"] == pytest.approx(99.0)
    # Other sources keep their locally aggregated cost.
    assert out["glm-5.2|kilo"]["cost_total"] == pytest.approx(3.0)
