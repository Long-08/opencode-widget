"""Phase 2.1: run the Node unit tests for electron/runtime_env.js under pytest.

Skips when node is unavailable. The Node suite itself lives in
tests/js/runtime_env.test.js (node:test + node:assert, no new dependencies).
"""
import os
import shutil
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
NODE_TEST = os.path.join("tests", "js", "runtime_env.test.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_runtime_env_node_suite():
    proc = subprocess.run(
        ["node", "--test", NODE_TEST],
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
