"""``ansible impact ROLE --uses VAR``: the dependents that define, set or read a variable."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ansible.unit.test_cli import _record_refresh, _run, _seed_index, _use_config
from untaped.testing import CliResult
from untaped_ansible.domain.payloads import IndexedDependency
from untaped_ansible.domain.reach import ReachedNode
from untaped_ansible.domain.variable_models import AnalysedFile, CommitFacts, CommitListing
from untaped_ansible.domain.variable_queries import render_key_path, unanalysed
from untaped_ansible.domain.variables import analyse, selected
from untaped_ansible.infrastructure import SqliteDependencyIndex
from untaped_ansible.settings import analyser_version

_ROLE = "acme/haku-role"
_SOURCE = {"sources": [{"name": "platform", "orgs": ["acme"]}]}
_SITE = """\
- hosts: web
  roles:
    - role: haku-role
      haku_port: 8080
"""
_TASKS = """\
- name: start
  debug:
    msg: hi
  when: haku_port > 1
- ansible.builtin.set_fact:
    haku_port: 9090
"""
_FILES: dict[str, dict[str, str]] = {
    "acme/web": {"group_vars/prod.yml": "haku_port: 8080\n", "site.yml": _SITE},
    "acme/platform": {
        "inventory/prod.yml": "all:\n  children:\n    web:\n      vars:\n        haku_port: 1\n"
    },
    "acme/web-wrapper": {
        "defaults/main.yml": "haku_port: 80\n",
        "tasks/main.yml": _TASKS,
        "templates/app.conf.j2": "listen {{ haku_port }}\n",
    },
    "acme/other": {"tasks/main.yml": "- debug:\n    msg: '{{ other }}'\n"},
    _ROLE: {"defaults/main.yml": "haku_port: 80\nhaku_host: x\n"},
}


def _sha(repo: str) -> str:
    return f"sha-{repo.rsplit('/', maxsplit=1)[-1]}"


def _blob(content: str) -> str:
    return hashlib.sha1(content.encode(), usedforsecurity=False).hexdigest()


def _dependent(repo: str, *, ref: str = "main", sha: str | None = None) -> IndexedDependency:
    return IndexedDependency(
        source_repo=repo,
        source_ref=ref,
        source_sha=sha or _sha(repo),
        dependency_repo=_ROLE,
        dependency_name="haku-role",
        dependency_version="main",
        source_path="roles/requirements.yml",
    )


def _role_ref() -> IndexedDependency:
    """The role's own scan (it depends on something), so its commit is indexed too."""
    return IndexedDependency(
        source_repo=_ROLE,
        source_ref="main",
        source_sha=_sha(_ROLE),
        dependency_repo="acme/base",
        dependency_name="base",
        dependency_version="main",
        source_path="meta/main.yml",
    )


def _analyse(index: SqliteDependencyIndex, commits: dict[tuple[str, str], dict[str, str]]) -> None:
    """Record the variable analysis of each ``(repo, sha)``'s files, as a refresh does."""
    analysed: dict[tuple[str, str], AnalysedFile] = {}
    listings = []
    for (repo, sha), files in commits.items():
        listed = []
        for path, text in files.items():
            if selected(path) is None:
                continue
            listed.append((path, _blob(text)))
            analysed[(_blob(text), path)] = AnalysedFile(
                blob=_blob(text), path=path, analysis=analyse(path, text)
            )
        listings.append(CommitListing(repo=repo, sha=sha, files=tuple(listed)))
    index.commit_source_ref_partial_refresh(
        "source:platform",
        scans=(),
        touches=(),
        keep=set(),
        analysed=tuple(analysed.values()),
        listings=tuple(listings),
        analyser=analyser_version(),
    )


def _seed(
    tmp_path: Path,
    edges: tuple[IndexedDependency, ...],
    analysed: dict[tuple[str, str], dict[str, str]],
) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")

    def seed() -> None:
        _seed_index(index, "source:platform", edges)
        _analyse(index, analysed)

    _record_refresh("source:platform", datetime.now(UTC), seed)


def _seed_haku(tmp_path: Path, *, skip: tuple[str, ...] = ()) -> None:
    repos = ["acme/web", "acme/platform", "acme/web-wrapper", "acme/other"]
    edges = tuple(
        _dependent(repo, ref="v2.1" if repo == "acme/web-wrapper" else "main") for repo in repos
    )
    analysed = {(repo, _sha(repo)): _FILES[repo] for repo in [*repos, _ROLE] if repo not in skip}
    _seed(tmp_path, (*edges, _role_ref()), analysed)


def _uses(*args: str) -> CliResult:
    return _run("impact", _ROLE, "--source", "platform", *args)


