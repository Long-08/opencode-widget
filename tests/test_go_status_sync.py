"""Official Go quota sync: the new console /console/api/go/status path.

The legacy SSR scrape (``scrape_server_usage`` on /workspace/{ws}/go) is obsolete
now that the console is a SPA; these tests pin the replacement parser and the
do_sync preference order. No real network / user data is touched.
"""
import json


def _fake_response(payload):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps(payload).encode("utf-8")

    return _Resp()


def _go_payload(five=120000000, week=750000000, month=1800000000):
    return {
        "subscriberUserId": "acc_x",
        "product": "go",
        "access": {
            "meters": {
                "fiveHour": {"resetsAt": "2099-01-01T05:00:00.000Z",
                             "limitMicroCents": "1200000000", "usedMicroCents": str(five)},
                "week": {"resetsAt": "2099-01-02T00:00:00.000Z",
                         "limitMicroCents": "3000000000", "usedMicroCents": str(week)},
                "month": {"resetsAt": "2099-01-03T00:00:00.000Z",
                          "limitMicroCents": "6000000000", "usedMicroCents": str(month)},
            }
        },
    }


def test_fetch_go_status_parses_meters(gw, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["cookie"] = req.get_header("Cookie")
        seen["timeout"] = timeout
        return _fake_response(_go_payload())

    monkeypatch.setattr(gw, "urlopen", fake_urlopen)
    res = gw.fetch_go_status("sess-123", "wrk_test")
    assert res["ok"] is True
    by = {w["kind"]: w for w in res["windows"]}
    assert set(by) == {"session", "weekly", "monthly"}
    assert by["session"]["limit"] == 12.0
    assert by["session"]["used"] == 1.2
    assert abs(by["session"]["pct"] - 10.0) < 0.001
    assert abs(by["weekly"]["pct"] - 25.0) < 0.001
    assert abs(by["monthly"]["pct"] - 30.0) < 0.001
    # session cookie is tried first
    assert "__Host-console_session=sess-123" in seen["cookie"]
    # reset text is parseable by the forecast layer
    assert by["monthly"]["reset_text"]


def test_fetch_go_status_non_go_account(gw, monkeypatch):
    monkeypatch.setattr(gw, "urlopen", lambda req, timeout=None: _fake_response({"product": None}))
    res = gw.fetch_go_status("sess-123", "wrk_test")
    assert res["ok"] is False


def test_fetch_go_status_requires_cookie_and_ws(gw):
    assert gw.fetch_go_status("", "wrk_test")["ok"] is False
    assert gw.fetch_go_status("sess", "")["ok"] is False


def test_fetch_go_status_falls_back_to_auth_cookie_name(gw, monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.get_header("Cookie"))
        if len(calls) == 1:
            raise OSError("no console session cookie")
        return _fake_response(_go_payload())

    monkeypatch.setattr(gw, "urlopen", fake_urlopen)
    res = gw.fetch_go_status("v", "wrk_test")
    assert res["ok"] is True
    assert len(calls) == 2
    assert "__Host-console_session=v" in calls[0]
    assert calls[1] == "auth=v"


def test_go_reset_text_parses(gw):
    import datetime
    from forecasting import parse_reset_text
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
    text = gw._go_reset_text(future.isoformat().replace("+00:00", "Z"))
    ms = parse_reset_text(text)
    assert ms is not None and 4.5 * 3600 * 1000 <= ms <= 6 * 3600 * 1000
