import pytest
from pydantic import ValidationError
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    WorkspaceManifest,
    derive_repo_name,
)


def test_repo_name_derived_from_https_url() -> None:
    repo = Repo(url="https://github.com/org/svc-a.git")
    assert repo.name == "svc-a"


def test_repo_name_derived_from_ssh_url() -> None:
    repo = Repo(url="git@github.com:org/svc-bee.git")
    assert repo.name == "svc-bee"


def test_repo_name_explicit_overrides_derivation() -> None:
    repo = Repo(url="https://github.com/org/svc-a.git", name="custom")
    assert repo.name == "custom"


def test_repo_url_without_dot_git_suffix() -> None:
    repo = Repo(url="https://github.com/org/svc-a")
    assert repo.name == "svc-a"


def test_repo_branch_optional() -> None:
    repo = Repo(url="https://github.com/org/svc-a.git")
    assert repo.branch is None


def test_repo_rejects_empty_url() -> None:
    with pytest.raises(ValidationError):
        Repo(url="")
    with pytest.raises(ValidationError):
        Repo(url="   ")


def test_manifest_target_branch_per_repo_wins() -> None:
    m = WorkspaceManifest(
        defaults=ManifestDefaults(branch="main"),
        repos=[Repo(url="https://x/a.git", branch="develop")],
    )
    assert m.target_branch_for(m.repos[0]) == "develop"


def test_manifest_target_branch_falls_back_to_workspace_default() -> None:
    m = WorkspaceManifest(
        defaults=ManifestDefaults(branch="main"),
        repos=[Repo(url="https://x/a.git")],
    )
    assert m.target_branch_for(m.repos[0]) == "main"


def test_manifest_target_branch_none_when_no_default() -> None:
    m = WorkspaceManifest(repos=[Repo(url="https://x/a.git")])
    assert m.target_branch_for(m.repos[0]) is None


def test_manifest_find_by_name_or_url() -> None:
    m = WorkspaceManifest(
        repos=[
            Repo(url="https://github.com/org/svc-a.git", name="alpha"),
            Repo(url="https://github.com/org/svc-b.git"),
        ]
    )
    assert m.find_repo("alpha") is m.repos[0]
    assert m.find_repo("https://github.com/org/svc-a.git") is m.repos[0]
    assert m.find_repo("svc-b") is m.repos[1]
    assert m.find_repo("nonexistent") is None


def test_manifest_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        WorkspaceManifest.model_validate({"repos": [], "unknown_field": True})


def test_repo_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Repo.model_validate({"url": "https://x/a.git", "wat": 1})


def test_derive_repo_name_edge_cases() -> None:
    assert derive_repo_name("https://github.com/org/svc.git") == "svc"
    assert derive_repo_name("https://github.com/org/svc.git/") == "svc"
    assert derive_repo_name("git@github.com:org/svc.git") == "svc"
    assert derive_repo_name("file:///tmp/foo.git") == "foo"
