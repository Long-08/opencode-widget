# Secondary Development Audit — opencode-widget

> Phase 1 audit (read-only + test baseline). No production behavior was changed.
>
> Repository: `https://github.com/ikunops/opencode-widget`
> Branch: `main` · Commit: `37e399e343789a5e7efd92c5cab626527f2bf05c`
> Audit date: 2026-09-27 · Auditor: automated agent workflow (DeepSeek V4.1 Flash orchestrator + explorer/fixer lanes)
>
> Evidence convention: `file.py:line` references point at the cloned commit above.
> Statements about "official" vs "inferred" behavior are separated explicitly — do not read
> author-calibrated heuristics as official platform rules.

---

## 1. Executive Summary

`opencode-widget` is a Windows-only desktop widget: a stdlib-only Python HTTP data server
(`data_server.py`, `127.0.0.1:8765`) that reads local OpenCode/Codex usage plus opencode.ai
official quota, applies a locally-executed "cloud formula", and serves JSON to an Electron
transparent overlay (`electron/`).

Phase-1 conclusions:

1. **Architecture is coherent but the local OpenCode reader is broken against the current
   OpenCode DB schema.** `read_opencode_all()` queries a `message` table that no longer exists
   in current OpenCode builds (verified on this machine: 0 rows returned, silently). The
   current schema exposes `session_message` + `session_v2`, which additionally contain
   **per-message `agent`, `model {id, providerID, variant}`, `cost`, `tokens`, and
   `parent_id`** — i.e. the Agent-level statistics requested for Phase 2 are feasible, but
   require a new reader first.
2. **The localhost HTTP API leaks secrets.** `GET /api/config` returns `api_key` and
   `auth_cookie` in plaintext, `POST /api/grab` returns the raw cookie, all responses carry
   `Access-Control-Allow-Origin: *`, and there is no auth or Origin check. Any web page the
   user visits while the widget runs (including via DNS rebinding) can read the opencode.ai
   session cookie. This is the single highest-priority issue (P0).
3. **The remote formula is pure data, not code.** No `eval`/`exec`/dynamic import exists
   anywhere; cloud `expr` strings are cosmetic metadata and `formula_registry.compute()`
   always calls local Python functions. Worst case of a malicious formula is manipulated
   numbers/labels/quotas (and a possible HTML-injection chain through `display_names` into
   unescaped `innerHTML` sinks), not code execution.
4. **A test baseline now exists** (pytest, 79 tests; see §13). It covers model normalization,
   cost calculation, provider mapping, time windows, credits, account split/dedup, formula
   store/apply/registry, and the HTTP API — fully isolated from real user data, network,
   subprocesses, and browser cookies.
5. **The accounting layer contains many empirically calibrated rules** (meter ratio 1.4212,
   credit deduction $5/credit, per-model quotas/request limits/token-per-request tables,
   ±120 s official/local matching window, subscription-day anchoring). These must not be
   touched without the new tests as protection. §10 documents each rule with official vs
   inferred provenance.

No Phase-2 change was made. §20 is a plan only.

---

## 2. Current Architecture

| Component | File | Role | Notes |
|---|---|---|---|
| HTTP data server | `data_server.py` (730 L) | `ThreadingHTTPServer` on `127.0.0.1:8765`; `/api/*`; state cache (30 s TTL); formula sync loop (900 s); auto sync loop (1800 s) | stdlib only; imports `go-usage-widget.py` via `importlib` spec (hyphenated filename) |
| Calculation engine | `go-usage-widget.py` (885 L) | data functions: `read_opencode_all`, `read_codex_logs`, `build_windows`, `model_stats`, `model_history`, `supplier_stats`, `heatmap`; rule tables (`PRICES`, `MODEL_QUOTAS`, `REQ_LIMITS`, `TOKENS_PER_REQ`, `LIMITS`, …); `norm_model`; `scrape_server_usage` | also contains legacy pywebview widget remnants (module is imported as `gw`) |
| Official sync → `server_usage.db` | `server_data.py` (420 L) | legacy sync path: `usage.list` + `getCosts` RPC + `/go` HTML quota scrape → SQLite | reads back via `read_server_rows()` when `usage_remote.db` has no data |
| Official sync → `usage_remote.db` | `usage_remote.py` (526 L) | current sync path: Seroval parser, RPC calls, quota snapshot, subscription billing scrape, credits meta | DB schema: `usage_records`, `cost_summary`, `quota_snapshot`, `sync_meta` |
| Formula store + view engine | `views.py` (460 L) | fetch/cache/fallback cloud formula (`FormulaStore`, TTL 900 s); `ViewEngine` executes named views over rows; `DEFAULT_FORMULA` (v4) offline fallback | `_validate` checks only `params` + `views` presence |
| Formula registry | `formula_registry.py` (398 L) | single source of truth for formula metadata; local Python implementations; `compute(id, **kw)` | cloud can override display/source/expr metadata only, never `func` |
| Browser cookie helper | `browser_cookie.py` (280 L) | best-effort Chrome/Edge cookie decryption (pure-Python AES-CBC/GCM + DPAPI) | modern Chrome (app-bound) → returns `""` |
| Electron shell | `electron/main.js` (306 L), `preload.js` (13 L) | transparent frameless window, snap-to-top, click-through, login window + cookie capture, IPC bridge | `contextIsolation: true`, `nodeIntegration: false`, no `sandbox` flag, no CSP |
| Renderer | `electron/app/index.html` (~1810 L) | all UI + inline JS; fetches `http://127.0.0.1:8765/api/*` | no external scripts/fonts; heavy `innerHTML` usage |
| Cloud formula source | `cloud/formula.json` (v6), `cloud/build_worker.py`, `cloud/formula-worker.js` | static JSON payload generated into a Cloudflare Worker; served at `/formula` | committed worker artifact is stale (v4 payload) vs `formula.json` v6 |
| Auxiliary | `gen_widget_xlsx.py` | exports a formula/usage spreadsheet from `/api/state` to Desktop | not part of runtime path |
| Startup | `启动Go用量悬浮窗.vbs` / `.cmd` | starts `pythonw.exe data_server.py` + `electron.exe`, duplicate-process guard | VBS is the zero-console entry |

Data ownership: `config.json`, `server_usage.db`, `usage_remote.db`, `pylib/`, `webdata/`,
`logs/` are gitignored (`.gitignore:1-14`); verified no secrets are committed in the clone.

---

## 3. Data Flow

Local chain (as implemented):

