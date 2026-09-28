# Phase 6B — Forecast-aware Notifications + Minimal Tray

> Opt-in, forecast-consumer notifications with dedupe, cooldown, escalation, reset-aware state
> and quiet hours, plus a minimal tray. The forecast engine remains the single prediction source:
> the notification layer never recomputes burn rate / time-to-limit and never changes
> quota/cost/observability semantics.

## 1. Scope

In: notification policy, main-process notification manager, settings persistence + validation,
minimal tray (Show / Refresh / Notifications toggle / Quit), forecast-aware triggers, quiet hours,
startup baseline, and the renderer settings block inside the Forecast tab.

Out: forecasting changes, charts, notification history, notification center, complex settings,
autostart UI, formula signing.

## 2. Forecast dependency

The manager consumes `GET /api/forecast` (authenticated, main-process only) and nothing else: no
database reads, no usage recomputation, no LLM calls. A debug/test-only env hook
`OPENCODE_WIDGET_NOTIFY_FIXTURE` (path to a forecast JSON) can override the fetch; it is never
exposed to the renderer and is used only by tests/smoke.

## 3. Default-off behavior

Notifications are **off by default** (`settings.enabled = false`). No state is written until the
user opts in; upgrading users stay quiet; nothing pops up on first launch. Enabling is explicit
(tray toggle or the Forecast-tab checkbox).

## 4. Trigger states

Only `limit_before_reset` (session/weekly) and `already_at_limit` (official remaining ≤ 0) can
trigger. `reset_before_limit`, `no_usage`, `insufficient_data`, `unavailable`, `reset_unknown` are
ignored. Period projections are not a trigger in this version (their status vocabulary has no
`limit_before_reset`).

## 5. Signal selection

Deterministic, constant-driven preference; the **stable window wins** (never the scariest):

- session: `window_avg` → `last_60m` → `last_30m`
- weekly: `last_7d` → `last_3d` → `last_24h`
- period: `daily_average` → `recent_3d_average` → `recent_7d_average`

The first window whose rate status is `ok` is used; shorter windows are only used when the more
stable one has no usable rate. A short-lived burst therefore cannot produce an alarm when the
stable window is calm.

## 6. Dedupe key

`event_type | window | reset_at` (reset-less events use `noreset`). A new reset window forms a new
key, so the next window can notify again.

## 7. Cooldown

`warning: 4 h`, `critical: 8 h` (fixed constants, no UI). Repeat notifications for the same event
within cooldown are suppressed and recorded as `cooldown` (not as notified).

## 8. Escalation

Cooldown is broken only by a clear worsening: `limit_before_reset` → `already_at_limit`, or
previous `time_to_limit_hours > 2 h` while the new one is `≤ 1 h`. No severity scores.

## 9. Reset-aware semantics

Before each evaluation, events whose `reset_at < now` are pruned, so a stale window can never
suppress or re-trigger today's risk. State is bounded (current/recent windows only; notifications
are not an audit log).

## 10. Quiet hours

Optional (`enabled`, `start`/`end` in `HH:MM`, local system time; cross-midnight supported,
default off). During quiet hours notifications are recorded as `suppressed`, never as notified, so
an event that is still relevant can notify once after quiet hours end. Forecast `reset_at` remains
UTC/epoch; quiet hours use local time — the two are not mixed.

## 11. Startup baseline

The first evaluation (startup, and again whenever notifications are enabled) only records the
current events as a baseline and sends nothing. Only later changes (new window, worsening) notify —
so enabling or upgrading never dumps historical risk.

## 12. Notification wording

Hedged, privacy-preserving, English:

- title: `OpenCode Widget`
- warning body: e.g. `OpenCode usage may reach the 5h limit before reset. Estimated time remaining: about 1h 40m.`
- critical body: `Current quota state indicates the limit has been reached.`

No "will/certain/guaranteed", no session id / agent / model / workspace / prompt / response.
`Notification.isSupported()` is checked; unsupported → silent skip (recorded, never marked shown).
A failed `show()` is recorded as `failed` and is **not** marked notified, so it can retry.

## 13. Tray behavior

Created once, after the single-instance lock and window creation; destroyed on quit. Menu (exact):

