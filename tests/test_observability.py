"""Phase 3: pure-function tests for observability.py.

No DB, no network: rows are built in-memory with helpers.make_row.
"""
import json
import threading
from datetime import datetime

import helpers
import observability


# --------------------------------------------------------------------------
# local helpers
# --------------------------------------------------------------------------
def _tokens(input_=100, output_=50, cache_read=0, cache_write=0):
    return {
        "input": input_,
        "output": output_,
        "cache": {"read": cache_read, "write": cache_write},
        "reasoning": 0,
    }


def _row(ts, agent=None, model="model-a", cost=0.0, src="go",
         session_id=None, parent_session_id=None, provider_id="opencode-go",
         variant="high", dur_ms=None, tokens=None, **extra):
    row = helpers.make_row(
        ts, model, cost=cost, src=src,
        tokens=tokens if tokens is not None else _tokens(),
        agent=agent, session_id=session_id,
        parent_session_id=parent_session_id, provider_id=provider_id,
        variant=variant, dur_ms=dur_ms,
    )
    row.update(extra)
    return row


def _day_ts(gw, y, m, d, hour=12):
    return int(datetime(y, m, d, hour, tzinfo=gw.LOCAL_TZ).timestamp() * 1000)


def _run_with_timeout(fn, timeout=5.0):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except Exception as e:  # pragma: no cover - surfaced below
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "build_session_tree did not terminate"
    if "error" in box:
        raise box["error"]
    return box.get("result")


# --------------------------------------------------------------------------
# filter_rows
# --------------------------------------------------------------------------
def test_filter_rows_none_returns_all_and_copy(gw):
    rows = [_row(1000), _row(2000)]
    out = observability.filter_rows(rows, None, gw.LOCAL_TZ)
    assert out == rows
    assert out is not rows


def test_filter_rows_local_day_boundary(gw):
    on_cut = _day_ts(gw, 2026, 1, 15, 0)
    before = on_cut - 1
    after = _day_ts(gw, 2026, 1, 16, 0)
    rows = [_row(before), _row(on_cut), _row(after)]
    kept = observability.filter_rows(rows, "2026-01-15", gw.LOCAL_TZ)
    assert [r["ts"] for r in kept] == [on_cut, after]


def test_filter_rows_after_cutoff_excluded(gw):
    rows = [_row(_day_ts(gw, 2026, 1, 14))]
    assert observability.filter_rows(rows, "2026-01-15", gw.LOCAL_TZ) == []


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------
def test_arbitrary_agent_name_preserved():
    rows = [_row(1000, agent="foo-agent-2027", cost=1.0)]
    agents = observability.aggregate_agents(rows)
    assert [a["agent"] for a in agents] == ["foo-agent-2027"]


def test_unknown_agent_bucket_for_missing_agent():
    rows = [_row(1000, agent=None, cost=1.0)]
    agents = observability.aggregate_agents(rows)
    assert agents[0]["agent"] == "unknown"


def test_group_mapping_returns_raw_agent_and_group():
    rows = [_row(1000, agent="build", cost=1.0)]
    agents = observability.aggregate_agents(rows, agent_groups={"build": "Main"})
    assert agents[0]["agent"] == "build"
    assert agents[0]["group"] == "Main"


def test_unmapped_group_is_none_not_unmapped():
    rows = [_row(1000, agent="oracle", cost=1.0)]
    agents = observability.aggregate_agents(rows, agent_groups={"build": "Main"})
    assert agents[0]["group"] is None
    assert agents[0]["group"] != "unmapped"
    # agent_groups not configured at all -> None too
    agents2 = observability.aggregate_agents(rows)
    assert agents2[0]["group"] is None


def test_agent_requests_sessions_and_tokens_total():
    rows = [
        _row(1000, agent="build", session_id="s1", cost=1.0,
             tokens=_tokens(input_=10, output_=20, cache_read=30, cache_write=40)),
        _row(2000, agent="build", session_id="s2", cost=2.0, tokens=_tokens()),
        _row(3000, agent="build", session_id="s2", cost=3.0, tokens=_tokens()),
    ]
    a = observability.aggregate_agents(rows)[0]
    assert a["requests"] == 3
    assert a["sessions"] == 2
    assert a["tokens"]["total"] == 10 + 20 + 30 + 40 + 150 + 150
    assert a["tokens"]["cache_read"] == 30
    assert a["tokens"]["cache_write"] == 40