```
~/.local/share/opencode/opencode.db ──► gw.read_opencode_all() ──┐
   (legacy `message` table only;                                   │
    current schema NOT supported — see §4)                         ├─► rows[]
~/.codex/logs_2.sqlite ──────────────► gw.read_codex_logs() ──────┤   {ts, cost, model,
   (cursor persisted as config.codex_log_id)                      │    tokens, src,
                                                                  │    dur_ms}
usage_remote.db ─────────────────────► ur.read_remote_rows() ─────┤
   (official sync; authoritative)                                 │
server_usage.db ─────────────────────► sd.read_server_rows() ─────┘
                                          │
                                          ▼
                        data_server._collect_rows()
                        · if official rows exist: _split_own_other(local, official)
                        · else: all local rows → account="other"
                                          │
                                          ▼
        gw.build_windows / model_stats / model_history / supplier_stats / heatmap
        + formula params (prices, quotas, meter ratio, windows) via apply_params_to_gw
        + fr.compute(...) registered formulas
                                          │
                                          ▼
                       data_server.build_state() (30 s cache, startup preheat)
                                          │
                                          ▼
                 HTTP/JSON http://127.0.0.1:8765/api/state (+ /api/formula, /api/views)
                                          │
                                          ▼
                       Electron renderer (index.html) — plaintext fetch
```

Official chain (as implemented):

```
opencode.ai/auth (login window, persist:opencode-auth partition)
        │  auth cookie captured via ses.cookies.get({url:'https://opencode.ai'})
        ▼
renderer ◄── IPC grab-auth ── main.js ──► POST /api/server {auth_cookie, workspace_id}
        │                                        │
        │                                        ▼
        │                              config.json (plaintext)
        ▼
POST /api/sync (or auto_sync_loop every 1800 s)
        │
        ▼
gw.scrape_server_usage() ──► https://opencode.ai/workspace/{ws}/go   (HTML quota pct + credits)
ur.full_sync()           ──► https://opencode.ai/_server?id=<hash>   (usage.list + getCosts)
                         ──► https://opencode.ai/workspace/{ws}/billing (subscription anchor, cached 6 h)
        │
        ▼
usage_remote.db (usage_records / cost_summary / quota_snapshot / sync_meta)
        │
        ▼
_collect_rows → _apply_server_quota (official pct overrides local windows) → UI
```

Cloud formula chain: `config.formula_url` → `FormulaStore` (TTL 900 s, fallback to
`DEFAULT_FORMULA`) → `apply_params_to_gw()` mutates `gw` rule tables → `ViewEngine` rebuilt →
`/api/formula`, `/api/views`, `/api/view/*` and all calculations reflect the latest formula.

---

## 4. Local Data Sources

| Source | Path (Windows) | Access | Status |
|---|---|---|---|
| OpenCode usage DB | `~/.local/share/opencode/opencode.db` | read-only URI (`mode=ro`, timeout 15 s) | **Legacy schema only.** Current DBs have no `message` table → `read_opencode_all()` returns `[]` silently (`go-usage-widget.py:406-450`; verified on this machine) |
| Codex logs | `~/.codex/logs_2.sqlite` | read-only URI; incremental cursor `config.codex_log_id` | Works. Query filters `feedback_log_body LIKE '%response.completed%' AND '%usage%'`; parses SSE JSON (`go-usage-widget.py:453-503`) |
| Official mirror (legacy) | `<repo>/server_usage.db` | write via `server_data.py`; read-only read-back | Created only when legacy sync path runs |
| Official mirror (current) | `<repo>/usage_remote.db` | write via `usage_remote.py`; mostly read-only reads | Primary official store; `read_remote_*` helpers use `mode=ro` |
| Widget config | `<repo>/config.json` | read/write (plaintext JSON, `utf-8-sig`) | Holds `api_key`, `server.auth_cookie`, `workspace_id`, calibration, caches, `codex_log_id`, `credit_deductions`, `subscription.start` |
| OpenCode auth | `~/.local/share/opencode/auth.json` | read-only | `discover_go_key()` reads `opencode-go.key` as fallback API key |

Schema details that matter for Phase 2 (observed on the current OpenCode build):

- `session_v2`: `id, project_id, workspace_id, parent_id, fork_session_id, slug, directory,
  title, … cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read,
  tokens_cache_write, agent, model, time_created, …`
  - `model` is a JSON object string: `{"id":"deepseek-v4.1-flash","providerID":"opencode-go","variant":"high"}`.
  - `agent` holds the agent name (`orchestrator`, `explorer`, `fixer`, `oracle`, …); 19 of 25
    sessions have `parent_id` (subagent sessions linked to a parent session).
- `session_message`: `id, session_id, type, seq, time_created, time_updated, data` where
  `type ∈ {assistant, user, system, idle, synthetic}` and assistant `data` JSON contains:
  `time{created,streamed,completed}, agent, model{id,providerID,variant}, content, finish,
  cost, tokens{input,output,reasoning,cache}`.
  - Verified presence counts on this machine: `agent` in 476/476 assistant messages, `cost`
    and `tokens` in 471/476.
- There is **no** `message` table; the reader's `json_extract(data,'$.role')` filter also no
  longer matches the new `type` column convention.

Codex `logs_2.sqlite` schema: `logs(id, ts, ts_nanos, level, target, feedback_log_body,
module_path, file, line, thread_id, process_uuid, estimated_bytes)` — matches the widget's
query.

SQLite access notes: all reads use `file:...?mode=ro`; the only read-write opens are the
widget-owned DBs (`server_usage.db`, `usage_remote.db`). No WAL/locking issues observed in
code; writes are short transactions with `commit()`.

---

## 5. Remote Data Sources

| # | URL | Caller | Method | Sent | Received | Auth | Frequency | Failure / timeout |
|---|---|---|---|---|---|---|---|---|
| 1 | `https://opencode.ai/zen/go/v1/models` | `gw.fetch_go_model_list` (`go-usage-widget.py:359-384`) | GET | — | model list JSON | `Authorization: Bearer <api_key>` | ≤1/24 h (cached in `config.models_cache`) | `[]`; timeout 10 s |
| 2 | `https://opencode.ai/_server?id=<USAGE_LIST_HASH>&args=...` | `ur.sync_usage_list` / `sd.fetch_usage_list` | GET | workspace_id, page in query | Seroval stream (usage records) | `Cookie: auth=<cookie>`, `X-Server-Id` | per sync; ≤130 pages (300 full) | exception → sync stops; timeout 25 s |
| 3 | `https://opencode.ai/_server?id=<GET_COSTS_HASH>&args=...` | `ur.sync_costs` / `sd.fetch_costs` | GET | workspace_id, year, api_month, tz ∈ {−480, 480} | Seroval stream (monthly cost rows) | same | per sync | tries both tz; timeout 25 s |
| 4 | `https://opencode.ai/workspace/{ws}/go` | `gw.scrape_server_usage` | GET | — | HTML: usage-item pct + reset text + credits (`status:"applied"`) | `Cookie: auth=...` | per `/api/sync` + every 1800 s auto-sync | error dict; timeout 12 s |
| 5 | `https://opencode.ai/workspace/{ws}/billing` | `ur.fetch_subscription_start` | GET | — | HTML payment records `{id:"pay_…", timeCreated:…}` | `Cookie: auth=...` | cached 6 h; daily retry when ≥30 days old | `None`; timeout 15 s |
| 6 | `https://opencode.ai/auth` | `data_server.do_grab` fallback + Electron `open-login` | GET | — | redirect Location with `wrk_…` | `Cookie: auth=...` | only when workspace_id missing | ignored; timeout 10 s |
| 7 | `https://opencode-formula.opencode-widget.workers.dev/formula` (default; overridable via `config.formula_url` / env `FORMULA_URL`) | `views.FormulaStore.refresh` | GET | — | JSON formula (params/constants/formulas/views) | none | startup + every 900 s + on demand | keeps last good version; falls back to `DEFAULT_FORMULA`; timeout 5 s |

