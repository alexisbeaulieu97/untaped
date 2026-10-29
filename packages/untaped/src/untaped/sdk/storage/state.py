"""Plugin state: typed rows a plugin class keeps in its own ``state.yml``.

:class:`StateRows` keeps typed rows, one per key, checked against their
model, in ``plugins/<plugin>/state.yml`` (a project's in its
``.untaped/plugins/<plugin>/state.yml``, :meth:`StateRows.per_project`). It
is the only copy: a damaged row stops the command and names the file, the
row and the fix. Fields a newer untaped added are written back as they were.
The rows' key drops out of the file when they are emptied.
"""

from __future__ import annotations

import copy
import typing
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from untaped.runtime.config_file import read_state_dict, write_state_dict
from untaped.sdk._block import OwnedBlock
from untaped.sdk._home import PLUGINS_FOLDER
from untaped.sdk.errors import ConfigError, ErrorCategory, UntapedError, first_validation_error
from untaped.sdk.marks import experimental
from untaped.sdk.storage._fs import locked_write

if TYPE_CHECKING:
    from untaped.sdk.projects import Project


@experimental
class RowExists(ConfigError):
    """:meth:`StateRows.add` found a row with the same key (exits ``1``)."""

    category = ErrorCategory.CONFLICT


@experimental
class StateRows[M: BaseModel](OwnedBlock):
    """Typed rows a plugin keeps as the only copy.

    A plugin class holding it names it: ``items = StateRows[Item]()`` keeps
    ``Item`` rows under ``items`` in the plugin's own
    ``plugins/<plugin>/state.yml``, stamped and written under that file's
    lock. The row's key is its one required ``str`` field marked ``Key``; a
    model with no such field makes the rows a list (:meth:`all`,
    :meth:`append`, :meth:`replace_all`). The command that removes a row
    marks itself ``removes = items`` and takes the key under the ``Key``
    field's name, as its first positional or an option (else composition
    names the problem ``remover-key``): a damaged row's error offers it.

    A row is written ``mode="json"``, by alias; ``omit_none`` leaves its
    ``None`` fields out. Rows are read with ``extra="ignore"``, nested models
    included, and the fields a newer untaped added are written back as they
    were. A row that fails its model, has no key, or shares its key with
    another is damaged: reading it (``get``, ``all``) or changing it stops
    with a :class:`~untaped.sdk.errors.ConfigError` (exit 4) naming the file,
    the row and the fix, while ``keys``, other rows and ``delete`` keep
    working. Change a row with :meth:`update`, never ``get`` then ``put``:
    ``update`` runs under the state file's lock.
    """

    block_kind = "state-rows"

    model: type[M] | None
    omit_none: bool

    def __init__(self, *, omit_none: bool = False) -> None:
        self._setup(None, None, None, omit_none, None)

    @classmethod
    def __class_getitem__(cls, params: object) -> object:
        # ``StateRows[Item]`` keeps its model for ``.per_project()``, which a
        # plain alias hands to the class without its arguments.
        alias = super().__class_getitem__(params)  # type: ignore[misc]
        return _RowsAlias(alias.__origin__, alias.__args__)

    @classmethod
    def per_project(cls, *, omit_none: bool = False) -> ProjectStateRows[M]:
        """Rows each project keeps, in its ``.untaped/plugins/<plugin>/state.yml``.

        ``.of(project)`` opens them; spell it ``StateRows[Item].per_project()``.
        """
        return ProjectStateRows(omit_none=omit_none)

    @classmethod
    def _bound(
        cls, plugin: str, name: str, model: type[M], omit_none: bool, path: Path
    ) -> StateRows[M]:
        """One project's rows (:meth:`ProjectStateRows.of`): named, at ``path``."""
        rows: StateRows[M] = object.__new__(cls)
        rows._setup(plugin, name, model, omit_none, path)
        return rows

    def _setup(
        self,
        plugin: str | None,
        name: str | None,
        model: type[M] | None,
        omit_none: bool,
        path: Path | None,
    ) -> None:
        OwnedBlock.__init__(self, plugin, name)
        self.model = model
        self.omit_none = omit_none
        self._resolved: _Shape | None = None
        self._path = path

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        if name == STAMP:
            return f"state rows cannot be named {STAMP!r}: the state file keeps its stamp there"
        return None

    def problems(self) -> tuple[str, ...]:
        return _shape_problems(self)

    def _keyed_by(self) -> str | None:
        """The model's field keying the rows; ``None``: a list, or a shape composition refuses."""
        return _key_field(self)

    @property
    def _file(self) -> _PluginFile:
        path = self._path or _plugin_state_path(self.plugin)
        return _PluginFile(self.plugin, self.name, path)

    @property
    def qualified(self) -> str:
        return f"{self.plugin}.{self.name}"

    def __repr__(self) -> str:
        return f"StateRows({self.where!r})"

    def path(self) -> Path:
        """The state file these rows live in."""
        return self._file.path()

    # -- reads ------------------------------------------------------------

    def get(self, key: str) -> M | None:
        """The row keyed ``key``, or ``None``; a damaged one stops (exit 4)."""
        self._key_name("get")
        found = self._find(self._file.read(), key)
        return None if found is None else found[1]

    def all(self) -> list[M]:
        """Every row, in file order; the first damaged one stops (exit 4)."""
        return [value for _, value in self._all(self._file.read())]

    def keys(self) -> list[str]:
        """Every row's key, in file order and once each; never stops on a damaged row."""
        name = self._key_name("keys")
        seen: dict[str, None] = {}
        for row in self._file.read():
            key = row.get(name) if isinstance(row, dict) else None
            if isinstance(key, str):
                seen[key] = None
        return list(seen)

    # -- writes -----------------------------------------------------------

    def append(self, value: M) -> None:
        """Append ``value`` to rows that are a list (their model has no ``Key`` field)."""
        if self._shape().key is not None:
            raise TypeError(
                f"{_label(self)} rows are keyed: add, put or update one instead of appending"
            )
        self._check_type(value)
        self._file.mutate(lambda rows: [*rows, self._dump(value)])

    def add(self, value: M) -> None:
        """Append ``value``; :class:`RowExists` when its key already has a row."""
        self._key_name("add")
        key = self._key_of(value)

        def _add(rows: list[Any]) -> list[Any]:
            if self._indexes(rows, key):
                raise RowExists(
                    f"{self.qualified} already has a row {key!r}",
                    details={"path": str(self.path()), "key": key},
                )
            return [*rows, self._dump(value)]

        self._file.mutate(_add)

    def put(self, value: M) -> None:
        """Replace the row with ``value``'s key in place, or append ``value``."""
        self._key_name("put")
        key = self._key_of(value)

        def _put(rows: list[Any]) -> list[Any]:
            found = self._find(rows, key)
            if found is None:
                return [*rows, self._dump(value)]
            index, current = found
            rows[index] = self._written(rows[index], current, value)
            return rows

        self._file.mutate(_put)

    def update(self, key: str, change: Callable[[M], M]) -> M | None:
        """Replace the row keyed ``key`` with ``change(row)`` under the lock; ``None``: no such row.

        ``change`` must keep the key, and must not write state itself (a
        nested write to the file is refused).
        """
        self._key_name("update")
        updated: M | None = None

        def _update(rows: list[Any]) -> list[Any] | None:
            nonlocal updated
            found = self._find(rows, key)
            if found is None:
                return None
            index, current = found
            new = change(current)
            if self._key_of(new) != key:
                raise UntapedError(
                    f"{self.qualified}: update({key!r}) changed the row's key to "
                    f"{self._key_of(new)!r}; delete the row and add the new one instead"
                )
            rows[index] = self._written(rows[index], current, new)
            updated = new
            return rows

        self._file.mutate(_update)
        return updated

    def replace_all(self, change: Callable[[list[M]], list[M]]) -> list[M]:
        """Replace every row with ``change(rows)`` under the lock and return the new rows.

        A row whose key was already stored keeps the fields a newer untaped
        added; a damaged row stops before ``change`` runs.
        """
        replaced: list[M] = []

        def _replace(rows: list[Any]) -> list[Any]:
            nonlocal replaced
            current = self._all(rows)
            if self._shape().key is None:
                replaced = list(change([value for _, value in current]))
                for value in replaced:
                    self._check_type(value)
                return [self._dump(value) for value in replaced]
            stored = {self._key_of(value): (rows[index], value) for index, value in current}
            replaced = list(change([value for _, value in current]))
            keys = [self._key_of(value) for value in replaced]
            repeated = sorted({key for key in keys if keys.count(key) > 1})
            if repeated:
                raise UntapedError(
                    f"{self.qualified}: replace_all returned the key {repeated[0]!r} twice"
                )
            return [
                self._written(*stored[key], value) if key in stored else self._dump(value)
                for key, value in zip(keys, replaced, strict=True)
            ]

        self._file.mutate(_replace)
        return replaced

    def delete(self, key: str) -> int:
        """Remove every row keyed ``key``, damaged ones included; the number removed."""
        self._key_name("delete")
        removed = 0

        def _delete(rows: list[Any]) -> list[Any] | None:
            nonlocal removed
            drop = set(self._indexes(rows, key))
            removed = len(drop)
            return [row for index, row in enumerate(rows) if index not in drop] if drop else None

        self._file.mutate(_delete)
        return removed

    # -- internals --------------------------------------------------------

    def _shape(self) -> _Shape:
        if self._resolved is None:
            self._resolved = _resolved_shape(self)
        return self._resolved

    def _key_name(self, method: str) -> str:
        """The stored key's name; :class:`TypeError` naming ``method`` for rows that are a list."""
        key = self._shape().key
        if key is None:
            raise TypeError(
                f"{_label(self)} rows are a list (their model has no Key field), which {method} "
                "cannot read: use all, append and replace_all"
            )
        return key

    def _check_type(self, value: M) -> None:
        shape = self._shape()
        if not isinstance(value, shape.model):
            raise UntapedError(
                f"{self.qualified} keeps {shape.model.__name__} rows, not {type(value).__name__}"
            )

    def _key_of(self, value: M) -> str:
        self._check_type(value)
        key: str = getattr(value, self._shape().field)
        return key

    def _dump(self, value: M) -> dict[str, Any]:
        return value.model_dump(mode="json", by_alias=True, exclude_none=self.omit_none)

    def _written(self, stored: dict[str, Any], current: M, new: M) -> dict[str, Any]:
        """``new`` as written over ``stored`` (whose validated value is ``current``)."""
        shape = self._shape()
        from untaped.sdk.storage._write_back import write_back  # noqa: PLC0415 - writes only

        return write_back(shape.model, stored, self._dump(current), self._dump(new))

    def _indexes(self, rows: Sequence[Any], key: str) -> list[int]:
        name = self._key_name("this")
        return [i for i, row in enumerate(rows) if isinstance(row, dict) and row.get(name) == key]

    def _find(self, rows: Sequence[Any], key: str) -> tuple[int, M] | None:
        indexes = self._indexes(rows, key)
        if not indexes:
            return None
        if len(indexes) > 1:
            raise self._damaged(key, key, f"its key is repeated (rows {_rows_text(indexes)})")
        return indexes[0], self._validate(rows[indexes[0]], key)

    def _all(self, rows: Sequence[Any]) -> list[tuple[int, M]]:
        name = self._shape().key
        found: list[tuple[int, M]] = []
        seen: dict[str, int] = {}
        for index, row in enumerate(rows):
            label = f"#{index + 1}"
            if not isinstance(row, dict):
                raise self._damaged(label, None, "it is not a mapping")
            if name is None:
                found.append((index, self._validate(row, label)))
                continue
            key = row.get(name)
            if not isinstance(key, str):
                problem = f"it has no {name}" if key is None else f"its {name} is not a string"
                raise self._damaged(label, None, problem)
            if key in seen:
                repeated = self._indexes(rows, key)
                raise self._damaged(key, key, f"its key is repeated (rows {_rows_text(repeated)})")
            seen[key] = index
            found.append((index, self._validate(row, key)))
        return found

    def _validate(self, row: Any, label: str) -> M:
        try:
            value: M = self._shape().model.model_validate(row, extra="ignore")  # type: ignore[assignment]
        except ValidationError as exc:
            key = label if self._shape().key is not None else None
            raise self._damaged(label, key, first_validation_error(exc)) from None
        return value

    def _remove_line(self, key: str | None) -> str | None:
        """The command line removing the row keyed ``key``, when the rows name one."""
        if key is None:
            return None
        from untaped.sdk._run import current_run  # noqa: PLC0415 - the run imports the SDK

        try:
            return current_run().composition.remove_line(self.plugin, self.name, key)
        except LookupError:  # rows this run did not compose: the hand fix alone
            return None

    def _damaged(self, label: str, key: str | None, problem: str) -> ConfigError:
        path = self.path()
        fix = "correct or remove that row by hand"
        remove = self._remove_line(key)
        if remove is not None:
            fix = f"correct that row by hand, or remove it with `{remove}`"
        return ConfigError(
            f"{self.qualified} row {label} in {path} is damaged: {problem}",
            hint=fix,
            details={"path": str(path), "row": label},
        )


