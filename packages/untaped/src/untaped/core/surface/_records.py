"""``record`` lines: the kinds untaped prints and reads, and their fields (#631).

Every kind has a bare owner line and one typed line per field (a ``choice=``
line per Literal value), so a reader's promise is that no field goes away or
changes type. A kind scripts write, one a command reads on ``--stdin``
(``Stdin[R]`` names its model or a base of it, or ``Stdin(via=)`` reads it
through a composed provider of the contract) or in a file (``File[M]``),
also gets an owner line naming its required fields and a ``default=`` line
per optional field: a new required field or a changed default fails. A
command that accepts any kind (``Stdin[Record]``) promises no fields. A
kind takes the mark of the commands that reach it, stable when any stable
one does.

The models a kind's fields reach get the same lines, as
``record <owner>.<ClassName>``, written when a written kind reaches them.
Core's diagnostic kinds (``untaped.failure``, ``untaped.progress``...) are
printed on stderr and parsed like records, with a ``level`` choice line from
their class constant. A 12.0 plugin's kinds come from its composed
declaration; an 11.x plugin's records join when the plugin moves.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

from untaped.core.surface._contracts import _closure, _model_mark, model_fields
from untaped.core.surface._lines import DeprecatedMark, Line, LineMark
from untaped.core.surface._sdk import SurfaceError
from untaped.core.surface._types import choice_text
from untaped.sdk.marks import Experimental, class_mark

if TYPE_CHECKING:
    from untaped.runtime.declaration import CommandDecl, GroupDecl, StabilityDecl
    from untaped.runtime.mount import Mount
    from untaped.runtime.registry import CompositionResult


@dataclass
class _Reached:
    """A model the surface lists: who owns it, the marks reaching it, whether scripts write it."""

    owner: str
    model: type[BaseModel]
    marks: set[LineMark | None] = field(default_factory=set)
    written: bool = False


def record_lines(
    result: CompositionResult, marks: Mapping[str, LineMark | None]
) -> dict[str, list[Line]]:
    """The ``record`` lines of core's diagnostic kinds and of ``result``'s declared kinds.

    By the plugin that owns each kind or nested model.
    """
    kinds = _diagnostics(result)
    _declared(result.mount, marks, kinds)
    by_model: dict[type[BaseModel], list[_Reached]] = {}
    for each in kinds.values():
        by_model.setdefault(each.model, []).append(each)
    # What reaches a kind reaches every model its fields reach, kinds included.
    direct = {kind: (set(each.marks), each.written) for kind, each in kinds.items()}
    nested: dict[tuple[str, str], _Reached] = {}
    for kind, each in kinds.items():
        reaching, written = direct[kind]
        for model in _closure({each.model: set()})[1:]:
            for found in by_model.get(model) or [_nested(model, each, result, nested)]:
                found.marks |= reaching
                found.written |= written
    subjects = dict(kinds)
    subjects.update({f"{at}.{name}": each for (at, name), each in nested.items()})
    owned: dict[str, list[Line]] = {}
    for subject, each in sorted(subjects.items()):
        owned.setdefault(each.owner, []).extend(
            _lines(f"record {subject}", each, _mark(subject, each))
        )
    return owned


def _nested(
    model: type[BaseModel],
    reaching: _Reached,
    result: CompositionResult,
    nested: dict[tuple[str, str], _Reached],
) -> _Reached:
    """The entry of ``model``, a model a kind reaches that is no kind itself."""
    at = _owner_of(model, result, reaching.owner)
    found = nested.setdefault((at, model.__name__), _Reached(at, model))
    if found.model is not model:
        raise SurfaceError(
            f"{at}: two record models are named {model.__name__} "
            f"({found.model.__module__} and {model.__module__}); rename one"
        )
    return found


def _diagnostics(result: CompositionResult) -> dict[str, _Reached]:
    """The root plugin's (core's) diagnostic kinds: the SDK's, which core owns."""
    from untaped.sdk.diagnostic import Diagnostic, record_kinds  # noqa: PLC0415

    core = next(plugin.spec.name for plugin in result.plugins if plugin.root)
    return {
        kind: _Reached(core, model, {None})
        for kind, model in record_kinds().items()
        if kind.partition(".")[0] == core and issubclass(model, Diagnostic)
    }


