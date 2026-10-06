"""The picker path of `create`/`add`, scripted (no real terminal)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import Picked, PickItem, PickRequest, PickResult
from untaped.testing import CliInvoker, ScriptedPromptBackend
from untaped_github import api as github_api
from untaped_github.api import RepoInventory, RepositoryInventoryItem
from untaped_workspace.cli import app
from untaped_workspace.domain import RepoSpec, WorkspaceRecord
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from workspace.conftest import git

pytestmark = pytest.mark.usefixtures("workspace_env")
run = CliInvoker().invoke


def _pick(title: str, *urls: str) -> PickResult:
    settings = {"mode": "write", "base": "", "branch": ""}
    return PickResult(
        title=title,
        defaults=settings,
        picks=tuple(Picked(item=PickItem(id=url, label=url), settings=settings) for url in urls),
    )


def test_create_without_repos_opens_the_picker(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    url = str(make_upstream("api"))
    backend = ScriptedPromptBackend(picks=[_pick("J-1", url)])
    result = run(app, ["create"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 0, result.output
    assert (workspace_env / "J-1" / "api" / "README.md").exists()
    assert backend.calls == [("pick_many", "New workspace")]


def test_cancelled_picker_creates_nothing(workspace_env: Path) -> None:
    result = run(
        app, ["create", "J-1"], interactive=True, prompt_backend=ScriptedPromptBackend(picks=[None])
    )
    assert result.exit_code == 1
    assert "cancelled" in result.output
    assert not (workspace_env / "J-1").exists()
    assert StateWorkspaceStore().get("J-1") is None


def test_picker_confirmed_with_no_repos_creates_an_empty_workspace(workspace_env: Path) -> None:
    backend = ScriptedPromptBackend(picks=[_pick("J-1")])
    result = run(app, ["create"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 0, result.output
    assert (workspace_env / "J-1").is_dir()


def test_no_terminal_and_no_name_is_a_usage_error() -> None:
    result = run(app, ["create"])
    assert result.exit_code == 2
    assert "workspace name is required" in result.output
    assert "pass NAME and --repo OWNER/NAME (or --stdin)" in result.output
    assert "needs a terminal" in result.output


@pytest.mark.parametrize("name", ["a/b", "taken"])
def test_bad_name_is_refused_before_the_picker(name: str) -> None:
    StateWorkspaceStore().create(
        WorkspaceRecord(name="taken", created_at=datetime(2026, 10, 1, tzinfo=UTC))
    )
    backend = ScriptedPromptBackend()
    result = run(app, ["create", name], interactive=True, prompt_backend=backend)
    assert result.exit_code == 2
    assert backend.calls == []


def test_occupied_name_is_refused_before_the_picker(workspace_env: Path) -> None:
    (workspace_env / "old").mkdir(parents=True)
    (workspace_env / "old" / "x.txt").write_text("x")
    backend = ScriptedPromptBackend()
    result = run(app, ["create", "old"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 2
    assert "not empty" in result.output
    assert backend.calls == []


def test_repo_flags_without_a_name_is_a_usage_error(make_upstream: Callable[..., Path]) -> None:
    result = run(app, ["create", "--repo", str(make_upstream("api"))], interactive=True)
    assert result.exit_code == 2
    assert "workspace name is required" in result.output


def test_repo_flags_skip_the_picker(make_upstream: Callable[..., Path]) -> None:
    backend = ScriptedPromptBackend()
    result = run(
        app,
        ["create", "J-1", "--repo", str(make_upstream("api"))],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert backend.calls == []


def test_add_opens_the_picker(make_upstream: Callable[..., Path], workspace_env: Path) -> None:
    run(app, ["create", "J-1", "--repo", str(make_upstream("api"))])
    web = str(make_upstream("web"))
    backend = ScriptedPromptBackend(picks=[_pick("", web)])
    result = run(app, ["add", "J-1"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 0, result.output
    assert (workspace_env / "J-1" / "web").exists()
    assert backend.calls == [("pick_many", "Add to J-1")]


class _Capture(ScriptedPromptBackend):
    def __init__(self, result: PickResult | None) -> None:
        super().__init__(picks=[result])
        self.requests: list[PickRequest] = []

    def pick_many(self, request: PickRequest) -> PickResult | None:
        self.requests.append(request)
        return super().pick_many(request)


def test_add_does_not_offer_repos_already_in_the_workspace(workspace_env: Path) -> None:
    cache = workspace_env.parent / "cache" / "github.com" / "acme"
    for name in ("api", "web"):
        (cache / f"{name}.git").mkdir(parents=True)
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="J-1", created_at=datetime(2026, 10, 1, tzinfo=UTC)))
    spec = RepoSpec(
        url="git@github.com:acme/api.git", name="acme/api", dir="api", branch="J-1", base="main"
    )
    store.add_repos("J-1", [spec])
    backend = _Capture(None)
    result = run(app, ["add", "J-1"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 1
    (request,) = backend.requests
    assert request.title_label == ""
    assert [item.id for item in request.catalog.items] == ["github.com/acme/web"]
    assert request.subtitle is not None
    assert request.subtitle("", {"branch": ""}).endswith("J-1")


def test_create_picker_prefills_the_name_and_flag_defaults(
    make_upstream: Callable[..., Path],
) -> None:
    backend = _Capture(_pick("J-1", str(make_upstream("api"))))
    result = run(
        app,
        ["create", "J-1", "--base", "main", "--branch", "hotfix"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    (request,) = backend.requests
    assert (request.title, request.title_label) == ("J-1", "name")
    assert {s.key: s.default for s in request.settings} == {
        "mode": "write",
        "base": "main",
        "branch": "hotfix",
    }


def test_read_only_pick_is_a_detached_checkout(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    url = str(make_upstream("api"))
    settings = {"mode": "read-only", "base": "", "branch": ""}
    pick = PickResult(
        title="J-1",
        defaults={"mode": "write", "base": "", "branch": ""},
        picks=(Picked(item=PickItem(id=url, label=url), settings=settings),),
    )
    result = run(
        app, ["create"], interactive=True, prompt_backend=ScriptedPromptBackend(picks=[pick])
    )
    assert result.exit_code == 0, result.output
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].read_only
    assert git(workspace_env / "J-1" / "api", "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_cached_only_pick_uses_its_cache_url(
    make_upstream: Callable[..., Path], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inventory is unavailable (no scope) while GitHub is the default host: the
    cached-only repo must come from its own cache's origin, not github.com/acme/api."""
    upstream = make_upstream("api")
    origin = "https://gitlab.example/acme/api.git"
    for key, value in {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{upstream}.insteadOf",
        "GIT_CONFIG_VALUE_0": origin,
    }.items():
        monkeypatch.setenv(key, value)
    # A 10.x cache, made the way `create` makes one; its worktree is gone again.
    caches = LocalGitWorktrees(workspace_env.parent / "cache")
    scratch = workspace_env.parent / "scratch"
    caches.checkout(origin, scratch, branch=None, base=None)
    caches.remove(origin, scratch, force=True)
    settings = {"mode": "write", "base": "", "branch": ""}
    item = PickItem(id="gitlab.example/acme/api", label="gitlab.example/acme/api")
    backend = _Capture(
        PickResult(title="J-1", defaults=settings, picks=(Picked(item=item, settings=settings),))
    )
    result = run(app, ["create"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 0, result.output
    assert [i.id for i in backend.requests[0].catalog.items] == ["gitlab.example/acme/api"]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].url == origin
    assert (workspace_env / "J-1" / "api" / "README.md").exists()


class _RefreshThenPick(_Capture):
    """Selects, then forces a refresh in the picker before confirming."""

    def pick_many(self, request: PickRequest) -> PickResult | None:
        assert request.refresh is not None
        request.refresh(True)
        return super().pick_many(request)


def test_a_pick_dropped_by_a_refresh_uses_its_remembered_url(
    make_upstream: Callable[..., Path], workspace_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = str(make_upstream("api"))
    item = RepositoryInventoryItem(full_name="acme/api", clone_url=url)
    refreshed: list[bool] = []

    def inventory(*, refresh: bool | None = None) -> RepoInventory:
        if refresh is True:
            refreshed.append(True)
        repos = () if refreshed else (item,)
        return RepoInventory(repos=repos, refreshed_at=None, scope_key="k")

    monkeypatch.setattr(github_api, "repo_inventory", inventory)
    backend = _RefreshThenPick(_pick("J-1", "acme/api"))
    result = run(app, ["create"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 0, result.output
    assert refreshed == [True]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].url == url


def test_an_owner_less_hosted_cache_is_offered(workspace_env: Path) -> None:
    cache = workspace_env.parent / "cache" / "git.example" / "project.git"
    cache.parent.mkdir(parents=True)
    git(workspace_env.parent, "init", "-q", "--bare", str(cache))
    git(cache, "remote", "add", "origin", "https://git.example/project.git")
    backend = _Capture(None)
    run(app, ["create", "J-1"], interactive=True, prompt_backend=backend)
    (request,) = backend.requests
    assert [item.id for item in request.catalog.items] == ["git.example/project"]
