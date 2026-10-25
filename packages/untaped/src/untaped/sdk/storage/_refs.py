"""References: who still uses a store's layout, one file per holder in core's ``refs`` folder.

A holder declares what it uses; the store decides when an unheld layout goes
(:meth:`untaped.sdk.storage.store.Store.prune`). 11.1 writes one kind of holder, a
registered install: ``untaped setup register`` writes
``refs/install-<id>.json`` naming the install's environment (the directory
holding untaped's own distribution, whatever installed it), its version and
the layout of every store its composition declares, and each later run of
that install refreshes the file on its first store open. A reference is live
while its anchor still holds an ``untaped-*.dist-info``, or, with no anchor,
until its holder removes it. A project (:mod:`untaped.sdk.projects`) writes
``refs/project-<id>.json`` naming its folder and the folder's filesystem
identity (``anchor_id``); it is always live here, since only its own service
decides when a project is gone. Install and project references are read
strictly: a target this untaped cannot read makes the file opaque, never
silently shorter. A reference of a kind this untaped does not know (a later
untaped's) is always live and holds the targets it can read, and nothing in
it pauses cleanup (a newer file format still does). A dead
reference counts for nothing, but its file is never another process's to
delete: ``setup register --remove`` or the previewed ``setup migrate-dirs``
row removes it.

Each target is a :class:`RefTarget`, written as a JSON object: a layout of a
store, or one item of a store that keeps one per item. No clock is read
anywhere.

The folder is core's block :data:`REFS`, ``plugins/untaped/refs/`` under the
home, and each file a data file (:class:`~untaped.sdk.storage.data_file.DataFile`)
whose stamp names its format; every writer and every pruner holds the
block's lock. ``Core`` holds the block as ``refs``, but its readers (every
store) have no core class in hand, so it is bound where it is declared.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from untaped.sdk._home import CORE
from untaped.sdk.diagnostic import _is_none
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.marks import experimental
from untaped.sdk.run import NoRunError
from untaped.sdk.storage.data_file import Absent, DataFile, OtherVersion, Unreadable
from untaped.sdk.storage.dir import Dir

if TYPE_CHECKING:
    from untaped.sdk.storage.store import Store

_LOG = logging.getLogger("untaped.refs")

#: The holder kind of a registered install.
INSTALL = "install"
#: The holder kind of a project (:mod:`untaped.sdk.projects`).
PROJECT = "project"
#: The kinds this untaped writes, read strictly.
_OWN_KINDS = frozenset({INSTALL, PROJECT})
_STATE = threading.Lock()
_refreshed = False

#: Core's ``refs``: the folder of reference files, and the lock every writer and pruner
#: holds, waiting up to ten seconds. ``Core.refs`` holds it.
REFS = Dir(wait=timedelta(seconds=10))._bound_to(CORE, "refs")


@experimental
class RefTarget(BaseModel):
    """What a reference keeps: one ``layout`` of a store, or one ``item`` in it.

    ``RefTarget(plugin="acme", store="catalogue", layout=2)`` keeps a layout;
    ``RefTarget(plugin="git", store="store", item="github.com/acme/pay")`` one
    item of a store that keeps one per item. Exactly one of the two is set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    plugin: Annotated[str, Field(min_length=1)]
    store: Annotated[str, Field(min_length=1)]
    layout: Annotated[int | None, Field(ge=0, exclude_if=_is_none)] = None
    item: Annotated[str | None, Field(exclude_if=_is_none)] = None

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.layout is None) == (self.item is None):
            raise ValueError("a target names exactly one of layout or item")
        return self

    def describe(self) -> str:
        """How a row names it: ``acme.catalogue layout 2`` or ``git.store item <item>``."""
        what = f"layout {self.layout}" if self.item is None else f"item {self.item}"
        return f"{self.plugin}.{self.store} {what}"