def test_agent_models_shares_sum_to_one():
    rows = [
        _row(1000, agent="build", model="m-a", cost=1.0, tokens=_tokens(input_=100)),
        _row(2000, agent="build", model="m-b", cost=3.0, tokens=_tokens(input_=300)),
    ]
    a = observability.aggregate_agents(rows)[0]
    assert sum(m["request_share"] for m in a["models"]) == 1.0
    assert abs(sum(m["token_share"] for m in a["models"]) - 1.0) < 1e-9
    assert abs(sum(m["cost_share"] for m in a["models"]) - 1.0) < 1e-9
    # sorted by cost desc
    assert [m["model"] for m in a["models"]] == ["m-b", "m-a"]
    by = {m["model"]: m for m in a["models"]}
    assert by["m-b"]["cost_share"] == 0.75


def test_agent_model_shares_zero_denominator():
    rows = [
        _row(1000, agent="build", model="m-a", cost=0.0,
             tokens=_tokens(input_=0, output_=0)),
        _row(2000, agent="build", model="m-b", cost=0.0,
             tokens=_tokens(input_=0, output_=0)),
    ]
    a = observability.aggregate_agents(rows)[0]
    for m in a["models"]:
        assert m["cost_share"] == 0.0
        assert m["token_share"] == 0.0


def test_agent_model_provider_raw_and_ordered():
    rows = [
        _row(1000, agent="build", model="m-a", provider_id="opencode-go", cost=1.0),
        _row(2000, agent="build", model="m-a", provider_id="openrouter", cost=5.0),
    ]
    a = observability.aggregate_agents(rows)[0]
    # two provider buckets for the same model name -> two model entries
    assert {m["provider"] for m in a["models"]} == {"opencode-go", "openrouter"}
    assert a["models"][0]["provider"] == "openrouter"
    assert {p["provider_id"] for p in a["providers"]} == {"opencode-go", "openrouter"}
    assert a["providers"][0]["provider_id"] == "openrouter"


def test_agents_sorted_by_cost_desc():
    rows = [
        _row(1000, agent="cheap", cost=1.0),
        _row(2000, agent="pricey", cost=9.0),
    ]
    assert [a["agent"] for a in observability.aggregate_agents(rows)] == ["pricey", "cheap"]


# --------------------------------------------------------------------------
# cache_read_ratio
# --------------------------------------------------------------------------
def test_cache_read_ratio_formula():
    rows = [_row(1000, agent="build", tokens=_tokens(input_=100, cache_read=100))]
    a = observability.aggregate_agents(rows)[0]
    assert a["cache_read_ratio"] == 0.5


def test_cache_read_ratio_zero_denominator_none():
    rows = [_row(1000, agent="build", tokens=_tokens(input_=0, output_=5, cache_read=0))]
    a = observability.aggregate_agents(rows)[0]
    assert a["cache_read_ratio"] is None


# --------------------------------------------------------------------------
# averages / duration
# --------------------------------------------------------------------------
def test_average_metrics_normal_values():
    rows = [
        _row(1000, agent="build", cost=2.0, dur_ms=100, tokens=_tokens(input_=100, output_=0)),
        _row(2000, agent="build", cost=4.0, dur_ms=300, tokens=_tokens(input_=200, output_=0)),
    ]
    a = observability.aggregate_agents(rows)[0]
    assert a["duration"]["total_ms"] == 400
    assert a["duration"]["avg_ms"] == 200
    assert a["avg_duration_per_request"] == 200
    assert a["avg_tokens_per_request"] == 150
    assert a["avg_cost_per_request"] == 3.0


def test_average_duration_zero_denominator_none():
    rows = [_row(1000, agent="build", cost=2.0, dur_ms=None)]
    a = observability.aggregate_agents(rows)[0]
    assert a["duration"]["total_ms"] == 0
    assert a["duration"]["avg_ms"] is None
    assert a["avg_duration_per_request"] is None


def test_agent_duration_only_counts_rows_with_dur():
    rows = [
        _row(1000, agent="build", dur_ms=100),
        _row(2000, agent="build", dur_ms=None),
        _row(3000, agent="build", dur_ms=300),
    ]
    a = observability.aggregate_agents(rows)[0]
    assert a["duration"]["total_ms"] == 400
    assert a["duration"]["avg_ms"] == 200


