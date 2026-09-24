"""Unit tests for the ``WorkspaceManifest`` aggregate and its ``Repo`` entries."""

import pickle

import pytest
from pydantic import ValidationError

from untaped.capabilities.workspace.domain import (
    DuplicateRepoNameError,
    DuplicateRepoUrlError,
    ManifestDefaults,
    Repo,
    WorkspaceManifest,
    derive_repo_name,
)


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/org/svc.git", "svc"),
        ("https://github.com/org/svc.git/", "svc"),
        ("https://github.com/org/svc", "svc"),
        ("git@github.com:org/svc.git", "svc"),
        ("file:///tmp/foo.git", "foo"),
        ("C:\\repos\\svc-a.git", "svc-a"),
        ("C:/repos/svc-b", "svc-b"),
        ("\\\\server\\share\\svc-c.git", "svc-c"),
        ("git@github.com:org\\svc-d.git", "svc-d"),
    ],
)
def test_derive_repo_name(url: str, name: str) -> None:
    assert derive_repo_name(url) == name
    assert Repo(url=url).name == name


def test_repo_explicit_name_overrides_derivation() -> None:
    assert Repo(url="https://github.com/org/svc-a.git", name="custom").name == "custom"


@pytest.mark.parametrize("url", ["", "   "])
def test_repo_rejects_empty_url(url: str) -> None:
    with pytest.raises(ValidationError, match="repo url cannot be empty"):
        Repo(url=url)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (WorkspaceManifest, {"repos": [], "unknown_field": True}),
        (Repo, {"url": "https://x/a.git", "wat": 1}),
    ],
)
def test_models_reject_extra_fields(
    model: type[Repo | WorkspaceManifest], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("repo_branch", "default_branch", "expected"),
    [("develop", "main", "develop"), (None, "main", "main"), (None, None, None)],
)
def test_target_branch_cascade(
    repo_branch: str | None, default_branch: str | None, expected: str | None
) -> None:
    m = WorkspaceManifest(
        defaults=ManifestDefaults(branch=default_branch),
        repos=[Repo(url="https://x/a.git", branch=repo_branch)],
    )
    assert m.target_branch_for(m.repos[0]) == expected


def test_manifest_with_default_branch_sets_and_unsets() -> None:
    original = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[Repo(url="https://x/api.git")],
    )

    updated = original.with_default_branch("develop")
    cleared = updated.with_default_branch(None)

    assert updated.defaults.branch == "develop"
    assert updated.repos == original.repos
    assert cleared.defaults.branch is None
    assert cleared.repos == original.repos
    assert original.defaults.branch == "main"


def test_manifest_with_repo_branch_sets_and_unsets_by_name_preserving_order() -> None:
    original = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[
            Repo(url="https://x/api.git", name="api"),
            Repo(url="https://x/ui.git", name="ui", branch="release"),
        ],
    )

    updated, repo = original.with_repo_branch("api", "develop")
    cleared, cleared_repo = updated.with_repo_branch("api", None)

    assert repo.name == "api"
    assert updated.repos[0].branch == "develop"
    assert updated.repos[1] == original.repos[1]
    assert [r.name for r in updated.repos] == ["api", "ui"]
    assert cleared_repo.name == "api"
    assert cleared.repos[0].branch is None
    assert original.repos[0].branch is None


def test_manifest_with_repo_branch_accepts_url_and_errors_on_unknown_repo() -> None:
    original = WorkspaceManifest(repos=[Repo(url="https://x/api.git", name="api")])

    updated, repo = original.with_repo_branch("https://x/api.git", "main")

    assert repo.name == "api"
    assert updated.repos[0].branch == "main"
    with pytest.raises(ValueError, match="no repo matches 'ghost'"):
        original.with_repo_branch("ghost", "main")


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


# ---- duplicate-repo invariants ---------------------------------------------


