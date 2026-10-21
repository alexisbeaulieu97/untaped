"""Projects: a folder whose ``.untaped/`` holds ``project.json``.

A project is found by walking up from a folder to the nearest
``.untaped/project.json``; ``~`` and ``~/.untaped`` are never one, and a
``.untaped/`` holding only a plugin's committed files (``lint.yml``) is not
one either. ``untaped project init`` makes a folder a project: it writes
``project.json`` (core's data file, :mod:`untaped.data_file`: its stamp and
``"id": <12 hex digits>``, created exclusively) and a self-ignoring
``.untaped/.gitignore``, and records the project in the home as a reference,
``refs/project-<id>.json`` (:mod:`untaped.refs`), naming the folder and its filesystem identity.

"Same folder" is decided by that identity, ``(st_dev, st_ino)``, never by
comparing path strings: a second spelling, a symlink or a bind mount is the
same project; a ``mv`` on one filesystem keeps the identity (a move) and a
copy doesn't. The first use in a process checks the folder against its
reference and only reports; a writing use (the first ``hold()`` or
``release()``, or a mutating ``project`` command) re-checks under the
project's lock and repairs: it records an unrecorded project, follows a move
(asking each hold's owner to repair what points into the folder), or gives a
copy its own id. What it cannot decide (a cross-filesystem move, a copy
while the original's drive is away) it refuses with exit 4, and never
guesses; ``untaped project move OLD NEW`` is the user saying which it was.

A project holds items other plugins own (:mod:`untaped.holds`):
``project.hold(STORE, item)`` records the hold in its reference, and
``project.release(STORE)`` lets go through the owner.

Each plugin's committed files under ``.untaped/`` are named through
:func:`committed_path`, so every name there is core's or one plugin's.
"""

from __future__ import annotations

import os
import secrets
import threading
from collections.abc import Collection, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from untaped.errors import ConfigError, ErrorCategory, UsageError
from untaped.fs import atomic_write
from untaped.holds import (
    HolderState,
    HolderView,
    HoldOutcome,
    ItemStore,
    UnknownHolder,
    add_hold,
    holder_lock,
    holds_of,
    release_rows,
    repair_holds,
    view,
)
from untaped.messages import shown_path
from untaped.output.sink import Diagnostics
from untaped.refs import (
    PROJECT,
    AnchorId,
    Reference,
    RefHolder,
    RefTarget,
    locked,
    read_reference,
    read_references,
    reference_file,
    refs_dir,
    write_reference,
)
from untaped.settings import DEFAULT_CONFIG_PATH
from untaped.stability import experimental

if TYPE_CHECKING:
    from untaped.plugins.registry import PluginSpec
    from untaped.records import OutcomeRecord

#: The folder a project keeps its untaped files in, at its root.
DIRECTORY = ".untaped"
#: The file that makes a folder a project.
MARKER = "project.json"
#: The lock files core's locked writes leave beside the state and config files.
CORE_LOCKS = ("state.yml.lock", "config.yml.lock")
#: What ``init`` keeps out of version control: the id and machine records, never config.
#: Each is anchored at ``.untaped/``'s top level and named (core's lock files too), so a
#: plugin's committed files (``recipe.lock``, ``awx/plugins/``) stay tracked.
GITIGNORE = ("/project.json", "/state.yml", "/plugins/", *(f"/{name}" for name in CORE_LOCKS))
#: The whole ``.gitignore`` ``init`` creates: those lines and itself.
GITIGNORE_TEXT = "\n".join(("/.gitignore", *GITIGNORE)) + "\n"
#: Folders under ``~/.untaped`` that are core's own, never a project.
_CORE_FOLDERS = ("refs", "plugins")

_diag = Diagnostics()
_SEEN: dict[str, Project] = {}
_SEEN_GUARD = threading.Lock()


