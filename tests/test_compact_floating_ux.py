"""Compact Floating UX refactor: static guards for the mini/compact/expanded
desktop model.

Factual string / block assertions only (no Electron, no execution), following
the test_dashboard_static.py style: window-size ordering in electron/main.js,
the default-mini cold start and explicit transitions in electron/app/app.js,
the content policy per state (no ring/list/timeline in mini; Top-3 cap in
compact), the tray/notification state policy, the rem text-rendering fix, the
mode-scoped chrome CSS, and accessibility surfaces.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
MAIN_JS = os.path.join(PROJECT_DIR, "electron", "main.js")
INDEX_HTML = os.path.join(APP_DIR, "index.html")
APP_JS = os.path.join(APP_DIR, "app.js")
APP_CSS = os.path.join(APP_DIR, "app.css")
FORECAST_JS = os.path.join(APP_DIR, "dashboard", "forecast.js")


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


# ---------------------------------------------------------------------------
# window sizes
# ---------------------------------------------------------------------------

def test_window_sizes_are_ordered_and_mini_is_slim():
    src = _read(MAIN_JS)
    m = re.search(
        r"const\s+SIZES\s*=\s*\{[^}]*small:\s*\[(\d+),\s*(\d+)\]"
        r"[^}]*mid:\s*\[(\d+),\s*(\d+)\][^}]*large:\s*\[(\d+),\s*(\d+)\]",
        src, re.S,
    )
    assert m, "SIZES with small/mid/large entries not found in main.js"
    small = (int(m.group(1)), int(m.group(2)))
    mid = (int(m.group(3)), int(m.group(4)))
    large = (int(m.group(5)), int(m.group(6)))
    assert small[0] < mid[0] < large[0], "widths must increase mini -> compact -> expanded"
    assert small[1] < mid[1] < large[1], "heights must increase mini -> compact -> expanded"
    assert small[1] <= 120, "mini must be a slim status bar (height <= 120), got %s" % (small,)
    assert large == (960, 720), "expanded keeps the existing large dashboard size"


def test_window_is_born_at_mini_size_and_defaults_to_small_state():
    src = _read(MAIN_JS)
    create = _block_after_marker(src, "function createWindow(")
    assert "width: SIZES.small[0]" in create
    assert "height: SIZES.small[1]" in create
    assert 'curUiState = "small"' in src
    assert 'preSnapUiState = "small"' in src
    # snap restore fallback follows the default state
    restore = _block_after_marker(src, "function restoreFromSnap(")
    assert "SIZES.small" in restore


# ---------------------------------------------------------------------------
# default-mini cold start
# ---------------------------------------------------------------------------

def test_renderer_default_state_is_small_and_boot_sets_it_without_resize():
    src = _read(APP_JS)
    assert 'let uiState = "small"' in src, "renderer initial state must be mini"
    boot = _block_after_marker(src, "function initDefaultView(")
    assert 'setUiState("small", true)' in boot, (
        "cold start must settle on mini with noResize (window is born mini-sized)"
    )
    assert "initDefaultView" in src and "setTimeout(initDefaultView" in src


def test_mini_view_has_no_ring_no_grid_no_list():
    src = _read(INDEX_HTML)
    small = src[src.index('id="smallView"'):src.index('id="compactView"')]
    assert 'id="miniBar"' in small, "mini must render the single-row status bar"
    for forbidden in ("ringbox", "sGrid", "sMStats", "m-list"):
        assert forbidden not in small, "mini must not contain %s" % forbidden
    # ring stays a compact/expanded-only element
    compact = src[src.index('id="compactView"'):]
    assert "ringbox" in compact


# ---------------------------------------------------------------------------
# explicit transitions
# ---------------------------------------------------------------------------

def test_transition_wiring_is_explicit():
    src = _read(APP_JS)
    # mini -> compact by clicking the mini bar
    assert '$("#smallView").addEventListener("click"' in src
    # compact -> expanded via the explicit full-panel button
    assert '$("#btnOpenFull").onclick' in src
    # header buttons keep the toggles: small<->mid and large<->mid
    assert 'setUiState(uiState === "small" ? "mid" : "small", false, true)' in src
    assert 'setUiState(uiState === "large" ? "mid" : "large", false, true)' in src


def test_esc_collapses_to_mini_and_ignores_typing_targets():
    src = _read(APP_JS)
    assert 'e.key === "Escape"' in src
    assert 'setUiState("small", false, true)' in src
    assert "input, select, textarea" in src, "Esc must be ignored while typing"


def test_tab_is_not_globally_prevented():
    src = _read(APP_JS)
    assert 'e.key === "Tab"' not in src, (
        "global Tab hijack must be removed so Tab/Shift+Tab keep native focus traversal"
    )


def test_mode_scoped_body_classes_are_toggled():
    src = _read(APP_JS)
    block = _block_after_marker(src, "function setUiState(")
    for cls in ("mode-small", "mode-compact", "mode-expanded"):
        assert ('"%s"' % cls) in block, cls


# ---------------------------------------------------------------------------
# tray / notification state policy
# ---------------------------------------------------------------------------

def test_tray_show_respects_the_current_state():
    src = _read(MAIN_JS)
    focus = _block_after_marker(src, "function focusWidget(")
    for call in ("isMinimized()", "restore()", "show()", "focus()"):
        assert call in focus, "tray show must restore/focus: missing %s" % call
    for forbidden in ("resize", "setUiState", "SIZES", "send("):
        assert forbidden not in focus, "tray show must never change widget state: %s" % forbidden


def test_notification_open_still_reaches_expanded_forecast():
    src = _read(FORECAST_JS)
    wire = _block_after_marker(src, "function wireForecastNotificationActions(")
    assert 'setUiState("large")' in wire
    assert 'OCW.obsSelectTab("forecast")' in wire


# ---------------------------------------------------------------------------
# mode-aware data fetching
# ---------------------------------------------------------------------------

def test_mode_aware_view_prefetch():
    src = _read(APP_JS)
    assert "function neededViewIds(" in src
    assert "neededViewIds(" in src.split("async function refresh(")[1], (
        "refresh() must use the shared neededViewIds policy"
    )
    assert "function ensureExpandedViews(" in src
    set_block = _block_after_marker(src, "function setUiState(")
    assert "ensureExpandedViews()" in set_block, "expanding must ensure dashboard views"
    large_block = _block_after_marker(src, "function ensureExpandedViews(")
    assert 'uiState !== "large"' in large_block or 'uiState === "large"' in large_block


def test_render_is_gated_by_mode():
    src = _read(APP_JS)
    block = _block_after_marker(src, "function render(")
    assert 'uiState !== "small"' in block, "compact rendering must be skipped in mini"
    assert 'uiState === "large"' in block, "expanded rendering must be skipped outside large"
    assert "renderMini()" in block


# ---------------------------------------------------------------------------
# compact content policy
# ---------------------------------------------------------------------------

def test_compact_model_list_is_capped_at_top_3():
    src = _read(APP_JS)
    assert re.search(r"COMPACT_MODEL_LIMIT\s*=\s*3", src), "Top-3 cap constant missing"
    fill = _block_after_marker(src, "function fillModelList(")
    assert "limit" in fill and "slice(0, limit)" in fill


def test_compact_has_full_panel_entry_and_updated_time():
    src = _read(INDEX_HTML)
    compact = src[src.index('id="compactView"'):src.index('id="dashView"')]
    assert 'id="btnOpenFull"' in compact
    assert 'aria-label' in compact
    assert 'id="cUpdated"' in compact
    assert "打开完整面板" in compact


# ---------------------------------------------------------------------------
# text rendering fix
# ---------------------------------------------------------------------------

def test_rem_badge_is_rendered_as_escaped_text_not_escaped_markup():
    src = _read(APP_JS)
    assert 'esc(rem)' not in src, "app-generated markup must not be double-escaped into text"
    assert "rem = `<span" not in src and "rem = '<span" not in src
    assert re.search(r'remText\s*=\s*"剩"', src), "rem badge must be built as plain text"
    assert 'esc(remText)' in src, "data-adjacent rem text must still go through esc()"


# ---------------------------------------------------------------------------
# mode-scoped chrome CSS
# ---------------------------------------------------------------------------

def test_chrome_hidden_in_mini_and_compact_footer_policy():
    css = _read(APP_CSS)
    for needle in ("body.mode-small footer", "body.mode-small .op-wrap",
                   "body.mode-small .status", ".mini-bar", ".mb-dot", ".c-actions"):
        assert needle in css, "missing CSS rule: %s" % needle


def test_mini_bar_accessibility():
    src = _read(INDEX_HTML)
    assert re.search(r'id="miniBar"[^>]*role="button"', src)
    assert re.search(r'id="miniBar"[^>]*tabindex="0"', src)
    assert re.search(r'id="miniBar"[^>]*aria-label="[^"]+"', src)
    assert re.search(r'id="btnOpenFull"[^>]*aria-label="[^"]+"', src)
