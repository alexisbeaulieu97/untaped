"""Plugin state in ``state.yml``: typed rows, string maps and dict rows.

The shared state file is co-owned by every untaped plugin, so all writes ride
:func:`untaped.config_file.mutate_plugin_state` (section-scoped, locked,
atomic). One row layer (:class:`_RowFile`) serves every kind:

- :class:`StateRows` keeps typed rows, one per key, checked against their
  model. It is the only copy: a damaged row stops the command and names the
  file, the row and the fix. Fields a newer untaped added are written back as
  they were.
- :class:`StateMap` is a flat ``str → str`` map (ansible aliases shape).
- :class:`StateCollection` is a list of dict records keyed by an id field.

Each drops its key when emptied so the enclosing section can collapse. Rows of
a project live in its own ``.untaped/state.yml``
(:meth:`StateRows.per_project`, ``StateMap(project=)``).
"""

from __future__ import annotations

import sys
import types
import typing
import weakref
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from pydantic import AliasChoices, BaseModel, ValidationError

from untaped.config_file import mutate_plugin_state, read_plugin_state
from untaped.errors import ConfigError, ErrorCategory, UntapedError, first_validation_error
from untaped.settings import CORE_STATE_SECTIONS, RESERVED_SECTIONS, resolve_state_path
from untaped.stability import experimental

if TYPE_CHECKING:
    from untaped.projects import Project

type Rows = list[dict[str, Any]]

_HERE = frozenset({__name__})


class _RowFile:
    """The rows under ``<section>.<name>`` of one state file (the home's unless ``path``)."""

    __slots__ = ("_core", "_path", "name", "section")

    def __init__(self, section: str, name: str, path: Path | None, *, core: bool = False) -> None:
        self.section = section
        self.name = name
        self._path = path
        self._core = core

    def path(self) -> Path:
        return self._path or resolve_state_path()

    def read(self) -> list[Any]:
        """The raw rows; ``[]`` when unset. Anything but a list raises ConfigError."""
        return self.rows(read_plugin_state(self.section, self._path, core=self._core))

    def mutate(self, fn: Callable[[list[Any]], list[Any] | None]) -> None:
        """Replace the rows under the state file's lock; ``None`` from ``fn`` writes nothing."""

        def _apply(state: dict[str, Any]) -> None:
            replacement = fn(self.rows(state))
            if replacement is None:
                return
            if replacement:
                state[self.name] = replacement
            else:
                state.pop(self.name, None)

        mutate_plugin_state(self.section, _apply, self._path, core=self._core)

    def rows(self, state: Mapping[str, Any]) -> list[Any]:
        if self.name not in state:
            return []
        raw = state[self.name]
        if not isinstance(raw, list):
            raise ConfigError(
                f"invalid state: `{self.section}.{self.name}` must be a list of mappings "
                f"(in {self.path()})"
            )
        return list(raw)


#: Every row declaration made so far, with the module that made it, so composition can
#: flag one its plugin's own package declares but the plugin doesn't list.
_DECLARED: list[tuple[weakref.ref[StateRows[Any] | ProjectStateRows[Any]], str]] = []


def declared_rows(plugin: str, home: str) -> list[StateRows[Any] | ProjectStateRows[Any]]:
    """The row declarations for ``plugin`` made in the module ``home`` or, a package, under it.

    ``home`` is a module's name, with a trailing ``.`` when it is a package
    (:func:`caller_home`).
    """
    _DECLARED[:] = [(ref, module) for ref, module in _DECLARED if ref() is not None]
    return [
        each
        for ref, module in _DECLARED
        if (each := ref()) is not None
        and each.plugin == plugin
        and (module == home.rstrip(".") or (home.endswith(".") and module.startswith(home)))
    ]


def _declare(declared: StateRows[Any] | ProjectStateRows[Any]) -> None:
    _DECLARED.append((weakref.ref(declared), caller_home(_HERE).rstrip(".")))


def caller_home(skip: frozenset[str]) -> str:
    """The module of the nearest caller outside ``skip`` (as namedtuple finds one).

    A package's name ends in ``.``, so what lies under it can be told apart.
    """
    frame: types.FrameType | None = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") in skip:
        frame = frame.f_back
    if frame is None:
        return ""
    name = str(frame.f_globals.get("__name__", ""))
    return f"{name}." if "__path__" in frame.f_globals else name


@experimental
class RowExists(ConfigError):
    """:meth:`StateRows.add` found a row with the same key (exits ``1``)."""

    category = ErrorCategory.CONFLICT


