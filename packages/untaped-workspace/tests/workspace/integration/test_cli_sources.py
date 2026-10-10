"""``create``/``add``/``repos resolve`` asking the plugins that fill ``RepoSource``, end to end.

Real git and state; the providers are fakes (``workspace.fakes``) composed beside
git and workspace, named after the plugins of design §15's scenarios.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from untaped.contracts import Contract, NotReady, Source
from untaped.sdk import UntapedError
from untaped.testing import CliInvoker, CliResult, compose_with
from untaped.testing.git import GitRemote
from untaped_git import SPEC as GIT
from untaped_workspace import SPEC as WORKSPACE
from untaped_workspace.api import Repo
from untaped_workspace.cli import app
from untaped_workspace.infrastructure import StateWorkspaceStore
from workspace.conftest import git
from workspace.fakes import Forge, Host, Listing, Project, rank, repo

pytestmark = pytest.mark.usefixtures("workspace_env", "composed")
run = CliInvoker().invoke
NO_BASE_URL = NotReady("no base URL", setting="gitlab.base_url")
GITHUB = Source(plugin="github", kind="workspace.repo")


@contextmanager
def providers(**plugins: Sequence[Contract]) -> Iterator[None]:
    """Git and workspace composed with fake plugins, each offering its providers."""
    with compose_with(GIT, WORKSPACE, provides={name: list(p) for name, p in plugins.items()}):
        yield


def _rows(result: CliResult) -> list[dict[str, object]]:
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
    gitlab = Listing(repo("acme/api", "gitlab.example"), not_ready=NO_BASE_URL)
    with providers(github=[Listing(_listed(api, default_branch="develop"))], gitlab=[gitlab]):
        created = run(app, ["create", "J-1", "--repo", "acme/api", "--format", "json"])
    assert created.exit_code == 0, created.output
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
        github=[Listing(repo("acme/api"))], gitlab=[Listing(repo("acme/api", "gitlab.example"))]
    ):
        result = run(app, ["create", "J-1", "--repo", "acme/api"])
    assert result.exit_code == 2, result.output
    assert "error: github, gitlab each have a match; rank them to choose" in result.stderr
    assert "hint: run `untaped plugin rank workspace.repo_source repos github gitlab`" in (
        result.stderr
    )
    assert _nothing_created(workspace_env)


def test_a_ranked_provider_that_is_down_fails_with_its_own_exit_code(workspace_env: Path) -> None:
    """S4: github ranked first and down, nothing stored: its error (exit 5), not gitlab's."""
    rank("github", "gitlab")
    down = UntapedError("HTTP 503", category="unavailable", system="github")
    with providers(
        github=[Listing(error=down)], gitlab=[Listing(repo("acme/api", "gitlab.example"))]
    ):
        result = run(app, ["create", "J-1", "--repo", "acme/api"])
    assert result.exit_code == 5, result.output
    assert "HTTP 503" in result.stderr
    assert _nothing_created(workspace_env)


def test_an_expired_token_exits_with_the_auth_code_and_its_hint(workspace_env: Path) -> None:
    """S6: gitlab's token expired, nothing ranked: exit 4 and ``untaped auth set gitlab``."""
    expired = UntapedError(
        "401 Unauthorized", category="auth", system="gitlab", hint="run `untaped auth set gitlab`"
    )
    with providers(github=[Listing(repo("acme/api"))], gitlab=[Listing(error=expired)]):
        result = run(app, ["create", "J-1", "--repo", "acme/api"])
    assert result.exit_code == 4, result.output
    assert "hint: run `untaped auth set gitlab`" in result.stderr
    assert "not found" not in result.stderr
    assert _nothing_created(workspace_env)


