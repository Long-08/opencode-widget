# Phase 3 — Agent Observability Backend

> Plugin-independent observability over OpenCode runtime data.
> Status: see `docs/ROADMAP.md`. Oh My OpenCode Slim integration is **CANCELLED** —
> core observability depends only on the local OpenCode database.

## 1. Data layers

### 1.1 Fact layer (never rewritten)

Raw values recorded by OpenCode, carried on every normalized row:

```
timestamp (ts)        session_id           parent_session_id
agent                 provider_id          model_id              variant
tokens.input          tokens.output        tokens.cache.read     tokens.cache.write
cost                  dur_ms
```

- The fact layer is never renamed, remapped or overwritten because of UI needs or user
  group configuration.
- `agent = "explorer"` stays `explorer` forever; arbitrary future names such as
  `foo-agent-2027` are preserved verbatim.
- Historical records created while an uninstalled plugin (e.g. Slim) was active are real
  data and are never filtered, renamed or deleted.

### 1.2 Normalized layer

Existing logic may map provider → source (`opencode-go` → `go`) and normalize model
identifiers for display. The raw `provider_id` and `model_id`/`model` always remain on
each record; aggregation groups by the raw identity.

### 1.3 Display layer

User-configurable, display-only:

```json
{ "agent_groups": { "orchestrator": "Main", "explorer": "Research", "fixer": "Worker" } }
```

The API always returns the raw agent alongside its group:

```json
{ "agent": "explorer", "group": "Research" }
```

It never returns only `"Research"`.

## 2. Agent identity

Source order: `session_message.data.agent` → `session_v2.agent` → `"unknown"`.
There is **no allowlist**; any observed name enters the statistics automatically.

## 3. Unknown / unmapped semantics

- No agent data at all → `agent = "unknown"`.
- Agent exists but has no configured group → `group = null` (JSON `null`).
  `"unmapped"` is intentionally **not** used as a fact value; a UI may render null as
  "Unmapped" later.

## 4. Cost basis

- Every observability endpoint declares `cost_basis: "opencode_message_raw"`.
- `cost` is the sum of raw OpenCode message costs.
- **No** meter ratio (1.4212), credit deduction, subscription adjustment or official
  quota conversion is applied. Billing/quota numbers elsewhere are unaffected.

## 5. Agent aggregation (`GET /api/agents`)

Per agent:

```json
{
  "agent": "explorer", "group": null,
  "requests": 0, "sessions": 0, "cost": 0.0,
  "tokens": {"total": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
  "cache_read_ratio": null,
  "duration": {"total_ms": 0, "avg_ms": null},
  "avg_tokens_per_request": null,
  "avg_cost_per_request": null,
  "avg_duration_per_request": null,
  "models": [ ... ], "providers": [ ... ]
}
```

- `requests` = row count; `sessions` = distinct non-null `session_id` count.
- `duration.total_ms` sums non-null `dur_ms`; `duration.avg_ms` averages rows that carry
  `dur_ms` (none → `null`).
- Agents are sorted by `cost` desc.

### 5.1 Agent → Model

`models[]` entries:

```json
{ "model": "...", "provider": "<provider_id>", "variant": "...",
  "requests": 0, "cost": 0.0,
  "tokens": {"total":0,"input":0,"output":0,"cache_read":0,"cache_write":0},
  "cache_read_ratio": null,
  "request_share": 0.0, "token_share": 0.0, "cost_share": 0.0 }
```

Shares are floats in `0.0–1.0` relative to the agent's totals; a zero denominator yields
`0.0`. No percentage strings.

## 6. Model aggregation (`GET /api/models`)

Per model identity (`model`, `provider_id`, `source`, `variant`):

```json
{ "model": "...", "provider_id": "opencode-go", "source": "go", "variant": "high",
  "requests": 0, "sessions": 0, "cost": 0.0,
  "tokens": {...}, "cache_read_ratio": null,
  "agents": [ {"agent": "...", "group": null, "requests": 0, "tokens": {...}, "cost": 0.0, "cache_read_ratio": null} ] }
```

`agents[]` answers "which agents actually used this model"; sorted by cost desc.

## 7. Provider aggregation (`GET /api/providers`)

Provider identity is explicit: raw `provider_id`, mapped `source`, and `display_name`
(from the supplier-name map, falling back to the source). Providers are never guessed from
model names.

```json
{ "provider_id": "opencode-go", "source": "go", "display_name": "OpenCode Go",
  "requests": 0, "sessions": 0, "cost": 0.0, "tokens": {...}, "cache_read_ratio": null,
  "agents": [ {"agent": "...", "group": null, "requests": 0, "cost": 0.0, "tokens": {...}} ],
  "models": [ {"model": "...", "variant": "...", "requests": 0, "cost": 0.0, "tokens": {...}} ] }
```

## 8. Session aggregation (`GET /api/sessions`)

Each session reports usage metadata only:

```json
{ "session_id": "ses_...", "parent_session_id": "ses_..." | null,
  "start_ts": 0, "end_ts": 0, "duration_ms": 0,
  "requests": 0, "cost": 0.0, "tokens": {...},
  "agents": [ ... ], "models": [ ... ], "providers": [ ... ] }
```

