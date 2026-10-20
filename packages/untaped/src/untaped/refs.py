"""References: who still uses a store's layout, one file per holder in ``~/.untaped/refs/``.

A holder declares what it uses; the store decides when an unheld layout goes
(:meth:`untaped.store.Store.prune`). 11.1 writes one kind of holder, a
registered install: ``untaped setup register`` writes
``refs/install-<id>.json`` naming the install's environment (the directory
holding untaped's own distribution, whatever installed it), its version and
the layout of every store its composition declares, and each later run of
that install refreshes the file on its first store open. A reference is live
while its anchor still holds an ``untaped-*.dist-info``, or, with no anchor,
until its holder removes it. A dead reference counts for nothing, but its file
is never another process's to delete: ``setup register --remove`` or the
previewed ``setup migrate-dirs`` row removes it.

Each target is a :class:`RefTarget`, written as a JSON object: a layout of a
store, or one item of a store that keeps one per item. No clock is read
anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from untaped.errors import ConfigError
from untaped.fs import atomic_write, file_lock
from untaped.output.diagnostic import _is_none
from untaped.settings import DEFAULT_CONFIG_PATH
from untaped.stability import experimental

_LOG = logging.getLogger("untaped.refs")

#: The reference file's own format, bumped only for a change an older reader cannot use.
FORMAT = "1"
#: The holder kind of a registered install.
INSTALL = "install"
#: Seconds a writer waits for ``refs/.lock``.
_WAIT = 10.0
_LOCK = ".lock"
_STATE = threading.Lock()
_refreshed = False


def refs_dir() -> Path:
    """``~/.untaped/refs``: beside ``plugins/``, so it reserves no plugin name."""
    return Path(DEFAULT_CONFIG_PATH).expanduser().parent / "refs"


@experimental
class RefTarget(BaseModel):
    """What a reference keeps: one ``layout`` of a store, or one ``item`` in it.

    ``RefTarget(plugin="acme", store="catalogue", layout=2)`` keeps a layout;
    ``RefTarget(plugin="git", store="store", item="github.com/acme/pay")`` one
    item of a store that keeps one per item. Exactly one of the two is set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    plugin: str
    store: str
    layout: Annotated[int | None, Field(exclude_if=_is_none)] = None
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

    def _key(self) -> tuple[str, str, int, str]:
        return (self.plugin, self.store, self.layout or 0, self.item or "")


@dataclass(frozen=True)
class Reference:
    """One holder's file, as read.

    ``opaque`` says why it cannot be used (unreadable, or written by a newer
    untaped): an opaque reference counts as live and pauses pruning.
    """

    file: Path
    holder: str = ""
    kind: str = ""
    anchor: Path | None = None
    plugin: str | None = None
    version: str | None = None
    targets: tuple[RefTarget, ...] = ()
    opaque: str | None = None

    @property
    def live(self) -> bool:
        """Whether it still counts: opaque, anchorless, or its anchor still holds untaped.

        An anchor that exists but cannot be listed counts as live.
        """
        if self.opaque is not None or self.anchor is None:
            return True
        versions = _installed_versions(self.anchor)
        return versions is None or bool(versions)

    @property
    def pre_upgrade(self) -> bool:
        """A live install whose directory now holds another untaped version than it records."""
        if self.anchor is None or self.opaque is not None or self.version is None:
            return False
        versions = _installed_versions(self.anchor)
        return bool(versions) and versions is not None and self.version not in versions

    def layouts(self, plugin: str, store: str) -> set[int]:
        """The layouts of ``plugin``'s ``store`` this reference names."""
        return {
            target.layout
            for target in self.targets
            if target.plugin == plugin and target.store == store and target.layout is not None
        }

    def describe(self) -> str:
        """How a row names the holder: ``untaped 11.1.0 at <env>``, or the holder itself."""
        from untaped.messages import shown_path  # noqa: PLC0415 - no cycle

        if self.kind == INSTALL and self.anchor is not None:
            return f"untaped {self.version or '?'} at {shown_path(self.anchor)}"
        return self.holder or self.file.name


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


def install_holder(env: Path) -> str:
    """``install:<12 hex digits>``: the holder an install at ``env`` writes."""
    return f"{INSTALL}:{hashlib.sha256(str(env).encode()).hexdigest()[:12]}"


