"""Real-git fixtures for tests that fetch: a local remote that behaves like a Git host.

:func:`git_remote` makes a bare repository that answers at an ``https://``
URL (the test ``HOME``'s git config rewrites it to the local path), with an
authoring clone to commit and push from. The remote can stand in for the
hosts the repo store has to survive: one that ignores the partial-clone
filter (:meth:`GitRemote.allow_filter`), one that refuses blob fetches by
object id (:meth:`GitRemote.refuse_by_oid`, the protocol v0 case), and one
that drops the connection mid-pack (:meth:`GitRemote.drop_pack`).
:func:`hostile_git_home` adds the global settings a store must never obey,
:func:`git_shim` puts a ``git`` on ``PATH`` that reports another version,
refuses ``--stdin`` or stalls maintenance, and :func:`trace2_events` reads a
``GIT_TRACE2_EVENT`` file. Everything runs without a network.

Tests run under ``untaped.testing.plugin``'s hermetic ``HOME``: the
fixtures write to its ``~/.gitconfig``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_AUTHOR = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
#: Settings a store must never obey, as a user's global config might set them.
HOSTILE_GLOBALS: Mapping[str, str] = {
    "fetch.prune": "true",
    "fetch.pruneTags": "true",
    "fetch.unpackLimit": "100",
    "gc.auto": "0",
    "maintenance.auto": "false",
}


def _git(*args: str, cwd: Path | None = None, input: str | None = None) -> str:
    env = {**os.environ, **_AUTHOR}
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        input=input,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def global_config(key: str, value: str, *, add: bool = False) -> None:
    """Set ``key`` in the test ``HOME``'s ``~/.gitconfig``."""
    _git("config", "--global", *(["--add"] if add else []), key, value)


