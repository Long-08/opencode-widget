#!/usr/bin/env python3
"""Agent Observability backend: pure, plugin-independent aggregations.

Everything in this module is a pure function over already-normalized rows from
``gw.read_opencode_usage()`` (and only that row shape). No DB access, no
network, no global mutation. Callers (``data_server.py``) own the single read
and pass filtered rows in.

Shared semantics
----------------
* Agent identity is ``row.get("agent") or "unknown"``; arbitrary names are
  preserved verbatim (there is no allowlist and no historical renaming).
* ``group`` is ``agent_groups.get(agent)`` when ``agent_groups`` is configured,
  else ``None`` (JSON null). The literal ``"unmapped"`` is never emitted.
* Token buckets are ``{"total","input","output","cache_read","cache_write"}``
  with ``total = input + output + cache_read + cache_write``.
* ``cache_read_ratio = cache_read / (input + cache_read)``; a zero denominator
  yields ``None`` (never ``cache_read / total``).
* Averages with a zero denominator yield ``None``; shares with a zero
  denominator yield ``0.0``. No NaN/Infinity ever escapes.
* No implicit session roll-up: a child session's rows belong only to the child.
"""
from datetime import datetime

COST_BASIS = "opencode_message_raw"

_UNKNOWN_AGENT = "unknown"
_UNKNOWN_MODEL = "?"


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------
def _token_parts(row):
    """Extract (input, output, cache_read, cache_write) from a row's tokens."""
    tk = row.get("tokens")
    if not isinstance(tk, dict):
        tk = {}
    cache = tk.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    return (
        tk.get("input") or 0,
        tk.get("output") or 0,
        cache.get("read") or 0,
        cache.get("write") or 0,
    )


def _tokens_obj(inp, out, cache_read, cache_write):
    return {
        "total": inp + out + cache_read + cache_write,
        "input": inp,
        "output": out,
        "cache_read": cache_read,
        "cache_write": cache_write,
    }


def _ratio(cache_read, inp):
    """cache_read / (input + cache_read); zero denominator -> None."""
    denom = cache_read + inp
    if not denom:
        return None
    return cache_read / denom


def _avg(total, n):
    """total / n; zero denominator -> None (never NaN/Infinity)."""
    if not n:
        return None
    return total / n


def _share(part, whole):
    """part / whole as a 0.0-1.0 float; zero denominator -> 0.0."""
    if not whole:
        return 0.0
    return part / whole


def _agent_of(row):
    return row.get("agent") or _UNKNOWN_AGENT


def _group_of(agent, agent_groups):
    if not agent_groups:
        return None
    return agent_groups.get(agent)


def _local_day(ts, tz):
    return datetime.fromtimestamp(ts / 1000, tz).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# filter
