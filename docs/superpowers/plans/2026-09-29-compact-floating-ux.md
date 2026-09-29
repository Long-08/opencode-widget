# Compact Floating UX Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the desktop floating widget default to a lightweight always-on-desktop Mini bar, with an explicit Mini → Compact → Expanded interaction model, while keeping the full dashboard available on demand.

**Architecture:** Reuse the existing `small/mid/large` UI-state machine (`setUiState()` in `electron/app/app.js` + `SIZES` in `electron/main.js`); do not invent a new mode system. The Mini view (`#smallView`) is slimmed to a single-row status bar (no ring, no grid), the Compact view (`#compactView`) is capped at Top-3 models plus an "open full panel" action, and Expanded (`#dashView`) keeps everything it has today. Default cold start becomes `small`; expanded-grade view prefetches are skipped unless Expanded is (or was) active.

**Tech Stack:** Electron (no-framework renderer, classic scripts), pytest static guards, `node --test` behavior tests that eval the renderer with a DOM stub.

**Spec:** user-supplied 60-section brief "Compact Floating UX Refactor" (this session); behavior doc output at `docs/COMPACT_FLOATING_UX.md`.

## Global Constraints

- Do NOT change: Forecast algorithms, Observability data semantics, Timeline aggregation, notification policy rules, security boundaries (DPAPI / runtime token / local API allowlist), packaging/release/formula trust.
- No new preload bridge methods (`tests/test_renderer_token_boundary.py::EXPECTED_WIDGET_API` must stay byte-identical).
- Renderer must never gain: `apiEnv`, `API_BASE`, `API_TOKEN`, `Bearer `, `runtime.json`, generic fetch bridge, prompt/response/secret surfaces.
- All 621 baseline tests (main HEAD `07933e7`) must keep passing; old tests may only be modified if they assert the rejected "default is mid/large" behavior, with the reason documented in this plan / commit messages.
- New Mini/Compact DOM must not render raw HTML from data; app-generated markup must contain only computed numbers, with data-derived text through `esc()`.
- Respect `prefers-reduced-motion`: no new animations are added (existing ring transition untouched).
- Window sizes stay stable/testable constants in `electron/main.js` `SIZES`.

## Review Focus

1. **Cold-start flicker/focus**: startup must settle on Mini with no resize thrash (window is born at the Mini size; renderer boots with `noResize=true`). Test: default-launch assertions in `tests/js/widget_modes.test.js`.
2. **Snap (吸顶) regression**: snap pins the window at `SIZES.small` and drives `setUiState("small", true)`; the new smaller Mini must not break enter/restore (`restoreFromSnap` fallback target). Test: static assertions on `main.js` blocks + node snap-restore simulation.
3. **Mode switch must not refetch**: entering Compact from Mini triggers no `apiGetState`/`apiGetView` calls; entering Expanded fetches only missing view ids (cache hits do not re-request). Test: bridge call counts in `widget_modes.test.js`.
4. **Data-derived text escaping**: the `rem` fix must keep escaping the rendered text (`esc(remText)`) even though the wrapper span is now in the template. Test: node assertion that rendered list HTML contains `<span class="rem">剩` and never `&lt;span class="rem"&gt;`.
5. **Keyboard/AT surfaces**: Esc collapses Compact/Expanded to Mini but must be ignored while typing in observability search/select inputs; Mini bar is reachable and activatable by keyboard. Test: node Esc-guard test + static aria assertions.

---

### Task 1: Branch + failing regression tests (TDD red)

**Files:**
- Create: `tests/js/widget_modes.test.js`
- Create: `tests/test_compact_floating_ux.py`

**Interfaces:**
- Produces: behavior harness `loadWidgetApp()` (used only inside the node test) that evals `dashboard/format.js` + `dashboard/core.js` + `dashboard/forecast.js` + `app.js` against a stub DOM and exposes `{ setUiState, render, refresh, fillModelList, uiState, flushTimers, calls, els }`.
- Produces: pytest static guards referencing final source strings (Task 2-4 make them pass).

- [x] **Step 1: Create branch from published main**

```bash
cd /d/opencode/opencode-widget
git checkout -b dev/compact-floating-ux   # base = 07933e7 = origin/main
```

- [x] **Step 2: Write the node behavior suite** (`tests/js/widget_modes.test.js`)

