"""Unit tests for ``WorkspaceManifest.add_repo`` / ``remove_repo``.

These exercise the aggregate root's mutation methods directly. The
duplicate-rejection invariant lives on the model so use cases delegate
to it rather than re-checking themselves. The methods return new
manifests (copy-on-write) because all three manifest models are
``frozen=True``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from untaped_workspace.domain import (
    ManifestDefaults,
    Repo,
    WorkspaceManifest,
)


def _manifest(*repos: Repo, name: str | None = None) -> WorkspaceManifest:
    return WorkspaceManifest(name=name, repos=list(repos))


# ---- add_repo ----------------------------------------------------------


def test_add_repo_returns_new_manifest_with_repo_appended() -> None:
    manifest = _manifest(Repo(url="https://x/a.git"))
    new_manifest = manifest.add_repo(Repo(url="https://x/b.git"))
    assert [r.name for r in new_manifest.repos] == ["a", "b"]


def test_add_repo_does_not_mutate_original() -> None:
    """Copy-on-write: the original manifest's repos list must be untouched."""
    manifest = _manifest(Repo(url="https://x/a.git"))
    original_repos = manifest.repos
    manifest.add_repo(Repo(url="https://x/b.git"))
    assert manifest.repos is original_repos
    assert [r.name for r in manifest.repos] == ["a"]


def test_add_repo_preserves_name_and_defaults() -> None:
    """``name`` and ``defaults`` ride along through the copy."""
    manifest = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[Repo(url="https://x/a.git")],
    )
    new_manifest = manifest.add_repo(Repo(url="https://x/b.git"))
    assert new_manifest.name == "prod"
    assert new_manifest.defaults.branch == "main"


def test_add_repo_raises_on_name_collision() -> None:
    """The model validator runs on the new manifest — duplicate name → ValueError."""
    manifest = _manifest(Repo(url="https://x/a.git", name="alpha"))
    with pytest.raises(ValueError, match="duplicate"):
        manifest.add_repo(Repo(url="https://x/b.git", name="alpha"))


def test_add_repo_raises_on_url_collision() -> None:
    """Same URL twice is rejected even with different names."""
    manifest = _manifest(Repo(url="https://x/a.git", name="alpha"))
    with pytest.raises(ValueError, match="duplicate"):
        manifest.add_repo(Repo(url="https://x/a.git", name="beta"))


def test_add_repo_raises_on_derived_name_collision() -> None:
    """Two URLs that derive to the same name collide just as explicit names do."""
    manifest = _manifest(Repo(url="https://github.com/org/svc.git"))
    with pytest.raises(ValueError, match="duplicate"):
        manifest.add_repo(Repo(url="https://gitlab.com/team/svc.git"))


# ---- remove_repo -------------------------------------------------------


def test_remove_repo_by_name_returns_new_manifest_and_removed_repo() -> None:
    repo_a = Repo(url="https://x/a.git", name="alpha")
    repo_b = Repo(url="https://x/b.git", name="beta")
    manifest = _manifest(repo_a, repo_b)
    new_manifest, removed = manifest.remove_repo("alpha")
    assert removed is repo_a
    assert [r.name for r in new_manifest.repos] == ["beta"]


def test_remove_repo_by_url_returns_new_manifest_and_removed_repo() -> None:
    repo_a = Repo(url="https://x/a.git", name="alpha")
    repo_b = Repo(url="https://x/b.git", name="beta")
    manifest = _manifest(repo_a, repo_b)
    new_manifest, removed = manifest.remove_repo("https://x/a.git")
    assert removed is repo_a
    assert [r.name for r in new_manifest.repos] == ["beta"]


def test_remove_repo_does_not_mutate_original() -> None:
    """Copy-on-write: the original manifest's repos list must be untouched."""
    repo_a = Repo(url="https://x/a.git")
    manifest = _manifest(repo_a, Repo(url="https://x/b.git"))
    original_repos = manifest.repos
    manifest.remove_repo("a")
    assert manifest.repos is original_repos
    assert [r.name for r in manifest.repos] == ["a", "b"]


def test_remove_repo_preserves_name_and_defaults() -> None:
    manifest = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")],
    )
    new_manifest, _ = manifest.remove_repo("a")
    assert new_manifest.name == "prod"
    assert new_manifest.defaults.branch == "main"


def test_remove_repo_raises_for_unknown_ident() -> None:
    manifest = _manifest(Repo(url="https://x/a.git"))
    with pytest.raises(ValueError, match="nope"):
        manifest.remove_repo("nope")


# ---- frozen -----------------------------------------------------------


def test_repo_is_frozen() -> None:
    """``Repo`` is frozen — attribute reassignment must raise."""
    repo = Repo(url="https://x/a.git")
    with pytest.raises(ValidationError):
        repo.name = "rename"  # type: ignore[misc]


def test_manifest_defaults_is_frozen() -> None:
    defaults = ManifestDefaults(branch="main")
    with pytest.raises(ValidationError):
        defaults.branch = "develop"  # type: ignore[misc]


def test_workspace_manifest_is_frozen() -> None:
    manifest = _manifest()
    with pytest.raises(ValidationError):
        manifest.name = "renamed"  # type: ignore[misc]