@experimental
class ProjectStateRows[M: BaseModel](OwnedBlock):
    """Typed rows each project keeps (:meth:`StateRows.per_project`); :meth:`of` opens one's.

    They live in the project's ``.untaped/plugins/<plugin>/state.yml``.
    """

    block_kind = "state-rows"

    model: type[M] | None
    omit_none: bool

    def __init__(self, *, model: type[M] | None = None, omit_none: bool = False) -> None:
        super().__init__()
        self.model = model
        self.omit_none = omit_none

    @classmethod
    def name_problem(cls, name: str) -> str | None:
        return StateRows.name_problem(name)

    def problems(self) -> tuple[str, ...]:
        return _shape_problems(self)

    def _keyed_by(self) -> str | None:
        """The model's field keying the rows; ``None``: a list, or a shape composition refuses."""
        return _key_field(self)

    @property
    def qualified(self) -> str:
        return f"{self.plugin}.{self.name}"

    def __repr__(self) -> str:
        return f"ProjectStateRows({self.where!r})"

    def of(self, project: Project) -> StateRows[M]:
        """This project's rows."""
        from untaped.sdk.projects import DIRECTORY  # noqa: PLC0415 - projects imports the registry

        model = self.model if self.model is not None else _resolved_shape(self).model
        plugin, name = self.plugin, self.name
        path = project.root / DIRECTORY / PLUGINS_FOLDER / plugin / _STATE_FILE
        return StateRows._bound(plugin, name, model, self.omit_none, path)  # type: ignore[arg-type]


