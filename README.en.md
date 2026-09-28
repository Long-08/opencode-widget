# OpenCode Widget

**A Windows desktop observability widget for OpenCode** — Agent / Model analytics, a usage
timeline, explainable quota forecasting, opt-in notifications, and a hardened localhost data
boundary.

![Platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078D6?logo=windows&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-desktop%20shell-47848F?logo=electron&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Source RC](https://img.shields.io/badge/Source%20RC-v0.9.0--rc.1-6E56CF)

> [!NOTE]
> This project is a substantially modified fork of
> [ikunops/opencode-widget](https://github.com/ikunops/opencode-widget).
> Thanks to [@ikunops](https://github.com/ikunops) for the original project and codebase.
> See [UPSTREAM.md](UPSTREAM.md) for attribution and licensing notes.
>
> This is an independent community project and is not affiliated with or endorsed by the OpenCode
> team. 本项目基于 ikunops/opencode-widget 进行大幅二次开发。

![Large dashboard: Overview, usage timeline and top agents / models](docs/assets/hero-dashboard.png)

## Features

- 📊 **Agent / Model observability** — per-agent, per-model, per-provider and per-session usage,
  token and cost breakdowns computed from your local OpenCode data.
- 📈 **Usage timeline** — day/hour bucketing, a GitHub-style heatmap and an **Agent × Model**
  matrix.
- 🔮 **Explainable forecasting** — deterministic, reset-aware burn rates and time-to-limit
  estimates, always labelled `estimate`.
- 🔔 **Opt-in notifications** — forecast-aware alerts and a minimal tray menu. **Off by default.**
- 🪟 **Transparent Windows widget** — compact dock / mid / full dashboard, with drag-to-top
  snapping.
- 🔒 **Hardened local security** — read-only OpenCode DB, an authenticated localhost API, and
  DPAPI-protected secrets.

## Screenshots

Rendered from the current post-RC UI with **synthetic demo data**.

| Compact widget | Agent × Model matrix |
|---|---|
| ![Compact widget](docs/assets/compact-widget.png) | ![Agent × Model matrix](docs/assets/agent-model-matrix.png) |

![Forecast dashboard](docs/assets/forecast-notification.png)

## What this fork adds

- current OpenCode database-schema support (`session_message` / `session_v2`);
- Agent / Model / Provider / Session observability;
- a usage timeline and an **Agent × Model** visualization;
- deterministic, reset-aware usage forecasting;
- opt-in forecast-aware notifications and a minimal tray;
- desktop lifecycle hardening (single instance, server ownership, stale-runtime recovery);
- an authenticated localhost API and Windows DPAPI secret storage;
- reproducible portable Windows packaging, versioned migrations and release sanitation.

See [UPSTREAM.md](UPSTREAM.md) for the full attribution and licensing note.

## Quick start

`v0.9.0-rc.1` is a **source** release candidate: the Git tag is public, but no compiled binary is
distributed (see [Binary availability](#binary-availability)).

### Run from source

```bash
git clone https://github.com/Long-08/opencode-widget.git
cd opencode-widget

python data_server.py                     # backend (Python 3.11+, stdlib only)

cd electron && npm install && npm start   # Electron shell (Node.js for source runs)
```

The backend listens on `127.0.0.1:8765` (override with `OPENCODE_WIDGET_PORT`).

### Local portable RC (not publicly distributed)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-release.ps1
```

Produces `dist/opencode-widget-0.9.0-rc.1-win-x64.zip`; run the launcher
`启动 OpenCode Widget.vbs` inside the staged folder.

## Requirements

- **Windows 10 / 11** (x64).
- **Python 3.11+**, standard library only — no `pip install` at runtime.
- **Node.js** only for source runs or building from source; the packaged RC embeds Electron.

## Binary availability

A local Windows RC build exists, but compiled binaries are **not currently distributed publicly**
because the upstream repository does not establish explicit redistribution licensing.

## Architecture

```text
OpenCode DB (read-only) ─┐
                         ▼
             Python local data service  (standard library only)
                         │  authenticated localhost API  (127.0.0.1, per-runtime token)
                         ▼
             Electron main process  (owns the runtime token)
                         │  narrow preload bridge  (no token, no generic fetch)
                         ▼
                     Renderer UI
```

Optional side paths: **official quota sync** (opt-in, uses your own auth cookie) and the **remote
cloud formula** (pure data, reported as `integrity_status: unsigned`). The renderer never receives
the runtime token; a single instance is enforced.

## Security model

- OpenCode DB opened **read-only**; never modified.
- localhost API requires a **per-run random runtime token** plus **Host / Origin policy**;
  **CORS is never `*`**.
- the **renderer never receives the runtime token or base URL** and exposes no generic
  fetch / settings / notification capability.
- secrets protected with **Windows DPAPI** (user-bound), fail-closed writes, no plaintext fallback.
- **notifications are opt-in**.
- **prompt / response content is not exposed** by the observability APIs.

Full details: [`docs/PHASE7_RELEASE_HARDENING.md`](docs/PHASE7_RELEASE_HARDENING.md).

## Forecast semantics

> Forecasts are estimates based on observed usage and available official quota state. They are not
> guaranteed predictions.

## About “Raw Cost”

> Raw Cost reflects OpenCode message-record cost data and is not the same as official Go quota
> consumption.

## Data, privacy and lifecycle

Mutable data lives in `%APPDATA%\opencode-widget\` (`config.json`, DPAPI `secrets.enc`,
`usage_remote.db`, `server_usage.db`, `formula_cache.json`); the runtime token file is
`%TEMP%\opencode-widget\runtime.json`. Only local usage metadata is processed, plus official quota
sync when you opt in. Upgrade keeps your data dir; there is no auto-updater. Uninstall is deleting
the app folder (user data is kept unless removed manually).

## Development

```bat
set TEMP=%LOCALAPPDATA%\Temp\opencode
set TMP=%TEMP%
python -m pytest -o addopts="" -q
```

**609 automated tests passing at the RC source tag `v0.9.0-rc.1`.** Tag provenance is checked by
`scripts/check_provenance.py`.

```bash
git remote -v
# origin    https://github.com/Long-08/opencode-widget.git   (this fork)
# upstream  https://github.com/ikunops/opencode-widget.git   (original project)
```

## Upstream & attribution

This project is a substantially modified fork of
[ikunops/opencode-widget](https://github.com/ikunops/opencode-widget); the upstream Git history is
preserved. See [UPSTREAM.md](UPSTREAM.md) for the development base commit, attribution and
screenshot provenance. It is an independent community project and is not affiliated with or
endorsed by the OpenCode team.

## License status

The upstream repository did not include an explicit software license at the time of this fork, so
no new blanket license is asserted over upstream-derived code and **no `LICENSE` file is added**.
Compiled binary redistribution waits until upstream licensing/permission is clarified.

## Release status

`v0.9.0-rc.1` is a **source** release candidate (Git tag, not a stable release and not a binary
download): [`v0.9.0-rc.1`](https://github.com/Long-08/opencode-widget/tree/v0.9.0-rc.1).
It is **unsigned**, so Windows SmartScreen may warn on a local build.
