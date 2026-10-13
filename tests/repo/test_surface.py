"""``tests/repo/surface.txt``: current, and keeping every stable line of the last release.

The file lists what ``docs/versioning.md`` promises, one line per promise
(:mod:`untaped.surface` holds the grammar and the rules). A minor release
keeps every stable line of the newest final tag; a major in preparation (an
``upgrading`` fragment in ``changelog.d/``, or a workspace version whose
major is past the tag's) may drop them.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import gen_surface as generator  # scripts/gen_surface.py
import pytest
import release  # scripts/release.py
from packaging.version import InvalidVersion, Version

from repo.support import PACKAGES, REPO_ROOT
from untaped.contracts._fills import FILLS_FILE
from untaped.deprecated_keys import DECLARATIONS
from untaped.git import GitCommandError, run_git
from untaped.management import plugin_check
from untaped.plugins.registry import PLUGINS_ENTRY_POINT_GROUP
from untaped.surface import Line, parse, removed
from untaped.surface._lists import CONVENTIONS
from untaped_git.infrastructure import worktree_config

REGENERATE = "uv run python scripts/gen_surface.py"
FILE = "tests/repo/surface.txt"


@pytest.fixture(scope="module")
def generated() -> str:
    """The file as the generator writes it now (generated once: it takes a second or two)."""
    return generator.render()


def test_surface_is_current(generated: str) -> None:
    committed = (REPO_ROOT / FILE).read_text(encoding="utf-8")
    assert committed == generated, f"{FILE} is stale; run: {REGENERATE}"


def test_generation_does_not_depend_on_home(
    generated: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert generator.render() == generated


# --- removals since the last release -------------------------------------------


def _git(*args: str) -> str:
    return run_git(["-C", str(REPO_ROOT), *args], timeout=60, capture=True).text


def baseline_tag(tags: list[str]) -> str | None:
    """The newest final release among ``tags`` (``vX.Y.Z``; pre- and dev releases skipped)."""
    found: list[tuple[Version, str]] = []
    for tag in tags:
        try:
            version = Version(tag.removeprefix("v"))
        except InvalidVersion:
            continue
        if tag.startswith("v") and not version.is_prerelease and not version.is_devrelease:
            found.append((version, tag))
    return max(found)[1] if found else None


def removal_exempt(version: str, baseline: str, fragments: list[str]) -> bool:
    """A major in preparation: an ``upgrading`` fragment, or a version past the baseline major."""
    if any(name.endswith(".upgrading.md") for name in fragments):
        return True
    return Version(version).major > Version(baseline.removeprefix("v")).major


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


def no_baseline() -> NoReturn:
    """No release tag to compare with: a CI checkout without tags fails; a local one skips."""
    if os.environ.get("CI"):
        pytest.fail("no release tag reachable from HEAD: CI must check out with tags")
    pytest.skip("no release tag reachable from HEAD (fetch tags to run this check)")


def test_no_stable_surface_removed(generated: str) -> None:
    tag = baseline_tag(_git("tag", "--list", "v*", "--merged", "HEAD").split())
    if tag is None:
        no_baseline()
    try:
        recorded = _git("show", f"{tag}:{FILE}")
    except GitCommandError:
        pytest.skip(f"the baseline {tag} predates {FILE}")
    fragments = [path.name for path in (REPO_ROOT / "changelog.d").glob("*.md")]
    if removal_exempt(release.release_version(REPO_ROOT), tag, fragments):
        pytest.skip("a major release in preparation may remove stable lines")
    gone = removed(parse(recorded), parse(generated))
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
    ("version", "fragments", "exempt"),
    [
        ("11.2.0", ["x.added.md"], False),
        ("11.2.0", ["x.upgrading.md"], True),
        ("12.0.0rc1", [], True),
        ("12.0.0", [], True),
    ],
)
def test_a_major_in_preparation_may_remove(
    version: str, fragments: list[str], exempt: bool
) -> None:
    assert removal_exempt(version, "v11.1.0", fragments) is exempt


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
