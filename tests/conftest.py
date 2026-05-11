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
unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Set
from pathlib import Path
from typing import Any

from untaped_workspace.domain import RepoStatus, Workspace
from untaped_workspace.errors import GitError, RegistryError


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
