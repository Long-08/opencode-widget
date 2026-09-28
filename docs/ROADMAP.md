# Roadmap — opencode-widget

> Last updated: Phase 3 kickoff (2026-09-28)

## Status

| Phase | Name | Status |
|---|---|---|
| 1 | Audit + regression baseline | **COMPLETE** |
| 2 Core | Security hardening + current-schema reader + raw Agent backend | **COMPLETE** |
| 2.1 | Runtime-token boundary + secret-storage semantics + Agent cost semantics | **COMPLETE** |
| 2D | Oh My OpenCode Slim integration | **CANCELLED** — Slim is no longer installed |
| 3 | Agent Observability Backend | **COMPLETE** |
| 4 | Explainable usage forecasting | **COMPLETE** |
| 5A | Observability Dashboard UI | **COMPLETE** |
| 5.1 | Frontend structure cleanup (no behavior change) | **COMPLETE** |
| 5B | Advanced visualization (timeline, matrix, forecast bars) | **COMPLETE** |
| 6A | Desktop lifecycle & hygiene | **COMPLETE** |
| 6B | Forecast notifications + minimal tray | **COMPLETE** |
| 7 | Release hardening & packaging | **COMPLETE** (RC pending review) |

## Permanent constraints

1. **Core observability is plugin-independent.** All core statistics depend only on
   OpenCode runtime data (the local OpenCode database), never on Oh My OpenCode Slim or
   any third-party agent plugin.
2. **Third-party integrations, if ever added, must be optional adapters** — never required
   for core statistics, never a source of truth, and removable without breaking the widget.
3. **Historical records are real data.** Usage produced while an uninstalled plugin was
   active (including Slim-era agent names) is preserved: never filtered, renamed or deleted.
   The widget does not need to know which plugin produced an agent name.
4. **Fact data is never overwritten.** Raw `agent`, `provider_id`, `model_id`, `variant`,
   `session_id`, `parent_session_id` stay as recorded; display concerns live in the display
   layer only.
5. **Billing/quota/meter logic is out of scope** for observability work and must not change.
6. **No subjective scoring** (performance/quality/efficiency/ranking scores) is ever added.

## Phase 3 — Agent Observability Backend (COMPLETE)

Plugin-independent aggregation over normalized OpenCode usage records:

- Enhanced Agent aggregation (requests, sessions, cost, tokens, duration, models, providers,
  cache ratio, derived averages).
- Agent → Model and Model → Agent statistics.
- Provider aggregation (raw `provider_id`, mapped `source`, display name).
- Session usage aggregation plus a usage-roll-up-free parent/child hierarchy.
- `/api/agents`, `/api/models`, `/api/providers`, `/api/sessions` behind the Phase 2.1
  runtime-token bridge.
- Regression tests + invariants; documentation in `docs/PHASE3_AGENT_OBSERVABILITY.md`.

Explicitly NOT in Phase 3: forecasting, quota prediction, notifications, dashboard UI, tray,
autostart redesign, third-party adapters.

## Phase 4 — Explainable Usage Forecasting (COMPLETE)

Deterministic, reset-aware rate extrapolation over observed usage + official quota state
(`/api/forecast`, Forecast dashboard tab). Every output is marked `type: "estimate"`;
official quota usage and raw OpenCode message cost stay separate bases. See
`docs/PHASE4_FORECASTING.md`.

Delivered: 5 h / weekly / subscription-period burn rates (multiple windows), reset-aware
time-to-limit, projection to window end. Not delivered (by design): notifications/tray
alerts (Phase 6) and advanced visualization (5B).

## Phase 5 — Dashboard UI

**Phase 5A — Observability Dashboard — COMPLETE.** Large-screen tabs Overview / Agents /
Models / Sessions with shared range state, lazy per-tab loading, in-memory cache, agent/model
detail panels, session tree, explicit Raw Cost semantics, and reader/empty/error states.
See `docs/PHASE5_DASHBOARD_UI.md`.

**Phase 5.1 — Frontend structure cleanup — COMPLETE.** The dashboard was split out of
`index.html` into `app.css` + `app.js` + `dashboard/*.js` classic-script modules with no
behavior change; see `docs/PHASE5_1_FRONTEND_STRUCTURE.md`.

**Phase 5B — Advanced visualization — COMPLETE.** Usage Timeline (`/api/timeline`) on Overview,
an Agent × Model usage matrix on Agents, and compact Forecast/quota bars — all rendering
already-verified data with no recomputed business semantics. See
`docs/PHASE5B_ADVANCED_VISUALIZATION.md`. Providers remains a filter/detail dimension.

## Phase 6 — Desktop UX & Hygiene

**Phase 6A — Desktop lifecycle & hygiene — COMPLETE.** Single-instance semantics, server
ownership on quit, orphan/idle watchdog, stale-runtime recovery, cookie-temp cleanup,
opt-in debug logging with rotation, production DevTools policy, port-collision behavior,
and a documented DB retention policy. See `docs/PHASE6A_DESKTOP_LIFECYCLE.md`.

**Phase 6B — Forecast notifications + minimal tray — COMPLETE.** Opt-in, default-off,
reset-aware notifications consuming the Phase 4 forecast (dedupe, cooldown, escalation,
quiet hours, startup baseline) plus a minimal tray (Show / Refresh / Notifications / Quit).
See `docs/PHASE6B_NOTIFICATIONS.md`.

Still deferred: notification history/center, complex settings UI, autostart toggle UI,
and formula signing/version pinning.

## Phase 7 — Release Hardening & Packaging (COMPLETE, RC pending review)

Portable Windows Release Candidate `0.9.0-rc.1`: remote-formula validation/version/size/timeout/
last-known-good/atomic adoption with an explicit unsigned trust model, versioned data-dir + config
migrations, production sanitation, a reproducible build, and packaged lifecycle verification.
See `docs/PHASE7_RELEASE_HARDENING.md` and `docs/PHASE7_RELEASE_AUDIT.md`.
No auto-updater, no Authenticode signing, no remote publication.
