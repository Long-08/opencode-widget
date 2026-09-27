"""Phase 2C: OpenCode DB reader layer (legacy + current schema) and agent stats.

Fixtures build disposable SQLite DBs via tests/helpers.py; no real user DB is
ever touched. The module global gw.OPENCODE_DB is monkeypatched per test.
"""
import hashlib
import sqlite3

import pytest

import helpers


def _insert_raw_message(path, data, session_id="ses_1", type_="assistant", id_="raw_0"):
    """Insert a session_message row with a raw (possibly invalid-JSON) data blob."""
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "INSERT OR REPLACE INTO session_message "
            "(id, session_id, type, seq, time_created, time_updated, data) "
            "VALUES (?,?,?,?,?,?,?)",
            (id_, session_id, type_, 0, 0, 0, data),
        )
        con.commit()
    finally:
        con.close()


def _day(gw, ts):
    from datetime import datetime

    return datetime.fromtimestamp(ts / 1000, gw.LOCAL_TZ).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# schema detection
# --------------------------------------------------------------------------
def test_detect_schema_missing(gw, monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "OPENCODE_DB", str(tmp_path / "nope.db"))
    det = gw.detect_opencode_schema()
    assert det["schema"] == "missing"
    assert det["status"] == "missing_db"
    assert det["tables"] == []
    assert det["error"] is None


def test_detect_schema_legacy(gw, monkeypatch, tmp_path):
    db = helpers.build_legacy_opencode_db(tmp_path / "opencode.db", [])
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    det = gw.detect_opencode_schema()
    assert det["schema"] == "legacy"
    assert det["status"] == "ok"
    assert "message" in det["tables"]


def test_detect_schema_current(gw, monkeypatch, tmp_path):
    db = helpers.build_current_opencode_db(tmp_path / "opencode.db", [])
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    det = gw.detect_opencode_schema()
    assert det["schema"] == "current"
    assert det["status"] == "ok"
    assert "session_message" in det["tables"] and "session_v2" in det["tables"]
    assert "legacy_also_present" not in det


