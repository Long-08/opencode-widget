# RC Final Validation — 0.9.0-rc.1

> Final packaged-only validation record for the Windows portable Release Candidate.
> This phase added attribution/publication notices and re-validated the packaged artifact; no product
> behavior changed.

## Source

```text
version:         0.9.0-rc.1   (single source: electron/package.json)
source tag:      v0.9.0-rc.1
tagged commit:   the exact commit the v0.9.0-rc.1 tag peels to
                 (verify: git rev-parse "v0.9.0-rc.1^{}"); docs/ is not staged into
                 the artifact, so doc-only commits do not change the packaged bytes
branch:          dev/phase7-release-hardening
upstream:        https://github.com/ikunops/opencode-widget  (development base 37e399e343789a5e7efd92c5cab626527f2bf05c)
fork URL:        (not published — see "Publication status")
```

The commit actually recorded by the built artifact is written by the build to
`dist/BUILD_INFO.json` (release output, outside the tagged tree). The
`scripts/check_provenance.py` guard asserts `HEAD == tag^{} == BUILD_INFO.commit`
before publication.

## Validation

```text
tests:               609 passed / 0 failed / 0 skipped  (594 pre-existing + 15 provenance-guard tests; python -m pytest -o addopts="" -q)
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
file:                          dist/opencode-widget-0.9.0-rc.1-win-x64.zip
size:                          144,588,592 bytes (137.89 MB)
Pre-publication candidate
SHA256:                        3E6B4B3D8541E5B8F8E510044811A0C1318D1DE464390712AB044F86EC5D635C
                               (this was a pre-tag candidate built from a commit that the
                               v0.9.0-rc.1 tag does not point to; it is NOT the final tagged
                               artifact and must not be published as such)
sanitizer:                     clean (0 forbidden / 0 secret / 0 debug-hook findings)
final tagged artifact checksum: generated after the source tag is created and recorded
                               outside the tagged tree in dist/SHA256SUMS.txt — it is
                               deliberately not committed back into tagged source
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
