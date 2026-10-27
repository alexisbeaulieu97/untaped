"""``StateDotfilesStore``: repos, item choices and applied records, kept as the plugin's state rows.

The rows and the operation lock are blocks the plugin class holds
(:class:`~untaped_dotfiles.plugin.Dotfiles`); the lock keeps a timer's
``sync`` and a manual ``apply`` from interleaving. The store also writes the
two status files a prompt segment reads (``status.json`` and the one-line
``attention``).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import timedelta
from pathlib import Path

from untaped.sdk import Lock, StateRows, atomic_write
from untaped_dotfiles.models import AppliedRecord, ItemChoice, RepoRecord, item_id
from untaped_dotfiles.records import StatusSummary

STATUS_FILE = "status.json"
ATTENTION_FILE = "attention"

#: Subscribed repos, by name.
REPOS = StateRows[RepoRecord]()
#: The machine's enabled items, by ``<repo>/<item>``.
ITEMS = StateRows[ItemChoice]()
#: One row per placed path, by its absolute path.
APPLIED = StateRows[AppliedRecord]()
#: Held by ``apply``, ``sync`` and ``remove``.
SYNC = Lock(wait=timedelta(minutes=10))


class StateDotfilesStore:
    """:class:`DotfilesStore` over the plugin's state rows; status files go to ``state_dir``."""

    def __init__(self, *, state_dir: Path) -> None:
        self._state_dir = state_dir

    def locked(self) -> AbstractContextManager[None]:
        return SYNC.locked()

    def repos(self) -> list[RepoRecord]:
        return REPOS.all()

    def get_repo(self, name: str) -> RepoRecord | None:
        return REPOS.get(name)

    def put_repo(self, record: RepoRecord) -> None:
        REPOS.put(record)

    def remove_repo(self, name: str) -> bool:
        return REPOS.delete(name) > 0

    def items(self) -> list[ItemChoice]:
        return ITEMS.all()

    def get_item(self, repo: str, name: str) -> ItemChoice | None:
        return ITEMS.get(item_id(repo, name))

    def put_item(self, choice: ItemChoice) -> None:
        ITEMS.put(choice)

    def remove_item(self, repo: str, name: str) -> bool:
        return ITEMS.delete(item_id(repo, name)) > 0

    def applied(self) -> list[AppliedRecord]:
        return APPLIED.all()

    def put_applied(self, record: AppliedRecord) -> None:
        APPLIED.put(record)

    def remove_applied(self, target: str) -> bool:
        return APPLIED.delete(target) > 0

    def write_status(self, summary: StatusSummary) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._state_dir / STATUS_FILE, summary.model_dump_json(indent=2) + "\n", mode=0o600
        )
        atomic_write(self._state_dir / ATTENTION_FILE, f"{summary.attention}\n", mode=0o600)