# --------------------------------------------------------------------------
def filter_rows(rows, cutoff, tz):
    """Keep rows whose local day >= ``cutoff``.

    ``cutoff`` is a local-tz ``YYYY-MM-DD`` string or None. Mirrors the exact
    day-boundary semantics used by ``views.ViewEngine`` (a malformed day string
    does not pass the cutoff). Returns a new list; input is not mutated.
    """
    if cutoff is None:
        return list(rows)
    out = []
    for r in rows:
        day = _local_day(r["ts"], tz)
        if len(day) != 10 or day < cutoff:
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------
def aggregate_agents(rows, agent_groups=None):
    """Aggregate rows per agent, with nested model and provider breakdowns."""
    buckets = {}
    for r in rows:
        agent = _agent_of(r)
        b = buckets.get(agent)
        if b is None:
            b = buckets[agent] = {
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "dur_sum": 0,
                "dur_n": 0,
                "sessions": set(),
                "models": {},
                "providers": {},
            }
        inp, out, cr, cw = _token_parts(r)
        cost = r.get("cost") or 0.0
        b["requests"] += 1
        b["cost"] += cost
        b["input"] += inp
        b["output"] += out
        b["cache_read"] += cr
        b["cache_write"] += cw
        dur = r.get("dur_ms")
        if dur is not None:
            b["dur_sum"] += dur
            b["dur_n"] += 1
        sid = r.get("session_id")
        if sid is not None:
            b["sessions"].add(sid)

        model_id = r.get("model") or _UNKNOWN_MODEL
        mkey = (model_id, r.get("provider_id"), r.get("variant"))
        m = b["models"].get(mkey)
        if m is None:
            m = b["models"][mkey] = {
                "model": model_id,
                "provider": r.get("provider_id"),
                "variant": r.get("variant"),
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
            }
        m["requests"] += 1
        m["cost"] += cost
        m["input"] += inp
        m["output"] += out
        m["cache_read"] += cr
        m["cache_write"] += cw

        pkey = (r.get("provider_id"), r.get("src"))
        p = b["providers"].get(pkey)
        if p is None:
            p = b["providers"][pkey] = {
                "provider_id": r.get("provider_id"),
                "source": r.get("src"),
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
            }
        p["requests"] += 1
        p["cost"] += cost
        p["input"] += inp
        p["output"] += out
        p["cache_read"] += cr
        p["cache_write"] += cw

    out = []
    for agent, b in buckets.items():
        tok = _tokens_obj(b["input"], b["output"], b["cache_read"], b["cache_write"])
        requests = b["requests"]
        dur_avg = _avg(b["dur_sum"], b["dur_n"])

        models = []
        for m in b["models"].values():
            mt = _tokens_obj(m["input"], m["output"], m["cache_read"], m["cache_write"])
            models.append({
                "model": m["model"],
                "provider": m["provider"],
                "variant": m["variant"],
                "requests": m["requests"],
                "cost": m["cost"],
                "tokens": mt,
                "cache_read_ratio": _ratio(m["cache_read"], m["input"]),
                "request_share": _share(m["requests"], requests),
                "token_share": _share(mt["total"], tok["total"]),
                "cost_share": _share(m["cost"], b["cost"]),
            })
        models.sort(key=lambda x: x["cost"], reverse=True)

        providers = []
        for p in b["providers"].values():
            pt = _tokens_obj(p["input"], p["output"], p["cache_read"], p["cache_write"])
            providers.append({
                "provider_id": p["provider_id"],
                "source": p["source"],
                "requests": p["requests"],
                "cost": p["cost"],
                "tokens": pt,
                "cache_read_ratio": _ratio(p["cache_read"], p["input"]),
            })
        providers.sort(key=lambda x: x["cost"], reverse=True)

        out.append({
            "agent": agent,
            "group": _group_of(agent, agent_groups),
            "requests": requests,
            "sessions": len(b["sessions"]),
            "cost": b["cost"],
            "tokens": tok,
            "cache_read_ratio": _ratio(b["cache_read"], b["input"]),
            "duration": {"total_ms": b["dur_sum"], "avg_ms": dur_avg},
            "avg_tokens_per_request": _avg(tok["total"], requests),
            "avg_cost_per_request": _avg(b["cost"], requests),
            "avg_duration_per_request": dur_avg,
            "models": models,
            "providers": providers,
        })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
def aggregate_models(rows, agent_groups=None):
    """Aggregate rows per (model, provider_id, source, variant)."""
    buckets = {}
    for r in rows:
        model_id = r.get("model") or _UNKNOWN_MODEL
        key = (model_id, r.get("provider_id"), r.get("src"), r.get("variant"))
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "model": model_id,
                "provider_id": r.get("provider_id"),
                "source": r.get("src"),
                "variant": r.get("variant"),
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "sessions": set(),
                "agents": {},
            }
        inp, out, cr, cw = _token_parts(r)
        cost = r.get("cost") or 0.0
        b["requests"] += 1
        b["cost"] += cost
        b["input"] += inp
        b["output"] += out
        b["cache_read"] += cr
        b["cache_write"] += cw
        sid = r.get("session_id")
        if sid is not None:
            b["sessions"].add(sid)

        agent = _agent_of(r)
        a = b["agents"].get(agent)
        if a is None:
            a = b["agents"][agent] = {
                "agent": agent,
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
            }
        a["requests"] += 1
        a["cost"] += cost
        a["input"] += inp
        a["output"] += out
        a["cache_read"] += cr
        a["cache_write"] += cw

    out = []
    for b in buckets.values():
        tok = _tokens_obj(b["input"], b["output"], b["cache_read"], b["cache_write"])
        agents = []
        for a in b["agents"].values():
            at = _tokens_obj(a["input"], a["output"], a["cache_read"], a["cache_write"])
            agents.append({
                "agent": a["agent"],
                "group": _group_of(a["agent"], agent_groups),
                "requests": a["requests"],
                "tokens": at,
                "cost": a["cost"],
                "cache_read_ratio": _ratio(a["cache_read"], a["input"]),
            })
        agents.sort(key=lambda x: x["cost"], reverse=True)
        out.append({
            "model": b["model"],
            "provider_id": b["provider_id"],
            "source": b["source"],
            "variant": b["variant"],
            "requests": b["requests"],
            "sessions": len(b["sessions"]),
            "cost": b["cost"],
            "tokens": tok,
            "cache_read_ratio": _ratio(b["cache_read"], b["input"]),
            "agents": agents,
        })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
