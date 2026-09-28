"""Phase 3: endpoint tests for /api/models, /api/providers, /api/sessions and the
reworked /api/agents (ephemeral data_server.Handler).

Same isolation pattern as tests/test_agents_api.py: the config's formula_url
points at the local valid fixture so get_formula never hits the network, and the
OpenCode DB is a disposable current-schema fixture.
"""
import json
import os
import time
import urllib.error

import helpers

FORMULA_FIXTURE = "formula_valid.json"


def _save_config(data_server, fixtures_dir, **extra):
    cfg = {"formula_url": os.path.join(fixtures_dir, FORMULA_FIXTURE)}
    cfg.update(extra)
    data_server.gw.save_config(cfg)
    return cfg


def _get_json(url, headers=None, timeout=10):
    try:
        return helpers.http_get_json(url, timeout=timeout, headers=headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, json.loads(body)


def _build_db(data_server, monkeypatch, tmp_path, messages, sessions=None):
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db", messages, sessions=sessions or []
    )
    monkeypatch.setattr(data_server.gw, "OPENCODE_DB", str(db))
    return db


def _msg(session_id, created, agent="worker", model_id="m-a",
         provider_id="opencode-go", cost=1.0, variant="high", tokens=None):
    data = helpers.current_assistant_message(
        created=created, agent=agent, model_id=model_id,
        provider_id=provider_id, cost=cost, variant=variant, tokens=tokens,
    )
    if agent is None:
        del data["agent"]
    return {"session_id": session_id, "data": data}


def _three_row_db(data_server, monkeypatch, tmp_path):
    """worker/m-a/go, build/m-b/router, unknown/m-a/go — sessions ses_a, ses_b."""
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            _msg("ses_a", 2000, agent="worker", model_id="m-a", cost=1.0,
                 tokens=helpers.default_tokens(input_=10, output_=20,
                                               cache_read=5, cache_write=1)),
            _msg("ses_b", 3000, agent="build", model_id="m-b",
                 provider_id="openrouter", cost=2.0,
                 tokens=helpers.default_tokens(input_=30, output_=40)),
            _msg("ses_a", 4000, agent=None, model_id="m-a", cost=0.5,
                 tokens=helpers.default_tokens(input_=1, output_=1)),
        ],
        sessions=[
            {"id": "ses_a", "parent_id": None, "agent": None},
            {"id": "ses_b", "parent_id": "ses_a", "agent": None},
        ],
    )


# --------------------------------------------------------------------------
# token guard
# --------------------------------------------------------------------------
def test_models_requires_token(api_server):
    status, data = _get_json(api_server + "/api/models")
    assert status == 401
    assert data["error"] == "unauthorized"


def test_providers_requires_token(api_server):
    status, data = _get_json(api_server + "/api/providers")
    assert status == 401
    assert data["error"] == "unauthorized"


def test_sessions_requires_token(api_server):
    status, data = _get_json(api_server + "/api/sessions")
    assert status == 401
    assert data["error"] == "unauthorized"


# --------------------------------------------------------------------------
# /api/agents (reworked)
# --------------------------------------------------------------------------
def test_agents_shape_new_fields(api_server, api_token, data_server,
                                 fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir, agent_groups={"worker": "Main"})
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/agents?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["reader"]["schema"] == "current"
    assert data["range"] == "all"
    assert data["cost_basis"] == "opencode_message_raw"
    worker = {a["agent"]: a for a in data["agents"]}["worker"]
    for key in ("agent", "group", "requests", "sessions", "cost", "tokens",
                "cache_read_ratio", "duration", "avg_tokens_per_request",
                "avg_cost_per_request", "avg_duration_per_request", "models",
                "providers"):
        assert key in worker, key
    assert worker["group"] == "Main"
    assert worker["requests"] == 1
    assert worker["sessions"] == 1
    assert worker["models"][0]["provider"] == "opencode-go"
    unknown = {a["agent"]: a for a in data["agents"]}["unknown"]
    assert unknown["group"] is None


