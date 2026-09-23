"""Unit tests for the ForgetWorkspace use case."""

from pathlib import Path

import pytest

from untaped.capabilities.workspace.application import ForgetWorkspace
from untaped.capabilities.workspace.domain import Workspace
from untaped.capabilities.workspace.errors import GitError, RegistryError, WorkspaceError
from untaped.capabilities.workspace.infrastructure import LocalFilesystem, YamlManifestRepository
from workspace.conftest import StubRegistry


class _PruneSafety:
    def __init__(
        self,
        blockers: dict[Path, tuple[str, ...]] | None = None,
        failures: set[Path] | None = None,
    ) -> None:
        self._blockers = blockers or {}
        self._failures = failures or set()
        self.calls: list[Path] = []

    def prune_blockers(self, repo_path: Path) -> tuple[str, ...]:
        self.calls.append(repo_path)
        if repo_path in self._failures:
            raise GitError("git status failed: not a git repository")
        return self._blockers.get(repo_path, ())


def _seed_manifest(ws_path: Path, *, repos: list[tuple[str, str]] = ()) -> None:
    """Write a workspace manifest with the given (name, url) repos."""
    from untaped.capabilities.workspace.domain import Repo, WorkspaceManifest

    manifest = WorkspaceManifest(
        name=ws_path.name,
        repos=[Repo(url=url, name=name) for name, url in repos],
    )
    YamlManifestRepository().write(ws_path, manifest)


def test_forget_removes_registry_entry(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ws = Workspace(name="prod", path=ws_path)
    reg = StubRegistry([ws])

    use_case = ForgetWorkspace(
        reg,
        YamlManifestRepository(),
        fs=LocalFilesystem(),
        prune_safety=_PruneSafety(),
    )

    result = use_case("prod")

    assert result.name == "prod"
    assert reg.registered == []
    assert reg.unregistered == ["prod"]
    assert ws_path.is_dir()  # files preserved


def test_forget_unknown_workspace_raises(tmp_path: Path) -> None:
    reg = StubRegistry([])
    with pytest.raises(RegistryError, match="unknown workspace"):
        ForgetWorkspace(
            reg,
            YamlManifestRepository(),
            fs=LocalFilesystem(),
            prune_safety=_PruneSafety(),
        )("ghost")


def test_forget_with_prune_deletes_managed_workspace_dir(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    clone = ws_path / "svc-a"
    (clone / ".git").mkdir(parents=True)
    (clone / "src.py").write_text("x")
    orphan = ws_path / "old"
    (orphan / ".git").mkdir(parents=True)
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    warnings: list[str] = []

    ForgetWorkspace(
        reg,
        YamlManifestRepository(),
        fs=LocalFilesystem(),
        prune_safety=_PruneSafety(),
        warn=warnings.append,
    )("prod", prune=True)

    assert not ws_path.exists()
    assert reg.registered == []
    assert warnings == []


def test_forget_prune_keeps_unmanaged_files_and_warns(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "notes.txt").write_text("mine")
    (ws_path / "scratch-dir").mkdir()
    clone = ws_path / "svc-a"
    (clone / ".git").mkdir(parents=True)
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    warnings: list[str] = []

    ForgetWorkspace(
        reg,
        YamlManifestRepository(),
        fs=LocalFilesystem(),
        prune_safety=_PruneSafety(),
        warn=warnings.append,
    )("prod", prune=True)

    assert (ws_path / "notes.txt").read_text() == "mine"
    assert (ws_path / "scratch-dir").is_dir()
    assert not clone.exists()
    assert not (ws_path / "untaped.yml").exists()
    assert reg.registered == []
    assert len(warnings) == 1
    assert str(ws_path) in warnings[0]
    assert "notes.txt" in warnings[0] and "scratch-dir" in warnings[0]


def test_forget_prune_refuses_dirty_repo(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    repo_dir = ws_path / "svc-a"
    (repo_dir / ".git").mkdir(parents=True)
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    status = _PruneSafety(blockers={repo_dir: ("dirty working tree",)})

    with pytest.raises(WorkspaceError, match="unsafe local state"):
        ForgetWorkspace(reg, YamlManifestRepository(), fs=LocalFilesystem(), prune_safety=status)(
            "prod", prune=True
        )

    assert ws_path.is_dir()  # untouched
    assert reg.registered  # registry untouched


def test_forget_prune_succeeds_when_path_missing(tmp_path: Path) -> None:
    ws_path = tmp_path / "ghost"  # never created
    reg = StubRegistry([Workspace(name="ghost", path=ws_path)])

    ForgetWorkspace(
        reg, YamlManifestRepository(), fs=LocalFilesystem(), prune_safety=_PruneSafety()
    )("ghost", prune=True)

    assert reg.registered == []


def test_forget_succeeds_when_manifest_missing(tmp_path: Path) -> None:
    """Without ``--prune``, a missing manifest is tolerated — the registry
    entry is removed regardless. Files on disk are preserved.
    """
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "stranded.txt").write_text("no manifest here")
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    ForgetWorkspace(
        reg, YamlManifestRepository(), fs=LocalFilesystem(), prune_safety=_PruneSafety()
    )("prod")

    assert reg.registered == []
    assert ws_path.is_dir()  # preserved


def test_forget_prune_refuses_when_manifest_missing(tmp_path: Path) -> None:
    """With ``--prune``, refuse to rmtree if we can't read the manifest —
    we have no way to verify cleanliness, so the user is asked to delete
    the directory manually.
    """
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "stranded.txt").write_text("no manifest here")
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    with pytest.raises(WorkspaceError, match="no manifest"):
        ForgetWorkspace(
            reg,
            YamlManifestRepository(),
            fs=LocalFilesystem(),
            prune_safety=_PruneSafety(),
        )("prod", prune=True)

    assert ws_path.is_dir()  # untouched
    assert reg.registered  # registry untouched


def test_forget_prune_leaves_declared_dir_that_is_not_a_clone(tmp_path: Path) -> None:
    """A declared directory without ``.git`` is never inspected with git
    (that would fall through to an enclosing repo) nor deleted: it is
    left in place and reported."""
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "svc-a").mkdir()  # exists but no .git
    (ws_path / "svc-a" / "work.txt").write_text("keep")
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    status = _PruneSafety()
    warnings: list[str] = []

    ForgetWorkspace(
        reg,
        YamlManifestRepository(),
        fs=LocalFilesystem(),
        prune_safety=status,
        warn=warnings.append,
    )("prod", prune=True)

    assert (ws_path / "svc-a" / "work.txt").read_text() == "keep"
    assert status.calls == []
    assert "svc-a" in warnings[0]


