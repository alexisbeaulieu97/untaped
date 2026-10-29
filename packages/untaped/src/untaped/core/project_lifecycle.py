"""A project's end of life and its moves: ``untaped project remove|prune|move``.

``remove`` and ``prune`` run the holds service (:mod:`untaped.sdk.storage.holds`) over a
project's holds: each owner plans, the plans are previewed, one refusal
applies nothing, and after confirmation each plan applies on a fresh ask.
When no hold is left, ``remove`` deletes only what core wrote in the folder
(``project.json``, ``plugins/``, its own lock file and an untouched
``.gitignore``) and then the reference; ``config.yml`` and people's files
stay, and so does the folder unless nothing else is in it. ``prune`` does
the same for every project whose folder is gone, keeping those it can't read
now (a drive away).

``move`` moves a project's folder on one filesystem, or, when the folder
was already moved by hand, makes the record follow it (adopting a folder
that has no ``project.json`` any more); each owner then repairs what points
into the folder. Core never copies a project across filesystems.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from untaped.sdk._home import PLUGINS_FOLDER
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import ConfigError, UntapedError, UsageError
from untaped.sdk.projects import (
    CORE_LOCKS,
    DIRECTORY,
    GITIGNORE_TEXT,
    MARKER,
    Project,
    _discover,
    _forget,
    _marker_id,
    _project_below,
    _reserved,
    _same,
    _situation,
    _undecided,
    _write_gitignore,
    _write_marker,
    anchor_state,
    find_root,
    follow_move,
    holder_of,
    project_at,
    project_references,
    writing,
)
from untaped.sdk.storage._refs import (
    PROJECT,
    AnchorId,
    Reference,
    RefHolder,
    locked,
    read_reference,
    reference_file,
)
from untaped.sdk.storage.holds import (
    HolderView,
    HoldOutcome,
    Plan,
    apply_plans,
    holder_lock_file,
    holder_view,
    plan_holds,
    preview_rows,
    view,
)
from untaped.sdk.words import shown_path

#: Core's own files in ``.untaped/`` that ``remove`` deletes (``.gitignore`` only when untouched).
_CORE_FILES = (MARKER,)


@dataclass(frozen=True)
class Removal:
    """One project's planned removal: its holds' plans, and its folder when it is there."""

    holder: RefHolder
    seen: HolderView
    root: Path | None
    plans: list[Plan] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.seen.anchor.name

    def preview(self) -> list[HoldOutcome]:
        """The rows before anything is applied: each hold's, then the project's own."""
        if self.root is not None:
            deletes, keeps = core_files(self.root)
            detail = f"deletes {', '.join(deletes) or 'nothing of core'}"
            if keeps:
                detail += f"; keeps {', '.join(keeps)} (yours)"
            elif any(name != DIRECTORY for name in _entries(self.root)):
                detail += "; keeps the folder (it holds your files)"
            else:
                detail += "; then the folder, which holds nothing else"
        else:
            detail = f"forgets the project: {shown_path(self.seen.anchor)} is gone"
        return [*preview_rows(self.plans), self._row("planned", detail)]

    def _row(self, action: str, detail: str, error: Failure | None = None) -> HoldOutcome:
        return HoldOutcome(
            holder=self.seen.label,
            plugin="untaped",
            item=str(self.root or self.seen.anchor),
            action=action,
            detail=detail,
            error=error,
        )


def plan_removal(project_or_ref: Project | Reference, *, force: Collection[str]) -> Removal:
    """Plan removing a project: present (``Project``) or recorded only (its ``Reference``).

    Call it under the project's lock; it changes nothing.
    """
    if isinstance(project_or_ref, Project):
        project = project_or_ref
        holder = holder_of(project)
        # The project as a writing use leaves it: here, present (a preview of a moved project
        # asks its owners what the real run, which follows the move first, will ask).
        seen = HolderView(
            holder=holder, label=f"project {project.name}", anchor=project.root, state="present"
        )
        if project.copy_of is not None:  # a copy not yet given its own id holds nothing of its own
            return Removal(holder, seen, project.root)
        file = reference_file(holder)
        if not file.exists():  # an unrecorded project holds nothing
            return Removal(holder, seen, project.root)
        ref = read_reference(file)
        if ref.opaque is not None:
            raise ConfigError(f"cannot read project {project.name}'s record: {ref.opaque}")
        return Removal(holder, seen, project.root, plan_holds(seen, ref.targets, force=force))
    if project_or_ref.holder is None:
        raise ConfigError(f"cannot read the project record {shown_path(project_or_ref.file)}")
    if not project_or_ref.file.exists():  # removed since it was listed (another prune)
        raise UntapedError(f"{project_or_ref.describe()} was already removed")
    ref = read_reference(project_or_ref.file)  # read again: the caller holds the lock now
    found = view(ref)
    if not isinstance(found, HolderView) or ref.holder is None:
        raise ConfigError(f"cannot read the project record {shown_path(ref.file)}")
    seen = found
    if seen.state == "unreadable":
        raise ConfigError(
            f"{seen.label}: {shown_path(seen.anchor)} can't be read now, so it keeps what it holds",
            hint="mount the drive or share it is on, then run the command again",
        )
    if seen.state == "present":
        raise UntapedError(
            f"{seen.label} is at {shown_path(seen.anchor)} again, so it isn't removed",
            hint="run the command again",
        )
    return Removal(ref.holder, seen, None, plan_holds(seen, ref.targets, force=force))


def preview_check(project: Project) -> None:
    """For a preview (``--dry-run``, ``--check``): refuse what a writing use would refuse."""
    try:
        pid = _marker_id(project.root)
    except ValueError as exc:
        raise ConfigError(
            f"cannot read {shown_path(project.root)}'s project marker: {exc}"
        ) from exc
    if pid is None:
        raise ConfigError(f"{shown_path(project.root)} is no longer a project")
    situation = _situation(project.root, pid)
    if situation.kind in {"unreadable", "undecided"}:
        raise _undecided(project, situation)


def apply_removal(removal: Removal) -> list[HoldOutcome]:
    """Apply every plan, then, when no hold is left, delete core's files and the record."""
    rows = apply_plans(removal.holder, removal.plans, now=lambda: holder_view(removal.holder))
    left = _holds_left(removal.holder)
    if any(row.action in {"failed", "refused"} for row in rows) or left:
        detail = f"the project stays: it still holds {left} item{'s' if left != 1 else ''}"
        return [*rows, removal._row("skipped", detail)]
    try:
        kept = _delete_core_files(removal.root) if removal.root is not None else []
        with locked(), suppress(FileNotFoundError):
            reference_file(removal.holder).unlink()
    except OSError as exc:
        failure = Failure.plain(f"could not remove project {removal.name}: {exc.strerror or exc}")
        return [*rows, removal._row("failed", failure.message, failure)]
    if removal.root is not None:
        _forget(removal.root)
        where = shown_path(removal.root)
        detail = (
            f"removed the project; kept {where}: {', '.join(kept)}"
            if kept
            else (f"removed the project and {where}, which held nothing else")
        )
    else:
        detail = "removed the project's record"
    return [*rows, removal._row("removed", detail)]


