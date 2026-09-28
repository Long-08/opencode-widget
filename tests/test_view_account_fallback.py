"""ViewEngine range/account accounting.

Period views (today/7d/30d) normally count only the logged-in (official) account
and exclude local-estimate ``account="other"`` rows. When a source has *no*
official rows at all (e.g. only the quota pct was synced, no per-record official
data), period views must fall back to the local rows instead of returning 0.
"""
import time
from datetime import timezone

import views


def _row(account, ts):
    return {
        "ts": int(ts),
        "cost": 1.0,
        "model": "deepseek-v4.1-flash",
        "tokens": {"input": 100, "output": 50, "cache": {"read": 0, "write": 0}},
        "src": "go",
        "account": account,
        "provider_id": "opencode-go",
    }


def _engine():
    return views.ViewEngine(views.DEFAULT_FORMULA, norm=lambda s: s,
                            is_free=lambda m: False, local_tz=timezone.utc)


def test_period_view_falls_back_to_local_when_no_official_rows():
    now = time.time() * 1000
    eng = _engine()
    out = eng.execute("go_7d", [_row("other", now)])
    assert out["totals"]["count"] == 1
    assert out["totals"]["cost"] > 0


def test_period_view_prefers_official_rows_when_present():
    now = time.time() * 1000
    eng = _engine()
    out = eng.execute("go_7d", [_row("other", now), _row("own", now)])
    # only the official row is counted for a period view
    assert out["totals"]["count"] == 1


def test_all_view_includes_local_rows():
    now = time.time() * 1000
    eng = _engine()
    out = eng.execute("go_all", [_row("other", now), _row("own", now)])
    assert out["totals"]["count"] == 2
