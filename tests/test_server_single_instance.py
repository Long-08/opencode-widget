"""RC.2 soak Day-0 finding F1: Windows duplicate-server guard must decide
ownership BEFORE binding.

``ThreadingHTTPServer`` inherits ``allow_reuse_address = 1``; on Windows
SO_REUSEADDR lets a second ``data_server.py`` silently double-bind
127.0.0.1:PORT, so the OSError-based guard in ``main()`` never fires. The
second server clobbers runtime.json and the first server is orphaned forever
(it never sees auth traffic again, so its idle watchdog never exits).

Live-reproduced during the RC.2 soak (two LISTENING sockets on 127.0.0.1:8766).
The fix: before opening the port, yield to a live, healthy owner named by
runtime.json. Unit tests pin the decision helper; the OSError path stays as
the POSIX / simultaneous-race fallback.
"""
import os

import data_server as ds


def _info(pid=4321, port=8766, token="t"):
    return {"pid": pid, "port": port, "token": token, "created": 0}


def test_healthy_owned_server_must_exit_before_bind():
    assert ds._existing_owned_server_running(
        _info(), pid_alive_fn=lambda pid: True, probe_fn=lambda port: True,
    ) is True


def test_dead_pid_is_not_a_running_owner():
    assert ds._existing_owned_server_running(
        _info(), pid_alive_fn=lambda pid: False, probe_fn=lambda port: True,
    ) is False


def test_unhealthy_probe_proceeds():
    assert ds._existing_owned_server_running(
        _info(), pid_alive_fn=lambda pid: True, probe_fn=lambda port: False,
    ) is False


def test_own_pid_is_not_another_owner():
    assert ds._existing_owned_server_running(
        _info(pid=os.getpid()), pid_alive_fn=lambda pid: True,
        probe_fn=lambda port: True,
    ) is False


def test_missing_info_proceeds():
    assert ds._existing_owned_server_running(
        None, pid_alive_fn=lambda pid: True, probe_fn=lambda port: True,
    ) is False


def test_malformed_info_proceeds():
    for info in ({}, {"pid": "x", "port": 8766}, {"pid": 0, "port": 8766},
                 {"pid": -1, "port": 8766}, {"pid": 4321, "port": "abc"},
                 {"pid": True, "port": 8766}, {"pid": 4321, "port": 0}):
        assert ds._existing_owned_server_running(
            info, pid_alive_fn=lambda pid: True, probe_fn=lambda port: True,
        ) is False, info


def test_probe_receives_numeric_port():
    seen = {}

    def probe(port):
        seen["port"] = port
        return True

    ds._existing_owned_server_running(
        _info(port="8766"), pid_alive_fn=lambda pid: True, probe_fn=probe,
    )
    assert seen["port"] == 8766
