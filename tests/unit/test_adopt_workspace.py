"""Unit tests for the AdoptWorkspace use case."""

from pathlib import Path

import pytest
from untaped_workspace.application import AdoptWorkspace
from untaped_workspace.domain import DiscoveredRepo, Workspace
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import ManifestRepository


class _StubRegistry:
    def __init__(self) -> None:
        self.registered: list[Workspace] = []

    def register(self, *, name: str, path: Path) -> Workspace:
        ws = Workspace(name=name, path=path)
        self.registered.append(ws)
        return ws

    def find_by_path(self, path: Path) -> Workspace | None:
        for w in self.registered:
            if w.path == path:
                return w
        return None

    def entries(self) -> list[Workspace]:
        return list(self.registered)


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


def test_adopt_writes_manifest_with_discovered_repos(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    discoverer = _StubDiscoverer(
        [
            DiscoveredRepo(name="svc-a", url="https://x/svc-a.git", branch="main"),
            DiscoveredRepo(name="svc-b", url="https://x/svc-b.git", branch="develop"),
        ]
    )
    reg = _StubRegistry()

    result = AdoptWorkspace(ManifestRepository(), reg, discoverer)(ws_path, name="prod")

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


def test_adopt_derives_name_from_path(tmp_path: Path) -> None:
    ws_path = tmp_path / "lab"
    ws_path.mkdir()
    AdoptWorkspace(ManifestRepository(), _StubRegistry(), _StubDiscoverer([]))(ws_path)
    assert ManifestRepository().read(ws_path).name == "lab"


def test_adopt_with_empty_discovery_succeeds(tmp_path: Path) -> None:
    ws_path = tmp_path / "empty"
    ws_path.mkdir()
    reg = _StubRegistry()
    AdoptWorkspace(ManifestRepository(), reg, _StubDiscoverer([]))(ws_path)
    assert ManifestRepository().read(ws_path).repos == []
    assert reg.registered[0].path == ws_path.resolve()


def test_adopt_refuses_when_path_missing(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="does not exist"):
        AdoptWorkspace(ManifestRepository(), _StubRegistry(), _StubDiscoverer([]))(
            tmp_path / "ghost"
        )


def test_adopt_refuses_when_path_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("nope")
    with pytest.raises(WorkspaceError, match="not a directory"):
        AdoptWorkspace(ManifestRepository(), _StubRegistry(), _StubDiscoverer([]))(f)


def test_adopt_refuses_when_manifest_already_exists(tmp_path: Path) -> None:
    from untaped_workspace.domain import WorkspaceManifest

    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ManifestRepository().write(ws_path, WorkspaceManifest())

    with pytest.raises(WorkspaceError, match="already initialised"):
        AdoptWorkspace(ManifestRepository(), _StubRegistry(), _StubDiscoverer([]))(
            ws_path, name="prod"
        )


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

    AdoptWorkspace(ManifestRepository(), _StubRegistry(), discoverer, warn=warnings.append)(
        ws_path, name="lab"
    )

    assert warnings == [
        "b: no 'origin' remote — skipping",
        "c: symlink — skipping",
    ]


def test_adopt_refuses_when_path_already_registered(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    reg = _StubRegistry()
    reg.registered.append(Workspace(name="prod", path=ws_path.resolve()))

    with pytest.raises(WorkspaceError, match="already registered"):
        AdoptWorkspace(ManifestRepository(), reg, _StubDiscoverer([]))(ws_path, name="prod")
