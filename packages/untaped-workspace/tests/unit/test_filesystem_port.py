"""Contract tests for the widened ``Filesystem`` port.

These tests pin two invariants the port was widened to support:

1. ``LocalFilesystem`` faithfully delegates each method to the
   equivalent :mod:`pathlib` / :mod:`shutil` operation (the default
   adapter is a pure pass-through).
2. ``application/`` no longer reaches into ``pathlib`` for I/O —
   every disk read/write flows through the port. The grep guard
   doubles as a regression test for future PRs that might
   accidentally add ``Path.is_dir()`` / ``.exists()`` / ``.iterdir()``
   / ``.mkdir()`` calls back into application code.

Worked examples using :class:`conftest.StubFilesystem` demonstrate
the payoff: use-case tests that don't need ``tmp_path`` at all.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from untaped_workspace.application import Foreach, WorkspaceStatus
from untaped_workspace.domain import (
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.infrastructure import LocalFilesystem

from conftest import StubFilesystem

# ── LocalFilesystem pass-through ──────────────────────────────────────────


def test_local_filesystem_exists_and_is_dir(tmp_path: Path) -> None:
    fs = LocalFilesystem()
    sub = tmp_path / "x"
    assert fs.exists(tmp_path) and fs.is_dir(tmp_path)
    assert not fs.exists(sub) and not fs.is_dir(sub)
    sub.mkdir()
    assert fs.exists(sub) and fs.is_dir(sub)


def test_local_filesystem_mkdir_parents_and_exist_ok(tmp_path: Path) -> None:
    """``parents`` / ``exist_ok`` flow through to ``pathlib.Path.mkdir``.

    The Protocol intentionally has no defaults so call sites can't
    silently flip from `pathlib`'s defaults — pin both branches here.
    """
    fs = LocalFilesystem()
    nested = tmp_path / "a" / "b" / "c"
    fs.mkdir(nested, parents=True, exist_ok=True)
    assert nested.is_dir()
    fs.mkdir(nested, parents=True, exist_ok=True)  # idempotent under exist_ok=True
    sibling = tmp_path / "a" / "b" / "d"
    with pytest.raises(FileNotFoundError):
        # parents=False: still raises if the parent is real (it exists) but the
        # check is interesting when it doesn't — use a deeper gap to prove it.
        fs.mkdir(tmp_path / "x" / "y", parents=False, exist_ok=True)
    sibling.mkdir()
    with pytest.raises(FileExistsError):
        fs.mkdir(sibling, parents=False, exist_ok=False)


def test_local_filesystem_iterdir_returns_children(tmp_path: Path) -> None:
    fs = LocalFilesystem()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c.txt").write_text("x")
    children = {p.name for p in fs.iterdir(tmp_path)}
    assert children == {"a", "b", "c.txt"}


def test_local_filesystem_rmtree_removes_recursively(tmp_path: Path) -> None:
    fs = LocalFilesystem()
    target = tmp_path / "to-remove"
    (target / "nested").mkdir(parents=True)
    (target / "f.txt").write_text("x")
    fs.rmtree(target)
    assert not target.exists()


# ── Lint-as-test: no pathlib I/O in application/ ──────────────────────────


def test_no_pathlib_io_in_application_layer() -> None:
    """Every disk touch in ``application/`` flows through a port.

    The ``Filesystem`` Protocol (plus ``ManifestReader.exists``) exists
    so use cases stay testable with stubs; if a future PR accidentally
    adds a bare ``Path.is_dir()`` / ``.exists()`` / ``.iterdir()`` /
    ``.mkdir()`` to a use case, this test fails fast with a precise
    pointer.

    **Convention enforced by this test.** Port-mediated calls must use
    the ``self._<name>.method(...)`` shape — ``self._fs.is_dir(...)``,
    ``self._manifests.exists(...)``, etc. The lint allows that exact
    receiver shape and *only* that shape. A locally-bound ``fs = self._fs``
    followed by ``fs.is_dir(p)`` would trip a false positive — but that
    pattern would also defeat the readability the port is meant to
    deliver, so the convention doubles as a style rule. See
    ``packages/untaped-workspace/AGENTS.md``.

    ``ports.py`` itself is skipped: the matches there are Protocol
    method *declarations*, not calls.
    """
    pkg = Path(__file__).resolve().parents[2] / "src" / "untaped_workspace" / "application"
    leak_pattern = re.compile(r"\.(is_dir|exists|iterdir|mkdir)\(")
    # Any attribute access of the form ``self._<name>.method(`` is a
    # port call (``self._fs``, ``self._manifests``, ``self._registry``,
    # ``self._discoverer``, …) — those are the seam, not the leak.
    port_call = re.compile(r"self\._\w+\.(is_dir|exists|iterdir|mkdir)\(")
    hits: list[str] = []
    for path in pkg.rglob("*.py"):
        if path.name == "ports.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if leak_pattern.search(line) and not port_call.search(line):
                hits.append(f"{path.relative_to(pkg.parent.parent)}:{lineno}: {line.strip()}")
    assert not hits, "pathlib I/O leaked into application/:\n  " + "\n  ".join(hits)


# ── Payoff: a use-case test with no tmp_path ──────────────────────────────


def test_workspace_status_uses_port_to_check_clone_presence() -> None:
    """`WorkspaceStatus._row_for` returns ``cloned=False`` for repos
    whose local path the port reports as missing — no real filesystem
    required."""

    class _StubManifests:
        def read(self, workspace_dir: Path) -> WorkspaceManifest:
            return WorkspaceManifest(repos=[Repo(url="https://x/a.git")])

        def exists(self, workspace_dir: Path) -> bool:
            return True

    class _StubGit:
        def status(self, repo_path: Path) -> RepoStatus:
            return RepoStatus(branch="main")

        def is_dirty(self, repo_path: Path) -> bool:
            return False

        def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None:
            return None

        def read_current_branch(self, repo_path: Path) -> str | None:
            return None

    fs = StubFilesystem()  # no dirs present → "a" reads as not-cloned
    ws = Workspace(name="prod", path=Path("/tmp/ws"))
    entries = WorkspaceStatus(_StubManifests(), _StubGit(), fs=fs)(ws)
    assert entries[0].cloned is False


def test_foreach_uses_port_to_short_circuit_uncloned_repos() -> None:
    """`Foreach._run_one` returns a ``not cloned`` outcome when the port
    reports the local clone as missing — the shell runner never fires."""

    class _StubManifests:
        def read(self, workspace_dir: Path) -> WorkspaceManifest:
            return WorkspaceManifest(repos=[Repo(url="https://x/a.git")])

        def exists(self, workspace_dir: Path) -> bool:
            return True

    runner_calls: list[str] = []

    def runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        runner_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    fs = StubFilesystem()  # no dirs → uncloned branch
    ws = Workspace(name="prod", path=Path("/tmp/ws"))
    outcomes = Foreach(_StubManifests(), runner=runner, fs=fs)(ws, command="x")
    assert outcomes[0].returncode == -1
    assert "not cloned" in outcomes[0].stderr
    assert runner_calls == []