@experimental
class RefHolder(BaseModel):
    """Who a reference is for: its ``kind`` and its ``name`` within that kind.

    A registered install is ``RefHolder(kind="install", name=<12 hex digits>)``;
    a later untaped may write other kinds (a project, say). Its file is
    ``refs/<kind>-<name>.json``, as is: both are lower case and a kind has no
    ``-``, so no two holders share a file, even on a case-insensitive disk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]
    name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=128)]

    def describe(self) -> str:
        """How a row names it: ``project pay``."""
        return f"{self.kind} {self.name}"


class AnchorId(BaseModel):
    """A folder's filesystem identity, ``(st_dev, st_ino)``: the same folder under any spelling."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dev: int
    ino: int

    @classmethod
    def of(cls, path: Path) -> AnchorId:
        """The identity of ``path`` (following symlinks); raises :class:`OSError`."""
        found = path.stat()
        return cls(dev=found.st_dev, ino=found.st_ino)


class _Stored(BaseModel):
    """A reference file's fields as stored, each read as any JSON: :func:`read_reference` judges.

    A holder of a kind this untaped writes is read strictly and any other
    kind tolerantly, so the shape of each field is decided there, not here.
    """

    holder: JsonValue = None
    anchor: Annotated[JsonValue, Field(exclude_if=_is_none)] = None
    anchor_id: Annotated[JsonValue, Field(exclude_if=_is_none)] = None
    moved_from: Annotated[JsonValue, Field(exclude_if=_is_none)] = None
    plugin: Annotated[JsonValue, Field(exclude_if=_is_none)] = None
    version: Annotated[JsonValue, Field(exclude_if=_is_none)] = None
    targets: JsonValue = None


@dataclass(frozen=True)
class Reference:
    """One holder's file, as read.

    ``opaque`` says why it cannot be used (unreadable, or written by a newer
    untaped): an opaque reference counts as live and pauses pruning.
    ``anchor_id`` is the anchor's filesystem identity when the holder records
    one (a project does); ``moved_from`` is where the holder was before each
    move whose repairs haven't all succeeded yet, oldest first.
    """

    file: Path
    holder: RefHolder | None = None
    anchor: Path | None = None
    anchor_id: AnchorId | None = None
    moved_from: tuple[Path, ...] = ()
    plugin: str | None = None
    version: str | None = None
    targets: tuple[RefTarget, ...] = ()
    opaque: str | None = None

    @property
    def kind(self) -> str:
        """The holder's kind, ``""`` when the file names no readable holder."""
        return "" if self.holder is None else self.holder.kind

    @property
    def live(self) -> bool:
        """Whether it still counts: opaque, not an install, anchorless, or its anchor holds untaped.

        An anchor that exists but cannot be listed counts as live. Only an
        install's liveness is known here; a project, whatever its state, and
        a later untaped's kind of holder are never judged dead, so never
        offered for removal.
        """
        if self.opaque is not None or self.kind != INSTALL or self.anchor is None:
            return True
        versions = _installed_versions(self.anchor)
        return versions is None or bool(versions)

    @property
    def pre_upgrade(self) -> bool:
        """A live install whose directory now holds another untaped version than it records."""
        if self.kind != INSTALL or self.anchor is None or self.opaque is not None:
            return False
        if self.version is None:
            return False
        versions = _installed_versions(self.anchor)
        return bool(versions) and versions is not None and self.version not in versions

    def layouts(self, store: Store) -> set[int]:
        """The layouts of ``store`` this reference names."""
        return {
            target.layout
            for target in self.targets
            if (target.plugin, target.store) == (store.plugin, store.name)
            and target.layout is not None
        }

    def describe(self) -> str:
        """How a row names the holder: ``untaped 11.1.0 at <env>``, or the holder itself."""
        from untaped.sdk.words import shown_path  # noqa: PLC0415 - no cycle

        if self.kind == INSTALL and self.anchor is not None:
            return f"untaped {self.version or '?'} at {shown_path(self.anchor)}"
        if self.kind == PROJECT and self.anchor is not None:
            return f"project {self.anchor.name}"
        return self.file.name if self.holder is None else self.holder.describe()


