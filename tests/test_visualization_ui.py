"""Phase 5B: run the visualization Node suites + syntax-check the changed files.

Mirrors tests/test_dashboard_core.py: skip cleanly when node is unavailable and
run the three new suites from the project root, then `node --check` every
new/changed renderer + bridge script so a syntax regression fails here.
"""
import os
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)

NODE_SUITES = [
    os.path.join("tests", "js", "timeline.test.js"),
    os.path.join("tests", "js", "matrix.test.js"),
    os.path.join("tests", "js", "forecast_visual.test.js"),
]

CHECK_FILES = [
    os.path.join("electron", "main.js"),
    os.path.join("electron", "preload.js"),
    os.path.join("electron", "app", "dashboard", "core.js"),
    os.path.join("electron", "app", "dashboard", "charts.js"),
    os.path.join("electron", "app", "dashboard", "timeline.js"),
    os.path.join("electron", "app", "dashboard", "matrix.js"),
    os.path.join("electron", "app", "dashboard", "overview.js"),
    os.path.join("electron", "app", "dashboard", "agents.js"),
    os.path.join("electron", "app", "dashboard", "forecast.js"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
@pytest.mark.parametrize("suite", NODE_SUITES)
def test_visualization_node_suite(suite):
    proc = subprocess.run(
        ["node", "--test", suite],
        cwd=PROJECT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    assert proc.returncode == 0, suite


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
@pytest.mark.parametrize("rel", CHECK_FILES)
def test_visualization_syntax(rel):
    path = os.path.join(PROJECT_DIR, rel)
    assert os.path.isfile(path), rel
    proc = subprocess.run(
        ["node", "--check", rel],
        cwd=PROJECT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    assert proc.returncode == 0, rel
