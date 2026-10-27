"""The picker path of `create`/`add`, scripted (no real terminal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import Picked, PickItem, PickRequest, PickResult
from untaped.sdk.contracts import Source, gather
from untaped.sdk.testing import ScriptedTerminal
from untaped.sdk.testing.git import GitRemote, global_config
from untaped_workspace.api import Repo, RepoSource
from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from workspace_test_support import composed, err, git, listing, run


@dataclass
class Picker:
    """The test's terminal, recording each picker it opens; ``before`` runs inside it first."""

    terminal: ScriptedTerminal
    requests: list[PickRequest] = field(default_factory=list)
    before: Callable[[PickRequest], object] | None = None


@pytest.fixture
def picker(terminal: ScriptedTerminal, monkeypatch: pytest.MonkeyPatch) -> Picker:
    recorded = Picker(terminal.answers())  # present, with nothing scripted yet
    answer = terminal.pick_many

    def pick_many(request: PickRequest) -> PickResult | None:
        recorded.requests.append(request)
        if recorded.before is not None:
            recorded.before(request)
        return answer(request)

    monkeypatch.setattr(terminal, "pick_many", pick_many)
    return recorded


def _pick(title: str, *urls: str) -> PickResult:
    settings = {"mode": "write", "base": "", "branch": ""}
    return PickResult(
        title=title,
        defaults=settings,
        picks=tuple(Picked(item=PickItem(id=url, label=url), settings=settings) for url in urls),
    )


def test_create_without_repos_opens_the_picker(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, picker: Picker
) -> None:
    url = make_upstream("api").url
    picker.terminal.picks(_pick("J-1", url))
    result = run(["create"])
    assert result.exit == 0, err(result)
    assert (workspace_env / "J-1" / "api" / "README.md").exists()
    assert picker.terminal.asked == [("pick_many", "New workspace")]


@pytest.mark.parametrize("command", ["create", "add"])
def test_the_picker_names_its_command_and_flags_for_the_no_terminal_refusal(
    command: str,
    picker: Picker,
) -> None:
    StateWorkspaceStore().create(
        WorkspaceRecord(name="J-1", created_at=datetime(2026, 10, 1, tzinfo=UTC))
    )
    picker.terminal.picks(None)
    run([command, *(["J-1"] if command == "add" else [])])
    (request,) = picker.requests
    assert request.command == f"untaped workspace {command}"
    assert request.alternative == f"untaped workspace {command} NAME --repo OWNER/NAME"


def test_cancelled_picker_creates_nothing(workspace_env: Path, picker: Picker) -> None:
    picker.terminal.picks(None)
    result = run(["create", "J-1"])
    assert result.exit == 1
    assert "cancelled" in err(result)
    assert not (workspace_env / "J-1").exists()
    assert StateWorkspaceStore().get("J-1") is None


def test_picker_confirmed_with_no_repos_creates_an_empty_workspace(
    workspace_env: Path, picker: Picker
) -> None:
    picker.terminal.picks(_pick("J-1"))
    result = run(["create"])
    assert result.exit == 0, err(result)
    assert (workspace_env / "J-1").is_dir()


def test_no_terminal_and_no_name_is_a_usage_error() -> None:
    result = run(["create"])
    assert result.exit == 2
    assert "workspace name is required" in err(result)
    assert "pass NAME and --repo OWNER/NAME (or --stdin)" in err(result)
    assert "needs a terminal" in err(result)


@pytest.mark.parametrize("name", ["a/b", "taken"])
def test_bad_name_is_refused_before_the_picker(name: str, picker: Picker) -> None:
    StateWorkspaceStore().create(
        WorkspaceRecord(name="taken", created_at=datetime(2026, 10, 1, tzinfo=UTC))
    )
    result = run(["create", name])
    assert result.exit == 2
    assert picker.terminal.asked == []


