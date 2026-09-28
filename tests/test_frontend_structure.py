"""Phase 5.1: front-end structure guards.

Asserts the split of the former inline renderer into classic scripts + a
local stylesheet: file layout, stylesheet link, ordered classic <script src>
tags, no inline <script>/<style>, no ESM, no external/CDN assets, a minimal CSP
without wildcards or unsafe-eval, and the preserved view roots.
"""
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
INDEX_HTML = os.path.join(APP_DIR, "index.html")

STYLESHEET = "app.css"
SCRIPTS = [
    "dashboard/format.js",
    "dashboard/core.js",
    "dashboard/views.js",
    "dashboard/charts.js",
    "dashboard/timeline.js",
    "dashboard/matrix.js",
    "dashboard/overview.js",
    "dashboard/agents.js",
    "dashboard/models.js",
    "dashboard/sessions.js",
    "dashboard/forecast.js",
    "app.js",
]
STATIC_FILES = ["app.css", "app.js"] + SCRIPTS[:-1]  # app.js covered by SCRIPTS


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _index():
    return _read(INDEX_HTML)


def test_layout_files_exist():
    for rel in STATIC_FILES:
        assert os.path.isfile(os.path.join(APP_DIR, rel)), "missing %s" % rel


def test_index_links_local_stylesheet():
    src = _index()
    assert '<link rel="stylesheet" href="%s">' % STYLESHEET in src


def test_index_has_ordered_classic_scripts():
    src = _index()
    found = re.findall(r"<script[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", src, re.I)
    assert found == SCRIPTS, found


def test_index_has_no_style_block_and_no_inline_script():
    src = _index()
    assert "<style" not in src, "inline <style> block still present"
    openings = re.findall(r"<script\b([^>]*)>", src, re.I)
    assert openings, "no script tags found"
    for attrs in openings:
        assert re.search(r"\bsrc\s*=", attrs, re.I), "inline <script> without src: %r" % attrs
    assert len(openings) == len(re.findall(r"</script>", src, re.I))


def test_no_es_modules():
    assert 'type="module"' not in _index()
    for rel in SCRIPTS:
        assert 'type="module"' not in _read(os.path.join(APP_DIR, rel)), rel


def test_no_external_scripts_or_stylesheets():
    src = _index()
    assert not re.search(r"<script[^>]*\bsrc\s*=\s*[\"']https?://", src, re.I)
    assert not re.search(r"<link[^>]*\bhref\s*=\s*[\"']https?://", src, re.I)
    assert not re.search(r"\bsrc\s*=\s*[\"']//", src, re.I)
    for host in ("cdn.", "unpkg", "jsdelivr", "cdnjs", "googleapis"):
        assert host not in src, "external CDN reference: %s" % host


def test_csp_present_without_wildcards_or_eval():
    src = _index()
    match = re.search(
        r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', src)
    assert match, "CSP meta tag not found"
    csp = match.group(1)
    assert "unsafe-eval" not in csp
    assert "default-src 'none'" in csp
    for directive in csp.split(";"):
        tokens = directive.split()
        if not tokens:
            continue
        name = tokens[0]
        if name in ("script-src", "style-src"):
            assert "'self'" in tokens, "%s must allow local files" % name
            assert "file:" in tokens, "%s must allow file:" % name
            assert "*" not in tokens, "%s must not use a wildcard source" % name
            assert not any(t.startswith("*") for t in tokens), "%s wildcard host" % name


def test_preserved_view_roots():
    src = _index()
    for needle in ('id="smallView"', 'id="compactView"', 'id="dashView"',
                   'id="obsDashboard"', 'id="btnExpand"'):
        assert needle in src, needle
