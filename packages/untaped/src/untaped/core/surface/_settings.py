"""``cfg`` and ``state`` lines, and the settings sections they share with the config reference.

The sections are core's own (``untaped``: ``http``, ``ui``, ``skills``) and
each composed plugin's settings and state models, as
``scripts/gen_config_reference.py`` documents them; both read them through
:func:`sections` and :func:`setting_leaves`. The surface asks for each owner's
``extensions`` too, written as one key per owned contract and method
(``<owner>.extensions.<contract>.rank.<method>``), since that is what a user
sets.

A ``cfg`` line carries the key's coarse type and declared default, with a
``choice=`` line per Literal value; a renamed key that is still read is
``cfg <old> -> <new>  [deprecated, removed in <major>.0]``. A ``state`` line carries the key and its
type. A retired key gives no line: it is a major's removal, and its
``upgrading`` fragment is the signal the guard already reads.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import BaseModel, Field, create_model

from untaped.core.surface._lines import DeprecatedMark, Line, LineMark
from untaped.core.surface._sdk import line_mark
from untaped.core.surface._types import choice_text, default_text, node_token, schema_of
from untaped.sdk._home import PLUGINS_FOLDER

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.runtime.registry import CompositionResult
    from untaped.sdk._schema import Choices
    from untaped.sdk.marks import Stability


class Section(NamedTuple):
    """One settings or state model: its heading, key prefix, mark and kind."""

    title: str
    prefix: str
    model: type[BaseModel]
    is_state: bool
    stability: Stability | None
    rows: tuple[Any, ...] = ()
    """A state section's typed row declarations, each field of which has a line."""
    file: str | None = None
    """A plugin class's state section: the file its rows live in."""


@dataclass(frozen=True)
class Setting:
    """One leaf key of a section."""

    key: str
    annotation: Any
    """The leaf type, ``X`` for ``X | None``."""
    optional: bool
    default: Any
    description: str | None
    mark: Stability | None
    """The key's own mark, else its plugin's."""
    choices: Choices | None = None
    """The names the key accepts, when its field declares them."""
    bounds: str | None = None
    """The key's bounds as a sentence (``Above 0.``), when its field declares any."""


def check_quarantine(result: CompositionResult) -> None:
    """Raise :class:`RuntimeError` naming every plugin ``result`` quarantined.

    A generator never drops a quarantined first-party plugin: every line or
    row it owns would read as removed.
    """
    if result.quarantine:
        reasons = "; ".join(
            f"{record.name!r} [{record.reason}]: {record.detail}" for record in result.quarantine
        )
        raise RuntimeError(
            f"first-party plugins quarantined; fix them before generating: {reasons}"
        )


def sections(
    result: CompositionResult,
    *,
    extensions: bool,
    owner: str | None = None,
    settings_only: bool = False,
) -> list[Section]:
    """Each plugin's sections, the root plugin's (core's) first, then by name; ``owner``'s only.

    ``extensions=False`` leaves the injected ``extensions`` key out of an
    owner's section (the config reference documents it elsewhere).
    ``settings_only`` leaves the state sections out, so a plugin class's
    state rows, which import the class, are not read.
    """
    from untaped.runtime.registry import section_settings  # noqa: PLC0415

    found: list[Section] = []
    ordered = sorted(result.plugins, key=lambda each: (not each.root, each.spec.name))
    for spec in (each.spec for each in ordered if owner in (None, each.spec.name)):
        settings = section_settings(spec, extensions=extensions)
        if settings is not None:
            found.append(Section(f"`{spec.name}`", spec.name, settings, False, spec.stability))
        if settings_only:
            continue
        rows = _held_rows(result, spec.name)
        if not rows:
            continue
        state, file = _rows_model(spec.name, rows), None
        if state is not None:  # rows in the home, in the plugin's own state file
            file = f"~/.untaped/{PLUGINS_FOLDER}/{spec.name}/state.yml"
        else:  # only a project's rows: no section in the home
            state = create_model(spec.name.title().replace("-", "") + "State")
        found.append(
            Section(f"`{spec.name}` state", spec.name, state, True, spec.stability, rows, file)
        )
    return found


def _held_rows(result: CompositionResult, name: str) -> tuple[Any, ...]:
    """The state rows plugin ``name``'s class holds, named as the declaration names them."""
    return tuple(
        block._spelled_as(name, decl.name) for decl, block in result.mount.held(name, "state-rows")
    )


def _rows_model(name: str, rows: tuple[Any, ...]) -> type[BaseModel] | None:
    """A model of the rows a plugin class keeps in the home: a field per rows, its model's summary.

    A project's rows live in the project and are left out; ``None`` when only those are held.
    """
    from untaped.sdk.storage.state import ProjectStateRows, _resolved_shape  # noqa: PLC0415

    fields: dict[str, Any] = {}
    for each in rows:
        if isinstance(each, ProjectStateRows):
            continue
        model = _resolved_shape(each).model
        summary = (inspect.getdoc(model) or "").partition("\n\n")[0].replace("\n", " ")
        fields[each.name] = (list[model], Field(default_factory=list, description=summary))  # type: ignore[valid-type]
    if not fields:
        return None
    return create_model(name.title().replace("-", "") + "State", **fields)


