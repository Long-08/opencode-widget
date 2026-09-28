# Upstream and Attribution

This project is a substantially modified fork of:

- **Project:** opencode-widget
- **Original repository:** https://github.com/ikunops/opencode-widget
- **Original author / repository owner:** [@ikunops](https://github.com/ikunops)
- **Development base commit:** `37e399e343789a5e7efd92c5cab626527f2bf05c`

The upstream project provided the original OpenCode usage-widget codebase and the desktop-widget
architecture. This fork keeps the upstream Git history intact and builds on it.

## What this fork adds

- localhost API security hardening (runtime bearer token, Host/Origin policy, no wildcard CORS)
- Windows DPAPI secret storage with fail-closed writes and fail-safe plaintext migration
- current OpenCode database-schema support (`session_message` / `session_v2`)
- Agent / Model / Provider / Session observability
- usage timeline and Agent × Model visualization
- deterministic, reset-aware usage forecasting
- forecast-aware desktop notifications and a minimal tray
- desktop lifecycle management (single instance, server ownership, stale-runtime recovery)
- release hardening and portable Windows packaging

## Licensing note

At the time this fork was prepared, the upstream repository did **not** contain an explicit software
license, and its README did not state one. Accordingly, this repository does **not** assert a new
blanket open-source license over upstream-derived code, and no `LICENSE` file is added.

If redistribution terms are later clarified by the upstream author, this notice and the repository
licensing can be updated accordingly.

> TODO (non-blocking): if upstream licensing / redistribution permission is clarified, revisit the
> `LICENSE` file and the publication of compiled binary releases.

## Independence

This is an independent community project. It is not an official OpenCode project and is not
affiliated with or endorsed by the OpenCode team or by the upstream author beyond the attribution
above.

## Thanks

Thanks to [@ikunops](https://github.com/ikunops) for creating and publishing the original
`opencode-widget` project, which provided the initial code and architecture.