No retry/backoff logic beyond the periodic loops; all failures are silent (`except: pass`
style) by design. No request bodies are POSTed to opencode.ai; identifiers travel in query
strings (usage RPC) and cookies. Note: `USAGE_LIST_HASH` / `GET_COSTS_HASH` are
reverse-engineered SolidStart server-function hashes — they can change without notice when
opencode.ai redeploys.

---

## 6. Authentication & Secrets

| Secret | Read from | Stored at | Plaintext? | Returned by HTTP API? | Logged? | Git? | Third party? | Renderer access? |
|---|---|---|---|---|---|---|---|---|
| OpenCode Go API key | `config.api_key`, else `~/.local/share/opencode/auth.json` → `opencode-go.key` (`go-usage-widget.py:349-356`) | `config.json` (plaintext) | **Yes** | **Yes** — `GET /api/config` (`data_server.py:581-590`); also written via `POST /api/key` | No (only `key: bool` in `/api/state`) | No (gitignored) | Yes: sent as `Authorization: Bearer` to `opencode.ai/zen/go/v1/models` | **Yes** — filled into `#keyInput` (`index.html:1571-1572`) |
| auth cookie | Electron login window (`main.js:20-26`, `241-290`) or best-effort browser DB (`browser_cookie.py`) | `config.json` (plaintext); also Chromium `persist:opencode-auth` partition store | **Yes** in config | **Yes** — `GET /api/config` and `POST /api/grab` return the raw value | No | No | Yes: `Cookie:` header to opencode.ai endpoints | **Yes** — `widgetAPI.grabAuth()` returns it (`index.html:1590,1745`), filled into `#srvCookie` |
| workspace_id (`wrk_…`) | URL after login / redirect | `config.json`; returned by `/api/config` | Yes | Yes | No | No | Yes (URL path) | Yes |
| Chrome/Edge cookies | `%LOCALAPPDATA%\<browser>\User Data\<profile>\Network\Cookies` (+ `Local State` key) | temp copy `%TEMP%\opencode\cookie_<hash>.db` (never deleted) | encrypted values; DPAPI key | Only via `/api/grab` result | No | No | No | Only via grab flow |
| OpenCode service password | (not read by widget) | `~/.config/opencode/service.json` | — | — | — | No | — | No |
| Formula content | Cloudflare Worker | memory only (`FormulaStore._formula`), not persisted | n/a | `GET /api/formula` echoes params/views/formulas | No | `cloud/*` committed (no secrets) | Fetched from workers.dev | Yes via `/api/formula` |

Answers to the nine questions: (1) see table; (2) config.json + Electron partition + widget
DBs; (3) yes for config.json; (4) yes — `/api/config`, `/api/grab`, and `/api/formula` echo;
(5) no secret values are logged (only booleans/counts; `print` statements contain versions
and dates); (6) not committed — `.gitignore` covers `config.json`, `server_usage.db`,
`usage_remote.db`, `webdata/`, `logs/`; verified clone is clean; (7) yes — cookies/keys are
sent to opencode.ai only, as intended; (8) yes — the renderer can read cookie and API key
via IPC and `/api/config`; (9) yes — cross-origin risk exists today: `Access-Control-Allow-
Origin: *` on every response plus no auth/origin checks (see §7, P0).

---

## 7. HTTP API Audit

Server: `ThreadingHTTPServer(("127.0.0.1", 8765))` (`data_server.py:805`). No TLS, no auth,
no Origin/Referer validation, `log_message` disabled. All JSON responses carry
`Access-Control-Allow-Origin: *` (`data_server.py:635-644, 674-680, 715-721, 764-769`).

| Endpoint | Method | Handler | Auth | Response | Risk |
|---|---|---|---|---|---|
| `/api/health` | GET | `:715-721` | none | `ok` | negligible |
| `/api/state` | GET | `:664-680` | none | full computed state (windows/stats/history/suppliers/heatmap/credits/…); contains `key: bool`, not the key | read-only info; 30 s cache |
| `/api/config` | GET | `:681-682` → `_config_json` `:581-590` | none | **`api_key`, `server.auth_cookie`, `workspace_id` in plaintext**, calibration | **P0 secret disclosure** |
| `/api/formula` | GET | `:683-698` | none | version/source/url/error/params/views/formulas; `?refresh` forces fetch | formula metadata disclosure; no secrets |
| `/api/views` | GET | `:699-705` | none | view id/label/scope/range/group | low |
| `/api/view/<id>` | GET | `:706-714` | none | computed view totals/daily | low |
| `/api/key` | POST | `:732-735` | none | writes `config.api_key` | **write from any origin/local process** |
| `/api/server` | POST | `:736-742` | none | writes `config.server.auth_cookie` + `workspace_id` | **write; can repoint widget to attacker account** |
| `/api/calibrate` | POST | `:743-756` | none | writes calibration percentages | write |
| `/api/sync` | POST | `:757-758` → `do_sync` `:551-578` | none | triggers network sync with stored cookie | triggers cookie-bearing requests; CSRF-style |
| `/api/grab` | POST | `:759-760` → `do_grab` `:593-632` | none | **returns `auth_cookie` in the response body** and persists it | **P0 secret disclosure** |
| OPTIONS (any) | OPTIONS | `:764-769` | none | 204 + CORS `*` | enables cross-origin reads |

Why this is a real attack surface (not theoretical): the API has no authentication and
advertises `ACAO: *`, so any page loaded in a normal browser can `fetch('http://127.0.0.1:8765/api/config')`
and read the response. `http://127.0.0.1` is exempt from mixed-content blocking, so an HTTPS
page can reach it; even without CORS reads, DNS rebinding (attacker domain resolving to
127.0.0.1) makes requests same-origin. Modern Chrome's Local Network Access gating may add a
permission prompt in some versions, but that does not make the endpoint safe. Condition:
widget/data_server is running (including auto-start configurations). Impact: theft of the
opencode.ai session cookie and Go API key.

---

## 8. Electron Security Audit

- Window options (`electron/main.js:149-168`): `contextIsolation: true`,
  `nodeIntegration: false`, `transparent: true`, `frame: false`, `alwaysOnTop: true`.
  **No `sandbox: true`**; `webSecurity` left at default (on).
- Preload surface (`electron/preload.js:3-13`): `widgetAPI.{resize, openLogin, grabAuth,
  saveUiState, onSnapSmall, onSnapRestore, setClickThrough, exitSnap, quit}`. `grabAuth`
  returns the **raw auth cookie** to renderer JS. IPC handlers (`main.js:209-303`) do not
  validate the sender frame.
- Login window (`main.js:251-264`): loads `https://opencode.ai/auth` in
  `persist:opencode-auth`; **no `will-navigate` guard, no `setWindowOpenHandler`, no
  permission handler**. Cookie captured from the session cookie store
  (`main.js:20-26, 272-282`).
