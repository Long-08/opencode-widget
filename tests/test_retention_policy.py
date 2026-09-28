"""Phase 6A: quota_snapshot retention policy.

Covers the pure prune helper in usage_remote, the opt-in/disabled-by-default
startup hook in data_server, and the guarantee that the OpenCode DB is only
ever opened read-only by the reader/observability path.
"""
import os
import sqlite3

import pytest

import helpers
import usage_remote as ur

DAY_MS = 24 * 3600 * 1000


def _insert_quota(conn, fetched_at, kind="session"):
    conn.execute(
        "INSERT INTO quota_snapshot (fetched_at, kind, label, pct, reset_text, workspace_id) "
        "VALUES (?,?,?,?,?,?)",
        (fetched_at, kind, "label", 10.0, "reset", "wrk_dummy"),
    )


def _quota_fetched(conn):
    return sorted(r[0] for r in conn.execute("SELECT fetched_at FROM quota_snapshot"))


def test_prune_cutoff_correctness():
    conn = ur.init_remote_db()
    try:
        now = 1_000_000_000_000
        cutoff = now - 30 * DAY_MS
        _insert_quota(conn, cutoff - 1)      # older -> pruned
        _insert_quota(conn, cutoff)          # exactly at cutoff -> kept
        _insert_quota(conn, cutoff + 1)      # newer -> kept
        _insert_quota(conn, now)             # newest -> kept
        conn.commit()

        removed = ur.prune_quota_snapshot(conn, 30, now_ms=now)

        assert removed == 1
        assert _quota_fetched(conn) == [cutoff, cutoff + 1, now]
    finally:
        conn.close()


def test_prune_returns_count_and_is_idempotent():
    conn = ur.init_remote_db()
    try:
        now = 2_000_000_000_000
        for i in range(3):
            _insert_quota(conn, now - 40 * DAY_MS - i)
        conn.commit()

        assert ur.prune_quota_snapshot(conn, 30, now_ms=now) == 3
        assert ur.prune_quota_snapshot(conn, 30, now_ms=now) == 0
    finally:
        conn.close()


def test_prune_non_positive_days_is_noop():
    conn = ur.init_remote_db()
    try:
        now = 3_000_000_000_000
        _insert_quota(conn, now - 1000 * DAY_MS)
        conn.commit()

        assert ur.prune_quota_snapshot(conn, 0, now_ms=now) == 0
        assert ur.prune_quota_snapshot(conn, -5, now_ms=now) == 0
        assert ur.prune_quota_snapshot(conn, None, now_ms=now) == 0
        assert len(_quota_fetched(conn)) == 1
    finally:
        conn.close()


def test_prune_touches_only_quota_snapshot():
    conn = ur.init_remote_db()
    try:
        now = 4_000_000_000_000
        _insert_quota(conn, now - 100 * DAY_MS)
        conn.execute(
            "INSERT INTO usage_records (id, workspace_id, time_created, model, cost) "
            "VALUES (?,?,?,?,?)",
            ("u1", "wrk_dummy", now, "m-a", 1),
        )
        conn.execute(
            "INSERT INTO cost_summary (workspace_id, year, month, model, key_id, plan, total_cost, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("wrk_dummy", 2026, 1, "m-a", "k", "go", 1, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?,?)",
            ("last_sync_ts", str(now)),
        )
        conn.commit()

        assert ur.prune_quota_snapshot(conn, 30, now_ms=now) == 1

        assert conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM cost_summary").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sync_meta").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# data_server startup hook: opt-in only
# ---------------------------------------------------------------------------
@pytest.fixture()
def _no_cookie_cleanup(monkeypatch):
    import browser_cookie

    monkeypatch.setattr(browser_cookie, "cleanup_stale_cookie_temps", lambda *a, **k: 0)


def test_quota_prune_disabled_by_default(data_server, monkeypatch, _no_cookie_cleanup):
    calls = []
    monkeypatch.setattr(
        data_server.ur, "prune_quota_snapshot",
        lambda *a, **k: calls.append(a) or 0,
    )
    data_server._startup_hygiene()
    assert calls == []


@pytest.mark.parametrize("value", ["30", True, 30.0, 0, -1, None])
def test_quota_prune_only_for_positive_int(data_server, monkeypatch, _no_cookie_cleanup, value):
    calls = []
    monkeypatch.setattr(
        data_server.ur, "prune_quota_snapshot",
        lambda *a, **k: calls.append(a) or 0,
    )
    data_server.gw.save_config({"quota_snapshot_retention_days": value})
    data_server._startup_hygiene()
    assert calls == []


def test_quota_prune_runs_when_configured(data_server, monkeypatch, _no_cookie_cleanup):
    seen = {}

    class _Conn:
        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(data_server.ur, "init_remote_db", lambda: _Conn())
    monkeypatch.setattr(
        data_server.ur, "prune_quota_snapshot",
        lambda conn, days, **k: seen.update(days=days) or 2,
    )
    data_server.gw.save_config({"quota_snapshot_retention_days": 30})

    data_server._startup_hygiene()

    assert seen["days"] == 30
    assert seen["closed"] is True


# ---------------------------------------------------------------------------
# OpenCode DB is only ever opened read-only
# ---------------------------------------------------------------------------
def test_reader_source_uses_readonly_uri(gw):
    with open(gw.__file__, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "?mode=ro" in src
    assert src.count("?mode=ro") >= 4


def test_reader_does_not_modify_opencode_db(gw, monkeypatch, tmp_path):
    db = helpers.build_legacy_opencode_db(
        tmp_path / "opencode.db",
        [helpers.legacy_assistant_message(1000)],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    before = os.path.getmtime(db)

    rows, meta = gw.read_opencode_usage()

    assert meta["schema"] == "legacy"
    assert len(rows) == 1
    assert os.path.getmtime(db) == before
    for suffix in ("-wal", "-shm", "-journal"):
        assert not os.path.exists(db + suffix)
