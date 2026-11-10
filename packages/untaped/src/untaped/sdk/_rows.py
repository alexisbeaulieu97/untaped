"""Records as rows: a model or mapping as a plain row, and its fields, kind and glyphs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from typing import get_args

from pydantic import BaseModel

from untaped.sdk.diagnostic import WireModel, _field_order, dumped_names
from untaped.sdk.records import OutcomeRecord, TableGlyph, check_data_kind, kind_of


def _validate_kind(kind: str | None) -> None:
    """Reject emit kinds that break the ``<plugin>.<noun>[.summary]`` grammar.

    Raises ``ValueError`` (not ``UntapedError``): a bad kind is a programming
    error that must surface at development time, not a user-facing condition
    for ``report_errors`` to soften.
    """
    if kind is not None:
        check_data_kind(kind)


def _rows_kind(items: Sequence[BaseModel | Mapping[str, object]], kind: str | None) -> str | None:
    """The kind of an emit: the one its rows declare, else ``kind``.

    Raises ``ValueError`` (a programming error) when ``kind`` names another
    kind than a row declares, or rows declare different kinds, or only some
    rows declare one: each kind has exactly one schema.
    """
    declared = {kind_of(type(item)) if isinstance(item, BaseModel) else None for item in items}
    if kind is not None:
        if declared - {None, kind}:
            others = ", ".join(sorted(repr(each) for each in declared - {None, kind}))
            raise ValueError(f"emit(kind={kind!r}) on records of kind {others}")
        return kind
    if len(declared) > 1:
        shown = ", ".join(sorted("no kind" if each is None else repr(each) for each in declared))
        raise ValueError(f"emit() rows declare different kinds ({shown}); emit each kind apart")
    return declared.pop() if declared else None


def _reports_change(items: Sequence[BaseModel | Mapping[str, object]]) -> bool:
    """Whether a row is an :class:`OutcomeRecord`: what a mutation did, after it ran."""
    return any(isinstance(item, OutcomeRecord) for item in items)


def _model_schema(
    records: BaseModel | Mapping[str, object] | Sequence[BaseModel | Mapping[str, object]],
) -> list[str] | None:
    """Field names when every record is a pydantic model (a known schema).

    A model with ``extra="allow"`` passes a service's answer through, so its
    schema is open, as a mapping row's is (``None``).
    """
    items = [records] if isinstance(records, BaseModel | Mapping) else list(records)
    if not items or not all(
        isinstance(item, BaseModel) and item.model_config.get("extra") != "allow" for item in items
    ):
        return None
    names: dict[str, None] = {}
    for item in items:
        model = type(item)
        assert issubclass(model, BaseModel)
        names.update(dict.fromkeys(_model_fields(model)))
    return list(names)


def _model_fields(model: type[BaseModel]) -> list[str]:
    """The columns ``model`` rows have: their dumped field names, then computed fields.

    A record's fields come in the order its rows are written: own fields
    first, then those it inherits (an outcome's ``action`` and ``error``).
    """
    fields = _field_order(model) if issubclass(model, WireModel) else dumped_names(model).values()
    return [*fields, *model.model_computed_fields]


def _record_table_columns(items: Sequence[BaseModel | Mapping[str, object]]) -> list[str] | None:
    """The default ``table`` columns every record's type declares (``None``: no shared ones)."""
    from untaped.sdk._table import default_columns  # noqa: PLC0415 - fields import this module

    declared = {
        default_columns(type(item)) if isinstance(item, BaseModel) else () for item in items
    }
    return (list(declared.pop()) or None) if len(declared) == 1 else None


def _row_glyphs(
    items: Sequence[BaseModel | Mapping[str, object]],
) -> list[dict[str, TableGlyph]] | None:
    """Each record's :class:`TableGlyph` fields (``None``: no record has any)."""
    glyphs = [_glyph_fields(type(item)) if isinstance(item, BaseModel) else {} for item in items]
    return glyphs if any(glyphs) else None


@cache
def _glyph_fields(model: type[BaseModel]) -> dict[str, TableGlyph]:
    """The fields of ``model`` annotated with a :class:`TableGlyph` (also inside ``| None``)."""
    return {
        name: meta
        for name, field in model.model_fields.items()
        for meta in [
            *field.metadata,
            *(extra for arg in get_args(field.annotation) for extra in _metadata(arg)),
        ]
        if isinstance(meta, TableGlyph)
    }


def _metadata(annotation: object) -> tuple[object, ...]:
    """The ``Annotated`` metadata of ``annotation`` (none when not annotated)."""
    return tuple(getattr(annotation, "__metadata__", ()))


def _as_row(record: BaseModel | Mapping[str, object]) -> dict[str, object]:
    """Normalize a model or mapping into a plain row dict.

    Models dump in JSON mode so paths, enums, dates, etc. become plain
    JSON-compatible values every output format can encode.
    """
    if isinstance(record, BaseModel):
        return record.model_dump(mode="json")
    return dict(record)
