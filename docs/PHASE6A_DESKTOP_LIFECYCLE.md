# Phase 6A — Desktop Lifecycle & Hygiene

> Single-instance semantics, process ownership, stale-runtime recovery, temporary-file and
> logging hygiene, and a production debug/devtools policy — for long-running desktop use.
> No quota/cost/forecast semantics, no observability API contract, and no secure-bridge changes.

## 1. Process topology

```
launcher (启动Go用量悬浮窗.vbs / .cmd)
   ├─ pythonw.exe data_server.py     (HTTP data service, %TEMP%\opencode-widget\runtime.json)
   └─ electron.exe .                 (main process → renderer via narrow preload bridge)
```

- The launcher starts both processes independently (zero-console goal); Electron does **not**
  spawn the Python server (the VBS `ws.Run` parent exits immediately, so parent-PID ownership is
  not viable).
- Ownership is therefore enforced by **runtime-file + token identity**: only Electron may
  terminate the server it can prove is ours (see §5).
- OpenCode's own database is never part of this topology (widget reads it read-only).

## 2. Startup sequence

1. Launcher: if a `pythonw.exe … data_server.py` process already exists it is not restarted; same
   for Electron (`Win32_Process` command-line check).
2. `data_server.py main()`: bind → on success write `runtime.json` atomically → register
   `atexit` cleanup → start daemon threads (preheat, auto-sync, formula-sync, idle watchdog) →
   `serve_forever()`.
3. `electron.exe main`: `app.requestSingleInstanceLock()` first; second instance quits
   immediately. First instance creates windows, starts the 60 s heartbeat, exposes `api-request`.
4. Startup hygiene (before serving): stale cookie temp copies, stray `runtime.json.tmp`, optional
   quota-snapshot prune.

## 3. Single-instance semantics

- Electron: `requestSingleInstanceLock()`; if not obtained the process calls `app.quit()` **before**
  creating any window, timer, or server work. `second-instance` → `restore()` (only if minimized)
  + `show()` + `focus()`; size/snap state is never changed.
- Python: a second `data_server.py` cannot serve (the port is taken); it classifies the conflict
  (§13) and exits without writing a runtime file.

## 4. Runtime metadata

`%TEMP%\opencode-widget\runtime.json` (env override `OPENCODE_WIDGET_RUNTIME_DIR`):

```json
{"token": "<256-bit urlsafe>", "port": 8765, "pid": 1234, "created": 1700000000000, "instance_id": "<32-hex>"}
```

- `instance_id` is a non-secret per-process id for diagnostics/ownership; the **token is never
  returned by any API** and stays in the main process only.
- Atomic write (`tmp + os.replace`); pid-liveness validated by consumers.

## 5. Server ownership

- Electron's `will-quit` cleanup probes `GET /api/state?heartbeat=1` with the runtime token; the
  server's token proves ownership.
- Only on a successful probe does Electron `process.kill(pid)` the server and unlink `runtime.json`
  (and only if its pid still matches). On probe failure it kills nothing (no innocent kills) and
  leaves the stale file for startup handling.
- PID-reuse misfires are avoided by the token-authenticated probe, not by pid alone.

## 6. Normal shutdown

Order: stop timers (heartbeat + click-through poll) → close the login window → terminate the owned
server → remove the runtime file → `app.quit()`. The login session/cookies are **not** cleared.
Python daemon threads do not block exit; the server's `finally`/`atexit` cleanup removes its own
runtime file on graceful stops.

## 7. Crash / stale recovery

- **Electron crash / hard kill**: heartbeats stop; the server's idle watchdog exits after
  `OPENCODE_WIDGET_IDLE_TIMEOUT_S` (default 300 s; `0` disables) once it has seen authenticated
  traffic. Cleanup runs before exit.
- **Server crash**: the renderer shows the existing error/retry states; Electron does not spawn
  duplicate servers or retry-storm.
- **Stale runtime file**: a new server start overwrites it atomically; Electron ignores a
  runtime file whose pid is not alive (`runtime_env.js`) and best-effort removes it.
- No reliance on `atexit` alone (hard kills never run it) — startup cleanup is the backstop.

## 8. Runtime file lifecycle

Created atomically at startup; overwritten by any new instance; removed on graceful exit
(`finally`/`atexit`) or by the owning Electron on quit; ignored (and cleaned) when stale by pid
check. `runtime.json.tmp` leftovers are removed at startup.

## 9. Cookie temp cleanup

`browser_cookie.py` copies the browser cookie DB to a temp file before reading. Now:

- our own subdir + ownership marker: `opencode-widget-cookie-<hash>-<pid>.db`
  (base dir = `OPENCODE_WIDGET_TMPDIR` or `%TEMP%`), legacy files were `%TEMP%\opencode\cookie_*.db`;
- the sqlite connection is closed before deletion; deletion happens in a `finally` on success and
  on error; failures emit a generic non-sensitive warning only;