@experimental
class Project(BaseModel):
    """A project: a folder whose ``.untaped/`` holds ``project.json``.

    ``id`` is random at init and stays with the folder through moves;
    ``root`` is the folder, resolved; ``copy_of`` is the original's folder
    while this one is a copy not yet given its own id (its first writing use
    gives it one).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: Annotated[str, Field(pattern=r"^[0-9a-f]{12}$")]
    root: Path
    copy_of: Path | None = None

    @property
    def name(self) -> str:
        """The folder's name: how rows name the project (never stored)."""
        return self.root.name

    def locked(self) -> AbstractContextManager[None]:
        """Hold the project's lock for the block: re-entrant; waits 600 s, then exit 5."""
        return holder_lock(holder_of(self), self.name)

    def plugin_dir(self, spec: PluginSpec) -> Path:
        """``<root>/.untaped/plugins/<name>/``: a plugin's machine files in this project."""
        return self.root / DIRECTORY / "plugins" / spec.name

    def hold(self, store: ItemStore[Any], item: object) -> None:
        """Hold ``item`` of ``store`` (an item store a composed plugin lists); idempotent.

        A writing use: the project's identity is re-checked under its lock
        first (a copy gets its own id, a move is followed); in a copy, look
        the project up again (``current_project()``) before using it further.
        Hold, then use: call it before the work that needs the item.
        """
        with self.locked():
            here, _ = writing(self)
            add_hold(holder_of(here), store, item)

    def holds[T: str | RootModel[str]](self, store: ItemStore[T]) -> tuple[T, ...]:
        """The items of ``store`` this project holds; only reads."""
        return holds_of(holder_of(self), store)

    def release(
        self,
        store: ItemStore[Any],
        *,
        items: Collection[object] | None = None,
        force: Collection[object] = (),
        dry_run: bool = False,
    ) -> list[OutcomeRecord]:
        """Let go of this project's holds of ``store`` (or only ``items``) through its owner.

        The service without a prompt, for a plugin's own command that
        previewed and confirmed: one refused or failed plan applies nothing
        and comes back as its row; ``force`` names the items whose refusal
        the user overrides; ``dry_run`` returns the preview and repairs
        nothing. Each row is an ``untaped.hold_outcome`` to emit.
        """
        with self.locked():
            here = self if dry_run else writing(self)[0]
            return release_rows(holder_of(here), store, items=items, force=force, dry_run=dry_run)


@experimental
def committed_path(spec: PluginSpec, name: str, *, root: Path) -> Path:
    """``<root>/.untaped/<name>``: a plugin's files that people write and may commit.

    ``name`` is the plugin's own: its name (``awx``), ``<plugin>.<ext>``
    (``lint.yml``) or a path under ``<plugin>/`` (``lint/house.py``); any
    other name raises :class:`ValueError`, so a plugin never meets core's
    files or another plugin's. ``root`` is the caller's choice: the
    checkout's root, a project's, or ``Path()`` for a path relative to it.
    """
    plugin = spec.name
    rest = name.removeprefix(f"{plugin}/") if name.startswith(f"{plugin}/") else None
    if rest is not None:
        parts = PurePosixPath(rest).parts
        valid = bool(parts) and not rest.startswith("/") and ".." not in parts
    else:
        extension = name.removeprefix(f"{plugin}.") if name.startswith(f"{plugin}.") else None
        valid = name == plugin or (bool(extension) and "/" not in name)
    if not valid or "\\" in name:
        raise ValueError(
            f"{name!r} is not plugin {plugin}'s to name under {DIRECTORY}/: "
            f"use {plugin!r}, '{plugin}.<ext>' or a path under '{plugin}/'"
        )
    return root / DIRECTORY / name


def holder_of(project: Project) -> RefHolder:
    """``project`` as a holder of references: kind ``project``, its id."""
    return RefHolder(kind=PROJECT, name=project.id)


def untaped_home() -> Path:
    """``~/.untaped``: the home folder, never a project."""
    return Path(DEFAULT_CONFIG_PATH).expanduser().parent


def find_root(start: Path) -> Path | None:
    """The nearest folder from ``start`` up whose ``.untaped/`` holds ``project.json``.

    Reads nothing but the marker's presence: no identity check, no line
    printed. ``~``, ``~/.untaped`` and core's folders in it are skipped.
    """
    start = start.expanduser().resolve()
    for folder in (start, *start.parents):
        if _reserved(folder) is None and (folder / DIRECTORY / MARKER).is_file():
            return folder
    return None


@experimental
def project_at(path: Path) -> Project | None:
    """The project ``path`` is in, or ``None``.

    The first call for a project in a process checks its folder against
    its reference and reports what a writing use would do (a copy, a move,
    a project this home doesn't list); it never writes.
    """
    root = find_root(path)
    return None if root is None else _discover(root)


@experimental
def current_project() -> Project:
    """The project the working directory is in; a usage error (exit 2) outside one."""
    cwd = Path.cwd()
    found = project_at(cwd)
    if found is None:
        raise UsageError(
            f"not in a project: no {DIRECTORY}/{MARKER} in {shown_path(cwd.resolve())} or above",
            hint="make this folder a project with `untaped project init`",
        )
    return found


