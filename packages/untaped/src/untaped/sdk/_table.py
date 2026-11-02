"""A record's columns and table rows: which fields show, in what role and spelling."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from functools import cache
from typing import TYPE_CHECKING, Annotated, get_args, get_origin

from pydantic import BaseModel

from untaped.sdk._keys import SettingKey, model_in
from untaped.sdk.fields import (
    Age,
    Bytes,
    Column,
    Duration,
    Following,
    Key,
    ObjectName,
    Roled,
    Spread,
    Status,
    _plain,
)

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo


def _column_name(column: SettingKey) -> str:
    """The table column ``column`` names: its fields' dumped names, dotted for a nested one."""
    from untaped.sdk.diagnostic import dumped_names  # noqa: PLC0415

    if not SettingKey.path_of(column):
        raise TypeError(f"a table column is a field of the record, not {column!r}")
    model = SettingKey.model_of(column)
    names: list[str] = []
    for attr in SettingKey.path_of(column):
        if attr not in model.model_fields:
            raise TypeError(f"a table column is a field of the record, not a map entry: {column!r}")
        names.append(dumped_names(model)[attr])
        model = model_in(model.model_fields[attr].annotation) or model
    return ".".join(names)


class Columns:
    """A command's default table columns for one run: ``self.output.columns.add(key(Repo).url)``.

    A column is a reference to a field of ``R`` (or of a base it inherits);
    a string is a ``Spread`` key, the one column no field names. Iterating
    gives the column names in order.
    """

    def __init__(self, names: Iterable[str] = (), *, model: type[BaseModel] | None = None) -> None:
        self._names = list(dict.fromkeys(names))
        self._model = model
        #: ``Spread`` keys ``--columns`` may pick that the table hides by default.
        self.offered: list[str] = []

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __repr__(self) -> str:
        return f"Columns({self._names!r})"

    def add(self, *columns: SettingKey | str, first: bool = False) -> None:
        """Show ``columns`` too: after the columns already shown, or before them with ``first``."""
        added = [name for name in self._named(columns) if name not in self._names]
        self._names = [*added, *self._names] if first else [*self._names, *added]

    def drop(self, *columns: SettingKey | str) -> None:
        """Hide ``columns`` (a column the table does not show is no error)."""
        dropped = set(self._named(columns))
        self._names = [name for name in self._names if name not in dropped]

    def show(self, *columns: SettingKey | str) -> None:
        """Show exactly ``columns``, in this order, instead of the defaults."""
        self._names = list(dict.fromkeys(self._named(columns)))

    def offer(self, *keys: str) -> None:
        """Let ``--columns`` pick the ``Spread`` keys ``keys`` without showing them by default."""
        named = self._named(keys)
        self.offered.extend(name for name in named if name not in self.offered)

    def _named(self, columns: Iterable[SettingKey | str]) -> list[str]:
        """Each column's name, checked against ``R`` (:func:`_column_names_of`)."""
        return _column_names_of(columns, self._model)


def _column_names_of(
    columns: Iterable[SettingKey | str], model: type[BaseModel] | None
) -> list[str]:
    """Each column's name: a field by reference, a string only for a key no field declares.

    Such a key is a ``Spread`` key, or one a model that keeps undeclared keys
    carries (an AWX row's ``launch_type``). With no model yet, a reference is
    taken as given and a string waits for the check once the model is known.
    """
    from untaped.sdk.diagnostic import dumped_names  # noqa: PLC0415

    names: list[str] = []
    for column in columns:
        if isinstance(column, SettingKey):
            if model is not None and not issubclass(model, SettingKey.model_of(column)):
                raise TypeError(f"{model.__name__}'s columns do not include {column!r}")
            names.append(_column_name(column))
            continue
        if model is not None:
            fields = {dumped: attr for attr, dumped in dumped_names(model).items()}
            if column in fields:
                raise TypeError(
                    f"name the column {column!r} by its field: "
                    f"key({model.__name__}).{fields[column]}"
                )
            if not spreads(model) and model.model_config.get("extra") != "allow":
                raise TypeError(f"{model.__name__} has no field {column!r} to show as a column")
        names.append(column)
    return names


