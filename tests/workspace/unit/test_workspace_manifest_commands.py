from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.workspace.application import (
    ApplyWorkspaceBranch,
    SetWorkspaceBranch,
    ShowWorkspace,
    UnsetWorkspaceBranch,
)
from untaped.capabilities.workspace.domain import (
    ManifestDefaults,
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped.capabilities.workspace.errors import UnmatchedRepoFilterError, WorkspaceError
from workspace.conftest import StubFilesystem, StubGit, StubManifests


def test_show_workspace_returns_repo_detail_rows(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifest = WorkspaceManifest(
        defaults=ManifestDefaults(branch="main"),
        repos=[
            Repo(url="https://x/api.git", name="api"),
            Repo(url="https://x/ui.git", name="ui", branch="develop"),
        ],
    )

    rows = ShowWorkspace(StubManifests({workspace.path: manifest}))(workspace)

    assert [row.model_dump(mode="json") for row in rows] == [
        {
            "workspace": "prod",
            "path": str(workspace.path),
            "target_path": str(workspace.path / "api"),
            "default_branch": "main",
            "repo_count": 2,
            "repo": "api",
            "url": "https://x/api.git",
            "repo_branch": None,
            "target_branch": "main",
        },
        {
            "workspace": "prod",
            "path": str(workspace.path),
            "target_path": str(workspace.path / "ui"),
            "default_branch": "main",
            "repo_count": 2,
            "repo": "ui",
            "url": "https://x/ui.git",
            "repo_branch": "develop",
            "target_branch": "develop",
        },
    ]


def test_show_workspace_returns_summary_row_for_empty_manifest(tmp_path: Path) -> None:
    workspace = Workspace(name="empty", path=tmp_path / "empty")
    rows = ShowWorkspace(StubManifests({workspace.path: WorkspaceManifest()}))(workspace)

    assert [row.model_dump(mode="json") for row in rows] == [
        {
            "workspace": "empty",
            "path": str(workspace.path),
            "default_branch": None,
            "repo_count": 0,
            "repo": "",
            "url": "",
            "repo_branch": None,
            "target_branch": None,
        }
    ]


def test_set_and_unset_default_branch_writes_manifest(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests({workspace.path: WorkspaceManifest()})

    set_change = SetWorkspaceBranch(manifests)(workspace, branch="main")
    unset_change = UnsetWorkspaceBranch(manifests)(workspace)

    assert set_change.model_dump() == {
        "workspace": "prod",
        "repo": None,
        "branch": "main",
        "action": "updated",
    }
    assert unset_change.model_dump() == {
        "workspace": "prod",
        "repo": None,
        "branch": None,
        "action": "updated",
    }
    assert manifests.read(workspace.path).defaults.branch is None


def test_set_and_unset_repo_branch_writes_manifest(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests(
        {workspace.path: WorkspaceManifest(repos=[Repo(url="https://x/api.git", name="api")])}
    )

    set_change = SetWorkspaceBranch(manifests)(workspace, branch="develop", repo="api")
    unset_change = UnsetWorkspaceBranch(manifests)(workspace, repo="api")

    assert set_change.model_dump() == {
        "workspace": "prod",
        "repo": "api",
        "branch": "develop",
        "action": "updated",
    }
    assert unset_change.model_dump() == {
        "workspace": "prod",
        "repo": "api",
        "branch": None,
        "action": "updated",
    }
    assert manifests.read(workspace.path).repos[0].branch is None


@pytest.mark.parametrize(
    "call",
    [
        lambda m, ws: SetWorkspaceBranch(m)(ws, branch="main", repo="ghost"),
        lambda m, ws: UnsetWorkspaceBranch(m)(ws, repo="ghost"),
    ],
    ids=["set", "unset"],
)
def test_branch_change_errors_on_unknown_repo(tmp_path: Path, call: Any) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests({workspace.path: WorkspaceManifest()})

    with pytest.raises(WorkspaceError, match="repo 'ghost' not declared in workspace 'prod'"):
        call(manifests, workspace)


# ---- apply -------------------------------------------------------------------

_WS = Workspace(name="prod", path=Path("/ws/prod"))


def _apply(
    git: StubGit,
    *,
    repos: list[Repo] | None = None,
    default: str | None = "develop",
    cloned: bool = True,
    **kwargs: Any,
) -> list[Any]:
    repos = repos or [Repo(url="https://x/api.git", name="api")]
    manifest = WorkspaceManifest(defaults=ManifestDefaults(branch=default), repos=repos)
    dirs = [_WS.path / r.name / sub for r in repos for sub in ("", ".git")] if cloned else []
    use_case = ApplyWorkspaceBranch(
        StubManifests({_WS.path: manifest}), git, fs=StubFilesystem(dirs)
    )
    return use_case(_WS, **kwargs)


def test_apply_workspace_branch_checks_out_clean_repo_to_default_branch() -> None:
    git = StubGit(statuses={"api": RepoStatus(branch="main")})

    outcomes = _apply(git)

    assert [row.model_dump(mode="json") for row in outcomes] == [
        {
            "target_path": str(_WS.path / "api"),
            "action": "checked_out",
            "repo": "api",
            "workspace": "prod",
            "target_branch": "develop",
            "detail": "from main",
        }
    ]
    assert git.events.index(("fetch", "api")) < git.events.index(("checkout", "api", "develop"))


def test_apply_workspace_branch_repo_override_wins_over_default() -> None:
    git = StubGit(statuses={"api": RepoStatus(branch="main")})
    outcomes = _apply(git, default="main", repos=[Repo(url="https://x/api.git", branch="release")])
    assert (outcomes[0].target_branch, outcomes[0].action) == ("release", "checked_out")
    assert ("checkout", "api", "release") in git.events


@pytest.mark.parametrize(
    ("git", "kwargs", "action", "detail"),
    [
        (
            StubGit(statuses={"api": RepoStatus(branch="develop")}),
            {},
            "unchanged",
            "already on develop",
        ),
        (StubGit(), {"default": None}, "skipped", "no target branch"),
        (StubGit(), {"cloned": False}, "skipped", "not cloned"),
        (
            StubGit(statuses={"api": RepoStatus(branch="main", modified=1)}),
            {},
            "skipped",
            "dirty working tree",
        ),
        (
            StubGit(statuses={"api": RepoStatus(branch="main", ahead=1, behind=1)}),
            {},
            "skipped",
            "diverged from origin",
        ),
        (
            StubGit(missing_branches={"develop"}),
            {},
            "skipped",
            "branch not found locally or on origin",
        ),
        (StubGit(missing_branches={"develop"}), {"create": True}, "checked_out", "from main"),
        (
            StubGit(statuses={"api": RepoStatus(branch="main")}, checkout_fail=frozenset({"api"})),
            {},
            "failed",
            "checkout failed: checkout failed",
        ),
    ],
)
def test_apply_workspace_branch_outcomes(
    git: StubGit, kwargs: dict[str, Any], action: str, detail: str
) -> None:
    outcomes = _apply(git, **kwargs)
    assert (outcomes[0].action, outcomes[0].detail) == (action, detail)
    checked_out = any(e[0] == "checkout" for e in git.events)
    assert checked_out == (action != "unchanged" and action != "skipped")


def test_apply_workspace_branch_skips_dir_without_git_metadata() -> None:
    manifest = WorkspaceManifest(
        defaults=ManifestDefaults(branch="develop"), repos=[Repo(url="https://x/api.git")]
    )
    git = StubGit()
    use_case = ApplyWorkspaceBranch(
        StubManifests({_WS.path: manifest}), git, fs=StubFilesystem([_WS.path / "api"])
    )

    outcomes = use_case(_WS)

    assert (outcomes[0].action, outcomes[0].detail) == ("skipped", "not a git repository")
    assert git.events == []


@pytest.mark.parametrize(
    ("selector", "expected"),
    [("https://x/api.git", ["api"]), (["api", "ui"], ["api", "ui"])],
)
def test_apply_workspace_branch_filters_by_repo(
    selector: str | list[str], expected: list[str]
) -> None:
    git = StubGit()
    repos = [Repo(url=f"https://x/{n}.git") for n in ("api", "ui", "docs")]

    outcomes = _apply(git, repos=repos, repo=selector)

    assert [row.repo for row in outcomes] == expected
    assert [e[1] for e in git.events if e[0] == "checkout"] == expected


def test_apply_workspace_branch_errors_on_unknown_repo() -> None:
    with pytest.raises(UnmatchedRepoFilterError, match="ghost") as excinfo:
        _apply(StubGit(), repo="ghost")
    assert excinfo.value.unmatched == ("ghost",)
