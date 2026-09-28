"""Phase 5A: static guards for the observability dashboard after the Phase 5.1
front-end split.

Nav tabs and range buttons stay in electron/app/index.html; the generated table
markup, the mandated "Raw Cost" naming and the cost disclaimer moved to
electron/app/dashboard/*.js. The default-hidden dashboard CSS lives in
electron/app/app.css and the large-mode toggle in electron/app/app.js.
"""
import glob
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
INDEX_HTML = os.path.join(APP_DIR, "index.html")
APP_CSS = os.path.join(APP_DIR, "app.css")
APP_JS = os.path.join(APP_DIR, "app.js")

TABS = ("overview", "agents", "models", "sessions", "forecast")
TAB_LABELS = ("Overview", "Agents", "Models", "Sessions", "Forecast")
RANGES = ("today", "7d", "30d", "all")
FORBIDDEN_COST_NAMES = ("Official Cost", "Go Usage", "Quota Cost", "Subscription Usage")
DISCLAIMER = (
    "Raw cost recorded in OpenCode message data. "
    "It is not equivalent to official OpenCode Go quota consumption."
)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _read_index():
    return _read(INDEX_HTML)


def _renderer_files():
    return [INDEX_HTML, APP_JS] + sorted(glob.glob(os.path.join(APP_DIR, "dashboard", "*.js")))


def _renderer_text():
    return "\n".join(_read(p) for p in _renderer_files())


def test_five_tabs_with_data_tab_ids():
    src = _read_index()
    for tab in TABS:
        assert 'data-tab="%s"' % tab in src, tab
    for label in TAB_LABELS:
        assert label in src, label
    # keyboard-focusable native buttons with aria-labels
    for tab, label in zip(TABS, TAB_LABELS):
        pattern = (
            r'<button[^>]*data-tab="%s"[^>]*aria-label="%s"' % (tab, label)
        )
        assert re.search(pattern, src), pattern


def test_range_values_present():
    src = _read_index()
    for rng in RANGES:
        assert 'data-range="%s"' % rng in src, rng
    for label in ("Today", "7 Days", "Current Period", "All"):
        assert label in src, label


def test_raw_cost_label_present_and_forbidden_names_absent():
    src = _renderer_text()
    assert ("Raw Cost" in src) or ("原始 Cost" in src)
    for bad in FORBIDDEN_COST_NAMES:
        assert bad not in src, "forbidden cost label present: %s" % bad


def test_raw_cost_disclaimer_present():
    src = _renderer_text()
    assert "Raw cost recorded in OpenCode message data." in src
    assert "not equivalent to official OpenCode Go quota consumption" in src
    # The exact mandated sentence must survive intact (whitespace-tolerant).
    flat = re.sub(r"\s+", " ", src)
    assert DISCLAIMER in flat


def test_dashboard_root_hidden_outside_large_mode():
    assert 'id="obsDashboard"' in _read_index()
    # CSS: hidden by default, shown only with the .on class (now in app.css)
    css = _read(APP_CSS)
    assert re.search(r"\.obs\s*\{[^}]*display:\s*none", css), "no default-hidden .obs rule"
    assert re.search(r"\.obs\.on\s*\{[^}]*display:\s*(flex|block)", css), "no .obs.on show rule"
    # large-mode logic toggles the distinct root (now in app.js)
    src = _read(APP_JS)
    assert "obsRoot.classList.toggle(\"on\", onLarge)" in src


def test_no_external_scripts_or_cdn_added():
    src = _read_index()
    assert not re.search(r"<script[^>]*\bsrc\s*=\s*['\"]https?://", src, re.I), "external <script src> found"
    assert not re.search(r"<link[^>]*\bhref\s*=\s*['\"]https?://", src, re.I), "external stylesheet found"
    for host in ("cdn.", "unpkg", "jsdelivr", "cdnjs", "googleapis"):
        assert host not in src, "external CDN reference: %s" % host


def test_renderer_has_no_token_surface_extra():
    src = _renderer_text()
    for needle in ("apiEnv", "API_BASE", "API_TOKEN", "Bearer ", "runtime.json"):
        assert needle not in src, "renderer still references %r" % needle
