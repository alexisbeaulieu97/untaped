"""A place of a plugin's own: ``checkouts = Dir(at=key(WorkspaceSettings).workspaces_dir)``.

A plugin class names the place; core decides where it is and keeps it. By
default it is the folder ``plugins/<plugin>/<attribute>/`` under the home;
``at=`` names a path setting instead (the user's folder, an absolute path).
``file=`` makes the place hold one file of that name. ``layout=N`` numbers
what it holds as a store numbers its layouts (N is the oldest layout this code
still opens), inside the place: ``<place>/<attribute>.v4/``, or
``<place>/index.v4.sqlite3`` with ``file="index.sqlite3"``, so two installs at
different layouts never open each other's data and nothing numbered lands
beside the user's folder. ``open()`` gives the path, and ``locked(name)``
holds the operation lock of one thing in the place (``locked()`` the place's
own lock), waiting the declared ``wait=``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk._block import OwnedBlock
from untaped.sdk._home import CORE
from untaped.sdk.errors import ConfigError, ErrorCategory, UntapedError
from untaped.sdk.fields import SettingKey
from untaped.sdk.marks import experimental
from untaped.sdk.storage.lock import operation_lock

if TYPE_CHECKING:
    from untaped.sdk.storage.store import Store

#: Names core keeps under ``plugins/<plugin>/`` itself: no Dir may take them.
RESERVED = frozenset({"cached", "state", CORE})


@experimental
class Dir(OwnedBlock):
    """A folder of the plugin's own, or one file in it, numbered or not; see the module."""

    block_kind = "dir"

    def __init__(
        self,
        *,
        at: SettingKey | None = None,
        layout: int | None = None,
        file: str | None = None,
        wait: timedelta | None = None,
    ) -> None:
        if at is not None and not isinstance(at, SettingKey):
            raise TypeError(f"Dir: at takes a path setting, as in key(Settings).path, not {at!r}")
        if layout is not None and (
            isinstance(layout, bool) or not isinstance(layout, int) or layout < 1
        ):
            raise TypeError(f"Dir: layout takes an integer from 1, not {layout!r}")
        if file is not None and (
            not isinstance(file, str) or not file or Path(file).name != file or file in {".", ".."}
        ):
            raise TypeError(f"Dir: file takes one file name, not {file!r}")
        if wait is not None and (not isinstance(wait, timedelta) or wait < timedelta(0)):
            raise TypeError(f"Dir: wait takes a timedelta of zero or more, not {wait!r}")
        super().__init__()
        self.at = at
        self.layout = layout
        self.file = file
        self.wait = wait
        self._stores: dict[tuple[str, str, Path], Store] = {}

    def __repr__(self) -> str:
        options = [
            f"{label}={value!r}"
            for label, value in (("at", self.at), ("layout", self.layout), ("file", self.file))
            if value is not None
        ]
        return f"Dir({', '.join([repr(self.where), *options])})"

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if name in RESERVED:
            return f"a Dir cannot be named {name!r}: core keeps plugins/<plugin>/{name} itself"
        return None

    def problems(self) -> tuple[str, ...]:
        if self.layout is None:
            return ()
        from untaped.sdk.storage.store import valid_store_name  # noqa: PLC0415 - numbered only

        return tuple(
            f"a numbered Dir is named [a-z][a-z0-9_]*, not {name!r}"
            for name in (self.derived_name(binding.attribute) for binding in self.bindings)
            if not valid_store_name(name)
        )

    @property
    def _label(self) -> str:
        """How a message names the place in a run: ``workspace.checkouts``."""
        return f"{self.plugin}.{self.name}"

    # -- where it is ---------------------------------------------------------

    @property
    def place(self) -> Path:
        """The folder: the ``at=`` setting's path, or ``plugins/<plugin>/<name>`` under the home."""
        from untaped.sdk.run import (  # noqa: PLC0415 - the run imports the SDK
            current_run,
            settings_of,
        )

        if self.at is None:
            return current_run().home / "plugins" / self.plugin / self.name
        value: object = settings_of(SettingKey.model_of(self.at))
        for part in SettingKey.path_of(self.at):
            value = getattr(value, part)
        folder = Path(value).expanduser() if isinstance(value, str | Path) and value else None
        if folder is None or not folder.is_absolute() or not folder.name:
            raise ConfigError(
                f"{self._label}: the setting {self.at!r} holds {value!r}, not an absolute "
                "path to a folder",
            )
        return folder

    @property
    def path(self) -> Path:
        """This code's path: the folder, its file, or this layout's of either. No side effect."""
        if self.layout is None:
            return self.place if self.file is None else self.place / self.file
        return self._store().path()

    def open(self) -> Path:
        """The path, made ready: its folder created; a numbered one held for the process.

        A numbered place is opened as a store opens its layout: its in-use
        lock is held for the rest of the process, so no other install moves
        it while this one reads it.
        """
        path = self._store().open() if self.layout is not None else self.path
        folder = path if self.file is None else path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"{self._label}: cannot create {folder} ({exc.strerror or exc})",
                details={"path": str(folder)},
            ) from exc
        return path

    def _store(self) -> Store:
        """The store machinery behind a numbered place, one per plugin, name and folder."""
        from untaped.sdk.storage.store import Store  # noqa: PLC0415 - numbered places only

        base = self.place / (self.file or self.name)
        held = (self.plugin, self.name, base)
        store = self._stores.get(held)
        if store is None:
            store = Store(self.plugin, self.name, base=lambda: base, layout=self.layout or 1)
            self._stores[held] = store
        return store

    # -- its operation locks ---------------------------------------------------

    @contextmanager
    def locked(self, name: str | None = None) -> Iterator[None]:
        """Hold the lock of ``name`` in the place (the place's own lock without one).

        Waits up to the declared ``wait=`` for another process, then fails
        with ``<plugin>.<dir> '<name>' is busy (another untaped process)``.
        Taking it again in one thread only counts. The place's own lock and
        its things' locks are separate: a plugin uses one or the other.
        """
        if self.wait is None:
            raise ConfigError(
                f"{self._label} declares no wait=, which locked() waits: declare one, "
                "or call try_locked()"
            )
        with self._held(name, self.wait.total_seconds()):
            yield

    @contextmanager
    def try_locked(self, name: str | None = None) -> Iterator[bool]:
        """Hold the lock of ``name`` (or the place's own) if no other process does."""
        with ExitStack() as stack:
            try:
                stack.enter_context(self._held(name, 0))
            except UntapedError as exc:
                if exc.category != ErrorCategory.UNAVAILABLE:
                    raise
                held = False
            else:
                held = True
            yield held

    def lock_path(self, name: str | None = None) -> Path:
        """The lock file of ``name`` (or the place's own): under the home, never in the place."""
        from untaped.sdk.run import current_run  # noqa: PLC0415 - the run imports the SDK

        folder = current_run().home / "plugins" / self.plugin
        if name is None:
            return folder / f"{self.name}.lock"
        digest = hashlib.sha256(name.encode()).hexdigest()[:32]
        return folder / f"{self.name}.locks" / f"{digest}.lock"

    @contextmanager
    def _held(self, name: str | None, timeout: float) -> Iterator[None]:
        if name is not None and (not isinstance(name, str) or not name):
            raise TypeError(f"{self._label}: locked() takes a thing's name, not {name!r}")
        label = self._label + ("" if name is None else f" {name!r}")
        with operation_lock(self.plugin, label, self.lock_path(name), timeout):
            yield
