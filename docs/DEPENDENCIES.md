# Dependencies

This document records the runtime and development dependencies of
OpenCode Widget, their licenses, and where the audit summaries are produced.

## Python backend — standard library only

The backend has **no third-party runtime dependencies**. It runs on **Python
3.11+** and imports only the standard library. Notable standard-library modules
used:

| Module | Used for |
|--------|----------|
| `http.server` | localhost JSON API (`ThreadingHTTPServer`) |
| `urllib.request` / `urllib.parse` | official quota sync and cloud formula fetch |
| `sqlite3` | read-only OpenCode database access; official ledger mirrors |
| `json` | configuration and API serialization |
| `ctypes` | DPAPI secret protection and Win32 process-query helpers |
| `tempfile` | runtime token file and atomic secret writes |
| `secrets` / `hmac` / `hashlib` / `base64` | per-run token, auth comparison, formula hashing |
| `threading` / `time` / `datetime` | background sync loops, caching, time windows |
| `os` / `sys` / `shutil` / `atexit` | filesystem, process and shutdown handling |
| `re` / `math` / `bisect` | parsing and numeric helpers |
| `importlib.util` | loading the hyphenated `go-usage-widget.py` module |
| `zipfile` | optional XLSX export |

No `pip install` is required at runtime, and there are no Python packages pinned
in a requirements file.

## Node / Electron — development and build only

- **Electron** (`electron/package.json`, `^43.3.0`) is a **development/build**
  dependency. The packaged release **embeds Electron**, so end users do not
  install Node.
- The application ships **no runtime npm dependencies beyond Electron itself**.
- `electron/package-lock.json` is the **pinned lockfile** and is the source of
  record for the exact resolved Electron version and its transitive tree.
- Build tooling uses only Node's standard library and the pinned Electron
  toolchain.

## Licenses

- **Electron** — MIT. Transitive Electron dev dependencies carry their own
  licenses as recorded in `electron/package-lock.json`; no separate license
  audit is maintained here.
- **Project assets** — the project's own assets, including the self-made
  `electron/assets/tray.png` and the `screenshot.png` / `screenshot-large.png`
  screenshots, are self-authored.
- **No third-party icon assets** are bundled.

## Audit summaries

The release build step records the dependency audit output. Numbers are produced
by that step, not by this document:

- **`npm audit` summary (Electron / dev tree):** `<filled by build step>`
- **Python dependency audit summary:** `NOT APPLICABLE — standard library only,
  no third-party runtime dependencies to audit.`