def environment() -> Path | None:
    """The directory holding untaped's own distribution, or ``None`` (run from a source tree).

    The standard library's answer, the same under every installer; nothing
    else is read (never ``sys.prefix``, never an installer's layout).
    """
    try:
        located = distribution("untaped").locate_file("")
    except PackageNotFoundError:
        return None
    return Path(os.path.abspath(str(located)))


def install_holder(env: Path) -> RefHolder:
    """The holder an install at ``env`` writes: kind ``install``, 12 hex digits of its path."""
    return RefHolder(kind=INSTALL, name=hashlib.sha256(str(env).encode()).hexdigest()[:12])


def reference_file(holder: RefHolder) -> Path:
    """``refs/<kind>-<name>.json``: the file a holder's reference lives in."""
    return REFS.place / f"{holder.kind}-{holder.name}.json"


def this_install() -> Reference | None:
    """This install's own reference when it is registered, else ``None``."""
    env = environment()
    if env is None:
        return None
    file = reference_file(install_holder(env))
    if not file.is_file():
        return None
    found = read_reference(file)
    return found if found.opaque is None else None


def read_references() -> list[Reference]:
    """Every reference file, by name; an unreadable one is opaque, never skipped.

    No ``refs/`` folder means no references. A folder that cannot be listed
    is one opaque reference for the folder itself, so the cleanup stops
    rather than take every layout for unheld.
    """
    folder = REFS.place
    try:
        files = sorted(
            entry
            for entry in folder.iterdir()
            if entry.suffix == ".json" and not entry.name.startswith(".")
        )
    except FileNotFoundError:
        return []
    except OSError as exc:
        why = f"the folder cannot be read ({exc.strerror or exc.__class__.__name__})"
        return [Reference(folder, opaque=why)]
    return [read_reference(file) for file in files]


def read_reference(file: Path) -> Reference:
    """One reference file, read tolerantly."""
    found = DataFile(file, _Stored).read()
    if isinstance(found, Absent):
        return Reference(file, opaque="unreadable (it is gone)")
    if isinstance(found, OtherVersion):
        return Reference(file, opaque="written by a newer untaped")
    if isinstance(found, Unreadable):
        return Reference(file, opaque=f"unreadable (it {found.reason})")
    data = found.value
    holder, targets, anchor = _holder(data.holder), data.targets, data.anchor
    if holder is None:
        why = "no holder" if data.holder is None else "the holder is malformed"
        return Reference(file, opaque=f"unreadable ({why})")
    anchor_id = None
    moved_from: tuple[Path, ...] = ()
    if holder.kind in _OWN_KINDS:
        # This untaped's own kinds: anything it cannot read pauses cleanup.
        if not isinstance(targets, list):
            return Reference(file, holder=holder, opaque="unreadable (no targets)")
        try:
            read = tuple(RefTarget.model_validate(target) for target in targets)
        except ValidationError:
            return Reference(file, holder=holder, opaque="unreadable (a target is malformed)")
        if holder.kind == PROJECT:
            if not isinstance(anchor, str) or not anchor:
                return Reference(file, holder=holder, opaque="unreadable (no anchor)")
            try:
                anchor_id = AnchorId.model_validate(data.anchor_id)
            except ValidationError:
                return Reference(file, holder=holder, opaque="unreadable (no anchor identity)")
            moved = [] if data.moved_from is None else data.moved_from
            if not isinstance(moved, list) or not all(isinstance(m, str) and m for m in moved):
                return Reference(file, holder=holder, opaque="unreadable (moved_from is malformed)")
            moved_from = tuple(Path(str(m)) for m in moved)
    else:
        # Another kind (a later untaped's) is live and holds what it can read; it never pauses.
        read = _readable(targets)
    return Reference(
        file,
        holder=holder,
        anchor=Path(anchor) if isinstance(anchor, str) and anchor else None,
        anchor_id=anchor_id,
        moved_from=moved_from,
        plugin=_text(data.plugin) or None,
        version=_text(data.version) or None,
        targets=read,
    )


