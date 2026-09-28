"""Tag provenance guard for the OpenCode Widget RC build pipeline.

Verifies a strict one-to-one relationship between:

    source tag  ->  tagged commit  ->  BUILD_INFO commit

The build emits ``BUILD_INFO.json`` next to the artifact with a *short* commit
(``git rev-parse --short HEAD``).  That file lives in ``dist/`` (release output)
and is deliberately **not** part of the tagged source tree, so its checksum can be
recorded as post-tag release metadata without creating a self-referential
provenance loop.

The guard is pure stdlib and usable both as a CLI and from tests::

    python scripts/check_provenance.py --tag v0.9.0-rc.1
    python scripts/check_provenance.py --tag v0.9.0-rc.1 --build-info dist/BUILD_INFO.json

Exit status is ``0`` (PASS) only when every requested assertion holds, otherwise
``1`` (FAIL).  It never mutates the repository and never requires network access.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


class ProvenanceError(RuntimeError):
    """Raised when git cannot resolve a requested revision."""


def _git(repo: str, *args: str) -> str:
    """Run a read-only git command in ``repo`` and return trimmed stdout."""
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise ProvenanceError(f"git {' '.join(args)} failed: {stderr or 'unknown error'}")
    return proc.stdout.strip()


def resolve_commits(repo: str, tag: str) -> tuple[str, str]:
    """Return ``(head_commit, tag_peeled_commit)`` for ``repo``/``tag``."""
    head = _git(repo, "rev-parse", "HEAD")
    tagged = _git(repo, "rev-parse", f"{tag}^{{}}")
    return head, tagged


def short_commit_matches(short: str, full: str) -> bool:
    """True when a short/abbreviated commit unambiguously identifies ``full``."""
    short = (short or "").strip().lower()
    full = (full or "").strip().lower()
    if not short or not full:
        return False
    return full.startswith(short)


def read_build_info(path: str) -> dict:
    """Read and parse ``BUILD_INFO.json``; raise ``ProvenanceError`` on problems."""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ProvenanceError(f"cannot read build info {path}: {exc}") from exc
    except ValueError as exc:
        raise ProvenanceError(f"build info {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProvenanceError(f"build info {path} is not a JSON object")
    return data


def check_provenance(repo: str, tag: str, build_info_path: str | None = None) -> dict:
    """Evaluate the provenance assertions.

    Returns a result dict with ``ok`` plus ``checks``.  A ``check`` is a mapping of
    ``name`` -> ``{"ok": bool, "detail": str}``.  Raises ``ProvenanceError`` for
    hard resolution failures (missing git repo, unknown tag, unreadable build
    info) so callers can distinguish "cannot verify" from "verified mismatch".
    """
    head, tagged = resolve_commits(repo, tag)

    checks: dict[str, dict] = {
        "tag_exists": {"ok": True, "detail": f"tag {tag} -> {tagged}"},
        "head_equals_tag": {
            "ok": head == tagged,
            "detail": f"HEAD={head} tag={tagged}",
        },
    }

    if build_info_path is not None:
        info = read_build_info(build_info_path)
        commit = str(info.get("commit", "")).strip()
        version = str(info.get("version", "")).strip()
        checks["build_info_commit_equals_tag"] = {
            "ok": short_commit_matches(commit, tagged),
            "detail": f"BUILD_INFO.commit={commit or '<missing>'} tag={tagged}",
        }
        if version:
            checks["build_info_version_present"] = {"ok": True, "detail": f"version={version}"}
        else:
            checks["build_info_version_present"] = {
                "ok": False,
                "detail": "BUILD_INFO.json has no version field",
            }

    return {
        "ok": all(item["ok"] for item in checks.values()),
        "repo": repo,
        "tag": tag,
        "head": head,
        "tagged_commit": tagged,
        "build_info_path": build_info_path,
        "checks": checks,
    }


def _format_report(result: dict) -> str:
    lines = [
        f"tag:              {result['tag']}",
        f"source HEAD:      {result['head']}",
        f"tagged commit:    {result['tagged_commit']}",
    ]
    if result.get("build_info_path"):
        lines.append(f"build info:       {result['build_info_path']}")
    for name, item in result["checks"].items():
        status = "PASS" if item["ok"] else "FAIL"
        lines.append(f"{status}  {name}: {item['detail']}")
    lines.append("RESULT: " + ("PASS" if result["ok"] else "FAIL"))
    return os.linesep.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tag provenance guard")
    parser.add_argument("--repo", default=".", help="repository directory (default: .)")
    parser.add_argument("--tag", required=True, help="source tag to verify")
    parser.add_argument(
        "--build-info",
        default=None,
        help="optional path to BUILD_INFO.json to cross-check",
    )
    args = parser.parse_args(argv)

    try:
        result = check_provenance(args.repo, args.tag, args.build_info)
    except ProvenanceError as exc:
        print(f"RESULT: FAIL")
        print(f"error: {exc}")
        return 1

    print(_format_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
