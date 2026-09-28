# Release Checklist — OpenCode Widget

Manual release checklist for the `0.9.0-rc.1` release candidate (unsigned).
Check every box before sign-off. Record evidence (command output, log excerpt,
artifact hash) in the release notes, never here.

## 1. Preflight

- [ ] Working tree is clean (no uncommitted or untracked changes intended for
      the release).
- [ ] `electron/package.json` is the single source of truth and reads
      **`0.9.0-rc.1`**.
- [ ] Test suite is green: `python -m pytest -o addopts="" -q` reports
      **all tests passing** (461 passed when this RC was cut). (If pytest cannot
      access its temp base, point `TEMP`/`TMP` at a writable folder first.)
- [ ] Docs are current: `README.md`, `CHANGELOG.md`, `docs/DEPENDENCIES.md`
      and this checklist match the built artifact.
- [ ] No machine-specific absolute paths or real secrets appear in
      release-facing docs.

## 2. Build

- [ ] Build runs from the pinned lockfile
      (`electron/package-lock.json`); no lockfile drift.
- [ ] Artifacts are assembled into a **staging** directory, not the repo root.
- [ ] **Sanitation** pass completed on the staging tree: no `config.json`,
      `secrets.enc`, `*.db`, `node_modules` dev leftovers, logs, `.env`, or
      editor/OS cruft.
- [ ] Debug-only files and debug flags are excluded from the production
      artifact (DevTools off, debug logging off).

## 3. Artifact

- [ ] Portable artifact produced (zip/folder) as intended for distribution.
- [ ] **SHA256** of the artifact computed and recorded in the release notes.
- [ ] Artifact is marked **unsigned** in the release notes; SmartScreen may warn.
- [ ] No script or instruction is shipped that bypasses OS security warnings.

## 4. Smoke tests

- [ ] **Clean install:** unzip on a machine/profile with no prior widget data;
      launcher starts backend + shell; window appears.
- [ ] **Upgrade:** replace binaries over an existing install while keeping
      `%APPDATA%\opencode-widget`; config, secrets and history are preserved;
      migrations complete without data loss.
- [ ] **Offline:** with the network unavailable, the widget still starts and
      falls back gracefully (official sync unavailable, formula
      last-known-good retained).
- [ ] **Unicode path:** install and run from a path containing non-ASCII
      characters; launch and data reads succeed.
- [ ] **Normal quit:** quitting terminates the backend; no orphan process is
      left behind.

## 5. Security

- [ ] **Secret scan:** no plaintext API key / auth cookie anywhere in the
      artifact or docs.
- [ ] **Token-leak scan:** the runtime token and base URL are absent from the
      renderer bundle and rendered pages.
- [ ] **CSP** is present and restrictive in `electron/app/index.html`; CORS is
      never `*`.
- [ ] Host / Origin policy rejects bad Host and bad Origin (403), and missing or
      wrong token is rejected (401).
- [ ] **DevTools off** and debug logging off in the production build.
- [ ] Secret-store failure path is **fail closed** (no plaintext downgrade).
- [ ] Formula client reports `integrity_status: unsigned`; docs do not describe
      HTTPS as content signing.

## 6. Packaging / runtime behavior

- [ ] **Single instance** enforced (second launch does not start a second
      shell/backend).
- [ ] **Lifecycle:** idle watchdog, runtime-token file cleanup, and port
      conflict handling behave as documented (never kills a foreign process).
- [ ] **Tray** icon is present and its menu works (Show / Refresh /
      Notifications / Quit).
- [ ] **Notifications** are off by default and are only created after explicit
      opt-in; opt-in and quiet hours behave as documented.

## 7. RC sign-off

- [ ] All of the above boxes are checked, with evidence recorded.
- [ ] Known issues and deferred items are listed in the release notes.
- [ ] The unsigned status of the RC is stated in the release notes.
- [ ] Release manager sign-off.

| Role | Name | Date | Notes |
|------|------|------|-------|
| Release manager | | | |
