# Phase 7 — Release Hardening (RC Report)

> The project is hardened and validated as a Windows **portable Release Candidate**. No new
> observability, forecasting, notification, or visualization features were introduced; nothing was
> published remotely.

## 1. Release version & artifact

```text
version:        0.9.0-rc.1          (single source: electron/package.json; Python reads it via paths.app_version())
packaging mode: portable folder + zip (manual reproducible staging; no auto-updater, no second packager)
artifact:       dist/opencode-widget-0.9.0-rc.1-win-x64.zip
size:           144,587,627 bytes (137.89 MB)
pre-publication candidate SHA256 (superseded by the final tagged rebuild; NOT release evidence):
                0E32558ADE4085DBC482033440A6CF76A38E6767ACEEF933A45B19C5F8AC86E9
final tagged artifact checksum:
                generated after the source tag is created and recorded outside the tagged
                tree in dist/SHA256SUMS.txt
build info:     dist/BUILD_INFO.json {version, commit, built_at}  (no paths/usernames)
signing:        UNSIGNED (no Authenticode certificate) — Windows SmartScreen may warn; no bypass script provided
```

## 2. Remote formula trust model

**Signature verification: NOT implemented** — there is no independent trust root or key-distribution
channel, so implementing a signature would be security theatre (see Phase 1 §9 and §15 of the Phase 7
brief). `integrity_status` is always `"unsigned"`. Measures actually in place:

| Measure | Implementation |
|---|---|
| schema validation | `views.validate_formula()` — dict; `params`/`views` required; per-section strict types/ranges; `NaN`/`Infinity` rejected; named size caps (strings 512, lists 2000, dicts 5000) |
| version compatibility | `SUPPORTED_FORMULA_SCHEMA = 1`; `MIN_SUPPORTED_FORMULA_VERSION = 1`; `MAX_SUPPORTED_FORMULA_VERSION = 7`; legacy payloads without `schema_version` are treated as schema 1; `unsupported_schema` / `too_old` / `too_new` are rejected (never silently adopted) |
| unknown fields | top-level allowlist `{version, schema_version, source_note, params, constants, formulas, views, extensions}`; anything else rejects the payload (`extensions` is the documented safely-ignored bag) |
| size limit | `MAX_FORMULA_BYTES = 512 KiB` (reads at most cap+1) |
| timeout | `FORMULA_FETCH_TIMEOUT_S = 5` |
| last-known-good | precedence: in-memory LKG → validated persisted cache (`%APPDATA%\opencode-widget\formula_cache.json`) → bundled `DEFAULT_FORMULA`; the active formula is never cleared on failure |
| atomic adoption | validated payload persisted via `tmp + os.replace` with provenance; leftover `.tmp` never breaks loading; cached payload is re-validated before use |
| disable | `formula_enabled: false` → no network at all (regression-verified) |
| provenance | `/api/formula` exposes `source, schema_version, formula_version, loaded_at, validation_status, integrity_status, enabled, fallback, hash` (no secrets) |

Failure of formula sync never creates a system notification.

## 3. Migrations

| Area | Behavior |
|---|---|
| data dir | mutable data moved out of the install dir to `%APPDATA%\opencode-widget` (env `OPENCODE_WIDGET_DATA_DIR` override): `config.json`, `secrets.enc`, `usage_remote.db`, `server_usage.db`, `formula_cache.json` |
| legacy migration | `paths.migrate_legacy_data()` copies in-repo legacy files **once**, verifies the copy, never overwrites, leaves originals untouched, idempotent |
| config version | `config_version = 1`; legacy configs gain it via the secret-safe save path; `config_version > supported` is returned as-is and **not** overwritten (`config_status() == "future_version"`); corrupt config is backed up to `<config>.corrupt.bak` (max one) and defaults are used (`"corrupt"`) |
| secrets | Phase 2.1 semantics unchanged: DPAPI store, write→verify→strip migration, **fail-closed** new writes |
| notification state | `notification_state.json` gains `version: 1`; a missing version migrates on next save; a newer version is never overwritten (defaults for the run) |
| failure behavior | every migration reads old → builds new → validates → atomic-writes → read-back verifies; on failure the old file is preserved |

## 4. Production sanitation

