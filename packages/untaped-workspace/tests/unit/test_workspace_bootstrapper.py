"""Unit tests for the ``WorkspaceBootstrapper`` use case.

Pins the shared opening (canonicalise → derive name → manifest/registry
collision raises → mkdir → manifests.write → registry.register) that
``InitWorkspace`` / ``AdoptWorkspace`` / ``ImportWorkspace`` delegate
to. The per-use-case tests retain only their payload-specific
assertions.
"""

from pathlib import Path

import pytest
from conftest import StubFilesystem, StubManifests, StubRegistry
from untaped_workspace.application import WorkspaceBootstrapper
from untaped_workspace.domain import ManifestDefaults, Workspace, WorkspaceManifest
from untaped_workspace.errors import WorkspaceError


def _manifest(name: str = "ws") -> WorkspaceManifest:
    return WorkspaceManifest(name=name, defaults=ManifestDefaults())


def test_canonicalises_path_and_registers_at_resolved_path(tmp_path: Path) -> None:
    """``path`` is expanded + resolved before every check and side
    effect. We pass an unresolved path via ``tmp_path / 'ws/..'`` style
    and verify the registry sees the resolved form.
    """
    fs = StubFilesystem()
    manifests = StubManifests()
    registry = StubRegistry()
    boot = WorkspaceBootstrapper(manifests, registry, fs=fs)

    nested = tmp_path / "outer" / "inner" / ".." / "leaf"
    workspace = boot(nested, build_manifest=lambda n: _manifest(n))

    expected = nested.expanduser().resolve()
    assert workspace.path == expected
    assert registry.find_by_path(expected) is workspace


def test_derives_name_from_canonical_name_when_caller_omits_name(tmp_path: Path) -> None:
    fs = StubFilesystem()
    manifests = StubManifests()
    boot = WorkspaceBootstrapper(manifests, StubRegistry(), fs=fs)

    ws_dir = tmp_path / "from-dirname"
    seen: list[str] = []

    def _builder(n: str) -> WorkspaceManifest:
        seen.append(n)
        return _manifest(n)

    boot(ws_dir, build_manifest=_builder)

    assert seen == ["from-dirname"]
    assert manifests.read(ws_dir.resolve()).name == "from-dirname"


def test_raises_when_no_name_can_be_derived() -> None:
    boot = WorkspaceBootstrapper(StubManifests(), StubRegistry(), fs=StubFilesystem())
    with pytest.raises(WorkspaceError, match="unable to derive workspace name"):
        boot(Path("/"), build_manifest=lambda n: _manifest(n))


def test_raises_when_manifest_already_exists(tmp_path: Path) -> None:
    ws_dir = tmp_path / "prod"
    manifests = StubManifests({ws_dir.resolve(): _manifest("prod")})
    boot = WorkspaceBootstrapper(manifests, StubRegistry(), fs=StubFilesystem())

    with pytest.raises(WorkspaceError, match="already initialised"):
        boot(ws_dir, build_manifest=lambda n: _manifest(n), name="prod")


def test_raises_when_registry_already_has_path(tmp_path: Path) -> None:
    ws_dir = tmp_path / "prod"
    registry = StubRegistry()
    registry.registered.append(Workspace(name="other", path=ws_dir.resolve()))
    boot = WorkspaceBootstrapper(StubManifests(), registry, fs=StubFilesystem())

    with pytest.raises(WorkspaceError, match="already registered"):
        boot(ws_dir, build_manifest=lambda n: _manifest(n), name="prod")


def test_mkdir_called_with_parents_and_exist_ok(tmp_path: Path) -> None:
    """``Filesystem.mkdir`` declares both kwargs without defaults so a
    silent flip would surface here. Pin both flags."""
    fs = StubFilesystem()
    boot = WorkspaceBootstrapper(StubManifests(), StubRegistry(), fs=fs)

    ws_dir = tmp_path / "prod"
    boot(ws_dir, build_manifest=lambda n: _manifest(n))

    # StubFilesystem.mkdir raises if the path already exists with
    # exist_ok=False; landing in events with the canonical path is
    # enough to pin "exist_ok=True + canonical target".
    assert ("mkdir", ws_dir.resolve()) in fs.events


def test_writes_built_manifest_and_returns_registered_workspace(tmp_path: Path) -> None:
    """Happy path: ``build_manifest(ws_name)``'s return value gets
    written and the use case returns the ``Workspace`` produced by
    ``registry.register``."""
    fs = StubFilesystem()
    manifests = StubManifests()
    registry = StubRegistry()
    boot = WorkspaceBootstrapper(manifests, registry, fs=fs)

    ws_dir = tmp_path / "prod"
    built = WorkspaceManifest(name="prod", defaults=ManifestDefaults(branch="main"))
    result = boot(ws_dir, build_manifest=lambda _n: built, name="prod")

    assert result.name == "prod"
    assert result.path == ws_dir.resolve()
    assert manifests.read(ws_dir.resolve()) is built
    assert registry.registered[-1] is result


def test_explicit_name_overrides_canonical_dirname(tmp_path: Path) -> None:
    manifests = StubManifests()
    boot = WorkspaceBootstrapper(manifests, StubRegistry(), fs=StubFilesystem())

    ws_dir = tmp_path / "dirname"
    boot(ws_dir, build_manifest=lambda n: _manifest(n), name="override")

    assert manifests.read(ws_dir.resolve()).name == "override"


def test_verify_raises_for_each_collision_without_mutating(tmp_path: Path) -> None:
    """``verify`` reproduces ``__call__``'s read-only checks so callers
    with expensive pre-bootstrap work (e.g. ``AdoptWorkspace``'s
    git-subprocess discovery walk) can fail fast on collision.
    """
    ws_dir = tmp_path / "prod"

    # Collision on manifest.
    seeded_manifests = StubManifests({ws_dir.resolve(): _manifest("prod")})
    boot_m = WorkspaceBootstrapper(seeded_manifests, StubRegistry(), fs=StubFilesystem())
    with pytest.raises(WorkspaceError, match="already initialised"):
        boot_m.verify(ws_dir, name="prod")

    # Collision on registry.
    reg = StubRegistry()
    reg.registered.append(Workspace(name="other", path=ws_dir.resolve()))
    boot_r = WorkspaceBootstrapper(StubManifests(), reg, fs=StubFilesystem())
    with pytest.raises(WorkspaceError, match="already registered"):
        boot_r.verify(ws_dir, name="prod")

    # No name derivable.
    boot_n = WorkspaceBootstrapper(StubManifests(), StubRegistry(), fs=StubFilesystem())
    with pytest.raises(WorkspaceError, match="unable to derive workspace name"):
        boot_n.verify(Path("/"))

    # Happy path: no raise, no mutation observable via the stubs.
    fs = StubFilesystem()
    manifests = StubManifests()
    registry = StubRegistry()
    WorkspaceBootstrapper(manifests, registry, fs=fs).verify(tmp_path / "ok")
    assert fs.events == []
    assert registry.registered == []
