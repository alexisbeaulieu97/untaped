"""``create``/``add``/``repos resolve`` asking the plugins that fill ``RepoSource``, end to end.

Real git and state; the providers are fakes (``workspace_test_support``) composed beside
git and workspace, named after the plugins of design §15's scenarios.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

from untaped.sdk import UntapedError, key
from untaped.sdk.contracts import NotReady, Source
from untaped.sdk.testing import ManualClock, Ran, Running
from untaped.sdk.testing.git import GitRemote
from untaped_workspace.api import Repo
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from workspace_test_support import (
    Listing,
    Project,
    composed,
    err,
    forge,
    git,
    host,
    listing,
    rank,
    repo,
    run,
)


class _Gitlab(BaseModel):
    base_url: str | None = None


class _Github(BaseModel):
    default_org: str | None = None


NO_BASE_URL = NotReady("no base URL", key(_Gitlab).base_url)
GITHUB = Source(plugin="github", kind="workspace.repo")


@contextmanager
def providers(**plugins: Sequence[type]) -> Iterator[Running]:
    """Workspace composed with fake plugins, each filling contracts with its provider classes."""
    with composed({name: tuple(p) for name, p in plugins.items()}) as running:
        yield running


def _rows(result: Ran) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def _listed(remote: GitRemote, **fields: object) -> Repo:
    """``remote`` as a provider lists it: ``acme/<name>`` at its URL."""
    name = remote.url.removeprefix("https://git.example/").removesuffix(".git")
    return Repo(name=name, url=remote.url, **fields)  # type: ignore[arg-type]


def _nothing_created(workspace_env: Path) -> bool:
    return StateWorkspaceStore().get("J-1") is None and not (workspace_env / "J-1").exists()


def test_create_by_name_checks_out_the_repo_the_ready_provider_lists(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """S1: gitlab installed, not configured: ``--repo acme/api`` is github's."""
    api = make_upstream("api", branches=("develop",))
    gitlab = listing(repo("acme/api", "gitlab.example"), not_ready=NO_BASE_URL)
    with providers(github=[listing(_listed(api, default_branch="develop"))], gitlab=[gitlab]):
        created = run(["create", "J-1", "--repo", "acme/api", "--format", "json"])
    assert created.exit == 0, err(created)
    [row] = _rows(created)
    assert (row["repo"], row["action"], row["base"]) == ("acme/api", "created", "develop")
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [spec] = record.repos
    assert (spec.url, spec.source, spec.default_branch) == (api.url, GITHUB, "develop")
    assert gitlab.calls == 0


def test_a_repo_two_unranked_providers_list_is_a_usage_error_naming_the_rank_command(
    workspace_env: Path,
) -> None:
    """S3: exit 2 with the ``untaped plugin rank`` hint; nothing is created."""
    with providers(
        github=[listing(repo("acme/api"))], gitlab=[listing(repo("acme/api", "gitlab.example"))]
    ):
        result = run(["create", "J-1", "--repo", "acme/api"])
    assert result.exit == 2, err(result)
    assert "error: github, gitlab each have a match; rank them to choose" in err(result)
    assert "hint: run `untaped plugin rank workspace.repo_source repos github gitlab`" in (
        err(result)
    )
    assert _nothing_created(workspace_env)


def test_a_ranked_provider_that_is_down_fails_with_its_own_exit_code(workspace_env: Path) -> None:
    """S4: github ranked first and down, nothing stored: its error (exit 5), not gitlab's."""
    rank("github", "gitlab")
    down = UntapedError("HTTP 503", category="unavailable", system="github")
    with providers(
        github=[listing(error=down)], gitlab=[listing(repo("acme/api", "gitlab.example"))]
    ):
        result = run(["create", "J-1", "--repo", "acme/api"])
    assert result.exit == 5, err(result)
    assert "HTTP 503" in err(result)
    assert _nothing_created(workspace_env)


def test_an_expired_token_exits_with_the_auth_code_and_its_hint(workspace_env: Path) -> None:
    """S6: gitlab's token expired, nothing ranked: exit 4 and ``untaped auth login gitlab``."""
    expired = UntapedError(
        "401 Unauthorized", category="auth", system="gitlab", hint="run `untaped auth login gitlab`"
    )
    with providers(github=[listing(repo("acme/api"))], gitlab=[listing(error=expired)]):
        result = run(["create", "J-1", "--repo", "acme/api"])
    assert result.exit == 4, err(result)
    assert "hint: run `untaped auth login gitlab`" in err(result)
    assert "not found" not in err(result)
    assert _nothing_created(workspace_env)


