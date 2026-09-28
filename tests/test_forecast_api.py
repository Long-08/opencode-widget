"""Phase 4: GET /api/forecast endpoint tests (ephemeral data_server.Handler).

Same isolation pattern as tests/test_agents_api.py: the config's formula_url
points at the local valid fixture so get_formula never hits the network, and the
OpenCode DB is a disposable current-schema fixture built with helpers.py.
"""
import json
import os
import sqlite3
import time
import urllib.error

import helpers

FORMULA_FIXTURE = "formula_valid.json"

RATE_STATUSES = {"ok", "no_usage", "insufficient_data"}
ESTIMATE_STATUSES = {
    "ok", "no_usage", "insufficient_data", "unavailable", "already_at_limit",
    "reset_before_limit", "reset_unknown", "limit_before_reset",
}
SECRET_KEYS = {"api_key", "auth_cookie", "token", "secret", "password", "ciphertext", "key"}


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


def _build_quota_db(ur, rows):
    """rows: list of (fetched_at, kind, label, pct, reset_text)."""
    con = sqlite3.connect(ur.REMOTE_DB)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS quota_snapshot ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, fetched_at INTEGER, kind TEXT,"
            " label TEXT, pct REAL, reset_text TEXT, workspace_id TEXT)")
        for fetched_at, kind, label, pct, reset_text in rows:
            con.execute(
                "INSERT INTO quota_snapshot (fetched_at, kind, label, pct, reset_text)"
                " VALUES (?,?,?,?,?)",
                (fetched_at, kind, label, pct, reset_text),
            )
        con.commit()
    finally:
        con.close()


def _assert_no_nan(body):
    assert "NaN" not in body
    assert "Infinity" not in body


def test_forecast_requires_token(api_server):
    status, data = _get_json(api_server + "/api/forecast")
    assert status == 401
    assert data["error"] == "unauthorized"


def test_forecast_empty_db_is_estimate_not_fabricated(api_server, api_token, data_server,
                                                      fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(data_server, monkeypatch, tmp_path, [], sessions=[])
    status, data = _get_json(
        api_server + "/api/forecast", headers=helpers.auth_headers(api_token))
    assert status == 200
    assert data["type"] == "estimate"
    assert data["basis"] == "official_quota_usage_rate"
    assert data["reader"]["schema"] == "current"
    for section in ("session", "weekly", "period"):
        assert set(data[section]) >= {"official", "rates", "estimates"}
        # empty DB: official is unavailable, never a fake number
        assert data[section]["official"]["status"] == "unavailable"
        assert data[section]["official"]["used"] is None
        assert data[section]["official"]["remaining"] is None
        for rate in data[section]["rates"].values():
            assert rate["status"] == "no_usage"
            assert rate["usage_per_hour"] == 0.0
            assert rate["usage_per_day"] == 0.0


def test_forecast_sections_and_statuses(api_server, api_token, data_server,
                                        fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    now_ms = int(time.time() * 1000)
    _build_db(
        data_server, monkeypatch, tmp_path,
        [
            {"session_id": "ses_1",
             "data": helpers.current_assistant_message(
                 created=now_ms - 45 * 60 * 1000, cost=1.0)},
            {"session_id": "ses_1",
             "data": helpers.current_assistant_message(
                 created=now_ms - 15 * 60 * 1000, cost=2.0)},
        ],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": None}],
    )
    import usage_remote as ur

    _build_quota_db(ur, [
        (now_ms - 60 * 1000, "session", "Session", 25.0, "5 hours 0 minutes"),
        (now_ms - 60 * 1000, "weekly", "Weekly", 10.0, "2 days 3 hours"),
        (now_ms - 60 * 1000, "monthly", "Monthly", 5.0, "20 days 0 hours"),
    ])
    status, data = _get_json(
        api_server + "/api/forecast", headers=helpers.auth_headers(api_token))
    assert status == 200
    assert set(data["session"]["rates"]) == {"last_30m", "last_60m", "window_avg"}
    assert set(data["weekly"]["rates"]) == {"last_24h", "last_3d", "last_7d"}
    assert set(data["period"]["rates"]) == {
        "daily_average", "recent_3d_average", "recent_7d_average"}
    for section in ("session", "weekly", "period"):
        assert data[section]["official"]["status"] == "ok"
        for rate in data[section]["rates"].values():
            assert rate["status"] in RATE_STATUSES
        for est in data[section]["estimates"].values():
            assert est["status"] in ESTIMATE_STATUSES
    _assert_no_nan(json.dumps(data))


def test_forecast_official_uses_reset_text(api_server, api_token, data_server,
                                           fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(data_server, monkeypatch, tmp_path, [], sessions=[])
    import usage_remote as ur

    fetched = int(time.time() * 1000) - 1000
    _build_quota_db(ur, [
        (fetched, "session", "Session", 50.0, "5 hours 0 minutes"),
    ])
    status, data = _get_json(
        api_server + "/api/forecast", headers=helpers.auth_headers(api_token))
    assert status == 200
    off = data["session"]["official"]
    assert off["reset_source"] == "official_reset_text"
    assert off["reset_at"] == fetched + 5 * 3600000
    # official used is derived from the official pct (limit * pct/100)
    assert off["limit"] == data_server.gw.limit_for("session")
    assert off["used"] == off["limit"] * 0.5


def test_forecast_official_falls_back_to_local_reset(api_server, api_token, data_server,
                                                     fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    _build_db(data_server, monkeypatch, tmp_path, [], sessions=[])
    import usage_remote as ur

    _build_quota_db(ur, [
        (int(time.time() * 1000), "weekly", "Weekly", 10.0, "whenever"),
    ])
    status, data = _get_json(
        api_server + "/api/forecast", headers=helpers.auth_headers(api_token))
    assert status == 200
    off = data["weekly"]["official"]
    assert off["reset_source"] == "local_window_estimate"
    assert isinstance(off["reset_at"], int)


def test_forecast_no_secret_fields(api_server, api_token, data_server,
                                   fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir, server={
        "auth_cookie": "cookie-xyz", "workspace_id": "wrk_abc"})
    _build_db(data_server, monkeypatch, tmp_path, [], sessions=[])
    status, body = helpers.http_get(
        api_server + "/api/forecast", headers=helpers.auth_headers(api_token))
    assert status == 200
    assert "cookie-xyz" not in body
    assert "wrk_abc" not in body
    lowered = body.lower()
    for key in SECRET_KEYS:
        assert f'"{key}"' not in lowered
    _assert_no_nan(body)


def test_forecast_json_strict_no_nan_infinity(api_server, api_token, data_server,
                                              fixtures_dir, monkeypatch, tmp_path):
    _save_config(data_server, fixtures_dir)
    now_ms = int(time.time() * 1000)
    _build_db(
        data_server, monkeypatch, tmp_path,
        [{"session_id": "ses_1",
          "data": helpers.current_assistant_message(created=now_ms - 600000, cost=1.0)}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": None}],
    )
    status, body = helpers.http_get(
        api_server + "/api/forecast", headers=helpers.auth_headers(api_token))
    assert status == 200
    _assert_no_nan(body)
    # re-serialize strictly: raises if any NaN/Infinity survived parsing
    json.dumps(json.loads(body), allow_nan=False)
