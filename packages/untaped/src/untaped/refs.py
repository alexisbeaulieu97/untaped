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

A target is ``<plugin>.<store>@<layout>`` (a layout) or
``<plugin>.<store>#<item>`` (an item in a store); ``docs/caches.md`` defines
the grammar. No clock is read anywhere.
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

from untaped.errors import ConfigError
from untaped.fs import atomic_write
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
    targets: tuple[str, ...] = ()
    opaque: str | None = None

    @property
    def live(self) -> bool:
        """Whether it still counts: opaque, anchorless, or its anchor still holds untaped."""
        if self.opaque is not None or self.anchor is None:
            return True
        return bool(_installed_versions(self.anchor))

    @property
    def pre_upgrade(self) -> bool:
        """A live install whose directory now holds another untaped version than it records."""
        if self.anchor is None or self.opaque is not None or self.version is None:
            return False
        versions = _installed_versions(self.anchor)
        return bool(versions) and self.version not in versions

    def layouts(self, store: str) -> set[int]:
        """The layouts of ``store`` (``<plugin>.<store>``) this reference names."""
        found: set[int] = set()
        for target in self.targets:
            name, at, layout = target.partition("@")
            if at and name == store and layout.isdigit():
                found.add(int(layout))
        return found

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
    """Every reference file, by name; an unreadable one is opaque, never skipped."""
    try:
        files = sorted(
            entry
            for entry in refs_dir().iterdir()
            if entry.suffix == ".json" and not entry.name.startswith(".")
        )
    except OSError:
        return []
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
    return Reference(
        file,
        holder=holder,
        kind=holder.partition(":")[0],
        anchor=Path(anchor) if isinstance(anchor, str) and anchor else None,
        plugin=_text(data.get("plugin")) or None,
        version=_text(data.get("version")) or None,
        targets=tuple(str(target) for target in targets if isinstance(target, str)),
    )


@experimental
def holders(target: str) -> list[str]:
    """The live holders that name ``target`` (``<plugin>.<store>@<layout>`` or ``…#<item>``).

    What a resource asks before it removes something. An opaque reference
    is not listed: it cannot be read, and :meth:`~untaped.store.Store.prune`
    stops when it meets one.
    """
    return [
        ref.holder
        for ref in read_references()
        if ref.opaque is None and target in ref.targets and ref.live
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


def composed_targets() -> tuple[str, ...] | None:
    """``<plugin>.<store>@<layout>`` for every store this run's composition declares.

    ``None`` when nothing is composed (a library call outside the CLI).
    """
    from untaped.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root

    try:
        result = composition()
    except RuntimeError:
        return None
    return tuple(
        sorted(
            f"{store.qualified}@{store.layout}"
            for registered in result.plugins
            for store in registered.spec.stores
        )
    )


def register(targets: Sequence[str]) -> Reference:
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
        file.unlink()
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
    except (OSError, TimeoutError, ConfigError) as exc:
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
    """Hold ``refs/.lock`` for the block (every writer and every pruner takes it)."""
    from filelock import FileLock  # noqa: PLC0415

    folder = refs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with FileLock(str(folder / _LOCK), timeout=_WAIT):
        yield


def write_reference(
    holder: str,
    targets: Sequence[str],
    *,
    anchor: Path | None = None,
    plugin: str | None = None,
    version: str | None = None,
) -> Path:
    """Write one holder's reference file whole (``untaped.testing`` builds others with it)."""
    data: dict[str, object] = {"untaped": FORMAT, "holder": holder}
    if anchor is not None:
        data["anchor"] = str(anchor)
    if plugin is not None:
        data["plugin"] = plugin
    if version is not None:
        data["version"] = version
    data["targets"] = list(targets)
    file = reference_file(holder)
    atomic_write(file, json.dumps(data, indent=2) + "\n")
    return file


def _write(holder: str, env: Path, targets: Sequence[str]) -> None:
    write_reference(holder, targets, anchor=env, version=_version())


def _version() -> str | None:
    try:
        return distribution("untaped").version
    except PackageNotFoundError:
        return None


def _installed_versions(anchor: Path) -> set[str]:
    """The untaped versions whose ``dist-info`` sits in ``anchor`` (none: not an install any more)."""
    try:
        return {
            entry.name.removeprefix("untaped-").removesuffix(".dist-info")
            for entry in anchor.glob("untaped-*.dist-info")
        }
    except OSError:
        return set()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _forget() -> None:
    """Let the next store open refresh again (``untaped.testing.plugin``)."""
    global _refreshed
    with _STATE:
        _refreshed = False


__all__ = ["holders"]
