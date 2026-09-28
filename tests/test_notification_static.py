"""Phase 6B: static guards for opt-in, forecast-aware notifications.

Checks electron/notification_policy.js, electron/notification_manager.js,
electron/main.js and electron/preload.js only (factual string / block
assertions, no execution): default-OFF, trigger-state constants, validated
settings IPC, a >=15 min interval, start-once/stop-on-quit wiring, a click
handler that focuses + opens the local forecast and never opens external URLs,
no generic notification/settings surface in preload, DEBUG-gated logging, no
token/cookie in the notification code, and the single-instance lock preceding
the manager start.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
MAIN_JS = os.path.join(PROJECT_DIR, "electron", "main.js")
PRELOAD_JS = os.path.join(PROJECT_DIR, "electron", "preload.js")
POLICY_JS = os.path.join(PROJECT_DIR, "electron", "notification_policy.js")
MANAGER_JS = os.path.join(PROJECT_DIR, "electron", "notification_manager.js")

NOTIFICATION_FILES = (POLICY_JS, MANAGER_JS)


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


def test_settings_default_to_off():
    src = _read(POLICY_JS)
    block = _block_after_marker(src, "const DEFAULT_SETTINGS")
    assert re.search(r"enabled:\s*false", block)
    assert "quiet_hours" in block


def test_trigger_state_constants_are_declared():
    src = _read(POLICY_JS)
    match = re.search(r"const TRIGGER_STATES = \[([^\]]*)\]", src)
    assert match, "TRIGGER_STATES array not found"
    assert "limit_before_reset" in match.group(1)
    assert "already_at_limit" in match.group(1)


def test_settings_ipc_present_and_policy_validated():
    main = _read(MAIN_JS)
    assert "ipcMain.handle('notification-settings-get'" in main
    assert "ipcMain.handle('notification-settings-set'" in main
    set_block = _block_after_marker(main, "ipcMain.handle('notification-settings-set'")
    assert "setSettings(payload)" in set_block
    # Validation lives in the policy module, and the manager routes through it.
    assert "function validateSettingsPayload(" in _read(POLICY_JS)
    assert "policy.validateSettingsPayload(patch)" in _read(MANAGER_JS)


def test_default_interval_is_at_least_15_minutes():
    main = _read(MAIN_JS)
    assert "NOTIFICATION_DEFAULT_INTERVAL_MS = 900 * 1000" in main
    assert "NOTIFICATION_MIN_INTERVAL_MS = 15 * 60 * 1000" in main
    manager = _read(MANAGER_JS)
    assert "MIN_INTERVAL_MS = 15 * 60 * 1000" in manager
    assert "Math.max(" in manager


def test_manager_started_once_after_window_and_stopped_on_quit():
    main = _read(MAIN_JS)
    assert main.count("startNotificationManager()") == 2  # definition-free call + guard
    assert "if (notificationManager) return;" in main
    assert "startNotificationManager();" in main
    quit_block = _block_after_marker(main, "app.on('will-quit'")
    assert "stopNotificationManager()" in quit_block
    # started from whenReady (after window creation), not before the lock
    ready_region = main[main.index("app.whenReady()"):]
    assert "startNotificationManager();" in ready_region
    assert "createWindow();" in ready_region
    assert ready_region.index("createWindow();") < ready_region.index("startNotificationManager();")


def test_notification_click_focuses_and_opens_forecast_only():
    main = _read(MAIN_JS)
    click = main[main.index("notification.on('click'"):]
    click = click[: click.index("\n") + 1]
    assert "focusWidget()" in click
    assert "openForecast()" in click
    focus_block = _block_after_marker(main, "function focusWidget(")
    for call in ("isMinimized()", "restore()", "show()", "focus()"):
        assert call in focus_block
    # never resize / change snap state
    assert "setBounds" not in focus_block
    assert "snapped" not in focus_block
    open_block = _block_after_marker(main, "function openForecast(")
    assert "'open-forecast'" in open_block
    # No external URL / destructive action in the notification code.
    show_block = _block_after_marker(main, "function showWidgetNotification(")
    assert "openExternal" not in show_block
    assert "shell" not in show_block


def test_preload_exposes_no_generic_notification_surface():
    src = _read(PRELOAD_JS)
    assert "showNotification" not in src
    assert "show-notification" not in src
    # Phase 6B continuation: the notification bridge is consolidated onto
    # widgetAPI (no separate namespace). It stays narrow: settings get/set plus
    # the open-forecast / tray-refresh actions only.
    widget = _block_after_marker(src, "exposeInMainWorld('widgetAPI'")
    for name in ("apiGetNotificationSettings:", "apiSetNotificationSettings:",
                 "onOpenForecast:", "onTrayRefresh:"):
        assert name in widget, name
    assert "notificationAPI" not in src
    # no token/base exposure
    for needle in ("apiEnv", "Authorization", "Bearer ", "runtime.json"):
        assert needle not in src


def test_debug_gated_notification_logging():
    main = _read(MAIN_JS)
    body = _block_after_marker(main, "function notifyLog(")
    assert "if (!DEBUG) return;" in body


def test_notification_code_has_no_token_or_cookie():
    for path in NOTIFICATION_FILES:
        src = _read(path).lower()
        for needle in ("token", "cookie", "authorization", "bearer", "api_key"):
            assert needle not in src, "%s references %r" % (path, needle)
    main = _read(MAIN_JS)
    notify_log = _block_after_marker(main, "function notifyLog(")
    for secret in ("token", "cookie", "authorization"):
        assert secret not in notify_log.lower()


def test_single_instance_lock_precedes_manager_start():
    src = _read(MAIN_JS)
    assert src.index("app.requestSingleInstanceLock()") < src.index("startNotificationManager()")


def test_fixture_hook_is_main_only_and_documented():
    main = _read(MAIN_JS)
    assert "OPENCODE_WIDGET_NOTIFY_FIXTURE" in main
    assert "debug/test-only" in main or "Debug/test hook" in main
    # never exposed to the renderer
    assert "OPENCODE_WIDGET_NOTIFY_FIXTURE" not in _read(PRELOAD_JS)


def test_disabled_manager_does_not_fetch():
    src = _read(MANAGER_JS)
    block = _block_after_marker(src, "async function evaluateOnce(")
    guard = block.index("current.settings.enabled")
    fetch = block.index("fetchForecast()")
    assert guard < fetch, "enabled check must precede fetchForecast"
