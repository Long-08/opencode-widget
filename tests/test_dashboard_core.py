"""Phase 5A: run the shared dashboard-core Node suite + inline script syntax check.

Mirrors tests/test_renderer_escaping.py: skip cleanly when node is unavailable,
run `node --test tests/js/dashboard_core.test.js` from the project root, and
additionally `node --check` the renderer's inline script (extracted from
electron/app/index.html) so a syntax regression fails here, not only in a browser.
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
DASH_TEST = os.path.join("tests", "js", "dashboard_core.test.js")


def _read_index():
    with open(INDEX_HTML, "r", encoding="utf-8") as fh:
        return fh.read()


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_inline_script_syntax(tmp_path):
    src = _read_index()
    match = re.search(r"<script>([\s\S]*?)</script>", src)
    assert match, "no inline <script> block found in index.html"
    script_file = tmp_path / "inline_script.js"
    script_file.write_text(match.group(1), encoding="utf-8")
    proc = subprocess.run(
        ["node", "--check", str(script_file)],
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
