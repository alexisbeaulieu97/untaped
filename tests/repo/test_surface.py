"""``tests/repo/surface.txt``: current, and keeping every stable line of the last release.

The file lists what ``docs/versioning.md`` promises, one line per promise
(:mod:`untaped.surface` holds the grammar and the rules). A minor release
keeps every stable line of the newest final tag; a major in preparation (an
``upgrading`` fragment in ``changelog.d/``, or a workspace version whose
major is past the tag's) may drop them, except a deprecated line dated for a
later major.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import shlex
import subprocess
import sys
from pathlib import Path

import gen_surface as generator  # scripts/gen_surface.py
import pytest
import release  # scripts/release.py
from packaging.version import Version

from repo.support import (
    PACKAGES,
    REPO_ROOT,
    SURFACE_FILE,
    baseline_tag,
    git,
    no_baseline,
    release_baseline,
)
from untaped.contracts._fills import FILLS_FILE
from untaped.deprecated_keys import DECLARATIONS
from untaped.git import GitCommandError
from untaped.management import plugin_check
from untaped.plugins.registry import PLUGINS_ENTRY_POINT_GROUP
from untaped.surface import DeprecatedMark, Line, LineMark, parse, removed
from untaped.surface._lists import CONVENTIONS
from untaped_git.infrastructure import worktree_config

REGENERATE = "uv run python scripts/gen_surface.py"


@pytest.fixture(scope="module")
def generated() -> str:
    """The file as the generator writes it now (generated once: it takes a second or two)."""
    return generator.render()


def test_surface_is_current(generated: str) -> None:
    committed = (REPO_ROOT / SURFACE_FILE).read_text(encoding="utf-8")
    assert committed == generated, f"{SURFACE_FILE} is stale; run: {REGENERATE}"


def test_generation_does_not_depend_on_home(
    generated: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert generator.render() == generated


# --- removals since the last release -------------------------------------------


def removal_target(version: str, baseline: str, fragments: list[str]) -> int:
    """The major whose removals may land: the next one while an ``upgrading`` fragment waits."""
    if any(name.endswith(".upgrading.md") for name in fragments):
        return Version(baseline.removeprefix("v")).major + 1
    return Version(version).major


def removal_exempt(line: Line, target: int, baseline: str) -> bool:
    """Whether ``line`` may be gone: a dated one at its major, any other in a major's prep.

    A frozen line never may: stubs installed by every version call it.
    """
    if line.mark == "frozen":
        return False
    mark = line.deprecation
    if mark is not None and mark.removed_in is not None:
        return mark.removed_in <= target
    return target > Version(baseline.removeprefix("v")).major


#: Stable lines a minor drops anyway, by the baseline release they left: the
#: maintainer's waiver of "a breaking change waits for the next major"
#: (CONTRIBUTING § Releasing), each marked breaking in its changelog fragment.
#: Name the exact line; an entry stops matching once its baseline is not the
#: newest release, and the next pre-release cleanup removes it.
MINOR_WAIVERS: dict[str, frozenset[str]] = {
    # skills.updates moves to auto with stub skills (#694): under warn, every
    # upgrader would see a warning after every command until they updated.
    "v11.0.0": frozenset({"cfg skills.updates string default=warn"}),
}


def removal_message(tag: str, gone: list[Line]) -> str:
    major = Version(tag.removeprefix("v")).major
    return "\n".join(
        [
            f"Removed since {tag}, which every {major}.x release promises to keep:",
            *(f"  {line}" for line in gone),
            "Keep the old spelling as a deprecated alias (deprecated_alias, renamed_keys,",
            f"Cache(renamed_from=), a deprecation warning) until {major + 1}.0, or move the "
            f"issue to the {major + 1}.0",
            "milestone, where it merges with an upgrading fragment.",
        ]
    )


def test_no_stable_surface_removed(generated: str) -> None:
    tag = release_baseline()
    try:
        recorded = git("show", f"{tag}:{SURFACE_FILE}")
    except GitCommandError:
        pytest.skip(f"the baseline {tag} predates {SURFACE_FILE}")
    fragments = [path.name for path in (REPO_ROOT / "changelog.d").glob("*.md")]
    target = removal_target(release.release_version(REPO_ROOT), tag, fragments)
    gone = [
        line
        for line in removed(parse(recorded), parse(generated))
        if not removal_exempt(line, target, tag)
        and line.text not in MINOR_WAIVERS.get(tag, frozenset())
    ]
    assert not gone, removal_message(tag, gone)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ([], None),
        (["v10.1.0", "v9.0.0", "v11.0.0rc1", "v11.0.0.dev1", "nightly"], "v10.1.0"),
        (["v10.1.0", "v10.10.0", "v10.2.0"], "v10.10.0"),
    ],
)
def test_the_baseline_is_the_newest_final_tag(tags: list[str], expected: str | None) -> None:
    assert baseline_tag(tags) == expected


@pytest.mark.parametrize(
    ("version", "fragments", "target"),
    [
        ("11.2.0", ["x.added.md"], 11),
        ("11.2.0", ["x.upgrading.md"], 12),
        ("12.0.0rc1", [], 12),
        ("12.0.0", [], 12),
    ],
)
def test_the_removal_target(version: str, fragments: list[str], target: int) -> None:
    assert removal_target(version, "v11.1.0", fragments) == target


@pytest.mark.parametrize(
    ("mark", "target", "exempt"),
    [
        # A minor keeps everything it promised.
        (None, 11, False),
        (DeprecatedMark(), 11, False),
        (DeprecatedMark(12), 11, False),
        # A major's prep drops stable and undated lines, and what is dated for it.
        (None, 12, True),
        (DeprecatedMark(), 12, True),
        (DeprecatedMark(12), 12, True),
        (DeprecatedMark(11), 12, True),
        # A line dated later stays: it was promised that long.
        (DeprecatedMark(13), 12, False),
        # A frozen line stays through every release, a major's prep included.
        ("frozen", 11, False),
        ("frozen", 12, False),
    ],
)
def test_a_major_in_preparation_removes_what_is_due(
    mark: LineMark | None, target: int, exempt: bool
) -> None:
    assert removal_exempt(Line("cli untaped awx put", mark), target, "v11.1.0") is exempt


def test_a_clone_without_tags_fails_under_ci_and_skips_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="CI must check out with tags"):
        no_baseline()
    monkeypatch.delenv("CI")
    with pytest.raises(pytest.skip.Exception, match="fetch tags"):
        no_baseline()


def test_the_removal_message_names_the_way_out() -> None:
    message = removal_message("v11.2.0", [Line("sdk untaped.sdk.bounded_map(func, items)")])
    assert message.startswith("Removed since v11.2.0, which every 11.x release promises to keep:")
    assert "  sdk untaped.sdk.bounded_map(func, items)" in message
    assert "until 12.0" in message


# --- what the walker can't read from declarations -------------------------------


def test_overrides_are_declared() -> None:
    """A member a class overrides on an exported SDK class is a hook the SDK reads.

    Each must be in ``HOOKS`` for that base, or the base on ``IMPLEMENTERS``
    (``Contract`` subclasses are implementers by rule). The sweep imports every
    first-party and example module, so it runs in its own process: the
    examples declare record kinds this suite's own fakes declare too.
    """
    done = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("override_sweep.py"))],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert not done.stdout, (
        "these override members of exported SDK classes; list each in HOOKS "
        "(untaped/surface/_lists.py) with why the SDK reads it, or its class in "
        "IMPLEMENTERS:\n" + done.stdout
    )


def test_written_outside_spellings_have_lines(generated: str) -> None:
    """The git credential helper untaped writes into worktree configs uses promised spellings."""
    words = shlex.split(worktree_config._helper_command("work").removeprefix("!"))[1:]
    assert words == ["--profile", "work", "git", "credential"]
    lines = {line.text for line in parse(generated)}
    assert "cli untaped --profile VALUE default=unset" in lines
    assert "cli untaped git credential ACTION" in lines
    for action in ("get", "store", "erase"):
        assert f"cli untaped git credential ACTION choice={action}" in lines


def test_conventions_are_real() -> None:
    """Each ``convention`` line names something core really finds by that name."""
    auth = (PACKAGES / "untaped" / "src" / "untaped" / "auth.py").read_text(encoding="utf-8")
    conformance = inspect.getsource(plugin_check._owner_conformance)
    real = {
        f"entry-point {PLUGINS_ENTRY_POINT_GROUP}": True,
        "module untaped.testing.plugin": importlib.util.find_spec("untaped.testing.plugin")
        is not None,
        "module <owner>.testing.conformance": 'f"{package}.testing"' in conformance
        and '"conformance"' in conformance,
        f"file {FILLS_FILE}": True,
        "attribute renamed_keys": "renamed_keys" in DECLARATIONS,
        "attribute retired_keys": "retired_keys" in DECLARATIONS,
        "attribute token_sources": 'getattr(type(settings), "token_sources"' in auth,
    }
    assert {name for name, ok in real.items() if ok} == set(CONVENTIONS)
