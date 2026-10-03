"""Which files of an enabled item apply on this machine, and why the others do not."""

from __future__ import annotations

from collections.abc import Iterator

from untaped_dotfiles.domain.manifest import FileEntry, Item
from untaped_dotfiles.domain.models import Machine, Mode, Policy


def _filter_reason(
    machine: Machine, *, os: tuple[str, ...], only: tuple[str, ...], unless: tuple[str, ...]
) -> str | None:
    if os and machine.os not in os:
        return f"os is {machine.os}, not {' or '.join(os)}"
    if only and not machine.tags.intersection(only):
        return f"needs tag {' or '.join(sorted(only))}"
    hit = machine.tags.intersection(unless)
    if hit:
        return f"excluded by tag {', '.join(sorted(hit))}"
    return None


def item_exclusion(item: Item, machine: Machine) -> str | None:
    """Why the whole item does not apply on ``machine``, or ``None``."""
    return _filter_reason(machine, os=item.os, only=item.only, unless=item.unless)


def file_exclusion(
    item: Item, entry: FileEntry, machine: Machine, *, skip: tuple[str, ...] = ()
) -> str | None:
    """Why ``entry`` does not apply on ``machine``, or ``None`` when it does."""
    reason = item_exclusion(item, machine)
    if reason is not None:
        return reason
    if entry.key in skip:
        return "skipped on this machine"
    return _filter_reason(machine, os=entry.os, only=entry.only, unless=entry.unless)


def selected_files(
    item: Item, machine: Machine, *, skip: tuple[str, ...] = ()
) -> Iterator[tuple[FileEntry, str | None]]:
    """Every file of ``item`` with its exclusion reason (``None`` when it applies)."""
    for entry in item.files:
        yield entry, file_exclusion(item, entry, machine, skip=skip)


def effective_mode(mode: Mode, policy: Policy) -> Mode:
    """The mode a file is placed with: ``once`` places ``link`` files as copies.

    A symlink cannot be a snapshot, and a held-back clone would freeze every
    ``sync`` item on it without a word; a copy is what ``once`` means.
    """
    return "copy" if mode == "link" and policy == "once" else mode
