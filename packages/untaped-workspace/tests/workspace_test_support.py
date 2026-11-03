"""Helpers the workspace plugin's tests share: real git, the command line, fake providers.

The fakes (git's own, from ``untaped_git.testing``, and a forge with its own
record kind) fill git's ``RepoSource`` and ``GitHost`` as plugins workspace
knows nothing about, as it knows nothing about the real ones:
``install(WorkspacePlugin, providers={"github": (listing(make_repo("acme/api")),)})``.
Each factory returns a provider class of its own, since core builds a
provider itself; what the class lists, raises or counted is on the class, so
a test changes ``github.error`` or reads ``github.calls`` after composing it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import ClassVar

from untaped.sdk import Record, UsageError
from untaped.sdk.contracts import Provider
from untaped.sdk.testing import Ran, Running, install
from untaped_git.api import Repo, RepoSource, store_key
from untaped_git.testing import GitRemote
from untaped_workspace.domain.models import RepoSpec, WorkspaceRecord
from untaped_workspace.infrastructure import ProjectWorkspaceStore
from untaped_workspace.plugin import WorkspacePlugin

type Providers = Mapping[str, Sequence[type]]


#: The providers the innermost :func:`composed` block composes, which :func:`run` installs.
_COMPOSED: ContextVar[Providers | None] = ContextVar("workspace_test_providers", default=None)


def run(args: Sequence[str], *, input: str | None = None, quiet: bool = False) -> Ran:
    """``untaped [-q] workspace <args>``: workspace (and git) installed, with the providers
    of the :func:`composed` block it runs in."""
    installed = install(WorkspacePlugin, providers=_COMPOSED.get())
    return installed.cli([*(["-q"] if quiet else []), "workspace", *args], input=input)


@contextmanager
def composed(providers: Providers | None = None) -> Iterator[Running]:
    """The block runs as a workspace command would, with ``providers`` composed (for
    :func:`run` too)."""
    token = _COMPOSED.set(providers)
    try:
        with install(WorkspacePlugin, providers=providers).running() as running:
            yield running
    finally:
        _COMPOSED.reset(token)


def err(ran: Ran) -> str:
    """What the command showed on stderr, as one text."""
    return "\n".join(ran.stderr)


def _git_dir(cwd: Path) -> list[str]:
    """``--git-dir`` for a bare repository: the suite runs with ``safe.bareRepository=explicit``."""
    return (
        ["--git-dir", str(cwd)] if (cwd / "HEAD").is_file() and (cwd / "objects").is_dir() else []
    )


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *_git_dir(cwd), *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def store_repo(root: Path, url: str) -> Path:
    """The store repo of ``url`` under the store ``root``."""
    return root.joinpath(*store_key(url))


def commit_in(worktree: Path, name: str = "change.txt") -> None:
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(worktree, "config", key, value)
    (worktree / name).write_text("x")
    git(worktree, "add", name)
    git(worktree, "commit", "-q", "-m", f"add {name}")


def add_submodule(upstream: GitRemote, sub: GitRemote, *, path: str = "lib") -> None:
    """Push a commit adding ``sub`` as a submodule at ``path`` to ``upstream``'s ``main``."""
    seed = upstream.path.parent / f"_seed_sub_{upstream.path.stem}"
    git(upstream.path.parent, "clone", "-q", str(upstream.path), str(seed))
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(seed, "config", key, value)
    git(seed, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub.path), path)
    git(seed, "commit", "-q", "-m", "add submodule")
    git(seed, "push", "-q", "origin", "main")
    shutil.rmtree(seed)


def init_submodules(worktree: Path) -> None:
    git(worktree, "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init")


class Project(Record):
    """The forge's own record of a repo: what it pipes (``--format pipe``)."""

    path: str
    clone_url: str


class Forge(RepoSource[Project], Provider):
    """A provider with its own record kind, turned into a repo by its bridge.

    Like github's, the bridge refuses a record whose clone URL is on another
    host than the forge's (a forged record).
    """

    rows: ClassVar[list[Project]] = []
    host: ClassVar[str] = "git.example"
    bridged: ClassVar[list[Project]] = []
    listings: ClassVar[int] = 0

    def to_repo(self, item: Project) -> Repo:
        self.bridged.append(item)
        if not item.clone_url.startswith(f"https://{self.host}/"):
            raise UsageError(f"forge.project {item.path!r}: clone URL is not on {self.host}")
        return Repo(name=item.path, url=item.clone_url)

    def repos(self) -> list[Repo]:
        type(self).listings += 1
        return [self.to_repo(row) for row in self.rows]


def forge(*rows: Project, host: str = "git.example") -> type[Forge]:
    """A :class:`Forge` of its own, listing ``rows`` on ``host``."""
    attrs = {"rows": list(rows), "host": host, "bridged": [], "listings": 0}
    return type("Forge", (Forge,), attrs)


def stored(name: str) -> WorkspaceRecord | None:
    """The active workspace ``name``, as its project keeps it; ``None`` without one."""
    found = [r for r in ProjectWorkspaceStore().all() if r.name == name and not r.archived]
    assert len(found) <= 1, found
    return found[0] if found else None


def archived_records() -> list[WorkspaceRecord]:
    """Every archived workspace, by name."""
    return [record for record in ProjectWorkspaceStore().all() if record.archived]


def new_workspace(name: str, *repos: RepoSpec) -> WorkspaceRecord:
    """``workspace create NAME`` with no repo, then ``repos`` held and their rows written."""
    created = run(["create", name, "--empty"])
    assert created.exit == 0, err(created)
    record = stored(name)
    assert record is not None
    if repos:
        store = ProjectWorkspaceStore()
        for spec in repos:
            store.hold(record, spec.url)
        record = store.put_repos(record, repos)
    return record
