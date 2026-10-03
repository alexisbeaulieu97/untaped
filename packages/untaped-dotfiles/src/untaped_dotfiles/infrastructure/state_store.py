"""``StateDotfilesStore``: repos, item choices and applied records in ``state.yml``.

Also the advisory lock (``<state_dir>/.lock``) that keeps a timer's ``sync``
and a manual ``apply`` from interleaving, and the two status files a prompt
segment reads (``status.json`` and the one-line ``attention``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from untaped.sdk import StateCollection, atomic_write, file_lock
from untaped_dotfiles.domain.models import AppliedRecord, ItemChoice, RepoRecord, item_id
from untaped_dotfiles.domain.records import StatusSummary
from untaped_dotfiles.errors import DotfilesError

_BUSY_HINT = "wait for the other untaped dotfiles command to finish, then retry"
STATUS_FILE = "status.json"
ATTENTION_FILE = "attention"


class StateDotfilesStore:
    """:class:`DotfilesStore` over ``state.yml`` → ``dotfiles``."""

    def __init__(self, *, state_dir: Path, lock_timeout: float = 600.0) -> None:
        self._repos = StateCollection("dotfiles", "repos", id_field="name")
        self._items = StateCollection("dotfiles", "items", id_field="id")
        self._applied = StateCollection("dotfiles", "applied", id_field="target")
        self._state_dir = state_dir
        self._lock_timeout = lock_timeout

    @contextmanager
    def locked(self) -> Iterator[None]:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        with file_lock(
            self._state_dir / ".lock",
            timeout=self._lock_timeout,
            error=lambda message: DotfilesError(message, hint=_BUSY_HINT),
            busy="dotfiles are busy (another untaped process)",
            failed="could not lock the dotfiles state",
        ):
            yield

    def repos(self) -> list[RepoRecord]:
        return [RepoRecord.model_validate(row) for row in self._repos.entries()]

    def get_repo(self, name: str) -> RepoRecord | None:
        row = self._repos.get(name)
        return None if row is None else RepoRecord.model_validate(row)

    def put_repo(self, record: RepoRecord) -> None:
        self._repos.upsert(_dump(record))

    def remove_repo(self, name: str) -> bool:
        return self._repos.remove(name)

    def items(self) -> list[ItemChoice]:
        return [ItemChoice.model_validate(row) for row in self._items.entries()]

    def get_item(self, repo: str, name: str) -> ItemChoice | None:
        row = self._items.get(item_id(repo, name))
        return None if row is None else ItemChoice.model_validate(row)

    def put_item(self, choice: ItemChoice) -> None:
        self._items.upsert(_dump(choice))

    def remove_item(self, repo: str, name: str) -> bool:
        return self._items.remove(item_id(repo, name))

    def applied(self) -> list[AppliedRecord]:
        return [AppliedRecord.model_validate(row) for row in self._applied.entries()]

    def put_applied(self, record: AppliedRecord) -> None:
        self._applied.upsert(_dump(record))

    def remove_applied(self, target: str) -> bool:
        return self._applied.remove(target)

    def write_status(self, summary: StatusSummary) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._state_dir / STATUS_FILE, summary.model_dump_json(indent=2) + "\n", mode=0o600
        )
        atomic_write(self._state_dir / ATTENTION_FILE, f"{summary.attention}\n", mode=0o600)


def _dump(record: Any) -> dict[str, Any]:
    data: dict[str, Any] = record.model_dump(mode="json")
    return data