- `scripts/release_sanitize.py` (pure, stdlib-only) scans the staging tree for: tests/fixtures/bytecode/
  pytest cache/logs/`runtime.json`/cookie temp DBs/notification test state/`*.bak`/coverage/screenshots,
  real-looking secret patterns (`Bearer <long>`, `sk-…`, `auth=…`, literal `wk_`), and debug hooks
  (a bundled `config.json`, a bundled fixture/notification state, default-on debug env).
- The build **fails** if any category is non-empty; the RC build reported
  `{"forbidden": [], "secret_hits": [], "debug_hook_hits": []}`.
- Production defaults remain: `OPENCODE_WIDGET_DEBUG` unset → snap logging off; DevTools off; fixture
  hooks reachable only via explicit environment variables (no bundled config enables them).

## 5. Packaging

```text
Electron:             embedded from electron/node_modules/electron/dist (electron.exe renamed opencode-widget.exe)
                      + resources/app/{main,preload,runtime_env,runtime_manager,notification_*,tray_manager,package.json,app/,assets/}
Python:               system Python 3.11+ (stdlib only); staged modules + a generated launcher
                      (.vbs zero-console + .cmd) that starts pythonw data_server.py then the app
mutable data path:    %APPDATA%\opencode-widget (+ Electron userData for notification state), runtime token in %TEMP%
install-dir writes:   none at runtime (verified 115 → 115 files); `sys.dont_write_bytecode = True` avoids __pycache__
unicode/space paths:  data dir under `…\OpenCode Widget 测试\data` used end-to-end by the packaged backend
```

## 6. Dependency / license audit

- Python: **standard library only** (no third-party runtime dependencies; nothing to lock).
- Node: Electron (dev/build; embedded runtime). `package-lock.json` is the pinned lockfile.
- Licenses: Electron (MIT); project assets self-made (`electron/assets/tray.png`, screenshots).
- `npm audit`/Python-audit summaries are recorded in `docs/DEPENDENCIES.md` (the build step prints them);
  no production-reachable issues remain unaddressed in this RC.

## 7. Tests

```text
before: 439
after:  588
passed: 588 / failed: 0 / skipped: 0
```

```powershell
$env:TEMP="$env:LOCALAPPDATA\Temp\opencode"; $env:TMP=$env:TEMP
python -m pytest -o addopts="" -q     # 588 passed
```

Phase 7 added 149 tests: formula hardening (~100 parametrized), data-dir/config/notification migration
(23), release sanitation (25), plus the installed-directory/secret-store fix (1).

## 8. Packaged RC smoke (staged artifact, isolated profile)

`23 passed / 0 failed`:
artifact present + SHA256 computed; sanitation re-scan clean; packaged server ready with token metadata;
`/api/state`, `/api/agents`, `/api/models`, `/api/sessions`, `/api/timeline`, `/api/forecast` all 200
under the security contract; `/api/config` flags-only; legacy config migrated (`config_version = 1`);
`formula_enabled: false` → no remote fetch; **clean install created no `secrets.enc`**; packaged
Electron loaded the UI; renderer bridge present and **no `apiEnv`**; second launch exits (single
instance); quit exited Electron, terminated the owned server and removed `runtime.json`; **install
directory not written at runtime** (115 → 115 files).

Dashboard / forecast / timeline / matrix / forecast-bars and notifications behaviour remain covered by
the Phase 4/5B/6B smokes (unchanged in this phase). Offline behavior is exercised by the disabled-formula
check plus the existing "official unavailable" paths.

## 9. Upgrade / rollback / uninstall

- **Upgrade**: replace binaries, keep `%APPDATA%\opencode-widget`; migrations are versioned and fail-safe.
- **Rollback**: not formally supported across a version downgrade; a newer config is never overwritten by
  an older reader, but downgrade requires a backup — documented in the README.
- **Uninstall**: delete the portable folder; user data in `%APPDATA%` is kept unless manually removed
  (README explains removing `secrets.enc`/DBs). No credential export exists (and none was added).

## 10. Known limitations / deferred (non-blocking)

1. Authenticode signing not done (no certificate) — unsigned RC by design.
2. No auto-updater (deliberately deferred to a future supply-chain phase).
3. Python backend requires a system Python 3.11+ (PyInstaller bundling was evaluated and deferred; not an
   RC blocker given the documented requirement and the portable launcher).
4. Python dependency audit is trivial (stdlib only); `npm audit` output is informational.
5. Sankey/network visualization and notification history remain out of scope.