def test_detect_schema_unsupported(gw, monkeypatch, tmp_path):
    import sqlite3 as s

    db = tmp_path / "opencode.db"
    con = s.connect(str(db))
    con.execute("CREATE TABLE unrelated (x TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    det = gw.detect_opencode_schema()
    assert det["schema"] == "unsupported"
    assert det["status"] == "unsupported_schema"


def test_detect_schema_both_prefers_current(gw, monkeypatch, tmp_path):
    # both schemas present -> current wins, legacy flagged
    db = helpers.build_current_opencode_db(tmp_path / "opencode.db", [])
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE message (id TEXT PRIMARY KEY, data TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    det = gw.detect_opencode_schema()
    assert det["schema"] == "current"
    assert det["legacy_also_present"] is True


def test_detect_schema_error_generic_message(gw, monkeypatch, tmp_path):
    # a non-database file triggers an error; the message must be generic
    db = tmp_path / "opencode.db"
    db.write_bytes(b"this is not a sqlite database at all")
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    det = gw.detect_opencode_schema()
    assert det["status"] == "error"
    assert det["error"]
    assert str(db) not in det["error"]
    assert " " not in det["error"]  # exception type only, no detail/path


# --------------------------------------------------------------------------
# legacy reader
# --------------------------------------------------------------------------
def test_read_legacy_usage_normalized_extras(gw, monkeypatch, tmp_path):
    db = helpers.build_legacy_opencode_db(
        tmp_path / "opencode.db",
        [helpers.legacy_assistant_message(created=1000, model_id="glm-5.2",
                                          provider_id="opencode-go", cost=1.5, completed=1600)],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, skipped = gw.read_legacy_usage()
    assert skipped == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == 1000
    assert row["model"] == "glm-5.2"
    assert row["model_id"] == "glm-5.2"
    assert row["provider_id"] == "opencode-go"
    assert row["agent"] is None
    assert row["session_id"] is None
    assert row["parent_session_id"] is None
    assert row["variant"] is None


def test_read_legacy_usage_skip_counts_and_srcs_filter(gw, monkeypatch, tmp_path):
    db = helpers.build_legacy_opencode_db(
        tmp_path / "opencode.db",
        [
            {"role": "assistant", "modelID": "no-time", "providerID": "opencode",
             "cost": 1.0, "time": {"created": 0}},  # missing created -> skipped
            {"role": "assistant", "modelID": "no-provider", "cost": 1.0,
             "time": {"created": 3000}},  # missing providerID -> skipped
            helpers.legacy_assistant_message(created=1000, provider_id="opencode-go"),
            helpers.legacy_assistant_message(created=2000, provider_id="openkilo"),
        ],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, skipped = gw.read_legacy_usage()
    assert skipped == 2
    assert len(rows) == 2
    # srcs filter drops rows silently (not counted as skipped):
    # openkilo->kilo and the no-time opencode row (filtered before the time check)
    rows, skipped = gw.read_legacy_usage(srcs=["go"])
    assert skipped == 1
    assert len(rows) == 1
    assert rows[0]["src"] == "go"


# --------------------------------------------------------------------------
# current reader
# --------------------------------------------------------------------------
def test_read_current_usage_field_mapping_and_agent_precedence(gw, monkeypatch, tmp_path):
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [{"session_id": "ses_child",
          "data": helpers.current_assistant_message(created=1000, agent="worker",
                                                    model_id="deepseek-v4.1-flash",
                                                    provider_id="opencode-go", cost=2.5)}],
        sessions=[
            {"id": "ses_parent", "parent_id": None, "agent": "build"},
            {"id": "ses_child", "parent_id": "ses_parent", "agent": "session-agent"},
        ],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, skipped = gw.read_current_usage()
    assert skipped == 0
    row = rows[0]
    assert row["ts"] == 1000
    assert row["model"] == "deepseek-v4.1-flash"
    assert row["provider_id"] == "opencode-go"
    assert row["variant"] == "high"
    assert row["cost"] == pytest.approx(2.5)
    assert row["session_id"] == "ses_child"
    assert row["parent_session_id"] == "ses_parent"
    # message-level agent wins over session-level
    assert row["agent"] == "worker"
    assert row["dur_ms"] == 1000


def test_read_current_usage_session_agent_fallback(gw, monkeypatch, tmp_path):
    msg = helpers.current_assistant_message(created=1000)
    del msg["agent"]  # message-level agent absent -> session agent used
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [{"session_id": "ses_1", "data": msg}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "oracle"}],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, _ = gw.read_current_usage()
    assert rows[0]["agent"] == "oracle"


def test_read_current_usage_skips_invalid_and_missing(gw, monkeypatch, tmp_path):
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [
            # missing time.created
            {"data": {"model": {"id": "m", "providerID": "opencode-go"}, "time": {}}},
            # missing model/providerID
            {"data": {"time": {"created": 5}, "model": {}}},
        ],
        sessions=[],
    )
    _insert_raw_message(db, "{not valid json", id_="bad")
    _insert_raw_message(db, "123", id_="scalar")  # json_valid but not an object
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, skipped = gw.read_current_usage()
    assert rows == []
    # the truly invalid blob is excluded by `json_valid` at SQL level; the
    # valid-JSON scalar plus the two missing-field rows are counted as skipped
    assert skipped == 3


def test_read_current_usage_src_mapping_and_silent_filter(gw, monkeypatch, tmp_path):
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [
            {"data": helpers.current_assistant_message(created=1000, provider_id="opencode")},
            {"data": helpers.current_assistant_message(created=2000, provider_id="mystery-vendor")},
        ],
        sessions=[],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, skipped = gw.read_current_usage()
    # known provider mapped to short name; unknown passed through verbatim
    assert [r["src"] for r in rows] == ["zen", "mystery-vendor"]
    # srcs filter drops rows silently (not counted as skipped)
    rows, skipped = gw.read_current_usage(srcs=["zen"])
    assert skipped == 0
    assert [r["src"] for r in rows] == ["zen"]


def test_read_current_usage_model_json_string_defensive(gw, monkeypatch, tmp_path):
    # some DBs store `model` as a JSON string rather than a dict
    data = {
        "time": {"created": 1000, "completed": 1600},
        "agent": "build",
        "model": '{"id": "glm-5.2", "providerID": "opencode-go", "variant": "low"}',
        "cost": 1.0,
        "tokens": {"input": 1, "output": 2},
    }
    db = helpers.build_current_opencode_db(tmp_path / "opencode.db", [{"data": data}])
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, skipped = gw.read_current_usage()
    assert skipped == 0
    row = rows[0]
    assert row["model"] == "glm-5.2"
    assert row["provider_id"] == "opencode-go"
    assert row["variant"] == "low"


def test_read_current_usage_tokens_and_dur_ms(gw, monkeypatch, tmp_path):
    data = helpers.current_assistant_message(
        created=1000, completed=1000,  # non-positive duration -> None
        tokens={"input": 10, "output": 4},  # cache/reasoning missing -> 0
    )
    db = helpers.build_current_opencode_db(tmp_path / "opencode.db", [{"data": data}])
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, _ = gw.read_current_usage()
    assert rows[0]["dur_ms"] is None
    assert rows[0]["tokens"] == {
        "input": 10, "output": 4, "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }


def test_readers_are_read_only(gw, monkeypatch, tmp_path):
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [{"data": helpers.current_assistant_message(created=1000)}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "build"}],
    )
    before = hashlib.sha256(open(db, "rb").read()).hexdigest()
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    gw.detect_opencode_schema()
    gw.read_current_usage()
    gw.read_opencode_usage()
    gw.read_opencode_all()
    after = hashlib.sha256(open(db, "rb").read()).hexdigest()
    assert before == after


# --------------------------------------------------------------------------
# dispatcher + wrapper
# --------------------------------------------------------------------------
def test_read_opencode_usage_current_meta(gw, monkeypatch, tmp_path):
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [{"data": helpers.current_assistant_message(created=1000)}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "build"}],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, meta = gw.read_opencode_usage()
    assert len(rows) == 1
    assert meta["schema"] == "current"
    assert meta["status"] == "ok"
    assert meta["row_count"] == 1
    assert meta["skipped_rows"] == 0
    assert meta["error"] is None


