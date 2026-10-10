"""Fake plugins filling workspace's ``RepoSource`` (and git's ``GitHost``) for the tests.

Compose them with ``untaped.testing.compose_with(GIT, WORKSPACE, provides={"github": [...]})``:
the fake plugin's name is the ``provides`` key, so ``github`` and ``gitlab`` here are fakes
workspace knows nothing about, as it knows nothing about the real ones.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from untaped.contracts import NotReady, Record
from untaped.sdk import UntapedError, UsageError
from untaped.settings import get_settings
from untaped_git.api import Credential, GitHost
from untaped_workspace.api import Repo, RepoSource


def repo(name: str, host: str = "github.com", **fields: object) -> Repo:
    """``name`` on ``host``: ``https://<host>/<name>.git``."""
    return Repo(name=name, url=f"https://{host}/{name}.git", **fields)  # type: ignore[arg-type]


def invalid_repo(name: str, host: str = "gitlab.example") -> Repo:
    """A row a provider might return that is not a valid :class:`Repo` (``infra//api``)."""
    return Repo.model_construct(name=name, url=f"https://{host}/{name}.git")


class Listing(RepoSource):
    """Lists ``rows``, or raises ``error``; ``not_ready`` makes it wait for a setting.

    ``calls`` counts the live calls (the answer cache may serve the others).
    """

    def __init__(
        self,
        *rows: Repo,
        error: UntapedError | None = None,
        not_ready: NotReady | None = None,
    ) -> None:
        self.rows = list(rows)
        self.error = error
        self.not_ready = not_ready
        self.calls = 0

    def ready(self) -> NotReady | None:
        return self.not_ready

    def repos(self) -> list[Repo]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.rows)


class Project(Record, kind="forge.project"):
    """The forge's own record of a repo: what it pipes (``--format pipe``)."""

    path: str
    clone_url: str


class Forge(RepoSource[Project]):
    """A provider with its own record kind, turned into a repo by its bridge.

    Like github's, the bridge refuses a record whose clone URL is on another
    host than the forge's (a forged record).
    """

    def __init__(self, *rows: Project, host: str = "git.example") -> None:
        self.rows = list(rows)
        self.host = host
        self.bridged: list[Project] = []
        self.listings = 0

    def to_repo(self, item: Project) -> Repo:
        self.bridged.append(item)
        if not item.clone_url.startswith(f"https://{self.host}/"):
            raise UsageError(f"forge.project {item.path!r}: clone URL is not on {self.host}")
        return Repo(name=item.path, url=item.clone_url)

    def repos(self) -> list[Repo]:
        self.listings += 1
        return [self.to_repo(row) for row in self.rows]


class Host(GitHost):
    """A plugin serving ``home`` (git's ``GitHost``), with no token."""

    def __init__(self, home: str) -> None:
        self._home = home

    def home(self) -> str | None:
        return self._home

    def credential(self, url: str) -> Credential | None:
        return None


def rank(*plugins: str) -> None:
    """Rank ``plugins`` (first first) for ``RepoSource.repos`` in the default profile."""
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "profiles:\n  default:\n    workspace:\n      extensions:\n        repo_source:\n"
        f"          rank:\n            repos: [{', '.join(plugins)}]\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()


def age_answers(by: timedelta) -> None:
    """Make every stored contract answer ``by`` older (past its max age, say)."""
    for entry in (Path.home() / ".untaped" / "plugins").glob("*/cache/**/*.json"):
        data = json.loads(entry.read_text(encoding="utf-8"))
        data["refreshed_at"] = (datetime.fromisoformat(data["refreshed_at"]) - by).isoformat()
        entry.write_text(json.dumps(data), encoding="utf-8")