- **Show Widget** → restore only if minimized, show, focus (never changes size/snap)
- **Refresh** → sends `tray-refresh` to the renderer, which reuses its existing refresh path and
  the active observability tab only (no Dashboard fan-out)
- **Notifications: On/Off** → toggles + persists + rebuilds the menu
- **Quit** → `app.quit()` through the Phase 6A cleanup (never `process.exit`)

Clicking a notification shows/focuses the widget and opens the Forecast tab (`open-forecast`).
Background notifications never pop or resize the window by themselves. Icon: local
`electron/assets/tray.png` (no remote icons); a missing icon skips tray creation without crashing.
Second instances cannot create a second tray (single-instance lock exits first).

## 14. Settings storage

`notification_state.json` (Electron-owned, atomic tmp+rename, non-secret): `{settings, baseline_done,
events, updated_at}`. Path = env `OPENCODE_WIDGET_NOTIFY_STATE` else
`app.getPath('userData')/notification_state.json`. A missing/corrupt file yields defaults
(`enabled: false`) with no error. Settings live here rather than `config.json` because
`config.json` is Python-owned and secret-aware; the required fields (enabled / quiet hours) are
non-secret either way. Validation rejects unknown fields, wrong types, non-`HH:MM` values and
oversized strings.

## 15. Security boundary

Unchanged: Host allowlist + Origin policy + runtime-token auth; the token never leaves the main
process; the renderer's bridge stays narrow (`widgetAPI.apiGetNotificationSettings /
apiSetNotificationSettings / onOpenForecast / onTrayRefresh`); there is **no** generic notification
or settings-write capability and no generic fetch. Dynamic strings in the settings UI are escaped.
Debug logging is non-sensitive (event type, decision, cooldown, quiet hours — never tokens/cookies/
payloads). `will-quit` cleanup is per-step guarded with a 3 s hard fallback so shutdown can never
wedge.

## 16. Tests

- Suite total: **382 passed / 0 failed / 0 skipped** (`python -m pytest -o addopts="" -q`, TEMP
  redirected) — 363 before + 19 new (policy 35 / manager 15 node tests + 12 notification-static + 7
  tray-static, plus updates).
- Policy tests: default off; trigger vs ignored states; stable-window selection (including "stable
  usable but `reset_before_limit` → no event even if a shorter window says `limit_before_reset`");
  dedupe; cooldown; escalation; reset pruning; quiet hours incl. cross-midnight; baseline; at most
  one notification per tick; `applyResult` (`shown` only marks notified); settings validation;
  wording.
- Manager tests (injected fakes): start once / stop clears / shutdown guard; disabled → no fetch;
  baseline first tick; notify on a new event; dedupe/cooldown; re-baseline on enable; persistence;
  failed show not marked notified; interval seam.
- Tray tests (injected fakes): menu labels/order/checkbox; create-once; handler wiring; missing icon.
- Static guards: default-off, trigger constants, validated settings IPC, ≥15 min production interval,
  manager stopped on quit, notification click -> focus + forecast (no external), no generic bridge,
  DEBUG-gated logs, no token/cookie, tray created after the single-instance lock, tray quit uses
  `app.quit`, local icon.
- Smoke (real environment, fixture-driven — never real risk): notifications **16/16 PASS** — default
  off, baseline, one real notification shown (`[notify] … -> shown`), cooldown (no repeat), quiet
  hours (suppressed), tray created, quit terminates the server + removes runtime.json, settings
  persist across restart with no notification storm. Dashboard regression PASS (5 tabs, Forecast,
  small/mid/large, `PAGE_EVENTS: []`, `leaks: []`).

## 17. Known limitations

1. Notification evaluation runs at most once per 15 min (production); risk onset between ticks is
   noticed at the next tick. The fixture seam (test-only) may shorten it.
2. At most one notification per evaluation tick (highest priority wins); other pending risks are
   considered on the next tick, never mass-fired.
3. No notification history / center / audit timeline.
4. Cooldown and thresholds are fixed constants (no UI).
5. Quiet hours only suppress; they do not forward notifications later than the next evaluation.
6. Tray actions are not automated in the smoke (menu clicks are covered by unit tests over the menu
   template + handlers; tray creation by the debug marker).
7. Autostart toggle UI is not part of 6B (still deferred).