def unlink_lock(holder: RefHolder) -> None:
    """Remove a removed project's lock file; call it once the lock is let go."""
    if not reference_file(holder).exists():
        with suppress(OSError):
            holder_lock_file(holder).unlink()


def core_files(root: Path) -> tuple[list[str], list[str]]:
    """What ``remove`` would delete in ``root``'s ``.untaped/``, and what it keeps there."""
    folder = root / DIRECTORY
    try:
        names = sorted(entry.name for entry in folder.iterdir())
    except OSError:
        return [], []
    deletes, keeps = [], []
    for name in names:
        path = folder / name
        core_file = name in _CORE_FILES or name in CORE_LOCKS or name == ".gitignore"
        if name == PLUGINS_FOLDER and path.is_dir() and not path.is_symlink():
            deletes.append(f"{name}/")
        elif core_file and path.is_file() and (name != ".gitignore" or _untouched(folder)):
            deletes.append(name)
        else:
            keeps.append(name)
    return deletes, keeps


def _delete_core_files(root: Path) -> list[str]:
    """Delete core's files in ``root/.untaped``, then empty folders; what stays, by name."""
    folder = root / DIRECTORY
    deletes, _ = core_files(root)
    marker = folder / MARKER
    for name in deletes:
        path = folder / name.rstrip("/")
        if path == marker:
            continue
        if name.endswith("/"):  # core's plugins/ folder, checked a real folder above
            shutil.rmtree(path)
        else:
            with suppress(FileNotFoundError):
                path.unlink()
    with suppress(FileNotFoundError):
        marker.unlink()  # last: until here, the folder is still the project
    with suppress(OSError):
        folder.rmdir()  # only when empty
    with suppress(OSError):
        root.rmdir()
    if not root.exists():
        return []
    return [*_entries(folder), *(name for name in _entries(root) if name != DIRECTORY)]


