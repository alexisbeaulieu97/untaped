"""Stub-driven unit tests for ``application.WorkspaceResolver``.

Demonstrates the layering benefit of moving the resolver to the
application layer: precedence can be exercised with stub ports, no
``tmp_path`` registry / on-disk manifest required. The disk-touching
end-to-end tests live next door in ``test_workspace_resolver.py``.
"""

from pathlib import Path

import pytest
from conftest import StubManifests, StubRegistry, empty_manifest
from untaped_core import ConfigError
from untaped_workspace.application.workspace_resolver import WorkspaceResolver
from untaped_workspace.domain import Workspace, WorkspaceManifest


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
    resolver = _resolver([ws])

    found = resolver.resolve(name="prod")

    assert found is ws


def test_resolve_by_path_registered_returns_registry_entry() -> None:
    ws_path = Path("/ws/prod").resolve()
    registered = Workspace(name="prod", path=ws_path)
    resolver = _resolver([registered], {ws_path: empty_manifest()})

    found = resolver.resolve(path=ws_path)

    # Identity assertion (not just field equality) pins that the registry
    # entry is returned verbatim — a regression where `_workspace_for`
    # synthesises a fresh `Workspace` with matching fields instead of
    # returning the registered one would silently pass an equality check.
    assert found is registered


def test_resolve_by_path_unregistered_prefers_manifest_name() -> None:
    """Registry miss + manifest declares ``name: bar`` → manifest wins
    over the directory's basename. The dirname-derived fallback only
    runs when the manifest's ``name`` is empty.
    """
    ws_path = Path("/ws/lab").resolve()
    resolver = _resolver(manifests={ws_path: WorkspaceManifest(name="bar")})

    found = resolver.resolve(path=ws_path)

    assert found.name == "bar"
    assert found.path == ws_path


def test_resolve_by_path_unregistered_falls_back_to_dirname_when_manifest_has_no_name() -> None:
    """Manifest's ``name`` is ``None`` (e.g. hand-authored without
    ``name:``) → the dirname-derived fallback wins.
    """
    ws_path = Path("/ws/lab").resolve()
    resolver = _resolver(manifests={ws_path: empty_manifest()})

    found = resolver.resolve(path=ws_path)

    assert found.name == "lab"
    assert found.path == ws_path


def test_resolve_by_path_unregistered_falls_back_to_dirname_when_manifest_name_is_empty() -> None:
    """``WorkspaceManifest.name`` permits ``""`` (no
    ``min_length`` validator); the resolver's ``or`` fallback treats
    empty same as ``None`` and picks up the dirname. Pins the contract
    so a future model-level validator that promotes ``""`` to
    ``None`` (or rejects it outright) doesn't change resolver
    behaviour silently.
    """
    ws_path = Path("/ws/lab").resolve()
    resolver = _resolver(manifests={ws_path: WorkspaceManifest(name="")})

    found = resolver.resolve(path=ws_path)

    assert found.name == "lab"
    assert found.path == ws_path


def test_resolve_by_path_missing_manifest_raises() -> None:
    resolver = _resolver()

    with pytest.raises(ConfigError, match="no workspace manifest"):
        resolver.resolve(path=Path("/ws/empty"))


def test_resolve_from_cwd_walks_up_and_prefers_manifest_name() -> None:
    """cwd-walk path mirrors the same precedence as path-resolved: a
    declared ``manifest.name`` wins over the directory's basename.
    """
    ws_path = Path("/ws/prod").resolve()
    resolver = _resolver(manifests={ws_path: WorkspaceManifest(name="bar")})
    deep_cwd = ws_path / "src" / "deep"

    found = resolver.resolve(cwd=deep_cwd)

    assert found.path == ws_path
    assert found.name == "bar"


def test_resolve_from_cwd_walks_up_falls_back_to_dirname_when_manifest_has_no_name() -> None:
    ws_path = Path("/ws/prod").resolve()
    resolver = _resolver(manifests={ws_path: empty_manifest()})
    deep_cwd = ws_path / "src" / "deep"

    found = resolver.resolve(cwd=deep_cwd)

    assert found.path == ws_path
    assert found.name == "prod"


def test_resolve_from_cwd_outside_workspace_raises() -> None:
    resolver = _resolver()

    with pytest.raises(ConfigError, match="not inside a workspace"):
        resolver.resolve(cwd=Path("/elsewhere"))
