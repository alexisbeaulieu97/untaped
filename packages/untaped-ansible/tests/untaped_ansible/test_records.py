"""Ansible's record models: each kind, and the keys its dump writes, in order.

The keys and their order are the wire of ``-f json``, ``-f yaml`` and
``-f pipe``; they match the rows these models replaced (#613).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ansible_test_support import installed, read_state, run, write_state
from untaped.sdk import ConfigError, Failure, Record
from untaped_ansible.records import (
    Dependency,
    DependencyMatch,
    Dependent,
    FoundIn,
    Source,
    SourceAlias,
    SourceAliasOutcome,
    SourceOutcome,
    SourceStatus,
    VariableUseHit,
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
        SourceAliasOutcome(action="created", alias="common", repo="acme/common"),
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
    (Dependency(**_REACHED), "ansible.dependency", list(_REACHED)),
    (Dependent(**_REACHED), "ansible.dependent", list(_REACHED)),
]


@pytest.mark.parametrize(
    ("record", "kind", "keys"), _SAMPLES, ids=[kind for _, kind, _ in _SAMPLES]
)
def test_each_record_has_its_kind_and_dumps_its_keys_in_order(
    record: Record, kind: str, keys: list[str]
) -> None:
    assert set(keys) <= set(_schema(kind)["properties"])
    assert list(record.model_dump(mode="json")) == keys


def _schema(kind: str) -> dict[str, Any]:
    """``untaped plugin schema KIND``: the JSON schema of the kind's model."""
    ran = installed().cli(["plugin", "schema", kind])
    assert ran.exit == 0, ran.stderr
    schema: dict[str, Any] = json.loads(ran.stdout)
    assert schema["$id"] == kind
    return schema


def test_the_bases_are_no_kind_of_their_own() -> None:
    ran = installed().cli(["plugin", "schema", "ansible.reached_row"])
    assert ran.exit != 0


def test_source_get_writes_an_unset_ref_scan_default_as_null() -> None:
    state = {"sources": [{"name": "prod", "orgs": ["acme"]}]}
    write_state(state)

    result = run("source", "get", "prod", "-f", "json")

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
def test_an_unknown_column_is_a_usage_error(argv: list[str], valid: str) -> None:
    state = {"sources": [{"name": "prod", "orgs": ["acme"]}], "aliases": {"common": "acme/c"}}
    write_state(state)

    result = run(*argv)

    assert result.exit_code == 2
    assert f"unknown column 'bogus'; valid columns: {valid}" in result.stderr


def test_an_unknown_column_on_an_outcome_is_refused_before_the_change() -> None:
    write_state({"aliases": {"common": "acme/c"}})

    result = run("source-alias", "set", "common", "acme/common", "--columns", "alias,bogus")

    assert result.exit_code == 2
    assert "unknown column 'bogus'; valid columns: action, error, alias, repo" in result.stderr
    assert read_state()["aliases"]["common"] == "acme/c"


_ROWS: list[Record] = [
    Dependency.model_validate({**_REACHED, "stopped": "depth"}),
    Dependent.model_validate({**_REACHED, "stopped": "depth"}),
    DependencyMatch(
        root_repo="acme/site",
        root_ref="main",
        repo="acme/base",
        declared_ref=None,
        declared_in="meta/main.yml",
        path=["acme/site@main", "acme/base"],
        input_kind="awx.job_template",
        input_id=7,
        input_name="deploy",
    ),
    Source(
        name="prod",
        orgs=["acme"],
        teams=["acme/platform"],
        repos=[],
        dependency_paths=[],
        ref_kinds=["heads"],
        ref_patterns=[],
        ref_scan_default="all",
    ),
    SourceAlias(alias="common", repo="acme/common"),
    SourceAliasOutcome(action="created", alias="common", repo="acme/common"),
    SourceOutcome(
        action="failed",
        name="prod",
        changes=["failed to release acme/web"],
        error=Failure.from_exception(ConfigError("x")),
    ),
    SourceStatus(
        source="prod",
        source_key="source:prod",
        state="stale",
        configured=True,
        scanned_at="2026-10-10T00:00:00Z",
        repos=2,
        refreshed_at="2026-10-10T00:00:00Z",
        stale=True,
        max_age_seconds=3600,
    ),
    VariableUseHit(
        variable="port",
        repo="acme/web",
        ref=None,
        file="site.yml",
        line=3,
        key_path=[0, "ansible.builtin.set_fact", "port"],
        how="sets",
        dependent=FoundIn(repo="acme/web", ref=None, path=["acme/web", "acme/role"]),
    ),
]


@pytest.mark.parametrize("row", _ROWS, ids=[type(each).__name__ for each in _ROWS])
def test_an_ansible_record_reads_back_its_json_strictly(row: Record) -> None:
    dumped = json.dumps(row.model_dump(mode="json"))

    assert type(row).model_validate_json(dumped, strict=True) == row
