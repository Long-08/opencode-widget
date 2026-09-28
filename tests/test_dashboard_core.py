"""Phase 5A: run the shared dashboard-core Node suite + JS syntax checks.

Mirrors tests/test_renderer_escaping.py: skip cleanly when node is unavailable
and run `node --test tests/js/dashboard_core.test.js` from the project root.
Additionally `node --check` the split renderer scripts (app.js + dashboard/*.js)
so a syntax regression fails here, not only in a browser.
"""
import glob
import os
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
DASH_TEST = os.path.join("tests", "js", "dashboard_core.test.js")

# Classic renderer scripts in load order; app.js is last in the document.
EXPECTED_SCRIPTS = [
    os.path.join("electron", "app", "app.js"),
    os.path.join("electron", "app", "dashboard", "format.js"),
    os.path.join("electron", "app", "dashboard", "core.js"),
    os.path.join("electron", "app", "dashboard", "views.js"),
    os.path.join("electron", "app", "dashboard", "charts.js"),
    os.path.join("electron", "app", "dashboard", "timeline.js"),
    os.path.join("electron", "app", "dashboard", "matrix.js"),
    os.path.join("electron", "app", "dashboard", "overview.js"),
    os.path.join("electron", "app", "dashboard", "agents.js"),
    os.path.join("electron", "app", "dashboard", "models.js"),
    os.path.join("electron", "app", "dashboard", "sessions.js"),
    os.path.join("electron", "app", "dashboard", "forecast.js"),
]


def _renderer_js_files():
    files = [os.path.join(APP_DIR, "app.js")]
    files += sorted(glob.glob(os.path.join(APP_DIR, "dashboard", "*.js")))
    return files


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_dashboard_core_node_suite():
    proc = subprocess.run(
        ["node", "--test", DASH_TEST],
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


def test_renderer_scripts_exist():
    for rel in EXPECTED_SCRIPTS:
        assert os.path.isfile(os.path.join(PROJECT_DIR, rel)), rel
    assert len(_renderer_js_files()) == len(EXPECTED_SCRIPTS)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
@pytest.mark.parametrize("rel", EXPECTED_SCRIPTS)
def test_renderer_script_syntax(rel):
    """node --check every split renderer script (app.js + dashboard/*.js)."""
    proc = subprocess.run(
        ["node", "--check", os.path.join(PROJECT_DIR, rel)],
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