- `start_ts`/`end_ts` are the min/max row timestamps; `duration_ms = end_ts - start_ts`.
- Sessions are sorted by `start_ts` desc.
- Rows without `session_id` are excluded from session aggregation (they still count for
  agent/model/provider aggregation).
- **Never returned:** prompt/response text, tool arguments, message content, reasoning,
  `title`, or local file paths. This project is not a chat browser.

### 8.1 Parent/child semantics

The endpoint also returns `tree`:

```json
{ "nodes": [ {"session_id":"...", "parent_session_id": null, "depth": 0, "children_count": 2, "is_root": true} ],
  "roots": ["ses_..."],
  "diagnostics": {"nodes": 0, "missing_parent": 0, "self_parent": 0, "cycles": 0, "max_depth": 0} }
```

- Arbitrary depth is supported (root → child → grandchild …).
- Defensive handling: missing parent (counted; treated as a root), self-parent (counted;
  depth 0), cycles (counted; traversal breaks instead of looping or crashing).
- **Tree ≠ usage roll-up.** Session metrics count only that session's own rows; parents
  never absorb descendants. Any future subtree totals must use explicitly different field
  names (not implemented in Phase 3).

## 9. Cache ratio and derived metrics

- `cache_read_ratio = cache_read / (input + cache_read)`; denominator 0 → `null`
  (never `cache_read / total_tokens`).
- `avg_tokens_per_request = tokens.total / requests`
- `avg_cost_per_request = cost / requests`
- `avg_duration_per_request` = mean of `dur_ms`
- Any zero denominator (or no data) → `null`. `NaN`/`Infinity` are never emitted
  (verified with `json.dumps(..., allow_nan=False)`).
- No subjective scores (performance/quality/efficiency/ranking) exist by design.

## 10. Range semantics

All observability endpoints accept `?range=today|7d|30d|all` (invalid → `all`) and reuse
the existing view cutoff mechanism (`ViewEngine._cutoff`, including the subscription-period
anchor for `30d`). No endpoint implements its own date boundaries. Rows are read from the
OpenCode DB once per request and then aggregated.

## 11. API contracts

Every observability endpoint returns the same metadata envelope:

```json
{ "reader": {"schema": "current", "status": "ok", "row_count": 0, "skipped_rows": 0, "error": null},
  "range": "all",
  "cost_basis": "opencode_message_raw",
  "<agents|models|providers|sessions>": [] }
```

`/api/sessions` additionally returns `tree`.

## 12. Security boundary

- `/api/agents`, `/api/models`, `/api/providers`, `/api/sessions` all pass through the
  Phase 2.1 runtime-token bridge (Host allowlist + Origin policy + `Authorization: Bearer`).
- Electron exposes them only as `widgetAPI.apiGetAgents/apiGetModels/apiGetProviders/
  apiGetSessions(range)`; the renderer never receives the base URL or token, and the main
  process validates method/path against a strict allowlist.
- No message content can reach the renderer because the backend never selects it.

## 13. Known limitations

1. `gw.agent_stats()` (Phase 2C) remains in `go-usage-widget.py` for its existing tests; the
   endpoints now use `observability.py`. It is superseded and may be removed in a later
   cleanup.
2. Sessions without any usage rows (e.g. opened and never used) do not appear.
3. Legacy-schema rows have no `session_id`/`agent`, so session aggregation excludes them
   while agent/model/provider aggregation buckets them under `unknown`.
4. `duration` depends on `dur_ms` being present in the OpenCode records; missing values are
   excluded from averages rather than treated as zero.
5. No subtree roll-up totals and no forecasting/UI (Phases 4–5).

## 14. Verification (Phase 3)

- Suite: **224 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`, TEMP redirected).
  Phase 3 added 48 tests (33 pure-aggregation + 15 endpoint), covering the semantics above and
  cross-aggregation invariants.
- Server smoke (real OpenCode DB, read-only, port 8799): 15/15 PASS — reader `current`
  (1285 rows), `/api/agents` 9, `/api/models` 9, `/api/providers` 1, `/api/sessions` 39 with
  `tree` (39 nodes; diagnostics `missing_parent=self_parent=cycles=0`, `max_depth=1`);
  no content keys in any response; no secret fields; Host/Origin/token checks pass.
- Electron bridge smoke (CDP): `apiEnv` absent; `apiGetState/apiGetAgents/apiGetModels/
  apiGetProviders/apiGetSessions` reachable; every endpoint response carries `cost_basis`;
  `tree` present; `forbiddenHits: []`; **renderer token-leak scan `leaks: []`**.
- Timing (informational, uncached, 1285 normalized rows, no SLA): agents ≈223 ms,
  models ≈216 ms, providers ≈215 ms, sessions ≈195 ms; cached `/api/state` ≈13 ms.
  One DB read per request (single-read principle), no repeated scans.
- Not exercised: no real model calls; no prompt/response/secret values were read, printed,
  or stored during verification.
