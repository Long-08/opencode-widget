# Phase 5.1 — Frontend Structure Cleanup

> Behavior-preserving refactor: the Phase 5A dashboard was moved out of the single
> `index.html` into CSS + classic-script modules. **No functional, API, security or
> observability-semantics change.**

## 1. Scope

In: extract CSS; extract dashboard formatting, shared state/cache/orchestration and
per-tab views; move the legacy widget JS into `app.js`; retarget tests; add structure guards.

Out: any new feature, forecasting, charts, new tabs, DOM/id/class changes, CSP widening,
ES modules/bundlers/frameworks, security-boundary changes, legacy UI redesign.

## 2. File responsibilities (final)

| File | Responsibility |
|---|---|
| `electron/app/index.html` | HTML structure only + `<link rel="stylesheet" href="app.css">` + 8 ordered classic `<script src>` tags + CSP meta. No `<style>`, no inline `<script>`, no ESM. |
| `electron/app/app.css` | The entire former `<style>` block, moved verbatim (selectors unchanged). |
| `electron/app/app.js` | Legacy widget JS (verbatim): small/mid/large state, quota UI, provider tabs, history curves, heatmap, modals, IPC wiring, snap/click-through, `esc` consumers, `DOMContentLoaded` bootstrap and globals (e.g. `uiState`). Only the 2 dashboard call sites use the namespace (`OCW.obsEnsure(OCW.getActiveTab())`). |
| `electron/app/dashboard/format.js` | DOM-free: `esc()` + `dashNull`/`formatInteger`/`formatTokens`/`formatCost`/`formatPercent`/`formatDuration`/`formatDateTime`/`shortSessionId`. |
| `electron/app/dashboard/core.js` | DOM-free dashboard core (`phase5a-core`): tab/range constants, `dashRangeValue/dashCacheKey/dashEndpointsFor/dashShouldFetch/dashLoad/dashSortRows`, top-* helpers, `overviewSummary/topModelsFromAgents`, `readerState/readerMessage`. Plus the shared runtime state: `OCW` namespace, `dashCache` (`tab|range`), active tab, `registerTab`, `obsEnsure/obsRenderTab/obsRenderActiveView/obsSelectTab/obsSelectRange/getActiveTab`, and tab/range/retry/sort wiring. |
| `electron/app/dashboard/views.js` | Shared view-model + markup helpers (`phase5a-views`): `dashCell/dashCellsHtml/dashStateHtml/dashErrorHtml/dashReaderError/providerKey/providerOptions/filterByProvider/filterAgents/filterModels/filterSessions/agentRowCells/modelRowCells/sessionRowCells/sessionTreeRows/agentDetailModel/modelDetailModel/sessionDetailModel/dashSortBtn/dashMetricsHtml/dashDetailTable` + `obsFillProviderSelect`. |
| `electron/app/dashboard/overview.js` | Overview cards + Top Agents/Top Models; registers via `OCW.registerTab("overview", …)`. |
| `electron/app/dashboard/agents.js` | Agents table/drawer/selection/sort/search/provider filter; registers `OCW.registerTab("agents", …)`. |
| `electron/app/dashboard/models.js` | Models table/drawer/selection/sort/search/provider filter; registers `OCW.registerTab("models", …)`. |
| `electron/app/dashboard/sessions.js` | Sessions tree/drawer/expand/collapse; registers `OCW.registerTab("sessions", …)`. |

Window-load order is dependency order: `format.js → core.js → views.js → overview.js →
agents.js → models.js → sessions.js → app.js`.

## 3. Module system choice

Classic scripts + a `window.OCW` namespace — deliberately **not** ES modules: Chromium blocks
local module scripts over `file://` (CORS), and the app loads `index.html` directly
(`loadFile`). No bundler/build step; the app still works by opening the file.

Load-time rule: modules only define functions/state. **No module triggers an API request at
load time**; requests remain controlled by the active-tab orchestration in `core.js`.

## 4. CSP

`index.html` keeps the minimal policy and adds only what local files require:

```
default-src 'none'; script-src 'self' 'unsafe-inline' file:; style-src 'self' 'unsafe-inline' file:;
connect-src http://127.0.0.1:* http://localhost:*; img-src data:;
base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'
```

No `*` source, no `unsafe-eval`, no external/CDN resources. `'unsafe-inline'` remains for
inline style attributes and legacy inline handlers.

## 5. Behavior preserved

- Nav: 4 tabs (`overview|agents|models|sessions`) + 4 ranges (`today|7d|30d|all`), default today.
- Lazy loading: entering large loads only the active tab (Overview → agents + sessions);
  each tab first-loads on activation; no init fan-out.
- Cache: `OCW.dashCache` keyed `tab|range`, in memory only.
- Refresh: only the active tab + range.
- Overview/Agents/Models/Sessions content, sorting defaults, search, provider filters,
  detail panels, session tree, expand/collapse, loading/empty/error and reader-error states:
  unchanged.
- `Raw Cost` label + disclaimer unchanged; `group: null` → no badge; `cache_read_ratio: null`
  → `—`; all dynamic strings still escaped via the shared `esc()`.

## 6. Security boundary

Unchanged: token lifecycle, `runtime.json`, `runtime_env.js`, preload narrow bridge, main
allowlist, Host/Origin policy, Authorization injection. The renderer (now across
`app.js` + `dashboard/*.js`) still has no base URL, no runtime token, no generic fetch/request
bridge, no `Authorization`, no `runtime.json` reference.

## 7. Tests

- Suite total: **257 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`, TEMP
  redirected) — 241 Phase 5A + 16 new.
- New/updated:
  - `tests/js/{esc,dashboard_core,dashboard_views}.test.js` now load `format.js → core.js →
    views.js` (same assertions — the behavior lock).
  - `tests/test_dashboard_core.py` runs `node --check` over `app.js` + all `dashboard/*.js`.
  - `tests/test_dashboard_static.py`, `tests/test_renderer_escaping.py`,
    `tests/test_renderer_token_boundary.py` retargeted to the files that now hold each string;
    token-boundary checks scan all renderer JS.
  - NEW `tests/test_frontend_structure.py`: file existence, stylesheet link, 8 scripts in order,
    no `<style>`/inline `<script>`, no `type="module"`, no external assets, CSP has no wildcard
    in script/style and no `unsafe-eval`, preserved `smallView`/`compactView`/`dashView`/
    `#obsDashboard`/`#btnExpand`.

## 8. Smoke (real environment, read-only DB)

Electron CDP smoke: PASS.

- Console/log/exception capture: **0 events** (`PAGE_EVENTS: []`) → no CSP violations, no module
  load errors, no page errors.
- 4 tabs, ranges `today/7d/30d/all`; Overview shows `Raw Cost`; Agents/Models/Sessions load only
  when activated (lazy); agent/model detail and session detail work; range switch works; no content
  words rendered.
- Timing evidence (today range): first Agents load ≈307 ms (network) vs cached re-open ≈1 ms.
- Small/Mid/Large toggles all pass; `widgetAPI.apiEnv` absent; renderer token-leak scan `leaks: []`.
- Counts at `today`: agents 7, models 3, sessions 23.

## 9. Known limitations

1. Legacy widget JS lives in a single `app.js` (pure relocation, not further split) — deliberate;
   the legacy quota UI was out of scope.
2. `dashboard/views.js` is an extra shared module beyond the suggested layout (keeps DOM-free
   view-model + markup helpers out of `core.js`).
3. Dashboard mutable state lives inside `OCW`; legacy globals (`uiState`, etc.) remain as before.
4. No charts (5B), no forecasting (Phase 4), no desktop UX (Phase 6).