def test_occupied_name_is_refused_before_the_picker(workspace_env: Path, picker: Picker) -> None:
    (workspace_env / "old").mkdir(parents=True)
    (workspace_env / "old" / "x.txt").write_text("x")
    result = run(["create", "old"])
    assert result.exit == 2
    assert "not empty" in err(result)
    assert picker.terminal.asked == []


def test_repo_flags_without_a_name_is_a_usage_error(
    make_upstream: Callable[..., GitRemote],
) -> None:
    result = run(["create", "--repo", make_upstream("api").url])
    assert result.exit == 2
    assert "workspace name is required" in err(result)


def test_repo_flags_skip_the_picker(
    make_upstream: Callable[..., GitRemote], picker: Picker
) -> None:
    result = run(["create", "J-1", "--repo", make_upstream("api").url])
    assert result.exit == 0, err(result)
    assert picker.terminal.asked == []


def test_add_opens_the_picker(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, picker: Picker
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    web = make_upstream("web").url
    picker.terminal.picks(_pick("", web))
    result = run(["add", "J-1"])
    assert result.exit == 0, err(result)
    assert (workspace_env / "J-1" / "web").exists()
    assert picker.terminal.asked == [("pick_many", "Add to J-1")]


def test_create_picker_allows_confirming_no_repos(workspace_env: Path, picker: Picker) -> None:
    picker.terminal.picks(_pick("J-1"))
    result = run(["create"])
    assert result.exit == 0, err(result)
    assert [request.allow_empty for request in picker.requests] == [True]


def test_add_picker_requires_a_repo(
    make_upstream: Callable[..., GitRemote], picker: Picker
) -> None:
    run(["create", "J-1", "--repo", make_upstream("api").url])
    picker.terminal.picks(None)
    run(["add", "J-1"])
    assert [request.allow_empty for request in picker.requests] == [False]


def test_empty_flag_skips_the_picker(workspace_env: Path, picker: Picker) -> None:
    result = run(["create", "J-1", "--empty"])
    assert result.exit == 0, err(result)
    assert picker.terminal.asked == []
    assert (workspace_env / "J-1").is_dir()
    assert StateWorkspaceStore().get("J-1") is not None


def test_empty_flag_needs_a_name(picker: Picker) -> None:
    result = run(["create", "--empty"])
    assert result.exit == 2
    assert "workspace name is required" in err(result)
    assert "pass NAME before the options" in err(result)
    assert picker.terminal.asked == []


@pytest.mark.parametrize(
    "flag",
    [
        ["--repo", "acme/api"],
        ["--read-only", "acme/api"],
        ["--stdin"],
        ["--branch", "b"],
        ["--base", "main"],
    ],
)
def test_empty_flag_refuses_repo_flags(flag: list[str]) -> None:
    result = run(["create", "J-1", "--empty", *flag])
    assert result.exit == 2
    assert "--empty takes no repos" in err(result)
    assert StateWorkspaceStore().get("J-1") is None


def _plant(store_root: Path, ident: str, origin: str) -> None:
    """A store repo of ``ident`` that workspace has used, first fetched from ``origin``."""
    repo = store_root / f"{ident}.git"
    repo.parent.mkdir(parents=True, exist_ok=True)
    git(store_root, "init", "-q", "--bare", str(repo))
    git(repo, "config", "remote.origin.url", origin)
    (repo / "untaped-workspace.json").write_text('{"history": "complete"}\n')


def test_add_does_not_offer_repos_already_in_the_workspace(
    workspace_env: Path, store_root: Path, picker: Picker
) -> None:
    for name in ("api", "web"):
        _plant(store_root, f"github.com/acme/{name}", f"https://github.com/acme/{name}.git")
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="J-1", created_at=datetime(2026, 10, 1, tzinfo=UTC)))
    spec = RepoSpec(
        url="git@github.com:acme/api.git", name="acme/api", dir="api", branch="J-1", base="main"
    )
    store.add_repos("J-1", [spec])
    picker.terminal.picks(None)
    result = run(["add", "J-1"])
    assert result.exit == 1
    (request,) = picker.requests
    assert request.title_label == ""
    assert [item.id for item in request.catalog.items] == ["github.com/acme/web"]
    assert request.subtitle is not None
    assert request.subtitle("", {"branch": ""}).endswith("J-1")