class _Shape(typing.NamedTuple):
    model: type[BaseModel]
    key: str | None
    """The key field's name in a stored row (its alias, when it has one); ``None``: a list."""
    field: str = ""
    """The key field's name on the model."""


class _ShapeProblem(ValueError):
    """Why rows cannot be read: their model or its ``Key`` marks; the caller names the rows."""


def _resolved_shape(rows: StateRows[Any] | ProjectStateRows[Any]) -> _Shape:
    """The shape of ``rows``; :class:`ConfigError` naming them when they have none."""
    try:
        return _shape_from(rows)
    except _ShapeProblem as exc:
        raise ConfigError(f"state rows {_label(rows)}: {exc}") from None


def _shape_problems(rows: StateRows[Any] | ProjectStateRows[Any]) -> tuple[str, ...]:
    """Why composition refuses ``rows``, which it names itself."""
    try:
        _shape_from(rows)
    except _ShapeProblem as exc:
        return (str(exc),)
    return ()


def _key_field(rows: StateRows[Any] | ProjectStateRows[Any]) -> str | None:
    try:
        return _shape_from(rows).field or None
    except _ShapeProblem:
        return None


def _shape_from(rows: StateRows[Any] | ProjectStateRows[Any]) -> _Shape:
    model: object = rows.model if rows.model is not None else _model_argument(rows)
    return _marked_shape(model)


