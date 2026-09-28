"""Phase 2.1: static guards that the runtime Bearer token stays in the main process.

Checks the preload bridge's exposed surface, the main-process api-request proxy,
and that the renderer has no token/base/Authorization plumbing at all.
"""
import glob
import os
import re

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
PRELOAD_JS = os.path.join(PROJECT_DIR, "electron", "preload.js")
MAIN_JS = os.path.join(PROJECT_DIR, "electron", "main.js")
APP_DIR = os.path.join(PROJECT_DIR, "electron", "app")
INDEX_HTML = os.path.join(APP_DIR, "index.html")
APP_JS = os.path.join(APP_DIR, "app.js")

EXPECTED_WIDGET_API = {
    # pre-existing bridge methods, unchanged
    "resize",
    "openLogin",
    "grabAuth",
    "saveUiState",
    "onSnapSmall",
    "onSnapRestore",
    "setClickThrough",
    "exitSnap",
    "quit",
    # per-endpoint api proxy
    "apiGetState",
    "apiGetConfig",
    "apiGetFormula",
    "apiGetViews",
    "apiGetView",
    "apiGetAgents",
    "apiGetModels",
    "apiGetProviders",
    "apiGetSessions",
    "apiGetForecast",
    "apiPostKey",
    "apiPostServer",
    "apiPostCalibrate",
    "apiPostSync",
}


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _balanced_block(src, open_idx):
    """Return src[open_idx:close_idx+1] for the brace at open_idx (strings skipped)."""
    depth = 0
    in_str = None
    i = open_idx
    while i < len(src):
        ch = src[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
        i += 1
    raise AssertionError("unbalanced braces while scanning source")


def _block_after_marker(src, marker):
    start = src.index(marker)
    brace = src.index("{", start)
    return _balanced_block(src, brace)


def _top_level_property_names(block):
    """Names of properties at depth 1 inside a `{ ... }` block."""
    block = block.strip()
    assert block.startswith("{") and block.endswith("}")
    body = block[1:-1]
    segments = []
    cur = []
    depth = 0
    in_str = None
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str is not None:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(body):
                cur.append(body[i + 1])
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in "\"'`":
            in_str = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            segments.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    segments.append("".join(cur))

    names = set()
    for seg in segments:
        head, sep, _ = seg.partition(":")
        head = head.strip()
        if sep and re.fullmatch(r"\w+", head):
            names.add(head)
    return names


def _returned_object_literals(block):
    literals = []
    for match in re.finditer(r"\breturn\s*\{", block):
        literals.append(_balanced_block(block, match.end() - 1))
    return literals


def test_preload_has_no_api_env_and_exact_method_allowlist():
    src = _read(PRELOAD_JS)
    assert "apiEnv" not in src
    assert "api-request" in src
    exposed = _top_level_property_names(
        _block_after_marker(src, "exposeInMainWorld('widgetAPI'")
    )
    assert exposed == EXPECTED_WIDGET_API


def test_main_has_api_request_proxy_and_no_api_env_handler():
    src = _read(MAIN_JS)
    assert "api-request" in src
    for route in ("/api/state", "/api/config", "/api/agents", "/api/sync",
                  "/api/forecast",
                  "/api/models", "/api/providers", "/api/sessions"):
        assert route in src
    assert "Authorization" in src
    assert "api-env" not in src
    # forecast is allowlisted bare only: never as a query-bearing path
    assert "/api/forecast?" not in src


def test_main_api_request_result_never_contains_token():
    src = _read(MAIN_JS)
    block = _block_after_marker(src, "ipcMain.handle('api-request'")
    literals = _returned_object_literals(block)
    assert literals, "api-request handler has no object returns"
    for literal in literals:
        assert not re.search(r"\btoken\s*:", literal), literal
    assert not re.search(r"\breturn\s+env\b", block)


def test_renderer_has_no_token_surface():
    files = [INDEX_HTML, APP_JS] + sorted(glob.glob(os.path.join(APP_DIR, "dashboard", "*.js")))
    for path in files:
        src = _read(path)
        for needle in ("apiEnv", "API_BASE", "API_TOKEN", "Bearer ", "runtime.json"):
            assert needle not in src, "%s still references %r" % (path, needle)