@experimental
def known_projects() -> tuple[HolderView | UnknownHolder, ...]:
    """Every project this home records, in any state; an unreadable record is an UnknownHolder.

    Only reads: a project that holds nothing is listed too.
    """
    return tuple(view(ref) for ref in project_references())


def project_references() -> list[Reference]:
    """Every ``refs/project-*`` reference, readable or not; no other kind."""
    folder = refs_dir()
    return [
        ref
        for ref in read_references()
        if ref.kind == PROJECT
        or (ref.holder is None and ref.file.name.startswith(f"{PROJECT}-"))
        or ref.file == folder  # refs/ can't be listed: any project may be there
    ]


def anchor_state(ref: Reference) -> HolderState:
    """Whether a project reference's folder is ``present``, ``unreadable`` or ``missing``.

    Missing only when the folder (or its id) is gone from a parent that can
    be listed on the recorded device: an ejected drive, an unmounted share
    or another machine's paths read as unreadable, which keeps its holds.
    """
    if ref.anchor is None or ref.anchor_id is None or ref.holder is None:
        return "unreadable"
    try:
        found = _marker_id(ref.anchor)
    except OSError, ValueError:
        return "unreadable"
    if found == ref.holder.name:
        return "present"
    existing = next((p for p in (ref.anchor, *ref.anchor.parents) if os.path.lexists(p)), None)
    if existing is None:
        return "unreadable"
    try:
        device = existing.stat().st_dev
        with os.scandir(existing):
            pass
    except OSError:
        return "unreadable"
    return "missing" if device == ref.anchor_id.dev else "unreadable"


# --- init ------------------------------------------------------------------------


type InitAction = Literal["created", "unchanged", "recorded", "repaired", "new id"]


@dataclass(frozen=True)
class Initialized:
    """What ``project init`` did to one folder."""

    project: Project
    action: InitAction
    detail: str


def init(path: Path, *, new_id: bool = False) -> Initialized:
    """Make ``path`` a project, or bring an existing one's record up to date.

    Refuses ``~``, ``~/.untaped`` and core's folders in it, a folder inside
    another project, and a folder holding a recorded one (exit 2). ``new_id`` gives a
    copy its own id; the folder the reference records keeps its id.
    """
    path = path.expanduser().resolve()
    reserved = _reserved(path)
    if reserved is not None:
        raise UsageError(f"{shown_path(path)} cannot be a project: {reserved}")
    if (path / DIRECTORY / MARKER).is_file():
        return _existing(path, new_id=new_id)
    if new_id:
        raise UsageError(
            f"{shown_path(path)} is not a project yet; --new-id is for a copied project",
            hint="run `untaped project init` without --new-id",
        )
    outer = find_root(path)
    if outer is not None:
        raise UsageError(f"{shown_path(outer)} is already a project; projects don't nest")
    inner = _project_below(path)
    if inner is not None:
        raise UsageError(
            f"{shown_path(inner)} is a project inside {shown_path(path)}; projects don't nest"
        )
    project = Project(id=_new_id(), root=path)
    folder = path / DIRECTORY
    # The lock is held before the marker appears, so a racing init or writing
    # use that reads the new id waits until the project is recorded.
    with project.locked():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            created = _write_marker(folder / MARKER, project.id, exclusive=True)
        except OSError as exc:
            raise ConfigError(
                f"could not make {shown_path(path)} a project: {exc.strerror or exc}"
            ) from exc
        if created:
            _write_gitignore(folder)
            _record(project, path)
    if not created:  # another init won the race: check its project like any other
        return _existing(path, new_id=False)
    _forget(path)
    return Initialized(project, "created", f"{shown_path(path)} is a project (id {project.id})")


def _existing(path: Path, *, new_id: bool) -> Initialized:
    project = _discover(path, report=False)  # init says what it did instead
    if new_id:
        with project.locked():
            situation = _situation(path, project.id)
            if situation.kind in {"ok", "refresh", "moved"}:
                raise UsageError(
                    f"{shown_path(path)} is the folder project {project.name} records; "
                    "--new-id is for a copy of it"
                )
            if situation.kind == "unreadable" or (
                situation.kind == "undecided" and situation.anchor is None
            ):
                # The record can't be read, or this folder can't: it may be the original.
                raise _undecided(project, situation)
            renewed = _renew(path, project.id)
        _forget(path)
        return Initialized(
            renewed, "new id", f"{shown_path(path)} is a project of its own (id {renewed.id})"
        )
    after, before = writing(project)
    was = shown_path(before.anchor or path)
    match before.kind:
        case "unrecorded":
            return Initialized(after, "recorded", f"recorded project {after.name} in this home")
        case "moved":
            return Initialized(
                after, "repaired", f"project {after.name} moved from {was}; its record follows"
            )
        case "copy":
            detail = f"{shown_path(path)} was a copy of {was}; it has its own id ({after.id})"
            return Initialized(after, "new id", detail)
        case _:
            return Initialized(after, "unchanged", f"{shown_path(path)} is already a project")


