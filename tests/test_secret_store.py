"""Phase 2B: encrypted secret store + fail-safe config migration.

All secrets here are dummy values. The autouse ``isolated_secret_store`` fixture
(conftest) installs a reversible fake backend and a per-test secret file, so no
test touches real DPAPI / credential storage. The single DPAPI roundtrip test
uses the real Windows API with dummy bytes only (skipped elsewhere).
"""
import json
import os
import sys

import pytest

import secret_store


def _read_raw(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write_config(gw, cfg):
    with open(gw.CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# backend abstraction
# ---------------------------------------------------------------------------
def test_fake_backend_roundtrip():
    backend = secret_store.get_backend()
    payload = b"phase2b-dummy-\x00\xff-bytes"
    assert backend.decrypt(backend.encrypt(payload)) == payload


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_backend_roundtrip():
    backend = secret_store.DPAPIBackend()
    payload = b"phase2b-dpapi-dummy-\x00\x01\xfe"
    assert backend.decrypt(backend.encrypt(payload)) == payload


def test_store_available_reflects_backend():
    assert secret_store.store_available() is True
    secret_store.set_backend(None)
    assert secret_store.store_available() is False
    assert secret_store.load_secrets() == {}


# ---------------------------------------------------------------------------
# file format / load errors
# ---------------------------------------------------------------------------
def test_save_load_file_format():
    secret_store.save_secrets({"api_key": "dummy-key-abc"})
    raw = json.loads(_read_raw(secret_store.secret_file_path()))
    assert raw["v"] == 1
    assert isinstance(raw["data"], str)
    # plaintext must not appear in the on-disk wrapper
    assert "dummy-key-abc" not in _read_raw(secret_store.secret_file_path())
    assert secret_store.load_secrets() == {"api_key": "dummy-key-abc"}


def test_missing_file_returns_empty():
    assert not os.path.exists(secret_store.secret_file_path())
    assert secret_store.load_secrets() == {}


def test_corrupt_file_raises():
    path = secret_store.secret_file_path()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("this is not json")
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.load_secrets()


def test_garbage_payload_raises():
    path = secret_store.secret_file_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"v": 1, "data": "!!!not-base64-decryptable!!!"}, fh)
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.load_secrets()


def test_unsupported_version_raises():
    path = secret_store.secret_file_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"v": 999, "data": ""}, fh)
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.load_secrets()


def test_env_override_changes_path(monkeypatch, tmp_path):
    secret_store.save_secrets({"api_key": "key-one"})
    original = secret_store.secret_file_path()
    assert os.path.exists(original)
    other = tmp_path / "other" / "secrets.enc"
    monkeypatch.setenv("OPENCODE_WIDGET_SECRET_FILE", str(other))
    assert secret_store.secret_file_path() == str(other)
    assert secret_store.load_secrets() == {}
    assert os.path.exists(original)


# ---------------------------------------------------------------------------
# load_config migration
# ---------------------------------------------------------------------------
def test_plaintext_secret_migration(gw):
    _write_config(gw, {
        "api_key": "dummy-api-key-123",
        "server": {"auth_cookie": "dummy-cookie-xyz", "workspace_id": "wrk_dummy"},
        "other": 42,
    })

    cfg = gw.load_config()
    assert cfg["api_key"] == "dummy-api-key-123"
    assert cfg["server"]["auth_cookie"] == "dummy-cookie-xyz"
    assert cfg["server"]["workspace_id"] == "wrk_dummy"
    assert cfg["other"] == 42

    # secrets moved to the encrypted store
    stored = secret_store.load_secrets()
    assert stored["api_key"] == "dummy-api-key-123"
    assert stored["auth_cookie"] == "dummy-cookie-xyz"

    # config.json no longer carries plaintext secrets, workspace_id survives
    raw = _read_raw(gw.CONFIG_PATH)
    assert "dummy-api-key-123" not in raw
    assert "dummy-cookie-xyz" not in raw
    assert "api_key" not in raw
    assert "auth_cookie" not in raw
    assert "wrk_dummy" in raw

    # second load is idempotent: same values, file unchanged
    before = _read_raw(gw.CONFIG_PATH)
    cfg2 = gw.load_config()
    assert cfg2["api_key"] == "dummy-api-key-123"
    assert cfg2["server"]["auth_cookie"] == "dummy-cookie-xyz"
    assert _read_raw(gw.CONFIG_PATH) == before


