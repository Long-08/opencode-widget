# Phase 7 — Release Audit

> Pre-change inventory of every artifact class in the repository, with the classification it must
> have in a production RC: **SHIP** (runtime), **DEV/BUILD** (tooling, never shipped),
> **TEST** (never shipped), **USER DATA** (created at runtime outside the install dir).
>
> Audit date: Phase 7 (release hardening). Repo: `opencode-widget`.

## 1. Entry points

| Artifact | Role | Class |
|---|---|---|
| `data_server.py` | Python backend entry point (stdlib HTTP server, `127.0.0.1:8765`); `main()` binds, writes runtime.json, starts threads | SHIP |
| `electron/main.js` | Electron main entry point (single instance, windows, heartbeat, tray, notification manager, quit cleanup) | SHIP |
| `electron/preload.js` | Narrow context bridge (`widgetAPI`) | SHIP |
| `scripts/build-release.ps1` | Build entry point (preflight → stage → sanitize → zip → SHA256) | DEV/BUILD |
| 启动Go用量悬浮窗.vbs / .cmd (repo) | Developer launcher (hardcodes Python311) | DEV/BUILD (staging gets a generated launcher) |
| generated `opencode-widget.exe` + `启动 OpenCode Widget.vbs/.cmd` | Portable RC entry points | SHIP (built into the artifact only) |

## 2. Backend modules

| File | Role | Class |
|---|---|---|
| `go-usage-widget.py` | readers, rule tables, `norm_model`, config load/save | SHIP |
| `views.py` | formula store + view engine + `validate_formula` | SHIP |
| `formula_registry.py` | local formula implementations | SHIP |
| `observability.py` | agent/model/provider/session/tree aggregation | SHIP |
| `timeline.py` | time-bucket aggregation | SHIP |
| `forecasting.py` | deterministic rate/estimate engine | SHIP |
| `usage_remote.py`, `server_data.py` | official sync + Widget DBs | SHIP |
| `secret_store.py` | DPAPI-encrypted secret storage | SHIP |
| `paths.py` | user data dir resolution + legacy migration + `app_version()` | SHIP |
| `runtime_lifecycle.py` | PID checks, idle-exit decision, temp-file selection | SHIP |
| `browser_cookie.py` | best-effort browser cookie copy (temp, self-cleaning) | SHIP |
| `gen_widget_xlsx.py` | auxiliary spreadsheet export (reads local API) | SHIP (optional) / DEV |

## 3. Renderer (Electron app)

| Artifact | Role | Class |
|---|---|---|
| `electron/package.json` | app manifest; **single version source** | SHIP |
| `electron/runtime_env.js` | runtime.json reader (pid liveness) | SHIP |
| `electron/notification_policy.js` / `notification_manager.js` | opt-in notifications | SHIP |
| `electron/tray_manager.js` + `electron/assets/tray.png` | minimal tray + local icon | SHIP |
| `electron/app/index.html`, `app.css`, `app.js`, `dashboard/*.js` | renderer UI (classic scripts + `OCW` namespace) | SHIP |
| `electron/node_modules/**` | Electron dependency tree (dev install; only `dist/` is staged) | DEV/BUILD |

## 4. Runtime / user data (never in the install dir)

| Artifact | Location | Class |
|---|---|---|
| `config.json` | `%APPDATA%\opencode-widget\` | USER DATA |
| `secrets.enc` (DPAPI) | `%APPDATA%\opencode-widget\` | USER DATA (secret) |
| `usage_remote.db`, `server_usage.db` | `%APPDATA%\opencode-widget\` | USER DATA |
| `formula_cache.json` (last-known-good, `formula_lkg.json` legacy fallback) | `%APPDATA%\opencode-widget\` | USER DATA |
| `runtime.json` (runtime token) | `%TEMP%\opencode-widget\` | USER DATA (secret, transient) |
| `notification_state.json` | Electron `userData` (`%APPDATA%\opencode-widget-electron\`) | USER DATA |
| `widget_snap.log` | `%TEMP%` (debug builds only) | USER DATA (debug) |
| OpenCode DB (`~/.local/share/opencode/opencode.db`) | user profile | **never written** (read-only) |
| Codex logs (`~/.codex/logs_2.sqlite`) | user profile | read-only |

Legacy in-repo copies of the four data files above are migrated once into the data dir; the
originals are left untouched (no deletion).

## 5. Remote trust surfaces

| Surface | Trust treatment |
|---|---|
| Cloud formula (`formula_url`, default workers.dev) | pure data; HTTPS; strict schema + version validation; 512 KiB size cap; 5 s timeout; last-known-good (memory + validated persisted cache); atomic adoption; `integrity_status = "unsigned"` (no signature — no independent trust root exists) |
| opencode.ai API/HTML (official quota, usage list, costs, billing) | authenticated with the user's own cookie; read-only; failures degrade gracefully |
| Model list (`/zen/go/v1/models`) | `Authorization: Bearer <api key>`; 24 h cache |

## 6. Test / dev-only artifacts (must NOT ship)

`tests/**` (including `tests/fixtures/**`, `tests/js/**`), `pytest.ini`, `.pytest_cache/`,
`__pycache__/`, `*.pyc`, coverage output, `scripts/**` (build tooling), `cloud/**` (formula source,
ships only via the Worker), screenshots, `.git/**`.

Debug/test hooks that must remain unreachable by default in production:
`OPENCODE_WIDGET_DEBUG`, `OPENCODE_WIDGET_NOTIFY_FIXTURE`, `OPENCODE_WIDGET_NOTIFY_STATE`,
`OPENCODE_WIDGET_NOTIFY_INTERVAL_S` (fixture-gated short interval), `OPENCODE_WIDGET_PORT`,
`OPENCODE_WIDGET_IDLE_TIMEOUT_S`, `OPENCODE_WIDGET_DATA_DIR`, `OPENCODE_WIDGET_TMPDIR`,
`FORMULA_URL` — all environment-only; no bundled config enables them. The build's sanitation scan
fails if any bundled `config.json`/fixture state is staged.

## 7. Assets

`electron/assets/tray.png` (self-made 16×16), `screenshot.png` / `screenshot-large.png`
(project screenshots — repo only, not staged). No third-party icons, no downloaded assets.

## 8. Dependencies

- Python: **standard library only** (no pip runtime deps).
- Node: Electron (dev/build dependency; the RC embeds the Electron runtime). `package-lock.json`
  is the pinned lockfile. Licenses and the audit summary: `docs/DEPENDENCIES.md`.

## 9. Packaging config

- No prior packaging pipeline existed. Phase 7 uses a single approach: a manual, reproducible
  portable staging script (`scripts/build-release.ps1`) — Electron dist + `resources/app` +
  Python modules + generated launcher — zipped with a SHA256. No second packaging system is
  introduced; no auto-updater.

## 10. Audit conclusions

- All runtime modules are stdlib/Electron-only; nothing third-party needs vendoring.
- Secrets are confined to DPAPI storage + the transient runtime token; neither is bundled.
- Data paths are already outside the install dir (Phase 7 paths.py), so a read-only install
  directory is supported.
- The formula boundary had validation gaps (documented in Phase 1 §9); Phase 7 closes them
  (schema/version/size/timeout/LKG/atomic/provenance) and states the unsigned trust model
  explicitly rather than pretending to sign.