def table_rows(
    records: Sequence[BaseModel | Mapping[str, object]],
    rows: list[dict[str, object]],
    columns: Sequence[str] | None,
) -> tuple[list[dict[str, object]], list[str] | None, list[str]]:
    """``rows`` as a table shows them, its default ``columns``, and the keys ``Spread`` added.

    A ``Bytes`` field's cell is its size in words, an ``Age`` field's how long
    ago it was by :func:`untaped.sdk.now`'s clock, a ``Duration`` field's its
    run time, an ``ObjectName`` field's its short name, a :class:`TableText` model's
    its ``table_text()``, and a ``Spread`` field gives
    way to a column per key (a key a row already has keeps the row's value). A
    word its field's ``Status`` names is a :class:`Roled` cell.
    """
    from untaped.sdk._run import run_clock  # noqa: PLC0415 - no cycle at import
    from untaped.sdk.diagnostic import dumped_names  # noqa: PLC0415

    now = run_clock().now()
    spread_keys: dict[str, dict[str, None]] = {}
    shown: list[dict[str, object]] = []
    for record, row in zip(records, rows, strict=True):
        if not isinstance(record, BaseModel):
            shown.append(row)
            continue
        fields = {
            dumped: (name, _marks(type(record).model_fields[name]))
            for name, dumped in dumped_names(type(record)).items()
        }
        roles = status_roles(type(record))
        out: dict[str, object] = {}
        for dumped, cell in row.items():  # the dump's order is the table's
            name, marks = fields.get(dumped, (None, []))
            value = getattr(record, name) if name is not None else cell
            if Spread in marks and isinstance(value, Mapping):
                keys = spread_keys.setdefault(dumped, {})
                for key, item in value.items():
                    keys[str(key)] = None
                    if str(key) not in row:
                        out[str(key)] = item
            else:
                out[dumped] = _marked_cell(marks, value, cell, now)
            role = _role(roles.get(name or ""), value)
            if role is not None and isinstance(text := out.get(dumped), str):
                out[dumped] = Roled(text, role)
        shown.append(out)
    if columns is not None and spread_keys:
        columns = [key for name in columns for key in (spread_keys.get(name) or [name])]
    added = [key for keys in spread_keys.values() for key in keys]
    return shown, None if columns is None else list(columns), added


def table_row(record: BaseModel, row: Mapping[str, object] | None = None) -> dict[str, object]:
    """``row`` (``record``'s dump by default) as a table shows it: marked cells, roled words.

    For a view a command builds from a record (a ``fix`` argv as the line to
    type, warnings joined into one cell) and prints itself: a plain ``dict`` row
    has no fields, so a table draws its words in no role. A key ``record`` has
    no field for stays as it is.
    """
    shown = dict(record.model_dump(mode="json") if row is None else row)
    [out], _, _ = table_rows([record], [shown], None)
    return out


def _marked_cell(marks: list[object], value: object, cell: object, now: datetime) -> object:
    """A cell as its field's table mark shows it (``cell``, the dumped value, when unmarked)."""
    from untaped.sdk.words import ago_text, elapsed_text, size_text  # noqa: PLC0415

    if Bytes in marks and isinstance(value, int):
        return size_text(value)
    if Age in marks and isinstance(value, datetime):
        return ago_text(value, now)
    if Duration in marks and isinstance(value, int | float) and not isinstance(value, bool):
        return elapsed_text(value)
    if ObjectName in marks and isinstance(value, str) and _FULL_NAME.fullmatch(value):
        return value[:_SHORT_NAME]
    from untaped.sdk.records import TableText  # noqa: PLC0415 - records import fields

    if isinstance(value, BaseModel) and isinstance(value, TableText):
        return value.table_text()
    return cell


def _role(words: Mapping[str, str] | None, value: object) -> str | None:
    """The role ``value``'s word is drawn in: its field's ``Status``."""
    word = _plain(value)
    if not isinstance(word, str):
        return None  # a number, a list, ``None``: no word to draw
    return words.get(word) if words else None


