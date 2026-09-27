# Phase 2 Core Implementation Report

> Scope: **Phase 2A (security hardening) + Phase 2B (regression tests & secret storage) + Phase 2C (current OpenCode schema reader + raw agent statistics backend)**.
> Phase 2D (Slim integration), 2E (forecasting), 2F (UI redesign) were **not started** — see §13.
>
> Branch: `dev/phase2-core` · Base: `37e399e` (`main`) · Head: see git log below
> Tests: **140 passed / 0 failed / 0 skipped** · Smoke: server 12/12 PASS, Electron PASS, login window PASS

Commits (in order):

```text
7315b6d chore: ignore pytest cache and local secret artifacts
adc0a83 test: add phase2 security regression cases          (TDD: red until the fixes below)
e22dfc7 fix: make remote formula opt-out explicit
e2a2820 fix: harden localhost API secret handling
f5305a7 fix: secure Electron renderer boundaries
0f88a7b feat: support current OpenCode usage schema
6ba7597 feat: expose raw agent usage statistics
909a704 feat: add DPAPI secret storage and fail-safe plaintext migration
```

---

## 1. Security Changes (Phase 2A)

| Area | Before | After | Where |
|---|---|---|---|
| `GET /api/config` | returned `api_key` + `auth_cookie` + `workspace_id` in plaintext | returns `{api_key_configured, auth_configured, workspace_configured, workspace_id, calibration}` — **no secret values** | `data_server.py:_config_json` |
| `POST /api/grab` | returned the raw cookie in the response | captures + persists server-side, returns `{ok, auth_configured, workspace_configured}` only | `data_server.py:do_grab` |
| CORS | `Access-Control-Allow-Origin: *` on every response | never `*`; echoes only `null` / `http://127.0.0.1:<port>` / `http://localhost:<port>`; disallowed Origin → 403; no Origin → no ACAO header | `data_server.py:_send_cors_headers/_origin_allowed` |
| Host | not checked | `127.0.0.1`/`localhost` + actual bound port only, else 403 | `data_server.py:_host_ok` |
| Authentication | none | `Authorization: Bearer <runtime token>` on **all** endpoints except `GET /api/health` and `OPTIONS`; `hmac.compare_digest`; 401 otherwise | `data_server.py:_auth_ok/_guard` |
| Remote formula | could not be disabled (`formula_url: ""` fell back to the default worker URL) | explicit `formula_enabled: false` → **no network request ever**, local default only; provenance fields (`enabled/hash/last_updated/fallback`) exposed | `views.py:FormulaStore`, `data_server.py` |
| Tooling | `gen_widget_xlsx.py` fetched `/api/state` anonymously | reads `runtime.json` and sends the Bearer token | `gen_widget_xlsx.py` |

`workspace_id` is deliberately still returned by `/api/config`: it is an account identifier required to prefill the manual server-config dialog, not a credential. The secret boundary is enforced by the runtime token, not by hiding identifiers (comment in code).

## 2. Runtime Authentication Design

- **Token**: `secrets.token_urlsafe(32)` → 43 chars, ≥256-bit entropy; generated lazily once per server process (`_TOKEN` / `ensure_runtime_token()`).
- **Delivery**: the server atomically writes `runtime.json` (`tmp + os.replace`) **before** `serve_forever()`:

  ```
  dir  = $OPENCODE_WIDGET_RUNTIME_DIR  or  %TEMP%\opencode-widget
  file = runtime.json
  body = {"token": "...", "port": 8799, "pid": 1234, "created": <ms>}
  ```

  Electron main reads it (retry up to ~10 s) and exposes `{base, token}` to the renderer via the `api-env` IPC channel; the renderer uses one `apiFetch()` helper for all API calls. The token is held in renderer memory only (never localStorage, never logged).
