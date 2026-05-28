from pathlib import Path

import pytest
from conftest import StubManifests

from untaped_workspace.application import (
    SetWorkspaceBranch,
    ShowWorkspace,
    UnsetWorkspaceBranch,
)
from untaped_workspace.domain import ManifestDefaults, Repo, Workspace, WorkspaceManifest
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
