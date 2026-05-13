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
    DuplicateRepoName,
    DuplicateRepoUrl,
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


def test_add_repo_raises_duplicate_repo_name_carrying_incumbent() -> None:
    """Collision on `name` raises `DuplicateRepoName` with the incumbent attached.

    The incumbent lets callers (`AddRepo`) build CLI messages without
    re-scanning the manifest.
    """
    incumbent = Repo(url="https://x/a.git", name="alpha")
    manifest = _manifest(incumbent)
    with pytest.raises(DuplicateRepoName) as exc_info:
        manifest.add_repo(Repo(url="https://x/b.git", name="alpha"))
    assert exc_info.value.existing is incumbent


def test_add_repo_raises_duplicate_repo_url_carrying_incumbent() -> None:
    """Same URL twice → ``DuplicateRepoUrl`` with the incumbent attached."""
    incumbent = Repo(url="https://x/a.git", name="alpha")
    manifest = _manifest(incumbent)
    with pytest.raises(DuplicateRepoUrl) as exc_info:
        manifest.add_repo(Repo(url="https://x/a.git", name="beta"))
    assert exc_info.value.existing is incumbent


def test_add_repo_raises_on_derived_name_collision() -> None:
    """Two URLs that derive to the same name collide just as explicit names do."""
    manifest = _manifest(Repo(url="https://github.com/org/svc.git"))
    with pytest.raises(DuplicateRepoName):
        manifest.add_repo(Repo(url="https://gitlab.com/team/svc.git"))


def test_duplicate_repo_exceptions_subclass_value_error() -> None:
    """``except ValueError`` keeps working for callers that don't care which kind."""
    assert issubclass(DuplicateRepoName, ValueError)
    assert issubclass(DuplicateRepoUrl, ValueError)


def test_duplicate_collision_precedence_url_before_name() -> None:
    """When both invariants would fire on the same input, ``add_repo`` and the
    YAML-load validator must raise the *same* typed exception. Url
    precedence keeps "re-add the same URL" surfacing as
    ``DuplicateRepoUrl`` ("already in workspace") rather than
    ``DuplicateRepoName`` — derived names also collide in that case but
    the user's correct mental model is the URL one."""
    incumbent = Repo(url="https://x/a.git", name="alpha")
    manifest = _manifest(incumbent)
    # Same name AND same url — both invariants violated simultaneously.
    colliding = Repo(url="https://x/a.git", name="alpha")

    with pytest.raises(DuplicateRepoUrl):
        manifest.add_repo(colliding)

    # YAML-load path: the validator wraps into ValidationError but the
    # wrapped cause must be the same type as the add_repo path raised.
    with pytest.raises(ValidationError) as exc_info:
        WorkspaceManifest(repos=[incumbent, colliding])
    causes = [err["ctx"]["error"] for err in exc_info.value.errors() if "ctx" in err]
    assert any(isinstance(cause, DuplicateRepoUrl) for cause in causes)


def test_duplicate_repo_exceptions_round_trip_through_pickle() -> None:
    """The typed exceptions cross process boundaries (foreach / sync workers).

    ``Exception.__reduce__`` defaults to pickling ``self.args``, which is
    a single message string for our subclasses — unpickling would call
    ``DuplicateRepoName(str)`` and crash on ``.name`` access. Our custom
    ``__reduce__`` round-trips via the incumbent.
    """
    import pickle

    incumbent = Repo(url="https://x/a.git", name="alpha")
    original = DuplicateRepoName(incumbent)
    restored = pickle.loads(pickle.dumps(original))
    assert isinstance(restored, DuplicateRepoName)
    assert restored.existing == incumbent
    assert str(restored) == str(original)


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