@experimental
class StateRows[M: BaseModel]:
    """Typed rows a plugin keeps as the only copy, in ``state.yml`` → ``<plugin>.<name>``.

    ``model`` is the rows' pydantic model, or a function of no arguments
    returning it when importing the class would load more than a declaration
    should; it is resolved on first use. ``key_field`` names the model's
    required ``str`` field that keys the rows: one row per key. A row is
    written ``mode="json"``, by alias; ``omit_none`` leaves its ``None``
    fields out. ``remove_hint`` is the plugin's command that removes a row,
    with ``{key}`` for its key (``"untaped acme forget {key}"``): a damaged
    row's error offers it.

    Rows are read with ``extra="ignore"``, nested models included, and the
    fields a newer untaped added are written back as they were. A row that
    fails its model, has no key, or shares its key with another is damaged:
    reading it (``get``, ``all``) or changing it stops with a
    :class:`~untaped.errors.ConfigError` (exit 4) naming the file, the row and
    the fix, while ``keys``, other rows and ``delete`` keep working.

    List it on the plugin's ``PluginSpec.stores``. Change a row with
    :meth:`update`, never ``get`` then ``put``: ``update`` runs under the
    state file's lock.
    """

    __slots__ = (
        "__weakref__",
        "_file",
        "_resolved",
        "key_field",
        "model",
        "name",
        "omit_none",
        "plugin",
        "remove_hint",
    )

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        model: type[M] | Callable[[], type[M]],
        key_field: str,
        omit_none: bool = False,
        remove_hint: str | None = None,
    ) -> None:
        _check_declaration(plugin, name, model, key_field, remove_hint)
        self._setup(plugin, name, model, key_field, omit_none, remove_hint, None)
        _declare(self)

    @classmethod
    def per_project(
        cls,
        plugin: str,
        name: str,
        *,
        model: type[M] | Callable[[], type[M]],
        key_field: str,
        omit_none: bool = False,
        remove_hint: str | None = None,
    ) -> ProjectStateRows[M]:
        """Rows each project keeps in its ``.untaped/state.yml``; ``.of(project)`` opens them."""
        return ProjectStateRows(
            plugin,
            name,
            model=model,
            key_field=key_field,
            omit_none=omit_none,
            remove_hint=remove_hint,
        )

    @classmethod
    def _bound(
        cls,
        plugin: str,
        name: str,
        model: type[M] | Callable[[], type[M]],
        key_field: str,
        omit_none: bool,
        remove_hint: str | None,
        path: Path | None,
        *,
        core: bool = False,
    ) -> StateRows[M]:
        rows: StateRows[M] = object.__new__(cls)
        rows._setup(plugin, name, model, key_field, omit_none, remove_hint, path, core=core)
        return rows

    def _setup(
        self,
        plugin: str,
        name: str,
        model: type[M] | Callable[[], type[M]],
        key_field: str,
        omit_none: bool,
        remove_hint: str | None,
        path: Path | None,
        *,
        core: bool = False,
    ) -> None:
        self.plugin = plugin
        self.name = name
        self.model = model
        self.key_field = key_field
        self.omit_none = omit_none
        self.remove_hint = remove_hint
        self._resolved: _Shape | None = None
        self._file = _RowFile(plugin, name, path, core=core)

    @property
    def qualified(self) -> str:
        return f"{self.plugin}.{self.name}"

    def _identity(self) -> tuple[object, ...]:
        return (
            self.plugin,
            self.name,
            self.model,
            self.key_field,
            self.omit_none,
            self.remove_hint,
            self._file._path,
        )

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"StateRows({self.plugin!r}, {self.name!r})"

    def path(self) -> Path:
        """The state file these rows live in."""
        return self._file.path()

    # -- reads ------------------------------------------------------------

    def get(self, key: str) -> M | None:
        """The row keyed ``key``, or ``None``; a damaged one stops (exit 4)."""
        found = self._find(self._file.read(), key)
        return None if found is None else found[1]

    def all(self) -> list[M]:
        """Every row, in file order; the first damaged one stops (exit 4)."""
        return [value for _, value in self._all(self._file.read())]

    def keys(self) -> list[str]:
        """Every row's key, in file order and once each; never stops on a damaged row."""
        shape = self._shape()
        seen: dict[str, None] = {}
        for row in self._file.read():
            key = row.get(shape.key) if isinstance(row, dict) else None
            if isinstance(key, str):
                seen[key] = None
        return list(seen)

    # -- writes -----------------------------------------------------------

    def add(self, value: M) -> None:
        """Append ``value``; :class:`RowExists` when its key already has a row."""
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
            model = self.model if isinstance(self.model, type) else self.model()
            self._resolved = _shape_of(self.qualified, model, self.key_field)
        return self._resolved

    def _key_of(self, value: M) -> str:
        shape = self._shape()
        if not isinstance(value, shape.model):
            raise UntapedError(
                f"{self.qualified} keeps {shape.model.__name__} rows, not {type(value).__name__}"
            )
        key: str = getattr(value, self.key_field)
        return key

    def _dump(self, value: M) -> dict[str, Any]:
        return value.model_dump(mode="json", by_alias=True, exclude_none=self.omit_none)

    def _written(self, stored: dict[str, Any], current: M, new: M) -> dict[str, Any]:
        """``new`` as written over ``stored`` (whose validated value is ``current``)."""
        shape = self._shape()
        return _write_back(shape.model, stored, self._dump(current), self._dump(new))

    def _indexes(self, rows: Sequence[Any], key: str) -> list[int]:
        name = self._shape().key
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

    def _validate(self, row: Any, key: str) -> M:
        try:
            value: M = self._shape().model.model_validate(row, extra="ignore")  # type: ignore[assignment]
        except ValidationError as exc:
            raise self._damaged(key, key, first_validation_error(exc)) from None
        return value

    def _damaged(self, label: str, key: str | None, problem: str) -> ConfigError:
        path = self.path()
        fix = "correct or remove that row by hand"
        if key is not None and self.remove_hint is not None:
            fix = (
                f"correct that row by hand, or remove it with `{self.remove_hint.format(key=key)}`"
            )
        return ConfigError(
            f"{self.qualified} row {label} in {path} is damaged: {problem}",
            hint=fix,
            details={"path": str(path), "row": label},
        )


