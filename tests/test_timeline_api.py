"""Phase 5B: GET /api/timeline endpoint tests (ephemeral data_server.Handler).

Same isolation pattern as tests/test_agents_api.py and
tests/test_observability_api.py: the config's formula_url points at the local
valid fixture so get_formula never hits the network, and the OpenCode DB is a
disposable current-schema fixture.
"""
import json
import os
import time
import urllib.error

import helpers

FORMULA_FIXTURE = "formula_valid.json"
HOUR_MS = 3600 * 1000
DAY_MS = 24 * HOUR_MS


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


def _msg(session_id, created, agent="worker", cost=1.0, tokens=None,
         model_id="m-a"):
    data = helpers.current_assistant_message(
        created=created, agent=agent, model_id=model_id, cost=cost, tokens=tokens,
    )
    return {"session_id": session_id, "data": data}


def _span_db(data_server, monkeypatch, tmp_path):
    """worker now, build now-2d, ancient now-40d (each in its own session)."""
    now = int(time.time() * 1000)
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            _msg("ses_a", now, agent="worker", cost=1.0,
                 tokens=helpers.default_tokens(input_=10, output_=20,
                                               cache_read=5, cache_write=1)),
            _msg("ses_b", now - 2 * DAY_MS, agent="build", cost=2.0,
                 tokens=helpers.default_tokens(input_=30, output_=40)),
            _msg("ses_c", now - 40 * DAY_MS, agent="ancient", cost=0.5,
                 tokens=helpers.default_tokens(input_=1, output_=1)),
        ],
        sessions=[
            {"id": "ses_a", "parent_id": None, "agent": None},
            {"id": "ses_b", "parent_id": None, "agent": None},
            {"id": "ses_c", "parent_id": None, "agent": None},
        ],
    )
    return now


# --------------------------------------------------------------------------
# guard
# --------------------------------------------------------------------------
def test_timeline_requires_token(api_server):
    status, data = _get_json(api_server + "/api/timeline")
    assert status == 401
    assert data["error"] == "unauthorized"


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------
def test_timeline_shape_and_cost_basis(api_server, api_token, data_server,
                                       fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _span_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/timeline?range=7d",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["range"] == "7d"
    assert data["cost_basis"] == "opencode_message_raw"
    assert data["reader"]["schema"] == "current"
    assert data["reader"]["status"] == "ok"
    assert set(data["bucket"]) == {"unit", "seconds"}
    assert data["series"]
    for entry in data["series"]:
        for key in ("start", "end", "requests", "tokens", "cost"):
            assert key in entry, key
        assert set(entry["tokens"]) == {
            "total", "input", "output", "cache_read", "cache_write",
        }


def test_timeline_bucket_unit_and_seconds_per_range(api_server, api_token,
                                                    data_server, fixtures_dir,
                                                    monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _span_db(data_server, monkeypatch, tmp_path)
    headers = helpers.auth_headers(api_token)
    expected = {
        "today": (1800, "minute"),
        "7d": (10800, "hour"),
        "30d": (86400, "day"),
    }
    for rng, (seconds, unit) in expected.items():
        status, data = _get_json(
            f"{api_server}/api/timeline?range={rng}", headers=headers)
        assert status == 200, rng
        assert data["bucket"]["seconds"] == seconds, rng
        assert data["bucket"]["unit"] == unit, rng
    # "all" is auto: a positive ladder size, at least an hour here.
    status, all_data = _get_json(
        api_server + "/api/timeline?range=all", headers=headers)
    assert status == 200
    assert all_data["bucket"]["seconds"] >= 3600
    assert all_data["bucket"]["seconds"] * 1000 % 1800000 == 0


def test_timeline_series_is_contiguous_and_nonempty(api_server, api_token,
                                                    data_server, fixtures_dir,
                                                    monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _span_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/timeline?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    series = data["series"]
    assert len(series) >= 2
    for left, right in zip(series, series[1:]):
        assert left["end"] == right["start"]
    assert series[0]["start"] < series[-1]["end"]
    assert all(entry["start"] < entry["end"] for entry in series)


# --------------------------------------------------------------------------
# invariants
# --------------------------------------------------------------------------
def test_timeline_requests_match_in_range_rows_and_agents(api_server, api_token,
                                                          data_server, fixtures_dir,
                                                          monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _span_db(data_server, monkeypatch, tmp_path)
    headers = helpers.auth_headers(api_token)
    expected = {"today": 1, "7d": 2, "30d": 2, "all": 3}
    for rng, count in expected.items():
        status, data = _get_json(
            f"{api_server}/api/timeline?range={rng}", headers=headers)
        assert status == 200, rng
        timeline_requests = sum(e["requests"] for e in data["series"])
        assert timeline_requests == count, rng

        status2, agents = _get_json(
            f"{api_server}/api/agents?range={rng}", headers=headers)
        assert status2 == 200, rng
        agent_requests = sum(a["requests"] for a in agents["agents"])
        assert timeline_requests == agent_requests, rng


def test_timeline_totals_match_rows_and_agents(api_server, api_token,
                                               data_server, fixtures_dir,
                                               monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _span_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/timeline?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    series = data["series"]
    assert sum(e["cost"] for e in series) == 3.5
    assert sum(e["tokens"]["total"] for e in series) == 108
    assert sum(e["tokens"]["input"] for e in series) == 41
    assert sum(e["tokens"]["output"] for e in series) == 61
    assert sum(e["tokens"]["cache_read"] for e in series) == 5
    assert sum(e["tokens"]["cache_write"] for e in series) == 1

    status2, agents = _get_json(
        api_server + "/api/agents?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status2 == 200
    assert sum(a["cost"] for a in agents["agents"]) == 3.5
    assert sum(a["tokens"]["total"] for a in agents["agents"]) == 108


def test_timeline_invalid_range_falls_back_to_all(api_server, api_token,
                                                  data_server, fixtures_dir,
                                                  monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _span_db(data_server, monkeypatch, tmp_path)
    status, data = _get_json(
        api_server + "/api/timeline?range=bogus",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["range"] == "all"
    assert sum(e["requests"] for e in data["series"]) == 3


def test_timeline_empty_db_all_zero_buckets(api_server, api_token, data_server,
                                            fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(data_server, monkeypatch, tmp_path, [], sessions=[])
    status, data = _get_json(
        api_server + "/api/timeline?range=all",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data["reader"]["schema"] == "current"
    assert data["reader"]["status"] == "ok"
    assert len(data["series"]) >= 1
    for entry in data["series"]:
        assert entry["requests"] == 0
        assert entry["cost"] == 0.0
        assert entry["tokens"]["total"] == 0
