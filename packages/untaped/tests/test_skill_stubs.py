"""Stub skills: the template, the marker, and the one "newer" rule."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from untaped.skill_stubs import (
    STUB_MARKER,
    Comparison,
    StubAuthor,
    compare,
    digest,
    marker_text,
    newer_side,
    read_marker,
    render_stub,
)


def _author(
    untaped: str = "11.1.0", plugin: str = "untaped-jira", version: str = "11.1.0"
) -> StubAuthor:
    return StubAuthor(untaped=untaped, plugin=plugin, plugin_version=version)


@pytest.mark.parametrize(
    ("written", "run", "expected"),
    [
        (_author(), _author(), Comparison.same),
        (_author(), _author(untaped="11.2.0"), Comparison.newer),
        (_author(), _author(version="11.2.0"), Comparison.newer),
        (_author(), _author(untaped="11.2.0", version="11.2.0"), Comparison.newer),
        (_author(), _author(untaped="11.2.0", version="11.0.0"), Comparison.same),
        (_author(), _author(untaped="11.0.0", version="11.2.0"), Comparison.same),
        (_author(), _author(untaped="11.0.0"), Comparison.older),
        # Dev builds order before their release (PEP 440).
        (_author(), _author(untaped="11.1.0.dev3"), Comparison.older),
        (_author(untaped="11.1.0.dev3"), _author(), Comparison.newer),
    ],
    ids=[
        "equal",
        "untaped-only",
        "plugin-only",
        "both",
        "untaped-newer-plugin-older",
        "untaped-older-plugin-newer",
        "older",
        "dev-run",
        "dev-stub",
    ],
)
def test_a_run_is_newer_or_older_only_when_neither_side_disagrees(
    written: StubAuthor, run: StubAuthor, expected: Comparison
) -> None:
    assert compare(written, run) is expected


@pytest.mark.parametrize(
    "run",
    [
        _author(untaped="11.2.0", plugin="acme-jira", version="2.0"),
        _author(untaped="11.2.0", version=""),
        _author(untaped="11.2.0", version="not a version"),
    ],
    ids=["another-distribution", "unknown-version", "unparsable-version"],
)
def test_an_incomparable_plugin_side_counts_as_equal(run: StubAuthor) -> None:
    # Only untaped's version decides: newer refreshes, equal and older leave alone.
    assert compare(_author(version="9.9.9"), run) is Comparison.newer
    assert compare(_author(untaped="11.2.0", version="9.9.9"), run) is Comparison.same
    assert compare(_author(untaped="11.3.0", version="9.9.9"), run) is Comparison.older


def test_distribution_names_compare_normalized() -> None:
    run = _author(plugin="Untaped_Jira", version="11.0.0")
    assert compare(_author(), run) is Comparison.older


def test_an_unparsable_untaped_version_never_fails() -> None:
    assert compare(_author(untaped="garbage"), _author()) is Comparison.same


def test_newer_side_names_what_wrote_the_stub() -> None:
    assert newer_side(_author(untaped="11.3.0"), _author()) == "untaped (11.3.0)"
    assert newer_side(_author(version="11.3.0"), _author()) == "untaped-jira (11.3.0)"


#: The command forms a stub may name: the frozen ``agents`` entry points, the
#: ``--help`` fallback for an untaped older than the stub, and the example of
#: running a project's own untaped.
_FROZEN_FORMS = re.compile(
    r"untaped|uv run untaped|untaped agents( \w[\w-]*( COMMAND)?)?|untaped( [\w-]+)? --help"
)


@pytest.mark.parametrize("plugin", ["workspace", "untaped"])
def test_the_template_names_only_frozen_command_forms(plugin: str) -> None:
    # Stubs installed by every version call these; changing them strands them.
    text = render_stub(f"untaped-{plugin}", plugin, "Workspaces.")
    commands = [
        span for span in re.findall(r"`([^`]+)`", text) if span.startswith(("untaped", "uv "))
    ]
    assert commands
    assert all(_FROZEN_FORMS.fullmatch(command) for command in commands), commands


def test_a_plugin_stub_points_at_its_plugin_and_its_help() -> None:
    text = render_stub("untaped-workspace", "workspace", 'Workspaces: "one dir" per task.')
    assert text.startswith(
        '---\nname: untaped-workspace\ndescription: "Workspaces: \\"one dir\\" per task."\n---\n'
    )
    assert "# untaped workspace" in text
    assert "`untaped agents workspace`" in text
    assert "`untaped agents workspace COMMAND`" in text
    assert "`untaped workspace --help`" in text


def test_core_s_stub_falls_back_to_root_help() -> None:
    text = render_stub("untaped", "untaped", "untaped itself.")
    assert "# untaped\n" in text
    assert "`untaped agents untaped`" in text
    assert "`untaped --help`" in text


def test_the_marker_round_trips(tmp_path: Path) -> None:
    author = _author(version="")
    text = marker_text(
        author,
        sha256="abc",
        name="untaped-jira",
        target="claude",
        scope="global",
        install_root=tmp_path,
    )
    (tmp_path / STUB_MARKER).write_text(text, encoding="utf-8")
    marker = read_marker(tmp_path)
    assert marker is not None
    assert (marker.author, marker.sha256, marker.target, marker.scope) == (
        author,
        "abc",
        "claude",
        "global",
    )
    assert '"format": 1' in text


@pytest.mark.parametrize("text", ["", "{}", "[]", '{"untaped": "1"}'])
def test_an_unreadable_marker_reads_as_none(tmp_path: Path, text: str) -> None:
    (tmp_path / STUB_MARKER).write_text(text, encoding="utf-8")
    assert read_marker(tmp_path) is None


def test_the_digest_sees_an_added_file() -> None:
    assert digest({"SKILL.md": b"x"}) != digest({"SKILL.md": b"x", "notes.md": b""})
