"""Attribution / publication regression guards.

These static checks make sure the upstream credit and the "no new blanket license" posture are
not accidentally removed, and that no LICENSE file is introduced over upstream-derived code.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM_URL = "https://github.com/ikunops/opencode-widget"
BASE_COMMIT = "37e399e343789a5e7efd92c5cab626527f2bf05c"


def _read(name):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as fh:
        return fh.read()


def test_upstream_md_present_and_complete():
    src = _read("UPSTREAM.md")
    assert UPSTREAM_URL in src
    assert BASE_COMMIT in src
    assert "@ikunops" in src
    # descriptions must say "fork"/"based on", not only "inspired by"
    low = src.lower()
    assert "fork" in low or "based on" in low
    assert "license" in low


def test_readme_has_top_attribution_and_upstream_link():
    src = _read("README.md")
    assert UPSTREAM_URL in src
    assert "UPSTREAM.md" in src
    assert "@ikunops" in src
    # attribution must appear near the top (not only in a footer)
    head = src[:2000]
    assert "ikunops/opencode-widget" in head or "UPSTREAM.md" in head


def test_readme_independence_disclaimer():
    src = _read("README.md")
    low = src.lower()
    assert "independent community project" in low
    assert "not affiliated" in low or "no affiliation" in low


def test_readme_states_python_requirement():
    src = _read("README.md")
    assert "Python 3.11+" in src


def test_changelog_credits_upstream():
    src = _read("CHANGELOG.md")
    assert "ikunops/opencode-widget" in src


def test_no_license_file_asserted():
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md"):
        assert not os.path.exists(os.path.join(ROOT, name)), (
            f"{name} must not be added while upstream licensing is unclarified"
        )
