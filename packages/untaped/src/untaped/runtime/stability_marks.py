"""Every stability mark in a composed tree, and the marks on settings fields.

:func:`marks` is the one query every checker and generator reads; a renamed
command's old spelling is :func:`deprecated_alias`, a different mechanism with a successor.
"""

from __future__ import annotations

import typing
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from cyclopts import App
from pydantic import BaseModel
from pydantic.fields import FieldInfo

import untaped.sdk._behaviours as _behaviours
from untaped.runtime.stability_help import (
    _forget_with,
    app_mark,
    mark_of,
    marked_options,
    option_name,
)
from untaped.sdk._behaviours import DeprecatedBehaviour
from untaped.sdk._replacement import children, replacement_text
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import Deprecated, Experimental, Stability, check_removed_in

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult


_DEPRECATED_ALIASES: dict[int, dict[str, Alias]] = {}


type MarkTarget = Literal["plugin", "group", "command", "option", "setting", "behaviour"]


@dataclass(frozen=True)
class Mark:
    """One mark: where it sits, what it marks, and what it says."""

    where: str
    """The command path (``awx test``), option (``awx test run --dry-run``), plugin name,
    setting key (``awx.test_parallel``) or behaviour (``untaped.row-kind``,
    ``<plugin>.<name>`` for a plugin's)."""
    target: MarkTarget
    stability: Stability
    replacement: str | None
    """The replacement as help shows it, or ``None``."""


def marks(root: App, result: CompositionResult, *, resolve: bool = False) -> list[Mark]:
    """Every mark in the composition: plugins, groups, commands, options, settings and
    behaviours.

    Takes the composition, not only the app, because a plugin's mark is not on a
    lazy app. A setting is recorded only for a mark of its own field; one
    inherited from its plugin is that plugin's record (ask
    :func:`setting_mark` for a setting's effective mark). ``resolve=True``
    imports every lazy plugin first (tests and generators only); the
    settings come from the sections the composition registered.
    """
    found: list[Mark] = []
    for plugin in result.plugins:
        if plugin.stability is not None:
            found.append(_record(plugin.name, "plugin", plugin.stability, root))
    plugins = frozenset(plugin.name for plugin in result.plugins)
    _collect(root, root, (), plugins, found, resolve=resolve)
    found.extend(_setting_marks(root))
    found.extend(
        _record(f"{owner_name}.{behaviour.name}", "behaviour", behaviour.mark, root)
        for owner_name, behaviour in listed_behaviours(result)
    )
    return found


def listed_behaviours(result: CompositionResult) -> list[tuple[str, DeprecatedBehaviour]]:
    """``(owner, behaviour)`` for core's behaviours (owner ``untaped``), then each plugin's.

    Reads each plugin's ``deprecations``, importing what it lists (checkers
    and generators only, never the CLI path).
    """
    listings = [(_behaviours._CORE_OWNER, _behaviours._core_behaviours())]
    listings.extend((plugin.name, plugin.deprecations) for plugin in result.plugins)
    found: list[tuple[str, DeprecatedBehaviour]] = []
    owners: dict[int, str] = {}
    for name, listing in listings:
        seen: set[str] = set()
        for item in listing:
            if not isinstance(item, DeprecatedBehaviour):
                raise ConfigError(f"{name} deprecations lists {item!r}, not a DeprecatedBehaviour")
            if item.name in seen:
                raise ConfigError(f"{name} deprecations lists {item.name!r} twice")
            if id(item) in owners:
                raise ConfigError(
                    f"{name} deprecations lists {item.name!r}, which {owners[id(item)]} owns"
                )
            seen.add(item.name)
            owners[id(item)] = name
            found.append((name, item))
    return found


def _setting_marks(root: App) -> Iterator[Mark]:
    from untaped.runtime.settings import profile_section_models  # noqa: PLC0415

    for section, model in profile_section_models().items():
        for path, stability in field_marks(model).items():
            yield _record(f"{section}.{path}", "setting", stability, root)


def _record(where: str, target: MarkTarget, stability: Stability, root: App) -> Mark:
    text = replacement_text(stability, root) if isinstance(stability, Deprecated) else None
    return Mark(where=where, target=target, stability=stability, replacement=text)


def _collect(
    app: App,
    root: App,
    path: tuple[str, ...],
    plugins: frozenset[str],
    found: list[Mark],
    *,
    resolve: bool,
) -> None:
    for name, sub in children(app, resolve=resolve):
        here = (*path, name)
        mark = mark_of(sub)
        entry = app_mark(sub)
        if mark is not None and not (entry is not None and entry.source == "plugin"):
            target: MarkTarget = (
                "plugin"
                if not path and name in plugins
                else ("group" if any(True for _ in children(sub, resolve=False)) else "command")
            )
            found.append(_record(" ".join(here), target, mark, root))
        found.extend(
            _record(f"{' '.join(here)} {option_name(argument)}", "option", option, root)
            for argument, option in marked_options(sub)
        )
        _collect(sub, root, here, plugins, found, resolve=resolve)


def _own_marks(field: FieldInfo) -> list[Stability]:
    """The marks in the outermost ``Annotated`` of a field (``FieldInfo.metadata``)."""
    return [item for item in field.metadata if isinstance(item, Experimental | Deprecated)]