#: A full git object name, and how much of it a table shows.
_FULL_NAME = re.compile(r"[0-9a-f]{40}")


_SHORT_NAME = 10


def spreads(model: type[BaseModel]) -> bool:
    """Whether ``model`` has a ``Spread`` field, whose keys a run names as columns."""
    return any(Spread in _marks(info) for info in model.model_fields.values())


def default_columns(model: type[BaseModel]) -> tuple[str, ...]:
    """A table's default columns for ``model``: its ``Column`` fields, in dump order.

    A field a record base marks (an outcome's ``action``, a check's
    ``status``) joins the record's own marks; a record marking none of its
    own fields shows every field, so the base's marks alone are no default.
    """
    from untaped.sdk.diagnostic import WireModel, _field_order, dumped_names  # noqa: PLC0415
    from untaped.sdk.records import (  # noqa: PLC0415 - records import fields
        CheckRecord,
        OutcomeRecord,
    )

    marked = set(column_fields(model))
    joining = {
        name
        for base in (OutcomeRecord, CheckRecord)
        if issubclass(model, base)
        for name in column_fields(base)
    }
    if marked <= joining:
        return ()
    keys = dumped_names(model)
    shown = {keys[name] for name in marked}
    order = _field_order(model) if issubclass(model, WireModel) else tuple(keys.values())
    return tuple(key for key in order if key in shown)


def column_fields(model: type[BaseModel]) -> tuple[str, ...]:
    """The fields of ``model`` marked ``Column``, by field name."""
    return tuple(name for name, info in model.model_fields.items() if Column in _marks(info))


def following_fields(model: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """The fields of ``model`` marked ``Following``, each with the model of its items.

    A table prints each as a second table after the first; a field that is
    not a list of models holds nothing to print (composition refuses it).
    """
    found: dict[str, type[BaseModel]] = {}
    for name, info in model.model_fields.items():
        if Following in _marks(info) and (item := list_item_model(info.annotation)) is not None:
            found[name] = item
    return found


def unprintable_following(model: type[BaseModel]) -> list[str]:
    """The fields of ``model`` marked ``Following`` that are not a list of models."""
    return [
        name
        for name, info in model.model_fields.items()
        if Following in _marks(info) and list_item_model(info.annotation) is None
    ]


def list_item_model(annotation: object) -> type[BaseModel] | None:
    """The model ``annotation`` lists (``list[M]``, ``list[M] | None``), if it lists one."""
    for each in (annotation, *get_args(annotation)):
        if get_origin(each) is Annotated:
            each = get_args(each)[0]
        if get_origin(each) is list:
            [item] = get_args(each) or (None,)
            if isinstance(item, type) and issubclass(item, BaseModel):
                return item
    return None


def key_field(model: type[BaseModel]) -> str | None:
    """The field of ``model`` marked ``Key``: what a bare identifier stands for."""
    return next((name for name, info in model.model_fields.items() if Key in _marks(info)), None)


def _marks(info: FieldInfo) -> list[object]:
    """A field's marks, those inside ``X | None`` too (pydantic lifts only top-level ones)."""
    marks = list(info.metadata)
    for arg in get_args(info.annotation):
        if get_origin(arg) is Annotated:
            marks.extend(get_args(arg)[1:])
    return marks


@cache
def status_roles(model: type[BaseModel]) -> dict[str, dict[str, str]]:
    """Each field of ``model`` with a ``Status``, its word → role map merged along the MRO."""
    found: dict[str, dict[str, str]] = {}
    for cls in reversed(model.__mro__):
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            continue
        for name, info in cls.model_fields.items():  # an inherited field repeats its base's
            for mark in _marks(info):
                if isinstance(mark, Status):
                    found[name] = {**found.get(name, {}), **mark.roles}
    return found


def _type_argument(annotation: object) -> type[BaseModel] | None:
    """The model a generic field names (``Output[R]`` gives ``R``), if it is one model."""
    args = get_args(annotation)
    model = args[0] if args else None
    return model if isinstance(model, type) and issubclass(model, BaseModel) else None