def setting_leaves(section: Section) -> list[Setting]:
    """Every leaf key of ``section``, collections included, with its effective mark."""
    from untaped.sdk._schema import bounds_text, walk_settings  # noqa: PLC0415
    from untaped.sdk.marks import setting_mark  # noqa: PLC0415

    prefix, model = section.prefix, section.model
    leaves = walk_settings(model, (prefix,), include_collections=True)
    models = {prefix: model}
    return [
        Setting(
            key=leaf.key,
            annotation=leaf.annotation,
            optional=leaf.optional,
            default=leaf.default,
            description=leaf.description,
            choices=leaf.choices,
            bounds=bounds_text(leaf),
            mark=None
            if section.is_state
            else setting_mark(
                leaf.key,
                sections=models,
                section_stability=dict.fromkeys(models, section.stability),
            ),
        )
        for leaf in leaves
    ]


def settings_lines(
    result: CompositionResult, *, owner: str | None = None, tree: App | None = None
) -> list[Line]:
    """Every ``cfg`` and ``state`` line of the composition, or only plugin ``owner``'s.

    ``tree`` (the root app) names a plugin mark's object replacement, as on
    the plugin's ``cli`` lines.
    """
    lines: list[Line] = []
    for section in sections(result, extensions=False, owner=owner):
        area = "state" if section.is_state else "cfg"
        plugin_mark = line_mark(section.stability, tree)
        schema = schema_of(section.model)
        for leaf in setting_leaves(section):
            mark = plugin_mark if section.is_state else line_mark(leaf.mark, tree)
            lines.extend(_leaf_lines(area, leaf, mark, schema))
        if not section.is_state:
            lines.extend(_renamed(section))
        for rows in section.rows:
            lines.extend(row_field_lines(rows, plugin_mark))
    return lines


def row_field_lines(rows: Any, mark: LineMark | None) -> list[Line]:
    """``state <plugin>.<name>.<field> <type> [default=…]``: one per field of a row model.

    A row is the only copy, so its fields are a promise: a minor keeps each
    line, and a field it adds has a default (``tests/repo/test_surface.py``).
    """
    from untaped.sdk.storage.state import _resolved_shape  # noqa: PLC0415

    model: type[BaseModel] = _resolved_shape(rows).model
    schema = schema_of(model)
    defs = schema.get("$defs", {})
    lines: list[Line] = []
    for name, info in model.model_fields.items():
        key = info.serialization_alias or info.alias or name
        token = node_token(schema["properties"][key], defs)
        text = f"state {rows.qualified}.{key} {token.token}"
        if not info.is_required():
            text += f" default={default_text(info.get_default(call_default_factory=True))}"
        lines.append(Line(text, mark))
        lines.extend(
            Line(f"state {rows.qualified}.{key} choice={choice_text(value)}", mark)
            for value in token.choices
        )
    return lines


def extension_lines(
    owner: str, contracts: Mapping[str, Mapping[str, LineMark | None]]
) -> list[Line]:
    """``cfg <owner>.extensions.<contract>.rank.<method>``: one per owned contract method,
    marked like that method's ``contract`` line."""
    return [
        Line(f"cfg {owner}.extensions.{contract}.rank.{method} string[] default=[]", mark)
        for contract, methods in sorted(contracts.items())
        for method, mark in sorted(methods.items())
    ]


def _leaf_lines(
    area: str, leaf: Setting, mark: LineMark | None, schema: Mapping[str, Any]
) -> list[Line]:
    parts = leaf.key.split(".")[1:]
    token = node_token(_node(schema, parts, leaf.key), schema.get("$defs", {}))
    text = f"{area} {leaf.key} {token.token}"
    if area == "cfg":
        text += f" default={default_text(leaf.default, none='unset')}"
    lines = [Line(text, mark)]
    lines.extend(
        Line(f"{area} {leaf.key} choice={choice_text(value)}", mark) for value in token.choices
    )
    return lines


def _node(schema: Mapping[str, Any], parts: list[str], key: str) -> Mapping[str, Any]:
    """The schema of the field at ``parts``, through nested (and nullable) models."""
    defs: Mapping[str, Any] = schema.get("$defs", {})
    node: Mapping[str, Any] = schema
    for part in parts:
        node = _model_node(node, defs)
        properties = node.get("properties", {})
        if part not in properties:
            raise KeyError(f"setting {key}: no schema property {part!r}")
        node = properties[part]
    return node


def _model_node(node: Mapping[str, Any], defs: Mapping[str, Any]) -> Mapping[str, Any]:
    refs = [node, *node.get("anyOf", ())]
    found: Mapping[str, Any] = next((defs[m["$ref"]] for m in refs if "$ref" in m), node)
    return found


def _renamed(section: Section) -> list[Line]:
    """``cfg <old> -> <new>  [deprecated, removed in <major>.0]`` per renamed key still read."""
    from untaped.sdk.health.deprecated_keys import key_mappings  # noqa: PLC0415

    models = {section.prefix: section.model}
    lines: list[Line] = []
    for prefix, model in models.items():
        mappings = key_mappings(model)
        lines.extend(
            Line(f"cfg {prefix}.{old} -> {prefix}.{new}", DeprecatedMark(mappings.removed_in[old]))
            for old, new in sorted(mappings.readable.items())
        )
    return lines
