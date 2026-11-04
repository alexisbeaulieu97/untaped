"""Ansible's source refresh on the repo store, against real git (S40, S25 through ansible).

The remotes answer at ``https://github.com/acme/<name>.git`` (rewritten to
local paths), the store root is a temp ``git.store_dir``, and github fills
``GitHost`` with the profile's token, so the refresh runs as the CLI wires it
for a host whose plugin has no batch ref probe: refs come from ``git
ls-remote`` with that plugin's credentials. The source's plugin answers
with those repos.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from ansible_index_support import index_path
from ansible_test_support import configure, github_source, run, running, write_state
from untaped.sdk import ErrorCategory, plugin_of
from untaped_ansible.application.refresh_git_index import RefreshGitSourceIndex, RefreshResult
from untaped_ansible.domain.payloads import GitRef
from untaped_ansible.errors import RepoStoreError
from untaped_ansible.infrastructure import GitSourceStore, SqliteDependencyIndex
from untaped_ansible.settings import AnsibleSettings
from untaped_ansible.sources import SourceDefinition
from untaped_git import remote as remote_module
from untaped_git import store_git as store_module
from untaped_git.api import GitResult, Released, RepoSelector, RepoStore, Selection, store_key
from untaped_git.host_auth import basic_header
from untaped_git.testing import GitRemote, git_remote, listing, make_repo
from untaped_github.providers.git import GithubHost

_REQS = "roles/requirements.yml"
_META = "meta/main.yml"
_PATHS = [_REQS, "requirements.yml", _META]
_TOKEN = "ghp_ansible_test"
_NAMESPACE = "refs/untaped/ansible/"


#: One network git call the store or ``ls_remote`` ran: argv and the secret it carried.
type Call = tuple[list[str], str | None]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    """Every ``fetch`` and ``ls-remote`` the git plugin runs; git still runs."""
    seen: list[Call] = []

    def spy(real: Callable[..., GitResult]) -> Callable[..., GitResult]:
        def run(args: Sequence[str], **kwargs: Any) -> GitResult:
            if args[0] in {"fetch", "ls-remote"}:
                secret = kwargs.get("auth_config") or {}
                seen.append((list(args), next(iter(secret.values()), None)))
            return real(args, **kwargs)

        return run

    for module in (store_module, remote_module):
        monkeypatch.setattr(module, "run_git", spy(module.run_git))
    return seen


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temp store root, a github token, and the block run as an ansible command runs."""
    root = tmp_path / "store"
    monkeypatch.setenv("GH_TOKEN", _TOKEN)
    # GitHub's batch probe would ask GraphQL: without it, its host is probed as any other.
    monkeypatch.delattr(GithubHost, "probe_refs")
    configure(git={"store_dir": root})
    with running():
        yield root


def _store() -> GitSourceStore:
    """The repo store adapter, for ansible as a command builds it."""
    return GitSourceStore(plugin=plugin_of(AnsibleSettings))


def _remote(tmp_path: Path, name: str, files: dict[str, str]) -> GitRemote:
    remote = git_remote(tmp_path, f"acme/{name}", host="github.com")
    for path, text in files.items():
        remote.commit(path, text)
    return remote


def _refresh(
    tmp_path: Path, *repos: str, source_key: str = "source:prod", **source: Any
) -> RefreshResult:
    git = _store()
    selected = tuple(make_repo(f"acme/{name}", default_branch="main") for name in repos)
    refresh = RefreshGitSourceIndex(
        select=lambda _: Selection(selected),
        git=git,
        index=SqliteDependencyIndex(index_path(tmp_path)),
        aliases={},
        default_dependency_paths=_PATHS,
    )
    definition = SourceDefinition(
        name=source_key, source="github", selector=RepoSelector(), **source
    )
    return refresh(definition, source_key=source_key)


def _dependents(tmp_path: Path, repo: str, *, source_key: str = "source:prod") -> set[str]:
    index = SqliteDependencyIndex(index_path(tmp_path))
    edges = index.dependents(repo, None, source_key=source_key)
    return {f"{edge.source_repo}@{edge.source_ref}" for edge in edges}