def _label(rows: StateRows[Any] | ProjectStateRows[Any]) -> str:
    """How a message names ``rows``: ``acme.items``, or where they were made outside a run."""
    try:
        return rows.qualified
    except LookupError, RuntimeError:  # unbound, or no run to name the plugin
        return rows.where


def _shape_of(model: object, key_field: str) -> _Shape:
    model = _row_model(model)
    field = model.model_fields.get(key_field)
    if field is None or field.annotation is not str or not field.is_required():
        raise _ShapeProblem(
            f"{model.__name__}.{key_field}, marked Key, must be a required str field"
        )
    return _Shape(model, field.serialization_alias or field.alias or key_field, key_field)


def _marked_shape(model: object) -> _Shape:
    """The rows' shape from the model's one field marked ``Key``; none makes them a list."""
    from untaped.sdk.fields import Key  # noqa: PLC0415 - fields imports the declaration's blocks

    model = _row_model(model)
    marked = [name for name, field in model.model_fields.items() if Key in field.metadata]
    if not marked:
        return _Shape(model, None)
    if len(marked) > 1:
        raise _ShapeProblem(f"{model.__name__} marks {' and '.join(marked)} Key; a row has one key")
    return _shape_of(model, marked[0])


def _row_model(model: object) -> type[BaseModel]:
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise _ShapeProblem(f"model is not a pydantic model: {model!r}")
    return model


