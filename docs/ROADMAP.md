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
| 4 | Forecasting | PLANNED |
| 5A | Observability Dashboard UI | **COMPLETE** |
| 5.1 | Frontend structure cleanup (no behavior change) | **COMPLETE** |
| 5B | Advanced visualization (charts) | PLANNED |
| 6 | Desktop UX & Hygiene | PLANNED |

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

## Phase 4 — Forecasting (PLANNED, not implemented)

Planned only (no production code in Phase 3):

- 5 h burn rate
- weekly burn rate
- subscription-period burn rate
- remaining quota estimate
- time-to-limit

## Phase 5 — Dashboard UI

**Phase 5A — Observability Dashboard — COMPLETE.** Large-screen tabs Overview / Agents /
Models / Sessions with shared range state, lazy per-tab loading, in-memory cache, agent/model
detail panels, session tree, explicit Raw Cost semantics, and reader/empty/error states.
See `docs/PHASE5_DASHBOARD_UI.md`.

**Phase 5.1 — Frontend structure cleanup — COMPLETE.** The dashboard was split out of
`index.html` into `app.css` + `app.js` + `dashboard/*.js` classic-script modules with no
behavior change; see `docs/PHASE5_1_FRONTEND_STRUCTURE.md`.

**Phase 5B (PLANNED):** advanced visualization (charts in Agent/Model detail) and a Forecast
screen once Phase 4 exists. Providers remains a filter/detail dimension, not a standalone tab.

## Phase 6 — Desktop UX & Hygiene (PLANNED, not implemented)

- Tray, autostart, notifications
- P3 hygiene items from the Phase-1 audit (cookie-DB temp copies, snap log, devtools flag,
  widget DB retention)
- Formula signing/version pinning