def _entries(folder: Path) -> list[str]:
    try:
        return sorted(entry.name for entry in folder.iterdir())
    except OSError:
        return []


def _untouched(folder: Path) -> bool:
    try:
        text = (folder / ".gitignore").read_text(encoding="utf-8")
    except OSError:
        return False
    return text == GITIGNORE_TEXT


def _holds_left(holder: RefHolder) -> int:
    file = reference_file(holder)
    if not file.exists():
        return 0
    ref = read_reference(file)
    return sum(1 for target in ref.targets if target.item is not None)


# --- finding a project by name or path ----------------------------------------------------


def find_project(target: str) -> Project | Reference:
    """The project ``target`` names: a folder (in a project), or a recorded folder or name.

    A folder that exists is looked up by walking up from it; a gone one, or
    a name, is matched against the records (a name by its folder's name).
    Raises :class:`UsageError` when nothing or more than one record matches.
    """
    path = Path(target).expanduser()
    by_path = os.sep in target or target.startswith(("~", "."))
    if os.path.lexists(path):
        found = project_at(path)
        if found is not None and found.root == path.resolve():
            return found
        if by_path or not _named(target):
            where = shown_path(path.resolve())
            raise UsageError(
                f"{where} is not a project's folder"
                + (f" (it is inside project {found.name})" if found is not None else ""),
                hint="name the project's own folder, or its name",
            )
    wanted = _real(path)
    matches = [
        ref
        for ref in project_references()
        if ref.anchor is not None
        and (ref.anchor == wanted if by_path else ref.anchor.name == target)
    ]
    if not matches:
        raise UsageError(
            f"no project recorded at {shown_path(wanted)}"
            if by_path
            else f"no project named {target!r}",
            hint="`untaped project list` shows the projects this home records",
        )
    if len(matches) > 1:
        where = ", ".join(shown_path(ref.anchor) for ref in matches if ref.anchor is not None)
        raise UsageError(
            f"{len(matches)} projects are named {target!r}: {where}", hint="name one by its path"
        )
    ref = matches[0]
    if ref.anchor is not None and anchor_state(ref) == "present":
        present = project_at(ref.anchor)
        if present is not None:
            return present
    return ref


def _named(name: str) -> bool:
    return any(ref.anchor is not None and ref.anchor.name == name for ref in project_references())


def _real(path: Path) -> Path:
    """``path`` absolute, its existing part resolved as the records are (``/tmp`` on macOS)."""
    return Path(os.path.realpath(path.expanduser()))


def missing_projects() -> tuple[list[Reference], list[Reference]]:
    """The recorded projects whose folder is gone, and those that can't be read now."""
    missing, unreadable = [], []
    for ref in project_references():
        if ref.opaque is not None or ref.anchor is None or ref.holder is None:
            continue
        state = anchor_state(ref)
        if state == "missing":
            missing.append(ref)
        elif state == "unreadable":
            unreadable.append(ref)
    return missing, unreadable


# --- move ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """What ``project move OLD NEW`` will do: ``rename``, ``follow`` a hand move, or ``adopt``."""

    how: str
    project: Project
    old: Path
    new: Path
    reference: Reference | None

    def preview(self) -> list[HoldOutcome]:
        detail = {
            "rename": f"moves {shown_path(self.old)} here; its reference follows",
            "follow": f"its reference follows it here from {shown_path(self.old)}",
            "adopt": f"writes {DIRECTORY}/{MARKER} with id {self.project.id} here; "
            f"its reference follows from {shown_path(self.old)}",
        }[self.how]
        rows = [self._row("planned", detail)]
        for target in self.reference.targets if self.reference is not None else ():
            if target.item is not None:
                rows.append(
                    HoldOutcome(
                        holder=f"project {self.new.name}",
                        plugin=target.plugin,
                        item=target.item,
                        action="planned",
                        detail=f"asks {target.plugin} to repair what points into the folder",
                    )
                )
        return rows

    def _row(self, action: str, detail: str) -> HoldOutcome:
        return HoldOutcome(
            holder=f"project {self.new.name}",
            plugin="untaped",
            item=str(self.new),
            action=action,
            detail=detail,
        )


