# Phase 5B — Advanced Visualization

> Three decision-useful visualizations on top of already-verified APIs — Usage Timeline,
> Agent × Model matrix, and compact Forecast/quota visuals. No business semantics are
> recomputed in the renderer; no charts framework, no new notification behavior.

## 1. Scope

In: `/api/timeline` aggregation + endpoint, Overview Usage Timeline, Agents Agent × Model matrix,
Forecast compact bars, metric switches, filters, error/empty states, accessibility basics.

Out: Sankey / network / force-directed / 3D / session graph / radar / gauges, forecast history,
official-quota-history claims, chart libraries, complex settings, notifications changes.

## 2. Why these visualizations

They answer three concrete questions the tables alone answer poorly:
*When* did usage happen (timeline buckets), *which agents actually use which models* (matrix
intensity), and *how close is each quota window to its limit vs the estimate* (compact bars).

## 3. Timeline API

`GET /api/timeline?range=today|7d|30d|all` (authenticated; invalid range → `all`):

```json
{ "reader": {"schema":"current","status":"ok","row_count":0,"skipped_rows":0,"error":null},
  "range": "7d", "cost_basis": "opencode_message_raw",
  "bucket": {"unit":"hour","seconds":10800},
  "series": [{"start":0,"end":0,"requests":0,
              "tokens":{"total":0,"input":0,"output":0,"cache_read":0,"cache_write":0},
              "cost":0.0}] }
```

`timeline.py` is pure (`COST_BASIS`, `NICE_SIZES`, `MAX_BUCKETS=200`, `bucket_plan`,
`build_buckets`, `aggregate_timeline`). The endpoint reuses the single-read observability
context (one `read_opencode_usage()` per request) and the existing formula-engine
range cutoff (`_cutoff` + `observability.filter_rows` with `LOCAL_TZ`) — no new "today"
definition, no per-bucket DB queries.

## 4. Bucket semantics

- Boundaries are epoch-ms, half-open `[start, end)`; the last bucket is truncated at `now`.
- Base sizes: today → 30 min, 7d → 3 h, 30d → 1 day; `all` → auto (ladder start).
- A named `NICE_SIZES` ladder (30 m → 365 d) upgrades the size until the bucket count ≤
  `MAX_BUCKETS` (200); no random downsampling.
- `unit` = `minute` (<1 h), `hour` (<1 d), `day` otherwise.
- Empty buckets are emitted (`requests 0`, zero tokens, `cost 0.0`) so the time axis is
  continuous; a reader error is never rendered as a flat zero line (see §11).
- `start` = local midnight of the range cutoff (for today/7d/30d) or the earliest row
  (`all`); the renderer displays local time.

## 5. Cost basis

`cost_basis = "opencode_message_raw"` and every UI label says **Raw Cost**. Timeline cost is the
raw OpenCode message cost; it is **not** official OpenCode Go quota consumption and is never
plotted on the same axis as official quota. The tooltip carries:
*Raw cost recorded in OpenCode message data. It is not equivalent to official OpenCode Go quota
consumption.*

## 6. Agent × Model matrix

Rows = raw agents (from `/api/agents`, no new API), columns = model identity
(`model` + `provider_id` + `variant`). Cell metric ∈ {Requests, Tokens, Raw Cost} (default
Tokens). Raw agent names are the primary identity; `group` is a secondary badge; `unknown` is
shown normally. The matrix follows the Agents tab's search + provider/source filters, and only
shows models those filtered agents actually used. Tooltip: Agent / Group / Model / Provider /
Variant / Requests / Tokens / Raw Cost (never prompts/session content). Sticky headers + scroll.

## 7. Matrix scaling

Visual intensity only, via `sqrt`/`log1p` so a single huge cell cannot flatten the rest; the raw
value is never altered (tooltip shows the true numbers). If agents > 20 or models > 20 the view
shows the top 20 by the selected metric **with a visible note** ("Showing top 20 by Tokens");
the agent table remains complete.

## 8. Forecast visualization

Compact bars appended to each Forecast section, driven only by the API payload:
- **Session**: used bar with the official reset context and, when `limit_before_reset`, an
  estimated-limit marker.
- **Weekly / Period**: current vs projected vs limit (a projected value above the limit is
  **not** clamped — the bar may pass the limit marker; tooltips keep the true numbers).
Statuses (`limit_before_reset`, `reset_before_limit`, `already_at_limit`, `no_usage`,
`insufficient_data`, `unavailable`) are consumed verbatim; the renderer derives nothing.

## 9. Official vs estimated semantics

Official quota state (used/remaining/limit/reset) and estimated values remain visually and
textually separate. The Forecast section keeps the **Estimate** wording and the existing
disclaimer; `official status = unavailable` renders "Official quota state unavailable" rather
than a fake 0%. Raw Cost (message-record cost) and official quota usage are never merged.

## 10. Lazy loading

Overview now loads exactly three sources: `agents`, `sessions`, `timeline` (in parallel, each
with an independent error). It still never loads `models`, `providers`, or `forecast`. Agents
loads on first activation (table + matrix from the same payload); Forecast stays lazy
(`/api/forecast` is requested only when the tab is opened). Charts never call the API
themselves — they render cached data passed by core.

## 11. Error / empty states

`Timeline unavailable.` (fetch/error) is distinct from `No usage in this range.` (empty) and from
`Timeline unavailable because the OpenCode usage schema is not supported.` (reader not ok). A
timeline failure degrades only the timeline card; the Overview summary and Top Agents/Models
remain. Matrix with no data shows `No Agent usage in this range.` (no empty grid). Forecast
visuals with `unavailable` show the official-unavailable text, never 0%.

## 12. Accessibility

Charts expose `aria-label`s and a short text summary; matrix cells are focusable (`tabindex`) with
`aria-label`/title; color is never the only signal (labels/values accompany it); motion is
minimal (no animation to reduce) so `prefers-reduced-motion` needs no special path.

## 13. Security

Unchanged boundary: Host/Origin/token. New bridge method `widgetAPI.apiGetTimeline(range)`;
main allowlist accepts bare `/api/timeline` + validated `?range=` only. SVG text uses
`textContent`/the shared escaping helpers (no unescaped `innerHTML` for agent/group/model/
provider/source/variant labels); tooltips are escaped; a single shared tooltip node is reused
(no DOM growth). Chart modules never touch notification APIs (visualization cannot trigger
notifications).

## 14. Performance

Single DB read per `/api/timeline` request; bucket count ≤ 200 so SVG node counts stay small.
Resize re-renders via rAF/debounce and never refetches; metric switches re-render from cache.
Measured on the real DB (~2.4k rows): timeline endpoint ≈328 ms; agents/models/providers/sessions
≈300–325 ms; the Dashboard smoke observed stable DOM node counts across repeated tab cycles
(1442 → 1442).

## 15. Known limitations

1. `Timeline` shows Requests/Tokens/Raw Cost only (no input/output/cache split, agent/session
   counts yet).
2. `all` bucket sizing is span-driven; very old, sparse history therefore uses coarser buckets.
3. Forecast visuals are current-state only — no forecast/quota history (no reliable snapshot
   series exists).
4. Matrix is capped at top-20 × top-20 for display when the data exceeds that (a visible note is
   shown; the underlying tables remain complete).
5. Charts intentionally avoid per-point hover nodes; one shared tooltip serves all charts.
6. No Sankey/network/session-graph views by design.