- Renderer (`index.html`): no CSP meta tag; no external resources (no CDN, no remote fonts);
  inline JS fetches only `http://127.0.0.1:8765` (plaintext). Many `innerHTML` sinks
  (e.g. `:743, :969, :1123, :1270, :1299, :1331, :1409, :1450`); model/supplier/display
  names come from the local API, which merges **cloud formula `display_names`** — a
  malicious/compromised formula source could inject markup into the renderer (which can call
  `widgetAPI.grabAuth()`).
- Diagnostics: `main.js:7-10` appends to `%TEMP%\widget_snap.log` on every drag; no rotation
  or cleanup.
- No devtools toggle or remote-debugging port is enabled by the app.

---

## 9. Formula System Audit

Mechanics:

- `views.FormulaStore` (`views.py:242-306`): fetch (`urllib`, 5 s timeout), `json.loads`,
  `_validate` (dict; `params` + `views` required; `formulas` must be dict if present),
  cache with TTL 900 s, `meta()` tracks `{source, url, error, fetched_at}`. On any failure it
  keeps the previous formula, else `DEFAULT_FORMULA` (v4, offline copy).
- `data_server.apply_params_to_gw` (`data_server.py:55-113`): overwrites `gw` module-level
  rule tables field-by-field; missing keys keep local defaults. **No per-key type guard** —
  e.g. `limits.session = "abc"` raises `ValueError` mid-apply (remaining keys are skipped).
  `PROVIDER_PREFIXES` is unioned (`|=`), so removed prefixes never disappear.
- `formula_registry.apply_formulas` (`formula_registry.py:407-434`): merges cloud
  `display/source/expr/params/used_by` metadata; `func` is always the local implementation;
  `compute()` (`:436-444`) executes only local functions.
- `ViewEngine` (`views.py:309-488`): executes view definitions (scope/range/group/post ops);
  `post` ops are a fixed enum (`meterRatio`, `round4`) — not code.

Answers to the requested questions:

1. What can a remote formula change? `params.limits`, `params.windows`, `params.meter`
   (ratio/rates/rate_default/rate_intercept), `params.sources`, `params.free_models`,
   `params.providers`, `params.prices`, `params.req_limits`, `params.tokens_per_req`,
   `params.model_quotas`, `params.display_names`, `constants`, `views` (labels, scopes,
   ranges, grouping, post ops), and formula metadata strings.
2. Can it execute code? **No.** No `eval`/`exec`/`new Function`/dynamic import anywhere
   (verified repo-wide). The Cloudflare worker embeds the JSON literal via
   `json.dumps(..., ensure_ascii=True)` (`cloud/build_worker.py:7-38`) — no injection vector.
3. Does it use eval/exec/dynamic import? No.
4. Is it just JSON config? Yes — pure data.
5. Schema validation? Minimal: presence of `params`/`views`; per-key isinstance checks in
   `apply_params_to_gw`; no value-range checks.
6. Version checks? `version` is read for change detection/logging only; **not pinned**.
7. Hash/signature? **None.**
8. Fallback on network failure? Keep last good version; else `DEFAULT_FORMULA`
   (`views.py:252-271`); silent.
9. Worst case of a malicious/corrupt formula? Manipulated displayed quotas/prices/percentages
   and labels; HTML-injection chain via `display_names` → renderer `innerHTML` (conditional on
   renderer sinks); a `ValueError` from a wrong-typed key aborts that apply pass (the
   previous formula stays in effect because apply happens before the engine swap? — note:
   `get_formula` calls `apply_params_to_gw` and only then rebuilds the engine; a raise
   propagates to the caller, and `build_state`/`formula_sync_loop` swallow it, so state
   computation falls back to previous tables/engine). No code execution, no file access.

Additional finding: the committed `cloud/formula-worker.js` embeds a **v4** payload while
`cloud/formula.json` is **v6** — the deployed worker may not match either; treat deployed
content as unverifiable from the repo (deployment drift risk).

Disabling remote formula is currently **not possible via config**: `formula_url: ""` falls
back to the default workers.dev URL inside `FormulaStore.__init__` (`views.py:245-246`)
because `"" or env or default` — only the `FORMULA_URL` env var can override, and there is no
"off" value. This is a Phase-2 requirement (§15 D).

---

## 10. Usage / Cost Calculation Audit

> Provenance discipline: "官方" below means the value/behavior is visible on opencode.ai or
> returned by its endpoints; "推算/经验校准" means the author reverse-engineered or
> calibrated it. Nothing in this repo is an official public API contract.

| 模块 | 输入 | 输出 | 数据来源 | 是否官方 | 是否推算 | 风险 |
|---|---|---|---|---|---|---|
| `meter.ratio` (RATE_DEFAULT 1.4212) | 行 cost | 官方口径 used = Σcost×ratio | `config`/cloud formula; code comment anchors it to a 2026-08-14 observation (`go-usage-widget.py:42-46`) | 否（数值来自官网对账观察） | **是**（明确反推：`used=70=49.2551×1.4212`） | 官方计价变化 → 所有进度百分比漂移；云端可改 |
| `RATE_INTERCEPT` | 常量 | 窗口 used 加截距 | cloud formula (default 0) | 否 | 是 | 影响 pct |
| `MODEL_RATES` | per-model rate | per-model 折算 | cloud `meter.rates`（默认空） | 否 | 是 | 同上 |
| `CREDIT_PER_APPLIED` ($5) | applied credits 数 | 抵扣额 | 官网 rewards `status:"applied", amount` (`gw.scrape_server_usage`) + `config.credit_deductions` 日期表 | 部分（$5 与官网一致） | 日期过滤/回退逻辑是推算 | 抵扣错算 → used 偏差 |
| `LIMITS` (12/30/60) | — | 窗口分母 | 官网进度条语境（$12/5h, $30/周, $60/月） | 数值来自官网展示 | 结构/行为是推算 | 官方调价 → 需云端更新 |
| `MODEL_QUOTAS` | model | 模型月额度 | 官网"使用额度"列 → 硬编码表 | 数值来自官网 | 表结构与缺省回退是推算 | 新模型缺省=60 → 可能错误 |
| `PRICES` | model+tokens | 本地估算 cost | 模型定价（含 high-context 档） | 数值来源官网/供应商 | 维护方式为手工表 | 价格变化 → 估算漂移（仅本地估算行使用） |
| `REQ_LIMITS` | model | [low,mid,high] 请求数 | 官网额度页 | 数值来源官网 | 是（表） | est_req_cost = quota/high 的推导 |
| `TOKENS_PER_REQ` | model | 平均 token/请求 | 官网"每次请求" | 数值来源官网 | 是 | 反推 token 配额误差 |
| session 窗口 5 h | ts | 滚动 5h used | 本地滚动近似；官网为周期制 | 官网为周期制 | **是**（滚动近似，登录后被官方 pct 覆盖） | 未登录时偏差 |
| weekly 窗口 | ts | 周一 00:00 本地起 7 天 | 本地近似；官网为"周期首请求锚定" | 否 | **是** | 同上 |
| monthly 窗口 | ts + 订阅锚点 | 订阅日锚定月窗 | `ur.fetch_subscription_start`（billing 页）→ `config.subscription.start` 兜底 | 订阅日期来自官网 | 锚定/回退策略是推算 | 抓取失败 → 窗口漂移 |
| 官方 pct 覆盖 | `/go` HTML pct | used = base×pct/100 | `_apply_server_quota` (`data_server.py:444-461`) | **是**（直接采用官网 pct） | 反推 used 是推算 | pct 解析失败 → 退回本地 |
| 校准 `calibration` | 用户输入 % | 抬高 used 至目标 | `config.calibration` | 否 | 用户手工 | 仅显示层 |
| `model_used` (go) | cost_map | 官方月成本 | `cost_summary`（官方 getCosts） | 是 | go 优先策略是设计选择 | cost_map 跨 key 合并 |
| token 配额反推 tq/tp | quota+均价 | token 配额/百分比 | 本地聚合 | 否 | **是** | 均价受窗口影响 |
| `effective_remain`/`remain_cnt` | model_remain/global_remain/avg_per_req | 剩余/剩余次数 | 计算 | 否 | 是 | 估算展示 |
| `cache_hit`, `rate` | tokens/dur | 命中率、tok/s | 本地 | 否 | 是 | 展示 |
| gateway 子集去重 | src=gateway | 从"全部"/窗口排除 | `SUBSET_SRCS` (`go-usage-widget.py:853-854`), `views._scope_rows` | 否 | **是**（本地网关镜像防双计） | 若网关不是镜像 → 少计 |
| free 判定 | model 名 | free/go 分组 | 后缀 + whitelist + `scan_free_models()` CLI 扫描 | 否 | **是** | 命名变化 → 误分组 |
| 官方/本地逐条匹配 | model+ts | own/other 分账 | `_split_own_other` ±120 s | 否 | **是**（经验窗口） | 见 §12 |

