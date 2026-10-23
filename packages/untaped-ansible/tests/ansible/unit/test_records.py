"""Ansible's record models: each kind, and the keys its dump writes, in order.

The keys and their order are the wire of ``-f json``, ``-f yaml`` and
``-f pipe``; they match the rows these models replaced (#613).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from untaped.sdk import Failure, Record, kind_of
from untaped.sdk.testing import invoke_cli
from untaped_ansible.cli import app
from untaped_ansible.records import (
    AliasOutcome,
    DependencyMatch,
    ReachedDependency,
    ReachedDependent,
    ReachedRow,
    Source,
    SourceAlias,
    SourceOutcome,
    SourceStatus,
)

_REACHED = {
    "repo": "acme/base",
    "ref": "main",
    "unresolved": None,
    "declared_ref": "v1",
    "declared_in": "roles/requirements.yml",
    "depth": 1,
    "path": ["acme/site@main", "acme/base@main"],
    "root_ref": "main",
    "stopped": None,
}

_SAMPLES: list[tuple[Record, str, list[str]]] = [
    (
        Source(
            name="prod",
            orgs=["acme"],
            teams=[],
            repos=[],
            dependency_paths=[],
            ref_kinds=[],
            ref_patterns=[],
        ),
        "ansible.source",
        [
            "name",
            "orgs",
            "teams",
            "repos",
            "dependency_paths",
            "ref_kinds",
            "ref_patterns",
            "ref_scan_default",
        ],
    ),
    (SourceAlias(alias="common", repo="acme/common"), "ansible.source_alias", ["alias", "repo"]),
    (
        AliasOutcome(action="created", alias="common", repo="acme/common"),
        "ansible.source_alias_outcome",
        ["alias", "action", "repo"],
    ),
    (
        SourceOutcome(action="failed", name="prod", error=Failure.plain("x")),
        "ansible.source_outcome",
        ["name", "action", "changes", "error"],
    ),
    (
        SourceStatus(
            source="prod",
            source_key="source:prod",
            state="fresh",
            configured=True,
            refreshed_at=None,
            stale=False,
            max_age_seconds=60,
        ),
        "ansible.source_status",
        [
            "source",
            "source_key",
            "state",
            "configured",
            "scanned_at",
            "repos",
            "refs",
            "edges",
            "refreshed_at",
            "stale",
            "max_age_seconds",
            "partial",
            "outdated",
            "attempted_at",
            "retry_at",
            "refresh_error",
        ],
    ),
    (
        DependencyMatch(
            root_repo="acme/site",
            root_ref="main",
            repo="acme/base",
            declared_ref=None,
            declared_in=None,
            path=["acme/site@main", "acme/base"],
        ),
        "ansible.dependency_match",
        [
            "root_repo",
            "root_ref",
            "repo",
            "declared_ref",
            "declared_in",
            "path",
            "input_kind",
            "input_id",
            "input_name",
        ],
    ),
    (ReachedDependency(**_REACHED), "ansible.dependency", list(_REACHED)),
    (ReachedDependent(**_REACHED), "ansible.dependent", list(_REACHED)),
]


@pytest.mark.parametrize(
    ("record", "kind", "keys"), _SAMPLES, ids=[kind for _, kind, _ in _SAMPLES]
)
def test_each_model_declares_its_kind_and_dumps_its_keys_in_order(
    record: Record, kind: str, keys: list[str]
) -> None:
    assert kind_of(type(record)) == kind
    assert list(record.model_dump(mode="json")) == keys


def test_the_reached_rows_base_declares_no_kind() -> None:
    assert kind_of(ReachedRow) is None


def test_source_get_writes_an_unset_ref_scan_default_as_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    profile = {"ansible": {"index_path": str(tmp_path / "index.sqlite3")}}
    cfg.write_text(yaml.safe_dump({"profiles": {"default": profile}}))
    state = {"sources": [{"name": "prod", "orgs": ["acme"]}]}
    (tmp_path / "state.yml").write_text(yaml.safe_dump({"ansible": state}))
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    result = invoke_cli(app, ["source", "get", "prod", "-f", "json"])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {
        "name": "prod",
        "orgs": ["acme"],
        "teams": [],
        "repos": [],
        "dependency_paths": [],
        "ref_kinds": [],
        "ref_patterns": [],
        "ref_scan_default": None,
    }


@pytest.mark.parametrize(
    ("argv", "valid"),
    [
        (
            ["source", "list", "--columns", "name,bogus"],
            "name, orgs, teams, repos, dependency_paths, ref_kinds, ref_patterns, ref_scan_default",
        ),
        (["source-alias", "list", "--columns", "bogus"], "alias, repo"),
    ],
    ids=["source list", "source-alias list"],
)
def test_an_unknown_column_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], valid: str
) -> None:
    cfg = tmp_path / "config.yml"
    profile = {"ansible": {"index_path": str(tmp_path / "index.sqlite3")}}
    cfg.write_text(yaml.safe_dump({"profiles": {"default": profile}}))
    state = {"sources": [{"name": "prod", "orgs": ["acme"]}], "aliases": {"common": "acme/c"}}
    (tmp_path / "state.yml").write_text(yaml.safe_dump({"ansible": state}))
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    result = invoke_cli(app, argv)

    assert result.exit_code == 2
    assert f"unknown column 'bogus'; valid columns: {valid}" in result.stderr


def test_an_unknown_column_on_an_outcome_only_warns_after_the_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    profile = {"ansible": {"index_path": str(tmp_path / "index.sqlite3")}}
    cfg.write_text(yaml.safe_dump({"profiles": {"default": profile}}))
    state = {"sources": [{"name": "prod", "orgs": ["acme"]}], "aliases": {"common": "acme/c"}}
    (tmp_path / "state.yml").write_text(yaml.safe_dump({"ansible": state}))
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))

    argv = ["source-alias", "set", "common", "acme/common", "--columns", "alias,bogus"]
    result = invoke_cli(app, argv)

    assert result.exit_code == 0, result.stderr
    assert "unknown column 'bogus'; valid columns: action, error, alias, repo" in result.stderr
    assert "common" in result.stdout
    saved = yaml.safe_load((tmp_path / "state.yml").read_text())
    assert saved["ansible"]["aliases"]["common"] == "acme/common"