def test_agents_range_filter_today_vs_all(api_server, api_token, data_server,
                                          fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 40 * 24 * 3600 * 1000
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            _msg("ses_1", now_ms, agent="recent"),
            _msg("ses_1", old_ms, agent="ancient"),
        ],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": None}],
    )
    headers = helpers.auth_headers(api_token)
    status, all_data = _get_json(api_server + "/api/agents?range=all", headers=headers)
    assert status == 200
    assert {a["agent"] for a in all_data["agents"]} == {"recent", "ancient"}
    status, today_data = _get_json(api_server + "/api/agents?range=today", headers=headers)
    assert status == 200
    assert "ancient" not in {a["agent"] for a in today_data["agents"]}
    status, bad = _get_json(api_server + "/api/agents?range=bogus", headers=headers)
    assert status == 200
    assert bad["range"] == "all"


# --------------------------------------------------------------------------
# /api/models
# --------------------------------------------------------------------------
def test_models_model_to_agents(api_server, api_token, data_server,
                                fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir, agent_groups={"worker": "Main"})
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/models?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["range"] == "all"
    assert data["cost_basis"] == "opencode_message_raw"
    by = {m["model"]: m for m in data["models"]}
    assert set(by) == {"m-a", "m-b"}
    ma = by["m-a"]
    assert ma["requests"] == 2
    assert ma["provider_id"] == "opencode-go"
    assert ma["source"] == "go"
    assert {a["agent"] for a in ma["agents"]} == {"worker", "unknown"}
    worker_agent = {a["agent"]: a for a in ma["agents"]}["worker"]
    assert worker_agent["group"] == "Main"


def test_models_range_filter(api_server, api_token, data_server,
                             fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 40 * 24 * 3600 * 1000
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            _msg("ses_1", now_ms, agent="recent", model_id="model-recent"),
            _msg("ses_1", old_ms, agent="ancient", model_id="model-ancient"),
        ],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": None}],
    )
    headers = helpers.auth_headers(api_token)
    status, today = _get_json(api_server + "/api/models?range=today", headers=headers)
    assert status == 200
    assert {m["model"] for m in today["models"]} == {"model-recent"}
    status, all_data = _get_json(api_server + "/api/models?range=all", headers=headers)
    assert {m["model"] for m in all_data["models"]} == {"model-recent", "model-ancient"}