Key caution for Phase 2: the README's phrase "官方计量口径（1.4212）" describes an
**empirically fitted** ratio, not a documented official constant. Preserve this distinction
in any user-facing copy.

---

## 11. Provider / Model Normalization

- `PROVIDER_SRC` (`go-usage-widget.py:268-269`): `opencode→zen`, `opencode-go→go`,
  `openkilo→kilo`, `tencent-tokenhub→zen`, `openrouter→router`. Unknown providerIDs pass
  through unchanged (dynamic discovery) and are registered as model prefixes
  (`register_provider_prefix`, `:277-280`).
- `norm_model` (`:298-318`): loops stripping known `provider/` prefixes; normalizes
  `:free`/`/free` → `-free`; strips `-free` when the base is whitelisted; applies
  `MODEL_ALIASES`. Verified examples: `tencent/hy3:free → hy3-free`,
  `cohere/north-mini-code:free → north-mini-code-free`, `kilo-auto/free → kilo-auto-free`
  (kilo-auto is a model name), `nemotron-3-ultra-free → nemotron-3-ultra-550b-a55b-free`,
  unknown `unknown-provider/model-x` unchanged.
- `is_free_model` (`:289-295`): whitelist + suffixes; `FREE_EXCLUDE` is applied only in
  `scan_free_models`, not in `is_free_model` (router placeholders are excluded from the
  scanned list, not from live rows).
- Official rows mapping (`usage_remote.py:449`, `server_data.py:367`): `src = "go"` iff
  `provider.startswith("inf-go")`, **else `"zen"`** — official kilo/router/gateway rows (if
  any) would be classified as zen. Risk noted in §12.
- Current-schema gap: new `session_message.data.model` is an **object**
  (`{id, providerID, variant}`), while the old reader expects top-level `modelID`/`providerID`
  strings — the new reader must normalize this shape.

---

## 12. Account Split & Deduplication

`data_server._split_own_other` (`data_server.py:464-497`):

- Matches local rows to official rows by **raw `model` string** and `|Δts| ≤ 120 000 ms`
  (`_OWN_MATCH_MS`), one official record per local record (`used` set).
- Matched locals are dropped (official representation wins); unmatched locals are marked
  `account="other"` and appended (`extra`). With no official rows, **all** local rows become
  `other`.
- Progress windows (`build_windows.counted`) exclude `account=="other"` and `src in
  SUBSET_SRCS`; "all"-scope views include `other` (but exclude gateway subset for own
  account); `supplier_stats["all"]` excludes gateway own-account rows.

Risks:

1. **Raw model-name matching** — official `model` strings and local `modelID` strings are
   compared without `norm_model`; prefix/free-suffix differences silently prevent matching,
   inflating "other" (double counting in "全部").
2. **Window heuristic (±120 s)** — clustered usage can mis-pair; the bisect probes only
   `i-1, i, i+1` and takes the first candidate in that order, not the globally nearest.
3. **Provider mapping in official rows** (see §11) may collapse distinct providers into
   `zen`.
4. `_split_own_other` mutates input rows in place (marks `account`), so callers must not
   reuse the list expecting pristine state.

All four behaviors are now characterized by tests (`tests/test_account_split.py`), so any
Phase-2 change will show up as an intentional test update.

---

## 13. Test Coverage

Framework: **pytest** (repo previously had none). Run from the repo root:

```bash
python -m pytest -q
```

Environment note: on this machine pytest's default basetemp
(`%LOCALAPPDATA%\Temp\pytest-of-<user>`) was not writable; run with an explicit
accessible temp root, e.g.:

```powershell
$env:TEMP="$env:LOCALAPPDATA\Temp\opencode"; $env:TMP=$env:TEMP; python -m pytest -q
```

Isolation contract (enforced by `tests/conftest.py`, autouse):

- All user-data paths redirected to per-test `tmp_path`: `gw.CONFIG_PATH`,
  `gw.OPENCODE_DB`, `gw.CODEX_LOGS`, `gw.OPENCODE_AUTH`, `ur.REMOTE_DB`, `sd.DB_PATH`.
- `gw.scan_free_models` stubbed to `[]` (no subprocess), `read_auth_cookie_from_webdata`
  stubbed to `""` (no browser access).
- Rule-table globals snapshotted/restored around every test; `formula_registry` state
  restored; `data_server._SUB_START`/`CACHE` reset.
- No network: HTTP tests use an ephemeral `127.0.0.1` port bound to
  `data_server.Handler`; formula tests point `FormulaStore` at local fixture files;
  network-failure paths are simulated by monkeypatching `urlopen`.
- Fixtures are synthetic (`tests/fixtures/*`); disposable SQLite DBs are built per test
  (`tests/helpers.py`) — no real `opencode.db` / `auth.json` / cookies are read.

| Area (user spec) | Test file | Tests | Status |
|---|---|---|---|
| A. model normalization | `tests/test_model_normalization.py` | 10 | ✔ |
| B. cost calculation | `tests/test_cost_calculation.py` | 9 | ✔ |
| C. provider mapping | `tests/test_provider_mapping.py` | 4 | ✔ |
| D. time windows | `tests/test_time_windows.py` (+ view-cutoff cases in formula tests) | 12 | ✔ |
| E. credits | `tests/test_credits.py` | 6 | ✔ |
| F. account split / dedup | `tests/test_account_split.py` | 7 | ✔ |
| G. formula | `tests/test_formula_system.py` | 16 | ✔ |
| H. HTTP API | `tests/test_http_api.py` | 15 | ✔ |

