# RC Final Validation — 0.9.0-rc.1

> Final packaged-only validation record for the Windows portable Release Candidate.
> This phase added attribution/publication notices and re-validated the packaged artifact; no product
> behavior changed.

## Source

```text
version:         0.9.0-rc.1   (single source: electron/package.json)
source tag:      v0.9.0-rc.1
final commit:    <recorded by the tag — see `git rev-parse v0.9.0-rc.1`)
branch:          dev/phase7-release-hardening
upstream:        https://github.com/ikunops/opencode-widget  (development base 37e399e343789a5e7efd92c5cab626527f2bf05c)
fork URL:        (not published — see "Publication status")
```

## Validation

```text
tests:               python -m pytest -o addopts="" -q  ->  all tests pass (see the release report for the exact count)
packaged smoke:      dist/opencode-widget-0.9.0-rc.1/ (staged artifact, not dev Electron)
PAGE_EVENTS:         []   (no console/CSP/module/page errors)
console errors:      []
CSP errors:          []
renderer leaks:      []    (runtime token not readable in the renderer)
apiEnv:              absent
generic bridge:      absent (narrow widgetAPI allowlist only)
secret scan:         artifact sanitizer clean (0 forbidden / 0 secret / 0 debug-hook)
```

## Artifact

```text
file:       dist/opencode-widget-0.9.0-rc.1-win-x64.zip
size:       see dist/BUILD_INFO.json
SHA256:     recorded in the RC final report and in dist/BUILD_INFO.json (rebuilt from the tagged commit)
sanitizer:  clean
```

## Publication status

```text
Source fork published:        NOT YET — the GitHub CLI (`gh`) is not installed/authenticated in this environment
Source tag published:         NOT YET — created locally, push blocked by the same prerequisite
Binary built locally:         YES
Binary GitHub Release:        NO
Reason (binary):              the upstream repository currently has no explicit software license; a
                              compiled-binary public release waits until upstream redistribution terms
                              are clarified or permission is granted
```

## License status

```text
upstream LICENSE:             none present at the time of this fork
new LICENSE added:            no
binary redistribution:        not established — no public binary release
```

## Notes

- The fork must be published through GitHub's **fork** mechanism (preserving the upstream history and
  the "Forked from" relationship), not as an unrelated new repository.
- After `gh` is available and authenticated: fork `ikunops/opencode-widget`, set
  `upstream = ikunops`, `origin = <user>/opencode-widget`, push `HEAD:main` (no `--force`) and the
  phase tags plus `v0.9.0-rc.1`, then set the repository description/topics and verify the fork
  relationship and attribution on the web UI. Still do **not** create a binary GitHub Release.
