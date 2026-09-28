"""Phase 5A: run the Agents/Models/Sessions dashboard-views Node suite plus
static assertions over electron/app/index.html.

Mirrors tests/test_dashboard_core.py: skip cleanly when node is unavailable and
run `node --test tests/js/dashboard_views.test.js` from the project root. The
static half guards the new panel markup/columns, the forbidden content labels,
the mandated "Raw Cost" naming, and the additive-only asset policy.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
INDEX_HTML = os.path.join(PROJECT_DIR, "electron", "app", "index.html")
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


def _read_index():
    with open(INDEX_HTML, "r", encoding="utf-8") as fh:
        return fh.read()


def _views_block(src):
    start = src.index("/* ==== phase5a-views:start ====")
    end = src.index("/* ==== phase5a-views:end ==== */")
    assert end > start
    return src[start:end]


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
    block = _views_block(_read_index())
    for name in VIEW_FUNCTIONS:
        assert re.search(r"function\s+%s\s*\(" % re.escape(name), block), name


def test_agents_table_headers_include_avg_tokens_and_duration():
    src = _read_index()
    assert "Avg Tokens/Request" in src
    assert "Avg Duration" in src


def test_agent_detail_includes_cost_share():
    assert "Cost Share" in _read_index()


def test_sessions_panel_has_children_column():
    src = _read_index()
    assert "<th>Children</th>" in src
    assert "data-obs-session-toggle" in src
    assert "data-obs-expand" in src and "data-obs-collapse" in src


def test_session_detail_has_no_content_labels():
    src = _read_index()
    for label in FORBIDDEN_CONTENT_LABELS:
        assert label not in src, "content label present: %s" % label


def test_raw_cost_label_present_and_forbidden_names_absent():
    src = _read_index()
    assert "Raw Cost" in src
    for bad in FORBIDDEN_COST_NAMES:
        assert bad not in src, "forbidden cost label present: %s" % bad


def test_no_external_scripts_or_cdn_added():
    src = _read_index()
    assert not re.search(r"<script[^>]*\bsrc\s*=", src, re.I), "external <script src> found"
    assert not re.search(r"<link[^>]*\bhref\s*=\s*['\"]https?://", src, re.I), "external stylesheet found"
    for host in ("cdn.", "unpkg", "jsdelivr", "cdnjs", "googleapis"):
        assert host not in src, "external CDN reference: %s" % host