@experimental
class ProjectStateRows[M: BaseModel]:
    """Typed rows each project keeps in its ``.untaped/state.yml`` (:meth:`StateRows.per_project`).

    List it on the plugin's ``PluginSpec.stores``; :meth:`of` opens one
    project's rows.
    """

    __slots__ = ("__weakref__", "key_field", "model", "name", "omit_none", "plugin", "remove_hint")

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        model: type[M] | Callable[[], type[M]],
        key_field: str,
        omit_none: bool = False,
        remove_hint: str | None = None,
    ) -> None:
        _check_declaration(plugin, name, model, key_field, remove_hint)
        self.plugin = plugin
        self.name = name
        self.model = model
        self.key_field = key_field
        self.omit_none = omit_none
        self.remove_hint = remove_hint
        _declare(self)

    @property
    def qualified(self) -> str:
        return f"{self.plugin}.{self.name}"

    def _identity(self) -> tuple[object, ...]:
        return (
            self.plugin,
            self.name,
            self.model,
            self.key_field,
            self.omit_none,
            self.remove_hint,
        )

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"ProjectStateRows({self.plugin!r}, {self.name!r})"

    def of(self, project: Project) -> StateRows[M]:
        """This project's rows, in ``<project>/.untaped/state.yml``."""
        return StateRows._bound(
            self.plugin,
            self.name,
            self.model,
            self.key_field,
            self.omit_none,
            self.remove_hint,
            project_state_path(project),
        )


def core_rows[M: BaseModel](
    section: str,
    name: str,
    *,
    model: type[M] | Callable[[], type[M]],
    key_field: str,
    omit_none: bool = False,
    remove_hint: str | None = None,
) -> StateRows[M]:
    """Core's own typed rows, in a state section core reserves (``credentials``).

    Core's, not the SDK's: no plugin may keep rows there.
    """
    if section not in CORE_STATE_SECTIONS:
        raise ConfigError(f"core_rows: {section!r} is not a state section core reserves")
    _check_declaration(None, name, model, key_field, remove_hint)
    return StateRows._bound(
        section, name, model, key_field, omit_none, remove_hint, None, core=True
    )


def project_state_path(project: Project) -> Path:
    """``<project>/.untaped/state.yml``: where a project's state rows live."""
    from untaped.projects import DIRECTORY  # noqa: PLC0415 - projects imports the registry
    from untaped.settings import STATE_FILE_NAME  # noqa: PLC0415

    return project.root / DIRECTORY / STATE_FILE_NAME