def reference_file(holder: str) -> Path:
    """``refs/<kind>-<segment>.json``: the file a holder's reference lives in."""
    from untaped.git import safe_path_segment  # noqa: PLC0415 - loads unicodedata

    kind, _, name = holder.partition(":")
    return refs_dir() / f"{safe_path_segment(kind)}-{safe_path_segment(name)}.json"


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
    folder = refs_dir()
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
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return Reference(file, opaque=f"unreadable ({exc.__class__.__name__})")
    if not isinstance(data, dict):
        return Reference(file, opaque="unreadable (not an object)")
    form = data.get("untaped")
    if form != FORMAT:
        newer = isinstance(form, str) and form.isdigit() and int(form) > int(FORMAT)
        why = "written by a newer untaped" if newer else f"unreadable (format {form!r})"
        return Reference(file, holder=_text(data.get("holder")), opaque=why)
    holder, targets, anchor = data.get("holder"), data.get("targets"), data.get("anchor")
    if not isinstance(holder, str) or not holder or not isinstance(targets, list):
        return Reference(file, opaque="unreadable (no holder or targets)")
    try:
        read = tuple(RefTarget.model_validate(target) for target in targets)
    except ValidationError:
        return Reference(file, holder=holder, opaque="unreadable (a target is malformed)")
    return Reference(
        file,
        holder=holder,
        kind=holder.partition(":")[0],
        anchor=Path(anchor) if isinstance(anchor, str) and anchor else None,
        plugin=_text(data.get("plugin")) or None,
        version=_text(data.get("version")) or None,
        targets=read,
    )


@experimental
def holders(target: RefTarget) -> list[str]:
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
    return [ref.holder for ref in found if target in ref.targets and ref.live]


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
    from untaped.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root

    try:
        result = composition()
    except RuntimeError:
        return None
    return tuple(
        sorted(
            (
                RefTarget(plugin=store.plugin, store=store.name, layout=store.layout)
                for registered in result.plugins
                for store in registered.spec.stores
            ),
            key=RefTarget._key,
        )
    )


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
    it records; writes nothing for an install nobody registered. Never
    raises: a reference that cannot be refreshed waits for the next run.
    """
    global _refreshed
    with _STATE:
        if _refreshed:
            return
        _refreshed = True
    try:
        _refresh()
    except (OSError, ConfigError) as exc:
        _LOG.debug("could not refresh this install's reference: %s", exc)


def _refresh() -> None:
    env = environment()
    if env is None:
        return
    holder = install_holder(env)
    file = reference_file(holder)
    if not file.is_file():
        return
    targets = composed_targets()
    if targets is None:
        return  # no composition to describe: keep what the file says
    current = read_reference(file)
    if current.opaque is None and (current.version, current.targets) == (_version(), targets):
        return
    with locked():
        if file.is_file():
            _write(holder, env, targets)


@contextmanager
def locked() -> Iterator[None]:
    """Hold ``refs/.lock`` for the block (every writer and every pruner takes it).

    Raises :class:`ConfigError` when the folder cannot be made or the lock
    cannot be taken (busy past the wait, or refused).
    """
    folder = refs_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"could not create {folder}: {exc.strerror or exc}") from exc
    with file_lock(
        folder / _LOCK,
        timeout=_WAIT,
        error=ConfigError,
        busy=f"another untaped is changing the install references in {folder}; try again",
        failed=f"could not lock the install references in {folder}",
    ):
        yield


@experimental
def write_reference(
    holder: str,
    targets: Sequence[RefTarget],
    *,
    anchor: Path | None = None,
    plugin: str | None = None,
    version: str | None = None,
) -> Path:
    """Write one holder's reference file whole, ``refs/<kind>-<name>.json``; return its path.

    ``untaped.testing`` exports it to fake another holder in the hermetic
    home: an ``anchor`` is live while it holds an ``untaped-*.dist-info``
    folder, and a holder without one lives until its file is removed.
    """
    data: dict[str, object] = {"untaped": FORMAT, "holder": holder}
    if anchor is not None:
        data["anchor"] = str(anchor)
    if plugin is not None:
        data["plugin"] = plugin
    if version is not None:
        data["version"] = version
    data["targets"] = [target.model_dump() for target in targets]
    file = reference_file(holder)
    atomic_write(file, json.dumps(data, indent=2) + "\n")
    return file


def _write(holder: str, env: Path, targets: Sequence[RefTarget]) -> None:
    try:
        write_reference(holder, targets, anchor=env, version=_version())
    except OSError as exc:
        raise ConfigError(
            f"could not write {reference_file(holder)}: {exc.strerror or exc}"
        ) from exc


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


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _forget() -> None:
    """Let the next store open refresh again (``untaped.testing.plugin``)."""
    global _refreshed
    with _STATE:
        _refreshed = False


__all__ = ["RefTarget", "holders"]
