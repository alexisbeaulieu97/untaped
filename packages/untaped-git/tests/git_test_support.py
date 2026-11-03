"""Helpers the git plugin's tests share: store handles, git run on a store repo, and fake
providers of git's contracts (``RepoSource`` listings, ``GitHost`` homes)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from untaped.sdk import UntapedError, home
from untaped.sdk.contracts import NotReady, Provider
from untaped.sdk.testing import Running, install
from untaped_git.api import Credential, GitHost, Repo, RepoSource
from untaped_git.errors import GitError
from untaped_git.plugin import Git
from untaped_git.store import RepoStore

type StoreFor = Callable[..., RepoStore]


class StoreError(GitError):
    """The error class the tests' store handles raise; a consumer passes its own."""


def ensure(store: RepoStore) -> bool:
    """Create the repo and reconcile its policy, as every writer does first; created?"""
    with store._repo.locked():
        return store._repo.ensure()


def git(repo: Path, *args: str, bare: bool = True, input: str | None = None) -> str:
    """Run git on ``repo`` (a bare repo by ``--git-dir``, else a worktree) and return stdout."""
    where = [f"--git-dir={repo}"] if bare else ["-C", str(repo)]
    result = subprocess.run(
        ["git", *where, *args], input=input, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def all_refs(repo: Path) -> dict[str, str]:
    out = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split(" ", 1) for line in out.splitlines())


def objects(repo: Path) -> dict[str, int]:
    """``count-objects -v`` as numbers: ``count`` is loose objects, ``packs`` pack files."""
    out = git(repo, "count-objects", "-v")
    return {
        key: int(value)
        for key, _, value in (line.partition(": ") for line in out.splitlines())
        if value.isdigit()
    }


@contextmanager
def composed(providers: Mapping[str, Sequence[type]]) -> Iterator[Running]:
    """The block runs as a git command would, with ``providers`` composed as plugins."""
    with install(Git, providers=providers).running() as running:
        yield running


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


class Host(GitHost, Provider):
    """A plugin serving ``home`` (``GitHost``), with no token."""

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
    git's section.
    """
    path = home().expanduser() / "config.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "format_version: 2\ngit:\n  extensions:\n    repo_source:\n"
        f"      rank:\n        repos: [{', '.join(plugins)}]\n",
        encoding="utf-8",
    )