def _check_declaration(
    plugin: str | None,
    name: str,
    model: object,
    key_field: str,
    remove_hint: str | None,
) -> None:
    from untaped.plugins.registry import check_plugin_name  # noqa: PLC0415 - no cycle

    if plugin is not None:
        check_plugin_name(plugin)
        if plugin in RESERVED_SECTIONS:
            raise ConfigError(f"state rows {plugin}.{name}: {plugin!r} is a section core reserves")
    if not isinstance(name, str) or not name.isidentifier():
        raise ConfigError(f"state rows name {name!r} must be one identifier")
    if not callable(model):
        raise ConfigError(
            f"state rows {name!r}: model takes a model class or a function returning one"
        )
    if not isinstance(key_field, str) or not key_field:
        raise ConfigError(f"state rows {name!r}: key_field must name a field")
    if remove_hint is not None and (not isinstance(remove_hint, str) or "{key}" not in remove_hint):
        raise ConfigError(f"state rows {name!r}: remove_hint must contain {{key}}")


class _Shape(typing.NamedTuple):
    model: type[BaseModel]
    key: str
    """The key field's name in a stored row (its alias, when it has one)."""


def _shape_of(qualified: str, model: object, key_field: str) -> _Shape:
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ConfigError(f"state rows {qualified}: model is not a pydantic model: {model!r}")
    field = model.model_fields.get(key_field)
    if field is None or field.annotation is not str or not field.is_required():
        raise ConfigError(
            f"state rows {qualified}: key_field {key_field!r} must be a required str field "
            f"of {model.__name__}"
        )
    return _Shape(model, field.serialization_alias or field.alias or key_field)


def _rows_text(indexes: Sequence[int]) -> str:
    shown = [str(index + 1) for index in indexes]
    return ", ".join(shown[:-1]) + f" and {shown[-1]}"


# -- the unknown-fields rule ----------------------------------------------


def _write_back(
    model: type[BaseModel], stored: Mapping[str, Any], known: Mapping[str, Any], new: dict[str, Any]
) -> dict[str, Any]:
    """``new`` (a dump) written over ``stored`` (a raw row whose known part dumps as ``known``).

    A field whose known part is unchanged is written exactly as stored; a
    changed nested model merges one level down by the same rule; a list keeps
    an element's stored form when its dump is unchanged at the same index.
    Keys the model doesn't know (a newer untaped's) are kept; the stored order
    is kept, new keys follow.
    """
    out: dict[str, Any] = {}
    for key, value in new.items():
        old, before = stored.get(key), known.get(key)
        if key in stored and key in known and before == value:
            out[key] = old
        elif isinstance(old, dict) and isinstance(before, dict) and isinstance(value, dict):
            nested = _nested_model(model, key)
            out[key] = value if nested is None else _write_back(nested, old, before, value)
        elif (
            isinstance(old, list)
            and isinstance(before, list)
            and isinstance(value, list)
            and len(old) == len(before)
        ):
            out[key] = [
                old[i] if i < len(before) and before[i] == item else item
                for i, item in enumerate(value)
            ]
        else:
            out[key] = value
    names = _known_keys(model)
    for key, value in stored.items():
        if key not in names:
            out[key] = value
    order = [key for key in stored if key in out]
    order += [key for key in out if key not in stored]
    return {key: out[key] for key in order}


def _known_keys(model: type[BaseModel]) -> frozenset[str]:
    """Every key a row of ``model`` may spell a field with: names and aliases."""
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        keys.add(name)
        for alias in (field.alias, field.serialization_alias):
            if alias:
                keys.add(alias)
        validation = field.validation_alias
        if isinstance(validation, str):
            keys.add(validation)
        elif isinstance(validation, AliasChoices):
            keys.update(choice for choice in validation.choices if isinstance(choice, str))
    return frozenset(keys)


def _nested_model(model: type[BaseModel], key: str) -> type[BaseModel] | None:
    """The model class of ``model``'s field stored as ``key`` (``X | None`` too), if it is one."""
    for name, field in model.model_fields.items():
        if key in (name, field.alias, field.serialization_alias):
            annotation = field.annotation
            options = (
                typing.get_args(annotation)
                if typing.get_origin(annotation) in (Union, types.UnionType)
                else (annotation,)
            )
            models = [
                each for each in options if isinstance(each, type) and issubclass(each, BaseModel)
            ]
            return models[0] if len(models) == 1 else None
    return None


# -- dict rows and string maps --------------------------------------------