Baseline result (2026-09-28, `main@37e399e`): **79 passed, 0 failed, 0 skipped**
(`python -m pytest -o addopts="" -q`, TEMP redirected to an accessible dir; exit code 0).
Characterization tests intentionally document current behavior that Phase 2 will change:
plaintext secrets in `/api/config`, `ACAO: *`, `apply_params_to_gw` raising on wrong-typed
values, and `read_opencode_all()` returning `[]` on the current schema.

---

## 14. Security Findings

### P0

**P0-1 — Localhost API discloses auth cookie / API key to any web origin (CORS `*` + no auth)**

- 位置: `data_server.py:581-590` (`_config_json`), `:635-644` (`_send_json` ACAO `*`),
  `:681-682` (`GET /api/config`), `:593-632` + `:759-760` (`POST /api/grab` returns cookie),
  `:764-769` (OPTIONS).
- 问题: `GET /api/config` returns `api_key` + `auth_cookie` + `workspace_id`; `POST /api/grab`
  returns the raw cookie; every response allows `Access-Control-Allow-Origin: *`; no
  authentication, no Origin/Referer check, no per-session token.
- 实际攻击面: any web page the user visits while the widget runs; any local process; DNS
  rebinding (`attacker.com → 127.0.0.1`) makes reads same-origin regardless of CORS.
- 成立条件: server running (default manual or auto-start usage) + user visits attacker page
  (or attacker has local code execution).
- 影响: opencode.ai session cookie theft (account/billing access), Go API key theft (quota
  consumption).
- 严重级别: **P0**.
- 推荐方案: runtime random token (generated at startup, passed to renderer via preload/IPC
  or query), Origin/Referer validation, remove `ACAO: *` (or restrict to `null`/file origin),
  never return secrets (`api_key_configured: true` style), make `/api/grab` write-only.
- 修改复杂度: 中（server + renderer + preload; ~150 LOC + frontend call sites).
- 兼容性影响: breaks third-party scripts that call the API directly (`gen_widget_xlsx.py`
  fetches `/api/state`); needs a documented token mechanism or localhost allowlist.

### P1

**P1-1 — Electron renderer can read all secrets; no CSP; unescaped HTML sinks fed by cloud data**

- 位置: `electron/main.js:241-290` (`grab-auth` returns cookie), `preload.js:3-13`,
  `index.html:1555-1572, 1590, 1745` (secrets into DOM), missing CSP in `index.html`,
  `innerHTML` sinks `:743, :969, :1123, :1270, :1299, :1331, :1409, :1450`.
- 问题: cookie/API key live in renderer-reachable places; no CSP; display strings
  (including cloud formula `display_names` and DB model names) are interpolated into
  `innerHTML`.
- 实际攻击面: renderer XSS (via formula-driven labels or tampered local API data) can call
  `widgetAPI.grabAuth()` and exfiltrate secrets; any future XSS inherits full secret access.
- 成立条件: malicious/compromised formula source or a local attacker who can tamper with
  API data; requires renderer execution context.
- 影响: cookie/key theft; window manipulation (quit, resize) via exposed API.
- 严重级别: **P1**.
- 推荐方案: add CSP meta; replace `innerHTML` interpolation of data strings with
  `textContent`/escaping; keep secrets in main process (never return raw values to renderer);
  validate IPC sender.
- 修改复杂度: 中（frontend + main.js）.
- 兼容性影响: renderer must stop relying on `/api/config` echoing secrets; UI flows for
  "show configured key/cookie" need redesign (masked display).

**P1-2 — Secrets at rest in plaintext (`config.json`)**

- 位置: `go-usage-widget.py:19, 321-333` (`CONFIG_PATH`, `load_config`, `save_config`);
  `data_server.py:732-742` (writes).
- 问题: `auth_cookie`, `api_key`, `workspace_id` stored as plaintext JSON in the repo
  directory; Electron partition store additionally persists the cookie (Chromium-encrypted).
- 实际攻击面: any user-level process/malware; cloud-synced or backed-up folders; accidental
  copies. Not in Git (verified).
- 成立条件: local access to the user profile.
- 影响: same as P0-1 if leaked.
- 严重级别: **P1**.
- 推荐方案: encrypt secret fields with Windows DPAPI (`CryptProtectData`, ctypes — no new
  dependency) or move secrets to Electron `safeStorage` managed by the main process; keep
  non-secret config plaintext for debuggability.
- 修改复杂度: 中.
- 兼容性影响: old configs must migrate transparently (read plaintext, rewrite encrypted).

### P2

**P2-1 — Remote formula trust: no signature/version pinning; cannot be disabled**

- 位置: `views.py:242-306`, `data_server.py:47-130, 133-153`, `cloud/build_worker.py`.
- 问题: fetched formula is trusted data; no hash/signature/version pin; `formula_url: ""`
  silently falls back to the default worker URL; a wrong-typed param raises mid-apply;
  committed worker artifact is v4 vs `formula.json` v6.
- 实际攻击面: compromised/malicious formula source or MITM on plaintext? (workers.dev is
  HTTPS); supply-chain compromise of the author's worker.
- 成立条件: control of the formula URL content.
- 影响: manipulated quotas/prices/labels (financial display integrity); display_names → HTML
  injection chain (see P1-1); no code execution.
- 严重级别: **P2**.
- 推荐方案: `formula_enabled` flag, `source/version/hash/last_updated/fallback_status`
  surfaced in `/api/formula` and UI, per-key type+range validation, optional version pin.
- 修改复杂度: 小-中.
- 兼容性影响: additive; default behavior unchanged.

**P2-2 — Account split/dedup heuristics can misattribute usage**

- 位置: `data_server.py:464-497`; `usage_remote.py:449`; `server_data.py:367`.
- 问题: raw model-string matching, ±120 s window, first-candidate bisect, non-go official
  providers mapped to `zen`.
- 实际攻击面: not adversarial — data-integrity risk.
- 成立条件: model naming differences between official and local rows; clustered usage;
  official non-go providers.
- 影响: wrong own/other attribution → wrong progress percentages and "全部" totals.
- 严重级别: **P2**.
- 推荐方案: normalize both sides with `norm_model` before matching; choose nearest candidate;
  add diagnostics counters; keep the current tests as regression protection.
- 修改复杂度: 小.
- 兼容性影响: numbers may change (should become more accurate); document in release notes.

**P2-3 — Local OpenCode reader silently returns zero rows on current schema**

- 位置: `go-usage-widget.py:406-450`.
- 问题: queries `message` table that no longer exists in current OpenCode builds; exception
  swallowed → `[]` (verified live).
- 实际攻击面: none (correctness).
- 成立条件: OpenCode versions using `session_message`/`session_v2` schema.
- 影响: local usage invisible (only official sync data shows); Agent statistics impossible
  until fixed.