- `cleanup_stale_cookie_temps(max_age_s=86400)` removes only our owned copies (new naming) and
  legacy `cookie_*.db` older than the age; unrelated temp files are never touched.

## 10. Logging policy

- `widget_snap.log` is written **only** when `OPENCODE_WIDGET_DEBUG=1|true`; production is a no-op
  (no growth).
- When enabled, the log rotates at ~1 MB to `widget_snap.log.1` (one backup).
- Logs contain non-sensitive data only (positions, pids, statuses). Never token, cookie,
  `Authorization`, prompt, response, or reasoning.

## 11. DevTools policy

`webPreferences.devTools = <debug flag>`: production has DevTools disabled for both windows; debug
mode enables it. `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` are unchanged.
No auto-open. (Remote debugging via `--remote-debugging-port`, used by the smoke tests, is a
separate mechanism and still works.)

## 12. DB retention policy

- `~/.local/share/opencode/opencode.db` — **never** opened writable, never VACUUM/DELETE; the
  widget has no business modifying OpenCode history.
- Widget-owned DBs: `usage_remote.db` (`usage_records`, `cost_summary`, `quota_snapshot`,
  `sync_meta`) and the legacy `server_usage.db`.
  - `quota_snapshot` is a small append-only per-sync log; an opt-in prune exists:
    `quota_snapshot_retention_days` in config (absent/0 = **disabled by default**; recommended
    ≥ 90 days). `prune_quota_snapshot()` deletes only old `quota_snapshot` rows, never VACUUMs,
    never touches other tables or the OpenCode DB.
  - `usage_records` / `cost_summary` are not auto-pruned in this phase (documented policy; they
    are the official-sync mirror and not fully rebuildable offline).

## 13. Port collision behavior

On bind failure the server distinguishes:

- `existing_widget` — a health probe succeeded AND `runtime.json` has token+pid → print a
  non-sensitive "already running" line and exit 0, writing nothing.
- `foreign` — anything else → clear stderr error, exit non-zero, write nothing, and **never kill
  the other process**.

Electron's single-instance lock handles the UI side (second launch focuses the existing window).

## 14. Threat model

- Owner detection uses the runtime **token** probe, so a stale/reused PID cannot cause the widget
  to kill an unrelated process.
- The runtime file lives in the per-user temp dir; same-user processes can read it (out of scope,
  as documented before) but web pages cannot (Host/Origin/token boundary unchanged).
- Logs never carry secrets; `debugSnap` is opt-in.
- Shutdown never opens a network kill API: no `/api/shutdown`; ownership is process-level.
- In-scope: duplicate launches, orphan servers, stale runtime files, temp/log growth, accidental
  debug exposure. Out of scope: malicious same-user processes, OS-level compromise.

## 15. Tests

- Suite total: **363 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`, TEMP
  redirected) — 308 before + 55 new.
- Python: `tests/test_runtime_lifecycle.py` (pid_alive injected impls, idle matrix, port-conflict
  classification, owned-name matcher, stale-file selection, instance id), `tests/test_cookie_temp.py`
  (delete on success/failure, stale cleanup, unrelated untouched), `tests/test_retention_policy.py`
  (cutoff, recent preserved, disabled by default, OpenCode DB never writable),
  `tests/test_runtime_token_file.py` (instance_id + heartbeat/auth).
- Electron: `tests/js/runtime_manager.test.js` (12 node:test) + `tests/test_lifecycle_static.py`
  (9 static guards: single-instance early quit, second-instance focus, will-quit cleanup, devTools
  gate, debug gate, webPreferences unchanged, heartbeat path, no secret logging).
- Smoke (real environment): lifecycle **19/19 PASS** (first start, second instance exits, single
  server, temp/log hygiene, normal quit → server terminated + runtime removed, restart, foreign
  port collision without killing the foreign process, stale runtime overwritten, orphan watchdog
  self-exit); dashboard regression PASS (5 tabs, Forecast lazy + range preserved,
  `PAGE_EVENTS: []`, `leaks: []`).

## 16. Known limitations

1. Launchers are unchanged (`.vbs` hardcodes `%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe`);
   a missing interpreter yields a clear failure, not auto-discovery. Launcher edits were avoided to
   protect the zero-console VBS encoding.
2. The server does not monitor a parent PID (the VBS parent exits immediately); orphan prevention
   is heartbeat/idle-based, so an orphan lingers at most the idle timeout (default 300 s).
3. Idle self-exit only activates after the server has seen authenticated traffic (a server used
   only by external tooling stays up).
4. DB retention is policy + an opt-in `quota_snapshot` prune; `usage_records`/`cost_summary` are
   not auto-pruned.
5. Column-level ACLs on `runtime.json` are not applied (per-user temp dir inherits user ACLs).
6. No tray/notifications/autostart UI (Phase 6B) and no new dashboard tabs.
