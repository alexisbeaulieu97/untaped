"""Backup bundle storage and restore."""

from __future__ import annotations

import builtins
import hashlib
import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from untaped.sdk import Absent, Found, OtherVersion, read_data_file, write_data_file
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import CONTENT_ERRORS, FileChange
from untaped_recipe.errors import BackupNotFoundError, LocalChangesError
from untaped_recipe.infrastructure.file_writer import flush_changes

_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
#: A bundle's description, beside its ``files/``.
METADATA_FILE = "metadata.json"


class BackupEntry(BaseModel):
    """One backed-up file: where it was, its hashes, and its saved before-content.

    ``backup_file`` (relative to the bundle) is ``None`` when the apply
    created the file, so a restore deletes it.
    """

    target: str
    relative_path: str
    before_hash: str | None = None
    after_hash: str | None
    backup_file: str | None
    inputs: dict[str, JsonValue] = Field(default_factory=dict)


class BackupMetadata(BaseModel):
    """A bundle's ``metadata.json``: which recipe ran, with what, over which files.

    A data file since 11.1; 11.0's, unstamped, read in the same shape.
    """

    id: str
    created_at: str | None = None
    recipe: str | None = None
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    files: list[BackupEntry]


@dataclass(frozen=True)
class BackupBundle:
    """One created backup bundle."""

    id: str
    path: Path


RestoreAction = Literal["restore", "create", "delete"]


@dataclass(frozen=True)
class RestoreItem:
    """One file-level restore action."""

    path: Path
    action: RestoreAction


@dataclass(frozen=True)
class _PlannedRestore:
    item: RestoreItem
    change: FileChange


@dataclass
class BackupReservation:
    """Backup entries staged before a target write."""

    entries: list[BackupEntry]


@dataclass
class BackupDraft:
    """Incremental backup bundle for one apply invocation."""

    id: str
    path: Path
    recipe_name: str
    inputs: dict[str, object]
    entries: list[BackupEntry] = field(default_factory=list)
    # Stamped once when the bundle is started; commits never move it.
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    _next_file_index: int = 0

    @property
    def files_dir(self) -> Path:
        """Directory containing saved before-content files."""
        return self.path / "files"

    def stage(
        self,
        changes: tuple[FileChange, ...] | list[FileChange],
        *,
        inputs: Mapping[str, object] | None = None,
    ) -> BackupReservation:
        """Save before-content for changes without publishing metadata yet."""
        entries: list[BackupEntry] = []
        display_inputs = dict(inputs or {})
        for change in changes:
            saved = None
            if change.before is not None:
                backup_file = self.files_dir / f"{self._next_file_index}"
                _write_private(backup_file, change.before)
                self._next_file_index += 1
                saved = str(backup_file.relative_to(self.path))
            entries.append(
                BackupEntry.model_validate(
                    {
                        "target": str(change.target),
                        "relative_path": str(change.relative_path),
                        "before_hash": _hash_text(change.before),
                        "after_hash": _hash_text(change.after),
                        "backup_file": saved,
                        "inputs": display_inputs,
                    }
                )
            )
        return BackupReservation(entries=entries)

    def commit(self, reservation: BackupReservation) -> None:
        """Publish staged entries into the bundle metadata."""
        self.entries.extend(reservation.entries)
        self.write_metadata()

    def write_metadata(self) -> None:
        """Write the current metadata snapshot."""
        metadata = BackupMetadata.model_validate(
            {
                "id": self.id,
                "created_at": self.created_at,
                "recipe": self.recipe_name,
                "inputs": self.inputs,
                "files": self.entries,
            }
        )
        write_data_file(self.path / METADATA_FILE, metadata)

    def discard_if_empty(self) -> None:
        """Remove an unused bundle directory."""
        if not self.entries and self.path.exists():
            shutil.rmtree(self.path)