- 严重级别: **P2**（功能/数据完整性）.
- 推荐方案: new schema adapter (see §17/§19) with legacy fallback; keep current test.
- 修改复杂度: 中.
- 兼容性影响: additive; must keep legacy `message` support for older OpenCode builds.

### P3

**P3-1 — Browser-cookie DB copies left in `%TEMP%`**
`browser_cookie.py:258-288` copies the browser cookie DB to
`%TEMP%\opencode\cookie_<hash>.db` and never deletes it. Values remain OS-encrypted; still a
hygiene issue. Fix: delete after read / use in-memory temp; complexity: trivial.

**P3-2 — Debug log growth**
`electron/main.js:7-10` appends window-position logs to `%TEMP%\widget_snap.log` without
rotation (marked "临时诊断"). Fix: remove or rotate; complexity: trivial.

**P3-3 — Missing Electron hardening flags**
No `sandbox: true`, devtools not disabled, no `setWindowOpenHandler` on either window
(`main.js:149-168, 251-264`). Low direct impact given `contextIsolation`; fix during any
Electron work; complexity: small.

**P3-4 — No rotation/retention for widget-owned DBs**
`usage_remote.db` grows unbounded (`quota_snapshot` appends on every sync; 1800 s cadence →
~48 rows/day). Housekeeping suggestion; complexity: small.

---

## 15. Technical Debt

1. **Silent failure culture**: nearly all data paths use `except Exception: pass/[]/None`,
   making the broken local reader invisible (P2-3) and formula misconfiguration hard to
   diagnose. Recommendation: structured diagnostics counters, not exceptions.
2. **Duplicated code**: `SerovalParser` + `call_rpc` duplicated in `server_data.py` and
   `usage_remote.py`; `DEFAULT_FORMULA` params duplicated between `views.py` and
   `cloud/formula.json` and partially in `go-usage-widget.py` rule tables (three sources of
   truth kept in sync manually).
3. **Global mutable rule tables** in `gw` mutated by cloud params at runtime — order-
   dependent and hard to test; current tests snapshot/restore, but a Phase-2 refactor should
   introduce an explicit config object passed to calculations.
4. **Hyphenated module name** (`go-usage-widget.py`) forces `importlib.util.spec_from_file_location`
   everywhere (data_server, tests, xlsx tool). Renaming would break compatibility with
   existing installs/scripts; keep, but isolate loading in one helper.
5. **Frontend monolith**: `index.html` ~1810 lines of inline JS/CSS; every UI feature
   increases regression risk. Suggest extracting `app.js`/`app.css` files (no framework) in
   Phase 2F, with manual smoke tests.
6. **No dependency pinning**: Python stdlib only (good), but Electron `^43.3.0` and
   `package-lock.json` present; no requirements file needed for Python, document the
   supported Python version (3.11+ observed).
7. **Stale committed artifacts**: `cloud/formula-worker.js` (v4) vs `cloud/formula.json`
   (v6); README references v4 in places. Either regenerate in CI or drop the artifact.
8. **`gen_widget_xlsx.py` writes to Desktop** with timestamped names; not part of the
   runtime, but shares formula logic and depends on a running server.

---

## 16. Secondary Development Feasibility (requested features)

| # | Feature | Feasibility | Key evidence / gap |
|---|---|---|---|
| 1 | Agent-level usage statistics | **Directly implementable** (after new reader) | `session_message.data.agent` (476/476 assistant rows on this machine) |
| 2 | Main / Worker / Oracle / Reviewer classification | **Directly implementable** | `agent` names + `session_v2.parent_id` hierarchy (19 child / 6 top-level sessions observed) |
| 3 | Agent → model mapping | **Directly implementable** | per-message `model{id,providerID,variant}`; session-level `model` JSON |
| 4 | Agent token / cost | **Directly implementable** | per-message `tokens`, `cost`; session aggregates in `session_v2` |
| 5 | Agent call counts | **Directly implementable** | count assistant `session_message` rows per `agent`/session |
| 6 | Oh My OpenCode Slim routing display | **Implementable, read-only** | `~/.config/opencode/oh-my-opencode-slim.json` → `preset` + `presets.<preset>.<agent>.{model,variant}` (verified) |
| 7 | Go / DeepSeek / GLM / GPT classification | **Directly implementable** | `model.id` prefixes + `providerID`; existing display-name tables |
| 8 | 5h burn rate | **Implementable** | message timestamps + cost; existing rolling-window helpers |
| 9 | Remaining quota forecast | **Implementable** | existing windows + linear projection; needs burn-rate history |
| 10 | Quota threshold alerts | **Implementable** | Electron `Notification` in main process; thresholds in config |
| 11 | Windows tray | **Implementable** | Electron `Tray` (main.js); needs icon asset |
| 12 | Autostart | **Implementable** | `app.setLoginItemSettings` or registry; VBS startup-folder path already documented |
| 13 | Safer secret storage | **Implementable** | DPAPI via ctypes (Python side) or Electron `safeStorage` (main side); see §15 |
| 14 | Remote formula on/off switch | **Requires change** | currently impossible via config (`""` → default URL, `views.py:245-246`) |
| 15 | Formula version/hash display | **Implementable** | `version` exists; add SHA-256 of payload + `fetched_at` (already in `meta()`) |

---

## 17. Agent-level Usage Feasibility

**Can agents be reliably identified? Yes — on current OpenCode schemas.** Evidence:

- `session_message.data.agent` present in **476/476** assistant messages
  (`type='assistant'`) on this machine; values are agent names.
- `session_v2.agent` carries the session-level agent; observed values include
  `orchestrator`, `explorer`, `fixer`, `oracle`, `librarian`, `observer`, `designer`, `build`.
- `session_v2.parent_id` links subagent sessions to parents (19/25 sessions on this machine);
  combined with `agent`, this yields Main → Worker/Oracle/Reviewer structure directly.
- `session_message.data.model` = `{id, providerID, variant}`; `cost` and `tokens` are
  per-message. `session_v2` also aggregates `cost`, `tokens_input/output/reasoning/
  cache_read/cache_write`.

**What is missing / required before implementation:**

1. **A new reader.** The production reader currently targets the removed `message` table
   (P2-3); Phase 2C must add a `session_message`/`session_v2` adapter while keeping the
   legacy path for older OpenCode builds.
2. **Agent classification mapping.** Agent names are open-ended (`build`, custom agents);
   Main/Worker/Oracle/Reviewer grouping needs a configurable mapping with an "unknown"
   fallback (do not hardcode).
3. **Session↔message join.** Messages carry `session_id`; session rows carry `parent_id` and
   `agent`; verify whether message `agent` can differ from session `agent` (subagent dispatch
   inside a session) and decide precedence.
4. **Variant semantics.** `variant` (high/max/low/xhigh/…) exists per model; useful to
   distinguish configured vs actual routing, but its meaning is OpenCode-internal.
5. **Data volume.** Message-level rows can be large; aggregation should stay SQL-side.

---

## 18. Oh My OpenCode Slim Integration Feasibility