def test_secret_migration_failure_is_safe(gw):
    _write_config(gw, {
        "api_key": "dummy-api-key-fail",
        "server": {"auth_cookie": "dummy-cookie-fail", "workspace_id": "wrk_fail"},
    })
    before = open(gw.CONFIG_PATH, "rb").read()

    class _BadEncrypt:
        def encrypt(self, data):
            raise RuntimeError("boom")

        def decrypt(self, data):
            return data

    secret_store.set_backend(_BadEncrypt())

    cfg = gw.load_config()
    assert cfg["api_key"] == "dummy-api-key-fail"
    assert cfg["server"]["auth_cookie"] == "dummy-cookie-fail"

    # config.json byte-identical, no partial migration / data loss
    with open(gw.CONFIG_PATH, "rb") as fh:
        assert fh.read() == before
    assert not os.path.exists(secret_store.secret_file_path())


def test_load_config_without_secrets_no_migration(gw):
    _write_config(gw, {"server": {"workspace_id": "wrk_only"}, "other": 7})
    cfg = gw.load_config()
    assert cfg == {"server": {"workspace_id": "wrk_only"}, "other": 7}
    assert not os.path.exists(secret_store.secret_file_path())


# ---------------------------------------------------------------------------
# save_config integration
# ---------------------------------------------------------------------------
def test_save_config_extracts_secrets(gw):
    gw.save_config({
        "api_key": "dummy-save-key",
        "server": {"auth_cookie": "dummy-save-cookie", "workspace_id": "wrk_save"},
        "other": 1,
    })

    raw = _read_raw(gw.CONFIG_PATH)
    assert "dummy-save-key" not in raw
    assert "dummy-save-cookie" not in raw
    assert "wrk_save" in raw
    assert "other" in raw

    stored = secret_store.load_secrets()
    assert stored["api_key"] == "dummy-save-key"
    assert stored["auth_cookie"] == "dummy-save-cookie"

    cfg = gw.load_config()
    assert cfg["api_key"] == "dummy-save-key"
    assert cfg["server"]["auth_cookie"] == "dummy-save-cookie"
    assert cfg["server"]["workspace_id"] == "wrk_save"


def test_save_config_empty_clears_stored_secret(gw):
    gw.save_config({
        "api_key": "dummy-clear-key",
        "server": {"auth_cookie": "dummy-clear-cookie", "workspace_id": "wrk_clear"},
    })
    assert secret_store.load_secrets()["api_key"] == "dummy-clear-key"

    gw.save_config({
        "api_key": "",
        "server": {"auth_cookie": "", "workspace_id": "wrk_clear"},
    })
    stored = secret_store.load_secrets()
    assert "api_key" not in stored
    assert "auth_cookie" not in stored

    raw = _read_raw(gw.CONFIG_PATH)
    assert "wrk_clear" in raw
    assert "dummy-clear-key" not in raw


def test_save_config_backend_unavailable_falls_back_to_plaintext(gw):
    secret_store.set_backend(None)
    gw.save_config({"api_key": "plain-key-fallback", "other": 2})

    raw = _read_raw(gw.CONFIG_PATH)
    assert "plain-key-fallback" in raw
    assert not os.path.exists(secret_store.secret_file_path())

    cfg = gw.load_config()
    assert cfg["api_key"] == "plain-key-fallback"


# Phase 2.1: this test used to assert the old silent-plaintext fallback when a
# store write failed while a backend was available. Writes now fail closed.
def test_save_config_store_failure_fails_closed(gw):
    _write_config(gw, {"server": {"workspace_id": "wrk_before"}, "other": 3})
    before = open(gw.CONFIG_PATH, "rb").read()

    class _BadEncrypt:
        def encrypt(self, data):
            raise RuntimeError("nope")

        def decrypt(self, data):
            return data

    secret_store.set_backend(_BadEncrypt())
    with pytest.raises(secret_store.SecretStoreError):
        gw.save_config({"api_key": "plain-on-store-failure", "other": 4})

    # Phase 2.1: config.json untouched and the secret never lands on disk.
    with open(gw.CONFIG_PATH, "rb") as fh:
        assert fh.read() == before
    assert "plain-on-store-failure" not in _read_raw(gw.CONFIG_PATH)
    assert not os.path.exists(secret_store.secret_file_path())


def test_clean_install_does_not_create_empty_store(gw):
    """Phase 7: saving a secret-less config on a clean install must not create an
    empty encrypted store (no unnecessary artifacts)."""
    assert not os.path.exists(secret_store.secret_file_path())
    gw.save_config({"formula_enabled": False})
    assert not os.path.exists(secret_store.secret_file_path())
    # once a real secret is saved the store is created
    gw.save_config({"api_key": "dummy-key-value"})
    assert os.path.exists(secret_store.secret_file_path())
