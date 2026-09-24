"""``WorkspaceResolver`` precedence: registry name, then path, then cwd walk.

Precedence is exercised with stub ports; one end-to-end test runs the real
registry and YAML manifest adapters.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.capabilities.workspace.application import WorkspaceResolver
from untaped.capabilities.workspace.domain import Workspace, WorkspaceManifest
from untaped.capabilities.workspace.infrastructure import (
    WorkspaceRegistryRepository,
    YamlManifestRepository,
)
from untaped.capability_api import ConfigError
from untaped.settings import get_settings
from workspace.conftest import StubManifests, StubRegistry

_WS = Path("/ws/lab").resolve()


def _resolver(
    registered: list[Workspace] | None = None,
    manifests: dict[Path, WorkspaceManifest] | None = None,
) -> WorkspaceResolver:
    return WorkspaceResolver(
        registry=StubRegistry(registered or []),
        manifests=StubManifests(manifests or {}),
    )


def test_resolve_by_name_hits_registry() -> None:
    ws = Workspace(name="prod", path=Path("/ws/prod"))
    assert _resolver([ws]).resolve(name="prod") is ws


def test_resolve_by_path_registered_returns_registry_entry() -> None:
    registered = Workspace(name="prod", path=_WS)
    found = _resolver([registered], {_WS: WorkspaceManifest(name="other")}).resolve(path=_WS)
    assert found is registered


@pytest.mark.parametrize("via", ["path", "cwd"])
@pytest.mark.parametrize(
    ("manifest_name", "expected"),
    # A declared manifest name wins over the dirname; ``None`` and ``""``
    # both fall back to it.
    [("bar", "bar"), (None, "lab"), ("", "lab")],
)
def test_unregistered_workspace_name_precedence(
    via: str, manifest_name: str | None, expected: str
) -> None:
    resolver = _resolver(manifests={_WS: WorkspaceManifest(name=manifest_name)})
    if via == "path":
        found = resolver.resolve(path=_WS)
    else:
        found = resolver.resolve(cwd=_WS / "src" / "deep")
    assert (found.name, found.path) == (expected, _WS)


def test_resolve_by_path_missing_manifest_raises() -> None:
    with pytest.raises(ConfigError, match="no workspace manifest"):
        _resolver().resolve(path=Path("/ws/empty"))


def test_resolve_from_cwd_outside_workspace_raises() -> None:
    with pytest.raises(ConfigError, match="not inside a workspace"):
        _resolver().resolve(cwd=Path("/elsewhere"))


@pytest.fixture
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("_isolate")
def test_real_adapters_resolve_by_name_path_and_cwd(tmp_path: Path) -> None:
    registry = WorkspaceRegistryRepository()
    manifests = YamlManifestRepository()
    resolver = WorkspaceResolver(registry=registry, manifests=manifests)
    prod = tmp_path / "prod"
    manifests.write(prod, WorkspaceManifest())
    registry.register(name="prod", path=prod)
    alien = tmp_path / "foo"
    manifests.write(alien, WorkspaceManifest(name="bar"))
    (prod / "src").mkdir()

    assert resolver.resolve(name="prod").path == prod.resolve()
    assert resolver.resolve(path=prod).name == "prod"
    assert resolver.resolve(cwd=prod / "src").name == "prod"
    assert resolver.resolve(path=alien).name == "bar"