def aggregate_providers(rows, agent_groups=None, supplier_names=None):
    """Aggregate rows per (provider_id, source)."""
    supplier_names = supplier_names or {}
    buckets = {}
    for r in rows:
        key = (r.get("provider_id"), r.get("src"))
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "provider_id": r.get("provider_id"),
                "source": r.get("src"),
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "sessions": set(),
                "agents": {},
                "models": {},
            }
        inp, out, cr, cw = _token_parts(r)
        cost = r.get("cost") or 0.0
        b["requests"] += 1
        b["cost"] += cost
        b["input"] += inp
        b["output"] += out
        b["cache_read"] += cr
        b["cache_write"] += cw
        sid = r.get("session_id")
        if sid is not None:
            b["sessions"].add(sid)

        agent = _agent_of(r)
        a = b["agents"].get(agent)
        if a is None:
            a = b["agents"][agent] = {
                "agent": agent,
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
            }
        a["requests"] += 1
        a["cost"] += cost
        a["input"] += inp
        a["output"] += out
        a["cache_read"] += cr
        a["cache_write"] += cw

        model_id = r.get("model") or _UNKNOWN_MODEL
        mkey = (model_id, r.get("variant"))
        m = b["models"].get(mkey)
        if m is None:
            m = b["models"][mkey] = {
                "model": model_id,
                "variant": r.get("variant"),
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
            }
        m["requests"] += 1
        m["cost"] += cost
        m["input"] += inp
        m["output"] += out
        m["cache_read"] += cr
        m["cache_write"] += cw

    out = []
    for b in buckets.values():
        tok = _tokens_obj(b["input"], b["output"], b["cache_read"], b["cache_write"])
        agents = []
        for a in b["agents"].values():
            agents.append({
                "agent": a["agent"],
                "group": _group_of(a["agent"], agent_groups),
                "requests": a["requests"],
                "cost": a["cost"],
                "tokens": _tokens_obj(a["input"], a["output"], a["cache_read"], a["cache_write"]),
            })
        agents.sort(key=lambda x: x["cost"], reverse=True)
        models = []
        for m in b["models"].values():
            models.append({
                "model": m["model"],
                "variant": m["variant"],
                "requests": m["requests"],
                "cost": m["cost"],
                "tokens": _tokens_obj(m["input"], m["output"], m["cache_read"], m["cache_write"]),
            })
        models.sort(key=lambda x: x["cost"], reverse=True)
        out.append({
            "provider_id": b["provider_id"],
            "source": b["source"],
            "display_name": supplier_names.get(b["source"], b["source"]),
            "requests": b["requests"],
            "sessions": len(b["sessions"]),
            "cost": b["cost"],
            "tokens": tok,
            "cache_read_ratio": _ratio(b["cache_read"], b["input"]),
            "agents": agents,
            "models": models,
        })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
