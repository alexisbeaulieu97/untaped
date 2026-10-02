"""CLI convention tests for the Ansible capability (docs/conventions.md).

Covers the renamed verbs and flags (removed spellings are usage errors through
the ``untaped`` root), mutation outcome records, destructive confirmation and
usage exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import first_party_candidates
from untaped.bootstrap import build_root_app
from untaped.capabilities.ansible.cli import app
from untaped.testing import ScriptedPromptBackend, invoke_cli

_SOURCES = {"sources": [{"name": "prod", "repos": ["acme/site"]}]}
_ALIASES = {"aliases": {"common": "acme/common"}}


def _config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: dict[str, object] | None = None,
) -> Path:
    cfg = tmp_path / "config.yml"
    profile = {"ansible": {"index_path": str(tmp_path / "index.sqlite3")}}
    cfg.write_text(yaml.safe_dump({"profiles": {"default": profile}}))
    if state is not None:
        (tmp_path / "state.yml").write_text(yaml.safe_dump({"ansible": state}))
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


def _state(tmp_path: Path) -> dict[str, Any]:
    state_file = tmp_path / "state.yml"
    if not state_file.exists():
        return {}
    return (yaml.safe_load(state_file.read_text()) or {}).get("ansible", {})


def _json(stdout: str) -> Any:
    return json.loads(stdout)


# --- source-alias --------------------------------------------------------


def test_source_alias_set_emits_created_then_unchanged_then_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    first = invoke_cli(app, ["source-alias", "set", "common", "acme/common", "-f", "json"])
    again = invoke_cli(app, ["source-alias", "set", "common", "acme/common", "-f", "json"])
    moved = invoke_cli(app, ["source-alias", "set", "common", "acme/other", "-f", "pipe"])

    assert first.exit_code == 0, first.output
    assert _json(first.stdout) == {"action": "created", "alias": "common", "repo": "acme/common"}
    assert _json(again.stdout)["action"] == "unchanged"
    envelope = _json(moved.stdout)
    assert envelope["kind"] == "ansible.source_alias_outcome"
    assert envelope["record"]["action"] == "updated"
    assert _state(tmp_path)["aliases"] == {"common": "acme/other"}


def test_source_alias_set_rejects_non_repo_target_as_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    result = invoke_cli(app, ["source-alias", "set", "foo", "bar"])

    assert result.exit_code == 2
    assert "owner/name" in result.stderr
    assert "aliases" not in _state(tmp_path)


@pytest.mark.parametrize(
    "args",
    [
        ["alias", "list"],
        ["source-alias", "add", "common", "acme/common"],
        ["source", "save", "prod", "--repo", "acme/site"],
        ["source", "edit", "prod", "--add-repo", "acme/api"],
        ["source", "show", "prod"],
        ["source", "refresh", "prod", "--concurrency", "4"],
        ["graph", "acme/site", "--concurrency", "4"],
        ["graph", "acme/site", "--output", "graph.json"],
        ["graph", "acme/site", "--contains", "acme/base"],
        ["graph", "--stdin"],
    ],
)
def test_removed_spellings_are_usage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    _config(tmp_path, monkeypatch, state={**_ALIASES, **_SOURCES})

    result = invoke_cli(
        build_root_app(candidates=first_party_candidates()), ["ansible", *args], input="acme/site\n"
    )

    assert result.exit_code == 2, result.output
    assert "deprecated" not in result.stderr
    assert _state(tmp_path) == {**_ALIASES, **_SOURCES}


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
    _config(tmp_path, monkeypatch, state=state)

    result = invoke_cli(app, [group, "remove", name])

    assert result.exit_code == 2
    assert f"{group} remove requires --yes when not interactive" in result.stderr
    assert _state(tmp_path) == state


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
) -> None:
    _config(tmp_path, monkeypatch, state=state)
    backend = ScriptedPromptBackend(confirms=[confirm])

    result = invoke_cli(
        app, [group, "remove", name, "-f", "json"], terminal=True, prompt_backend=backend
    )

    assert backend.calls and backend.calls[0][0] == "confirm"
    if confirm:
        assert result.exit_code == 0, result.output
        assert _json(result.stdout) == {"action": "deleted", **record}
        assert _state(tmp_path) == {}
    else:
        assert result.exit_code == 1
        assert "cancelled; no changes made" in result.stderr
        assert _state(tmp_path) == state


@_REMOVALS
def test_remove_dry_run_plans_without_prompting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group: str,
    name: str,
    state: dict[str, object],
    record: dict[str, object],
) -> None:
    _config(tmp_path, monkeypatch, state=state)

    result = invoke_cli(app, [group, "remove", name, "--dry-run", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert _json(result.stdout) == {"action": "planned", **record}
    assert _state(tmp_path) == state


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
    _config(tmp_path, monkeypatch, state={**_ALIASES, **_SOURCES})

    result = invoke_cli(app, args)

    assert result.exit_code == 1
    assert message in result.stderr


# --- source --------------------------------------------------------------


def test_source_set_emits_created_then_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    first = invoke_cli(app, ["source", "set", "prod", "--repo", "acme/site", "-f", "json"])
    again = invoke_cli(app, ["source", "set", "prod", "--repo", "acme/site", "-f", "json"])
    changed = invoke_cli(app, ["source", "set", "prod", "--repo", "acme/api", "-f", "pipe"])

    assert first.exit_code == 0, first.output
    assert _json(first.stdout) == {"action": "created", "name": "prod", "changes": []}
    assert _json(again.stdout)["action"] == "unchanged"
    envelope = json.loads(changed.stdout)
    assert envelope["kind"] == "ansible.source_outcome"
    assert envelope["record"]["action"] == "updated"


def test_source_patch_emits_changes_as_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state=_SOURCES)

    result = invoke_cli(app, ["source", "patch", "prod", "--add-repo", "acme/api", "-f", "json"])
    noop = invoke_cli(app, ["source", "patch", "prod", "--add-repo", "acme/api", "-f", "json"])

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
    _config(tmp_path, monkeypatch, state=_SOURCES)

    result = invoke_cli(app, ["source", "patch", *args])

    assert result.exit_code == 2
    assert message in result.stderr


# --- parallel / out flags -------------------------------------------------


def test_impact_help_hides_live() -> None:
    result = invoke_cli(app, ["impact", "--help"])

    assert result.exit_code == 0, result.output
    assert "--live" not in result.output
    assert "--refresh" in result.output


def test_graph_help_lists_current_flags_only() -> None:
    result = invoke_cli(app, ["graph", "--help"])
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
    _config(tmp_path, monkeypatch)
    target = tmp_path / "role"
    target.mkdir()
    out = tmp_path / "graph.json"

    result = invoke_cli(
        app,
        ["graph", str(target), "--target-repo", "acme/role", "--direction=down", "-f", "json",
         "--out", str(out)],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["target_id"]


def test_parallel_rejects_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, monkeypatch, state=_SOURCES)

    result = invoke_cli(app, ["source", "refresh", "prod", "-j", "0"])

    assert result.exit_code == 2