def test_a_typed_url_is_checked_out_under_its_full_path_without_asking_a_provider(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """S9: a URL is a plain repo named by its full path, with no source."""
    api = make_upstream("api")
    github = listing(repo("acme/api"))
    with providers(github=[github]):
        created = run(["create", "J-1", "--repo", api.url, "--format", "json"])
    assert created.exit == 0, err(created)
    assert [row["repo"] for row in _rows(created)] == ["acme/api"]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [spec] = record.repos
    assert (spec.name, spec.url, spec.source) == ("acme/api", api.url, None)
    assert github.calls == 0


def test_with_no_provider_ready_a_name_is_not_found_naming_the_setting(
    workspace_env: Path,
) -> None:
    """S12: github has no inventory scope: exit 2, the hint names the setting and the URL."""
    github = listing(
        repo("acme/api"), not_ready=NotReady("no inventory scope", key(_Github).default_org)
    )
    with providers(github=[github]):
        result = run(["create", "J-1", "--repo", "acme/api"])
    assert result.exit == 2, err(result)
    assert result.stderr[-2:] == [
        "error: repo not found: 'acme/api'",
        "hint: github wasn't asked: no inventory scope; set github.default_org, "
        "or pass the repo's clone URL",
    ]
    assert _nothing_created(workspace_env)


def test_a_listed_url_on_a_host_its_source_does_not_serve_is_refused(workspace_env: Path) -> None:
    """S17 (workspace's side): github serves github.com; its acme/api on evil.example is
    refused before anything is fetched (exit 2)."""
    forged = Repo(name="acme/api", url="https://evil.example/acme/api.git")
    with providers(github=[listing(forged), host("github.com")]):
        result = run(["create", "J-1", "--repo", "acme/api"])
    assert result.exit == 2, err(result)
    assert "refusing to fetch it" in err(result)
    assert _nothing_created(workspace_env)


@pytest.mark.parametrize("url", ["file:///srv/git/acme/api.git", "/srv/git/acme/api.git"])
def test_a_typed_url_untaped_refuses_exits_2(url: str, workspace_env: Path) -> None:
    """S17: a local path or ``file://`` URL is never a remote."""
    result = run(["create", "J-1", "--repo", url])
    assert result.exit == 2, err(result)
    assert f"error: repo '{url}': url: " in err(result)
    assert "hint: pass an https:// or ssh:// clone URL, or a repo name" in err(result)
    assert _nothing_created(workspace_env)


def _pipe(*records: tuple[str | None, dict[str, object]]) -> str:
    lines = []
    for kind, record in records:
        envelope: dict[str, object] = {"untaped": "1", "record": record}
        if kind is not None:
            envelope["kind"] = kind
        lines.append(json.dumps(envelope))
    return "\n".join(lines) + "\n"


def test_piped_records_of_a_providers_kind_go_through_its_bridge_without_listing(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """S11 (workspace's side): ``forge.project`` rows are converted by the forge's
    ``to_repo``; the forge lists nothing (its API may be down)."""
    api, web = make_upstream("api"), make_upstream("web")
    fake = forge()
    piped = _pipe(
        ("forge.project", {"path": "acme/api", "clone_url": api.url}),
        ("forge.project", {"path": "acme/web", "clone_url": web.url}),
    )
    with providers(forge=[fake]):
        created = run(["create", "J-1", "--stdin", "--format", "json"], input=piped)
    assert created.exit == 0, err(created)
    assert [(r["repo"], r["action"]) for r in _rows(created)] == [
        ("acme/api", "created"),
        ("acme/web", "created"),
    ]
    assert [p.path for p in fake.bridged] == ["acme/api", "acme/web"]
    assert fake.listings == 0
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    sources = [spec.source for spec in record.repos]
    assert [(s.plugin, s.kind) for s in sources if s is not None] == [
        ("forge", "forge.project"),
        ("forge", "forge.project"),
    ]


def test_a_forged_piped_record_refused_by_its_bridge_exits_2(workspace_env: Path) -> None:
    """S17 (workspace's side): the provider's bridge refuses the record: exit 2, nothing made."""
    forged = ("forge.project", {"path": "acme/api", "clone_url": "https://evil.example/x.git"})
    with providers(forge=[forge()]):
        result = run(["create", "J-1", "--stdin"], input=_pipe(forged))
    assert result.exit == 2, err(result)
    assert "clone URL is not on git.example" in err(result)
    assert _nothing_created(workspace_env)


def test_a_piped_workspace_repo_is_read_as_it_is(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api = make_upstream("api")
    github = listing()
    piped = _pipe(("workspace.repo", {"name": "acme/api", "url": api.url, "description": "API"}))
    with providers(github=[github]):
        created = run(["create", "J-1", "--stdin", "--format", "json"], input=piped)
    assert created.exit == 0, err(created)
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [spec] = record.repos
    assert (spec.name, spec.url, spec.description, spec.source) == (
        "acme/api",
        api.url,
        "API",
        None,
    )
    assert github.calls == 0


def test_a_piped_workspace_repo_with_a_local_url_is_invalid_input(workspace_env: Path) -> None:
    piped = _pipe(("workspace.repo", {"name": "acme/api", "url": "file:///srv/acme/api.git"}))
    result = run(["create", "J-1", "--stdin"], input=piped)
    assert result.exit == 1, err(result)
    assert result.stderr == ["error: line 1: url: file:// is not an accepted Git URL scheme"]
    assert _nothing_created(workspace_env)


def test_a_piped_record_without_a_kind_is_a_usage_error(workspace_env: Path) -> None:
    piped = _pipe((None, {"name": "acme/api", "url": "https://git.example/acme/api.git"}))
    result = run(["create", "J-1", "--stdin"], input=piped)
    assert result.exit == 2, err(result)
    assert "line 1: a piped record needs a kind to be read" in err(result)
    assert _nothing_created(workspace_env)


def test_a_piped_record_no_provider_reads_is_a_usage_error(workspace_env: Path) -> None:
    piped = _pipe(("forge.project", {"path": "acme/api", "clone_url": "https://x.example/a.git"}))
    result = run(["create", "J-1", "--stdin"], input=piped)  # no forge composed
    assert result.exit == 2, err(result)
    assert "no installed provider reads forge.project records as workspace.repo" in err(result)
    assert _nothing_created(workspace_env)


def test_piped_lines_are_names_or_urls(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    with providers(github=[listing(_listed(api))]):
        created = run(
            ["create", "J-1", "--stdin", "--format", "json"], input=f"acme/api\n{web.url}\n"
        )
    assert created.exit == 0, err(created)
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    assert [(s.name, s.source) for s in record.repos] == [("acme/api", GITHUB), ("acme/web", None)]


def test_a_workspace_works_from_its_saved_url_after_its_source_is_uninstalled(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """S18: with github gone, ``status --fetch`` fetches from the saved URL (the user's own
    git config resolves it) and the source stays in ``state.yml``."""
    api = make_upstream("api")
    with providers(github=[listing(_listed(api))]):
        assert run(["create", "J-1", "--repo", "acme/api"]).exit == 0
    new = api.commit("later.txt", "later\n")
    fetched = run(["status", "J-1", "--fetch", "--format", "json"])  # git and workspace only
    assert fetched.exit == 0, err(fetched)
    assert [row["state"] for row in _rows(fetched)] == ["ok"]
    assert git(workspace_env / "J-1" / "api", "rev-parse", "origin/main") == new
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].source == GITHUB
    resolved = run(["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit == 0, err(resolved)
    assert [(r["action"], r["detail"]) for r in _rows(resolved)] == [
        ("skipped", "github is not installed or not ready")
    ]


# --- repos resolve -------------------------------------------------------------


def _create(*listed: Repo, typed: Sequence[str] = ()) -> None:
    """Workspace J-1 with ``listed`` (github's, by name) and ``typed`` URLs."""
    args = [arg for each in listed for arg in ("--repo", each.name)]
    args += [arg for url in typed for arg in ("--repo", url)]
    with providers(github=[listing(*listed)]):
        created = run(["create", "J-1", *args])
    assert created.exit == 0, err(created)


def test_resolve_saves_the_url_the_source_lists_now_and_rewrites_the_worktree_config(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web, docs = make_upstream("api"), make_upstream("web"), make_upstream("docs")
    _create(_listed(api), _listed(web), typed=[docs.url])
    ssh = "ssh://git@git.example/acme/api.git"
    github = listing(Repo(name="acme/api", url=ssh), _listed(web))
    with providers(github=[github]):
        resolved = run(["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit == 0, err(resolved)
    assert [(r["repo"], r["action"], r["url"], r["detail"]) for r in _rows(resolved)] == [
        ("acme/api", "updated", ssh, f"was {api.url}"),
        ("acme/web", "unchanged", web.url, ""),
        ("acme/docs", "skipped", docs.url, "no source to ask"),
    ]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    assert [(s.url, s.source) for s in record.repos] == [
        (ssh, GITHUB),
        (web.url, GITHUB),
        (docs.url, None),
    ]
    worktree = workspace_env / "J-1" / "api"
    assert git(worktree, "config", "--worktree", f"url.{ssh}.insteadOf") == api.url


def test_resolve_keeps_the_old_url_when_the_worktree_cannot_be_configured(
    make_upstream: Callable[..., GitRemote],
    workspace_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new URL is saved only once the worktree follows it, so a retry configures it."""
    api = make_upstream("api")
    _create(_listed(api))
    ssh = "ssh://git@git.example/acme/api.git"

    def broken(self: object, url: str, dest: Path) -> None:
        raise UntapedError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr(LocalGitWorktrees, "configure", broken)
        with providers(github=[listing(Repo(name="acme/api", url=ssh))]):
            failed = run(["repos", "resolve", "J-1", "--format", "json"])
    assert failed.exit != 0
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].url == api.url

    with providers(github=[listing(Repo(name="acme/api", url=ssh))]):
        retried = run(["repos", "resolve", "J-1", "--format", "json"])
    assert retried.exit == 0, err(retried)
    assert [r["action"] for r in _rows(retried)] == ["updated"]
    worktree = workspace_env / "J-1" / "api"
    assert git(worktree, "config", "--worktree", f"url.{ssh}.insteadOf") == api.url


def test_resolve_saves_metadata_only_changes_without_reporting_them(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    web = make_upstream("web")
    _create(_listed(web))
    with providers(github=[listing(_listed(web, description="now described"))]):
        resolved = run(["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit == 0, err(resolved)
    assert [(r["action"], r["detail"]) for r in _rows(resolved)] == [("unchanged", "")]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].description == "now described"


def test_resolve_refuses_a_url_of_another_store_repo(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api = make_upstream("api")
    _create(_listed(api))
    moved = Repo(name="acme/api", url="https://git.example/acme/api-v2.git")
    with providers(github=[listing(moved)]):
        resolved = run(["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit == 1, err(resolved)
    [row] = _rows(resolved)
    assert (row["action"], row["url"]) == ("failed", moved.url)
    assert "another store repo than" in str(row["detail"])
    assert "remove api from the workspace and add it again" in str(row["detail"])
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].url == api.url


def test_resolve_skips_a_source_that_is_not_ready_and_reports_one_that_fails(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api = make_upstream("api")
    _create(_listed(api))
    with providers(github=[listing(_listed(api), not_ready=NotReady("no inventory scope"))]):
        skipped = run(["repos", "resolve", "J-1", "--format", "json"])
    assert skipped.exit == 0, err(skipped)
    assert [r["action"] for r in _rows(skipped)] == ["skipped"]
    down = UntapedError("HTTP 503", category="unavailable", system="github")
    with providers(github=[listing(error=down)]):
        failed = run(["repos", "resolve", "J-1", "--format", "json"])
    assert failed.exit == 5, err(failed)
    [row] = _rows(failed)
    assert (row["action"], row["detail"]) == ("failed", "HTTP 503")
    error = row["error"]
    assert isinstance(error, dict) and error["category"] == "unavailable"


def test_resolve_rows_are_resolve_outcome_records(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = make_upstream("api")
    _create(_listed(api))
    monkeypatch.chdir(workspace_env / "J-1" / "api")  # NAME defaults to the current workspace
    with providers(github=[listing(_listed(api))]):
        piped = run(["repos", "resolve", "--format", "pipe"])
    assert piped.exit == 0, err(piped)
    [line] = piped.stdout.splitlines()
    envelope = json.loads(line)
    assert envelope["kind"] == "workspace.resolve_outcome"
    assert envelope["record"]["action"] == "unchanged"


def test_add_by_name_asks_the_providers_too(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    with providers(github=[listing(_listed(api), _listed(web))]):
        assert run(["create", "J-1", "--repo", "api"]).exit == 0
        added = run(["add", "J-1", "--read-only", "web", "--format", "json"])
    assert added.exit == 0, err(added)
    assert [(r["repo"], r["read_only"]) for r in _rows(added)] == [("acme/web", True)]
    assert (workspace_env / "J-1" / "web" / "README.md").exists()


def test_a_picked_repo_keeps_its_listed_project(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """A forge's repo keeps its own record in ``source`` (what ``own()`` rehydrates later)."""
    api = make_upstream("api")
    fake = forge(Project(path="acme/api", clone_url=api.url))
    with providers(forge=[fake]):
        created = run(["create", "J-1", "--repo", "acme/api"])
    assert created.exit == 0, err(created)
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    source = record.repos[0].source
    assert source is not None and (source.plugin, source.kind) == ("forge", "forge.project")
    assert source.record == {"path": "acme/api", "clone_url": api.url}


def test_create_counts_the_repo_sources_under_its_own_task(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, clock: ManualClock
) -> None:
    from untaped.sdk.diagnostic import Progress

    api = make_upstream("api")

    class Slow(Listing):
        rows: ClassVar[list[Repo]] = [_listed(api)]

        def repos(self) -> list[Repo]:
            clock.advance(3)  # past the 2 s a progress line waits for
            return super().repos()

    with providers(github=[Slow]):
        created = run(["create", "J-1", "--repo", "acme/api"])
    assert created.exit == 0, err(created)
    lines = [each for each in created.diagnostics if isinstance(each, Progress)]
    assert any(
        each.label == "Creating workspace J-1"
        and (each.done, each.total) == (1, 1)
        and "github" in each.message
        for each in lines
    ), [each.message for each in lines]