@pytest.mark.parametrize(
    ("repos", "match"),
    [
        # Explicit names colliding would map two repos to one working tree.
        ([("https://x/a.git", "alpha"), ("https://x/b.git", "alpha")], "duplicate repo name"),
        # Derived names collide on disk too, even without explicit ``name``.
        (
            [("https://github.com/org/svc.git", None), ("https://gitlab.com/team/svc.git", None)],
            "duplicate repo name",
        ),
        # Names differing only by case collide on case-insensitive filesystems.
        (
            [("https://github.com/a/api.git", "api"), ("https://github.com/b/api.git", "API")],
            "duplicate repo name",
        ),
        ([("https://x/a.git", "alpha"), ("https://x/a.git", "beta")], "duplicate repo url"),
    ],
)
def test_manifest_rejects_duplicate_repos(repos: list[tuple[str, str | None]], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        WorkspaceManifest(repos=[Repo(url=url, name=name) for url, name in repos])


def test_add_repo_appends_and_keeps_name_and_defaults() -> None:
    manifest = WorkspaceManifest(
        name="prod", defaults=ManifestDefaults(branch="main"), repos=[Repo(url="https://x/a.git")]
    )
    new_manifest = manifest.add_repo(Repo(url="https://x/b.git"))
    assert [r.name for r in new_manifest.repos] == ["a", "b"]
    assert (new_manifest.name, new_manifest.defaults) == ("prod", manifest.defaults)
    assert [r.name for r in manifest.repos] == ["a"]


@pytest.mark.parametrize(
    ("new", "error"),
    [
        (Repo(url="https://x/b.git", name="alpha"), DuplicateRepoNameError),
        (Repo(url="https://x/a.git", name="beta"), DuplicateRepoUrlError),
    ],
)
def test_add_repo_duplicate_carries_incumbent(
    new: Repo, error: type[DuplicateRepoNameError | DuplicateRepoUrlError]
) -> None:
    """The incumbent lets ``AddRepo`` build CLI messages without re-scanning."""
    incumbent = Repo(url="https://x/a.git", name="alpha")
    with pytest.raises(error) as exc_info:
        WorkspaceManifest(repos=[incumbent]).add_repo(new)
    assert exc_info.value.existing is incumbent


def test_duplicate_collision_precedence_url_before_name() -> None:
    """When both invariants fire, ``add_repo`` and the YAML-load validator raise
    the *same* typed exception: re-adding a URL reads as "already in
    workspace", not as a name clash."""
    incumbent = Repo(url="https://x/a.git", name="alpha")
    colliding = Repo(url="https://x/a.git", name="alpha")

    with pytest.raises(DuplicateRepoUrlError):
        WorkspaceManifest(repos=[incumbent]).add_repo(colliding)
    with pytest.raises(ValidationError) as exc_info:
        WorkspaceManifest(repos=[incumbent, colliding])
    causes = [err["ctx"]["error"] for err in exc_info.value.errors() if "ctx" in err]
    assert any(isinstance(cause, DuplicateRepoUrlError) for cause in causes)


def test_duplicate_repo_exceptions_round_trip_through_pickle() -> None:
    """The typed exceptions cross process boundaries (foreach / sync workers);
    the custom ``__reduce__`` round-trips via the incumbent."""
    incumbent = Repo(url="https://x/a.git", name="alpha")
    original = DuplicateRepoNameError(incumbent)
    restored = pickle.loads(pickle.dumps(original))
    assert isinstance(restored, DuplicateRepoNameError)
    assert restored.existing == incumbent
    assert str(restored) == str(original)


@pytest.mark.parametrize("ident", ["alpha", "https://x/a.git"])
def test_remove_repo_by_name_or_url(ident: str) -> None:
    repo_a = Repo(url="https://x/a.git", name="alpha")
    manifest = WorkspaceManifest(
        name="prod",
        defaults=ManifestDefaults(branch="main"),
        repos=[repo_a, Repo(url="https://x/b.git", name="beta")],
    )
    new_manifest, removed = manifest.remove_repo(ident)
    assert removed is repo_a
    assert [r.name for r in new_manifest.repos] == ["beta"]
    assert (new_manifest.name, new_manifest.defaults) == ("prod", manifest.defaults)
    assert [r.name for r in manifest.repos] == ["alpha", "beta"]


def test_remove_repo_raises_for_unknown_ident() -> None:
    with pytest.raises(ValueError, match="no repo matches 'nope'"):
        WorkspaceManifest(repos=[Repo(url="https://x/a.git")]).remove_repo("nope")


def test_manifest_repos_are_an_immutable_tuple_in_order() -> None:
    m = WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")])
    assert isinstance(m.repos, tuple)
    assert [r.name for r in m.repos] == ["a", "b"]


# ---- repo names are single safe path segments -------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "..",
        ".",
        "../escape",
        "a/b",
        "/etc",
        "a\\b",
        "C:\\evil",
        "c:",
        "nul\0byte",
        "untaped.yml",
        "UNTAPED.YML",
    ],
)
def test_repo_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValidationError, match="repo name"):
        Repo(url="https://github.com/org/svc-a.git", name=name)


@pytest.mark.parametrize("url", ["https://evil.example/org/..", "https://evil.example/"])
def test_repo_rejects_urls_deriving_unsafe_names(url: str) -> None:
    with pytest.raises(ValidationError, match="repo name"):
        Repo(url=url)
