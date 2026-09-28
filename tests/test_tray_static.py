"""Phase 6B: static guards for the minimal system tray + notification UI.

Factual string / block assertions only (no Electron, no execution): the tray is
created after the single-instance lock and window creation, torn down on quit,
the menu labels are exact, the icon is a local asset (no remote URL), the
"tray created" log is DEBUG-gated, and the renderer wires the tray/notification
actions onto the existing local refresh path with escaped values.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
MAIN_JS = os.path.join(PROJECT_DIR, "electron", "main.js")
TRAY_JS = os.path.join(PROJECT_DIR, "electron", "tray_manager.js")
FORECAST_JS = os.path.join(
    PROJECT_DIR, "electron", "app", "dashboard", "forecast.js")

MENU_LABELS = ("Show Widget", "Refresh", "Notifications:", "Quit")


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
                return src[open_idx:i + 1]
        i += 1
    raise AssertionError("unbalanced braces while scanning source")


def _block_after_marker(src, marker):
    start = src.index(marker)
    brace = src.index("{", start)
    return _balanced_block(src, brace)


def test_tray_created_after_lock_and_window():
    src = _read(MAIN_JS)
    assert "require('./tray_manager')" in src
    assert src.index("app.requestSingleInstanceLock()") < src.index("createWidgetTray();")
    ready = src[src.index("app.whenReady()"):]
    assert "createWindow();" in ready
    assert "createWidgetTray();" in ready
    assert ready.index("createWindow();") < ready.index("createWidgetTray();")


def test_quit_handler_calls_app_quit_and_no_process_exit():
    src = _read(MAIN_JS)
    handlers = _block_after_marker(src, "function trayHandlers(")
    assert "app.quit()" in handlers
    # Quit must flow through the Phase 6A will-quit cleanup, never os-level exit.
    assert "process.exit" not in src


def test_menu_labels_are_exact_and_ordered():
    src = _read(TRAY_JS)
    labels = _block_after_marker(src, "const MENU_LABELS")
    for label in MENU_LABELS:
        assert "'%s'" % label in labels, label
    build = _block_after_marker(src, "function buildMenuTemplate(")
    order = [build.index("MENU_LABELS.show"), build.index("MENU_LABELS.refresh"),
             build.index("MENU_LABELS.notifications"), build.index("MENU_LABELS.quit")]
    assert order == sorted(order), "menu labels are not in contract order"
    assert "type: 'checkbox'" in build
    assert "type: 'separator'" in build


def test_icon_is_local_asset_without_remote_url():
    main = _read(MAIN_JS)
    tray = _read(TRAY_JS)
    assert "path.join(__dirname, 'assets', 'tray.png')" in main
    create = _block_after_marker(main, "function createWidgetTray(")
    assert "tray.png" in create
    assert "http" not in create.lower()
    assert "http" not in tray.lower()
    assert os.path.isfile(os.path.join(PROJECT_DIR, "electron", "assets", "tray.png"))


def test_tray_destroyed_on_quit_and_log_debug_gated():
    src = _read(MAIN_JS)
    quit_block = _block_after_marker(src, "app.on('will-quit'")
    assert "destroyWidgetTray()" in quit_block
    destroy = _block_after_marker(src, "function destroyWidgetTray(")
    assert ".destroy()" in destroy
    log = _block_after_marker(src, "function trayLog(")
    assert "if (!DEBUG) return;" in log
    assert "tray created" in src
    assert "trayLog('tray created')" in src


def test_forecast_wires_open_forecast_and_tray_refresh():
    src = _read(FORECAST_JS)
    wire = _block_after_marker(src, "function wireForecastNotificationActions(")
    assert "onOpenForecast" in wire
    assert 'setUiState("large")' in wire
    assert 'OCW.obsSelectTab("forecast")' in wire
    assert "onTrayRefresh" in wire
    # Reuse the existing refresh path, then reload only the active tab.
    assert "refresh()" in wire
    assert "OCW.obsEnsure(OCW.getActiveTab(), true)" in wire


def test_forecast_notification_settings_uses_bridge_and_escapes():
    src = _read(FORECAST_JS)
    assert "apiGetNotificationSettings" in src
    assert "apiSetNotificationSettings" in src
    fields = _block_after_marker(src, "function forecastNotificationFieldsHtml(")
    assert "esc(" in fields
    assert "esc(start)" in fields and "esc(end)" in fields
    assert '<input type="time"' in fields
    assert 'id="notifyEnabled"' in fields
    assert 'id="notifyQuietEnabled"' in fields
    # Never render a raw exception string.
    assert "e.message" not in src
    assert "String(e)" not in src