class BackupStore:
    """Create and restore file backups."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def start(self, *, recipe_name: str, inputs: dict[str, object]) -> BackupDraft:
        """Start one invocation-level backup bundle."""
        backup_id = f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        bundle_dir = self._root / backup_id
        files_dir = bundle_dir / "files"
        # Backups hold pre-change file content: owner-only from the start.
        self._root.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        bundle_dir.mkdir(mode=_PRIVATE_DIR_MODE)
        files_dir.mkdir(mode=_PRIVATE_DIR_MODE)
        draft = BackupDraft(
            id=backup_id,
            path=bundle_dir,
            recipe_name=recipe_name,
            inputs=inputs,
        )
        draft.write_metadata()
        return draft

    def list(self) -> list[BackupBundle]:
        """List backup bundles."""
        if not self._root.is_dir():
            return []
        return [
            BackupBundle(id=path.name, path=path)
            for path in sorted(self._root.iterdir(), key=lambda p: p.name)
            if (path / METADATA_FILE).is_file()
        ]

    def restore(self, backup_id: str, *, force: bool = False) -> None:
        """Restore a backup bundle as one staged transaction."""
        planned = self._restore_plan(backup_id, force=force)
        flush_changes(tuple(planned_item.change for planned_item in planned))

    def plan_restore(self, backup_id: str, *, force: bool = False) -> builtins.list[RestoreItem]:
        """Return the file-level restore actions for a backup bundle."""
        return [planned.item for planned in self._restore_plan(backup_id, force=force)]

    def _restore_plan(
        self,
        backup_id: str,
        *,
        force: bool,
    ) -> builtins.list[_PlannedRestore]:
        bundle = self.resolve(backup_id)
        bundle_dir = bundle.path
        planned: builtins.list[_PlannedRestore] = []
        for entry in read_metadata(bundle).files:
            target = Path(entry.target)
            relative_path = Path(entry.relative_path)
            path = confined_path(target, relative_path, field="relative_path")
            current_hash = _current_hash(path)
            if not force and current_hash != entry.after_hash:
                raise LocalChangesError(
                    f"{path} changed since backup {backup_id}; pass --force to restore"
                )
            backup_file = entry.backup_file
            before = (
                path.read_text(encoding="utf-8", errors=CONTENT_ERRORS, newline="")
                if path.is_file()
                else None
            )
            after = (
                None
                if backup_file is None
                else confined_path(bundle_dir, Path(backup_file), field="backup_file").read_text(
                    encoding="utf-8",
                    errors=CONTENT_ERRORS,
                    newline="",
                )
            )
            planned.append(
                _PlannedRestore(
                    item=RestoreItem(path=path, action=_restore_action(path, backup_file)),
                    change=FileChange(
                        target=target,
                        relative_path=relative_path,
                        before=before,
                        after=after,
                    ),
                )
            )
        return planned

    def delete(self, backup_id: str) -> None:
        """Delete one backup bundle by exact id."""
        bundle_dir = self._root / backup_id
        if not (bundle_dir / METADATA_FILE).is_file():
            raise BackupNotFoundError(f"backup not found: {backup_id}")
        shutil.rmtree(bundle_dir)

    def metadata(self, backup_id: str) -> BackupMetadata:
        """Read a backup bundle's metadata."""
        return read_metadata(self.resolve(backup_id))

    def resolve(self, backup_id: str) -> BackupBundle:
        """Resolve an exact id, unique prefix or ``latest`` to its bundle."""
        bundles = self.list()
        if backup_id == "latest":
            if not bundles:
                raise BackupNotFoundError("backup not found: latest")
            return bundles[-1]
        exact = [bundle for bundle in bundles if bundle.id == backup_id]
        if exact:
            return exact[0]
        matches = [bundle for bundle in bundles if bundle.id.startswith(backup_id)]
        if not matches:
            raise BackupNotFoundError(f"backup not found: {backup_id}")
        if len(matches) > 1:
            raise ValueError(f"backup id prefix is ambiguous: {backup_id}")
        return matches[0]


def bundle_bytes(bundle: BackupBundle) -> int:
    """Total on-disk bytes of one bundle's files."""
    return sum(path.stat().st_size for path in bundle.path.rglob("*") if path.is_file())


def bundle_created_at(bundle: BackupBundle) -> datetime | None:
    """Creation time parsed from the bundle id; None when unparsable."""
    stamp = bundle.id.split("-", 1)[0]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def prune_selection(
    bundles: Sequence[BackupBundle],
    *,
    keep: int | None,
    max_age_days: int | None,
    now: datetime,
) -> builtins.list[BackupBundle]:
    """Bundles to prune: outside the newest ``keep`` OR older than the age bound.

    Bundles whose id carries no parseable timestamp cannot be ordered or aged,
    so they are never pruned and do not consume ``keep`` slots.
    """
    datable = [bundle for bundle in bundles if bundle_created_at(bundle) is not None]
    newest_first = sorted(datable, key=lambda bundle: bundle.id, reverse=True)
    pruned: set[str] = set()
    if keep is not None:
        pruned.update(bundle.id for bundle in newest_first[keep:])
    if max_age_days is not None:
        cutoff = now - timedelta(days=max_age_days)
        for bundle in newest_first:
            created = bundle_created_at(bundle)
            if created is not None and created < cutoff:
                pruned.add(bundle.id)
    return [bundle for bundle in sorted(bundles, key=lambda b: b.id) if bundle.id in pruned]


def read_metadata(bundle: BackupBundle) -> BackupMetadata:
    """One bundle's metadata; ``ValueError`` when it cannot be read."""
    match read_data_file(bundle.path / METADATA_FILE, BackupMetadata, unstamped="accept"):
        case Found(value=metadata):
            return metadata
        case Absent():
            reason = "no metadata file"
        case OtherVersion(by=by):
            reason = f"written by a newer untaped ({by or 'unknown version'})"
        case unreadable:
            reason = unreadable.reason
    raise ValueError(f"invalid backup metadata: {bundle.id}: {reason}")


def _write_private(path: Path, content: str) -> None:
    """Create ``path`` owner-only (``0600``) and write ``content`` verbatim."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE)
    with open(fd, "w", encoding="utf-8", errors=CONTENT_ERRORS, newline="") as handle:
        handle.write(content)


def _hash_text(content: str | None) -> str | None:
    if content is None:
        return None
    return _hash_bytes(content.encode("utf-8", CONTENT_ERRORS))


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _current_hash(path: Path) -> str | None:
    if path.is_file():
        return _hash_bytes(path.read_bytes())
    if path.exists():
        return "__untaped_recipe_non_file__"
    return None


def _restore_action(path: Path, backup_file: object) -> RestoreAction:
    if backup_file is None:
        return "delete"
    if path.exists():
        return "restore"
    return "create"
