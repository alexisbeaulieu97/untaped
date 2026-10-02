"""The state of one placed path, and what each policy does about it.

:func:`file_state` compares three things: what the repo holds now
(:class:`SourceInfo`), what is on the machine (:class:`TargetInfo`) and what
the tool last wrote (:class:`AppliedRecord`). :func:`sync_action` is the
policy table: ``sync`` only ever writes a target that is absent, foreign or
still exactly what the tool wrote, so it never loses a local edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from untaped_dotfiles.domain.models import AppliedRecord, Mode, Policy

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

Action = Literal["apply", "skip", "report", "remove"]

ATTENTION: frozenset[str] = frozenset({"behind", "modified", "conflict", "missing", "orphan"})
"""States ``sync`` and ``status --check`` count toward exit 3 (``behind`` only when not synced)."""


@dataclass(frozen=True)
class SourceInfo:
    """What the repo holds for one placed path."""

    exists: bool
    hash: str | None = None
    """Content hash (``copy``/``merge``); the link destination for ``link``."""
    changed: bool = False
    """``link`` only: the fetched ref changed the path since the working tree's commit."""


@dataclass(frozen=True)
class TargetInfo:
    """What is on the machine at one placed path."""

    kind: Literal["missing", "file", "dir", "symlink", "other"]
    link_to: str | None = None
    hash: str | None = None
    """Content hash of a file; for ``merge``, the hash of the managed keys' values."""


def file_state(
    mode: Mode, record: AppliedRecord | None, source: SourceInfo, target: TargetInfo
) -> tuple[FileState, str]:
    """``(state, detail)`` of one placed path."""
    if not source.exists:
        gone = "the source is gone from the repo" if record else "the source is not in the repo"
        return "orphan", gone
    if record is None:
        return _unrecorded_state(mode, source, target)
    if target.kind == "missing":
        return "missing", "the target is gone"
    behind = source.changed if mode == "link" else source.hash != record.source_hash
    modified = target_modified(mode, record, target)
    if behind and modified:
        return "conflict", "changed in the repo and on this machine"
    if behind:
        return "behind", "a new version is in the repo"
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
    return "foreign", "a file the tool did not place is in the way; apply keeps it aside"


def target_modified(mode: Mode, record: AppliedRecord, target: TargetInfo) -> bool:
    """Whether the target is no longer what the tool wrote (``remove`` keeps such a file)."""
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


def sync_action(policy: Policy, state: FileState) -> Action:
    """What ``sync`` does for a path in ``state`` under ``policy`` (the policy table)."""
    if policy == "manual":
        return "skip" if state == "applied" else "report"
    if state in ("pending", "foreign"):
        return "apply"
    if policy == "sync":
        if state in ("behind", "missing"):
            return "apply"
        if state == "orphan":
            return "remove"
        return "skip" if state == "applied" else "report"
    # once: apply once, then leave alone; a vanished source or target is still worth a word
    return "report" if state in ("missing", "orphan") else "skip"


def needs_attention(policy: Policy, state: FileState) -> bool:
    """Whether a path in ``state`` under ``policy`` counts toward exit 3 after ``sync``."""
    if state not in ATTENTION:
        return False
    if policy == "once":
        return state in ("missing", "orphan")
    return True
