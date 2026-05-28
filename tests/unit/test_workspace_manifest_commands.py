from pathlib import Path

import pytest
from conftest import StubFilesystem, StubGit, StubManifests

from untaped_workspace.application import (
    ApplyWorkspaceBranch,
    SetWorkspaceBranch,
    ShowWorkspace,
    UnsetWorkspaceBranch,
)
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import WorkspaceError


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

    assert [row.model_dump() for row in rows] == [
        {
            "workspace": "prod",
            "path": str(workspace.path),
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

    assert [row.model_dump() for row in rows] == [
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

    assert set_change.model_dump() == {"workspace": "prod", "repo": None, "branch": "main"}
    assert unset_change.model_dump() == {"workspace": "prod", "repo": None, "branch": None}
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
    }
    assert unset_change.model_dump() == {"workspace": "prod", "repo": "api", "branch": None}
    assert manifests.read(workspace.path).repos[0].branch is None


def test_set_repo_branch_errors_on_unknown_repo(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests({workspace.path: WorkspaceManifest()})

    with pytest.raises(WorkspaceError, match="repo 'ghost' not declared in workspace 'prod'"):
        SetWorkspaceBranch(manifests)(workspace, branch="main", repo="ghost")


def test_unset_repo_branch_errors_on_unknown_repo(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests({workspace.path: WorkspaceManifest()})

    with pytest.raises(WorkspaceError, match="repo 'ghost' not declared in workspace 'prod'"):
        UnsetWorkspaceBranch(manifests)(workspace, repo="ghost")


def test_apply_workspace_branch_checks_out_clean_repo_to_default_branch(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[Repo(url="https://x/api.git", name="api")],
            )
        }
    )
    git = StubGit(statuses={"api": RepoStatus(branch="main")})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert [row.model_dump() for row in outcomes] == [
        {
            "repo": "api",
            "workspace": "prod",
            "target_branch": "develop",
            "action": "checkout",
            "detail": "from main",
        }
    ]
    assert ("fetch", "api") in git.events
    assert ("checkout", "api", "develop") in git.events


def test_apply_workspace_branch_reports_up_to_date_when_already_on_target(
    tmp_path: Path,
) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[Repo(url="https://x/api.git", name="api")],
            )
        }
    )
    git = StubGit(statuses={"api": RepoStatus(branch="develop")})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert outcomes[0].action == "up-to-date"
    assert outcomes[0].detail == "already on develop"
    assert ("checkout", "api", "develop") not in git.events


def test_apply_workspace_branch_repo_override_wins_over_default(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="main"),
                repos=[Repo(url="https://x/api.git", name="api", branch="release")],
            )
        }
    )
    git = StubGit(statuses={"api": RepoStatus(branch="main")})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert outcomes[0].target_branch == "release"
    assert outcomes[0].action == "checkout"
    assert ("checkout", "api", "release") in git.events


def test_apply_workspace_branch_skips_repo_without_target_branch(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {workspace.path: WorkspaceManifest(repos=[Repo(url="https://x/api.git", name="api")])}
    )
    git = StubGit(statuses={"api": RepoStatus(branch="main")})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "no target branch"
    assert git.events == []


def test_apply_workspace_branch_skips_missing_local_clone(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[Repo(url="https://x/api.git", name="api")],
            )
        }
    )
    git = StubGit(statuses={"api": RepoStatus(branch="main")})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem())(workspace)

    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "not cloned"
    assert git.events == []


def test_apply_workspace_branch_skips_dirty_repo(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[Repo(url="https://x/api.git", name="api")],
            )
        }
    )
    git = StubGit(statuses={"api": RepoStatus(branch="main", modified=1)})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "dirty working tree"
    assert ("fetch", "api") in git.events
    assert ("checkout", "api", "develop") not in git.events


def test_apply_workspace_branch_skips_diverged_repo(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[Repo(url="https://x/api.git", name="api")],
            )
        }
    )
    git = StubGit(statuses={"api": RepoStatus(branch="main", ahead=1, behind=1)})

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "diverged from origin"
    assert ("checkout", "api", "develop") not in git.events


def test_apply_workspace_branch_filters_by_repo_url(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    api = workspace.path / "api"
    ui = workspace.path / "ui"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[
                    Repo(url="https://x/api.git", name="api"),
                    Repo(url="https://x/ui.git", name="ui"),
                ],
            )
        }
    )
    git = StubGit(
        statuses={
            "api": RepoStatus(branch="main"),
            "ui": RepoStatus(branch="main"),
        }
    )

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([api, ui]))(
        workspace,
        repo="https://x/api.git",
    )

    assert [row.repo for row in outcomes] == ["api"]
    assert ("checkout", "api", "develop") in git.events
    assert ("checkout", "ui", "develop") not in git.events


def test_apply_workspace_branch_errors_on_unknown_repo(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    manifests = StubManifests({workspace.path: WorkspaceManifest()})

    with pytest.raises(WorkspaceError, match="repo 'ghost' not declared in workspace 'prod'"):
        ApplyWorkspaceBranch(manifests, StubGit(), fs=StubFilesystem())(workspace, repo="ghost")


def test_apply_workspace_branch_checkout_failure_returns_skip(tmp_path: Path) -> None:
    workspace = Workspace(name="prod", path=tmp_path / "prod")
    local = workspace.path / "api"
    manifests = StubManifests(
        {
            workspace.path: WorkspaceManifest(
                defaults=ManifestDefaults(branch="develop"),
                repos=[Repo(url="https://x/api.git", name="api")],
            )
        }
    )
    git = StubGit(
        statuses={"api": RepoStatus(branch="main")},
        checkout_fail=frozenset({"api"}),
    )

    outcomes = ApplyWorkspaceBranch(manifests, git, fs=StubFilesystem([local]))(workspace)

    assert outcomes[0].action == "skip"
    assert outcomes[0].detail == "checkout failed: checkout failed"
