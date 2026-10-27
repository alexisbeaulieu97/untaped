"""Helpers the workspace plugin's tests share: real git, the command line, fake providers.

The fakes fill workspace's ``RepoSource`` (and git's ``GitHost``) as plugins
workspace knows nothing about, as it knows nothing about the real ones:
``install(WorkspacePlugin, providers={"github": (listing(repo("acme/api")),)})``.
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

from untaped.sdk import UntapedError, UsageError, home
from untaped.sdk.contracts import NotReady, Provider, Record
from untaped.sdk.testing import Ran, Running, install
from untaped.sdk.testing.git import GitRemote
from untaped_git.api import Credential, GitHost, store_key
from untaped_workspace.api import Repo, RepoSource
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


def repo(name: str, host: str = "github.com", **fields: object) -> Repo:
    """``name`` on ``host``: ``https://<host>/<name>.git``."""
    return Repo(name=name, url=f"https://{host}/{name}.git", **fields)  # type: ignore[arg-type]


def invalid_repo(name: str, host: str = "gitlab.example") -> Repo:
    """A row a provider might return that is not a valid :class:`Repo` (``infra//api``)."""
    return Repo.model_construct(name=name, url=f"https://{host}/{name}.git")


class Listing(RepoSource, Provider):
    """Lists ``rows``, or raises ``error``; ``not_ready`` makes it wait for a setting.

    ``calls`` counts the live calls (the answer cache may serve the others).
    """

    rows: ClassVar[list[Repo]] = []
    error: ClassVar[UntapedError | None] = None
    not_ready: ClassVar[NotReady | None] = None
    calls: ClassVar[int] = 0

    def ready(self) -> NotReady | None:
        return type(self).not_ready

    def repos(self) -> list[Repo]:
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return list(self.rows)


def listing(
    *rows: Repo, error: UntapedError | None = None, not_ready: NotReady | None = None
) -> type[Listing]:
    """A :class:`Listing` of its own, listing ``rows``."""
    attrs = {"rows": list(rows), "error": error, "not_ready": not_ready, "calls": 0}
    return type("Listing", (Listing,), attrs)


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


class Host(GitHost, Provider):
    """A plugin serving ``home`` (git's ``GitHost``), with no token."""

    served: ClassVar[str] = "git.example"

    def home(self) -> str | None:
        return self.served

    def credential(self, url: str) -> Credential | None:
        return None


def host(served: str) -> type[Host]:
    """A :class:`Host` of its own, serving ``served``."""
    return type("Host", (Host,), {"served": served})


def rank(*plugins: str) -> None:
    """Rank ``plugins`` (first first) for ``RepoSource.repos``, as a user would in config.yml.

    Install the plugins after: ``install`` keeps the ranking core keeps in
    workspace's section.
    """
    path = home().expanduser() / "config.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "format_version: 2\nworkspace:\n  extensions:\n    repo_source:\n"
        f"      rank:\n        repos: [{', '.join(plugins)}]\n",
        encoding="utf-8",
    )
