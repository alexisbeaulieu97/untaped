"""Unit tests for the ``AddRepo`` use case."""

from pathlib import Path

import pytest

from untaped.capabilities.workspace.application import AddRepo
from untaped.capabilities.workspace.domain import Workspace, WorkspaceManifest
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capabilities.workspace.infrastructure import YamlManifestRepository

_SVC_A = "https://github.com/org/svc-a.git"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    YamlManifestRepository().write(tmp_path / "prod", WorkspaceManifest())
    return Workspace(name="prod", path=tmp_path / "prod")


def _repos(workspace: Workspace) -> list[tuple[str, str, str | None]]:
    return [(r.name, r.url, r.branch) for r in YamlManifestRepository().read(workspace.path).repos]


def test_add_repo_appends_with_optional_name_and_branch(workspace: Workspace) -> None:
    use_case = AddRepo(YamlManifestRepository())
    use_case(workspace, url=_SVC_A)
    use_case(workspace, url="https://x/b.git", repo_name="alpha", branch="develop")
    assert _repos(workspace) == [
        ("svc-a", _SVC_A, None),
        ("alpha", "https://x/b.git", "develop"),
    ]


def test_add_repo_rejects_same_url(workspace: Workspace) -> None:
    AddRepo(YamlManifestRepository())(workspace, url=_SVC_A)
    with pytest.raises(WorkspaceError, match="already in workspace"):
        AddRepo(YamlManifestRepository())(workspace, url=_SVC_A)
    assert len(_repos(workspace)) == 1


@pytest.mark.parametrize(
    ("kwargs", "suggests_flag"),
    [
        # A derived-name clash points at the disambiguation flag ...
        ({"url": "https://gitlab.com/team/svc-a.git"}, True),
        # ... an explicit one doesn't (the user already used it).
        ({"url": "https://github.com/team/other.git", "repo_name": "svc-a"}, False),
    ],
)
def test_add_repo_rejects_name_collision_without_touching_manifest(
    workspace: Workspace, kwargs: dict[str, str], suggests_flag: bool
) -> None:
    AddRepo(YamlManifestRepository())(workspace, url=_SVC_A)

    with pytest.raises(WorkspaceError, match="already in use") as exc_info:
        AddRepo(YamlManifestRepository())(workspace, **kwargs)

    assert ("--repo-name" in str(exc_info.value)) is suggests_flag
    assert _repos(workspace) == [("svc-a", _SVC_A, None)]


@pytest.mark.parametrize("repo_name", ["../escape", "/etc", ".", "C:\\x", "untaped.yml"])
def test_add_repo_rejects_unsafe_repo_name(workspace: Workspace, repo_name: str) -> None:
    with pytest.raises(WorkspaceError, match="repo name"):
        AddRepo(YamlManifestRepository())(workspace, url=_SVC_A, repo_name=repo_name)
    assert _repos(workspace) == []