@dataclass
class GitRemote:
    """A local bare remote reached at :attr:`url`, with a clone to author commits in."""

    path: Path
    work: Path
    url: str

    def commit(self, path: str, text: str, *, branch: str = "main") -> str:
        """Write ``path`` on ``branch`` (created from ``main`` if new), commit and push; the oid."""
        self._switch(branch)
        target = self.work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git("add", "--", path, cwd=self.work)
        _git("commit", "--quiet", "-m", f"write {path}", cwd=self.work)
        _git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=self.work)
        return _git("rev-parse", "HEAD", cwd=self.work).strip()

    def branch(self, name: str, at: str = "main") -> str:
        """Create branch ``name`` at ``at`` on the remote; its oid."""
        oid = self.oid(at)
        _git("push", "--quiet", "origin", f"{oid}:refs/heads/{name}", cwd=self.work)
        return oid

    def branches(self, names: list[str], at: str = "main") -> None:
        """Create many branches at ``at`` in one push."""
        oid = self.oid(at)
        specs = [f"{oid}:refs/heads/{name}" for name in names]
        _git("push", "--quiet", "origin", *specs, cwd=self.work)

    def spread(self, names: list[str], at: str = "main") -> None:
        """Create one branch per name, each on its own new commit on top of ``at``, in one push.

        Unlike :meth:`branches`, every branch brings objects of its own, so a
        fetch of any subset of them transfers a pack.
        """
        parent = self.oid(at)
        specs = []
        for name in names:
            blob = _git("hash-object", "-w", "--stdin", cwd=self.work, input=f"{name}\n").strip()
            tree = _git("mktree", cwd=self.work, input=f"100644 blob {blob}\t{name}.txt\n").strip()
            oid = _git("commit-tree", tree, "-p", parent, "-m", name, cwd=self.work).strip()
            specs.append(f"{oid}:refs/heads/{name}")
        _git("push", "--quiet", "origin", *specs, cwd=self.work)

    def delete_branch(self, name: str) -> None:
        _git("push", "--quiet", "origin", f":refs/heads/{name}", cwd=self.work)

    def tag(self, name: str, at: str = "main") -> str:
        oid = self.oid(at)
        _git("push", "--quiet", "--force", "origin", f"{oid}:refs/tags/{name}", cwd=self.work)
        return oid

    def oid(self, rev: str) -> str:
        """The remote's oid for ``rev`` (a branch name, tag or oid)."""
        for candidate in (f"refs/heads/{rev}", f"refs/tags/{rev}", rev):
            result = subprocess.run(
                ["git", f"--git-dir={self.path}", "rev-parse", "--verify", "--quiet", candidate],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        raise AssertionError(f"{rev!r} is not on the remote")

    def allow_filter(self, on: bool = True) -> None:
        """Whether the remote honours ``--filter`` (``uploadpack.allowFilter``)."""
        _git(f"--git-dir={self.path}", "config", "uploadpack.allowFilter", str(on).lower())

    def refuse_by_oid(self) -> None:
        """Refuse blob fetches by object id, as a protocol v0 server without ``allowAnySHA1InWant``.

        Protocol v2 never consults that setting, so the client is forced to v0
        in ``~/.gitconfig``: the only way the refusal happens.
        """
        self.allow_filter(True)
        _git(f"--git-dir={self.path}", "config", "uploadpack.allowAnySHA1InWant", "false")
        global_config("protocol.version", "0")

    def drop_pack(self, *, on_call: int, times: int = 1) -> None:
        """Cut the pack short on the ``on_call``-th fetch from now (1-based), ``times`` in a row.

        Installs a ``uploadpack.packObjectsHook`` (a global setting, the only
        scope git reads it from); ``ls-remote`` packs nothing, so only fetches
        count, and so do only fetches that need objects. The client sees the
        pack end early, which git reports as a transient failure.
        """
        hooks = self.path.parent / f"{self.path.name}.hooks"
        hooks.mkdir(exist_ok=True)
        counter = hooks / "count"
        counter.write_text("0", encoding="utf-8")
        script = hooks / "pack-objects"
        last = on_call + times - 1
        script.write_text(
            "#!/bin/sh\n"
            f'n=$(($(cat "{counter}") + 1)); echo "$n" > "{counter}"\n'
            f'if [ "$n" -ge {on_call} ] && [ "$n" -le {last} ]; then\n'
            '  "$@" | head -c 2048; exit 0\n'
            "fi\n"
            'exec "$@"\n',
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        global_config("uploadpack.packObjectsHook", str(script))

    def packs_requested(self) -> int:
        """How many packs fetches have asked for since :meth:`drop_pack`, dropped ones included."""
        counter = self.path.parent / f"{self.path.name}.hooks" / "count"
        return int(counter.read_text(encoding="utf-8").strip())

    def _switch(self, branch: str) -> None:
        current = _git("symbolic-ref", "--short", "HEAD", cwd=self.work).strip()
        if current == branch:
            return
        exists = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.work,
            capture_output=True,
            check=False,
        )
        if exists.returncode == 0:
            _git("checkout", "--quiet", branch, cwd=self.work)
        else:
            _git("checkout", "--quiet", "-b", branch, cwd=self.work)


def git_remote(base: Path, name: str = "app", *, host: str = "git.example") -> GitRemote:
    """A remote under ``base`` answering at ``https://<host>/<name>.git``, with one commit on main.

    The remote honours the partial-clone filter, as GitHub does.
    """
    path = base / "remotes" / f"{name}.git"
    work = base / "authoring" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    _git("init", "--bare", "--quiet", "--initial-branch=main", str(path))
    _git("init", "--quiet", "--initial-branch=main", str(work))
    _git("remote", "add", "origin", str(path), cwd=work)
    url = f"https://{host}/{name}.git"
    global_config(f"url.file://{path}.insteadOf", url)
    remote = GitRemote(path=path, work=work, url=url)
    remote.allow_filter(True)
    remote.commit("README.md", f"# {name}\n")
    return remote


def hostile_git_home() -> None:
    """Add :data:`HOSTILE_GLOBALS` to the test ``HOME``'s ``~/.gitconfig``."""
    for key, value in HOSTILE_GLOBALS.items():
        global_config(key, value)


def git_shim(
    bin_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str | None = None,
    refuse_stdin: bool = False,
    stall_maintenance: Path | None = None,
) -> None:
    """Put a ``git`` first on ``PATH`` that forwards to the real one, except as asked.

    ``version`` changes what ``git --version`` reports; ``refuse_stdin``
    fails any command given ``--stdin`` as a git older than 2.29 does; while
    the file ``stall_maintenance`` exists, ``git maintenance run`` sleeps
    (its child's pid in ``<stall_maintenance>.pid``) until it is killed.
    """
    real = shutil.which("git")
    if real is None:
        raise AssertionError("git is not on PATH")
    bin_dir.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh"]
    if version is not None:
        lines.append(f'if [ "$1" = "--version" ]; then echo "git version {version}"; exit 0; fi')
    if refuse_stdin:
        lines += [
            'for arg in "$@"; do',
            '  if [ "$arg" = "--stdin" ]; then echo "error: unknown option \\`stdin\'" >&2; '
            "exit 129; fi",
            "done",
        ]
    if stall_maintenance is not None:
        lines += [
            'for arg in "$@"; do',
            f'  if [ "$arg" = "maintenance" ] && [ -e "{stall_maintenance}" ]; then',
            "    echo 'error: stalled repack' >&2",
            f'    sleep 300 & echo $! > "{stall_maintenance}.pid"; wait; exit 1',
            "  fi",
            "done",
        ]
    lines.append(f'exec "{real}" "$@"')
    shim = bin_dir / "git"
    shim.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def trace2_events(path: Path) -> list[dict[str, Any]]:
    """The events of a ``GIT_TRACE2_EVENT`` file, one dict per line."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