def test_a_typed_url_is_checked_out_under_its_full_path_without_asking_a_provider(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """S9: a URL is a plain repo named by its full path, with no source."""
    api = make_upstream("api")
    github = Listing(repo("acme/api"))
    with providers(github=[github]):
        created = run(app, ["create", "J-1", "--repo", api.url, "--format", "json"])
    assert created.exit_code == 0, created.output
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
    github = Listing(
        repo("acme/api"), not_ready=NotReady("no inventory scope", setting="github.default_org")
    )
    with providers(github=[github]):
        result = run(app, ["create", "J-1", "--repo", "acme/api"])
    assert result.exit_code == 2, result.output
    assert result.stderr.endswith(
        "error: repo not found: 'acme/api'\n"
        "hint: github wasn't asked: no inventory scope; set github.default_org, "
        "or pass the repo's clone URL\n"
    )
    assert _nothing_created(workspace_env)


def test_a_listed_url_on_a_host_its_source_does_not_serve_is_refused(workspace_env: Path) -> None:
    """S17 (workspace's side): github serves github.com; its acme/api on evil.example is
    refused before anything is fetched (exit 2)."""
    forged = Repo(name="acme/api", url="https://evil.example/acme/api.git")
    with providers(github=[Listing(forged), Host("github.com")]):
        result = run(app, ["create", "J-1", "--repo", "acme/api"])
    assert result.exit_code == 2, result.output
    assert "refusing to fetch it" in result.stderr
    assert _nothing_created(workspace_env)


@pytest.mark.parametrize("url", ["file:///srv/git/acme/api.git", "/srv/git/acme/api.git"])
def test_a_typed_url_untaped_refuses_exits_2(url: str, workspace_env: Path) -> None:
    """S17: a local path or ``file://`` URL is never a remote."""
    result = run(app, ["create", "J-1", "--repo", url])
    assert result.exit_code == 2, result.output
    assert f"error: repo '{url}': url: " in result.stderr
    assert "hint: pass an https:// or ssh:// clone URL, or a repo name" in result.stderr
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
    forge = Forge()
    piped = _pipe(
        ("forge.project", {"path": "acme/api", "clone_url": api.url}),
        ("forge.project", {"path": "acme/web", "clone_url": web.url}),
    )
    with providers(forge=[forge]):
        created = run(app, ["create", "J-1", "--stdin", "--format", "json"], input=piped)
    assert created.exit_code == 0, created.output
    assert [(r["repo"], r["action"]) for r in _rows(created)] == [
        ("acme/api", "created"),
        ("acme/web", "created"),
    ]
    assert [p.path for p in forge.bridged] == ["acme/api", "acme/web"]
    assert forge.listings == 0
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
    with providers(forge=[Forge()]):
        result = run(app, ["create", "J-1", "--stdin"], input=_pipe(forged))
    assert result.exit_code == 2, result.output
    assert "clone URL is not on git.example" in result.stderr
    assert _nothing_created(workspace_env)


def test_a_piped_workspace_repo_is_read_as_it_is(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api = make_upstream("api")
    github = Listing()
    piped = _pipe(("workspace.repo", {"name": "acme/api", "url": api.url, "description": "API"}))
    with providers(github=[github]):
        created = run(app, ["create", "J-1", "--stdin", "--format", "json"], input=piped)
    assert created.exit_code == 0, created.output
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
    result = run(app, ["create", "J-1", "--stdin"], input=piped)
    assert result.exit_code == 1, result.output
    assert "line 1: invalid workspace.repo record: url: " in result.stderr
    assert _nothing_created(workspace_env)


def test_a_piped_record_without_a_kind_is_a_usage_error(workspace_env: Path) -> None:
    piped = _pipe((None, {"name": "acme/api", "url": "https://git.example/acme/api.git"}))
    result = run(app, ["create", "J-1", "--stdin"], input=piped)
    assert result.exit_code == 2, result.output
    assert "line 1: a piped record needs a kind to be read" in result.stderr
    assert _nothing_created(workspace_env)


def test_a_piped_record_no_provider_reads_is_a_usage_error(workspace_env: Path) -> None:
    piped = _pipe(("forge.project", {"path": "acme/api", "clone_url": "https://x.example/a.git"}))
    result = run(app, ["create", "J-1", "--stdin"], input=piped)  # no forge composed
    assert result.exit_code == 2, result.output
    assert "no installed provider reads forge.project records as workspace.repo" in result.stderr
    assert _nothing_created(workspace_env)


def test_piped_lines_are_names_or_urls(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    with providers(github=[Listing(_listed(api))]):
        created = run(
            app, ["create", "J-1", "--stdin", "--format", "json"], input=f"acme/api\n{web.url}\n"
        )
    assert created.exit_code == 0, created.output
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    assert [(s.name, s.source) for s in record.repos] == [("acme/api", GITHUB), ("acme/web", None)]


def test_a_workspace_works_from_its_saved_url_after_its_source_is_uninstalled(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """S18: with github gone, ``status --fetch`` fetches from the saved URL (the user's own
    git config resolves it) and the source stays in ``state.yml``."""
    api = make_upstream("api")
    with providers(github=[Listing(_listed(api))]):
        assert run(app, ["create", "J-1", "--repo", "acme/api"]).exit_code == 0
    new = api.commit("later.txt", "later\n")
    fetched = run(app, ["status", "J-1", "--fetch", "--format", "json"])  # git and workspace only
    assert fetched.exit_code == 0, fetched.output
    assert [row["state"] for row in _rows(fetched)] == ["ok"]
    assert git(workspace_env / "J-1" / "api", "rev-parse", "origin/main") == new
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].source == GITHUB
    resolved = run(app, ["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit_code == 0, resolved.output
    assert [(r["action"], r["detail"]) for r in _rows(resolved)] == [
        ("skipped", "github is not installed or not ready")
    ]


# --- repos resolve -------------------------------------------------------------


def _create(*listed: Repo, typed: Sequence[str] = ()) -> None:
    """Workspace J-1 with ``listed`` (github's, by name) and ``typed`` URLs."""
    args = [arg for each in listed for arg in ("--repo", each.name)]
    args += [arg for url in typed for arg in ("--repo", url)]
    with providers(github=[Listing(*listed)]):
        created = run(app, ["create", "J-1", *args])
    assert created.exit_code == 0, created.output


def test_resolve_saves_the_url_the_source_lists_now_and_rewrites_the_worktree_config(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web, docs = make_upstream("api"), make_upstream("web"), make_upstream("docs")
    _create(_listed(api), _listed(web), typed=[docs.url])
    ssh = "ssh://git@git.example/acme/api.git"
    github = Listing(Repo(name="acme/api", url=ssh), _listed(web))
    with providers(github=[github]):
        resolved = run(app, ["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit_code == 0, resolved.output
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


def test_resolve_saves_metadata_only_changes_without_reporting_them(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    web = make_upstream("web")
    _create(_listed(web))
    with providers(github=[Listing(_listed(web, description="now described"))]):
        resolved = run(app, ["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit_code == 0, resolved.output
    assert [(r["action"], r["detail"]) for r in _rows(resolved)] == [("unchanged", "")]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].description == "now described"


def test_resolve_refuses_a_url_of_another_store_repo(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api = make_upstream("api")
    _create(_listed(api))
    moved = Repo(name="acme/api", url="https://git.example/acme/api-v2.git")
    with providers(github=[Listing(moved)]):
        resolved = run(app, ["repos", "resolve", "J-1", "--format", "json"])
    assert resolved.exit_code == 1, resolved.output
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
    with providers(github=[Listing(_listed(api), not_ready=NotReady("no inventory scope"))]):
        skipped = run(app, ["repos", "resolve", "J-1", "--format", "json"])
    assert skipped.exit_code == 0, skipped.output
    assert [r["action"] for r in _rows(skipped)] == ["skipped"]
    down = UntapedError("HTTP 503", category="unavailable", system="github")
    with providers(github=[Listing(error=down)]):
        failed = run(app, ["repos", "resolve", "J-1", "--format", "json"])
    assert failed.exit_code == 5, failed.output
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
    with providers(github=[Listing(_listed(api))]):
        piped = run(app, ["repos", "resolve", "--format", "pipe"])
    assert piped.exit_code == 0, piped.output
    [line] = piped.stdout.splitlines()
    envelope = json.loads(line)
    assert envelope["kind"] == "workspace.resolve_outcome"
    assert envelope["record"]["action"] == "unchanged"


def test_add_by_name_asks_the_providers_too(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    with providers(github=[Listing(_listed(api), _listed(web))]):
        assert run(app, ["create", "J-1", "--repo", "api"]).exit_code == 0
        added = run(app, ["add", "J-1", "--read-only", "web", "--format", "json"])
    assert added.exit_code == 0, added.output
    assert [(r["repo"], r["read_only"]) for r in _rows(added)] == [("acme/web", True)]
    assert (workspace_env / "J-1" / "web" / "README.md").exists()


def test_a_picked_repo_keeps_its_listed_project(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    """A forge's repo keeps its own record in ``source`` (what ``own()`` rehydrates later)."""
    api = make_upstream("api")
    forge = Forge(Project(path="acme/api", clone_url=api.url))
    with providers(forge=[forge]):
        created = run(app, ["create", "J-1", "--repo", "acme/api"])
    assert created.exit_code == 0, created.output
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    source = record.repos[0].source
    assert source is not None and (source.plugin, source.kind) == ("forge", "forge.project")
    assert source.record == {"path": "acme/api", "clone_url": api.url}