# --------------------------------------------------------------------------
# /api/providers
# --------------------------------------------------------------------------
def test_providers_fields_and_display_name(api_server, api_token, data_server,
                                           fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/providers?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["cost_basis"] == "opencode_message_raw"
    by = {p["provider_id"]: p for p in data["providers"]}
    go = by["opencode-go"]
    for key in ("provider_id", "source", "display_name", "requests", "sessions",
                "cost", "tokens", "cache_read_ratio", "agents", "models"):
        assert key in go, key
    assert go["source"] == "go"
    assert go["display_name"] == "OpenCode Go"
    assert go["requests"] == 2
    assert {a["agent"] for a in go["agents"]} == {"worker", "unknown"}
    assert {m["model"] for m in go["models"]} == {"m-a"}
    # unknown source: display_name falls back to the raw source
    assert by["openrouter"]["source"] == "openrouter"
    assert by["openrouter"]["display_name"] == "openrouter"


def test_providers_invalid_range_falls_back_to_all(api_server, api_token,
                                                   data_server, fixtures_dir,
                                                   monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/providers?range=nonsense",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["range"] == "all"
    assert len(data["providers"]) == 2


# --------------------------------------------------------------------------
# /api/sessions
# --------------------------------------------------------------------------
def test_sessions_shape_and_tree(api_server, api_token, data_server,
                                 fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/sessions?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["cost_basis"] == "opencode_message_raw"
    by = {s["session_id"]: s for s in data["sessions"]}
    assert set(by) == {"ses_a", "ses_b"}
    ses_a = by["ses_a"]
    for key in ("session_id", "parent_session_id", "start_ts", "end_ts",
                "duration_ms", "requests", "cost", "tokens", "agents", "models",
                "providers"):
        assert key in ses_a, key
    assert ses_a["requests"] == 2
    assert ses_a["cost"] == 1.5  # own rows only; excludes ses_b's 2.0
    assert by["ses_b"]["parent_session_id"] == "ses_a"
    # tree
    tree = data["tree"]
    nodes = {n["session_id"]: n for n in tree["nodes"]}
    assert set(nodes) == {"ses_a", "ses_b"}
    assert nodes["ses_a"]["is_root"] is True
    assert nodes["ses_b"]["depth"] == 1
    assert tree["roots"] == ["ses_a"]
    assert tree["diagnostics"]["nodes"] == 2


def test_sessions_range_filter(api_server, api_token, data_server,
                               fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 40 * 24 * 3600 * 1000
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            _msg("ses_recent", now_ms, agent="recent"),
            _msg("ses_ancient", old_ms, agent="ancient"),
        ],
        sessions=[
            {"id": "ses_recent", "parent_id": None, "agent": None},
            {"id": "ses_ancient", "parent_id": None, "agent": None},
        ],
    )
    headers = helpers.auth_headers(api_token)
    status, today = _get_json(api_server + "/api/sessions?range=today", headers=headers)
    assert status == 200
    assert {s["session_id"] for s in today["sessions"]} == {"ses_recent"}
    assert {n["session_id"] for n in today["tree"]["nodes"]} == {"ses_recent"}


def test_sessions_json_has_no_content_or_paths(api_server, api_token, data_server,
                                               fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, body = helpers.http_get(
        api_server + "/api/sessions?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    low = body.lower()
    for needle in ("content", "prompt", "response", "text", "reasoning", "tool"):
        assert needle not in low, needle
    for path in ("c:\\", "d:\\", "/tmp/", "appdata", "users\\"):
        assert path not in low, path


# --------------------------------------------------------------------------
# cross-aggregation invariants
# --------------------------------------------------------------------------
def test_cross_aggregation_invariants(api_server, api_token, data_server,
                                      fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    headers = helpers.auth_headers(api_token)

    status, agents = _get_json(api_server + "/api/agents?range=all", headers=headers)
    status2, models = _get_json(api_server + "/api/models?range=all", headers=headers)
    status3, providers = _get_json(api_server + "/api/providers?range=all", headers=headers)
    assert status == status2 == status3 == 200

    total_rows = 3
    # request totals preserved across every aggregation
    assert sum(a["requests"] for a in agents["agents"]) == total_rows
    assert sum(m["requests"] for m in models["models"]) == total_rows
    assert sum(p["requests"] for p in providers["providers"]) == total_rows

    # token totals
    agent_tok = sum(a["tokens"]["total"] for a in agents["agents"])
    model_tok = sum(m["tokens"]["total"] for m in models["models"])
    provider_tok = sum(p["tokens"]["total"] for p in providers["providers"])
    assert agent_tok == model_tok == provider_tok

    # cost totals (float tolerance)
    agent_cost = sum(a["cost"] for a in agents["agents"])
    model_cost = sum(m["cost"] for m in models["models"])
    provider_cost = sum(p["cost"] for p in providers["providers"])
    assert abs(agent_cost - 3.5) < 1e-6
    assert abs(model_cost - agent_cost) < 1e-6
    assert abs(provider_cost - agent_cost) < 1e-6


def test_unknown_agent_totals_preserved(api_server, api_token, data_server,
                                        fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/agents?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    unknown = {a["agent"]: a for a in data["agents"]}["unknown"]
    assert unknown["requests"] == 1
    assert unknown["cost"] == 0.5


def test_sessions_requests_match_rows(api_server, api_token, data_server,
                                      fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _three_row_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/sessions?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    # every fixture message has a session_id, so sessions account for all rows
    assert sum(s["requests"] for s in data["sessions"]) == 3