def test_create_picker_prefills_the_name_and_flag_defaults(
    make_upstream: Callable[..., GitRemote],
    picker: Picker,
) -> None:
    picker.terminal.picks(_pick("J-1", make_upstream("api").url))
    result = run(["create", "J-1", "--base", "main", "--branch", "hotfix"])
    assert result.exit == 0, err(result)
    (request,) = picker.requests
    assert (request.title, request.title_label) == ("J-1", "name")
    assert {s.key: s.default for s in request.settings} == {
        "mode": "write",
        "base": "main",
        "branch": "hotfix",
    }


def test_read_only_pick_is_a_detached_checkout(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, picker: Picker
) -> None:
    url = make_upstream("api").url
    settings = {"mode": "read-only", "base": "", "branch": ""}
    pick = PickResult(
        title="J-1",
        defaults={"mode": "write", "base": "", "branch": ""},
        picks=(Picked(item=PickItem(id=url, label=url), settings=settings),),
    )
    picker.terminal.picks(pick)
    result = run(["create"])
    assert result.exit == 0, err(result)
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].read_only
    assert git(workspace_env / "J-1" / "api", "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_a_stored_only_pick_uses_its_stored_url(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, picker: Picker
) -> None:
    """No plugin lists repos: the stored-only repo comes from its own stored URL."""
    upstream = make_upstream("api")
    origin = "https://gitlab.example/acme/api.git"
    global_config(f"url.file://{upstream.path}.insteadOf", origin, add=True)
    # A store repo, made the way `create` makes one; its worktree is gone again.
    worktrees = LocalGitWorktrees(plugin="workspace")
    scratch = workspace_env.parent / "scratch"
    worktrees.checkout(origin, scratch, branch=None, base=None)
    worktrees.remove(origin, scratch, base="main", force=True)
    settings = {"mode": "write", "base": "", "branch": ""}
    item = PickItem(id="gitlab.example/acme/api", label="gitlab.example/acme/api")
    picker.terminal.picks(
        PickResult(title="J-1", defaults=settings, picks=(Picked(item=item, settings=settings),))
    )
    result = run(["create"])
    assert result.exit == 0, err(result)
    assert [i.id for i in picker.requests[0].catalog.items] == ["gitlab.example/acme/api"]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and record.repos[0].url == origin
    assert (workspace_env / "J-1" / "api" / "README.md").exists()


def test_a_pick_dropped_by_a_refresh_uses_its_remembered_repo(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, picker: Picker
) -> None:
    url = make_upstream("api").url
    github = listing(Repo(name="acme/api", url=url, default_branch="main"))
    with composed({"github": (github,)}):
        gather(RepoSource.repos)()  # github's answer is stored: the picker's first pass shows it
        github.rows = []  # and the refresh drops it
        picker.terminal.picks(_pick("J-1", "acme/api"))
        picker.before = lambda request: request.refresh and request.refresh(True)
        result = run(["create"])
    assert result.exit == 0, err(result)
    assert [i.id for i in picker.requests[0].catalog.items] == ["acme/api"]
    assert github.calls == 2
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [spec] = record.repos
    assert (spec.url, spec.source) == (url, Source(plugin="github", kind="workspace.repo"))


def test_an_owner_less_hosted_repo_is_offered(
    workspace_env: Path, store_root: Path, picker: Picker
) -> None:
    _plant(store_root, "git.example/project", "https://git.example/project.git")
    picker.terminal.picks(None)
    run(["create", "J-1"])
    (request,) = picker.requests
    assert [item.id for item in request.catalog.items] == ["git.example/project"]