@experimental
def holders(target: RefTarget) -> list[RefHolder]:
    """The live holders that name ``target``.

    What a resource asks before it removes something. Raises
    :class:`ConfigError` when a reference cannot be read (a newer untaped's,
    a damaged one, or a ``refs/`` folder that cannot be listed): who holds
    ``target`` is then unknown, never "nobody".
    """
    found = read_references()
    unknown = next((ref for ref in found if ref.opaque is not None), None)
    if unknown is not None:
        raise ConfigError(
            f"cannot tell who holds {target.describe()}: {unknown.file} is {unknown.opaque}",
            hint="upgrade this install, or fix or remove that file",
        )
    return [
        ref.holder for ref in found if ref.holder is not None and target in ref.targets and ref.live
    ]


def other_installs() -> list[Reference]:
    """The live registered installs other than this one (they may read the same config)."""
    env = environment()
    mine = install_holder(env) if env is not None else None
    return [
        ref
        for ref in read_references()
        if ref.opaque is None and ref.kind == INSTALL and ref.holder != mine and ref.live
    ]


def composed_targets() -> tuple[RefTarget, ...] | None:
    """The layout of every store this run's composition declares.

    ``None`` when nothing is composed (a library call outside the CLI).
    """
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root

    try:
        result = composition()
    except RuntimeError:
        return None
    from untaped.sdk.storage.store import Store  # noqa: PLC0415 - store imports this module

    stores = [
        store
        for registered in result.plugins
        for store in registered.spec.stores
        if isinstance(store, Store)  # an item store's holds are its holders', not an install's
    ]
    return tuple(store.target for store in sorted(stores, key=lambda s: (s.plugin, s.name)))


def others_text() -> str:
    """The other live installs, as rows name them (``untaped 11.0.2 at ~/…``); ``""`` for none.

    What ``doctor`` and ``config migrate`` name as other readers of the
    config: an older install may still read keys a migration rewrites.
    """
    return ", ".join(ref.describe() for ref in other_installs())


def register(targets: Sequence[RefTarget]) -> Reference:
    """Write this install's reference naming ``targets``; raise when it has no environment."""
    env = environment()
    if env is None:
        raise ConfigError(
            "this untaped runs from a source tree, not an installed distribution, "
            "so there is no environment to register",
            hint="install it with a package installer (uv, pipx, pip), then register that install",
        )
    holder = install_holder(env)
    with locked():
        _write(holder, env, targets)
    return read_reference(reference_file(holder))


#: Whether this install is registered (``source tree``: it cannot be).
type InstallState = Literal["registered", "not registered", "source tree"]


def install_state() -> InstallState:
    """This install's registration, as ``setup plan`` and the setup screen show it."""
    if environment() is None:
        return "source tree"
    return "registered" if this_install() is not None else "not registered"


def register_this_install() -> tuple[Reference, bool]:
    """Register this install for every store this run composes; whether that changed the file."""
    before = this_install()
    ref = register(composed_targets() or ())
    same = before is not None and (before.version, before.targets) == (ref.version, ref.targets)
    return ref, not same


def unregister() -> Reference | None:
    """Remove this install's reference; the reference removed, or ``None`` when there was none."""
    env = environment()
    if env is None:
        return None
    file = reference_file(install_holder(env))
    with locked():
        if not file.is_file():
            return None
        found = read_reference(file)
        try:
            file.unlink()
        except OSError as exc:
            raise ConfigError(f"could not remove {file}: {exc.strerror or exc}") from exc
    return found