def aggregate_sessions(rows, agent_groups=None):
    """Aggregate rows per session_id (own rows only; no descendant roll-up).

    Rows without a ``session_id`` are skipped — they cannot be attributed to a
    session (other aggregations still count them).
    """
    buckets = {}
    for r in rows:
        sid = r.get("session_id")
        if sid is None:
            continue
        b = buckets.get(sid)
        if b is None:
            b = buckets[sid] = {
                "session_id": sid,
                "parent_session_id": r.get("parent_session_id"),
                "start_ts": r.get("ts"),
                "end_ts": r.get("ts"),
                "requests": 0,
                "cost": 0.0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "agents": {},
                "models": {},
                "providers": {},
            }
        ts = r.get("ts")
        if ts is not None:
            if b["start_ts"] is None or ts < b["start_ts"]:
                b["start_ts"] = ts
            if b["end_ts"] is None or ts > b["end_ts"]:
                b["end_ts"] = ts
        if b["parent_session_id"] is None and r.get("parent_session_id") is not None:
            b["parent_session_id"] = r.get("parent_session_id")
        inp, out, cr, cw = _token_parts(r)
        cost = r.get("cost") or 0.0
        b["requests"] += 1
        b["cost"] += cost
        b["input"] += inp
        b["output"] += out
        b["cache_read"] += cr
        b["cache_write"] += cw

        agent = _agent_of(r)
        a = b["agents"].get(agent)
        if a is None:
            a = b["agents"][agent] = {
                "agent": agent, "requests": 0, "cost": 0.0,
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            }
        a["requests"] += 1
        a["cost"] += cost
        a["input"] += inp
        a["output"] += out
        a["cache_read"] += cr
        a["cache_write"] += cw

        model_id = r.get("model") or _UNKNOWN_MODEL
        mkey = (model_id, r.get("provider_id"), r.get("variant"))
        m = b["models"].get(mkey)
        if m is None:
            m = b["models"][mkey] = {
                "model": model_id, "provider_id": r.get("provider_id"),
                "variant": r.get("variant"), "requests": 0, "cost": 0.0,
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            }
        m["requests"] += 1
        m["cost"] += cost
        m["input"] += inp
        m["output"] += out
        m["cache_read"] += cr
        m["cache_write"] += cw

        pkey = (r.get("provider_id"), r.get("src"))
        p = b["providers"].get(pkey)
        if p is None:
            p = b["providers"][pkey] = {
                "provider_id": r.get("provider_id"), "source": r.get("src"),
                "requests": 0, "cost": 0.0,
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            }
        p["requests"] += 1
        p["cost"] += cost
        p["input"] += inp
        p["output"] += out
        p["cache_read"] += cr
        p["cache_write"] += cw

    out = []
    for b in buckets.values():
        agents = []
        for a in b["agents"].values():
            agents.append({
                "agent": a["agent"],
                "group": _group_of(a["agent"], agent_groups),
                "requests": a["requests"],
                "cost": a["cost"],
                "tokens": _tokens_obj(a["input"], a["output"], a["cache_read"], a["cache_write"]),
            })
        agents.sort(key=lambda x: x["cost"], reverse=True)
        models = []
        for m in b["models"].values():
            models.append({
                "model": m["model"],
                "provider_id": m["provider_id"],
                "variant": m["variant"],
                "requests": m["requests"],
                "cost": m["cost"],
                "tokens": _tokens_obj(m["input"], m["output"], m["cache_read"], m["cache_write"]),
            })
        models.sort(key=lambda x: x["cost"], reverse=True)
        providers = []
        for p in b["providers"].values():
            providers.append({
                "provider_id": p["provider_id"],
                "source": p["source"],
                "requests": p["requests"],
                "cost": p["cost"],
                "tokens": _tokens_obj(p["input"], p["output"], p["cache_read"], p["cache_write"]),
            })
        providers.sort(key=lambda x: x["cost"], reverse=True)
        start_ts = b["start_ts"]
        end_ts = b["end_ts"]
        duration_ms = 0
        if start_ts is not None and end_ts is not None:
            duration_ms = max(0, end_ts - start_ts)
        out.append({
            "session_id": b["session_id"],
            "parent_session_id": b["parent_session_id"],
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_ms": duration_ms,
            "requests": b["requests"],
            "cost": b["cost"],
            "tokens": _tokens_obj(b["input"], b["output"], b["cache_read"], b["cache_write"]),
            "agents": agents,
            "models": models,
            "providers": providers,
        })
    out.sort(key=lambda x: (x["start_ts"] if x["start_ts"] is not None else 0), reverse=True)
    return out


# --------------------------------------------------------------------------
# session tree
# --------------------------------------------------------------------------
def build_session_tree(rows):
    """Build the session parent/child tree from rows.

    Defends against missing parents (root), self-parent (root, depth 0) and
    cycles (count once, break, never loop). Depth is memoised; each node is
    resolved with a per-path visited set, so this is O(n).
    """
    parent = {}
    order = []
    for r in rows:
        sid = r.get("session_id")
        if sid is None:
            continue
        if sid not in parent:
            parent[sid] = r.get("parent_session_id")
            order.append(sid)
        elif parent[sid] is None and r.get("parent_session_id") is not None:
            parent[sid] = r.get("parent_session_id")

    depth = {}
    missing_parent = 0
    self_parent = 0
    cycles = 0

    for sid in order:
        if sid in depth:
            continue
        chain = []
        visited = set()
        cur = sid
        base = 0
        while True:
            if cur in depth:
                # cur is already resolved; chain[-1] sits one level below it
                base = depth[cur] + 1
                break
            if cur in visited:
                cycles += 1
                base = 0
                break
            visited.add(cur)
            chain.append(cur)
            p = parent.get(cur)
            if p is None:
                base = 0
                break
            if p == cur:
                self_parent += 1
                base = 0
                break
            if p not in parent:
                missing_parent += 1
                base = 0
                break
            cur = p
        # base is the depth of chain[-1]; walk back up the chain.
        for i in range(len(chain) - 1, -1, -1):
            if i == len(chain) - 1:
                depth[chain[i]] = base
            else:
                depth[chain[i]] = depth[chain[i + 1]] + 1

    children_count = {sid: 0 for sid in parent}
    for sid in parent:
        p = parent.get(sid)
        if p is not None and p != sid and p in children_count:
            children_count[p] += 1

    nodes = []
    roots = []
    for sid in order:
        d = depth.get(sid, 0)
        is_root = d == 0
        if is_root:
            roots.append(sid)
        nodes.append({
            "session_id": sid,
            "parent_session_id": parent.get(sid),
            "depth": d,
            "children_count": children_count.get(sid, 0),
            "is_root": is_root,
        })

    max_depth = max(depth.values()) if depth else 0
    diagnostics = {
        "nodes": len(order),
        "missing_parent": missing_parent,
        "self_parent": self_parent,
        "cycles": cycles,
        "max_depth": max_depth,
    }
    return {"nodes": nodes, "roots": roots, "diagnostics": diagnostics}
