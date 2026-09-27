"""Phase 2A: localhost HTTP API hardening tests.

Covers Bearer-token auth, Host allowlisting, CORS policy (no wildcard),
secret-free /api/config and /api/grab responses, and the runtime-info file.
All secrets are dummy values and the server runs on an ephemeral port.
"""
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import helpers

FORMULA_FIXTURE = "formula_valid.json"


def _save_config(data_server, fixtures_dir, **extra):
    cfg = {"formula_url": os.path.join(fixtures_dir, FORMULA_FIXTURE)}
    cfg.update(extra)
    data_server.gw.save_config(cfg)
    return cfg


def _raw(url, method="GET", headers=None, timeout=10):
    """Return (status, headers, body) without raising on 4xx/5xx."""
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", errors="replace")


def _port(api_server):
    return urllib.parse.urlparse(api_server).port


# ---------------------------------------------------------------------------
# token auth
# ---------------------------------------------------------------------------
def test_api_rejects_missing_runtime_token(api_server):
    status, _, body = _raw(api_server + "/api/state")
    assert status == 401
    assert json.loads(body) == {"ok": False, "error": "unauthorized"}


def test_api_rejects_invalid_runtime_token(api_server):
    status, _, body = _raw(
        api_server + "/api/state", headers={"Authorization": "Bearer not-the-token"}
    )
    assert status == 401
    data = json.loads(body)
    assert data["ok"] is False
    assert data["error"] == "unauthorized"


def test_api_accepts_valid_runtime_token(api_server, api_token, data_server, fixtures_dir):
    _save_config(data_server, fixtures_dir)
    status, data = helpers.http_get_json(
        api_server + "/api/state", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert "windows" in data


def test_health_anonymous(api_server):
    status, body = helpers.http_get(api_server + "/api/health")
    assert status == 200
    assert body == "ok"


# ---------------------------------------------------------------------------
# Host allowlist
# ---------------------------------------------------------------------------
def test_host_restriction(api_server):
    port = _port(api_server)

    # good Host: default HTTPConnection supplies 127.0.0.1:<port>
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/health")
    resp = conn.getresponse()
    assert resp.status == 200
    conn.close()

    # foreign hostname with the correct port -> 403
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/health", headers={"Host": f"evil.com:{port}"})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    assert resp.status == 403
    assert body == {"ok": False, "error": "forbidden_host"}

    # correct hostname, wrong port -> 403
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/health", headers={"Host": f"127.0.0.1:{port + 1}"})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    assert resp.status == 403
    assert body == {"ok": False, "error": "forbidden_host"}


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
def test_no_wildcard_cors(api_server, api_token):
    port = _port(api_server)

    # OPTIONS from a null origin: 204 + echoed ACAO (never "*"), no token needed
    status, headers, _ = _raw(
        api_server + "/api/health", method="OPTIONS", headers={"Origin": "null"}
    )
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == "null"
    assert headers.get("Access-Control-Allow-Origin") != "*"

    # GET with a disallowed Origin -> 403 forbidden_origin
    status, _, body = _raw(
        api_server + "/api/health", headers={"Origin": "http://evil.example"}
    )
    assert status == 403
    assert json.loads(body) == {"ok": False, "error": "forbidden_origin"}

    # GET without Origin -> no ACAO header at all
    status, headers, _ = _raw(api_server + "/api/health")
    assert status == 200
    assert "Access-Control-Allow-Origin" not in headers

    # GET with an allowed origin -> echoed, never "*"
    allowed = f"http://127.0.0.1:{port}"
    status, headers, _ = _raw(api_server + "/api/health", headers={"Origin": allowed})
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == allowed
    assert headers.get("Access-Control-Allow-Origin") != "*"
    assert headers.get("Vary") == "Origin"


# ---------------------------------------------------------------------------
# /api/config and /api/grab never expose secrets
# ---------------------------------------------------------------------------
def test_config_never_exposes_secrets(api_server, api_token, data_server, fixtures_dir):
    _save_config(
        data_server,
        fixtures_dir,
        api_key="sk-secret-key-XYZ",
        server={"auth_cookie": "secret-cookie-ABC", "workspace_id": "wrk_xyz"},
    )
    status, cfg = helpers.http_get_json(
        api_server + "/api/config", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    raw = json.dumps(cfg)
    assert "sk-secret-key-XYZ" not in raw
    assert "secret-cookie-ABC" not in raw
    assert cfg["api_key_configured"] is True
    assert cfg["auth_configured"] is True
    assert cfg["workspace_configured"] is True
    assert cfg["workspace_id"] == "wrk_xyz"
    assert "calibration" in cfg
    assert "api_key" not in cfg
    assert "server" not in cfg


def test_grab_never_returns_cookie(api_server, api_token, data_server, fixtures_dir, monkeypatch):
    _save_config(
        data_server,
        fixtures_dir,
        server={"auth_cookie": "", "workspace_id": "wrk_dummy"},
    )
    monkeypatch.setattr(
        data_server.gw, "read_auth_cookie_from_webdata", lambda: "grabbed-secret-cookie"
    )
    status, data = helpers.http_post_json_response(
        api_server + "/api/grab", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is True
    assert data["auth_configured"] is True
    assert data["workspace_configured"] is True
    assert "auth_cookie" not in data
    assert "grabbed-secret-cookie" not in json.dumps(data)


# ---------------------------------------------------------------------------
# runtime info file
# ---------------------------------------------------------------------------
def test_write_runtime_info(data_server, monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime-info"
    monkeypatch.setenv("OPENCODE_WIDGET_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setattr(data_server, "_TOKEN", None)

    data_server._write_runtime_info(4321)

    path = runtime_dir / "runtime.json"
    assert path.exists()
    info = json.loads(path.read_text(encoding="utf-8"))
    assert info["port"] == 4321
    assert info["pid"] == os.getpid()
    assert isinstance(info["created"], int)
    assert len(info["token"]) >= 43  # token_urlsafe(32) ~= 43 chars
