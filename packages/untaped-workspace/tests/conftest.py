"""Shared test scaffolding for the workspace use cases.

This module is reachable by sibling test files via ``from conftest import
StubGit, StubRegistry`` because the root ``pyproject.toml`` adds
``packages/untaped-workspace/tests`` to ``[tool.pytest.ini_options]
pythonpath``. Without that entry, pytest's ``--import-mode=importlib``
hides per-package conftest modules from runtime import.

**Global-namespace caveat.** Adding the workspace tests dir to
``pythonpath`` claims the unqualified module name ``conftest`` for the
whole pytest session. Do not add a second per-package tests dir to
``pythonpath`` — pick a unique module name (e.g.
``packages/untaped-<x>/tests/_<x>_stubs.py``) and import that instead.
Other packages can still ship ``tests/conftest.py`` files for pytest's
auto-discovery; only the ``from conftest import …`` runtime pattern is
exclusive to this package.

``StubGit`` mirrors the full ``GitRunner`` surface consumed across
sync and status tests; per-test failure injection rides on the kwargs
(``clone_fail``, ``fetch_fail``, ``local_fetch_fail``, ``status_fail``,
``pull_fail``). ``StubRegistry`` exposes the union of
``WorkspaceRegistryRepository`` methods (``register`` / ``find_by_path``
/ ``entries`` / ``get`` / ``unregister``) with optional positional
seeding so empty-init and seeded-init test sites both keep working
unchanged. ``StubFilesystem`` satisfies the widened ``Filesystem`` port
with an in-memory set of paths — lets use-case tests assert disk
predicates without touching ``tmp_path``. ``StubManifests`` satisfies
the ``ManifestRepository`` port with an in-memory map of paths to
manifests — used by the resolver's stub-based unit tests where the
disk-touching ``ManifestRepository`` would force a real workspace
on disk. ``empty_manifest()`` is a default-constructed
``WorkspaceManifest`` for tests that only need an empty file on disk
(init/add/remove/import + workspace-resolver tests).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Set
from pathlib import Path
from typing import Any

from untaped_workspace.domain import (
    ManifestSource,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError, ManifestError, RegistryError


def empty_manifest() -> WorkspaceManifest:
    """Default-constructed manifest for tests that only need an empty file on disk."""
    return WorkspaceManifest()


class StubGit:
    """Stub satisfying the ``GitRunner`` port for unit tests."""

    def __init__(
        self,
        *,
        on_disk: Iterable[str] = (),
        statuses: dict[str, RepoStatus] | None = None,
        clone_fail: Set[str] = frozenset(),
        fetch_fail: bool = False,
        local_fetch_fail: Set[str] = frozenset(),
        status_fail: Set[str] = frozenset(),
        pull_fail: Set[str] = frozenset(),
    ) -> None:
        self.events: list[tuple[Any, ...]] = []
        self._on_disk = set(on_disk)
        self._statuses = statuses or {}
        self._clone_fail = clone_fail
        self._fetch_fail = fetch_fail
        self._local_fetch_fail = local_fetch_fail
        self._status_fail = status_fail
        self._pull_fail = pull_fail

    def ensure_bare(self, url: str, *, cache_dir: Path) -> Path:
        self.events.append(("ensure_bare", url))
        return Path(f"/tmp/cache/{url.split('/')[-1]}")

    def bare_fetch(self, bare_path: Path) -> None:
        self.events.append(("bare_fetch", bare_path))
        if self._fetch_fail:
            raise GitError("network down")

    def clone_with_reference(
        self, *, url: str, dest: Path, bare: Path, branch: str | None = None
    ) -> None:
        self.events.append(("clone", str(dest), branch))
        if dest.name in self._clone_fail:
            raise GitError("clone failed")
        self._on_disk.add(dest.name)
        dest.mkdir(parents=True, exist_ok=True)

    def fetch(self, repo_path: Path) -> None:
        self.events.append(("fetch", repo_path.name))
        if repo_path.name in self._local_fetch_fail:
            raise GitError("network down")

    def status(self, repo_path: Path) -> RepoStatus:
        self.events.append(("status", repo_path.name))
        if repo_path.name in self._status_fail:
            raise GitError("status failed")
        return self._statuses.get(repo_path.name, RepoStatus(branch="main"))

    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None:
        self.events.append(("pull", repo_path.name, branch))
        if repo_path.name in self._pull_fail:
            raise GitError("non-fast-forward pull")


class StubRegistry:
    """Stub satisfying the ``WorkspaceRegistryRepository`` port."""

    def __init__(self, seeded: Iterable[Workspace] = ()) -> None:
        self.registered: list[Workspace] = list(seeded)
        self.unregistered: list[str] = []

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

    def get(self, name: str) -> Workspace:
        for w in self.registered:
            if w.name == name:
                return w
        raise RegistryError(f"unknown workspace: {name!r}")

    def unregister(self, name: str) -> bool:
        before = len(self.registered)
        self.registered = [w for w in self.registered if w.name != name]
        if len(self.registered) < before:
            self.unregistered.append(name)
            return True
        return False


class StubFilesystem:
    """In-memory ``Filesystem`` for use-case tests that don't need real I/O.

    Seed the constructor with the directories that should "exist".
    ``mkdir`` and ``rmtree`` mutate the set so call sequences are
    observable; the ``events`` list records every operation for tests
    that need to pin the order.

    **Semantic divergences from real ``pathlib`` / ``shutil``** worth
    knowing when reading test failures:

    - ``iterdir(p)`` yields seeded entries whose ``parent == p``. Real
      ``iterdir`` only yields entries that *literally exist* under
      ``p`` on disk — if a test seeds ``Path("/ws/a")`` without seeding
      ``Path("/ws")``, ``iterdir(Path("/ws"))`` still yields ``a``.
      Fine for the current callers (`SyncWorkspace._prune_orphans` only
      iterdirs a path it has already established exists), worth a
      thought before adding new callers.
    - ``rmtree(p)`` removes ``p`` and every seeded descendant; matches
      ``shutil.rmtree`` for the dirs-only model used here.
    """

    def __init__(self, dirs: Iterable[Path] = ()) -> None:
        self._dirs: set[Path] = {Path(p) for p in dirs}
        self.events: list[tuple[str, Path]] = []

    def exists(self, path: Path) -> bool:
        return path in self._dirs

    def is_dir(self, path: Path) -> bool:
        return path in self._dirs

    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None:
        # Honour `exist_ok` so tests catch any caller that flips it to
        # `False` against a path already in the set — keeps the stub
        # faithful to `pathlib.Path.mkdir` semantics for the cases that
        # matter. `parents` would require modelling the full path tree;
        # not worth it until a real caller cares.
        if not exist_ok and path in self._dirs:
            raise FileExistsError(path)
        self.events.append(("mkdir", path))
        self._dirs.add(path)

    def iterdir(self, path: Path) -> Iterator[Path]:
        return iter([p for p in self._dirs if p.parent == path])

    def rmtree(self, path: Path) -> None:
        self.events.append(("rmtree", path))
        self._dirs = {p for p in self._dirs if p != path and path not in p.parents}


class StubManifests:
    """In-memory ``ManifestRepository`` for stub-driven use-case tests.

    Seed ``manifests`` with the workspace dirs that should have a
    manifest; ``exists`` and ``read`` honour the map. ``read`` on an
    unseeded dir raises :class:`ManifestError` to match the real
    adapter's contract; ``write`` / ``read_external`` are stubbed out
    (no caller in the stub-only test set today).
    """

    def __init__(self, manifests: dict[Path, WorkspaceManifest] | None = None) -> None:
        self._manifests = dict(manifests or {})

    def exists(self, workspace_dir: Path) -> bool:
        return workspace_dir in self._manifests

    def read(self, workspace_dir: Path) -> WorkspaceManifest:
        if workspace_dir not in self._manifests:
            raise ManifestError(f"no manifest at {workspace_dir}/untaped.yml")
        return self._manifests[workspace_dir]

    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None:
        self._manifests[workspace_dir] = manifest

    def read_external(self, source: Path) -> ManifestSource:
        raise NotImplementedError("StubManifests.read_external is not used by current tests")
