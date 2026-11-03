"""A node is its repository's name as the provider lists it (design S8).

A source whose repositories live on a GitLab host has nested names such as
``infra/platform/base``; every graph verb takes them by name and by URL, and
a URL on a host none of the source's repositories live on names nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ansible_index_support import make_edge, seed
from ansible_test_support import Result, run, use_config
from untaped_ansible.domain.payloads import SourceRepoMetadata

_HOST = "gitlab.example.com"
_BASE = "infra/platform/base"
_WEB = "infra/apps/web"
_LIB = "infra/platform/lib"


def _metadata(repo: str) -> SourceRepoMetadata:
    return SourceRepoMetadata(
        source_key="source:prod",
        source_repo=repo,
        default_branch="main",
        url=f"https://{_HOST}/{repo}.git",
    )


@pytest.fixture(autouse=True)
def _gitlab_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """infra/apps/web@main → infra/platform/base@main → infra/platform/lib, on the GitLab host."""
    seed(
        tmp_path,
        "source:prod",
        make_edge(_WEB, _BASE),
        make_edge(_BASE, _LIB),
        repo_metadata=(_metadata(_WEB), _metadata(_BASE)),
    )
    state = {"sources": [{"name": "prod", "repos": [_BASE, _WEB]}]}
    use_config(tmp_path, monkeypatch, state)


def _repos(result: Result) -> list[str]:
    assert result.exit_code == 0, result.output
    return [row["repo"] for row in json.loads(result.stdout)]


@pytest.mark.parametrize(
    "target",
    [_BASE, f"https://{_HOST}/{_BASE}.git", f"git@{_HOST}:{_BASE}.git"],
)
def test_s8_impact_and_deps_take_a_nested_name_or_its_url(target: str) -> None:
    assert _repos(run("impact", target, "--source", "prod", "-f", "json")) == [_WEB]
    assert _repos(run("deps", target, "--source", "prod", "-f", "json")) == [_LIB]


def test_s8_find_and_graph_take_a_nested_name() -> None:
    found = run("find", _LIB, "--root", f"{_WEB}@main", "--source", "prod", "-f", "json")
    graph = run("graph", _BASE, "--source", "prod")

    assert found.exit_code == 0, found.output
    [row] = json.loads(found.stdout)
    assert (row["root_repo"], row["path"]) == (_WEB, [f"{_WEB}@main", f"{_BASE}@main", _LIB])
    assert graph.exit_code == 0, graph.output
    assert _WEB in graph.stdout
    assert _LIB in graph.stdout


def test_s8_a_root_line_names_a_nested_repo_at_a_ref() -> None:
    result = run("find", _LIB, "--stdin", "--source", "prod", "-f", "json", input=f"{_WEB}@main\n")

    assert result.exit_code == 0, result.output
    assert [row["root_ref"] for row in json.loads(result.stdout)] == ["main"]


def test_s8_a_url_on_a_host_the_source_does_not_hold_names_nothing() -> None:
    result = run("impact", f"https://gitlab.other.example.com/{_BASE}.git", "--source", "prod")

    assert result.exit_code == 1
    assert "could not resolve target to a repository" in result.stderr
