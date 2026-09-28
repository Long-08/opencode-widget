"""Phase 2.1: renderer escaping + CSP guards.

Runs the Node esc() suite (tests/js/esc.test.js) and statically checks the
renderer: the CSP meta tag stays in electron/app/index.html and must not allow
unsafe-eval, while data-derived model/supplier interpolations must be escaped in
the split scripts (app.js + dashboard/*.js).
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
INDEX_HTML = os.path.join(APP_DIR, "index.html")
APP_JS = os.path.join(APP_DIR, "app.js")
ESC_TEST = os.path.join("tests", "js", "esc.test.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _read_index():
    return _read(INDEX_HTML)


def _renderer_text():
    files = [APP_JS] + sorted(glob.glob(os.path.join(APP_DIR, "dashboard", "*.js")))
    return "\n".join(_read(p) for p in files)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_esc_node_suite():
    proc = subprocess.run(
        ["node", "--test", ESC_TEST],
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


def test_csp_meta_present_and_no_unsafe_eval():
    src = _read_index()
    assert 'http-equiv="Content-Security-Policy"' in src
    assert "unsafe-eval" not in src
    assert "default-src 'none'" in src


def test_data_derived_interpolations_are_wrapped_in_esc():
    src = _renderer_text()
    # Positive: the known data-derived interpolations go through esc().
    assert "${esc(x.name)}" in src
    assert "${esc(x.model)}" in src
    assert "${esc(s.name || s.model)}" in src


@pytest.mark.parametrize("needle", ["${x.name}", "${x.model}", "${s.name}", "${s.model}"])
def test_no_raw_data_derived_interpolations(needle):
    # Negative: none of the raw (unescaped) forms may appear literally.
    assert needle not in _renderer_text()