def test_forget_prune_refuses_when_clone_cannot_be_inspected(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "svc-a" / ".git").mkdir(parents=True)
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    with pytest.raises(WorkspaceError, match="cannot inspect 'svc-a'"):
        ForgetWorkspace(
            reg,
            YamlManifestRepository(),
            fs=LocalFilesystem(),
            prune_safety=_PruneSafety(failures={ws_path / "svc-a"}),
        )("prod", prune=True)

    assert (ws_path / "svc-a").is_dir()  # untouched
    assert reg.registered  # registry untouched


def test_forget_prune_refuses_unsafe_undeclared_clone(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    orphan = ws_path / "scratch"
    orphan.mkdir()
    (orphan / ".git").mkdir()
    _seed_manifest(ws_path)
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    status = _PruneSafety(
        blockers={orphan: ("local commits not reachable from any remote-tracking ref",)}
    )

    with pytest.raises(WorkspaceError, match="scratch"):
        ForgetWorkspace(reg, YamlManifestRepository(), fs=LocalFilesystem(), prune_safety=status)(
            "prod", prune=True
        )

    assert ws_path.is_dir()
    assert orphan.is_dir()
    assert reg.registered


def test_forget_prune_skips_symlinked_child_clones(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    declared_target = tmp_path / "declared-target"
    declared_target.mkdir()
    (declared_target / ".git").mkdir()
    orphan_target = tmp_path / "orphan-target"
    orphan_target.mkdir()
    (orphan_target / ".git").mkdir()
    declared_link = ws_path / "svc-a"
    declared_link.symlink_to(declared_target, target_is_directory=True)
    orphan_link = ws_path / "scratch"
    orphan_link.symlink_to(orphan_target, target_is_directory=True)
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    status = _PruneSafety(
        blockers={
            declared_link: ("local commits not reachable from any remote-tracking ref",),
            orphan_link: ("local commits not reachable from any remote-tracking ref",),
        }
    )

    ForgetWorkspace(reg, YamlManifestRepository(), fs=LocalFilesystem(), prune_safety=status)(
        "prod", prune=True
    )

    assert not ws_path.exists()
    assert declared_target.is_dir()
    assert orphan_target.is_dir()
    assert status.calls == []
    assert reg.registered == []