def _model_argument(rows: StateRows[Any] | ProjectStateRows[Any]) -> object:
    """The model a plugin class's rows were subscripted with (``StateRows[Item]()``)."""
    alias = getattr(rows, "__orig_class__", None)
    args: tuple[object, ...] = getattr(alias, "__args__", ())
    if len(args) != 1:
        raise _ShapeProblem("name the rows' model, as in StateRows[Item]()")
    return args[0]


class _RowsAlias(typing._GenericAlias, _root=True):  # type: ignore[call-arg,name-defined,misc]
    """``StateRows[Item]``: the alias that hands its model to ``.per_project()`` too."""

    def per_project(self, *, omit_none: bool = False) -> ProjectStateRows[Any]:
        return ProjectStateRows(model=self.__args__[0], omit_none=omit_none)


def _plugin_state_path(plugin: str) -> Path:
    """``~/.untaped/plugins/<plugin>/state.yml``: the plugin's own rows."""
    from untaped.sdk._run import current_run  # noqa: PLC0415 - the run imports the SDK

    return current_run().home / PLUGINS_FOLDER / plugin / _STATE_FILE


#: The key a state file keeps its stamp under (the data file format's).
STAMP = "untaped"
#: The state file's name, in the home's plugin folder and a project's alike.
_STATE_FILE = "state.yml"
#: How long a write waits for another process writing the same state file.
_WRITE_WAIT = 10.0


class _PluginFile:
    """The rows under ``<name>`` of one plugin's own ``state.yml``, stamped, under its lock."""

    __slots__ = ("_path", "name", "plugin")

    def __init__(self, plugin: str, name: str, path: Path) -> None:
        self.plugin = plugin
        self.name = name
        self._path = path

    def path(self) -> Path:
        return self._path

    def read(self) -> list[Any]:
        return self.rows(self._load())

    def mutate(self, fn: Callable[[list[Any]], list[Any] | None]) -> None:
        """Replace the rows under the file's lock; ``None`` or the same rows write nothing."""
        busy = f"{self.plugin} state is busy (another untaped process)"
        with locked_write(self._path, timeout=_WRITE_WAIT, busy=busy):
            state = self._load()
            before = self.rows(state)
            replacement = fn(copy.deepcopy(before))
            if replacement is None or replacement == before:
                return
            if replacement:
                state[self.name] = replacement
            else:
                state.pop(self.name, None)
            from untaped.sdk.storage.data_file import (  # noqa: PLC0415 - writes only
                FILE,
                written_by,
            )

            state[STAMP] = {"file": FILE, "by": written_by()}
            try:
                write_state_dict(state, self._path)
            except OSError as exc:
                raise ConfigError(
                    f"could not write {self._path}: {exc.strerror or exc}; nothing changed"
                ) from exc

    def _load(self) -> dict[str, Any]:
        from untaped.sdk.storage.data_file import (  # noqa: PLC0415 - kept off startup
            OtherVersion,
            check_stamp,
        )

        state = read_state_dict(self._path)
        stamp = state.get(STAMP)
        if stamp is None:
            return state
        checked = check_stamp(stamp) if isinstance(stamp, Mapping) else None
        if isinstance(checked, OtherVersion):
            raise ConfigError(
                f"{self._path} was written by a newer untaped ({checked.by or 'unknown'}), "
                "whose format this release cannot read",
                hint="upgrade untaped",
                details={"path": str(self._path)},
            )
        if not isinstance(checked, dict):
            raise ConfigError(
                f"{self._path} has a damaged {STAMP!r} stamp; correct it by hand",
                details={"path": str(self._path)},
            )
        return state

    def rows(self, state: Mapping[str, Any]) -> list[Any]:
        raw = state.get(self.name)
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ConfigError(
                f"invalid state: `{self.name}` must be a list of mappings (in {self._path})"
            )
        return list(raw)


def _rows_text(indexes: Sequence[int]) -> str:
    shown = [str(index + 1) for index in indexes]
    return ", ".join(shown[:-1]) + f" and {shown[-1]}"
