"""A major release ships without the previous major's deprecated spellings.

Fires when the workspace version is ``X.0.0`` (or an ``X.0.0`` pre-release),
``CHANGELOG.md`` has a section for that version and ``changelog.d/`` holds no
fragments, which is the state of a major release PR and of main just after the
release. Between releases main carries the last released version, so the
version alone would fire there.

The previous major's spellings are the lines its newest release (never the
major's own tag) marks ``[deprecated]`` in ``tests/repo/surface.txt``: what
that release warned about. A spelling deprecated while the major was being
prepared reached no release yet, so it stays, with its warning, until the next
major. Retired keys
never count: they have no surface line, and are what a major leaves behind for
``config migrate``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest
import release  # scripts/release.py (``pythonpath = ["scripts"]``)
from packaging.version import Version

import changelog  # scripts/changelog.py
from repo.support import REPO_ROOT, SURFACE_FILE, baseline_tag, git, release_baseline
from untaped.git import GitCommandError
from untaped.surface import Line, parse


def is_major_release(version: str, text: str, fragments: Sequence[str]) -> bool:
    """Whether ``version`` with this changelog ``text`` and fragments is a major release in prep."""
    parsed = Version(version)
    built = re.search(rf"^## {re.escape(version)}$", text, re.MULTILINE) is not None
    return parsed.minor == 0 and parsed.micro == 0 and built and not fragments


def leftovers(released: Sequence[Line], current: Sequence[Line]) -> list[Line]:
    """The lines ``released`` marks deprecated that ``current`` still has, marked or not."""
    present = {line.identity for line in current}
    return sorted(
        line for line in released if line.mark == "deprecated" and line.identity in present
    )


def test_a_major_release_drops_deprecated_spellings() -> None:
    version = release.release_version(REPO_ROOT)
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not is_major_release(version, text, changelog.fragment_paths(REPO_ROOT)):
        pytest.skip(f"{version} is not a major release being prepared")
    tag = release_baseline(before_major=Version(version).major)
    try:
        released = parse(git("show", f"{tag}:{SURFACE_FILE}"))
    except GitCommandError:
        pytest.skip(f"{tag} predates {SURFACE_FILE}, so it records no deprecations")
    # test_surface keeps the committed file equal to what the code generates.
    current = parse((REPO_ROOT / SURFACE_FILE).read_text(encoding="utf-8"))

    found = leftovers(released, current)

    assert not found, f"{tag} deprecated these; remove them before releasing {version}:\n" + (
        "\n".join(f"  {line.identity}" for line in found)
    )


@pytest.mark.parametrize(
    ("version", "text", "fragments", "expected"),
    [
        ("11.0.0", "# Changelog\n\n## 11.0.0\n", [], True),
        ("11.0.0rc1", "# Changelog\n\n## 11.0.0rc1\n", [], True),
        # A pre-release section is retitled by the final build, so the final version needs its own.
        ("11.0.0", "# Changelog\n\n## 11.0.0rc1\n", [], False),
        # Fragments left in changelog.d/ mean the release has not been built (main before it).
        ("11.0.0", "# Changelog\n\n## 10.0.0\n", ["changelog.d/x.added.md"], False),
        ("11.0.0", "# Changelog\n\n## 10.0.0\n", [], False),
        ("11.1.0", "# Changelog\n\n## 11.1.0\n", [], False),
        ("11.0.1", "# Changelog\n\n## 11.0.1\n", [], False),
    ],
)
def test_is_major_release(version: str, text: str, fragments: list[str], expected: bool) -> None:
    assert is_major_release(version, text, fragments) is expected


def test_leftovers_are_what_the_last_release_deprecated_and_still_exists() -> None:
    released = [
        Line("sdk untaped.sdk.old(x)", "deprecated"),
        Line("cli untaped jira me -> untaped jira whoami", "deprecated"),
        Line("cfg acme.old -> acme.new", "deprecated"),
        Line("sdk untaped.sdk.kept(x)"),
    ]
    current = [
        Line("sdk untaped.sdk.old(x)", "deprecated"),
        Line("cfg acme.old -> acme.new"),  # a dropped mark does not hide it
        # Deprecated since that release: it warned in no release yet, so it stays.
        Line("sdk untaped.sdk.kept(x)", "deprecated"),
    ]

    assert leftovers(released, current) == [
        Line("cfg acme.old -> acme.new", "deprecated"),
        Line("sdk untaped.sdk.old(x)", "deprecated"),
    ]


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        # On main just after the major's tag, the baseline is still the previous major.
        (["v10.1.0", "v11.0.0"], "v10.1.0"),
        (["v10.0.0", "v10.1.0", "v11.0.0rc1"], "v10.1.0"),
        (["v11.0.0"], None),
    ],
)
def test_the_baseline_is_the_previous_majors_newest_release(
    tags: list[str], expected: str | None
) -> None:
    assert baseline_tag(tags, before_major=11) == expected
