# Phase 5A — Observability Dashboard UI

> Large-screen (960×720) dashboard that surfaces the Phase 3 observability APIs
> (`/api/agents`, `/api/models`, `/api/sessions`) through the Phase 2.1 secure bridge.
> **Raw Cost is explicitly separated from official OpenCode Go quota consumption.**

## 1. Scope

In: Overview, Agents, Models, Sessions tabs; shared range; lazy loading; in-memory cache;
refresh-current-tab; agent/model detail panels; session tree; Raw Cost semantics;
reader/empty/loading/error states; escaping; small/mid/large regression safety.

Out (deferred): forecasting/quota prediction (Phase 4), notifications, tray/startup redesign,
third-party adapters, advanced charting (5B), Providers as a standalone tab.

## 2. Navigation

- Nav lives inside the existing large view: `#obsDashboard` (shown only in large mode),
  4 native focusable `<button role="tab">` with `data-tab="overview|agents|models|sessions"`
  and aria labels, plus a shared range control with `data-range="today|7d|30d|all"`.
- Small and mid layouts are untouched; the nav is not rendered there.

## 3. Shared range state

- One dashboard range, default **today**, internal values `today | 7d | 30d | all` passed
  directly to the API `?range=` parameter (no new window semantics, no client-side date math).
- Changing range refetches only the active tab.

## 4. Lazy loading

- Entering large mode triggers a load of the **active** tab only (`setUiState('large')` →
  `obsEnsure(obsTab)`).
- Endpoints per tab: Overview → `agents` + `sessions`; Agents → `agents`; Models → `models`;
  Sessions → `sessions`. Providers is never requested by the dashboard (it is derived from
  loaded Agent/Model data).
- Non-active tabs stay unloaded until first activated (verified in the smoke test).

## 5. UI cache

- Module-level in-memory `dashCache` keyed by `tab|range`; re-selecting the same tab+range
  renders from memory without a request.
- No localStorage/sessionStorage persistence of observability responses (data can become
  stale and grow); the cache is lost on reload by design.

## 6. Overview

- Sources `/api/agents` + `/api/sessions` for the selected range.
- Summary cards: Requests, Tokens, **Raw Cost**, Cache Read Ratio, Active Agents,
  Active Models, Sessions.
  - Totals come from API-provided rows (sum of `requests`/`tokens`/`cost`); Active Models is
    the distinct `(model, provider, variant)` set across agent model rows; the overall
    Cache Read Ratio aggregates API-provided token buckets
    (`Σcache_read / (Σinput + Σcache_read)`, `null` when the denominator is 0). No metric is
    recomputed from raw messages.
- **Top Agents** table (Agent, Group, Requests, Tokens, Raw Cost, Cache Ratio, Top Model),
  default sort Tokens DESC, switchable to Requests / Raw Cost; stable sort.
- **Top Models** table derived from agent model rows (`topModelsFromAgents`): Model, Provider,
  Variant, Requests, Tokens, Raw Cost, Top Agent; default sort Tokens DESC.

## 7. Agents

- `/api/agents`: Agent, Group badge, Requests, Sessions, Tokens, Raw Cost, Cache Ratio,
  Avg Tokens/Request, Avg Duration, Top Model.
- Sort: Requests / Tokens / Raw Cost / Sessions (default Tokens DESC, stable).
- Search filters the loaded rows by agent/group; a Provider/Source `<select>` is built
  dynamically from the loaded data (no hardcoded provider list).
- `group = null` renders as no badge / `—` (never the literal `unmapped` unless it is the real
  value); the raw agent name is always primary and the group is secondary.

## 8. Agent detail

- Row click opens a right-hand detail pane (no new window): raw agent name, group, requests,
  sessions, total tokens, Raw Cost, Cache Ratio, Avg Tokens/Request, Avg Cost/Request,
  Avg Duration; plus **Models** (Model, Provider, Variant, Requests, Tokens, Raw Cost,
  Request Share, Token Share, Cost Share — API shares are `0.0–1.0`, formatted as percentages
  at render time) and **Providers** (Provider, Source, Requests, Tokens, Raw Cost, Cache Ratio).

## 9. Models

- `/api/models`: Model, Provider (= `provider_id`), Source, Variant (`null` → `—`), Requests,
  Sessions, Tokens, Raw Cost, Cache Ratio, Top Agent.
- Sort: Requests / Tokens / Raw Cost / Sessions; search by model/provider/source; dynamic
  provider/source filter.

## 10. Model detail

- Row click opens the detail pane: base metrics plus the Agents breakdown
  (Agent, Group, Requests, Tokens, Raw Cost, Cache Ratio).

## 11. Sessions

