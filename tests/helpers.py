"""Test helpers: row builders, disposable SQLite fixture builders, HTTP helpers.

Everything here is stdlib-only. Builders create data in the test's own tmp
directory; never point them at real user databases.
"""
import json
import sqlite3
import urllib.request


# --------------------------------------------------------------------------
# row builders (the internal row shape used by go-usage-widget.py)
# --------------------------------------------------------------------------
def default_tokens(input_=100, output_=50, cache_read=0, cache_write=0, reasoning=0):
    return {
        "input": input_,
        "output": output_,
        "cache": {"read": cache_read, "write": cache_write},
        "reasoning": reasoning,
    }


def make_row(ts, model, cost=0.0, src="go", tokens=None, account=None, **extra):
    row = {
        "ts": int(ts),
        "cost": float(cost),
        "model": model,
        "tokens": tokens if tokens is not None else default_tokens(),
        "src": src,
    }
    if account is not None:
        row["account"] = account
    row.update(extra)
    return row


def make_remote_row(ts, model, cost=0.0, account="own"):
    """Row shape returned by ur.read_remote_rows() (official sync)."""
    return {
        "ts": int(ts),
        "cost": float(cost),
        "model": model,
        "tokens": default_tokens(),
        "src": "go",
        "key_id": "",
        "account": account,
    }


# --------------------------------------------------------------------------
# OpenCode DB fixture builders
# --------------------------------------------------------------------------
def legacy_assistant_message(
    created,
    model_id="glm-5.2",
    provider_id="opencode-go",
    cost=1.0,
    completed=None,
    tokens=None,
    role="assistant",
):
    """One row in the LEGACY opencode.db `message` table (data JSON column).

    This is the schema the current production reader (gw.read_opencode_all)
    targets: role/providerID/modelID/cost/tokens/time at top level of `data`.
    """
    msg = {
        "role": role,
        "providerID": provider_id,
        "modelID": model_id,
        "cost": cost,
        "time": {"created": int(created)},
        "tokens": tokens if tokens is not None else default_tokens(),
    }
    if completed is not None:
        msg["time"]["completed"] = int(completed)
    return msg


def build_legacy_opencode_db(path, messages):
    """Create a disposable opencode.db with the legacy `message` table."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS message (id TEXT PRIMARY KEY, data TEXT)")
        for i, msg in enumerate(messages):
            con.execute(
                "INSERT OR REPLACE INTO message (id, data) VALUES (?, ?)",
                (f"msg_{i}", json.dumps(msg)),
            )
        con.commit()
    finally:
        con.close()
    return str(path)


def build_current_opencode_db(path, messages):
    """Create a disposable opencode.db with the CURRENT schema subset
    (session_message + session_v2) that includes per-message `agent`.

    messages: list of dicts accepted by current_message().
    """
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS session_v2 ("
            " id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT,"
            " cost REAL, tokens_input INTEGER, tokens_output INTEGER,"
            " tokens_cache_read INTEGER, tokens_cache_write INTEGER)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS session_message ("
            " id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,"
            " time_created INTEGER, time_updated INTEGER, data TEXT)"
        )
        for i, msg in enumerate(messages):
            sid = msg.get("session_id", "ses_1")
            con.execute(
                "INSERT OR REPLACE INTO session_message (id, session_id, type, seq, time_created, time_updated, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    msg.get("id", f"sm_{i}"),
                    sid,
                    msg.get("type", "assistant"),
                    i,
                    msg.get("time_created", 0),
                    msg.get("time_updated", 0),
                    json.dumps(msg.get("data", {})),
                ),
            )
        con.commit()
    finally:
        con.close()
    return str(path)


def current_assistant_message(
    created, agent="build", model_id="deepseek-v4.1-flash", provider_id="opencode-go", cost=1.0
):
    """One session_message row's `data` payload in the current schema."""
    return {
        "time": {"created": int(created), "completed": int(created) + 1000},
        "agent": agent,
        "model": {"id": model_id, "providerID": provider_id, "variant": "high"},
        "content": [],
        "cost": cost,
        "tokens": default_tokens(),
    }


# --------------------------------------------------------------------------
# HTTP helpers (only against the ephemeral test server)
# --------------------------------------------------------------------------
def auth_headers(token, extra=None):
    """Phase 2A: build the Bearer auth header for the hardened localhost API."""
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


def http_get(url, timeout=10, headers=None):
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def http_post_json(url, payload, timeout=10, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, method="POST", headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def http_get_json(url, timeout=10, headers=None):
    status, body = http_get(url, timeout=timeout, headers=headers)
    return status, json.loads(body)


def http_post_json_response(url, payload, timeout=10, headers=None):
    status, body = http_post_json(url, payload, timeout=timeout, headers=headers)
    return status, json.loads(body)