Harness requirements: stub `document` (memoized per-selector elements with `classList` Set, `style`, `dataset`, `addEventListener`, `appendChild`, `innerHTML`, `textContent`), `window.widgetAPI` recording bridge (`resize/saveUiState/apiGetState/apiGetFormula/apiGetView/...`), `localStorage`, capture-only `setTimeout`/`setInterval`. State fixture supplies 3 windows (session/weekly/monthly), 5 go/free stats, minimal history/suppliers. Then assert:

1. **default launch opens mini**: eval → flush timers → `resize` called with `"small"`; `#smallView` has class `on`; `#compactView` style.display `"none"`; `saveUiState` last arg `"small"`.
2. **mini is smaller than compact/expanded**: parse `SIZES` from `electron/main.js` source: `small[0] < mid[0] < large[0]` and `small[1] < mid[1] < large[1]`, `small[1] <= 120`.
3. **mini → compact**: invoke the recorded `#smallView` click handler → `#compactView` display `"flex"`, `#smallView` loses `on`, `resize` `"mid"`, and no new `apiGetState` calls.
4. **compact → expanded**: `#btnOpenFull` click → `#dashView` has `on`, `resize` `"large"`, missing view ids fetched once (`apiGetView` calls ≥1); collapse + re-expand → no additional `apiGetView` calls (cache hit).
5. **expanded → mini**: from large, `#btnMin` click → `resize` `"small"`, `#dashView` loses `on`.
6. **compact model list is capped at Top 3**: after flush in mid, `#cList` innerHTML contains exactly 3 `class="m-item"`; `#dList` in large contains all 5.
7. **Esc collapses to mini (and ignores typing targets)**: large + captured `document` keydown `{key:"Escape"}` → resize `"small"`; large + keydown with `e.target` inside input stub → no change.
8. **notification open still reaches expanded/forecast**: captured `onOpenForecast` callback → `uiState === "large"`, `#dashView` on, `OCW.state.tab === "forecast"`.
9. **rem text rendering**: a go stat with `effective_remain`/`avg_per_req` renders `<span class="rem">剩` in `#cList` and never `&lt;span`.

- [x] **Step 3: Write the pytest static suite** (`tests/test_compact_floating_ux.py`)