def plan_move(old: Path, new: Path) -> Move:
    """Decide how ``project move OLD NEW`` goes; raises when it can't (exit 1 or 2)."""
    old = _absolute(old)
    new = _absolute(new)
    if new == old or _same(old, new):
        raise UsageError("OLD and NEW are the same folder")
    present = os.path.lexists(old)
    if present and old.is_symlink():
        raise UsageError(f"{shown_path(old)} is a symlink; name the project's folder itself")
    if present:
        if find_root(old) != old.resolve():
            raise UsageError(f"{shown_path(old)} is not a project's folder")
        project = _discover(old.resolve(), report=False)
    else:
        project = _recorded_at(old, new)
    reference = _reference(project.id)
    if os.path.lexists(new):
        return _onto(new.resolve(), project, old, reference, present=present)
    if not present:
        raise UntapedError(
            f"neither {shown_path(old)} nor {shown_path(new)} exists; nothing moved",
            hint="if you moved the folder by hand, name where it is now as NEW",
        )
    _check_rename(old.resolve(), new)
    return Move("rename", project, old.resolve(), new, reference)


def _onto(
    new: Path, project: Project, old: Path, reference: Reference | None, *, present: bool
) -> Move:
    """NEW exists: it holds this project already (``follow``), or a folder to adopt."""
    try:
        there = _marker_id(new)
    except (OSError, ValueError) as exc:
        raise UntapedError(f"{shown_path(new)} holds a damaged {MARKER}: {exc}") from exc
    if there is not None and there != project.id:
        raise UntapedError(f"{shown_path(new)} is another project; nothing moved")
    moved = Project(id=project.id, root=new)
    if there == project.id:
        if present:
            _check_not_copy(old.resolve(), new, project)
        return Move("follow", moved, old, new, reference)
    if present:
        raise UntapedError(f"{shown_path(new)} already exists; nothing moved")
    _check_adoptable(new)
    return Move("adopt", moved, old, new, reference)


def _check_not_copy(old: Path, new: Path, project: Project) -> None:
    """Both folders hold the id: refuse when the record's identity tells the original.

    The original is the folder with the recorded identity: OLD or NEW, or
    the recorded folder itself (then both are copies). When no folder has
    it, the record can't tell, and the move is the user's answer.
    """
    hint = "`untaped project init --new-id` in the copy gives it its own id"
    kinds = {path: _situation(path, project.id) for path in (old, new)}
    anchor = kinds[new].anchor
    name = anchor.name if anchor is not None else project.name  # as its record names it
    for original, copy in ((new, old), (old, new)):
        if kinds[original].kind in {"ok", "moved"}:
            raise UntapedError(
                f"{shown_path(copy)} is a copy of project {name} at {shown_path(original)}; "
                "nothing moved",
                hint=hint,
            )
    if anchor is not None and "copy" in {kinds[old].kind, kinds[new].kind}:
        raise UntapedError(
            f"{shown_path(old)} and {shown_path(new)} are copies of project {name} "
            f"at {shown_path(anchor)}; nothing moved",
            hint=hint,
        )


def _check_rename(old: Path, new: Path) -> None:
    if _device(old) != _device(new.parent):
        raise UntapedError(
            f"{shown_path(old)} and {shown_path(new)} are on different filesystems; "
            "untaped never copies a project",
            hint=f"move it yourself (`mv`), then run `untaped project move {shown_path(old)} "
            f"{shown_path(new)}`",
        )
    if new.is_relative_to(old):
        raise UsageError(f"{shown_path(new)} is inside {shown_path(old)}")
    reserved = _reserved(new)
    if reserved is not None:
        raise UsageError(f"{shown_path(new)} cannot be a project: {reserved}")
    outer = find_root(new.parent)
    if outer is not None:
        raise UsageError(f"{shown_path(outer)} is a project; projects don't nest")


