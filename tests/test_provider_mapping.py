"""Phase-1 baseline: providerID -> src mapping, legacy DB reader, subset semantics."""
import pytest

import helpers


# --------------------------------------------------------------------------
# static provider mapping
# --------------------------------------------------------------------------
def test_provider_src_mapping(gw):
    assert gw.PROVIDER_SRC["opencode"] == "zen"
    assert gw.PROVIDER_SRC["opencode-go"] == "go"
    assert gw.PROVIDER_SRC["openkilo"] == "kilo"
    assert gw.PROVIDER_SRC["tencent-tokenhub"] == "zen"
    assert gw.PROVIDER_SRC["openrouter"] == "router"


# --------------------------------------------------------------------------
# read_opencode_all against the LEGACY schema
# --------------------------------------------------------------------------
def test_read_opencode_all_legacy_schema(gw, monkeypatch, tmp_path):
    db = helpers.build_legacy_opencode_db(
        tmp_path / "opencode.db",
        [
            helpers.legacy_assistant_message(
                created=1000, model_id="glm-5.2", provider_id="opencode-go",
                cost=1.5, completed=1600,
            ),
            # unknown providerID is passed through unchanged (dynamic discovery)
            helpers.legacy_assistant_message(
                created=2000, model_id="mystery", provider_id="unknown-provider",
                cost=2.0,
            ),
            # time.created present but falsy -> skipped
            {"role": "assistant", "providerID": "opencode",
             "modelID": "no-time", "cost": 1.0, "time": {"created": 0}},
            # missing providerID -> skipped
            {"role": "assistant", "modelID": "no-provider", "cost": 1.0,
             "time": {"created": 3000}},
        ],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))

    rows = gw.read_opencode_all()
    assert len(rows) == 2

    first, second = rows
    assert first["src"] == "go"
    assert first["model"] == "glm-5.2"
    assert first["ts"] == 1000
    assert first["cost"] == pytest.approx(1.5)
    assert first["tokens"] == helpers.default_tokens()
    assert first["dur_ms"] == 600  # completed - created

    assert second["src"] == "unknown-provider"
    assert second["model"] == "mystery"
    assert second["cost"] == pytest.approx(2.0)
    assert second["dur_ms"] is None  # no completed timestamp


def test_read_opencode_all_current_schema_returns_rows(gw, monkeypatch, tmp_path):
    # Phase 2C: current schema is now supported (was a documented gap)
    db = helpers.build_current_opencode_db(
        tmp_path / "opencode.db",
        [{"data": helpers.current_assistant_message(created=1000)}],
        sessions=[{"id": "ses_1", "parent_id": None, "agent": "build"}],
    )
    monkeypatch.setattr(gw, "OPENCODE_DB", str(db))

    rows = gw.read_opencode_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == 1000
    assert row["model"] == "deepseek-v4.1-flash"
    assert row["model_id"] == "deepseek-v4.1-flash"
    assert row["provider_id"] == "opencode-go"
    assert row["src"] == "go"
    assert row["agent"] == "build"
    assert row["session_id"] == "ses_1"
    assert row["variant"] == "high"
    assert row["cost"] == pytest.approx(1.0)
    assert row["tokens"] == helpers.default_tokens()
    assert row["dur_ms"] == 1000


# --------------------------------------------------------------------------
# SUBSET_SRCS semantics via supplier_stats
# --------------------------------------------------------------------------
def test_supplier_stats_gateway_subset_semantics(gw):
    rows = [
        helpers.make_row(1000, "glm-5.2", cost=1.0, src="go"),
        # gateway mirror of the go spend (same account) -> excluded from "all"
        helpers.make_row(1000, "glm-5.2", cost=1.0, src="gateway"),
        # other-account gateway spend is independent -> included in "all"
        helpers.make_row(1000, "glm-5.2", cost=2.0, src="gateway", account="other"),
    ]
    st = gw.supplier_stats(rows)

    # gateway keeps its own bucket (both its rows counted)
    assert st["gateway"]["count"] == 2
    assert st["gateway"]["cost"] == pytest.approx(3.0)  # non-go src rate = 1.0

    # "all" drops the gateway mirror but keeps the other-account gateway row
    assert st["all"]["count"] == 2
    assert st["all"]["cost"] == pytest.approx(1.0 * gw.RATE_DEFAULT + 2.0)
