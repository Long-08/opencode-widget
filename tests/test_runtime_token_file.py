"""Phase 2A/2.1: runtime token + runtime.json file lifecycle.

Isolation comes from the autouse conftest fixture: OPENCODE_WIDGET_RUNTIME_DIR
is redirected to a per-test tmp directory, so these tests never touch a real
runtime file. Callers reset data_server._TOKEN to simulate a fresh startup.
"""
import json
import os

RUNTIME_FILE = "runtime.json"


def _runtime_path(data_server):
    return os.path.join(data_server._runtime_dir(), RUNTIME_FILE)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_write_runtime_info_contents(data_server, monkeypatch):
    monkeypatch.setattr(data_server, "_TOKEN", None)

    data_server._write_runtime_info(4321)

    path = _runtime_path(data_server)
    assert os.path.exists(path)
    info = _read_json(path)
    assert set(info) == {"token", "port", "pid", "created"}
    assert isinstance(info["token"], str)
    assert len(info["token"]) >= 43  # secrets.token_urlsafe(32)
    assert info["token"] == data_server._TOKEN
    assert info["port"] == 4321
    assert info["pid"] == os.getpid()
    assert isinstance(info["created"], int)


def test_write_runtime_info_overwrites_stale_file_with_new_token(data_server, monkeypatch):
    """A new startup must never reuse the token of a stale runtime.json."""
    monkeypatch.setattr(data_server, "_TOKEN", None)
    path = _runtime_path(data_server)
    stale = {
        "token": "stale-token-from-a-dead-run",
        "port": 1111,
        "pid": os.getpid() + 4242,
        "created": 1,
    }
    _write_json(path, stale)

    data_server._write_runtime_info(9999)

    info = _read_json(path)
    assert info["token"] != stale["token"]
    assert info["pid"] == os.getpid()
    assert info["port"] == 9999


def test_ensure_runtime_token_distinct_after_reset(data_server, monkeypatch):
    monkeypatch.setattr(data_server, "_TOKEN", None)
    first = data_server.ensure_runtime_token()
    assert isinstance(first, str)
    assert len(first) >= 43
    # cached while _TOKEN is set
    assert data_server.ensure_runtime_token() == first

    monkeypatch.setattr(data_server, "_TOKEN", None)
    second = data_server.ensure_runtime_token()
    assert second != first

    monkeypatch.setattr(data_server, "_TOKEN", None)
    third = data_server.ensure_runtime_token()
    assert third not in (first, second)


def test_cleanup_runtime_info_removes_own_file(data_server, monkeypatch):
    monkeypatch.setattr(data_server, "_TOKEN", None)
    data_server._write_runtime_info(1234)
    path = _runtime_path(data_server)
    assert os.path.exists(path)

    data_server._cleanup_runtime_info()

    assert not os.path.exists(path)
    # idempotent: calling again on a missing file is a quiet no-op
    data_server._cleanup_runtime_info()
    assert not os.path.exists(path)


def test_cleanup_runtime_info_keeps_foreign_file(data_server):
    path = _runtime_path(data_server)
    foreign = {
        "token": "foreign-process-token",
        "port": 2222,
        "pid": os.getpid() + 4242,
        "created": 2,
    }
    _write_json(path, foreign)

    data_server._cleanup_runtime_info()

    assert os.path.exists(path)
    assert _read_json(path) == foreign


def test_cleanup_runtime_info_missing_file_no_error(data_server):
    path = _runtime_path(data_server)
    assert not os.path.exists(path)

    data_server._cleanup_runtime_info()  # must not raise

    assert not os.path.exists(path)


def test_cleanup_runtime_info_corrupt_file_is_untouched(data_server):
    path = _runtime_path(data_server)
    _write_json(path, {"token": "x"})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")

    data_server._cleanup_runtime_info()  # must not raise

    assert os.path.exists(path)
    assert open(path, "r", encoding="utf-8").read() == "{not valid json"