# --- identity ------------------------------------------------------------------------


type SituationKind = Literal[
    "ok", "refresh", "unrecorded", "unreadable", "copy", "moved", "undecided"
]


@dataclass(frozen=True)
class Situation:
    """How a project's folder stands against its reference.

    ``anchor`` is the folder the reference records; ``reason`` says why an
    ``unreadable`` or ``undecided`` one can't be acted on.
    """

    kind: SituationKind
    reference: Reference | None = None
    anchor: Path | None = None
    reason: str = ""


def writing(project: Project) -> tuple[Project, Situation]:
    """Ready ``project`` for a writing use: re-check under its lock and repair.

    Returns the project now and the situation it acted on, read under the
    lock. Records an unrecorded project, refreshes a changed identity,
    follows a move and gives a copy its own id. Raises :class:`ConfigError`
    (exit 4) when its reference can't be read or it can't tell a move from
    a copy, and (exit 5) when its id changed meanwhile.
    """
    with project.locked():
        try:
            current = _marker_id(project.root)
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"cannot read {shown_path(project.root)}'s project marker: {exc}"
            ) from exc
        if current is None:
            raise ConfigError(f"{shown_path(project.root)} is no longer a project")
        if current != project.id:
            _forget(project.root)
            raise ConfigError(
                f"{shown_path(project.root)}'s id changed while untaped was checking it",
                hint="run the command again",
                category=ErrorCategory.UNAVAILABLE,
            )
        situation = _situation(project.root, current)
        here = Project(id=current, root=project.root)
        ref = situation.reference
        match situation.kind:
            case "ok" | "refresh" if ref is not None and ref.moved_from:
                _warn_unrepaired(here, follow_move(here, moved_from=None, keep=ref))
            case "ok":
                pass
            case "unrecorded":
                _record(here, project.root)
            case "refresh":
                _record(here, project.root, keep=ref)
            case "moved":
                moved_from = situation.anchor or project.root
                _warn_unrepaired(here, follow_move(here, moved_from=moved_from, keep=ref))
            case "copy":
                here = _renew(project.root, current)
            case _:
                raise _undecided(here, situation)
    _forget(project.root)
    return here, situation


def follow_move(
    project: Project, *, moved_from: Path | None, keep: Reference | None
) -> list[HoldOutcome]:
    """Point ``project``'s reference at its folder now, then ask each owner to repair.

    The reference records each folder it moved from (oldest first) until
    every owner's ``moved`` from it succeeded, so a later writing use
    retries; ``moved_from`` is the folder it just left, if any. A broken
    (quarantined) owner keeps it pending too, while one that isn't installed
    has nothing to run. The rows say what each owner did (``repaired``),
    skipped or failed, one per hold. Runs under the project's lock.
    """
    targets = keep.targets if keep is not None else ()
    # Each folder a repair is still pending from, then the one it just left: what wasn't
    # repaired points into the folder it was in then. Hooks are idempotent.
    earlier = keep.moved_from if keep is not None else ()
    sources = [
        path
        for path in dict.fromkeys((*earlier, *([moved_from] if moved_from is not None else [])))
        if path != project.root and not _same(project.root, path)
    ]
    _record(project, project.root, keep=keep, moved_from=sources)
    rows: dict[RefTarget | None, HoldOutcome] = {}
    pending = []
    for source in sources:
        asked, done = repair_holds(holder_of(project), targets, moved_from=source)
        for key, row in asked.items():  # one row per hold: a failure from any folder wins
            if key not in rows or not rows[key].failed:
                rows[key] = row
        if not done:
            pending.append(source)
    if pending != sources:  # read again: a hold taken meanwhile stays
        keep = read_reference(reference_file(holder_of(project)))
        _record(project, project.root, keep=keep, moved_from=pending)
    return list(rows.values())


