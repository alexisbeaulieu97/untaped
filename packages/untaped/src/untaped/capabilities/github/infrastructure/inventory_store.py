"""JSON-file adapter for the cached repository inventory.

One file (``github.inventory.path``) holds a versioned document; anything
unreadable loads as ``None`` so the use case refetches. Writes are atomic and
refreshes take ``<path>.lock``; a disk failure on either is a ``local``
``failed`` error.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from untaped.capabilities.github.domain.inventory import RepoInventory, RepositoryInventoryItem
from untaped.capabilities.github.errors import GithubError
from untaped.sdk import atomic_write, file_lock

_VERSION = 1


class JsonInventoryStore:
    """Read and write the inventory file at ``path``."""

    def __init__(self, path: Path, *, lock_timeout: float = 60.0) -> None:
        # Resolve symlinks so the lock file and the (symlink-following) write agree.
        self._path = path.resolve()
        self._lock_timeout = lock_timeout

    def load(self) -> RepoInventory | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("version") != _VERSION:
                return None
            refreshed = data["refreshed_at"]
            refreshed_at = datetime.fromisoformat(refreshed) if refreshed else None
            if refreshed_at is not None and refreshed_at.tzinfo is None:
                return None  # a naive timestamp cannot be compared with now(UTC)
            return RepoInventory(
                repos=tuple(RepositoryInventoryItem.model_validate(row) for row in data["repos"]),
                refreshed_at=refreshed_at,
                scope_key=str(data["scope_key"]),
            )
        except OSError, ValueError, KeyError, TypeError, AttributeError:
            return None

    def save(self, inventory: RepoInventory) -> None:
        document = {
            "version": _VERSION,
            "scope_key": inventory.scope_key,
            "refreshed_at": inventory.refreshed_at.isoformat() if inventory.refreshed_at else None,
            "repos": [repo.model_dump(mode="json") for repo in inventory.repos],
        }
        try:
            atomic_write(self._path, json.dumps(document, sort_keys=True) + "\n")
        except OSError as exc:
            raise self._write_error(exc) from exc

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._write_error(exc) from exc
        with file_lock(
            self._path.with_name(self._path.name + ".lock"),
            timeout=self._lock_timeout,
            error=lambda message: GithubError(message, system="local"),
            busy=f"repository inventory is being refreshed by another process: {self._path}",
            failed=f"could not lock the repository inventory {self._path}",
        ):
            yield

    def _write_error(self, exc: OSError) -> GithubError:
        # A local disk failure, attributed like workspace's and ansible's.
        return GithubError(
            f"could not write the repository inventory {self._path}: {exc.strerror or exc}",
            category="failed",
            system="local",
        )
