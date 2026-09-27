"""HTTP API tests against an ephemeral data_server.Handler (api_server fixture).

Before any endpoint that triggers data_server.get_formula (/api/state,
/api/formula, /api/views, /api/view/*) we write a config whose formula_url
points at the local valid fixture, so no network is ever attempted. All
secrets in payloads are dummy values.

Phase 2A: every endpoint except GET /api/health now requires
`Authorization: Bearer <token>`; each request below carries api_token.
"""
import json
import os
import urllib.error
import urllib.request

import helpers

FORMULA_FIXTURE = "formula_valid.json"


def _save_config(data_server, fixtures_dir, **extra):
    """Write an isolated config that always points formula_url at the local fixture."""
    cfg = {"formula_url": os.path.join(fixtures_dir, FORMULA_FIXTURE)}
    cfg.update(extra)
    data_server.gw.save_config(cfg)
    return cfg


def _get_json(url, headers=None, timeout=10):
    """http_get_json, but return the JSON error body instead of raising on 4xx/5xx."""
    try:
        return helpers.http_get_json(url, timeout=timeout, headers=headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, json.loads(body)


def _options(url, headers=None, timeout=10):
    req = urllib.request.Request(url, method="OPTIONS", headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers)


def test_health(api_server):
    # Phase 2A: GET /api/health stays anonymous (no token needed).
    status, body = helpers.http_get(api_server + "/api/health")
    assert status == 200
    assert body == "ok"


def test_state_empty(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_get_json(
        api_server + "/api/state", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    for key in ("windows", "stats", "history", "suppliers", "heatmap", "key", "models", "ts"):
        assert key in data
    assert data["key"] is False
    assert data["models"] == 0
    assert data["stats"] == []


def test_formula_endpoint(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_get_json(
        api_server + "/api/formula", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["version"] == 999
    assert data["source"] == "cloud"


def test_views_endpoint(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_get_json(
        api_server + "/api/views", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["version"] == 999
    assert "all_today" in [v["id"] for v in data["views"]]


def test_view_all_today(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_get_json(
        api_server + "/api/view/all_today", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert isinstance(data["totals"]["cost"], (int, float))


def test_view_unknown_returns_404(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = _get_json(
        api_server + "/api/view/definitely_not_a_view",
        headers=helpers.auth_headers(api_token),
    )
    assert status == 404
    assert data["ok"] is False


def test_set_key_and_config(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_post_json_response(
        api_server + "/api/key",
        {"key": "test-key-123"},
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data == {"ok": True}

    _, cfg = helpers.http_get_json(
        api_server + "/api/config", headers=helpers.auth_headers(api_token)
    )
    # Phase 2A: /api/config only exposes a configured flag, never the secret.
    assert cfg["api_key_configured"] is True
    assert "api_key" not in cfg


def test_set_server_and_config(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_post_json_response(
        api_server + "/api/server",
        {"auth_cookie": "dummy-cookie", "workspace_id": "wrk_dummy"},
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data == {"ok": True}

    _, cfg = helpers.http_get_json(
        api_server + "/api/config", headers=helpers.auth_headers(api_token)
    )
    # Phase 2A: cookie stays server-side; only flags + workspace_id are returned.
    assert cfg["auth_configured"] is True
    assert cfg["workspace_configured"] is True
    assert cfg["workspace_id"] == "wrk_dummy"
    assert "server" not in cfg


def test_calibrate_clamps_and_ignores_non_positive(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_post_json_response(
        api_server + "/api/calibrate",
        {"session": 50, "weekly": -1, "monthly": 150},
        headers=helpers.auth_headers(api_token),
    )
    assert status == 200
    assert data == {"ok": True}

    _, cfg = helpers.http_get_json(
        api_server + "/api/config", headers=helpers.auth_headers(api_token)
    )
    cal = cfg["calibration"]
    assert cal["session"] == 50.0
    assert cal["monthly"] == 100.0  # clamped to 100
    assert "weekly" not in cal  # non-positive ignored


def test_sync_without_cookie_fails(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_post_json_response(
        api_server + "/api/sync", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is False


def test_sync_success(api_server, api_token, data_server, fixtures_dir, monkeypatch):
    _save_config(
        data_server, fixtures_dir,
        server={"auth_cookie": "dummy-cookie", "workspace_id": "wrk_dummy"},
    )
    windows = [{"kind": "monthly", "pct": 10.0}]
    monkeypatch.setattr(
        data_server.gw, "scrape_server_usage",
        lambda *a, **k: {"ok": True, "windows": windows, "applied_credits": 3},
    )
    monkeypatch.setattr(data_server.ur, "full_sync", lambda *a, **k: {"ok": True})

    status, data = helpers.http_post_json_response(
        api_server + "/api/sync", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is True
    assert data["windows"] == windows
    assert data["remote"]["ok"] is True


def test_sync_scrape_failure(api_server, api_token, data_server, fixtures_dir, monkeypatch):
    _save_config(
        data_server, fixtures_dir,
        server={"auth_cookie": "dummy-cookie", "workspace_id": "wrk_dummy"},
    )
    monkeypatch.setattr(data_server.gw, "scrape_server_usage", lambda *a, **k: {"ok": False, "error": "x"})

    status, data = helpers.http_post_json_response(
        api_server + "/api/sync", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is False


def test_grab_without_cookie_fails(api_server, api_token, data_server, fixtures_dir):
    # isolated_state stubs the cookie reader to return "" by default
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_post_json_response(
        api_server + "/api/grab", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is False


def test_grab_with_cookie_and_workspace(api_server, api_token, data_server, fixtures_dir, monkeypatch):
    # workspace_id already configured -> do_grab skips the /auth network discovery
    _save_config(
        data_server, fixtures_dir,
        server={"auth_cookie": "dummy-cookie", "workspace_id": "wrk_dummy"},
    )
    monkeypatch.setattr(data_server.gw, "read_auth_cookie_from_webdata", lambda: "dummy-cookie")

    status, data = helpers.http_post_json_response(
        api_server + "/api/grab", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is True
    # Phase 2A: the cookie is saved server-side but never echoed back.
    assert data["auth_configured"] is True
    assert data["workspace_configured"] is True
    assert "auth_cookie" not in data


def test_options_cors_no_wildcard(api_server, api_token):
    # Phase 2A: OPTIONS no longer returns ACAO "*"; a null origin is echoed back.
    status, headers = _options(api_server + "/api/health", headers={"Origin": "null"})
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") != "*"
    assert headers.get("Access-Control-Allow-Origin") == "null"