def _declared(
    mount: Mount, marks: Mapping[str, LineMark | None], kinds: dict[str, _Reached]
) -> None:
    """Add each declared kind, and each kind a command reads, with the marks reaching it."""
    known = mount.kinds

    def reach(kind: str, mark: LineMark | None, *, written: bool) -> None:
        if kind in known:
            found = kinds.setdefault(kind, _Reached(kind.partition(".")[0], known[kind]))
            found.marks.add(mark)
            found.written |= written

    for declared in mount.declaration.kinds:
        if declared.kind in known:
            kinds.setdefault(declared.kind, _Reached(declared.plugin, known[declared.kind]))
    for plugin in mount.declaration.plugins:
        runs = () if plugin.runs is None else (plugin.runs,)
        for command, mark in _commands((*plugin.commands, *runs), marks.get(plugin.name)):
            for kind in (command.output, *command.writes):
                if kind is not None:
                    reach(kind, mark, written=False)
            for kind in (*command.inputs, *_read(command, known), *_through(command, mount)):
                reach(kind, mark, written=True)
    for each in kinds.values():
        if not each.marks:  # listed in ``records``: its own mark, else its plugin's
            experimental = isinstance(class_mark(each.model), Experimental)
            each.marks.add("experimental" if experimental else marks.get(each.owner))


def _read(command: CommandDecl, known: Mapping[str, type[BaseModel]]) -> list[str]:
    """The kinds ``command``'s ``Stdin[R]`` names: ``R``'s models and their subclasses.

    A record base (``Stdin[Record]``) accepts any kind and promises no fields.
    """
    from untaped.runtime.registry import resolve_target  # noqa: PLC0415
    from untaped.sdk.diagnostic import WireModel  # noqa: PLC0415
    from untaped.sdk.records import Record  # noqa: PLC0415

    accepted = []
    for target in command.stdin:
        try:
            accepted.append(resolve_target(target))
        except Exception:
            continue
    bases = tuple(
        m for m in accepted if isinstance(m, type) and m not in (BaseModel, WireModel, Record)
    )
    return [kind for kind, model in known.items() if bases and issubclass(model, bases)]


def _through(command: CommandDecl, mount: Mount) -> list[str]:
    """The kinds ``Stdin(via=)`` reads: the own records of the contract's composed providers."""
    from untaped.runtime.registry import resolve_target  # noqa: PLC0415
    from untaped.sdk.contracts._declare import contract_of, item_type  # noqa: PLC0415

    if command.stdin_via is None:
        return []
    kinds = []
    for plugin in mount.declaration.plugins:
        for fill in plugin.fills:
            if fill.contract != command.stdin_via:
                continue
            try:
                provider = resolve_target(fill.target)
                info = contract_of(provider)  # type: ignore[arg-type]
                item = None if info is None else item_type(provider, info)  # type: ignore[arg-type]
            except Exception:
                continue
            kind = None if item is None else mount.kind_of(item)
            if kind is not None:
                kinds.append(kind)
    return kinds


def _commands(
    nodes: Iterable[CommandDecl | GroupDecl], inherited: LineMark | None
) -> Iterable[tuple[CommandDecl, LineMark | None]]:
    """Every command under ``nodes`` with its mark: its own, else its group's, else its plugin's."""
    for node in nodes:
        mark = _decl_mark(node.stability) or inherited
        if node.node == "group":
            if node.runs is not None:
                yield node.runs, _decl_mark(node.runs.stability) or mark
            yield from _commands(node.commands, mark)
        else:
            yield node, mark


def _decl_mark(stability: StabilityDecl | None) -> LineMark | None:
    if stability is None:
        return None
    if stability.stability == "experimental":
        return "experimental"
    return DeprecatedMark(stability.removed_in, stability.replacement)


def _owner_of(model: type, result: CompositionResult, reaching: str) -> str:
    """The plugin whose package defines ``model`` (core's, the ``untaped`` package), else who
    reaches it."""
    return result.mount.declaration.owner_of(model.__module__) or reaching


def _mark(subject: str, each: _Reached) -> LineMark | None:
    mark = _model_mark(each.marks, stable_export=False)
    if mark is None and isinstance(class_mark(each.model), Experimental):
        raise SurfaceError(
            f"record {subject}: a stable command reaches {each.model.__name__}, which is "
            "@experimental; mark the command experimental, or stabilise the model on purpose"
        )
    return mark


def _lines(subject: str, each: _Reached, mark: LineMark | None) -> list[Line]:
    required, fields = model_fields(each.model)
    lines = [Line(subject, mark)]
    if each.written:
        lines.append(Line(f"{subject}({', '.join(required)})", mark))
    # A diagnostic's fixed level is a class constant, written beside its kind.
    level = getattr(each.model, "level", None)
    if "level" in each.model.__class_vars__ and isinstance(level, str):
        lines.append(Line(f"{subject}.level choice={level}", mark))
    for key, token, default in fields:
        lines.append(Line(f"{subject}.{key} {token.token}", mark))
        if each.written and default is not None:
            lines.append(Line(f"{subject}.{key} default={default}", mark))
        lines.extend(Line(f"{subject}.{key} choice={choice_text(v)}", mark) for v in token.choices)
    return lines
