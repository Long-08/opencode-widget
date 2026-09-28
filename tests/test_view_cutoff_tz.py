"""Range cutoffs must use the local timezone (consistent with ViewEngine._day).

Regression: `_cutoff` used UTC, so for a UTC+8 user "today" lagged 8 hours and
excluded the current local day's rows.
"""
import datetime as _dt

import views


class _FakeDateTime(_dt.datetime):
    # UTC is still 2026-09-28 while local (UTC+8) is already 2026-09-29.
    _fixed_utc = _dt.datetime(2026, 9, 28, 17, 5, tzinfo=_dt.timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed_utc.astimezone(tz) if tz else cls._fixed_utc

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        return _dt.datetime.fromtimestamp(ts, tz)


def _engine(local_tz):
    return views.ViewEngine(views.DEFAULT_FORMULA, norm=lambda s: s,
                            is_free=lambda m: False, local_tz=local_tz)


def test_cutoff_uses_local_timezone(monkeypatch):
    monkeypatch.setattr(views, "datetime", _FakeDateTime)
    tz = _dt.timezone(_dt.timedelta(hours=8))
    eng = _engine(tz)
    assert eng._cutoff("today") == "2026-09-29"
    assert eng._cutoff("7d") == "2026-09-23"
    assert eng._cutoff("all") is None


def test_day_and_cutoff_agree_on_timezone(monkeypatch):
    monkeypatch.setattr(views, "datetime", _FakeDateTime)
    tz = _dt.timezone(_dt.timedelta(hours=8))
    eng = _engine(tz)
    # a row at 2026-09-29 00:30 local must be inside the "today" cutoff
    import time as _time
    ts = int(_dt.datetime(2026, 9, 29, 0, 30, tzinfo=tz).timestamp() * 1000)
    cutoff = eng._cutoff("today")
    assert eng._day(ts) >= cutoff
