"""Phase 7: path resolution + legacy data migration.

These tests never touch real user data: every case that resolves a data
directory redirects ``OPENCODE_WIDGET_DATA_DIR`` (or the platform base) to a
per-test temp directory via ``monkeypatch``.
"""
import json
import os

import paths


def _jsonl(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# data_dir / env override
# ---------------------------------------------------------------------------
def test_data_dir_env_override_creates_dir(monkeypatch, tmp_path):
    target = tmp_path / "custom-data"
    monkeypatch.setenv("OPENCODE_WIDGET_DATA_DIR", str(target))
    resolved = paths.data_dir()
    assert resolved == os.path.abspath(str(target))
    assert os.path.isdir(resolved)


def test_data_dir_default_under_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_WIDGET_DATA_DIR", raising=False)
    if os.name == "nt":
        base = tmp_path / "Roaming"
        monkeypatch.setenv("APPDATA", str(base))
        expected = os.path.join(str(base), "opencode-widget")
    else:
        base = tmp_path / "xdg"
        monkeypatch.setenv("XDG_DATA_HOME", str(base))
        expected = os.path.join(str(base), "opencode-widget")
    assert paths.data_dir() == expected
    assert os.path.isdir(expected)


def test_path_helpers_live_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_WIDGET_DATA_DIR", str(tmp_path / "d"))
    base = paths.data_dir()
    assert os.path.dirname(paths.config_path()) == base
    assert os.path.dirname(paths.secrets_path()) == base
    assert os.path.dirname(paths.usage_remote_db_path()) == base
    assert os.path.dirname(paths.server_usage_db_path()) == base
    assert os.path.dirname(paths.formula_cache_path()) == base
    assert paths.config_path().endswith("config.json")
    assert paths.secrets_path().endswith("secrets.enc")
    assert paths.usage_remote_db_path().endswith("usage_remote.db")
    assert paths.server_usage_db_path().endswith("server_usage.db")
    # helpers honour an overridden data_dir for the same reason
    assert paths.formula_cache_path().endswith("formula_cache.json")


# ---------------------------------------------------------------------------
# app_version
# ---------------------------------------------------------------------------
def test_app_version_reads_package_json():
    package = os.path.join(paths.app_dir(), "electron", "package.json")
    assert paths.app_version() == _jsonl(package)["version"]


def test_app_version_falls_back_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    assert paths.app_version() == "0.0.0"


def test_app_version_falls_back_on_invalid_json(monkeypatch, tmp_path):
    (tmp_path / "electron").mkdir()
    (tmp_path / "electron" / "package.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    assert paths.app_version() == "0.0.0"


# ---------------------------------------------------------------------------
# migrate_legacy_data
# ---------------------------------------------------------------------------
def _legacy_install(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("OPENCODE_WIDGET_DATA_DIR", str(data))
    monkeypatch.setattr(paths, "app_dir", lambda: str(install))
    return install, data


def test_migrate_copies_only_missing_files(monkeypatch, tmp_path):
    install, data = _legacy_install(tmp_path, monkeypatch)
    (install / "config.json").write_text('{"a": 1}', encoding="utf-8")
    (install / "secrets.enc").write_bytes(b"\x00\x01\x02cipher")
    (install / "usage_remote.db").write_bytes(b"remote-db")
    (install / "server_usage.db").write_bytes(b"server-db")
    (install / "unrelated.txt").write_text("do not copy", encoding="utf-8")

    summary = paths.migrate_legacy_data()

    assert set(summary["migrated"]) == {
        "config.json", "secrets.enc", "usage_remote.db", "server_usage.db"
    }
    assert summary["skipped"] == []
    assert (data / "config.json").read_text(encoding="utf-8") == '{"a": 1}'
    assert (data / "secrets.enc").read_bytes() == b"\x00\x01\x02cipher"
    assert (data / "usage_remote.db").read_bytes() == b"remote-db"
    assert (data / "server_usage.db").read_bytes() == b"server-db"
    # unrelated files are never copied
    assert not (data / "unrelated.txt").exists()


def test_migrate_leaves_originals_untouched(monkeypatch, tmp_path):
    install, data = _legacy_install(tmp_path, monkeypatch)
    (install / "config.json").write_text('{"keep": true}', encoding="utf-8")

    paths.migrate_legacy_data()

    assert (install / "config.json").read_text(encoding="utf-8") == '{"keep": true}'
    assert (data / "config.json").read_text(encoding="utf-8") == '{"keep": true}'


def test_migrate_is_idempotent(monkeypatch, tmp_path):
    install, data = _legacy_install(tmp_path, monkeypatch)
    (install / "config.json").write_text('{"a": 1}', encoding="utf-8")

    first = paths.migrate_legacy_data()
    assert first["migrated"] == ["config.json"]
    before = (data / "config.json").read_bytes()

    second = paths.migrate_legacy_data()
    assert second["migrated"] == []
    assert "config.json" in second["skipped"]
    assert (data / "config.json").read_bytes() == before


def test_migrate_never_overwrites_existing_data(monkeypatch, tmp_path):
    install, data = _legacy_install(tmp_path, monkeypatch)
    (install / "config.json").write_text('{"legacy": true}', encoding="utf-8")
    data.mkdir(parents=True, exist_ok=True)
    (data / "config.json").write_text('{"existing": true}', encoding="utf-8")

    summary = paths.migrate_legacy_data()

    assert "config.json" in summary["skipped"]
    assert summary["migrated"] == []
    assert (data / "config.json").read_text(encoding="utf-8") == '{"existing": true}'


def test_migrate_swallows_failures(monkeypatch, tmp_path):
    # app_dir points at a non-existent directory: nothing to migrate, no crash.
    monkeypatch.setenv("OPENCODE_WIDGET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path / "missing-install"))
    summary = paths.migrate_legacy_data()
    assert summary == {"migrated": [], "skipped": []}


def test_migrate_missing_files_are_not_reported(monkeypatch, tmp_path):
    install, _data = _legacy_install(tmp_path, monkeypatch)
    (install / "usage_remote.db").write_bytes(b"only-this")
    summary = paths.migrate_legacy_data()
    assert summary["migrated"] == ["usage_remote.db"]
    assert summary["skipped"] == []