# --------------------------------------------------------------------------
# models (reverse aggregation)
# --------------------------------------------------------------------------
def test_aggregate_models_reverse_agents():
    rows = [
        _row(1000, agent="build", model="m-a", cost=1.0),
        _row(2000, agent="build", model="m-a", cost=1.0),
        _row(3000, agent="review", model="m-a", cost=5.0),
        _row(4000, agent="review", model="m-b", cost=2.0),
    ]
    models = observability.aggregate_models(rows)
    assert [m["model"] for m in models] == ["m-a", "m-b"]
    ma = models[0]
    assert ma["requests"] == 3
    assert ma["sessions"] == 0  # no session_id set
    assert {a["agent"] for a in ma["agents"]} == {"build", "review"}
    assert ma["agents"][0]["agent"] == "review"  # sorted by cost desc
    assert ma["source"] == "go"
    assert ma["provider_id"] == "opencode-go"


def test_aggregate_models_group_applied():
    rows = [_row(1000, agent="build", model="m-a", cost=1.0)]
    models = observability.aggregate_models(rows, agent_groups={"build": "Main"})
    assert models[0]["agents"][0]["group"] == "Main"


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
def test_aggregate_providers_fields_and_display_name():
    rows = [
        _row(1000, agent="build", provider_id="opencode-go", src="go", cost=1.0),
        _row(2000, agent="review", provider_id="weird-provider", src="weird", cost=3.0),
    ]
    providers = observability.aggregate_providers(
        rows, supplier_names={"go": "OpenCode Go"})
    assert [p["provider_id"] for p in providers] == ["weird-provider", "opencode-go"]
    by = {p["provider_id"]: p for p in providers}
    assert by["opencode-go"]["display_name"] == "OpenCode Go"
    assert by["weird-provider"]["display_name"] == "weird"
    assert by["opencode-go"]["source"] == "go"


def test_aggregate_providers_nested_agents_and_models():
    rows = [
        _row(1000, agent="build", model="m-a", provider_id="opencode-go", cost=1.0),
        _row(2000, agent="review", model="m-b", provider_id="opencode-go", cost=4.0),
    ]
    p = observability.aggregate_providers(rows)[0]
    assert {a["agent"] for a in p["agents"]} == {"build", "review"}
    assert p["agents"][0]["agent"] == "review"
    assert {m["model"] for m in p["models"]} == {"m-a", "m-b"}
    assert p["models"][0]["model"] == "m-b"
    assert p["sessions"] == 0


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
def test_sessions_own_rows_only_parent_excludes_child():
    rows = [
        _row(1000, agent="build", session_id="parent", cost=5.0),
        _row(2000, agent="build", session_id="child",
             parent_session_id="parent", cost=3.0),
    ]
    sessions = observability.aggregate_sessions(rows)
    by = {s["session_id"]: s for s in sessions}
    assert by["parent"]["cost"] == 5.0
    assert by["child"]["cost"] == 3.0
    assert by["parent"]["parent_session_id"] is None
    assert by["child"]["parent_session_id"] == "parent"


def test_session_start_end_duration():
    rows = [
        _row(5000, session_id="s1", cost=1.0),
        _row(1000, session_id="s1", cost=1.0),
    ]
    s = observability.aggregate_sessions(rows)[0]
    assert s["start_ts"] == 1000
    assert s["end_ts"] == 5000
    assert s["duration_ms"] == 4000


def test_sessions_skip_rows_without_session_id():
    rows = [
        _row(1000, session_id="s1", cost=1.0),
        _row(2000, session_id=None, cost=100.0),
    ]
    sessions = observability.aggregate_sessions(rows)
    assert [s["session_id"] for s in sessions] == ["s1"]
    assert sessions[0]["cost"] == 1.0


def test_sessions_sorted_by_start_ts_desc():
    rows = [
        _row(1000, session_id="old"),
        _row(9000, session_id="new"),
    ]
    sessions = observability.aggregate_sessions(rows)
    assert [s["session_id"] for s in sessions] == ["new", "old"]


