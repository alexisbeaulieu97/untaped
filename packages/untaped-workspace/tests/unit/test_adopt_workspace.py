"""Unit tests for the AdoptWorkspace use case.

Collision raises and name-derivation invariants live on
``test_workspace_bootstrapper.py``. This file pins what ``AdoptWorkspace``
itself owns: the ``path.exists`` / ``path.is_dir`` preconditions, the
discoverer-then-warn ordering, and the discovered-repos → manifest
plumbing.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import StubRegistry
from untaped_workspace.application import AdoptWorkspace, WorkspaceBootstrapper
from untaped_workspace.domain import DiscoveredRepo
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


class _StubResult:
    def __init__(self, repos: list[DiscoveredRepo], skipped: list[str] | None = None) -> None:
        self.repos = repos
        self.skipped = skipped or []


class _StubDiscoverer:
    def __init__(self, repos: list[DiscoveredRepo], *, skipped: list[str] | None = None) -> None:
        self._result = _StubResult(repos, skipped)
        self.calls: list[Path] = []

    def discover(self, path: Path) -> _StubResult:
        self.calls.append(path)
        return self._result


def _adopt(
    repo: ManifestRepository,
    reg: StubRegistry,
    discoverer: _StubDiscoverer,
    *,
    warn: Callable[[str], None] | None = None,
) -> AdoptWorkspace:
    boot = WorkspaceBootstrapper(repo, reg, fs=_FS)
    if warn is None:
        return AdoptWorkspace(boot, discoverer, fs=_FS)
    return AdoptWorkspace(boot, discoverer, fs=_FS, warn=warn)


def test_adopt_writes_manifest_with_discovered_repos(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    discoverer = _StubDiscoverer(
        [
            DiscoveredRepo(name="svc-a", url="https://x/svc-a.git", branch="main"),
            DiscoveredRepo(name="svc-b", url="https://x/svc-b.git", branch="develop"),
        ]
    )
    reg = StubRegistry()

    result = _adopt(ManifestRepository(), reg, discoverer)(ws_path, name="prod")

    assert result.workspace.name == "prod"
    assert [r.name for r in result.repos] == ["svc-a", "svc-b"]
    manifest = ManifestRepository().read(ws_path)
    assert manifest.name == "prod"
    assert manifest.defaults.branch is None
    assert [(r.name, r.url, r.branch) for r in manifest.repos] == [
        ("svc-a", "https://x/svc-a.git", "main"),
        ("svc-b", "https://x/svc-b.git", "develop"),
    ]
    assert reg.registered[0].name == "prod"
    assert discoverer.calls == [ws_path.resolve()]


def test_adopt_with_empty_discovery_succeeds(tmp_path: Path) -> None:
    ws_path = tmp_path / "empty"
    ws_path.mkdir()
    reg = StubRegistry()
    _adopt(ManifestRepository(), reg, _StubDiscoverer([]))(ws_path)
    assert ManifestRepository().read(ws_path).repos == []
    assert reg.registered[0].path == ws_path.resolve()


def test_adopt_refuses_when_path_missing(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="does not exist"):
        _adopt(ManifestRepository(), StubRegistry(), _StubDiscoverer([]))(tmp_path / "ghost")


def test_adopt_refuses_when_path_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("nope")
    with pytest.raises(WorkspaceError, match="not a directory"):
        _adopt(ManifestRepository(), StubRegistry(), _StubDiscoverer([]))(f)


def test_adopt_short_circuits_on_collision_without_invoking_discoverer(
    tmp_path: Path,
) -> None:
    """`AdoptWorkspace` calls `bootstrapper.verify()` *before* the
    discoverer runs. Re-running `adopt` on an already-initialised
    workspace should raise without N x 2 `git` subprocess spawns.
    """
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    from untaped_workspace.domain import WorkspaceManifest

    repo = ManifestRepository()
    repo.write(ws_path, WorkspaceManifest())  # seed collision
    discoverer = _StubDiscoverer([])

    with pytest.raises(WorkspaceError, match="already initialised"):
        _adopt(repo, StubRegistry(), discoverer)(ws_path, name="prod")

    assert discoverer.calls == []  # the expensive walk never happened


def test_adopt_forwards_skipped_reasons_to_warn(tmp_path: Path) -> None:
    """The simplify pass moved ``warn`` from infrastructure (the discoverer)
    up to application (``AdoptWorkspace``). Verify each skipped reason
    surfaced by the discoverer is forwarded to the injected callback.
    """
    ws_path = tmp_path / "lab"
    ws_path.mkdir()
    warnings: list[str] = []
    discoverer = _StubDiscoverer(
        [],
        skipped=["b: no 'origin' remote — skipping", "c: symlink — skipping"],
    )

    _adopt(ManifestRepository(), StubRegistry(), discoverer, warn=warnings.append)(
        ws_path, name="lab"
    )

    assert warnings == [
        "b: no 'origin' remote — skipping",
        "c: symlink — skipping",
    ]
