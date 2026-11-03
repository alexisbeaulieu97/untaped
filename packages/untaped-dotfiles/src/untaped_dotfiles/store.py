"""``StateDotfilesStore``: clones and placed paths, kept as the plugin's state rows.

What untaped did, which no source can give back: the clone it made for each
git source, every item it applied once, and every path it placed. The machine's choices live in
``config.yml`` instead (``dotfiles.sources``). The rows and the operation lock
are blocks the plugin class holds (:class:`~untaped_dotfiles.plugin.Dotfiles`);
the lock keeps two runs that change files from interleaving. The store also
writes the two status files a prompt segment reads (``status.json`` and the
one-line ``attention``).
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path

from untaped.sdk.storage import Lock, StateRows, atomic_write
from untaped_dotfiles.models import CloneRecord, ItemRecord, PlacedRecord
from untaped_dotfiles.records import StatusSummary

STATUS_FILE = "status.json"
ATTENTION_FILE = "attention"

#: The clone untaped made for each git source, by source name.
CLONED = StateRows[CloneRecord]()
#: Each item applied here at least once, by ``SOURCE/PKG/ITEM``.
ITEMS = StateRows[ItemRecord]()
#: One row per placed path, by its absolute path.
PLACED = StateRows[PlacedRecord]()
#: Held by every command that changes files, clones or choices.
CHANGING = Lock(wait=timedelta(minutes=10))


class StateDotfilesStore:
    """:class:`DotfilesStore` over the plugin's state rows; status files go to ``state_dir``."""

    def __init__(self, *, state_dir: Path) -> None:
        self._state_dir = state_dir

    def locked(self) -> AbstractContextManager[None]:
        return CHANGING.locked()

    def clones(self) -> list[CloneRecord]:
        return CLONED.all()

    def get_clone(self, source: str) -> CloneRecord | None:
        return CLONED.get(source)

    def put_clone(self, record: CloneRecord) -> None:
        CLONED.put(record)

    def remove_clone(self, source: str) -> bool:
        return CLONED.delete(source) > 0

    def applied_item(self, owner: tuple[str, str, str]) -> ItemRecord | None:
        return ITEMS.get("/".join(owner))

    def put_applied(self, owner: tuple[str, str, str], at: datetime) -> None:
        if ITEMS.get("/".join(owner)) is None:
            ITEMS.put(ItemRecord(item="/".join(owner), first_applied_at=at))

    def note_waited(self, owner: tuple[str, str, str], entries: Iterable[tuple[str, str]]) -> None:
        """Add ``entries`` to the waited ones of an item applied here (nothing otherwise)."""
        row = ITEMS.get("/".join(owner))
        if row is None:
            return
        waited = tuple(dict.fromkeys((*row.waited, *entries)))
        if waited != row.waited:
            ITEMS.put(row.model_copy(update={"waited": waited}))

    def forget_applied(self, prefix: tuple[str, ...]) -> None:
        """Forget every item under ``prefix`` (a source, a package or one item): turned off."""
        start = "/".join(prefix)
        for row in ITEMS.all():
            if row.item == start or row.item.startswith(start + "/"):
                ITEMS.delete(row.item)

    def placed(self) -> list[PlacedRecord]:
        return PLACED.all()

    def put_placed(self, record: PlacedRecord) -> None:
        PLACED.put(record)

    def remove_placed(self, target: str) -> bool:
        return PLACED.delete(target) > 0

    def write_status(self, summary: StatusSummary) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self._state_dir / STATUS_FILE, summary.model_dump_json(indent=2) + "\n", mode=0o600
        )
        atomic_write(self._state_dir / ATTENTION_FILE, f"{summary.attention}\n", mode=0o600)
