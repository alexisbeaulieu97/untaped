"""``subscribe`` and ``unsubscribe``: clone or register a repo and read its manifest."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import UsageError, git_toplevel, not_found, q
from untaped_dotfiles.domain.manifest import DEFAULT_MANIFEST
from untaped_dotfiles.domain.models import RepoRecord
from untaped_dotfiles.domain.records import RepoOutcome
from untaped_dotfiles.errors import DotfilesError, GitError, RepoNotFoundError

if TYPE_CHECKING:
    from untaped_dotfiles.application.inventory import Inventory
    from untaped_dotfiles.application.ports import DotfilesStore, GitRepos

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def repo_name_from(url: str) -> str:
    """The last path segment of a URL or path, without ``.git``."""
    cleaned = url.rstrip("/").replace("\\", "/")
    leaf = cleaned.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return leaf.removesuffix(".git") or "dotfiles"


class SubscribeRepo:
    """Clone (a URL) or register (a path) a repo, validate its manifest, and record it."""

    def __init__(
        self,
        store: DotfilesStore,
        git: GitRepos,
        inventory: Inventory,
        *,
        repos_dir: Path,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._git = git
        self._inventory = inventory
        self._repos_dir = repos_dir
        self._now = now

    def __call__(
        self, source: str, *, name: str | None, manifest: str | None, ref: str | None
    ) -> RepoRecord:
        name = name or repo_name_from(source)
        if not _NAME.match(name):
            raise UsageError(f"repo name {name!r} must be letters, digits, '.', '_' or '-'")
        if self._store.get_repo(name) is not None:
            raise DotfilesError(
                f"repo {q(name)} is already subscribed",
                category="conflict",
                hint="pass --name to subscribe under another name",
            )
        local = Path(source).expanduser()
        if local.is_dir() and not (local / "HEAD").is_file():  # a checkout, not a bare repo
            record = self._register(name, local, manifest=manifest, ref=ref)
        else:
            record = self._clone(name, source, manifest=manifest, ref=ref)
        try:
            self._inventory.manifest(record)
        except DotfilesError:
            if record.managed:
                shutil.rmtree(record.path, ignore_errors=True)
            raise
        self._store.put_repo(record)
        return record

    def _register(
        self, name: str, local: Path, *, manifest: str | None, ref: str | None
    ) -> RepoRecord:
        root = git_toplevel(local)
        if root is None:
            raise DotfilesError(f"{local} is not inside a git work tree", category="invalid")
        if root != local.resolve():
            raise DotfilesError(
                f"{local} is not the root of its git work tree ({root})", category="invalid"
            )
        branch = ref or self._git.branch(root)
        if branch is None:
            raise GitError(f"{root} has no branch checked out; pass --ref")
        return RepoRecord(
            name=name,
            url=self._git.origin_url(root) or str(root),
            path=str(root),
            managed=False,
            manifest=manifest or DEFAULT_MANIFEST,
            ref=branch,
            subscribed_at=self._now(),
        )

    def _clone(self, name: str, url: str, *, manifest: str | None, ref: str | None) -> RepoRecord:
        dest = self._repos_dir / name
        if dest.exists():
            raise DotfilesError(
                f"{dest} already exists",
                category="conflict",
                hint=f"move it away, or run `untaped dotfiles subscribe {dest} --name {name}`",
            )
        branch = self._git.clone(url, dest, ref=ref)
        return RepoRecord(
            name=name,
            url=url,
            path=str(dest),
            managed=True,
            manifest=manifest or DEFAULT_MANIFEST,
            ref=branch,
            subscribed_at=self._now(),
        )


class UnsubscribeRepo:
    """Forget a repo; refuses while any of its items is enabled."""

    def __init__(self, store: DotfilesStore) -> None:
        self._store = store

    def plan(self, name: str) -> RepoRecord:
        record = self._store.get_repo(name)
        if record is None:
            raise RepoNotFoundError(
                not_found("repo", name, known=[r.name for r in self._store.repos()])
            )
        enabled = sorted(c.name for c in self._store.items() if c.repo == name)
        if enabled:
            raise DotfilesError(
                f"repo {q(name)} still has enabled items: {', '.join(enabled)}",
                category="conflict",
                hint=f"run `untaped dotfiles disable --all --repo {name}` first",
            )
        return record

    def __call__(self, record: RepoRecord, *, delete_clone: bool) -> RepoOutcome:
        self._store.remove_repo(record.name)
        if delete_clone and record.managed:
            shutil.rmtree(record.path, ignore_errors=True)
            return RepoOutcome(name=record.name, action="deleted", detail=f"removed {record.path}")
        return RepoOutcome(name=record.name, action="deleted", detail=f"kept {record.path}")
