"""Phase 2.1: non-sensitive secret-storage status + fail-closed writes.

All secrets here are dummy values. The autouse ``isolated_secret_store``
fixture (conftest) installs a reversible fake backend and a per-test secret
file; ``isolated_state`` redirects CONFIG_PATH into tmp_path. Nothing here
touches real DPAPI / credential storage.
"""
import json
import os

import pytest

import helpers
import secret_store


class _FailingBackend:
    """Backend whose encrypt always fails (write path unavailable)."""

    def encrypt(self, data):
        raise RuntimeError("boom")

    def decrypt(self, data):
        return data


class _PlainBackend:
    def encrypt(self, data):
        return data

    def decrypt(self, data):
        return data


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write_config(gw, cfg):
    with open(gw.CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def _all_file_bytes(root):
    blobs = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                blobs.append(_read_bytes(os.path.join(dirpath, name)))
            except OSError:
                pass
    return blobs


# ---------------------------------------------------------------------------
# secret_store.backend_name
# ---------------------------------------------------------------------------
def test_backend_name_none_and_custom():
    secret_store.set_backend(None)
    assert secret_store.backend_name() == "none"

    secret_store.set_backend(_PlainBackend())
    assert secret_store.backend_name() == "custom"


def test_backend_name_backend_provided():
    class _NamedBackend(_PlainBackend):
        name = "acme"

    secret_store.set_backend(_NamedBackend())
    assert secret_store.backend_name() == "acme"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_backend_name_dpapi():
    secret_store.set_backend(secret_store.DPAPIBackend())
    assert secret_store.backend_name() == "dpapi"


# ---------------------------------------------------------------------------
# secret_storage_status
# ---------------------------------------------------------------------------
def test_status_secure(gw):
    _write_config(gw, {"server": {"workspace_id": "wrk_secure"}})
    status = gw.secret_storage_status()
    assert status == {"backend": "custom", "status": "secure"}
    assert set(status) == {"backend", "status"}


def test_status_unavailable(gw):
    secret_store.set_backend(None)
    assert gw.secret_storage_status() == {"backend": "none", "status": "unavailable"}


def test_status_insecure_fallback(gw):
    _write_config(gw, {"api_key": "dummy-fallback-key"})
    secret_store.set_backend(None)
    status = gw.secret_storage_status()
    assert status == {"backend": "none", "status": "insecure_fallback"}
    # never leaks the plaintext secret through the status payload
    assert "dummy-fallback-key" not in json.dumps(status)


def test_status_migration_failed(gw):
    _write_config(gw, {
        "api_key": "dummy-mig-key",
        "server": {"auth_cookie": "dummy-mig-cookie", "workspace_id": "wrk_mig"},
    })
    before = _read_bytes(gw.CONFIG_PATH)

    secret_store.set_backend(_FailingBackend())

    # fail-safe migration on load is unchanged: plaintext is retained as-is
    cfg = gw.load_config()
    assert cfg["api_key"] == "dummy-mig-key"
    assert cfg["server"]["auth_cookie"] == "dummy-mig-cookie"
    assert _read_bytes(gw.CONFIG_PATH) == before
    assert not os.path.exists(secret_store.secret_file_path())

    status = gw.secret_storage_status()
    assert status == {"backend": "custom", "status": "migration_failed"}
    assert "dummy-mig-key" not in json.dumps(status)
    assert "dummy-mig-cookie" not in json.dumps(status)


# ---------------------------------------------------------------------------
# fail-closed new writes
# ---------------------------------------------------------------------------
def test_save_config_fails_closed(gw):
    _write_config(gw, {"other": 1})
    before = _read_bytes(gw.CONFIG_PATH)

    secret_store.set_backend(_FailingBackend())
    with pytest.raises(secret_store.SecretStoreError):
        gw.save_config({"api_key": "dummy-closed-secret", "other": 2})

    # config.json byte-identical, no secret file, no secret text anywhere
    assert _read_bytes(gw.CONFIG_PATH) == before
    assert not os.path.exists(secret_store.secret_file_path())
    for blob in _all_file_bytes(os.path.dirname(gw.CONFIG_PATH)):
        assert b"dummy-closed-secret" not in blob


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------
def test_api_config_secret_storage_shape(api_server, api_token, data_server, fixtures_dir):
    data_server.gw.save_config({
        "formula_url": os.path.join(fixtures_dir, "formula_valid.json"),
        "api_key": "dummy-status-key",
        "server": {"auth_cookie": "dummy-status-cookie", "workspace_id": "wrk_status"},
    })
    status, cfg = helpers.http_get_json(
        api_server + "/api/config", headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert set(cfg["secret_storage"]) == {"backend", "status"}
    assert cfg["secret_storage"] == {"backend": "custom", "status": "secure"}
    # secret values are still never echoed back
    assert cfg["api_key_configured"] is True
    assert cfg["auth_configured"] is True
    assert "api_key" not in cfg
    assert "server" not in cfg
    body = json.dumps(cfg)
    assert "dummy-status-key" not in body
    assert "dummy-status-cookie" not in body


def test_api_grab_save_failure_reports_false(api_server, api_token, data_server,
                                             fixtures_dir, monkeypatch):
    data_server.gw.save_config({
        "formula_url": os.path.join(fixtures_dir, "formula_valid.json"),
        "server": {"workspace_id": "wrk_grab"},
    })
    monkeypatch.setattr(
        data_server.gw, "read_auth_cookie_from_webdata", lambda: "dummy-grab-cookie"
    )
    secret_store.set_backend(_FailingBackend())

    status, data = helpers.http_post_json_response(
        api_server + "/api/grab", {}, headers=helpers.auth_headers(api_token)
    )
    assert status == 200
    assert data["ok"] is False
    assert "error" in data
    assert "dummy-grab-cookie" not in json.dumps(data)