- `/api/sessions`: tree view + detail pane. Columns: short Session ID (full ID in the title),
  Depth, Requests, Tokens, Raw Cost, Agents count, Children count.
- Search by session_id; detail pane shows Session ID, Parent Session ID, Depth, Start, End,
  Duration, Requests, Tokens, Raw Cost, and Agents/Models/Providers breakdowns.
- **Never rendered:** prompt, response, reasoning, message text, tool arguments, commands,
  file paths, titles.

## 12. Session tree

- The renderer consumes the backend `tree` verbatim (nodes with `depth`, `children_count`,
  `parent_session_id`, `is_root`, plus `diagnostics`) and never re-derives parent/child,
  depth, cycles or missing-parent handling.
- Expand/collapse per row, plus Expand all / Collapse all. Default: roots expanded, deeper
  levels collapsed (avoids dumping deep trees at once).

## 13. Raw Cost semantics

- UI label: **Raw Cost** (Chinese UI may use 原始 Cost). Forbidden wording: Official Cost,
  Go Usage, Quota Cost, Subscription Usage.
- Tooltip: *Raw cost recorded in OpenCode message data. It is not equivalent to official
  OpenCode Go quota consumption.*
- No meter ratio, credit deduction or subscription adjustment is applied anywhere in the UI.

## 14. Reader error semantics

- `readerState(meta)` distinguishes `ok | unsupported | missing | error`;
  `readerMessage()` renders: `OpenCode usage database schema is not supported.` /
  `OpenCode usage database was not found.` / `OpenCode usage data could not be read.`
- A non-ok reader is shown as such and **never** rendered as `0 requests`.
- Per-tab states: Loading… / Empty (`No Agent usage in this range.`) / Error
  (`Failed to load Agent usage.` + Retry). Stack traces, exceptions and tokens are never
  rendered.

## 15. Security boundary

- The dashboard uses only `window.widgetAPI.apiGetAgents/apiGetModels/apiGetSessions`
  (plus the existing state/config/formula methods). No base URL, no runtime token, no `fetch`,
  no `Authorization` header, no generic request bridge in the renderer.
- All dynamic strings (agent, group, model, provider, source, variant, session_id, error
  messages) are escaped via the shared `esc()`/cell helpers before insertion into markup.

## 16. Small / Mid regression

- All new CSS is scoped under `.obs*`; new markup lives inside large-only panels.
- Existing transparent window, snap, click-through, quota cards, provider tabs, curves,
  heatmap and shortcuts are unchanged.
- Smoke verified DOM-level toggles: small shows the small view and hides `#obsDashboard`;
  mid shows the compact view; returning to large re-shows the dashboard.

## 17. Tests

- Suite total: **241 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`, TEMP
  redirected). Phase 5A added 17 tests:
  - `tests/js/dashboard_core.test.js` (29 node:test cases) + `tests/test_dashboard_core.py`
    wrapper (+ inline-script `node --check`).
  - `tests/js/dashboard_views.test.js` (16 node:test cases) + `tests/test_dashboard_views.py`
    wrapper + static checks.
  - `tests/test_dashboard_static.py` (7 static guards: tabs, ranges, Raw Cost label, disclaimer,
    large-only root, no external scripts, no token surface).
- Smoke (real database, read-only; Electron CDP): PASS — 4 tabs, ranges
  `today/7d/30d/all`, Overview loads with **Raw Cost**, Agents/Models/Sessions lazy-load only
  when activated, agent/model detail open, session detail shows Duration, session panel has a
  Children column, range switch works, no content words rendered, small/mid/large toggles OK,
  `widgetAPI.apiEnv` absent, renderer token-leak scan `leaks: []`.
  Observed row counts at the default `today` range: agents 7, models 3, sessions 22 (higher in
  `all`; matches the range-filtered APIs).

## 18. Known limitations

1. No charts yet (5B); tables + summary cards only.
2. Providers is not a standalone tab — provider data appears in Agent/Model detail and filters.
3. The dashboard is only visible in large mode; small/mid keep their existing content.
4. Range/selected tab are not persisted; the in-memory cache is cleared on reload.
5. Overview derives Top Models and the overall cache ratio by rolling up API-provided rows
   (display roll-up only; no metric is recomputed from raw messages).
6. No subtree roll-up totals; no forecasting.
7. The global `Tab` shortcut (supplier cycling) is intentionally unchanged, so keyboard users
   reach dashboard tabs via standard focus (they are native buttons).

## 19. Deferred Forecasting

Phase 4 (5 h / weekly / subscription-period burn rate, remaining-quota estimate, time-to-limit)
is not implemented. Overview shows only observed observability data.
