"""CLI convention tests for the Ansible capability (docs/conventions.md).

Covers the renamed verbs and flags (with their deprecated spellings through the
``untaped`` root), mutation outcome records, destructive confirmation, usage
exit codes and the source status record shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.bootstrap import build_root_app
from untaped.capabilities.ansible.cli import app
from untaped.capabilities.ansible.domain.payloads import RefScan
from untaped.capabilities.ansible.infrastructure import SqliteDependencyIndex
from untaped.testing import ScriptedPromptBackend, invoke_cli


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


# --- alias ---------------------------------------------------------------


def test_alias_set_emits_created_then_unchanged_then_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    first = invoke_cli(app, ["alias", "set", "common", "acme/common", "-f", "json"])
    again = invoke_cli(app, ["alias", "set", "common", "acme/common", "-f", "json"])
    moved = invoke_cli(app, ["alias", "set", "common", "acme/other", "-f", "json"])

    assert first.exit_code == 0, first.output
    assert _json(first.stdout) == {"action": "created", "alias": "common", "repo": "acme/common"}
    assert _json(again.stdout)["action"] == "unchanged"
    assert _json(moved.stdout)["action"] == "updated"
    assert _state(tmp_path)["aliases"] == {"common": "acme/other"}


def test_alias_set_pipe_record_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, monkeypatch)

    result = invoke_cli(app, ["alias", "set", "common", "acme/common", "-f", "pipe"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["kind"] == "ansible.alias_outcome"


def test_alias_set_rejects_non_repo_target_as_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    result = invoke_cli(app, ["alias", "set", "foo", "bar"])

    assert result.exit_code == 2
    assert "owner/name" in result.stderr
    assert "aliases" not in _state(tmp_path)


def test_alias_add_is_a_deprecated_spelling_of_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    result = invoke_cli(
        build_root_app(externals=[]), ["ansible", "alias", "add", "common", "acme/common"]
    )

    assert result.exit_code == 0, result.output
    assert "`add` is deprecated" in result.stderr
    assert "use `set`" in result.stderr
    assert _state(tmp_path)["aliases"] == {"common": "acme/common"}


def test_alias_remove_requires_yes_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"aliases": {"common": "acme/common"}})

    result = invoke_cli(app, ["alias", "remove", "common"])

    assert result.exit_code == 2
    assert "alias remove requires --yes when not interactive" in result.stderr
    assert _state(tmp_path)["aliases"] == {"common": "acme/common"}


def test_alias_remove_decline_exits_1_and_keeps_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"aliases": {"common": "acme/common"}})

    result = invoke_cli(
        app,
        ["alias", "remove", "common"],
        terminal=True,
        prompt_backend=ScriptedPromptBackend(confirms=[False]),
    )

    assert result.exit_code == 1
    assert "cancelled; no changes made" in result.stderr
    assert _state(tmp_path)["aliases"] == {"common": "acme/common"}


def test_alias_remove_dry_run_plans_without_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"aliases": {"common": "acme/common"}})

    result = invoke_cli(app, ["alias", "remove", "common", "--dry-run", "--yes", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert _json(result.stdout) == {"action": "planned", "alias": "common", "repo": "acme/common"}
    assert _state(tmp_path)["aliases"] == {"common": "acme/common"}


def test_alias_remove_yes_deletes_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"aliases": {"common": "acme/common"}})

    result = invoke_cli(app, ["alias", "remove", "common", "--yes", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert _json(result.stdout) == {"action": "deleted", "alias": "common", "repo": "acme/common"}
    assert "aliases" not in _state(tmp_path)


def test_alias_remove_unknown_alias_names_known_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"aliases": {"common": "acme/common"}})

    result = invoke_cli(app, ["alias", "remove", "missing", "--yes"])

    assert result.exit_code == 1
    assert "error: alias not found: 'missing'; known: common" in result.stderr


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


def test_source_set_without_boundary_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    result = invoke_cli(app, ["source", "set", "prod", "--path", "roles/requirements.yml"])

    assert result.exit_code == 2
    assert "source requires --org, --team, or --repo" in result.stderr


@pytest.mark.parametrize(
    ("old", "new_args"),
    [
        (["save", "prod", "--repo", "acme/site"], "set"),
        (["edit", "prod", "--add-repo", "acme/api"], "patch"),
        (["show", "prod"], "get"),
    ],
)
def test_renamed_source_verbs_keep_deprecated_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: list[str], new_args: str
) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

    result = invoke_cli(build_root_app(externals=[]), ["ansible", "source", *old])

    assert result.exit_code == 0, result.output
    assert f"`{old[0]}` is deprecated and will be removed in 7.0; use `{new_args}`" in (
        result.stderr
    )


def test_source_patch_emits_changes_as_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

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
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

    result = invoke_cli(app, ["source", "patch", *args])

    assert result.exit_code == 2
    assert message in result.stderr


def test_source_get_unknown_source_names_known_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

    result = invoke_cli(app, ["source", "get", "missing"])

    assert result.exit_code == 1
    assert "error: source not found: 'missing'; known: prod" in result.stderr


def test_source_remove_requires_yes_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

    result = invoke_cli(app, ["source", "remove", "prod"])

    assert result.exit_code == 2
    assert "source remove requires --yes when not interactive" in result.stderr
    assert _state(tmp_path)["sources"] == [{"name": "prod", "repos": ["acme/site"]}]


def test_source_remove_confirmed_interactively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})
    backend = ScriptedPromptBackend(confirms=[True])

    result = invoke_cli(
        app, ["source", "remove", "prod", "-f", "json"], terminal=True, prompt_backend=backend
    )

    assert result.exit_code == 0, result.output
    assert backend.calls and backend.calls[0][0] == "confirm"
    assert _json(result.stdout) == {"action": "deleted", "name": "prod", "changes": []}
    assert "sources" not in _state(tmp_path)


def test_source_remove_dry_run_keeps_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

    result = invoke_cli(app, ["source", "remove", "prod", "--dry-run", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert _json(result.stdout)["action"] == "planned"
    assert _state(tmp_path)["sources"] == [{"name": "prod", "repos": ["acme/site"]}]


def test_source_status_uses_snake_case_states_and_utc_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(
        tmp_path,
        monkeypatch,
        state={
            "sources": [
                {"name": "prod", "repos": ["acme/site"]},
                {"name": "new", "repos": ["acme/api"]},
            ]
        },
    )
    scanned_at = datetime(2026, 1, 2, 3, 4, 5, 678, tzinfo=UTC)
    scan = RefScan(
        source_key="source:prod",
        source_repo="acme/site",
        ref_kind="heads",
        source_ref="main",
        source_sha="sha-main",
        clone_url="https://github.com/acme/site.git",
        clone_protocol="https",
        dependency_paths_fingerprint="paths",
        checked_at=scanned_at,
        indexed_at=scanned_at,
        dependencies=(),
    )
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    repos = frozenset({"acme/site"})
    keep = {("acme/site", "heads", "main")}
    index.commit_source_ref_partial_refresh(
        "source:prod", scans=(scan,), touches=(), keep=keep, processed_repos=repos
    )
    index.complete_source_ref_refresh("source:prod", source_repos=repos, scanned_at=scanned_at)

    result = invoke_cli(app, ["source", "status", "-f", "json"])

    assert result.exit_code == 0, result.output
    rows = {row["source"]: row for row in _json(result.stdout)}
    assert rows["new"]["state"] == "not_refreshed"
    assert rows["prod"]["scanned_at"] == "2026-01-02T03:04:05Z"


# --- parallel / out flags -------------------------------------------------


def test_source_refresh_concurrency_is_a_deprecated_spelling_of_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)

    result = invoke_cli(
        build_root_app(externals=[]),
        ["ansible", "source", "refresh", "missing", "--concurrency", "4"],
    )

    assert "`--concurrency` is deprecated and will be removed in 7.0; use `--parallel`" in (
        result.stderr
    )
    assert "source not found: 'missing'" in result.stderr


def test_graph_help_lists_parallel_and_out() -> None:
    result = invoke_cli(app, ["graph", "--help"])
    output = " ".join(result.output.replace("│", " ").split())

    assert result.exit_code == 0, result.output
    assert "--parallel" in output
    assert "--out" in output
    assert "--concurrency" not in output
    assert "--output" not in output


def test_graph_output_is_a_deprecated_spelling_of_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch)
    target = tmp_path / "role"
    target.mkdir()
    out = tmp_path / "graph.json"

    result = invoke_cli(
        build_root_app(externals=[]),
        [
            "ansible",
            "graph",
            str(target),
            "--target-repo",
            "acme/role",
            "--downstream",
            "-f",
            "json",
            "--output",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "`--output` is deprecated and will be removed in 7.0; use `--out`" in result.stderr
    assert json.loads(out.read_text())["target_id"]


def test_parallel_rejects_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, monkeypatch, state={"sources": [{"name": "prod", "repos": ["acme/site"]}]})

    result = invoke_cli(app, ["source", "refresh", "prod", "-j", "0"])

    assert result.exit_code == 2