Static guards (source-string/regex only, following `test_dashboard_static.py` style): SIZES ordering + Mini ceiling; `app.js` default `let uiState = "small"` + `initDefaultView()` → `setUiState("small", true)`; `main.js` `createWindow()` born at `SIZES.small` and `curUiState = "small"`; transition wiring (`btnMin`/`btnExpand` ternaries unchanged, `#smallView` click, `#btnOpenFull`); `focusWidget()` contains no `resize`/`setUiState` (tray show respects state policy); `dashboard/forecast.js` `wireForecastNotificationActions` keeps `setUiState("large")` + `obsSelectTab("forecast")`; rem built as escaped text (`rem = "剩"`, `esc(remText)`, no `esc(rem)` and no `` rem = `<span ``); CSS rules `body.mode-small footer`/`body.mode-small .op-wrap` hidden + `.mini-bar` + `.c-actions` present; `#miniBar` has `role="button"`, `tabindex="0"`, `aria-label`; `#btnOpenFull` has `aria-label`; mode-aware prefetch (`neededViewIds` referenced by both `refresh()` and `ensureExpandedViews`).

- [x] **Step 4: Run to verify RED**

Run: `python -m pytest --basetemp=.tmp/pytest tests/test_compact_floating_ux.py` and `node --test tests/js/widget_modes.test.js`
Expected: FAIL (features not implemented).

- [x] **Step 5: Commit**

```bash
git add tests/js/widget_modes.test.js tests/test_compact_floating_ux.py
git commit -m "test: add compact floating UX regression coverage"
```

### Task 2: Mini as the default state (feat)

**Files:**
- Modify: `electron/main.js` (SIZES, createWindow bounds, curUiState/preSnapUiState defaults, resize fallback)
- Modify: `electron/app/index.html` (replace `#smallView` content with `#miniBar`; keep view-root ids)
- Modify: `electron/app/app.js` (uiState initial, initDefaultView, renderMini, render gating for mini, body mode classes)
- Modify: `electron/app/app.css` (mini-bar styles, mode-scoped header/footer hiding, drop dead small-view rules except `.s-hint`)

**Interfaces:**
- Produces: `SIZES = { small: [400, 88], mid: [560, 420], large: [960, 720] }`.
- Produces: `renderMini()` reads only `displayWindows()` + `state.keyOk` (windows already fetched by `/api/state`; no new API).
- Produces: body classes `mode-small` / `mode-compact` / `mode-expanded` set in `setUiState()`.

- [x] **Step 1: main.js** — set `SIZES.small = [400, 88]`, `SIZES.mid = [560, 420]`; `createWindow()` uses `SIZES.small` for width/height/x/y; `curUiState = "small"`, `preSnapUiState = "small"`; `restoreFromSnap` fallback `SIZES.small`; resize IPC fallback `SIZES.small`.
- [x] **Step 2: index.html** — `#smallView` becomes

```html
<div class="small on" id="smallView">
  <div class="mini-bar" id="miniBar" role="button" tabindex="0"
       aria-label="OpenCode 用量摘要：点击打开快速查看，双击按钮展开完整面板">
    <span class="mb-dot" id="miniDot"></span>
    <span class="mb-name">OCW</span>
    <span class="mb-item" id="miniPct">—</span>
    <span class="mb-item" id="miniCost">—</span>
    <span class="mb-item" id="miniState">…</span>
    <span class="mb-item" id="miniKey">…</span>
  </div>
</div>
```

- [x] **Step 3: app.js** — `let uiState = "small"`; rename `initExpanded` → `initDefaultView` calling `setUiState("small", true)`; add `miniSeverity(pct)` (≥95 告警/danger, ≥70 注意/warn, else 正常/green — mirrors `ringColor` thresholds); `renderMini()` fills `#miniPct` (`本月 N%`), `#miniCost` (`money(m.used)`, title = used/limit), `#miniState`, `#miniDot` color, `#miniKey` (`Key ✓`/`Key ✗`); `render()` calls `renderMini()` always and gates `renderSide/renderTabs/renderModels` behind `uiState !== "small"`, `renderChart/renderLarge` behind `uiState === "large"`; `setUiState()` toggles the three body classes; add `#smallView` click → `setUiState("mid", false, true)` and `#miniBar` Enter/Space keydown → same.
- [x] **Step 4: app.css** — add mini-bar rules; `body.mode-small` hides `footer`, `.op-wrap`, `.status`, `.brand .title`; `body.mode-compact` hides `footer`; remove dead `.s-top/.s-info/.s-sup/.s-model/.s-mstats/.s-reset/.s-grid/.small .rc*` rules (keep `.s-hint` used by footer); keep `#smallView` class toggling mechanics.
- [x] **Step 5: Run** `node --test tests/js/widget_modes.test.js` → mini/default/sizes tests green; `python -m pytest --basetemp=.tmp/pytest tests/test_compact_floating_ux.py -k mini` green. Baseline suite still green (dead-CSS removal checked against static guards).
- [x] **Step 6: Commit** `feat: make mini view the default widget state`

### Task 3: Compact summary mode + explicit expand/collapse flow (feat)

**Files:**
- Modify: `electron/app/index.html` (compact actions row)
- Modify: `electron/app/app.js` (Top-3 cap, mode-aware prefetch, Esc, updated-time)
- Modify: `electron/app/app.css` (`.c-actions`)

**Interfaces:**
- Produces: `fillModelList(el, key, limit)` — `limit > 0` slices the ranked list (rank unchanged; bar percentages still computed over the full list).
- Produces: `neededViewIds(src0)` (pure) + `ensureExpandedViews()` — both consumed by `refresh()` / `setUiState("large")`.
- Produces: `COMPACT_MODEL_LIMIT = 3` constant.

- [x] **Step 1: index.html** — append to `#compactView`:

```html
<div class="c-actions">
  <span class="c-updated" id="cUpdated"></span>
  <button class="c-expand-btn" id="btnOpenFull" aria-label="打开完整分析面板" title="展开完整面板">打开完整面板</button>
</div>
```

- [x] **Step 2: app.js** — `fillModelList(el, key, limit = 0)` renders `sorted.slice(0, limit)` when `limit > 0`; `renderModels()` fills `#cList` with `COMPACT_MODEL_LIMIT` when not small and `#dList` unbounded only when large; `#cCount` gets `" · Top 3"` suffix in compact; `neededViewIds(src0)` returns the base trio always and the expanded-only ids (`viewIdFor("all", heatRange)`, `"all_daily"`, conditional `viewIdFor(src0,"30")`, `model_*`) only when `uiState === "large"`; `refresh()` uses it; `ensureExpandedViews()` awaits `Promise.all(ids.map(fetchView))` (fetchView already cache-skips) and is called from `setUiState` when expanding; `render()` sets `#cUpdated` ("更新于 Ns 前"); document keydown gains the Esc branch (skips `input, select, textarea` targets; mid/large → `setUiState("small", false, true)`).
- [x] **Step 3: app.css** — `.c-actions` flex row + `.c-updated` + `.c-expand-btn` styles.
- [x] **Step 4: Run** node suite (transitions/top3/refetch/esc green) + full pytest.
- [x] **Step 5: Commit** `feat: add compact summary mode and explicit expand/collapse flow`

### Task 4: Text rendering fix (fix)

**Files:**
- Modify: `electron/app/app.js` (`fillModelList` rem construction)

- [x] **Step 1:** replace `let rem = ""` HTML accumulation with plain text:

```js
let remText = "";
if (isQuota && effectiveRemain > 0 && remainCnt > 0) remText = "剩" + Math.round(remainCnt) + "次";
```

and the row template becomes `...<span class="cnt">${esc(main)}<span class="sub">${esc(extra)}</span></span>${remText ? '<span class="rem">' + esc(remText) + '</span>' : ""}...`
- [x] **Step 2: Run** node rem test green; grep guards: no `esc(rem)`, no `rem = \`<span` in app.js.
- [x] **Step 3: Commit** `fix: clean up floating widget text rendering issues`

### Task 5: Docs (docs)

**Files:**
- Create: `docs/COMPACT_FLOATING_UX.md`

- [x] **Step 1:** document: why the large floating panel was a problem; the three states and their window sizes; default cold start (always Mini, previous position not restored as Expanded); switch interactions (click/Enter on Mini bar, `btnMin`/`btnExpand`, `打开完整面板`, Esc); tray (`Show Widget` restores/focuses the current visible state, never resizes); notifications (click → Expanded + Forecast tab); mapping `small/mid/large` → `Mini/Compact/Expanded` (state machine reused); performance policy (Mini/Compact fetch only `/api/state` + formula; Expanded lazily fetches views/obs tabs); accessibility (aria-labels, keyboard); known limitations (compact list capped at Top 3, hover auto-expand intentionally not implemented).
- [x] **Step 2: Commit** `docs: document compact floating UX behavior`

### Task 6: Full verification

- [x] **Step 1:** `python -m pytest --basetemp=.tmp/pytest` → all pass (621 baseline + new).
- [x] **Step 2:** `for f in tests/js/*.test.js; do node --test $f; done` → all green.
- [x] **Step 3:** security regression: token-boundary + escaping + lifecycle static suites green (no bridge changes).
- [x] **Step 4:** smoke: launch `data_server.py` + Electron with debug env, verify process tree up and snap log clean, then quit via tray-quit path; record observations in final report.

## Self-Review Notes

- Spec coverage: §4-6/15-21/27-28 (three states + content policy) → Tasks 2-3; §7/33 (default cold start Mini) → Task 2; §8-9 (transitions) → Tasks 2-3 + node tests; §10/23-24 (snap/positioning) → reuse, SIZES drive it; §13/14 (notification/tray) → preserved + pinned by tests; §34 (no new animation) → none added; §35-37 (copy length) → Mini short labels only; §38-39 (header/footer) → mode-scoped CSS; §40-43 (perf/data) → render gating + mode-aware prefetch; §46-47 (rem bug) → Task 4; §48-49 (a11y/Esc) → Tasks 2-3; §51-53 (tests/security) → Task 1 + Task 6; §57-59 (branch/commits/docs) → Tasks 1/5.
- Type consistency: `fillModelList(el, key, limit)` and `neededViewIds(src0)` are the only cross-task signatures; node test harness exposes them via the eval return object.
- No placeholders: all code content specified above or in the referenced task steps.