def _rows(result: CliResult) -> list[tuple[str, str, str, int, str, str]]:
    return [
        (row["repo"], row["ref"], row["file"], row["line"], render_key_path(row["key_path"]),
         row["how"])
        for row in json.loads(result.stdout)
    ]  # fmt: skip


def test_impact_uses_lists_each_place_a_dependent_uses_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_port", "-f", "json")

    assert result.exit_code == 0, result.output
    assert _rows(result) == [
        ("acme/platform", "main", "inventory/prod.yml", 5, "all.children.web.vars.haku_port",
         "sets"),
        ("acme/web", "main", "group_vars/prod.yml", 1, "haku_port", "sets"),
        ("acme/web", "main", "site.yml", 4, "[0].roles[0].haku_port", "sets"),
        ("acme/web-wrapper", "v2.1", "defaults/main.yml", 1, "haku_port", "defines"),
        ("acme/web-wrapper", "v2.1", "tasks/main.yml", 4, "[0].when", "reads"),
        ("acme/web-wrapper", "v2.1", "tasks/main.yml", 6,
         '[1]."ansible.builtin.set_fact".haku_port', "sets"),
        ("acme/web-wrapper", "v2.1", "templates/app.conf.j2", 1, "", "reads"),
    ]  # fmt: skip
    assert result.stderr == ""


def test_impact_uses_pipe_records_carry_segments_and_the_dependent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_port", "-f", "pipe")

    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert {record["kind"] for record in records} == {"ansible.variable_use_hit"}
    set_fact = next(r["record"] for r in records if r["record"]["line"] == 6)
    assert set_fact["key_path"] == [1, "ansible.builtin.set_fact", "haku_port"]
    assert set_fact["dependent"] == {
        "repo": "acme/web-wrapper",
        "ref": "v2.1",
        "path": ["acme/web-wrapper@v2.1", f"{_ROLE}@main"],
    }
    assert set_fact["variable"] == "haku_port"


def test_impact_uses_table_renders_the_key_path_dotted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_port")

    assert result.exit_code == 0, result.output
    assert '[1]."ansible.builtin.set_fact".haku_port' in result.stdout
    assert "acme/other" not in result.stdout


def test_repeated_uses_is_any_of(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "other", "--uses", "haku_host", "-f", "json")

    assert result.exit_code == 0, result.output
    assert {(row[0], row[2]) for row in _rows(result)} == {("acme/other", "tasks/main.yml")}


def test_depth_one_keeps_direct_dependents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edges = (
        _dependent("acme/web"),
        IndexedDependency(
            source_repo="acme/site",
            source_ref="main",
            source_sha="sha-site",
            dependency_repo="acme/web",
            dependency_name="web",
            dependency_version="main",
            source_path="roles/requirements.yml",
        ),
    )
    site = {"group_vars/all.yml": "haku_port: 2\n"}
    _seed(
        tmp_path,
        edges,
        {("acme/web", "sha-web"): _FILES["acme/web"], ("acme/site", "sha-site"): site},
    )
    _use_config(tmp_path, monkeypatch, _SOURCE)

    everything = _uses("--uses", "haku_port", "-f", "json")
    direct = _uses("--uses", "haku_port", "--depth", "1", "-f", "json")

    assert "acme/site" in {row[0] for row in _rows(everything)}
    assert {row[0] for row in _rows(direct)} == {"acme/web"}


def test_one_ref_held_at_two_commits_by_two_sources_gives_each_fact_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    changed = {**_FILES["acme/web"], "host_vars/h.yml": "haku_port: 3\n"}

    def seed() -> None:
        _seed_index(index, "source:platform", (_dependent("acme/web", sha="sha-a"),))
        _seed_index(index, "source:ops", (_dependent("acme/web", sha="sha-b"),))
        _analyse(index, {("acme/web", "sha-a"): _FILES["acme/web"], ("acme/web", "sha-b"): changed})

    for key in ("source:platform", "source:ops"):
        _record_refresh(key, datetime.now(UTC), seed)
    _use_config(
        tmp_path,
        monkeypatch,
        {"sources": [{"name": "platform", "orgs": ["acme"]}, {"name": "ops", "orgs": ["acme"]}]},
    )

    result = _run(
        "impact", _ROLE, "--source", "platform", "--source", "ops", "--uses", "haku_port",
        "-f", "json",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert [row[2] for row in _rows(result)] == [
        "group_vars/prod.yml",
        "host_vars/h.yml",
        "site.yml",
    ]


def test_a_misspelt_name_hints_that_the_role_defines_no_such_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_prot")

    assert result.exit_code == 0, result.output
    assert f"hint: {_ROLE} defines no variable haku_prot" in result.stderr
    assert "No dependents use haku_prot." in result.stderr
    assert "defines no variable" not in _uses("--uses", "haku_port").stderr


def test_a_local_role_takes_its_defines_from_the_working_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)
    role = tmp_path / "role"
    (role / "defaults").mkdir(parents=True)
    (role / "defaults" / "main.yml").write_text("haku_local: 1\n")

    def uses(name: str) -> CliResult:
        return _run(
            "impact", str(role), "--target-repo", _ROLE, "--source", "platform", "--uses", name
        )

    assert "defines no variable haku_port" in uses("haku_port").stderr
    assert "defines no variable" not in uses("haku_local").stderr


