"""Phase 7: config schema versioning + legacy-safe migration.

Dummy secret values only. The autouse conftest fixtures redirect CONFIG_PATH and
the secret store into per-test temp paths; the migration cases redirect the data
directory through ``OPENCODE_WIDGET_DATA_DIR``.
"""
import json
import os

import pytest

import paths
import secret_store


def _read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write_raw(gw, cfg):
    with open(gw.CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


class _FailingBackend:
    """Backend whose encrypt always fails (write path unavailable)."""

    def encrypt(self, data):
        raise RuntimeError("boom")

    def decrypt(self, data):
        return data


# ---------------------------------------------------------------------------
# version migration
# ---------------------------------------------------------------------------
def test_legacy_config_gains_version_and_preserves_keys(gw):
    _write_raw(gw, {
        "api_key": "dummy-legacy-key",
        "server": {"auth_cookie": "dummy-legacy-cookie", "workspace_id": "wrk_legacy"},
        "other": 42,
    })

    cfg = gw.load_config()
    assert gw.config_status() == "ok"
    # returned values are intact (secrets merged back from the store)
    assert cfg["api_key"] == "dummy-legacy-key"
    assert cfg["server"]["auth_cookie"] == "dummy-legacy-cookie"
    assert cfg["server"]["workspace_id"] == "wrk_legacy"
    assert cfg["other"] == 42

    # on disk: version bumped, non-secret keys preserved, plaintext stripped
    raw_text = _read_text(gw.CONFIG_PATH)
    raw = json.loads(raw_text)
    assert raw["config_version"] == 1
    assert raw["other"] == 42
    assert raw["server"]["workspace_id"] == "wrk_legacy"
    assert "api_key" not in raw
    assert "auth_cookie" not in raw
    assert "dummy-legacy-key" not in raw_text
    assert "dummy-legacy-cookie" not in raw_text

    stored = secret_store.load_secrets()
    assert stored["api_key"] == "dummy-legacy-key"
    assert stored["auth_cookie"] == "dummy-legacy-cookie"

    # a second read is idempotent and byte-stable
    before = _read_bytes(gw.CONFIG_PATH)
    cfg2 = gw.load_config()
    assert _read_bytes(gw.CONFIG_PATH) == before
    assert cfg2["api_key"] == "dummy-legacy-key"


def test_plain_config_without_version_gains_it_on_load(gw):
    _write_raw(gw, {"server": {"workspace_id": "wrk_plain"}, "other": 7})
    cfg = gw.load_config()
    assert cfg["other"] == 7
    assert cfg["server"]["workspace_id"] == "wrk_plain"
    raw = json.loads(_read_text(gw.CONFIG_PATH))
    assert raw["config_version"] == 1
    assert gw.config_status() == "ok"


def test_save_config_always_writes_version(gw):
    gw.save_config({"other": 1})
    raw = json.loads(_read_text(gw.CONFIG_PATH))
    assert raw["config_version"] == 1
    assert raw["other"] == 1


def test_legacy_config_without_backend_keeps_plaintext_and_gains_version(gw):
    secret_store.set_backend(None)
    _write_raw(gw, {"api_key": "dummy-plain", "other": 8})

    cfg = gw.load_config()
    assert cfg["api_key"] == "dummy-plain"

    raw = json.loads(_read_text(gw.CONFIG_PATH))
    assert raw["config_version"] == 1
    # no backend -> plaintext compatibility is preserved verbatim
    assert raw["api_key"] == "dummy-plain"
    assert raw["other"] == 8


def test_load_config_triggers_legacy_migration_once(gw, monkeypatch):
    calls = []
    monkeypatch.setattr(gw, "_LEGACY_MIGRATION_DONE", False)
    monkeypatch.setattr(
        gw.paths, "migrate_legacy_data",
        lambda: (calls.append(1), {"migrated": [], "skipped": []})[1],
    )
    gw.load_config()
    assert len(calls) == 1
    # only the first read migrates
    gw.load_config()
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# future version
# ---------------------------------------------------------------------------
def test_future_config_version_is_left_untouched(gw):
    _write_raw(gw, {
        "config_version": 99,
        "server": {"workspace_id": "wrk_future"},
        "other": 9,
    })
    before = _read_bytes(gw.CONFIG_PATH)

    cfg = gw.load_config()
    assert gw.config_status() == "future_version"
    # parsed object returned as-is
    assert cfg["config_version"] == 99
    assert cfg["server"]["workspace_id"] == "wrk_future"
    assert cfg["other"] == 9
    # file (and secret store) untouched
    assert _read_bytes(gw.CONFIG_PATH) == before
    assert not os.path.exists(secret_store.secret_file_path())


# ---------------------------------------------------------------------------
# corrupt config
# ---------------------------------------------------------------------------
def test_corrupt_config_is_backed_up_and_defaults_used(gw):
    with open(gw.CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")

    cfg = gw.load_config()
    assert gw.config_status() == "corrupt"
    assert cfg == {}
    # original was moved aside, not left in place, and no crash occurred
    assert not os.path.exists(gw.CONFIG_PATH)
    assert os.path.exists(gw.CONFIG_PATH + ".corrupt.bak")
    assert "not valid json" in _read_text(gw.CONFIG_PATH + ".corrupt.bak")


def test_corrupt_backup_keeps_at_most_one(gw):
    with open(gw.CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write("garbage one")
    gw.load_config()
    with open(gw.CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write("garbage two")
    gw.load_config()

    directory = os.path.dirname(gw.CONFIG_PATH)
    backups = [n for n in os.listdir(directory) if n.endswith(".corrupt.bak")]
    assert len(backups) == 1
    assert "garbage two" in _read_text(os.path.join(directory, backups[0]))


# ---------------------------------------------------------------------------
# secret handling stays fail-closed
# ---------------------------------------------------------------------------
def test_save_config_store_failure_keeps_file_untouched(gw):
    gw.save_config({"server": {"workspace_id": "wrk_before"}, "other": 3})
    before = _read_bytes(gw.CONFIG_PATH)

    secret_store.set_backend(_FailingBackend())
    with pytest.raises(secret_store.SecretStoreError):
        gw.save_config({"api_key": "dummy-closed", "other": 4})

    assert _read_bytes(gw.CONFIG_PATH) == before
    assert b"dummy-closed" not in _read_bytes(gw.CONFIG_PATH)


def test_load_config_store_failure_does_not_persist_version(gw):
    _write_raw(gw, {
        "api_key": "dummy-load-key",
        "server": {"auth_cookie": "dummy-load-cookie", "workspace_id": "wrk"},
    })
    before = _read_bytes(gw.CONFIG_PATH)

    secret_store.set_backend(_FailingBackend())
    cfg = gw.load_config()

    # plaintext values are still returned, file is byte-identical (no version
    # write, no partial secret migration)
    assert cfg["api_key"] == "dummy-load-key"
    assert cfg["server"]["auth_cookie"] == "dummy-load-cookie"
    assert _read_bytes(gw.CONFIG_PATH) == before
    assert gw.config_status() == "ok"


# ---------------------------------------------------------------------------
# data-dir isolation after migration
# ---------------------------------------------------------------------------
def test_data_dir_config_used_after_migration(gw, monkeypatch, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("OPENCODE_WIDGET_DATA_DIR", str(data))
    monkeypatch.setattr(paths, "app_dir", lambda: str(install))
    monkeypatch.setattr(gw, "_LEGACY_MIGRATION_DONE", False)
    (install / "config.json").write_text(
        json.dumps({"server": {"workspace_id": "wrk_legacy"}, "other": 5}),
        encoding="utf-8",
    )

    # Point the app at the data-dir config (overrides the conftest tmp path).
    monkeypatch.setattr(gw, "CONFIG_PATH", paths.config_path())

    cfg = gw.load_config()
    assert gw.config_status() == "ok"
    assert cfg["server"]["workspace_id"] == "wrk_legacy"
    assert cfg["other"] == 5
    # the migrated data-dir copy is what the app now reads/owns
    assert os.path.exists(paths.config_path())
    assert json.loads(_read_text(paths.config_path()))["config_version"] == 1
    # legacy install-dir file is left in place, untouched
    assert (install / "config.json").exists()