def _repo(root: Path, name: str) -> Path:
    return root.joinpath(*store_key(f"https://github.com/acme/{name}.git"))


def _git(repo: Path, *args: str) -> str:
    result = store_module.run_git(
        list(args), timeout=30, git_dir=repo, cwd=repo, capture=True, ceiling=True
    )
    return result.text.strip()


def _fetches(calls: list[Call], *, stdin: bool) -> list[list[str]]:
    return [argv for argv, _ in calls if argv[0] == "fetch" and ("--stdin" in argv) == stdin]


def test_a_refresh_lands_in_ansibles_namespace_and_reads_files_by_prefetch(
    tmp_path: Path, store_root: Path, calls: list[Call]
) -> None:
    site = _remote(
        tmp_path,
        "site",
        {_REQS: "- src: https://github.com/acme/base\n", "README.md": "# site\nmore\n"},
    )
    site.tag("v1", message="release 1")
    site.commit(_META, "dependencies:\n  - role: acme.lib\n    src: https://github.com/acme/lib\n")
    _remote(tmp_path, "lib", {"requirements.yml": "- src: https://github.com/acme/base\n"})
    _remote(tmp_path, "docs", {"index.md": "no dependency files here\n"})

    first = _refresh(tmp_path, "site", "lib", "docs")

    assert first.failures == ()
    assert (first.repos, first.refs, first.changed_refs) == (3, 4, 4)
    assert _dependents(tmp_path, "acme/base") == {"acme/site@main", "acme/site@v1", "acme/lib@main"}
    assert _dependents(tmp_path, "acme/lib") == {"acme/site@main"}
    repo = _repo(store_root, "site")
    refs = _git(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert refs == [f"{_NAMESPACE}heads/main", f"{_NAMESPACE}tags/v1"]
    assert set(json.loads((repo / "untaped-ansible.json").read_text())) == {"untaped"}
    # One prefetch per changed ref with blobs missing: v1's requirements file is
    # main's blob, already there; docs has no dependency file.
    assert len(_fetches(calls, stdin=True)) == 2
    assert _git(repo, "count-objects", "-v").splitlines()[0] == "count: 0"
    # GitHub's token reached every network call through GitHost.
    assert {secret for _, secret in calls} == {_basic(_TOKEN)}

    calls.clear()
    second = _refresh(tmp_path, "site", "lib", "docs")

    assert (second.changed_refs, second.unchanged_refs) == (0, 4)
    # Nothing moved, the annotated tag included: the probe's ls-remote only, no fetch.
    assert [argv[0] for argv, _ in calls] == ["ls-remote"] * 3


def test_a_moved_ref_is_fetched_and_read_once_and_the_rest_left_alone(
    tmp_path: Path, store_root: Path, calls: list[Call]
) -> None:
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    site.branch("release")
    _refresh(tmp_path, "site")
    calls.clear()

    site.commit(_REQS, "- src: https://github.com/acme/other\n")
    result = _refresh(tmp_path, "site")

    assert (result.changed_refs, result.unchanged_refs) == (1, 1)
    (fetch,) = _fetches(calls, stdin=False)
    assert fetch[-1] == f"+refs/heads/main:{_NAMESPACE}heads/main"
    assert len(_fetches(calls, stdin=True)) == 1
    assert _dependents(tmp_path, "acme/other") == {"acme/site@main"}
    assert _dependents(tmp_path, "acme/base") == {"acme/site@release"}


def test_two_sources_sharing_a_repo_each_see_a_move(
    tmp_path: Path, store_root: Path, calls: list[Call]
) -> None:
    """Rows are per source and ansible's namespace per repo: B rescans what A already fetched."""
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    _refresh(tmp_path, "site", source_key="source:a")
    _refresh(tmp_path, "site", source_key="source:b")
    site.commit(_REQS, "- src: https://github.com/acme/other\n")
    _refresh(tmp_path, "site", source_key="source:a")
    calls.clear()

    result = _refresh(tmp_path, "site", source_key="source:b")

    assert result.changed_refs == 1
    assert _fetches(calls, stdin=False) == []  # the store already holds the new tip
    assert _dependents(tmp_path, "acme/other", source_key="source:b") == {"acme/site@main"}
    assert _dependents(tmp_path, "acme/base", source_key="source:b") == set()


def test_a_repo_shared_with_workspace_keeps_workspaces_refs_and_worktree(
    tmp_path: Path, store_root: Path
) -> None:
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    workspace = RepoStore.for_url(
        site.url, plugin="workspace", error=RepoStoreError, plain_clone=True
    )
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    worktree = tmp_path / "ws" / "site"
    workspace.worktree_add(worktree, "refs/remotes/origin/main", branch="fix")
    site.commit(_REQS, "- src: https://github.com/acme/other\n")

    _refresh(tmp_path, "site")

    repo = _repo(store_root, "site")
    refs = _git(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert "refs/remotes/origin/main" in refs
    assert "refs/heads/fix" in refs
    assert f"{_NAMESPACE}heads/main" in refs
    # Ansible's fetch moved its own ref only; workspace's stays where its fetch left it.
    assert _git(repo, "rev-parse", "refs/remotes/origin/main") != site.oid("main")
    assert (worktree / _REQS).read_text() == "- src: https://github.com/acme/base\n"


def test_a_release_by_github_keeps_a_repo_ansibles_refresh_uses(
    tmp_path: Path, store_root: Path
) -> None:
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    _refresh(tmp_path, "site")
    github = RepoStore.for_url(site.url, plugin="github", error=RepoStoreError)
    github.fetch(branches=["main"])
    github.write_private()

    outcome = github.release()

    assert isinstance(outcome, Released)
    assert outcome.kept() == "ansible"
    refs = _git(_repo(store_root, "site"), "for-each-ref", "--format=%(refname)").splitlines()
    assert refs == [f"{_NAMESPACE}heads/main"]


def test_dependency_files_read_under_any_locale(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    site = _remote(tmp_path, "site", {"requirements.yml": "- src: acme/one\n"})
    sha = site.commit(_REQS, "- src: acme/two\n")
    site.commit("meta/main.yml/nested", "a directory where a file was expected\n")
    store = _store()
    store.fetch(site.url, [])  # nothing asked: nothing fetched, no repo made
    assert not _repo(store_root, "site").exists()

    store.fetch(site.url, [GitRef(kind="heads", name="main", sha=site.oid("main"))])
    files = store.read_files(site.url, [sha], [_REQS, "requirements.yml", _META, "missing.yml"])

    assert files == {sha: {_REQS: "- src: acme/two\n", "requirements.yml": "- src: acme/one\n"}}


def test_a_refresh_without_git_fails_each_repo_as_a_config_error(
    tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remote(tmp_path, "site", {_REQS: "- src: acme/base\n"})
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))

    result = _refresh(tmp_path, "site")

    [failure] = result.failures
    assert (failure.repo, failure.category) == ("acme/site", ErrorCategory.CONFIG)


def _basic(token: str) -> str:
    return basic_header("x-access-token", token)


def test_the_names_the_store_holds(store_root: Path) -> None:
    store = _store()

    assert store.refusal(GitRef(kind="heads", name="feature/+x", sha="0" * 40)) is None
    assert store.refusal(GitRef(kind="heads", name="-wip", sha="0" * 40)) == (
        "git allows this name, but the repo store cannot hold it"
    )


def _save_sources(*names: str, repo: str = "acme/site") -> None:
    """Save sources ``names``, each selecting ``repo``."""
    write_state({"sources": [github_source(name, repos=[repo]) for name in names]})


def _remove(name: str, *flags: str) -> list[str]:
    result = run("source", "remove", name, "--yes", "--format", "json", *flags)
    assert result.exit_code == 0, result.output
    return list(json.loads(result.stdout)["changes"])


def test_removing_a_source_keeps_a_repo_another_source_selects_then_removes_it(
    tmp_path: Path, store_root: Path
) -> None:
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    _save_sources("a", "b", "c", "d")
    for key in ("a", "b", "c", "d"):
        _refresh(tmp_path, "site", source_key=f"source:{key}")
    repo = _repo(store_root, "site")

    assert _remove("a") == ["kept acme/site (selected by sources b, c and d)"]
    _save_sources("b", "c", "d")
    assert _remove("d") == ["kept acme/site (selected by sources b and c)"]
    _save_sources("b", "c")
    assert _remove("c") == ["kept acme/site (selected by source b)"]
    assert repo.is_dir()
    site.commit(_REQS, "- src: https://github.com/acme/other\n")
    assert _refresh(tmp_path, "site", source_key="source:b").changed_refs == 1

    _save_sources("b")
    (change,) = _remove("b")

    assert change.startswith("removed acme/site (") and change.endswith(" freed)")
    assert not repo.exists()


def test_a_dry_run_plans_the_release_and_frees_nothing(tmp_path: Path, store_root: Path) -> None:
    _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    _save_sources("a")
    _refresh(tmp_path, "site", source_key="source:a")

    assert _remove("a", "--dry-run") == ["would release acme/site"]
    assert _repo(store_root, "site").is_dir()
    assert _dependents(tmp_path, "acme/base", source_key="source:a") == {"acme/site@main"}

    shutil.rmtree(_repo(store_root, "site"))  # gone from the store: nothing to plan or free
    assert _remove("a", "--dry-run") == []
    assert _remove("a") == []


def test_removing_a_source_releases_a_repo_whose_refs_it_no_longer_scans(
    tmp_path: Path, store_root: Path
) -> None:
    """A branch deleted upstream leaves no ref scan, but the source still selects the repo."""
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    site.branch("release-1")
    _save_sources("a")
    _refresh(tmp_path, "site", source_key="source:a", ref_patterns=["release-*"])
    site.delete_branch("release-1")
    _refresh(tmp_path, "site", source_key="source:a", ref_patterns=["release-*"])

    (change,) = _remove("a")

    assert change.startswith("removed acme/site (")
    assert not _repo(store_root, "site").exists()


def test_removing_a_source_releases_a_repo_workspace_holds(
    tmp_path: Path, store_root: Path
) -> None:
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    workspace = RepoStore.for_url(
        site.url, plugin="workspace", error=RepoStoreError, plain_clone=True
    )
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    worktree = tmp_path / "ws" / "site"
    workspace.worktree_add(worktree, "refs/remotes/origin/main", branch="fix")
    _save_sources("a")
    _refresh(tmp_path, "site", source_key="source:a")

    (change,) = _remove("a")

    assert change.startswith("released acme/site (kept: workspace")
    refs = _git(_repo(store_root, "site"), "for-each-ref", "--format=%(refname)").splitlines()
    assert "refs/remotes/origin/main" in refs
    assert not [ref for ref in refs if ref.startswith(_NAMESPACE)]
    status = store_module.run_git(["status", "--porcelain"], timeout=30, cwd=worktree, capture=True)
    assert status.text == ""
    assert (worktree / _REQS).read_text() == "- src: https://github.com/acme/base\n"


def test_removing_a_source_leaves_a_repo_it_never_fetched(tmp_path: Path, store_root: Path) -> None:
    """The source selects the repo, but no ref matched: ansible has no part in it to release."""
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    workspace = RepoStore.for_url(
        site.url, plugin="workspace", error=RepoStoreError, plain_clone=True
    )
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    _save_sources("a")
    _refresh(tmp_path, "site", source_key="source:a", ref_patterns=["nomatch-*"])
    before = _git(_repo(store_root, "site"), "for-each-ref", "--format=%(refname)")

    assert _remove("a", "--dry-run") == []
    assert _remove("a") == []
    assert _git(_repo(store_root, "site"), "for-each-ref", "--format=%(refname)") == before


def test_rerunning_a_remove_finishes_a_release_that_was_interrupted(
    tmp_path: Path, store_root: Path
) -> None:
    """Killed after ansible's refs and file went, before the repo was removed."""
    _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    _save_sources("a")
    _refresh(tmp_path, "site", source_key="source:a")
    repo = _repo(store_root, "site")
    _git(repo, "config", "untaped.release", "ansible")
    for ref in _git(repo, "for-each-ref", "--format=%(refname)", _NAMESPACE).split():
        _git(repo, "update-ref", "-d", ref)
    (repo / "untaped-ansible.json").unlink()

    assert _remove("a", "--dry-run") == ["would release acme/site"]
    (change,) = _remove("a")

    assert change.startswith("removed acme/site (")
    assert not repo.exists()


def test_removing_a_source_while_another_sources_refresh_holds_the_repo_keeps_it(
    tmp_path: Path, store_root: Path
) -> None:
    """The decision reads the other sources' last refreshes, not the store: no wait, no release."""
    site = _remote(tmp_path, "site", {_REQS: "- src: https://github.com/acme/base\n"})
    _save_sources("a", "b")
    _refresh(tmp_path, "site", source_key="source:a")
    _refresh(tmp_path, "site", source_key="source:b")
    store = RepoStore.for_url(site.url, plugin=plugin_of(AnsibleSettings), error=RepoStoreError)
    held = threading.Event()
    done = threading.Event()

    def refresh_holding_the_lock() -> None:
        with store._repo.locked():  # what b's fetch holds
            held.set()
            done.wait(timeout=30)

    holder = threading.Thread(target=refresh_holding_the_lock)
    holder.start()
    try:
        assert held.wait(timeout=10)
        assert _remove("a") == ["kept acme/site (selected by source b)"]
    finally:
        done.set()
        holder.join()
    assert _repo(store_root, "site").is_dir()


def test_a_refresh_stores_the_variable_facts_of_each_ref_from_the_store(
    tmp_path: Path, store_root: Path
) -> None:
    _remote(
        tmp_path,
        "site",
        {
            _REQS: "- src: https://github.com/acme/base\n",
            "group_vars/all.yml": "base_port: 1\n",
            "templates/app.conf": "listen {{ base_port }}\n",
            "docs/notes.md": "base_port\n",
        },
    )

    assert _refresh(tmp_path, "site").failures == ()

    sha = _git(_repo(store_root, "site"), "rev-parse", f"{_NAMESPACE}heads/main")
    facts = SqliteDependencyIndex(index_path(tmp_path)).variable_facts(
        {("acme/site", sha)}, {"base_port"}
    )
    assert not facts.unanalysed
    assert [(fact.file, fact.how) for fact in facts.facts[("acme/site", sha)]] == [
        ("group_vars/all.yml", "sets"),
        ("templates/app.conf", "reads"),
    ]


def test_a_source_from_a_plugin_without_select_takes_its_listing_by_match(
    tmp_path: Path, store_root: Path, calls: list[Call]
) -> None:
    """S10: a plugin listing only ``repos()``, narrowed by match; refs read with ls-remote."""
    api = git_remote(tmp_path, "infra/api", host="gitlab.example")
    api.commit(_REQS, "- src: https://gitlab.example/infra/base\n")
    git_remote(tmp_path, "data/lake", host="gitlab.example")
    gitlab = {"gitlab": (listing(*(make_repo(name, "gitlab.example") for name in _GITLAB)),)}

    saved = run("source", "set", "infra", "--from", "gitlab", "--match", "infra/*")
    refreshed = run("source", "refresh", "infra", providers=gitlab)

    assert saved.exit_code == 0, saved.output
    assert refreshed.exit_code == 0, refreshed.output
    assert "refreshed source infra: 1 repo, 1 ref, 1 edge" in refreshed.stderr
    assert "warning" not in refreshed.stderr  # a match-only selector asks for the listing
    remotes = {arg for argv, _ in calls if argv[0] == "ls-remote" for arg in argv if "://" in arg}
    assert remotes == {api.url}  # data/lake didn't match
    assert _dependents(tmp_path, "infra/base", source_key="source:infra") == {"infra/api@main"}


_GITLAB = ("infra/api", "data/lake")
