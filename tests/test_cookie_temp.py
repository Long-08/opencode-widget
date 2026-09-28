"""Phase 6A: browser cookie temp-copy hygiene.

Every test redirects the temp base dir (env + monkeypatched helper) into
``tmp_path`` so no real %TEMP% files are touched.
"""
import os
import sqlite3
import time

import pytest

import browser_cookie


@pytest.fixture(autouse=True)
def cookie_tmp(tmp_path, monkeypatch):
    """Point browser_cookie's temp base at a per-test directory."""
    monkeypatch.setenv("OPENCODE_WIDGET_TMPDIR", str(tmp_path))
    monkeypatch.setattr(browser_cookie, "_temp_base_dir", lambda: str(tmp_path))
    return tmp_path


def _cookie_temp_dir(tmp_path):
    return os.path.join(str(tmp_path), browser_cookie.COOKIE_TEMP_SUBDIR)


def _build_cookies_db(path):
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
        con.execute(
            "INSERT INTO cookies (host_key, name, encrypted_value) VALUES (?,?,?)",
            ("opencode.ai", "auth", b"v10"),
        )
        con.commit()
    finally:
        con.close()
    return str(path)


def _list_temp_copies(tmp_path):
    d = _cookie_temp_dir(tmp_path)
    if not os.path.isdir(d):
        return []
    return os.listdir(d)


def test_temp_copy_removed_on_success(cookie_tmp):
    src = _build_cookies_db(cookie_tmp / "Cookies")
    # key is arbitrary; a bogus encrypted_value simply yields no cookie value.
    result = browser_cookie._read_cookie(b"0" * 16, src)
    assert result == ""
    assert _list_temp_copies(cookie_tmp) == []


def test_temp_copy_removed_when_parsing_raises(cookie_tmp):
    src = cookie_tmp / "NotADatabase"
    src.write_bytes(b"this is definitely not a sqlite database")
    result = browser_cookie._read_cookie(b"0" * 16, str(src))
    assert result == ""
    assert _list_temp_copies(cookie_tmp) == []


def test_missing_source_leaves_no_file(cookie_tmp):
    result = browser_cookie._read_cookie(b"0" * 16, str(cookie_tmp / "missing.db"))
    assert result == ""
    assert _list_temp_copies(cookie_tmp) == []


def _mk(path, mtime):
    with open(path, "wb") as fh:
        fh.write(b"x")
    os.utime(path, (mtime, mtime))


def test_cleanup_stale_cookie_temps(cookie_tmp):
    d = _cookie_temp_dir(cookie_tmp)
    os.makedirs(d, exist_ok=True)
    now = time.time()

    old_owned = os.path.join(d, "opencode-widget-cookie-abc-1.db")
    fresh_owned = os.path.join(d, "opencode-widget-cookie-def-2.db")
    old_legacy = os.path.join(d, "cookie_123.db")
    fresh_legacy = os.path.join(d, "cookie_456.db")
    unrelated = os.path.join(d, "notes.txt")
    almost_legacy = os.path.join(d, "cookie_backup.db.bak")

    _mk(old_owned, now - 100000)
    _mk(fresh_owned, now)
    _mk(old_legacy, now - 100000)
    _mk(fresh_legacy, now)
    _mk(unrelated, now - 100000)
    _mk(almost_legacy, now - 100000)

    removed = browser_cookie.cleanup_stale_cookie_temps(max_age_s=86400)

    assert removed == 2
    assert not os.path.exists(old_owned)
    assert not os.path.exists(old_legacy)
    # fresh copies and unrelated files are preserved
    assert os.path.exists(fresh_owned)
    assert os.path.exists(fresh_legacy)
    assert os.path.exists(unrelated)
    assert os.path.exists(almost_legacy)


def test_cleanup_missing_dir_is_zero(cookie_tmp):
    assert browser_cookie.cleanup_stale_cookie_temps() == 0
