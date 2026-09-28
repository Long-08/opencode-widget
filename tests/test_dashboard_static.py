"""Phase 5A: static guards for the observability dashboard markup in index.html.

Read-only assertions over electron/app/index.html covering the nav tabs, range
values, the mandated "Raw Cost" naming (and the forbidden alternatives), the
cost disclaimer, large-mode gating of the dashboard root, additive-only assets
(no external scripts/CDN), and the renderer token boundary.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
INDEX_HTML = os.path.join(PROJECT_DIR, "electron", "app", "index.html")

TABS = ("overview", "agents", "models", "sessions")
RANGES = ("today", "7d", "30d", "all")
FORBIDDEN_COST_NAMES = ("Official Cost", "Go Usage", "Quota Cost", "Subscription Usage")
DISCLAIMER = (
    "Raw cost recorded in OpenCode message data. "
    "It is not equivalent to official OpenCode Go quota consumption."
)


def _read_index():
    with open(INDEX_HTML, "r", encoding="utf-8") as fh:
        return fh.read()


def test_four_tabs_with_data_tab_ids():
    src = _read_index()
    for tab in TABS:
        assert 'data-tab="%s"' % tab in src, tab
    for label in ("Overview", "Agents", "Models", "Sessions"):
        assert label in src, label
    # keyboard-focusable native buttons with aria-labels
    for tab, label in zip(TABS, ("Overview", "Agents", "Models", "Sessions")):
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
    src = _read_index()
    assert ("Raw Cost" in src) or ("原始 Cost" in src)
    for bad in FORBIDDEN_COST_NAMES:
        assert bad not in src, "forbidden cost label present: %s" % bad


def test_raw_cost_disclaimer_present():
    src = _read_index()
    assert "Raw cost recorded in OpenCode message data." in src
    assert "not equivalent to official OpenCode Go quota consumption" in src
    # The exact mandated sentence must survive intact (whitespace-tolerant).
    flat = re.sub(r"\s+", " ", src)
    assert DISCLAIMER in flat


def test_dashboard_root_hidden_outside_large_mode():
    src = _read_index()
    assert 'id="obsDashboard"' in src
    # CSS: hidden by default, shown only with the .on class
    assert re.search(r"\.obs\s*\{[^}]*display:\s*none", src), "no default-hidden .obs rule"
    assert re.search(r"\.obs\.on\s*\{[^}]*display:\s*(flex|block)", src), "no .obs.on show rule"
    # large-mode logic toggles the distinct root
    assert "obsRoot.classList.toggle(\"on\", onLarge)" in src


def test_no_external_scripts_or_cdn_added():
    src = _read_index()
    assert not re.search(r"<script[^>]*\bsrc\s*=", src, re.I), "external <script src> found"
    assert not re.search(r"<link[^>]*\bhref\s*=\s*['\"]https?://", src, re.I), "external stylesheet found"
    for host in ("cdn.", "unpkg", "jsdelivr", "cdnjs", "googleapis"):
        assert host not in src, "external CDN reference: %s" % host


def test_renderer_has_no_token_surface_extra():
    src = _read_index()
    for needle in ("apiEnv", "API_BASE", "API_TOKEN", "Bearer ", "runtime.json"):
        assert needle not in src, "renderer still references %r" % needle
