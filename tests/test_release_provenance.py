"""Tag provenance guard tests.

These tests exercise ``scripts/check_provenance.py`` entirely against throwaway
git repositories created under ``tmp_path`` — they never touch the real checkout,
the network, or user data, and they do not assume any particular tag exists in
this repository.  The live assertion for the actual RC tag is performed by the
release pipeline via ``python scripts/check_provenance.py --tag v0.9.0-rc.1``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_PATH = REPO_ROOT / "scripts" / "check_provenance.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_provenance", _GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _git(repo: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **env},
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _make_repo(tmp_path: Path, tag: str = "v0.9.0-rc.1") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "one")
    _git(repo, "tag", "-a", tag, "-m", "rc")
    return repo


def _write_build_info(
    tmp_path: Path, commit: str, version: str = "0.9.0-rc.1", name: str = "BUILD_INFO.json"
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps({"version": version, "commit": commit, "built_at": "2026-09-28T00:00:00Z"}),
        encoding="utf-8",
    )
    return path


def test_pass_when_head_equals_tag(tmp_path):
    repo = _make_repo(tmp_path)
    result = guard.check_provenance(str(repo), "v0.9.0-rc.1")
    assert result["ok"] is True
    assert result["head"] == result["tagged_commit"]


def test_fail_when_head_moved_after_tag(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "file.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "two")

    result = guard.check_provenance(str(repo), "v0.9.0-rc.1")
    assert result["ok"] is False
    assert result["checks"]["head_equals_tag"]["ok"] is False


def test_build_info_short_commit_matches_tag(tmp_path):
    repo = _make_repo(tmp_path)
    tagged = _git(repo, "rev-parse", "HEAD")
    info = _write_build_info(tmp_path, tagged[:7])

    result = guard.check_provenance(str(repo), "v0.9.0-rc.1", str(info))
    assert result["ok"] is True
    assert result["checks"]["build_info_commit_equals_tag"]["ok"] is True


def test_build_info_mismatch_fails(tmp_path):
    repo = _make_repo(tmp_path)
    info = _write_build_info(tmp_path, "0000000")

    result = guard.check_provenance(str(repo), "v0.9.0-rc.1", str(info))
    assert result["ok"] is False
    assert result["checks"]["build_info_commit_equals_tag"]["ok"] is False


def test_build_info_missing_commit_fails(tmp_path):
    repo = _make_repo(tmp_path)
    path = tmp_path / "BUILD_INFO.json"
    path.write_text(json.dumps({"version": "0.9.0-rc.1"}), encoding="utf-8")

    result = guard.check_provenance(str(repo), "v0.9.0-rc.1", str(path))
    assert result["ok"] is False
    assert result["checks"]["build_info_commit_equals_tag"]["ok"] is False


def test_unknown_tag_raises(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(guard.ProvenanceError):
        guard.check_provenance(str(repo), "v9.9.9-nope")


def test_unreadable_build_info_raises(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(guard.ProvenanceError):
        guard.check_provenance(str(repo), "v0.9.0-rc.1", str(tmp_path / "does-not-exist.json"))


def test_malformed_build_info_raises(tmp_path):
    repo = _make_repo(tmp_path)
    path = tmp_path / "BUILD_INFO.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(guard.ProvenanceError):
        guard.check_provenance(str(repo), "v0.9.0-rc.1", str(path))


@pytest.mark.parametrize(
    "short, full, expected",
    [
        ("fa6965b", "fa6965b4e9bcb0fa31689b7ef6d30d13927bdc7b", True),
        ("FA6965B", "fa6965b4e9bcb0fa31689b7ef6d30d13927bdc7b", True),
        ("0000000", "fa6965b4e9bcb0fa31689b7ef6d30d13927bdc7b", False),
        ("", "fa6965b4e9bcb0fa31689b7ef6d30d13927bdc7b", False),
        ("fa6965b", "", False),
    ],
)
def test_short_commit_matches(short, full, expected):
    assert guard.short_commit_matches(short, full) is expected


def test_cli_exit_zero_and_nonzero(tmp_path):
    repo = _make_repo(tmp_path)
    good = _write_build_info(tmp_path, _git(repo, "rev-parse", "HEAD")[:7], name="good.json")
    bad = _write_build_info(tmp_path, "deadbee", name="bad.json")

    ok_code = guard.main(["--repo", str(repo), "--tag", "v0.9.0-rc.1", "--build-info", str(good)])
    assert ok_code == 0
    bad_code = guard.main(["--repo", str(repo), "--tag", "v0.9.0-rc.1", "--build-info", str(bad)])
    assert bad_code == 1


def test_cli_reports_failure_for_missing_tag(tmp_path, capsys):
    repo = _make_repo(tmp_path)
    code = guard.main(["--repo", str(repo), "--tag", "v0.0.0-missing"])
    assert code == 1
    assert "FAIL" in capsys.readouterr().out