class StateCollection:
    """A list-of-records plugin-state collection keyed by ``id_field``.

    Rows are the caller's dicts, written as given; a row without the id
    field is never matched. Prefer :class:`StateRows` for typed rows.
    """

    def __init__(self, section: str, key: str, *, id_field: str = "name") -> None:
        self.plugin = section
        self.name = key
        self._id_field = id_field
        self._file = _RowFile(section, key, None)

    def entries(self) -> list[dict[str, Any]]:
        """All records; ``[]`` when unset. Malformed state raises ConfigError."""
        return self._mappings(self._file.read())

    def get(self, ident: str) -> dict[str, Any] | None:
        """The record whose id field equals ``ident``, or ``None``."""
        for row in self.entries():
            if row.get(self._id_field) == ident:
                return row
        return None

    def upsert(self, record: Mapping[str, Any]) -> None:
        """Insert ``record``, replacing any existing record with the same id."""
        ident = self._record_id(record)
        self.mutate(
            lambda rows: [
                *[row for row in rows if row.get(self._id_field) != ident],
                dict(record),
            ]
        )

    def insert(self, record: Mapping[str, Any]) -> None:
        """Insert ``record``; a ``conflict`` ConfigError when its id already exists."""
        ident = self._record_id(record)

        def _replace(rows: Rows) -> Rows:
            if any(row.get(self._id_field) == ident for row in rows):
                raise ConfigError(
                    f"state record {ident!r} already exists in `{self.plugin}.{self.name}`",
                    category="conflict",
                )
            return [*rows, dict(record)]

        self.mutate(_replace)

    def mutate(self, fn: Callable[[Rows], Rows]) -> Rows:
        """Replace rows under the state-file lock and return the replacement.

        A state write from inside ``fn`` (any state change) raises
        :class:`~untaped.errors.UntapedError`: read first, then write once.
        """
        replacement: Rows = []

        def _apply(rows: list[Any]) -> list[Any]:
            nonlocal replacement
            replacement = self._validate_rows(fn(self._mappings(rows)))
            return [dict(row) for row in replacement]

        self._file.mutate(_apply)
        return [dict(row) for row in replacement]

    def remove(self, ident: str) -> bool:
        """Remove the record with id ``ident``; report whether one existed."""
        removed = False

        def _apply(raw: list[Any]) -> list[Any] | None:
            nonlocal removed
            rows = self._mappings(raw)
            kept = [row for row in rows if row.get(self._id_field) != ident]
            removed = len(kept) != len(rows)
            return kept if removed else None

        self._file.mutate(_apply)
        return removed

    def _mappings(self, rows: list[Any]) -> Rows:
        if not all(isinstance(row, dict) for row in rows):
            raise ConfigError(
                f"invalid state: `{self.plugin}.{self.name}` must be a list of mappings"
            )
        return [dict(row) for row in rows]

    def _record_id(self, record: Mapping[str, Any]) -> str:
        ident = record.get(self._id_field)
        if not isinstance(ident, str) or not ident:
            raise ConfigError(
                f"state record for `{self.plugin}.{self.name}` must include {self._id_field!r}"
            )
        return ident

    def _validate_rows(self, rows: Rows) -> Rows:
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ConfigError(
                f"invalid state mutation: `{self.plugin}.{self.name}` replacement "
                "must be a list of mappings"
            )
        return [dict(row) for row in rows]


class StateMap:
    """A flat ``str → str`` plugin-state map; ``project=`` keeps it in that project's state file."""

    def __init__(self, section: str, key: str, *, project: Project | None = None) -> None:
        self.plugin = section
        self.name = key
        self._path = None if project is None else project_state_path(project)

    def entries(self) -> dict[str, str]:
        """The whole map; ``{}`` when unset. Malformed state raises ConfigError."""
        return self._mapping(read_plugin_state(self.plugin, self._path))

    def get(self, key: str) -> str | None:
        return self.entries().get(key)

    def set(self, key: str, value: str) -> None:
        def _apply(state: dict[str, Any]) -> None:
            mapping = self._mapping(state)
            mapping[key] = value
            state[self.name] = mapping

        mutate_plugin_state(self.plugin, _apply, self._path)

    def remove(self, key: str) -> bool:
        removed = False

        def _apply(state: dict[str, Any]) -> None:
            nonlocal removed
            mapping = self._mapping(state)
            if key not in mapping:
                return
            del mapping[key]
            removed = True
            if mapping:
                state[self.name] = mapping
            else:
                state.pop(self.name, None)

        mutate_plugin_state(self.plugin, _apply, self._path)
        return removed

    def _mapping(self, state: dict[str, Any]) -> dict[str, str]:
        if self.name not in state:
            return {}
        raw = state.get(self.name)
        if not isinstance(raw, dict):
            raise ConfigError(f"invalid state: `{self.plugin}.{self.name}` must be a mapping")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
            raise ConfigError(f"invalid state: `{self.plugin}.{self.name}` must be a string map")
        return dict(raw)
