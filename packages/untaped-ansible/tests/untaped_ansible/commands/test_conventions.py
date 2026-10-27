"""CLI convention tests for the Ansible plugin (docs/reference/conventions.md).

Covers the renamed verbs and flags (removed spellings are usage errors through
the ``untaped`` root), mutation outcome records, destructive confirmation and
usage exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ansible_test_support import read_state, run, write_state
from untaped.sdk.testing import ScriptedTerminal

_SOURCES = {"sources": [{"name": "prod", "repos": ["acme/site"]}]}
_ALIASES = {"aliases": {"common": "acme/common"}}


def _json(stdout: str) -> Any:
    return json.loads(stdout)


# --- source-alias --------------------------------------------------------


def test_source_alias_set_emits_created_then_unchanged_then_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run("source-alias", "set", "common", "acme/common", "-f", "json")
    again = run("source-alias", "set", "common", "acme/common", "-f", "json")
    moved = run("source-alias", "set", "common", "acme/other", "-f", "pipe")

    assert first.exit_code == 0, first.output
    assert _json(first.stdout) == {"action": "created", "alias": "common", "repo": "acme/common"}
    assert _json(again.stdout)["action"] == "unchanged"
    envelope = _json(moved.stdout)
    assert envelope["kind"] == "ansible.source_alias_outcome"
    assert envelope["record"]["action"] == "updated"
    assert read_state()["aliases"] == {"common": "acme/other"}


def test_source_alias_set_rejects_non_repo_target_as_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run("source-alias", "set", "foo", "bar")

    assert result.exit_code == 2
    assert "owner/name" in result.stderr
    assert "aliases" not in read_state()


# --- destructive removal (source-alias remove / source remove) -----------

_REMOVALS = pytest.mark.parametrize(
    ("group", "name", "state", "record"),
    [
        ("source-alias", "common", _ALIASES, {"alias": "common", "repo": "acme/common"}),
        ("source", "prod", _SOURCES, {"name": "prod", "changes": []}),
    ],
)


@_REMOVALS
def test_remove_requires_yes_without_a_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    name: str,
    state: dict[str, object],
    record: dict[str, object],
) -> None:
    write_state(state)

    result = run(group, "remove", name)

    assert result.exit_code == 2
    assert f"{group} remove requires --yes when not interactive" in result.stderr
    assert read_state() == state


@_REMOVALS
@pytest.mark.parametrize("confirm", [True, False])
def test_remove_prompts_and_honours_the_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    name: str,
    state: dict[str, object],
    record: dict[str, object],
    confirm: bool,
    terminal: ScriptedTerminal,
) -> None:
    write_state(state)
    terminal.answers(confirm)

    result = run(group, "remove", name, "-f", "json")

    assert terminal.asked and terminal.asked[0][0] == "confirm"
    if confirm:
        assert result.exit_code == 0, result.output
        assert _json(result.stdout) == {"action": "deleted", **record}
        assert read_state() == {}
    else:
        assert result.exit_code == 1
        assert "cancelled; no changes made" in result.stderr
        assert read_state() == state


@_REMOVALS
def test_remove_dry_run_plans_without_prompting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    name: str,
    state: dict[str, object],
    record: dict[str, object],
) -> None:
    write_state(state)

    result = run(group, "remove", name, "--dry-run", "-f", "json")

    assert result.exit_code == 0, result.output
    assert _json(result.stdout) == {"action": "planned", **record}
    assert read_state() == state


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["source-alias", "remove", "missing", "--yes"],
            "error: source alias not found: 'missing'; known: common",
        ),
        (["source", "get", "missing"], "error: source not found: 'missing'; known: prod"),
    ],
)
def test_unknown_names_list_the_known_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str
) -> None:
    write_state({**_ALIASES, **_SOURCES})

    result = run(*args)

    assert result.exit_code == 1
    assert message in result.stderr


# --- source --------------------------------------------------------------


def test_source_set_emits_created_then_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run("source", "set", "prod", "--repo", "acme/site", "-f", "json")
    again = run("source", "set", "prod", "--repo", "acme/site", "-f", "json")
    changed = run("source", "set", "prod", "--repo", "acme/api", "-f", "pipe")

    assert first.exit_code == 0, first.output
    assert _json(first.stdout) == {"action": "created", "name": "prod", "changes": []}
    assert _json(again.stdout)["action"] == "unchanged"
    envelope = json.loads(changed.stdout)
    assert envelope["kind"] == "ansible.source_outcome"
    assert envelope["record"]["action"] == "updated"


def test_source_patch_emits_changes_as_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_state(_SOURCES)

    result = run("source", "patch", "prod", "--add-repo", "acme/api", "-f", "json")
    noop = run("source", "patch", "prod", "--add-repo", "acme/api", "-f", "json")

    assert result.exit_code == 0, result.output
    assert _json(result.stdout) == {
        "action": "updated",
        "name": "prod",
        "changes": ["added repo acme/api"],
    }
    assert _json(noop.stdout) == {"action": "unchanged", "name": "prod", "changes": []}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["prod"], "source patch requires at least one mutation flag"),
        (
            ["prod", "--ref-scan-default", "all", "--clear-ref-scan-default"],
            "--ref-scan-default cannot be combined with --clear-ref-scan-default",
        ),
    ],
)
def test_source_patch_flag_problems_are_usage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str
) -> None:
    write_state(_SOURCES)

    result = run("source", "patch", *args)

    assert result.exit_code == 2
    assert message in result.stderr


@pytest.mark.parametrize(
    ("args", "state", "exit_code"),
    [
        (["set", "prod", "--repo", "not-a-repo"], None, 2),
        (["patch", "prod", "--add-repo", "not-a-repo"], _SOURCES, 1),
    ],
)
def test_source_validation_errors_print_one_readable_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    state: dict[str, object] | None,
    exit_code: int,
) -> None:
    if state is not None:
        write_state(state)

    result = run("source", *args)

    assert result.exit_code == exit_code
    assert "error: repo must be owner/name: 'not-a-repo'" in result.stderr
    assert "pydantic" not in result.stderr
    assert "validation error" not in result.stderr


# --- parallel / out flags -------------------------------------------------


def test_impact_help_hides_live() -> None:
    result = run("impact", "--help")

    assert result.exit_code == 0, result.output
    assert "--live" not in result.output
    assert "--refresh" in result.output


def test_graph_help_lists_current_flags_only() -> None:
    result = run("graph", "--help")
    output = " ".join(result.output.replace("│", " ").split())

    assert result.exit_code == 0, result.output
    shown = "--direction --all-refs --source --refresh --cached --live --target-repo"
    shown += " --parallel --out"
    hidden = "--concurrency --output --kind --cache-backend --scope --contains --stdin"
    assert [flag for flag in shown.split() if flag not in output] == []
    assert [flag for flag in hidden.split() if flag in output] == []


def test_graph_out_writes_the_rendered_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "role"
    target.mkdir()
    out = tmp_path / "graph.json"

    result = run(
        "graph", str(target), "--target-repo", "acme/role", "--direction=down", "-f", "json",
        "--out", str(out),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["target_id"]


def test_parallel_rejects_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_state(_SOURCES)

    result = run("source", "refresh", "prod", "-j", "0")

    assert result.exit_code == 2
