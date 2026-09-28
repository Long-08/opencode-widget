"""Phase 5B: static guards for the dashboard visualizations.

Checks the ordered <script> wiring, the /api/timeline allowlist, DOM-safe
rendering in the new chart modules (no unescaped innerHTML), the mandated
"Raw Cost" naming, the timeline state strings, the visible top-N note, and the
absence of any notification coupling in charts/timeline/matrix.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
DASH_DIR = os.path.join(APP_DIR, "dashboard")
INDEX_HTML = os.path.join(APP_DIR, "index.html")
MAIN_JS = os.path.join(PROJECT_DIR, "electron", "main.js")
PRELOAD_JS = os.path.join(PROJECT_DIR, "electron", "preload.js")

VIS_MODULES = ("charts.js", "timeline.js", "matrix.js")
TIMELINE_JS = os.path.join(DASH_DIR, "timeline.js")
MATRIX_JS = os.path.join(DASH_DIR, "matrix.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_index_script_order_includes_visualization_modules():
    src = _read(INDEX_HTML)
    found = re.findall(r"<script[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", src, re.I)
    for rel in ("dashboard/charts.js", "dashboard/timeline.js", "dashboard/matrix.js"):
        assert rel in found, "%s missing from script list" % rel
    assert found.index("dashboard/views.js") < found.index("dashboard/charts.js")
    assert found.index("dashboard/charts.js") < found.index("dashboard/timeline.js")
    assert found.index("dashboard/timeline.js") < found.index("dashboard/matrix.js")
    assert found.index("dashboard/matrix.js") < found.index("dashboard/overview.js")
    assert found[-1] == "app.js", found


def test_visualization_modules_do_not_use_innerhtml():
    # Dynamic labels must go through textContent/esc(), never raw innerHTML.
    for name in VIS_MODULES:
        src = _read(os.path.join(DASH_DIR, name))
        assert "innerHTML" not in src, "%s still uses innerHTML" % name


def test_visualization_modules_use_textcontent_and_escaped_helpers():
    for name in VIS_MODULES:
        src = _read(os.path.join(DASH_DIR, name))
        assert "textContent" in src, "%s must set dynamic text via textContent" % name
    matrix = _read(MATRIX_JS)
    assert "setAttribute" in matrix


def test_timeline_state_strings_present():
    src = _read(TIMELINE_JS)
    assert "No usage in this range." in src
    assert "Timeline unavailable." in src
    assert "Timeline unavailable because the OpenCode usage schema is not supported." in src
    assert "Loading" in src


def test_raw_cost_label_and_note_present():
    timeline = _read(TIMELINE_JS)
    assert "Raw Cost" in timeline
    assert "Raw cost recorded in OpenCode message data." in timeline
    assert "not equivalent to official OpenCode Go quota consumption" in timeline
    matrix = _read(MATRIX_JS)
    assert "Raw Cost" in matrix


def test_matrix_accessibility_and_topn_note():
    src = _read(MATRIX_JS)
    assert "tabIndex" in src
    assert "aria-label" in src
    assert "position" in src and "sticky" in src
    assert "MATRIX_TOP_N = 20" in src
    assert "Showing top " in src
    # never silently drop: the note is emitted whenever rows/cols exceed the cap
    assert "matrixTruncationNote" in src


def test_visualization_modules_are_dom_free_at_load():
    for name in VIS_MODULES:
        src = _read(os.path.join(DASH_DIR, name))
        # Top-level DOM access is forbidden; the guard is present instead.
        assert 'typeof document === "undefined"' in src, name
        for line in src.splitlines():
            assert not re.match(r"^document\.", line), "%s top-level document use: %r" % (name, line)
            assert not re.match(r"^window\.", line), "%s top-level window use: %r" % (name, line)


def test_no_notification_coupling_in_visualizations():
    for name in VIS_MODULES:
        src = _read(os.path.join(DASH_DIR, name)).lower()
        for needle in ("notification", "apiGetNotification", "onOpenForecast", "tray-refresh"):
            assert needle.lower() not in src, "%s couples to %r" % (name, needle)


def test_main_timeline_allowlist_is_bare_or_validated_range():
    src = _read(MAIN_JS)
    assert "/api/timeline" in src
    assert re.search(r"sessions\|timeline", src), "timeline missing from range allowlist regex"
    # The range set is still the shared API_AGENT_RANGES. No arbitrary query route.
    assert "API_AGENT_RANGES" in src
    assert "?range=([a-z0-9]+)" in src
    # Timeline is never allowlisted as an unvalidated query-bearing path.
    assert "p === '/api/timeline?'" not in src


def test_preload_exposes_api_get_timeline():
    src = _read(PRELOAD_JS)
    assert "apiGetTimeline:" in src
    assert "'/api/timeline'" in src
