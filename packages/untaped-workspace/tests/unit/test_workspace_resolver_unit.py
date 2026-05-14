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
from untaped_workspace.domain import Workspace


def _resolver(
    *,
    registered: list[Workspace] | None = None,
    manifest_paths: list[Path] | None = None,
) -> WorkspaceResolver:
    manifests = StubManifests(
        {p: empty_manifest() for p in (manifest_paths or [])},
    )
    registry = StubRegistry(registered or [])
    return WorkspaceResolver(registry=registry, manifests=manifests)


def test_resolve_by_name_hits_registry() -> None:
    ws = Workspace(name="prod", path=Path("/ws/prod"))
    resolver = _resolver(registered=[ws])

    found = resolver.resolve(name="prod")

    assert found is ws


def test_resolve_by_path_registered_returns_registry_entry() -> None:
    ws_path = Path("/ws/prod").resolve()
    registered = Workspace(name="prod", path=ws_path)
    resolver = _resolver(registered=[registered], manifest_paths=[ws_path])

    found = resolver.resolve(path=ws_path)

    assert found.name == "prod"
    assert found.path == ws_path


def test_resolve_by_path_unregistered_synthesises_from_dirname() -> None:
    ws_path = Path("/ws/lab").resolve()
    resolver = _resolver(manifest_paths=[ws_path])

    found = resolver.resolve(path=ws_path)

    assert found.name == "lab"
    assert found.path == ws_path


def test_resolve_by_path_missing_manifest_raises() -> None:
    resolver = _resolver()

    with pytest.raises(ConfigError, match="no workspace manifest"):
        resolver.resolve(path=Path("/ws/empty"))


def test_resolve_from_cwd_walks_up_to_manifest() -> None:
    ws_path = Path("/ws/prod").resolve()
    resolver = _resolver(manifest_paths=[ws_path])
    deep_cwd = ws_path / "src" / "deep"

    found = resolver.resolve(cwd=deep_cwd)

    assert found.path == ws_path
    assert found.name == "prod"


def test_resolve_from_cwd_outside_workspace_raises() -> None:
    resolver = _resolver()

    with pytest.raises(ConfigError, match="not inside a workspace"):
        resolver.resolve(cwd=Path("/elsewhere"))


def test_resolve_does_not_touch_filesystem_directly() -> None:
    """A regression-pin: the resolver speaks only to its ports.

    If ``WorkspaceResolver`` ever reaches back into ``pathlib`` for
    ``is_file()`` / ``MANIFEST_FILENAME``, this stub-only resolution
    would still succeed (the stub says the manifest exists) — so the
    real check happens via :class:`StubManifests.exists`, not the
    filesystem.
    """
    ws_path = Path("/never/touches/disk").resolve()
    resolver = _resolver(manifest_paths=[ws_path])

    found = resolver.resolve(path=ws_path)

    assert found.path == ws_path
