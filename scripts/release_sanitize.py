#!/usr/bin/env python3
"""Phase 7 release sanitation scanner (stdlib-only, pure, non-destructive).

This module inspects a *staging* tree (what would be shipped) and reports what
must never leave a developer machine. It only ever **reads** files: it does not
delete, rewrite, move or chmod anything, and it never touches user data
(``%APPDATA%``, the real ``config.json`` or any OpenCode database).

Three independent checks are produced by :func:`scan_staging`:

``forbidden``
    Paths that are test/dev-only artifacts: test trees, fixture dirs, caches,
    logs, local runtime state, screenshots, coverage output and backups.

``secret_hits``
    File *contents* that look like real credentials (bearer tokens, ``sk-``
    keys, ``auth=`` values, ``api_key`` literals, ``workspace_id`` ``wrk_``
    values). Matched secret text is deliberately **not** echoed back, only the
    path/line and the pattern label.

``debug_hook_hits``
    Debug affordances or bundled local state: a bundled ``config.json``, a file
    that *sets* ``OPENCODE_WIDGET_DEBUG`` to ``1``/``true`` as a default,
    ``notification_state*.json`` or a ``*fixture*.json``.

False positives are suppressed on purpose:

* :data:`KNOWN_PLACEHOLDERS` documents the placeholder values used by the test
  fixtures (``wrk_PLACEHOLDER``, ``dummy-*`` ...). A match containing one of
  them is ignored. Tests are excluded from staging anyway; this is a
  belt-and-braces guard so a placeholder can never block a release.
* ``OPENCODE_WIDGET_DEBUG`` mentions inside comments (e.g. the explanatory
  comment in ``runtime_manager.js``) are ignored: only real *assignments* count.

The public API is :func:`scan_staging` and :func:`is_clean`; the module is
importable with no side effects. ``python release_sanitize.py <staging>`` is a
thin CLI that prints the JSON report and exits non-zero when the tree is dirty.
"""
import fnmatch
import json
import os
import re
import sys

__all__ = ["scan_staging", "is_clean", "KNOWN_PLACEHOLDERS"]

# ---------------------------------------------------------------------------
# forbidden paths
# ---------------------------------------------------------------------------
# Directory *names* that must never appear in a release tree.
FORBIDDEN_DIRS = frozenset({
    "tests",
    "fixtures",
    "__pycache__",
    ".pytest_cache",
    ".git",
})

# File/dir *name* globs that must never appear in a release tree.
FORBIDDEN_GLOBS = (
    "*.log",
    "runtime.json",
    "cookie*.db",
    "notification_state*.json",
    "*.bak",
    ".coverage",
    "coverage*",
    "screenshot*.png",
    "*.pyc",
)

# Extensions whose contents are never text-scanned (keeps the scan fast and
# avoids decoding large Electron binaries). Everything else is read best-effort.
_SKIP_CONTENT_EXTS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".node", ".wasm",
    ".pak", ".dat", ".bin", ".asar", ".blob",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".icns", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".7z", ".gz", ".xz", ".tar", ".rar",
    ".db", ".sqlite", ".sqlite3", ".enc",
})