def test_read_opencode_usage_unsupported_explicit_status(gw, monkeypatch, tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE unrelated (x TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))
    rows, meta = gw.read_opencode_usage()
    assert rows == []
    assert meta["schema"] == "unsupported"
    assert meta["status"] == "unsupported_schema"


def test_read_opencode_usage_missing_db(gw, monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "OPENCODE_DB", str(tmp_path / "nope.db"))
    rows, meta = gw.read_opencode_usage()
    assert rows == []
    assert meta["schema"] == "missing"
    assert meta["status"] == "missing_db"


def test_read_opencode_all_wrapper_legacy_and_current(gw, monkeypatch, tmp_path):
    legacy = helpers.build_legacy_opencode_db(
        tmp_path / "legacy.db",
        [helpers.legacy_assistant_message(created=1000)],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(legacy))
    assert len(gw.read_opencode_all()) == 1

    current = helpers.build_current_opencode_db(
        tmp_path / "current.db",
        [{"data": helpers.current_assistant_message(created=2000)}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "build"}],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(current))
    rows = gw.read_opencode_all()
    assert len(rows) == 1
    assert rows[0]["ts"] == 2000


# --------------------------------------------------------------------------
# agent_stats
# --------------------------------------------------------------------------
def _arow(ts, agent, cost, model="m1", provider="opencode-go", variant="high", tokens=None):
    return helpers.make_row(ts, model, cost=cost, src="go", agent=agent,
                            provider_id=provider, variant=variant,
                            tokens=tokens or helpers.default_tokens())


def test_agent_stats_aggregation_and_sorting(gw):
    rows = [
        _arow(1000, "build", 1.0),
        _arow(2000, "build", 2.0),
        _arow(3000, "oracle", 0.5),
    ]
    stats = gw.agent_stats(rows)
    # agents sorted by cost desc
    assert [s["agent"] for s in stats] == ["build", "oracle"]
    build = stats[0]
    assert build["requests"] == 2
    assert build["cost"] == pytest.approx(3.0)
    # per-row default tokens: input=100, output=50
    assert build["tokens"] == {"total": 300, "input": 200, "output": 100,
                               "cache_read": 0, "cache_write": 0}


def test_agent_stats_unknown_agent_preserved(gw):
    rows = [_arow(1000, None, 1.0), _arow(2000, "mystery-agent", 1.0)]
    stats = gw.agent_stats(rows)
    names = {s["agent"] for s in stats}
    assert names == {"unknown", "mystery-agent"}
    for s in stats:
        assert s["group"] == "unmapped"


def test_agent_stats_group_mapping_and_unmapped_fallback(gw):
    rows = [_arow(1000, "build", 1.0), _arow(2000, "oracle", 1.0)]
    stats = gw.agent_stats(rows, agent_groups={"build": "Main"})
    by = {s["agent"]: s for s in stats}
    assert by["build"]["group"] == "Main"
    assert by["oracle"]["group"] == "unmapped"


def test_agent_stats_cutoff_filter(gw):
    old_ts, new_ts = 1_600_000_000_000, 1_600_000_000_000 + 2 * 86400 * 1000
    cutoff = _day(gw, new_ts)
    rows = [_arow(old_ts, "build", 1.0), _arow(new_ts, "build", 2.0)]
    stats = gw.agent_stats(rows, cutoff=cutoff)
    assert len(stats) == 1
    assert stats[0]["requests"] == 1
    assert stats[0]["cost"] == pytest.approx(2.0)


def test_agent_stats_model_breakdown_sorted_by_cost(gw):
    rows = [
        _arow(1000, "build", 1.0, model="cheap"),
        _arow(2000, "build", 5.0, model="pricey"),
        _arow(3000, "build", 0.5, model="mid"),
    ]
    stats = gw.agent_stats(rows)
    models = [m["model"] for m in stats[0]["models"]]
    assert models == ["pricey", "cheap", "mid"]
    top = stats[0]["models"][0]
    assert top["provider"] == "opencode-go"
    assert top["variant"] == "high"
    assert top["requests"] == 1
    assert top["cost"] == pytest.approx(5.0)
