"""Phase 2C: GET /api/agents endpoint tests (ephemeral data_server.Handler).

Same isolation pattern as tests/test_http_api.py: the config's formula_url
points at the local valid fixture so get_formula never hits the network, and
the OpenCode DB is a disposable current-schema fixture.
"""
import json
import os
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


def test_agents_requires_token(api_server):
    status, data = _get_json(api_server + "/api/agents")
    assert status == 401
    assert data["error"] == "unauthorized"


def test_agents_current_schema_raw_names(api_server, api_token, data_server,
                                        fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            {"session_id": "ses_a",
             "data": helpers.current_assistant_message(created=2000, agent="worker", cost=1.0)},
            {"session_id": "ses_b",
             "data": helpers.current_assistant_message(created=3000, agent="build", cost=2.0)},
        ],
        sessions=[
            {"id": "ses_a", "parent_id": None, "agent": "build"},
            {"id": "ses_b", "parent_id": None, "agent": "build"},
        ],
    )
    status, data = _get_json(
        api_server + "/api/agents?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["reader"]["schema"] == "current"
    assert data["reader"]["status"] == "ok"
    assert data["range"] == "all"
    # raw agent names preserved (not remapped to Main/Worker/Oracle)
    assert {a["agent"] for a in data["agents"]} == {"worker", "build"}


def test_agents_unknown_agent_included(api_server, api_token, data_server,
                                       fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    msg = helpers.current_assistant_message(created=2000)
    del msg["agent"]
    _build_db(
        data_server, monkeypatch, tmp_path,
        [{"session_id": "ses_x", "data": msg}],
        sessions=[{"id": "ses_x", "parent_id": None, "agent": None}],
    )
    status, data = _get_json(
        api_server + "/api/agents",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    agents = {a["agent"]: a for a in data["agents"]}
    assert "unknown" in agents


def test_agents_group_mapping_from_config(api_server, api_token, data_server,
                                          fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir, agent_groups={"build": "Main"})
    _build_db(
        data_server, monkeypatch, tmp_path,
        [{"session_id": "ses_1",
          "data": helpers.current_assistant_message(created=2000, agent="build")}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "build"}],
    )
    status, data = _get_json(
        api_server + "/api/agents",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    by = {a["agent"]: a for a in data["agents"]}
    assert by["build"]["group"] == "Main"


def test_agents_range_filter(api_server, api_token, data_server,
                             fixtures_dir, monkeypatch, tmp_path):
    import time

    _save_config(data_server, fixtures_dir)
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 40 * 24 * 3600 * 1000
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            {"session_id": "ses_1",
             "data": helpers.current_assistant_message(created=now_ms, agent="recent")},
            {"session_id": "ses_1",
             "data": helpers.current_assistant_message(created=old_ms, agent="ancient")},
        ],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": None}],
    )
    headers = helpers.auth_headers(api_token)

    status, all_data = _get_json(api_server + "/api/agents?range=all", headers=headers)
    assert status == 200
    assert {a["agent"] for a in all_data["agents"]} == {"recent", "ancient"}

    status, today_data = _get_json(api_server + "/api/agents?range=today", headers=headers)
    assert status == 200
    assert today_data["range"] == "today"
    # the 40-day-old row must be filtered out of "today"
    assert "ancient" not in {a["agent"] for a in today_data["agents"]}

    # invalid range falls back to "all"
    status, bad_data = _get_json(api_server + "/api/agents?range=bogus", headers=headers)
    assert status == 200
    assert bad_data["range"] == "all"


def test_agents_empty_db_explicit_status(api_server, api_token, data_server,
                                         fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(data_server, monkeypatch, tmp_path, [], sessions=[])
    status, data = _get_json(
        api_server + "/api/agents",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["agents"] == []
    assert data["reader"]["schema"] == "current"
    assert data["reader"]["status"] == "ok"


def test_agents_cost_basis_metadata(api_server, api_token, data_server,
                                    fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(
        data_server, monkeypatch, tmp_path,
        [{"session_id": "ses_1",
          "data": helpers.current_assistant_message(created=2000, agent="build", cost=1.0)}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "build"}],
    )
    status, data = _get_json(
        api_server + "/api/agents?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["cost_basis"] == "opencode_message_raw"


def test_agents_cost_is_raw_not_meter_adjusted(api_server, api_token, data_server,
                                               fixtures_dir, monkeypatch, tmp_path):
    """cost is the raw OpenCode message cost: RATE_DEFAULT (1.4212 for go) must
    NOT be applied, and the per-model breakdown shares the same raw basis."""
    _save_config(data_server, fixtures_dir)
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            {"session_id": "ses_1",
             "data": helpers.current_assistant_message(
                 created=2000, agent="worker", cost=1.0, model_id="model-one")},
            {"session_id": "ses_1",
             "data": helpers.current_assistant_message(
                 created=3000, agent="worker", cost=2.0, model_id="model-two")},
        ],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "worker"}],
    )
    status, data = _get_json(
        api_server + "/api/agents?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    worker = {a["agent"]: a for a in data["agents"]}["worker"]
    # exact raw sum, not 3.0 * 1.4212 (meter ratio)
    assert worker["cost"] == 3.0
    assert worker["cost"] != 3.0 * 1.4212
    # providerID "opencode-go" -> src "go" in the fixture DB
    models = {m["model"]: m for m in worker["models"]}
    assert models["model-one"]["cost"] == 1.0
    assert models["model-two"]["cost"] == 2.0
    assert models["model-one"]["cost"] != 1.0 * 1.4212
    assert models["model-two"]["cost"] != 2.0 * 1.4212