# Hard cap for content scanning (8 MiB); larger files are skipped. Best effort.
_MAX_SCAN_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# secret patterns
# ---------------------------------------------------------------------------
# (label, compiled regex). A match is reported by label only, never by value.
_SECRET_PATTERNS = (
    ("bearer_token", re.compile(r"Bearer\s+\S{20,}")),
    ("api_key_sk", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("auth_param", re.compile(r"auth=[A-Za-z0-9._-]{20,}")),
    (
        "api_key_literal",
        re.compile(r"""api_key["']?\s*[=:]\s*["']?[A-Za-z0-9._-]{16,}"""),
    ),
    (
        "workspace_id",
        re.compile(r"""workspace_id["']?\s*[=:]\s*["']?wrk_[A-Za-z0-9]{6,}"""),
    ),
)

# Known test-fixture placeholder values (documented negative guard). Any match
# whose matched text contains one of these is treated as a fixture, not a
# secret. Keep in sync with tests/ fixtures if they change.
KNOWN_PLACEHOLDERS = (
    "PLACEHOLDER-NOT-A-REAL-KEY",
    "wrk_PLACEHOLDER",
    "wrk_legacy",
    "wrk_plain",
    "wrk_before",
    "wrk_future",
    "wrk_dummy",
    "wrk_abc",
    "dummy-legacy-key",
    "dummy-legacy-cookie",
    "dummy-cookie",
    "dummy-plain",
    "dummy-load-key",
    "dummy-load-cookie",
)

# Real assignment of the debug flag, e.g. `set OPENCODE_WIDGET_DEBUG=1`,
# `"OPENCODE_WIDGET_DEBUG": "true"` or `process.env.OPENCODE_WIDGET_DEBUG='1'`.
_DEBUG_ENV_RE = re.compile(
    r"""OPENCODE_WIDGET_DEBUG["']?\s*[=:]\s*["']?(1|true)\b""",
    re.IGNORECASE,
)

# Lines that are pure comments are ignored by the debug-hook content check.
_COMMENT_PREFIXES = ("//", "#", "*", ";", "<!--", "--", "'")


def _norm(rel_path):
    return rel_path.replace("\\", "/").strip("/")


def _forbidden_reason(rel_path):
    """Return a human reason if *rel_path* is a forbidden artifact, else None."""
    norm = _norm(rel_path)
    if not norm:
        return None
    parts = norm.split("/")
    # Check every segment (a bare directory entry has itself as its last part).
    for part in parts:
        if part in FORBIDDEN_DIRS:
            return "forbidden directory '%s/'" % part
    for part in parts:
        for pattern in FORBIDDEN_GLOBS:
            if fnmatch.fnmatch(part, pattern):
                return "forbidden pattern '%s'" % pattern
    return None


def _is_placeholder(matched_text):
    return any(value in matched_text for value in KNOWN_PLACEHOLDERS)


def _read_text(full_path, rel_path):
    """Best-effort text read; returns None when the file is not scanned."""
    ext = os.path.splitext(full_path)[1].lower()
    if ext in _SKIP_CONTENT_EXTS:
        return None
    try:
        if os.path.getsize(full_path) > _MAX_SCAN_BYTES:
            return None
        with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def _scan_secrets(rel_path, text, found):
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, regex in _SECRET_PATTERNS:
            match = regex.search(line)
            if match is None:
                continue
            if _is_placeholder(match.group(0)):
                continue
            found.add("%s:%d: %s" % (rel_path, lineno, label))


def _scan_debug_hooks(rel_path, base_name, text, found):
    low = base_name.lower()
    if low == "config.json":
        found.add("%s: bundled config.json" % rel_path)
    if low.startswith("notification_state") and low.endswith(".json"):
        found.add("%s: bundled notification_state*.json" % rel_path)
    if "fixture" in low and low.endswith(".json"):
        found.add("%s: bundled fixture json" % rel_path)

    if text is None:
        return
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue
        if _DEBUG_ENV_RE.search(stripped):
            found.add("%s:%d: sets OPENCODE_WIDGET_DEBUG default" % (rel_path, lineno))


def _empty():
    return {"forbidden": [], "secret_hits": [], "debug_hook_hits": []}


def scan_staging(staging_dir):
    """Scan *staging_dir* and return the three lists of findings.

    Never raises and never modifies anything. A missing/blank directory yields
    the empty result. Entries are sorted, de-duplicated strings; secret values
    are never included (only path, line and pattern label).
    """
    result = _empty()
    if not staging_dir or not os.path.isdir(staging_dir):
        return result

    forbidden = set()
    secrets = set()
    debug_hooks = set()

    for root, dirs, files in os.walk(staging_dir):
        for name in list(dirs) + list(files):
            full = os.path.join(root, name)
            rel = _norm(os.path.relpath(full, staging_dir))
            reason = _forbidden_reason(rel)
            if reason:
                forbidden.add("%s: %s" % (rel, reason))
        for name in files:
            full = os.path.join(root, name)
            rel = _norm(os.path.relpath(full, staging_dir))
            text = _read_text(full, rel)
            if text is not None:
                _scan_secrets(rel, text, secrets)
            _scan_debug_hooks(rel, name, text, debug_hooks)

    result["forbidden"] = sorted(forbidden)
    result["secret_hits"] = sorted(secrets)
    result["debug_hook_hits"] = sorted(debug_hooks)
    return result


def is_clean(result):
    """True when *result* contains no findings at all."""
    if not isinstance(result, dict):
        return False
    return not (
        result.get("forbidden")
        or result.get("secret_hits")
        or result.get("debug_hook_hits")
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        sys.stderr.write("usage: release_sanitize.py <staging_dir>\n")
        return 2
    result = scan_staging(argv[0])
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if is_clean(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