def _warn_unrepaired(project: Project, rows: Collection[HoldOutcome]) -> None:
    """A failed repair after a move warns; it never changes an unrelated command's exit code."""
    for row in rows:
        if row.failed:
            _diag.warning(
                f"project {project.name} moved, and {row.plugin} could not repair "
                f"{row.item}: {row.detail}",
                hint="the next command that changes the project tries again",
            )


def _discover(root: Path, *, report: bool = True) -> Project:
    key = str(root)
    with _SEEN_GUARD:
        seen = _SEEN.get(key)
    if seen is not None:
        return seen
    try:
        pid = _marker_id(root)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {shown_path(root)}'s project marker: {exc}") from exc
    if pid is None:  # removed between the walk and now
        raise ConfigError(f"{shown_path(root)} is no longer a project")
    situation = _situation(root, pid)
    project = Project(
        id=pid, root=root, copy_of=situation.anchor if situation.kind == "copy" else None
    )
    if report:
        _report(project, situation)
    with _SEEN_GUARD:
        _SEEN[key] = project
    return project


def _situation(root: Path, pid: str) -> Situation:
    holder = RefHolder(kind=PROJECT, name=pid)
    file = reference_file(holder)
    if not os.path.lexists(file):
        return Situation("unrecorded")
    ref = read_reference(file)
    if ref.opaque is not None or ref.anchor is None or ref.anchor_id is None:
        return Situation("unreadable", ref, reason=f"{shown_path(file)} is {ref.opaque}")
    try:
        here = AnchorId.of(root)
    except OSError as exc:
        # No anchor: this folder itself can't be read, so nothing about it is known.
        return Situation("undecided", ref, None, f"{shown_path(root)} can't be read: {exc}")
    if _same(root, ref.anchor):
        return Situation("ok" if here == ref.anchor_id else "refresh", ref, ref.anchor)
    if here == ref.anchor_id:
        return Situation("moved", ref, ref.anchor)
    state = anchor_state(ref)
    if state == "present":
        try:
            original = AnchorId.of(ref.anchor) == ref.anchor_id
        except OSError:
            original = False
        if original:
            return Situation("copy", ref, ref.anchor)
        reason = f"{shown_path(ref.anchor)} holds its id too, and neither is the folder it recorded"
        return Situation("undecided", ref, ref.anchor, reason)
    reason = f"it recorded {shown_path(ref.anchor)} ({state}), and this folder isn't that one"
    return Situation("undecided", ref, ref.anchor, reason)


def _report(project: Project, situation: Situation) -> None:
    here, name = shown_path(project.root), project.name
    match situation.kind:
        case "unrecorded":
            _diag.info(
                f"project {name} ({here}) isn't recorded in this home; "
                "`untaped project init` there records it"
            )
        case "copy":
            original = situation.anchor or project.root
            _diag.info(
                f"{here} is a copy of project {original.name} ({shown_path(original)}); "
                "`untaped project init` there gives it its own id"
            )
        case "moved":
            was = shown_path(situation.anchor or here)
            _diag.info(
                f"project {name} ({here}) moved from {was}; "
                f"`untaped project move {was} {here}` repairs it",
                unmuted=True,
            )
        case "unreadable" | "undecided":
            error = _undecided(project, situation)
            _diag.warning(str(error), hint=error.hint)
        case _:
            pass


def _undecided(project: Project, situation: Situation) -> ConfigError:
    if situation.kind == "unreadable":
        return ConfigError(
            f"cannot read project {project.name}'s record: {situation.reason}",
            hint="upgrade untaped, or fix or remove that file",
        )
    if situation.anchor is None:  # this folder itself can't be read
        return ConfigError(
            f"cannot tell whether project {project.name} moved or was copied: {situation.reason}",
            hint=f"make {shown_path(project.root)} readable, then run the command again",
        )
    here = shown_path(project.root)
    return ConfigError(
        f"cannot tell whether project {project.name} moved or was copied: {situation.reason}",
        hint=f"if it moved here, `untaped project move {shown_path(situation.anchor)} {here}`; "
        "if this is a copy, `untaped project init --new-id` here gives it its own id",
    )