Research only — **no Slim configuration was modified** (read-only inspection).

- Config location: `~/.config/opencode/oh-my-opencode-slim.json`; active `preset` (e.g.
  `opencode-go`); per-agent entries under `presets.<preset>.<agent>` with `model`
  (`providerID/modelID`, e.g. `opencode-go/deepseek-v4.1-flash`), `variant`
  (`high`/`max`/`low`/`xhigh`), `skills`, `mcps`.
- Main OpenCode config: `~/.config/opencode/opencode.json` (plugin registration, agent
  disables); `tui.json` pins the Slim version; `service.json` holds an OpenCode service
  password (not touched by the widget).
- Read-only reading is trivial (plain JSON, stable schema published via `$schema`).
- **Configured vs actual model can be distinguished**: configured values live in the Slim
  JSON; actual per-call values live in `opencode.db` (`session_message.data.model` /
  `session_v2.model`). The widget can show actual usage and optionally annotate divergence
  from configuration (e.g. expected `gpt-6-sol` but calls recorded on
  `deepseek-v4.1-flash`).
- Real call records are available per agent/message, so "actual usage" displays are
  feasible without changing Slim.

---

## 19. Recommended Architecture Evolution

1. **Reader layer** (`sources/` or functions in `go-usage-widget.py`):
   `read_opencode_legacy()`, `read_opencode_current()` (session_message/session_v2),
   `read_codex()`, `read_official_remote()` → canonical row with optional
   `agent/session_id/parent_id/model_variant` fields (additive; old keys unchanged).
2. **Config/secret layer**: `SecretStore` (DPAPI) wrapping `config.json`; `/api/config`
   returns only `*_configured` booleans + non-secret fields.
3. **API hardening middleware**: runtime token + Origin validation + CORS restriction; a
   single `_authorized(self)` check in the handler; token delivered to Electron via preload
   (env/IPC), and to `gen_widget_xlsx.py` via a documented local mechanism.
4. **Formula trust**: `formula_enabled` + pin/version + SHA-256 + per-key type validation +
   `/api/formula` reporting `{enabled, source, version, hash, last_updated, fallback_status}`.
5. **Agent analytics module**: aggregation over the new reader (per agent/model/day/cost/
   tokens/calls) served via a new `/api/agents` endpoint; Slim config read-only join for
   configured-vs-actual comparison.
6. **Forecasting module**: burn-rate windows + simple projections; alert thresholds.
7. **Frontend**: extract inline JS into `app.js` incrementally; add agent dashboard and
   forecast cards without changing existing panels; sanitize all data interpolation.

---

## 20. Phase 2 Implementation Plan (not executed)

### Phase 2A — Security hardening

- 目标: eliminate P0/P1 secret exposure; no functional regression.
- 涉及文件: `data_server.py`, `electron/main.js`, `electron/preload.js`,
  `electron/app/index.html`, `go-usage-widget.py` (config helpers), `gen_widget_xlsx.py`.
- 新增文件: `tests/test_api_auth.py` (token/origin cases), optional `secrets_store.py`.
- 风险: renderer/API protocol change; third-party consumers of `/api/state`.
- 测试: extend HTTP tests (token required, bad origin rejected, `/api/config` no secrets);
  manual smoke test of widget flows (login, sync, calibrate, key).
- 验收标准: `GET /api/config` returns no secret values; cross-origin read of any endpoint
  fails; widget still functions; tests green.
- 上游兼容: breaking for direct API consumers — document token mechanism; keep an opt-out
  env flag for local tooling.

### Phase 2B — Test coverage expansion

- 目标: protect new readers and hardening changes; raise coverage of `views.py` execution
  paths (`_cutoff` boundaries, dynamic view parsing) and `usage_remote.py` parsing.
- 涉及文件: `tests/*` only (+ fixtures).
- 风险: none to production.
- 测试: `python -m pytest -q` in CI-style run.
- 验收标准: new modules have failing-first tests before implementation; suite green.
- 上游兼容: none.

### Phase 2C — Agent usage statistics

- 目标: read `session_message`/`session_v2` (agent, model, cost, tokens, parent_id);
  aggregate per agent/model/day; new `/api/agents`.
- 涉及文件: `go-usage-widget.py`, `data_server.py`, `electron/app/index.html`.
- 新增文件: `tests/test_agent_reader.py`, `tests/fixtures` additions.
- 风险: schema drift across OpenCode versions; large DB scan performance.
- 测试: legacy + current schema fixtures; aggregation correctness; empty-agent fallback.
- 验收标准: widget shows per-agent usage matching manual SQL on the current DB; legacy
  installs unaffected.
- 上游兼容: additive endpoints/fields; keep old reader path.

### Phase 2D — Slim integration

- 目标: read-only Slim config → configured model per agent; join with actual per-agent model
  usage; show divergence.
- 涉及文件: new `slim_config.py` (read-only), `data_server.py`, frontend.
- 风险: Slim schema changes; missing config → graceful empty state.
- 测试: fixture Slim JSON variants (missing file, missing preset, unknown agent).
- 验收标准: dashboard shows configured vs actual with no writes to `~/.config/opencode`.
- 上游兼容: additive.

### Phase 2E — Usage forecasting

- 目标: 5h burn rate, remaining-time/amount projection, threshold alerts.
- 涉及文件: `go-usage-widget.py` (windows), `data_server.py` (`/api/state` fields),
  `electron/main.js` (notifications), frontend.
- 风险: noisy alerts; projection quality with sparse data.
- 测试: deterministic burn-rate fixtures; alert threshold unit tests (JS or main-process
  logic extracted for testability).
- 验收标准: burn rate matches manual calculation on fixture rows; alerts fire once per
  threshold crossing.
- 上游兼容: additive.

### Phase 2F — UI enhancement

- 目标: agent dashboard, forecast cards, formula trust panel (version/hash/fallback),
  extract inline JS to `app.js` incrementally.
- 涉及文件: `electron/app/index.html` (+ new `app.js`/`app.css` if extracted), `main.js`
  (tray, autostart).
- 风险: regressions in the 1810-line single file; extract in small verified steps.
- 测试: manual smoke checklist per widget size; sanitize interpolation.
- 验收标准: no visual regression on the three window sizes; new panels toggleable.
- 上游兼容: visual-only; no API break.

---

## Appendix A — Repository State (Phase 1)

```
branch:        main
commit:        37e399e343789a5e7efd92c5cab626527f2bf05c
tracked files: unmodified (git diff empty) — no production file was changed
working tree:  only new untracked additions:
               ?? docs/   (this audit)
               ?? pytest.ini
               ?? tests/  (conftest, helpers, fixtures, 8 test files; 79 tests)
```

## Appendix B — What was NOT done (Phase 1 constraints)

- No production logic modified; no refactor; no rename; no billing/quota/meter/provider
  changes; no UI/Electron changes; no new features; no real account login; no real cookie
  grab; no real sync; no secrets committed.
- Test-only additions: `pytest.ini`, `tests/` (conftest, helpers, fixtures, test files),
  and this document under `docs/`.