@pytest.mark.parametrize("name", ["1bad", "has-dash", "class", "a.b"])
def test_an_invalid_variable_name_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", name)

    assert result.exit_code == 2
    assert "--uses takes a variable name" in result.stderr


@pytest.mark.parametrize("cached", [False, True])
def test_a_dependent_without_facts_fails_rather_than_reading_as_no_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached: bool
) -> None:
    _seed_haku(tmp_path, skip=("acme/platform",))
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_port", "-f", "json", *(["--cached"] if cached else []))

    assert result.exit_code == 1
    assert {row[0] for row in _rows(result)} == {"acme/web", "acme/web-wrapper"}
    assert "1 dependent without variable facts" in result.stderr
    assert "acme/platform@main" in result.stderr
    assert "untaped ansible source refresh platform" in result.stderr


def test_skipped_files_in_the_dependents_get_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = {"group_vars/prod.yml": "haku_port: [\n", "site.yml": _SITE}
    _seed(tmp_path, (_dependent("acme/web"),), {("acme/web", "sha-web"): broken})
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_port", "-f", "json")

    assert result.exit_code == 0, result.output
    assert result.stderr.count("warning:") == 1
    assert "acme/web@main group_vars/prod.yml" in result.stderr


def test_impact_without_uses_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("-f", "pipe")

    assert {json.loads(line)["kind"] for line in result.stdout.splitlines()} == {
        "ansible.dependent"
    }


@pytest.mark.parametrize(
    ("key_path", "rendered"),
    [
        ([3, "ansible.builtin.set_fact", "haku_port"], '[3]."ansible.builtin.set_fact".haku_port'),
        (["a", 0, "b"], "a[0].b"),
        (['say "hi"'], '"say \\"hi\\""'),
        (["x[1]"], '"x[1]"'),
        ([], ""),
    ],
)
def test_key_paths_render_dotted_with_odd_segments_quoted(
    key_path: list[str | int], rendered: str
) -> None:
    assert render_key_path(key_path) == rendered


def test_a_table_with_an_unanalysed_dependent_never_says_no_dependents_use_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path, skip=("acme/web", "acme/platform", "acme/web-wrapper", "acme/other"))
    _use_config(tmp_path, monkeypatch, _SOURCE)

    result = _uses("--uses", "haku_port")

    assert result.exit_code == 1
    assert "No dependents use" not in result.stderr
    assert "4 dependents without variable facts" in result.stderr


def test_several_names_add_the_variable_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)

    one = _uses("--uses", "other")
    several = _uses("--uses", "other", "--uses", "haku_port")

    def header(result: CliResult) -> str:
        return next(line for line in result.stdout.splitlines() if "repo" in line.lower()).lower()

    assert "variable" not in header(one)
    assert "variable" in header(several)


def test_a_local_role_walk_leaves_out_vendored_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_haku(tmp_path)
    _use_config(tmp_path, monkeypatch, _SOURCE)
    role = tmp_path / "role"
    (role / "defaults").mkdir(parents=True)
    (role / "defaults" / "main.yml").write_text("haku_local: 1\n")
    vendored = role / "node_modules" / "pkg" / "defaults"
    vendored.mkdir(parents=True)
    (vendored / "main.yml").write_text("haku_vendored: 1\n")

    result = _run(
        "impact", str(role), "--target-repo", _ROLE, "--source", "platform",
        "--uses", "haku_vendored",
    )  # fmt: skip

    assert "defines no variable haku_vendored" in result.stderr


def test_a_dependent_with_no_indexed_commit_counts_as_unanalysed() -> None:
    def node(repo: str, shas: frozenset[str]) -> ReachedNode:
        return ReachedNode(
            repo=repo, ref="main", unresolved=None, declared_ref=None, declared_in=None,
            depth=1, path=[repo], root_ref="main", stopped=None, shas=shas,
        )  # fmt: skip

    facts = CommitFacts(facts={}, unanalysed=frozenset(), skipped={})

    reached = [node("acme/legacy", frozenset()), node("acme/web", frozenset({"s"}))]
    assert unanalysed(reached, facts) == ["acme/legacy@main"]
