"""Phase 6A: unit tests for the pure runtime_lifecycle helpers.

No I/O beyond the explicitly injected/real tmp files used by ``stale_files``.
"""
import os
import time

import runtime_lifecycle as rl


# ---------------------------------------------------------------------------
# pid_alive
# ---------------------------------------------------------------------------
def test_pid_alive_injected_alive():
    assert rl.pid_alive(1234, _impl=lambda pid: True) is True


def test_pid_alive_injected_dead():
    assert rl.pid_alive(1234, _impl=lambda pid: False) is False


def test_pid_alive_none_and_invalid_never_calls_impl():
    calls = []
    impl = lambda pid: calls.append(pid) or True
    assert rl.pid_alive(None, _impl=impl) is False
    assert rl.pid_alive(0, _impl=impl) is False
    assert rl.pid_alive(-5, _impl=impl) is False
    assert rl.pid_alive("1234", _impl=impl) is False
    assert calls == []


def test_pid_alive_impl_raising_is_false():
    def boom(pid):
        raise RuntimeError("nope")

    assert rl.pid_alive(1234, _impl=boom) is False


def test_pid_alive_real_current_process():
    # The real Win32 OpenProcess path must report this live process.
    assert rl.pid_alive(os.getpid()) is True


# ---------------------------------------------------------------------------
# should_exit_idle
# ---------------------------------------------------------------------------
def test_should_exit_idle_no_auth_seen():
    assert rl.should_exit_idle(1000.0, None, 300) is False


def test_should_exit_idle_timeout_zero_disables():
    assert rl.should_exit_idle(1000.0, 0.0, 0) is False


def test_should_exit_idle_negative_timeout_disables():
    assert rl.should_exit_idle(1000.0, 0.0, -5) is False


def test_should_exit_idle_below_threshold():
    now, last, timeout = 1000.0, 900.0, 300.0
    assert rl.should_exit_idle(now, last, timeout) is False


def test_should_exit_idle_above_threshold():
    now, last, timeout = 1301.0, 1000.0, 300.0
    assert rl.should_exit_idle(now, last, timeout) is True


def test_should_exit_idle_exact_boundary_is_not_idle():
    now, last, timeout = 1300.0, 1000.0, 300.0
    assert rl.should_exit_idle(now, last, timeout) is False


# ---------------------------------------------------------------------------
# classify_port_conflict
# ---------------------------------------------------------------------------
def test_classify_existing_widget():
    info = {"token": "abc", "pid": 42, "instance_id": "deadbeef"}
    assert rl.classify_port_conflict(info, True) == "existing_widget"


def test_classify_probe_failed_is_foreign():
    info = {"token": "abc", "pid": 42}
    assert rl.classify_port_conflict(info, False) == "foreign"


def test_classify_missing_token_is_foreign():
    assert rl.classify_port_conflict({"pid": 42}, True) == "foreign"


def test_classify_missing_pid_is_foreign():
    assert rl.classify_port_conflict({"token": "abc"}, True) == "foreign"


def test_classify_invalid_info_is_foreign():
    assert rl.classify_port_conflict(None, True) == "foreign"
    assert rl.classify_port_conflict("nonsense", True) == "foreign"
    assert rl.classify_port_conflict({}, True) == "foreign"


# ---------------------------------------------------------------------------
# is_owned_cookie_temp_name
# ---------------------------------------------------------------------------
def test_owned_cookie_temp_name_positive():
    assert rl.is_owned_cookie_temp_name("opencode-widget-cookie-abc-123.db") is True
    assert rl.is_owned_cookie_temp_name("opencode-widget-cookie-") is True


def test_owned_cookie_temp_name_negative():
    assert rl.is_owned_cookie_temp_name("cookie_abc.db") is False
    assert rl.is_owned_cookie_temp_name("notes.txt") is False
    assert rl.is_owned_cookie_temp_name("opencode-widget-other.db") is False
    assert rl.is_owned_cookie_temp_name(None) is False


# ---------------------------------------------------------------------------
# stale_files
# ---------------------------------------------------------------------------
def _touch(path, mtime):
    with open(path, "wb") as fh:
        fh.write(b"x")
    os.utime(path, (mtime, mtime))


def test_stale_files_age_filter(tmp_path):
    now = time.time()
    old = tmp_path / "opencode-widget-cookie-old-1.db"
    fresh = tmp_path / "opencode-widget-cookie-fresh-2.db"
    unrelated = tmp_path / "keep-me.txt"
    _touch(old, now - 10000)
    _touch(fresh, now)
    _touch(unrelated, now - 10000)

    result = rl.stale_files(
        str(tmp_path), rl.is_owned_cookie_temp_name, 3600, now,
        _listdir=lambda d: ["opencode-widget-cookie-old-1.db",
                            "opencode-widget-cookie-fresh-2.db",
                            "keep-me.txt"],
    )
    assert result == [str(old)]
    # unrelated files are preserved on disk
    assert unrelated.exists()


def test_stale_files_missing_dir(tmp_path):
    assert rl.stale_files(str(tmp_path / "nope"), rl.is_owned_cookie_temp_name,
                          3600, time.time()) == []


def test_stale_files_missing_dir_with_listdir(tmp_path):
    def boom(_d):
        raise FileNotFoundError

    assert rl.stale_files(str(tmp_path), rl.is_owned_cookie_temp_name,
                          3600, time.time(), _listdir=boom) == []


def test_stale_files_skips_vanished_entries(tmp_path):
    now = time.time()
    real = tmp_path / "opencode-widget-cookie-real.db"
    _touch(real, now - 10000)
    result = rl.stale_files(
        str(tmp_path), rl.is_owned_cookie_temp_name, 3600, now,
        _listdir=lambda d: ["opencode-widget-cookie-real.db",
                            "opencode-widget-cookie-ghost.db"],
    )
    assert result == [str(real)]


# ---------------------------------------------------------------------------
# new_instance_id
# ---------------------------------------------------------------------------
def test_new_instance_id_is_hex():
    iid = rl.new_instance_id()
    assert isinstance(iid, str)
    assert len(iid) == 32
    assert set(iid) <= set("0123456789abcdef")


def test_new_instance_id_unique():
    ids = {rl.new_instance_id() for _ in range(50)}
    assert len(ids) == 50
