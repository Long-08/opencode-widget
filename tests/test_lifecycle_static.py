"""Phase 6A: static guards for Electron lifecycle hardening.

Checks electron/main.js and electron/runtime_manager.js only (factual string /
block assertions, no execution): single-instance lock with early quit,
second-instance restore+focus, will-quit cleanup (timers + login window + owned
server), debug-gated devTools, unchanged renderer hardening, the heartbeat path,
no auto-open devtools, and no token/cookie leaking into snap debug logs.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
MAIN_JS = os.path.join(PROJECT_DIR, "electron", "main.js")
RUNTIME_MANAGER_JS = os.path.join(PROJECT_DIR, "electron", "runtime_manager.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _balanced_block(src, open_idx):
    depth = 0
    in_str = None
    i = open_idx
    while i < len(src):
        ch = src[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
        i += 1
    raise AssertionError("unbalanced braces while scanning source")


def _block_after_marker(src, marker):
    start = src.index(marker)
    brace = src.index("{", start)
    return _balanced_block(src, brace)


def test_single_instance_lock_present_and_quits_early():
    src = _read(MAIN_JS)
    assert "app.requestSingleInstanceLock()" in src
    # The lock must be taken before any window can be created.
    assert src.index("app.requestSingleInstanceLock()") < src.index("function createWindow()")
    assert re.search(
        r"if\s*\(\s*!gotLock\s*\)\s*\{[^}]*app\.quit\(\);[^}]*return;[^}]*\}",
        src,
        re.S,
    ), "missing early app.quit()+return when the single-instance lock is not acquired"


def test_second_instance_restores_and_focuses_without_state_changes():
    src = _read(MAIN_JS)
    block = _block_after_marker(src, "app.on('second-instance'")
    for call in ("isMinimized()", "restore()", "show()", "focus()"):
        assert call in block, "second-instance handler missing %s" % call
    # It must never resize or change snap state.
    assert "SIZES" not in block
    assert "snapped" not in block


def test_will_quit_cleanup_stops_timers_closes_login_and_terminates_server():
    src = _read(MAIN_JS)
    block = _block_after_marker(src, "app.on('will-quit'")
    assert "quitCleanupStarted" in block  # run-once guard
    assert "preventDefault()" in block
    assert "stopHeartbeat()" in block
    assert "stopClickThroughPoll()" in block
    assert "loginWin" in block and ".close()" in block
    assert "terminateOwnedServer" in block


def test_devtools_is_debug_gated_and_debug_is_env_driven():
    src = _read(MAIN_JS)
    assert "const DEBUG = runtimeManager.debugEnabled(process.env)" in src
    assert src.count("devTools: DEBUG") >= 2, "devTools must be set on both windows"
    assert "debugEnabled" in _read(RUNTIME_MANAGER_JS)


def test_debug_env_flag_present():
    assert "OPENCODE_WIDGET_DEBUG" in _read(RUNTIME_MANAGER_JS)


def test_renderer_hardening_unchanged_on_both_windows():
    src = _read(MAIN_JS)
    for prop in ("contextIsolation: true", "nodeIntegration: false", "sandbox: true"):
        assert src.count(prop) >= 2, "%s must remain on both windows" % prop


def test_heartbeat_path_and_interval_present():
    assert "'/api/state?heartbeat=1'" in _read(RUNTIME_MANAGER_JS)
    src = _read(MAIN_JS)
    assert "runtimeManager.heartbeatPath()" in src
    assert "HEARTBEAT_INTERVAL_MS = 60000" in src
    assert "stopHeartbeat" in src


def test_no_auto_open_devtools():
    src = _read(MAIN_JS)
    assert "openDevTools" not in src


def test_debug_snap_is_noop_unless_debug_and_never_logs_secrets():
    src = _read(MAIN_JS)
    body = _block_after_marker(src, "function debugSnap(")
    assert "if (!DEBUG) return;" in body, "debugSnap must be a no-op unless debug is enabled"
    for match in re.finditer(r"debugSnap\((.*?)\)", src, re.S):
        call = match.group(1).lower()
        for secret in ("token", "cookie", "authorization"):
            assert secret not in call, "debugSnap call interpolates %r: %s" % (secret, call)