- **Constant-time compare**: `hmac.compare_digest`.
- **Non-exposure**: the token is never logged, never returned by any endpoint (including errors), and never stored in config.
- **Health stays anonymous** (`GET /api/health`) so Electron/scripts can wait for readiness without the token; it is still Host/CORS guarded.
- **Port override**: `OPENCODE_WIDGET_PORT` (needed because this machine's port 8765 is held by an unrelated IME process; also useful for isolated smoke runs).
- Rationale for `null` in the CORS allowlist: the renderer is a `file://` page, whose Origin is `null`; the token — not CORS — is the security boundary. Disallowed *named* origins still get 403.

## 3. Secret Storage Design

`secret_store.py` (new, stdlib-only):

- **Backend interface** `SecretBackend.encrypt/decrypt`; default = `DPAPIBackend` on Windows (`CryptProtectData` / `CryptUnprotectData`, `CRYPTPROTECT_UI_FORBIDDEN`, no entropy, buffers freed via `LocalFree`). Ciphertext is bound to the current Windows user. No new dependencies.
- **File**: `OPENCODE_WIDGET_SECRET_FILE` env override, else `<repo>/secrets.enc` (gitignored). Format `{"v": 1, "data": "<base64>"}`, atomic write, best-effort `chmod 0600`.
- **API**: `load_secrets() -> dict`, `save_secrets(dict)`, `store_available()`, `set_backend/get_backend/reset_backend` (tests install a deterministic fake backend — no test ever touches real DPAPI except one explicit dummy-data roundtrip test).
- **Failure semantics**: any backend/file failure raises `SecretStoreError`; callers never lose data (see §4).

## 4. Plaintext Migration Behavior

`gw.load_config()` / `gw.save_config()` integrate the store while keeping the returned config shape identical for all existing callers (`cfg["api_key"]`, `cfg["server"]["auth_cookie"]` still readable server-side):

- **Detection**: non-empty `api_key` or `server.auth_cookie` in `config.json`.
- **Fail-safe migration** on load: write merged secrets → re-read and verify → **only then** rewrite `config.json` without the secret fields. Any failure leaves `config.json` byte-identical and keeps returning the plaintext values (tested).
- **Idempotent**: the second load performs no migration.
- **Clearing**: `save_config` with an empty secret clears that entry in the store (previous semantics preserved); non-empty values are stored encrypted and stripped from `config.json`.
- **Fallback**: if no backend is available (non-Windows / DPAPI init failure) or the store write fails, the widget falls back to plaintext `config.json` — no data loss, documented limitation.
- **Remaining plaintext**: `workspace_id`, calibration, caches, `formula_url`, `subscription.start`, `credit_deductions` stay in `config.json` by design (non-secret). `secrets.enc` and `config.json` are both gitignored.

## 5. Electron Hardening

- **CSP** (index.html `<head>`):
  `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src http://127.0.0.1:* http://localhost:*; img-src data:; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'`
  (`'unsafe-inline'` is required by the single-file frontend; **no `unsafe-eval`**, no external resources).
- **Windows**: `sandbox: true` on both windows (contextIsolation stays on, nodeIntegration off).
- **Navigation**: widget window denies all navigation and all new windows; the login window allows only `https://opencode.ai/*` navigation and denies new windows.
- **Secret boundary**: `grab-auth` now POSTs the captured cookie to `/api/server` **from the main process** and returns only status flags — the cookie never enters the renderer. `/api/config` no longer prefills secrets; key/cookie inputs show "已配置（留空则不修改）" / "未配置", empty submits are no-ops (no accidental wipe), non-empty submits clear the field afterwards; no secret is logged or stored in localStorage.
- **`innerHTML` hygiene**: added `esc()` and wrapped all data-derived strings (model/display names, provider labels, reset text, tooltip/heatmap labels, tab labels, dates) in the affected sinks; trusted literals/numbers unchanged; visuals identical.
- Verification: `node --check` on main.js, preload.js and the extracted inline script (all pass); grep checks (0 hardcoded API URLs left, no `unsafe-eval`, guards present, no cookie-returning IPC).

## 6. Schema Detection

`gw.detect_opencode_schema(db_path=None)` (read-only, `mode=ro`):

- Returns `{"schema": "legacy"|"current"|"unsupported"|"missing", "status": "ok"|"missing_db"|"unsupported_schema"|"error", "error", "tables"}`.
- `current` requires both `session_message` and `session_v2`; if the legacy `message` table also exists, `current` wins and `legacy_also_present: true` is reported.
- Errors contain the exception type only (no paths, no data).
- **No more silent `[]`**: `read_opencode_usage()` returns `(rows, meta)` with explicit `schema/status/row_count/skipped_rows/error`; `/api/state` now includes this `reader` object.

## 7. Legacy / Current Compatibility

- `read_legacy_usage()` — the original `message`-table logic, byte-for-byte semantics (skip rows without `providerID`/`time.created`, provider→src mapping, dynamic prefix registration), plus normalized extra fields (`agent=None`, `session_id=None`, `parent_session_id=None`, `provider_id`, `model_id`, `variant=None`).
- `read_current_usage()` — `session_message` (assistant rows) joined with `session_v2` (parent/agent): message-level `agent` wins over session-level; `model` may be a dict or JSON string; tokens normalized; `dur_ms` from `time.completed - time.created`; providerID required (mirrors legacy semantics).
- `read_opencode_all()` remains as a rows-only wrapper for old callers.
- All reads use `file:...?mode=ro`; a test asserts the DB file hash is unchanged after reading.

## 8. Normalized Usage Record

Every reader row (additive to the existing keys used by billing logic — `ts, cost, model, tokens, src, dur_ms`):

| Field | Source |
|---|---|
| `ts` | message `time.created` (ms) |
| `cost` | message `cost` (float; 0.0 fallback) |
| `model` / `model_id` | `model.id` (legacy: `modelID`) |
| `provider_id` | `model.providerID` (legacy: top-level `providerID`) |
| `variant` | `model.variant` (current schema only) |
| `agent` | message `agent` → session `agent` → `None` |
| `session_id` / `parent_session_id` | `session_message.session_id` / `session_v2.parent_id` |
| `tokens` | `{input, output, reasoning, cache:{read, write}}` normalized |
| `src` | `PROVIDER_SRC` mapping, unknown providerID passes through |
| `dur_ms` | `time.completed - time.created` when positive |

Missing fields stay `None`/0 — nothing is guessed.

## 9. Agent Statistics API

`GET /api/agents` (token required, like every non-health endpoint):

- Query: `?range=today|7d|30d|all` (invalid → `all`).
- Range reuse: the endpoint calls `get_formula()`, wires `period_start` via the existing `_subscription_start()` mechanism and filters with `engine._cutoff(range)` — the **same** window mechanism as views; no new date algorithm.
- Data source: **local OpenCode DB records** (`gw.read_opencode_usage()`, one read per request). Rationale: official synced rows carry no agent attribution, so agent statistics must come from local records; the official/local account split is deliberately not applied here (no billing numbers change).
- Aggregation (`gw.agent_stats`): raw `agent` names first-class; `None` → `"unknown"` (preserved, never dropped); optional `config.agent_groups` maps agent→group, missing → `"unmapped"`; per agent: `requests`, `cost`, `tokens{total,input,output,cache_read,cache_write}`, and a `models` breakdown (`model/provider/variant` + per-model numbers), sorted by cost desc. **No Main/Worker/Oracle hardcoding** — semantics are deferred to Phase 2D.
- Response shape:

```json
{
  "reader": {"schema": "current", "status": "ok", "row_count": 814, "skipped_rows": 0, "error": null},
  "range": "all",
  "agents": [
    {"agent": "orchestrator", "group": "unmapped", "requests": 123, "cost": 0.0,
     "tokens": {"total": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
     "models": [{"model": "deepseek-v4.1-flash", "provider": "opencode-go", "variant": "max", "requests": 40, "cost": 0.0, "tokens": {}}]}
  ]
}
```

## 10. Formula Opt-Out / Hash

- `FormulaStore(url=None, ttl=900, enabled=True)`; `enabled=False` never calls `_fetch` (test monkeypatches `urlopen` to raise if called).
- Config semantics: `formula_enabled: false` disables remote formula entirely (source `disabled`, local default used). `formula_url: ""` + enabled true keeps the old default-URL behavior.
- Provenance in `/api/formula`: `enabled`, `source`, `version`, `hash` (SHA-256 of the raw fetched payload; canonical JSON hash for the built-in default), `last_updated`, `fallback`.
- **No signature scheme** was invented (explicitly deferred); hash is an integrity/visibility aid, not a trust anchor.

## 11. Tests

- **140 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`; on this machine redirect `TEMP`/`TMP` because pytest's default basetemp ACL is broken).
- New since the Phase-1 baseline (79): `test_security_api.py` (9), `test_formula_optout.py` (6), `test_opencode_reader.py` (24), `test_agents_api.py` (6), `test_secret_store.py` (16) = **61 new**, plus updates to `test_http_api.py` (token/CORS/config shape), `conftest.py`/`helpers.py` (isolation: runtime dir, secret file, fake backend), `test_provider_mapping.py` (the old "current schema returns []" characterization test was intentionally rewritten — comment: `# Phase 2C: current schema is now supported`).
- Isolation guarantees unchanged: tmp paths for all user data, stubbed subprocess/browser access, no network (formula fixtures + monkeypatched `urlopen`), ephemeral HTTP port.
- **Smoke (real environment, minimal)**:
  - Server (`smoke_server.py`, port 8799): 12/12 PASS — runtime token + pid match, health anonymous, 401 without/wrong token, 200 with token, `reader.schema=current` (814 rows, 0 skipped on the live DB), config flags-only, formula provenance, `/api/agents` 200 (8 agents), Host/CORS behavior.
  - Electron (CDP): page loaded (`file://.../index.html`), `window.widgetAPI` bridge present, `apiEnv` returned `{base, token}`, renderer `fetch /api/state` → 200 (`reader.schema=current`, 814 rows), `/api/config` → flags-only keys.
  - Login window: `grabAuth()` opened `https://opencode.ai/auth` (no login completed, no cookies captured).

## 12. Known Limitations

1. **CORS `null` origin** is allowed (file:// renderer requirement); CORS is not the security boundary — the runtime token is.
2. **Runtime token file** lives in the per-user temp dir and is readable by local processes of the same user (same trust level as `config.json`). It is not logged and never returned by the API.
3. **CSP** uses `'unsafe-inline'` for script/style (single-file frontend); no `unsafe-eval`, no external resources. Extracting inline JS is Phase 2F work.
4. **`workspace_id`** remains in `/api/config` and `config.json` (identifier, needed by the UI; documented decision).
5. **Formula hash ≠ signature**; no version pinning yet. A malicious formula source could still manipulate displayed numbers (no code execution, verified in Phase 1).
6. **Agent stats are local-record based**; if the local DB schema changes again, the reader status will surface it explicitly rather than silently returning 0. Official synced rows cannot contribute agent attribution.
7. **`30d` agent range** uses the subscription anchor when available, else a literal 30-day window (same behavior as views).
8. **Legacy reader** still swallows per-row errors (unchanged behavior); the current reader counts and reports skips in `reader.skipped_rows`.
9. **Account-split/dedup heuristics** (±120 s, raw model matching) were intentionally NOT modified (out of scope); covered by Phase-1 regression tests.
10. **Deferred P3 items**: `%TEMP%` cookie-DB copies, `widget_snap.log` growth, devtools flag, widget DB retention.
11. **No UI** for agent statistics or formula status yet (backend only, per scope).
12. The login smoke verified the window opens; it did **not** complete a real login (no real credentials used).

## 13. Deferred Phase 2D / 2E / 2F

- **2D — Slim integration**: read-only `~/.config/opencode/oh-my-opencode-slim.json`, configured-model vs actual-model comparison, agent→group semantics (Main/Worker/Oracle/Reviewer) — the raw agent backend from §9 is the foundation.
- **2E — Forecasting**: 5 h burn rate, remaining-quota projection, threshold alerts.
- **2F — UI enhancement**: agent dashboard, formula provenance panel, tray/autostart, inline-JS extraction.
- Also deferred: formula signature/version pinning, Electron devtools flag, P3 hygiene items listed in §12.
