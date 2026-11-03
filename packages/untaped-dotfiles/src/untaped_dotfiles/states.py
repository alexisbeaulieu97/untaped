"""The state of one placed path, and which states need a person.

:func:`file_state` compares three things: what the source holds now
(:class:`SourceInfo`), what is on the machine (:class:`TargetInfo`) and what
untaped last wrote (:class:`PlacedRecord`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from untaped.sdk import Status
from untaped_dotfiles.models import PlacedRecord
from untaped_dotfiles.package import Mode
from untaped_dotfiles.policy import Policy

FileState = Literal[
    "pending",
    "foreign",
    "applied",
    "behind",
    "modified",
    "conflict",
    "missing",
    "orphan",
    "excluded",
    "error",
]
"""``error``: the path could not be read (a git or filesystem failure); the row's error says why."""

#: How a table draws a :data:`FileState`: waiting, or in trouble.
FILE_STATE_ROLES = Status(warning=("pending",), error=("conflict", "error"))

Action = Literal["apply", "skip", "report", "remove"]
"""What ``apply`` does for one path; ``report`` leaves it for a person."""

ATTENTION: frozenset[str] = frozenset({"behind", "modified", "conflict", "missing", "orphan"})
"""States ``status --check`` counts toward exit 3 (under ``once``: ``missing``, ``orphan``)."""


@dataclass(frozen=True)
class SourceInfo:
    """What the source holds for one placed path."""

    exists: bool
    hash: str | None = None
    """Content hash (``copy``/``merge``); the link destination for ``link``."""
    changed: bool = False
    """``link`` only: the fetched commit changed the path since the one the clone has out."""


@dataclass(frozen=True)
class TargetInfo:
    """What is on the machine at one placed path."""

    kind: Literal["missing", "file", "dir", "symlink", "other"]
    link_to: str | None = None
    hash: str | None = None
    """Content hash of a file; for ``merge``, the hash of the managed keys' values."""


def file_state(
    mode: Mode, record: PlacedRecord | None, source: SourceInfo, target: TargetInfo
) -> tuple[FileState, str]:
    """``(state, detail)`` of one placed path."""
    if not source.exists:
        gone = "the source no longer has it" if record else "the source doesn't have it"
        return "orphan", gone
    if record is None:
        return _unrecorded_state(mode, source, target)
    if target.kind == "missing":
        return "missing", "the target is gone"
    behind = source.changed if mode == "link" else source.hash != record.source_hash
    modified = target_modified(mode, record, target)
    if behind and modified:
        return "conflict", "changed in the source and on this machine"
    if behind:
        return "behind", "a new version is in the source"
    if modified:
        return "modified", _modified_detail(mode, target)
    return "applied", ""


def _unrecorded_state(mode: Mode, source: SourceInfo, target: TargetInfo) -> tuple[FileState, str]:
    if target.kind == "missing":
        return "pending", ""
    if mode == "merge":
        return "pending", "merges into the existing file"
    if mode == "link" and target.kind == "symlink" and target.link_to == source.hash:
        return "pending", "already linked"
    if mode == "copy" and target.kind == "file" and target.hash == source.hash:
        return "pending", "already identical"
    return "foreign", "a file untaped didn't place is in the way; apply keeps it aside"


def target_modified(mode: Mode, record: PlacedRecord, target: TargetInfo) -> bool:
    """Whether the target is no longer what untaped wrote (``remove`` keeps such a file)."""
    if mode == "link":
        return target.kind != "symlink" or target.link_to != record.target_hash
    if target.kind != "file":
        return True
    return target.hash != record.target_hash


def _modified_detail(mode: Mode, target: TargetInfo) -> str:
    if mode == "link":
        return "the link was replaced" if target.kind != "symlink" else "the link points elsewhere"
    if target.kind != "file":
        return f"the target is now a {target.kind}"
    return "edited on this machine" if mode == "copy" else "managed keys edited on this machine"


def needs_attention(policy: Policy, state: FileState) -> bool:
    """Whether a path in ``state`` under ``policy`` needs a person (``status --check`` exits 3)."""
    if state not in ATTENTION:
        return False
    if policy == "once":
        return state in ("missing", "orphan")
    return True
