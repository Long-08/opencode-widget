# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).
The version is defined by a single source of truth: `electron/package.json`.

## [0.9.0-rc.1] — 2026-09-28

First release candidate. This build is **unsigned** (no Authenticode
signature); Windows SmartScreen may warn on first run.

### Security

- Encrypted, user-bound secret storage for the API key and auth cookie via a
  DPAPI-backed `secrets.enc`; plaintext secrets in `config.json` migrate
  fail-safely, and new secret writes fail closed.
- Runtime authentication: a per-run random bearer token guards the localhost
  API, together with Host and Origin allowlisting. CORS is never `*`.
- Renderer hardening: the renderer never receives the runtime token or base URL
  and exposes no generic fetch / settings / notification capability.
- Remote formula client enforces HTTPS, strict schema + version compatibility, a
  size cap and a fetch timeout, with last-known-good fallback and atomic
  adoption. No cryptographic signature is verified
  (`integrity_status: unsigned`).
- Production defaults: DevTools off and debug logging off.

### Observability

- Local, plugin-independent aggregation of Agent, Model, Provider and Session
  usage, tokens and cost over the OpenCode database.
- OpenCode database is always opened read-only; raw fields are preserved and
  never overwritten.
- New JSON endpoints for agents, models, providers and sessions behind the
  runtime-token bridge.

### Forecasting

- Deterministic, reset-aware burn-rate and time-to-limit estimation over 5h
  session, weekly and subscription-period windows.
- Every forecast output is typed `estimate`; official quota and raw usage cost
  remain separate bases.

### Dashboard

- Large-screen dashboard with Overview / Agents / Models / Sessions tabs, shared
  range state, lazy per-tab loading and explicit raw-cost semantics.
- Frontend split into dedicated module files for maintainability without
  behavior change.

### Lifecycle

- Single-instance semantics and server ownership on quit.
- Idle and orphan watchdog with stale-runtime recovery and port-collision
  classification that never terminates a foreign process.
- Cookie-temp cleanup, opt-in debug logging with rotation, and production
  DevTools policy.

### Notifications

- Opt-in, default-off forecast notifications with dedupe, cooldown, escalation,
  quiet hours and a startup baseline.
- Minimal tray menu (Show / Refresh / Notifications / Quit).

### Visualization

- Usage timeline, Agent × Model usage matrix and compact forecast / quota bars
  rendering already-verified data with no recomputed business semantics.
