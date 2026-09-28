"""Phase 7: release sanitation + reproducibility checks.

Everything here is pure and tmp-based: the scanner only reads a disposable
staging tree under ``tmp_path`` and never touches real user data. The last few
tests assert the single-version-source and build-output invariants statically.
"""
import json
import os

import pytest

import paths
from scripts import release_sanitize

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)


def _write(root, rel, text, binary=False):
    full = os.path.join(str(root), *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if binary:
        with open(full, "wb") as fh:
            fh.write(text)
    else:
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    return full


# ---------------------------------------------------------------------------
# missing / clean
# ---------------------------------------------------------------------------
def test_missing_dir_returns_empty_result(tmp_path):
    result = release_sanitize.scan_staging(str(tmp_path / "does-not-exist"))
    assert result == {"forbidden": [], "secret_hits": [], "debug_hook_hits": []}
    assert release_sanitize.is_clean(result)


def test_clean_staging_fixture_is_clean(tmp_path):
    staging = tmp_path / "staging"
    _write(staging, "opencode-widget.exe", b"\x00\x01", binary=True)
    _write(staging, "data_server.py", "import os\n\n\ndef main():\n    return 0\n")
    _write(staging, "resources/app/main.js", "const env = require('./runtime_env');\n")
    _write(staging, "resources/app/app/app.js", "window.__widget = 1;\n")
    _write(staging, "README.md", "A portable widget.\n")
    result = release_sanitize.scan_staging(str(staging))
    assert release_sanitize.is_clean(result), result


# ---------------------------------------------------------------------------
# forbidden artifacts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rel", [
    "tests/test_thing.py",
    "fixtures/data.json",
    "src/__pycache__/mod.pyc",
    ".pytest_cache/CACHEDIR.TAG",
    ".git/HEAD",
])
def test_forbidden_directories_flagged(tmp_path, rel):
    staging = tmp_path / "staging"
    _write(staging, rel, "payload\n")
    result = release_sanitize.scan_staging(str(staging))
    assert result["forbidden"], rel
    assert any(rel.split("/")[-2] in hit or rel.split("/")[-1] in hit
               for hit in result["forbidden"])


@pytest.mark.parametrize("rel", [
    "logs/run.log",
    "runtime.json",
    "cookies.db",
    "notification_state_1.json",
    "old.bak",
    ".coverage",
    "coverage.xml",
    "screenshot-large.png",
    "module.pyc",
])
def test_forbidden_files_flagged(tmp_path, rel):
    staging = tmp_path / "staging"
    _write(staging, rel, "payload\n")
    result = release_sanitize.scan_staging(str(staging))
    assert result["forbidden"], rel
    assert any(os.path.basename(rel) in hit for hit in result["forbidden"])


# ---------------------------------------------------------------------------
# secret-looking content
# ---------------------------------------------------------------------------
def test_secret_bearer_and_sk_key_flagged(tmp_path):
    staging = tmp_path / "staging"
    _write(staging, "net.txt",
           "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234\n"
           "key = sk-ABCDEFGHIJKLMNOP1234\n")
    result = release_sanitize.scan_staging(str(staging))
    labels = " ".join(result["secret_hits"])
    assert "bearer_token" in labels
    assert "api_key_sk" in labels
    assert not release_sanitize.is_clean(result)


def test_secret_auth_api_key_and_workspace_flagged(tmp_path):
    staging = tmp_path / "staging"
    _write(staging, "cfg.txt",
           "https://example.test/?auth=abcdefghijklmnopqrstuvwx\n"
           'api_key="abcdefghijklmnop1234"\n'
           "workspace_id=wrk_prod9x8y7z6w5v\n")
    result = release_sanitize.scan_staging(str(staging))
    labels = " ".join(result["secret_hits"])
    assert "auth_param" in labels
    assert "api_key_literal" in labels
    assert "workspace_id" in labels


def test_known_placeholders_are_not_flagged(tmp_path):
    staging = tmp_path / "staging"
    _write(staging, "example.json", json.dumps({
        "api_key": "PLACEHOLDER-NOT-A-REAL-KEY",
        "server": {"workspace_id": "wrk_PLACEHOLDER"},
    }))
    result = release_sanitize.scan_staging(str(staging))
    assert result["secret_hits"] == []
    assert release_sanitize.is_clean(result)


# ---------------------------------------------------------------------------
# debug hooks / bundled local state
# ---------------------------------------------------------------------------
def test_debug_hooks_bundled_config_state_and_fixture(tmp_path):
    staging = tmp_path / "staging"
    _write(staging, "config.json", "{}\n")
    _write(staging, "notification_state.json", "{}\n")
    _write(staging, "resources/app/renderer_fixture.json", "{}\n")
    result = release_sanitize.scan_staging(str(staging))
    hits = " ".join(result["debug_hook_hits"])
    assert "config.json" in hits
    assert "notification_state" in hits
    assert "fixture json" in hits


def test_debug_hook_env_default_flagged_comments_and_reads_ignored(tmp_path):
    staging = tmp_path / "staging"
    _write(staging, "launch.cmd", "set OPENCODE_WIDGET_DEBUG=1\n")
    _write(staging, "comment.js", "// OPENCODE_WIDGET_DEBUG=1 enables diagnostics\n")
    _write(staging, "read.js", "const v = process.env.OPENCODE_WIDGET_DEBUG;\n")
    result = release_sanitize.scan_staging(str(staging))
    hits = " ".join(result["debug_hook_hits"])
    assert "launch.cmd" in hits
    assert "comment.js" not in hits
    assert "read.js" not in hits


def test_is_clean_helper():
    assert release_sanitize.is_clean(
        {"forbidden": [], "secret_hits": [], "debug_hook_hits": []})
    assert not release_sanitize.is_clean(
        {"forbidden": ["x"], "secret_hits": [], "debug_hook_hits": []})
    assert not release_sanitize.is_clean(
        {"forbidden": [], "secret_hits": ["y"], "debug_hook_hits": []})
    assert not release_sanitize.is_clean(
        {"forbidden": [], "secret_hits": [], "debug_hook_hits": ["z"]})


# ---------------------------------------------------------------------------
# single version source + build-output invariants (static)
# ---------------------------------------------------------------------------
def test_version_single_source_matches_package_json():
    package = os.path.join(PROJECT_DIR, "electron", "package.json")
    with open(package, "r", encoding="utf-8") as fh:
        version = json.load(fh)["version"]
    assert version == "0.9.0-rc.1"
    assert paths.app_version() == version


def test_gitignore_ignores_build_output():
    with open(os.path.join(PROJECT_DIR, ".gitignore"), "r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh]
    for entry in ("dist/", "release/", "*.zip", "formula_cache.json"):
        assert entry in lines, entry


def test_build_release_script_references_required_steps():
    script = os.path.join(PROJECT_DIR, "scripts", "build-release.ps1")
    assert os.path.isfile(script)
    with open(script, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    assert "release_sanitize.py" in text
    assert "resources/app" in text.replace("\\", "/").replace("\\\\", "/")
    assert "SHA256" in text or "Get-FileHash" in text
