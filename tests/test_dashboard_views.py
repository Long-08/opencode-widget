"""Phase 5A: run the Agents/Models/Sessions dashboard-views Node suite plus
static assertions over the split renderer files.

Mirrors tests/test_dashboard_core.py: skip cleanly when node is unavailable and
run `node --test tests/js/dashboard_views.test.js` from the project root. The
static half guards the new panel markup/columns, the forbidden content labels,
the mandated "Raw Cost" naming, and the additive-only asset policy. The
view-model helpers now live in electron/app/dashboard/views.js; the panel
markup they generate lives in the per-tab dashboard modules.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
INDEX_HTML = os.path.join(APP_DIR, "index.html")
APP_JS = os.path.join(APP_DIR, "app.js")
VIEWS_JS = os.path.join(APP_DIR, "dashboard", "views.js")
VIEWS_TEST = os.path.join("tests", "js", "dashboard_views.test.js")

FORBIDDEN_COST_NAMES = ("Official Cost", "Go Usage", "Quota Cost", "Subscription Usage")
FORBIDDEN_CONTENT_LABELS = (
    "Prompt",
    "Response",
    "Reasoning",
    "Tool Calls",
    "Tool Arguments",
    "Message Content",
    "File Paths",
)
VIEW_FUNCTIONS = (
    "agentRowCells",
    "modelRowCells",
    "sessionTreeRows",
    "agentDetailModel",
    "modelDetailModel",
    "sessionDetailModel",
    "filterAgents",
    "filterModels",
    "filterSessions",
    "providerOptions",
    "filterByProvider",
)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _renderer_files():
    return [INDEX_HTML, APP_JS] + sorted(glob.glob(os.path.join(APP_DIR, "dashboard", "*.js")))


def _renderer_text():
    return "\n".join(_read(p) for p in _renderer_files())


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_dashboard_views_node_suite():
    proc = subprocess.run(
        ["node", "--test", VIEWS_TEST],
        cwd=PROJECT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    assert proc.returncode == 0


def test_views_block_defines_required_helpers():
    block = _read(VIEWS_JS)
    start = block.index("/* ==== phase5a-views:start ====")
    end = block.index("/* ==== phase5a-views:end ==== */")
    assert end > start
    block = block[start:end]
    for name in VIEW_FUNCTIONS:
        assert re.search(r"function\s+%s\s*\(" % re.escape(name), block), name


def test_agents_table_headers_include_avg_tokens_and_duration():
    src = _renderer_text()
    assert "Avg Tokens/Request" in src
    assert "Avg Duration" in src


def test_agent_detail_includes_cost_share():
    assert "Cost Share" in _renderer_text()


def test_sessions_panel_has_children_column():
    src = _renderer_text()
    assert "<th>Children</th>" in src
    assert "data-obs-session-toggle" in src
    assert "data-obs-expand" in src and "data-obs-collapse" in src


def test_session_detail_has_no_content_labels():
    src = _renderer_text()
    for label in FORBIDDEN_CONTENT_LABELS:
        assert label not in src, "content label present: %s" % label


def test_raw_cost_label_present_and_forbidden_names_absent():
    src = _renderer_text()
    assert "Raw Cost" in src
    for bad in FORBIDDEN_COST_NAMES:
        assert bad not in src, "forbidden cost label present: %s" % bad


def test_no_external_scripts_or_cdn_added():
    src = _read(INDEX_HTML)
    assert not re.search(r"<script[^>]*\bsrc\s*=\s*['\"]https?://", src, re.I), "external <script src> found"
    assert not re.search(r"<link[^>]*\bhref\s*=\s*['\"]https?://", src, re.I), "external stylesheet found"
    for host in ("cdn.", "unpkg", "jsdelivr", "cdnjs", "googleapis"):
        assert host not in src, "external CDN reference: %s" % host
