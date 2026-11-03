"""The Stdin and Lines blocks: rows or lines a command reads from a pipe or its arguments."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError

from untaped.sdk._block import FieldBlock, FieldOption, Invocation
from untaped.sdk._output import _copy
from untaped.sdk._table import key_field
from untaped.sdk.errors import (
    ConfigError,
    UsageError,
    first_validation_error,
)
from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from untaped.sdk._pipe import PipeEnvelope


class Stdin[R: BaseModel](FieldBlock):
    """What a command accepts on stdin: ``stdin: Stdin[Salutation]`` adds ``--stdin``.

    ``R`` is the record kind (or a union, or a base every accepted kind
    subclasses; ``Stdin[Record]`` takes any installed kind, and reads a
    row whose kind is ``null`` with every field it carries); a bare line
    stands for ``R``'s ``Key`` field. Accepting a kind never claims it.

    ``Stdin(via=RepoSource.to_repo)`` also takes the rows of any provider's
    own kind and reads each through that provider's bridge, so ``R`` is the
    owner's model; a row ``R`` takes reads as it would without ``via``. The
    contract's providers are built, and a provider's settings read, only
    when a row of a provider's kind arrives. A record bridge reads the same
    way: ``Stdin[RepoSelector](via=RepoSource.to_selector)`` takes each
    provider's own selector rows through the fill that names their record.
    A kind no provider reads, or a
    kind-less line, exits 2; a provider's row that doesn't validate as its
    record exits 1; an unready provider, or a bridge returning an invalid
    owner model, exits 4.
    """

    field_kind = "stdin"
    options = (FieldOption("--stdin", "bool", "Read the input from stdin."),)

    def __init__(self, *, via: Callable[..., Any] | None = None) -> None:
        self.via = via
        self._requested = False
        self._models: tuple[type[BaseModel], ...] = ()
        #: Each kind read as its model; with ``_any`` the kinds core knows.
        self._kinds: Mapping[str, type[BaseModel]] = {}
        #: Every kind core knows, for a row ``via`` reads through its provider.
        self._known: Mapping[str, type[BaseModel]] = {}
        self._any = False

    def _fill(self, flags: Mapping[str, object], call: Invocation) -> Stdin[R]:
        """A copy for one invocation: ``--stdin``, and the kinds ``R`` accepts.

        A kind is accepted when its model is one of ``R``'s models or a subclass;
        a model that is a record base (``Stdin[Record]``) accepts any kind.
        """
        from untaped.sdk.diagnostic import WireModel  # noqa: PLC0415 - reading only
        from untaped.sdk.records import Record  # noqa: PLC0415 - reading only

        args = get_args(call.annotation)
        accepted = args[0] if args else None
        members = get_args(accepted) if get_origin(accepted) in (UnionType, Union) else (accepted,)
        models = tuple(m for m in members if isinstance(m, type) and issubclass(m, BaseModel))
        filled = _copy(self)
        filled._requested = bool(flags.get("--stdin"))
        filled._models = models
        filled._any = any(model in (BaseModel, WireModel, Record) for model in models)
        known = call.kinds()
        filled._kinds = {
            kind: model for kind, model in known.items() if filled._any or issubclass(model, models)
        }
        if self.via is not None:
            filled._known = known  # composition checked that ``via`` is a bridge returning R
        return filled

    @property
    def requested(self) -> bool:
        """``--stdin`` was passed: what to read comes from stdin, whatever it carries."""
        return self._requested

    def identifiers(self, given: Sequence[str] = (), *, allow_empty: bool = False) -> list[str]:
        """The identifiers given, or read from stdin: bare lines, or each row's ``Key`` field.

        Both at once, or neither (unless ``allow_empty``), is a usage error.
        """
        return [
            value if isinstance(value, str) else self._identifier(value)
            for value in self._read(given, allow_empty=allow_empty)
        ]

    def values(self, given: Sequence[str] = (), *, allow_empty: bool = False) -> list[str | R]:
        """The arguments as given, or what stdin carried: each a bare line or a validated row."""
        return [
            value if isinstance(value, str) else self._row(value)
            for value in self._read(given, allow_empty=allow_empty)
        ]

    @experimental
    def text(self, *, what: str) -> str:
        """Stdin's whole text, for an argument whose ``-`` names stdin (``run W -``).

        For a command that tells ``-`` from a value itself, where
        :data:`TextOrStdin` would hand it the text in the value's place. One
        terminal newline is trimmed. ``-`` with ``--stdin``, or a terminal
        stdin, is a usage error naming ``what``. Unlike :data:`TextOrStdin`, an
        empty pipe is ``""``, left to the command. First consumer: ``workspace run``.
        """
        from untaped.sdk import _stdin  # noqa: PLC0415 - only a reader pays

        if self._requested:  # one stdin: the rows or this text, never both
            raise UsageError(f"- reads the {what} from stdin, so it can't be combined with --stdin")
        if _stdin.stdin_is_terminal():  # never a wait for typing
            raise UsageError(f"- reads the {what} from stdin; pipe it")
        return _stdin.read_stdin_text()

    def kind(self, row: BaseModel) -> str | None:
        """The kind of ``row``'s model among those this command reads (``None``: none of them).

        A row a ``via=`` bridge turned into ``R`` has ``R``'s kind, not the provider's.
        """
        return next((kind for kind, model in self._kinds.items() if model is type(row)), None)

    def rows(self, given: Sequence[str] = (), *, allow_empty: bool = False) -> list[R]:
        """Whole validated rows from stdin; a bare identifier is a usage error."""
        rows: list[R] = []
        for value in self.values(given, allow_empty=allow_empty):
            if isinstance(value, str):
                raise UsageError(f"expected rows on stdin, got the bare line {value!r}")
            rows.append(value)
        return rows

    def _read(self, given: Sequence[str], *, allow_empty: bool) -> list[str | PipeEnvelope]:
        from untaped.sdk._stdin import read_stdin_input  # noqa: PLC0415 - only a reader pays

        if not self._requested:
            if not given and not allow_empty:
                raise UsageError("at least one identifier is required (or use --stdin)")
            return list(given)
        if given:
            raise UsageError("provide identifiers as positional args or via --stdin, not both")
        kinds = None if self._any or self.via is not None else tuple(self._kinds)
        piped = read_stdin_input(accept_kinds=kinds, allow_empty=allow_empty)
        return list(piped.values) if piped.records is None else list(piped.records)

    def _model(self, envelope: PipeEnvelope) -> type[BaseModel]:
        if envelope.kind is not None:
            model = self._kinds.get(envelope.kind)
            if model is not None:
                return model
            raise ConfigError(
                f"line {envelope.lineno}: no installed plugin declares the kind "
                f"{envelope.kind!r}, so its rows cannot be read",
                category="invalid",
            )
        if not self._models:
            raise TypeError("Stdin was not filled with the models it accepts")
        if self._any:
            from untaped.sdk.records import KindlessRow  # noqa: PLC0415 - reading only

            return KindlessRow
        return self._models[0]

    def _through(self, envelope: PipeEnvelope) -> bool:
        """Whether ``via`` reads this row: a provider's kind or none, never one ``R`` takes."""
        return self.via is not None and envelope.kind not in self._kinds

    def _identifier(self, envelope: PipeEnvelope) -> str:
        row = self._row(envelope)  # validated whole, as values() reads it
        field = key_field(type(row))
        if field is None:
            raise ConfigError(
                "stdin is untaped pipe format but this command cannot map records to "
                "identifiers: no field of the accepted model is marked Key",
                category="invalid",
            )
        value = getattr(row, field, None)
        identifier = "" if value is None else str(value).strip()
        if not identifier:
            raise ConfigError(
                f"line {envelope.lineno}: record {field!r} is missing or blank",
                category="invalid",
            )
        return identifier

    def _row(self, envelope: PipeEnvelope) -> R:
        if self._through(envelope):
            from untaped.sdk.contracts._select import bridged  # noqa: PLC0415 - only via pays

            given = None if envelope.kind is None else self._known.get(envelope.kind)
            return cast("R", bridged(cast("Callable[..., Any]", self.via), envelope, given))
        try:
            model = self._model(envelope)  # strict, as a JSON row: as ``via`` reads one
            return cast("R", model.model_validate_json(json.dumps(envelope.record), strict=True))
        except ValidationError as exc:
            raise ConfigError(
                f"line {envelope.lineno}: {first_validation_error(exc)}", category="invalid"
            ) from exc


class Lines(FieldBlock):
    """Raw stdin lines with no flag, for a command whose stdin is its protocol.

    ``lines: Lines`` is read in ``run`` as ``for line in self.lines``: each
    line stripped, blank lines skipped, and nothing from a terminal (it never
    waits for typing).
    """

    field_kind = "lines"

    def __iter__(self) -> Iterator[str]:
        from untaped.sdk._stdin import read_stdin  # noqa: PLC0415 - read only when iterated

        return iter(read_stdin())