def test_sessions_nested_agents_models_providers():
    rows = [
        _row(1000, agent="build", model="m-a", provider_id="opencode-go",
             session_id="s1", cost=1.0),
        _row(2000, agent="review", model="m-b", provider_id="openrouter",
             src="router", session_id="s1", cost=4.0),
    ]
    s = observability.aggregate_sessions(rows)[0]
    assert {a["agent"] for a in s["agents"]} == {"build", "review"}
    assert all(a["group"] is None for a in s["agents"])
    # configured group mapping applies inside sessions too (raw agent preserved)
    s2 = observability.aggregate_sessions(rows, agent_groups={"build": "Main"})[0]
    by = {a["agent"]: a for a in s2["agents"]}
    assert by["build"]["group"] == "Main"
    assert by["review"]["group"] is None
    assert {m["model"] for m in s["models"]} == {"m-a", "m-b"}
    assert {p["source"] for p in s["providers"]} == {"go", "router"}


# --------------------------------------------------------------------------
# session tree
# --------------------------------------------------------------------------
def test_session_tree_two_level():
    rows = [
        _row(1000, session_id="root"),
        _row(2000, session_id="child", parent_session_id="root"),
    ]
    tree = observability.build_session_tree(rows)
    nodes = {n["session_id"]: n for n in tree["nodes"]}
    assert nodes["root"]["depth"] == 0
    assert nodes["root"]["is_root"] is True
    assert nodes["root"]["children_count"] == 1
    assert nodes["child"]["depth"] == 1
    assert nodes["child"]["is_root"] is False
    assert tree["roots"] == ["root"]


def test_session_tree_three_level():
    rows = [
        _row(1000, session_id="r"),
        _row(2000, session_id="c", parent_session_id="r"),
        _row(3000, session_id="g", parent_session_id="c"),
    ]
    tree = observability.build_session_tree(rows)
    nodes = {n["session_id"]: n for n in tree["nodes"]}
    assert nodes["g"]["depth"] == 2
    assert tree["diagnostics"]["max_depth"] == 2
    assert tree["diagnostics"]["nodes"] == 3


def test_session_tree_missing_parent_treated_as_root():
    rows = [_row(1000, session_id="orphan", parent_session_id="ghost")]
    tree = observability.build_session_tree(rows)
    nodes = {n["session_id"]: n for n in tree["nodes"]}
    assert nodes["orphan"]["depth"] == 0
    assert nodes["orphan"]["is_root"] is True
    assert tree["roots"] == ["orphan"]
    assert tree["diagnostics"]["missing_parent"] == 1


def test_session_tree_self_parent():
    rows = [_row(1000, session_id="self", parent_session_id="self")]
    tree = observability.build_session_tree(rows)
    nodes = {n["session_id"]: n for n in tree["nodes"]}
    assert nodes["self"]["depth"] == 0
    assert nodes["self"]["is_root"] is True
    assert tree["diagnostics"]["self_parent"] == 1


def test_session_tree_cycle_terminates_fast():
    rows = [
        _row(1000, session_id="a", parent_session_id="b"),
        _row(2000, session_id="b", parent_session_id="a"),
    ]
    tree = _run_with_timeout(lambda: observability.build_session_tree(rows), timeout=5.0)
    assert tree["diagnostics"]["nodes"] == 2
    assert tree["diagnostics"]["cycles"] >= 1
    assert tree["diagnostics"]["max_depth"] < 5
    # every node got a depth and a root exists
    assert all(isinstance(n["depth"], int) for n in tree["nodes"])
    assert tree["roots"]


def test_session_tree_ignores_rows_without_session_id():
    rows = [_row(1000, session_id=None)]
    tree = observability.build_session_tree(rows)
    assert tree["nodes"] == []
    assert tree["roots"] == []
    assert tree["diagnostics"]["nodes"] == 0


# --------------------------------------------------------------------------
# JSON safety: no NaN/Infinity anywhere
# --------------------------------------------------------------------------
def test_aggregates_json_dumps_allow_nan_false():
    rows = [
        _row(1000, agent="build", model="m-a", cost=1.5, session_id="s1",
             dur_ms=10, tokens=_tokens(input_=0, cache_read=0)),
        _row(2000, agent=None, model="m-b", cost=0.0, session_id="s2",
             parent_session_id="s1", dur_ms=None),
        _row(3000, agent="build", model="m-a", cost=2.5, session_id="s1",
             dur_ms=20, tokens=_tokens(input_=100, cache_read=100)),
    ]
    for result in (
        observability.aggregate_agents(rows),
        observability.aggregate_models(rows),
        observability.aggregate_providers(rows),
        observability.aggregate_sessions(rows),
        observability.build_session_tree(rows),
    ):
        json.dumps(result, allow_nan=False)  # must not raise