def refresh() -> None:
    """Keep a registered install's reference current, once per process.

    Rewrites the file when this run's version or layouts differ from what
    it records; writes nothing for an install nobody registered, nor
    outside a run (a library call). Never raises: a reference that cannot
    be refreshed waits for the next run.
    """
    global _refreshed
    with _STATE:
        if _refreshed:
            return
        _refreshed = True
    try:
        _refresh()
    except (OSError, UntapedError, NoRunError) as exc:
        _LOG.debug("could not refresh this install's reference: %s", exc)


def _refresh() -> None:
    env = environment()
    if env is None:
        return
    targets = composed_targets()
    if targets is None:
        return  # no composition to describe (a library call): keep what the file says
    holder = install_holder(env)
    file = reference_file(holder)
    if not file.is_file():
        return
    current = read_reference(file)
    if current.opaque is None and (current.version, current.targets) == (_version(), targets):
        return
    with locked():
        if file.is_file():
            _write(holder, env, targets)


@contextmanager
def locked() -> Iterator[None]:
    """Hold the ``refs`` block's lock for the block (every writer and every pruner takes it).

    Raises :class:`ConfigError` when the folder cannot be made, and an
    unavailable :class:`UntapedError` when the lock is busy past the wait.
    """
    REFS.open()
    with REFS.locked():
        yield


@experimental
def write_reference(
    holder: RefHolder,
    targets: Sequence[RefTarget],
    *,
    anchor: Path | None = None,
    anchor_id: AnchorId | None = None,
    moved_from: Sequence[Path] = (),
    plugin: str | None = None,
    version: str | None = None,
) -> Path:
    """Write one holder's reference file, ``refs/<kind>-<name>.json``; return its path.

    ``untaped.sdk.testing`` exports it to fake another holder in the hermetic
    home: an ``anchor`` is live while it holds an ``untaped-*.dist-info``
    folder, and a holder without one lives until its file is removed.
    ``anchor_id`` records the anchor's filesystem identity (a project's), and
    ``moved_from`` where it was before each move not yet fully repaired.
    """
    data = _Stored(
        holder=holder.model_dump(),
        anchor=None if anchor is None else str(anchor),
        anchor_id=None if anchor_id is None else anchor_id.model_dump(),
        moved_from=[str(path) for path in moved_from] or None,
        plugin=plugin,
        version=version,
        targets=[target.model_dump() for target in targets],
    )
    file = reference_file(holder)
    DataFile(file, _Stored).write(data)
    return file


def _write(holder: RefHolder, env: Path, targets: Sequence[RefTarget]) -> None:
    write_reference(holder, targets, anchor=env, version=_version())


def _version() -> str | None:
    try:
        return distribution("untaped").version
    except PackageNotFoundError:
        return None


def _installed_versions(anchor: Path) -> set[str] | None:
    """The untaped versions whose ``dist-info`` sits in ``anchor``; ``None`` when unknowable.

    An empty set means no install there (the folder is gone, or holds no
    untaped); a folder that exists but cannot be listed is ``None``.
    """
    try:
        with os.scandir(anchor) as entries:
            names = [entry.name for entry in entries]
    except FileNotFoundError, NotADirectoryError:
        return set()
    except OSError:
        return None
    return {
        name.removeprefix("untaped-").removesuffix(".dist-info")
        for name in names
        if name.startswith("untaped-") and name.endswith(".dist-info")
    }


def _readable(targets: object) -> tuple[RefTarget, ...]:
    """The targets of ``targets`` that read as a :class:`RefTarget`; the rest are skipped."""
    if not isinstance(targets, list):
        return ()
    read = []
    for target in targets:
        try:
            read.append(RefTarget.model_validate(target))
        except ValidationError:
            continue
    return tuple(read)


def _holder(value: object) -> RefHolder | None:
    """The holder ``value`` names, or ``None`` when it names none this untaped can read."""
    try:
        return RefHolder.model_validate(value)
    except ValidationError:
        return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _forget() -> None:
    """Let the next store open refresh again (``untaped.sdk.testing.plugin``)."""
    global _refreshed
    with _STATE:
        _refreshed = False


__all__ = ["RefHolder", "RefTarget", "holders"]