def _mark_inside_type(annotation: object) -> bool:
    """Whether a mark sits inside the type itself, where pydantic drops it.

    ``Annotated[int, experimental] | None`` and ``list[Annotated[int, ...]]``
    keep their marks in ``FieldInfo.annotation``; ``Annotated[int | None,
    experimental]`` moves its mark to ``FieldInfo.metadata``.
    """
    return any(
        isinstance(arg, Experimental | Deprecated) or _mark_inside_type(arg)
        for arg in typing.get_args(annotation)
    )


def _section_fields(
    model: type[BaseModel], prefix: str = ""
) -> Iterator[tuple[str, FieldInfo, bool]]:
    """``(dotted path, field, is a model)`` for every field, recursing into nested models."""
    for name, field in model.model_fields.items():
        nested = unwrap_optional(field.annotation)
        is_model = isinstance(nested, type) and issubclass(nested, BaseModel)
        yield f"{prefix}{name}", field, is_model
        if is_model:
            yield from _section_fields(nested, f"{prefix}{name}.")


def field_descriptions(model: type[BaseModel]) -> dict[str, str]:
    """The ``description`` of each described leaf field of a section ``model``, by dotted path."""
    return {
        path: field.description
        for path, field, is_model in _section_fields(model)
        if field.description and not is_model
    }


def field_marks(model: type[BaseModel]) -> dict[str, Stability]:
    """The marks on the leaf fields of a section ``model``, by dotted path.

    Only a mark in the outermost ``Annotated`` of a leaf counts; a misplaced
    one is reported by :func:`mark_errors` and ignored here.
    """
    found: dict[str, Stability] = {}
    for path, field, is_model in _section_fields(model):
        marks_here = _own_marks(field)
        if len(marks_here) == 1 and not is_model:
            found[path] = marks_here[0]
    return found


def pydantic_deprecated_fields(model: type[BaseModel]) -> list[str]:
    """The dotted paths of the fields of ``model`` that use pydantic's own ``deprecated=``."""
    return [path for path, field, _ in _section_fields(model) if field.deprecated]


def mark_errors(model: type[BaseModel]) -> list[str]:
    """One sentence per mark on ``model`` that would not take effect; ``[]`` when none.

    A mark must sit in the outermost ``Annotated`` of a leaf field of a profile
    model (``Annotated[int | None, experimental]``), never inside a union or
    generic argument, or on a model-typed field. A field with pydantic's own
    ``deprecated=`` is a style problem for ``check_plugin``
    (:func:`pydantic_deprecated_fields`), not one that stops a provider
    loading.
    """
    errors: list[str] = []
    for path, field, is_model in _section_fields(model):
        marks_here = _own_marks(field)
        inside = _mark_inside_type(field.annotation)
        if inside:
            errors.append(
                f"the mark on setting {path!r} sits inside its type, where pydantic drops it; "
                "put it in the outermost Annotated, as Annotated[int | None, experimental]"
            )
        if marks_here and is_model:
            errors.append(f"setting {path!r} is a model; mark its fields instead")
        if len(marks_here) > 1:
            errors.append(f"setting {path!r} has more than one stability mark")
        errors.extend(
            f"the replacement of the mark on setting {path!r} must be text"
            for mark in marks_here
            if isinstance(mark, Deprecated)
            and mark.replacement is not None
            and not isinstance(mark.replacement, str)
        )
    return errors


def setting_mark(
    key: str,
    *,
    sections: Mapping[str, type[BaseModel]],
    section_stability: Mapping[str, Stability | None],
) -> Stability | None:
    """The effective mark of setting ``key`` (``section.field``), or ``None`` when stable.

    The field's own mark wins; otherwise the setting inherits the mark of the
    plugin that owns its section. Pure: ``section_stability`` is what
    :func:`untaped.runtime.settings.section_stabilities` returns.
    """
    section, _, rest = key.partition(".")
    model = sections.get(section)
    own = None if model is None else field_marks(model).get(rest)
    return own if own is not None else section_stability.get(section)


@dataclass(frozen=True)
class Alias:
    """A deprecated command name's new name and the major that removes the old one."""

    new: str
    removed_in: int | None = None


def deprecated_alias(app: App, old: str, new: str, *, removed_in: int | None = None) -> None:
    """Keep command name ``old`` working under ``app`` as a hidden, deprecated ``new``.

    Mount calls it for each ``Command.aliases`` entry (``app`` is the
    command's parent). The root shell rewrites the old token to the new one
    before dispatch and warns naming both on stderr; the old spelling never
    appears in ``--help``. ``removed_in`` is the major that removes the old
    spelling; ``None`` reads "a future major release".
    """
    check_removed_in(removed_in)
    key = id(app)
    if key not in _DEPRECATED_ALIASES:
        _DEPRECATED_ALIASES[key] = {}
        _forget_with(app, _DEPRECATED_ALIASES)
    _DEPRECATED_ALIASES[key][old] = Alias(new, removed_in)


def deprecated_aliases(app: App) -> Mapping[str, Alias]:
    """The deprecated spellings registered on ``app``, old spelling → :class:`Alias`."""
    return _DEPRECATED_ALIASES.get(id(app), {})