def _record(
    project: Project,
    root: Path,
    *,
    keep: Reference | None = None,
    moved_from: Sequence[Path] = (),
) -> None:
    """Write the project's reference: this folder, its identity, the targets ``keep`` names.

    ``moved_from`` lists the folders whose repairs haven't all succeeded yet.
    """
    try:
        with locked():
            write_reference(
                holder_of(project),
                keep.targets if keep is not None else (),
                anchor=root,
                anchor_id=AnchorId.of(root),
                moved_from=moved_from,
            )
    except OSError as exc:
        raise ConfigError(
            f"{shown_path(root)} is a project, but recording it in {shown_path(refs_dir())} "
            f"failed: {exc.strerror or exc}",
            hint="the next command that changes the project records it",
        ) from exc


def _renew(root: Path, old: str) -> Project:
    """Give the copy at ``root`` its own id: a new ``project.json`` and a reference, no holds."""
    project = Project(id=_new_id(), root=root)
    marker = root / DIRECTORY / MARKER
    if _marker_id(root) != old:
        raise ConfigError(
            f"{shown_path(root)}'s id changed while untaped was giving it a new one",
            hint="run the command again",
            category=ErrorCategory.UNAVAILABLE,
        )
    _write_marker(marker, project.id)
    with project.locked():
        _record(project, root)
    return project


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class ProjectMarker(BaseModel):
    """``project.json``: the project's id, on core's data file."""

    id: str

    @field_validator("id")
    @classmethod
    def _an_id(cls, value: str) -> str:
        if not _valid_id(value):
            raise ValueError("is not a project id (12 lowercase hex digits)")
        return value


def _marker_id(root: Path) -> str | None:
    """The id in ``root``'s ``project.json``; ``None`` when there is none.

    Raises :class:`ValueError` for a file that isn't a project marker, that
    can't be read, or that a newer untaped wrote.
    """
    from untaped.data_file import (  # noqa: PLC0415 - read on first use only
        Absent,
        Found,
        OtherVersion,
        read_data_file,
    )

    marker = root / DIRECTORY / MARKER
    # "1": the string stamp project.json had before core's data file (development builds only)
    match read_data_file(marker, ProjectMarker, legacy_stamps=("1",)):
        case Found(value=found):
            return found.id
        case Absent():
            return None
        case OtherVersion(by=by):
            raise ValueError(f"{shown_path(marker)} was written by a newer untaped ({by})")
        case unreadable:
            raise ValueError(f"{shown_path(marker)} {unreadable.reason}")


def _valid_id(value: str) -> bool:
    return len(value) == 12 and all(char in "0123456789abcdef" for char in value)


def _new_id() -> str:
    return secrets.token_hex(6)


def _write_marker(marker: Path, pid: str, *, exclusive: bool = False) -> bool:
    """Write ``marker`` with ``pid``; with ``exclusive``, only if absent (whether it did)."""
    from untaped.data_file import write_data_file  # noqa: PLC0415 - read on first use only

    return write_data_file(marker, ProjectMarker(id=pid), exclusive=exclusive)


def _write_gitignore(folder: Path) -> None:
    """Write ``.gitignore`` ignoring itself and core's machine files, or add those to one."""
    file = folder / ".gitignore"
    try:
        existing = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        atomic_write(file, GITIGNORE_TEXT)
        return
    lines = existing.splitlines()
    missing = [line for line in GITIGNORE if line not in lines]
    if missing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        atomic_write(file, existing + separator + "\n".join(missing) + "\n")


def _reserved(folder: Path) -> str | None:
    """Why ``folder`` can never be a project, or ``None``."""
    home, untaped = Path.home().resolve(), untaped_home().resolve()
    if folder == home:
        return "it is the home folder"
    if folder == untaped:
        return f"it is {shown_path(untaped)}, untaped's own folder"
    if untaped.is_relative_to(folder):
        return f"it holds {shown_path(untaped)}, untaped's own folder"
    for name in _CORE_FOLDERS:
        if folder.is_relative_to(untaped / name):
            return f"{shown_path(untaped / name)} is untaped's own"
    return None


def _project_below(path: Path) -> Path | None:
    """A recorded project inside ``path``, if any (only recorded ones are seen)."""
    for ref in project_references():
        anchor = ref.anchor
        inside = anchor is not None and anchor != path and anchor.is_relative_to(path)
        if inside and anchor_state(ref) == "present":
            return anchor
    return None


def _forget(root: Path | None = None) -> None:
    """Drop what discovery remembers: for ``root``, or everything (``untaped.testing.plugin``)."""
    with _SEEN_GUARD:
        if root is None:
            _SEEN.clear()
        else:
            _SEEN.pop(str(root), None)


__all__ = ["Project", "committed_path", "current_project", "known_projects", "project_at"]