def apply_move(move: Move) -> list[HoldOutcome]:
    """Do the move under the project's lock; the project's row, then each owner's repair."""
    project, old = move.project, move.old
    with project.locked():
        if move.how == "rename":
            here, _ = writing(project)  # a copy gets its own id first; a move is followed
            try:
                move.new.parent.mkdir(parents=True, exist_ok=True)
                os.rename(here.root, move.new)
            except OSError as exc:
                raise UntapedError(
                    f"could not move {shown_path(old)}: {exc.strerror or exc}"
                ) from exc
            moved = Project(id=here.id, root=move.new.resolve())
            old = here.root
            detail = "moved the folder; its reference follows"
        else:
            if move.how == "adopt":
                folder = move.new / DIRECTORY
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    if (
                        not _write_marker(folder / MARKER, project.id, exclusive=True)
                        and _marker_id(move.new) != project.id
                    ):
                        raise UntapedError(
                            f"{shown_path(move.new)} became another project; nothing moved"
                        )
                    _write_gitignore(folder)
                except OSError as exc:
                    raise ConfigError(
                        f"could not make {shown_path(move.new)} the project: {exc.strerror or exc}"
                    ) from exc
                except ValueError as exc:  # its project.json can't be read
                    raise ConfigError(
                        f"could not make {shown_path(move.new)} the project: {exc}"
                    ) from exc
            moved = project
            detail = (
                "adopted the folder; its reference follows"
                if move.how == "adopt"
                else ("its reference follows the folder")
            )
        keep = _reference(moved.id)
        rows = follow_move(moved, moved_from=old, keep=keep)
    _forget(old)
    _forget(moved.root)
    return [move._row("moved" if move.how == "rename" else "repaired", detail), *rows]


def _recorded_at(old: Path, new: Path) -> Project:
    """The project recorded at a gone OLD, or whose repair from OLD is pending.

    A pending repair is retried only by following onto the folder the record
    names (or that folder moved by hand again): any other NEW would take the
    record from it.
    """
    real = _real(old)
    refs = [ref for ref in project_references() if ref.holder is not None]
    at = [ref for ref in refs if ref.anchor == real]
    if at and at[0].holder is not None:
        return Project(id=at[0].holder.name, root=old)
    pending = [ref for ref in refs if real in ref.moved_from]
    holder, anchor = (pending[0].holder, pending[0].anchor) if pending else (None, None)
    if holder is None or anchor is None:
        raise UsageError(
            f"no project recorded at {shown_path(old)}",
            hint="`untaped project list` shows the projects this home records",
        )
    recorded = os.path.lexists(new) and _situation(new.resolve(), holder.name).kind in {
        "ok",
        "refresh",
        "moved",  # moved by hand again since
    }
    if not recorded:
        here = os.path.lexists(anchor)
        raise UntapedError(
            f"project {anchor.name} moved from {shown_path(old)} to {shown_path(anchor)}; "
            "nothing moved",
            hint=f"`untaped project move {shown_path(old)} {shown_path(anchor)}` retries its repair"
            if here
            else f"`untaped project move {shown_path(anchor)} NEW`, NEW where it is now, "
            "retries its repair",
        )
    return Project(id=holder.name, root=old)


def _reference(pid: str) -> Reference | None:
    file = reference_file(RefHolder(kind=PROJECT, name=pid))
    if not file.exists():
        return None
    ref = read_reference(file)
    if ref.opaque is not None:
        raise ConfigError(
            f"cannot read project record {shown_path(file)}: {ref.opaque}",
            hint="upgrade untaped, or fix or remove that file",
        )
    return ref


def _check_adoptable(new: Path) -> None:
    reserved = _reserved(new)
    if reserved is not None:
        raise UsageError(f"{shown_path(new)} cannot be a project: {reserved}")
    outer = find_root(new)
    if outer is not None:
        raise UsageError(f"{shown_path(outer)} is already a project; projects don't nest")
    inner = _project_below(new)
    if inner is not None:
        raise UsageError(
            f"{shown_path(inner)} is a project inside {shown_path(new)}; projects don't nest"
        )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _device(path: Path) -> int | None:
    for folder in (path, *path.parents):
        with suppress(OSError):
            return AnchorId.of(folder).dev
    return None


def blocked_hint(plans: Sequence[Plan]) -> str:
    """The hint after a refusal: each row says what to do; ``--force`` names what it overrides."""
    forceable = [plan.item for plan in plans if plan.blocked and plan.forceable]
    if not forceable:
        return "do what each refused row's hint says, then run it again"
    names = " ".join(f"--force {item}" for item in forceable)
    return f"do what each refused row's hint says, or name the items to override: {names}"
