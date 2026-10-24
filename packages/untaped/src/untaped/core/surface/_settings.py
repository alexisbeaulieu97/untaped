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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import BaseModel, create_model

from untaped.core.surface._lines import DeprecatedMark, Line, LineMark
from untaped.core.surface._sdk import line_mark
from untaped.core.surface._types import choice_text, default_text, node_token, schema_of

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


def sections(result: CompositionResult, *, extensions: bool, root: bool = True) -> list[Section]:
    """Core's sections (unless ``root=False``), then each plugin's, in name order.

    ``extensions=False`` leaves the injected ``extensions`` key out of an
    owner's section (the config reference documents it elsewhere).
    """
    from untaped.runtime.bootstrap import SHELL_SPEC  # noqa: PLC0415
    from untaped.runtime.registry import (  # noqa: PLC0415
        section_settings,
        state_declarations,
        state_model,
    )
    from untaped.sdk.storage.state import ProjectStateRows, StateRows  # noqa: PLC0415

    found: list[Section] = []
    if root:
        found.append(
            Section(f"`{SHELL_SPEC.section}`", SHELL_SPEC.section, SHELL_SPEC.settings, False, None)
        )
        if SHELL_SPEC.state is not None:
            found.append(
                Section(
                    f"`{SHELL_SPEC.section}` state",
                    SHELL_SPEC.section,
                    SHELL_SPEC.state,
                    True,
                    None,
                )
            )
    for registered in sorted(result.plugins, key=lambda plugin: plugin.spec.name):
        spec = registered.spec
        settings = section_settings(spec, extensions=extensions)
        if settings is not None:
            found.append(Section(f"`{spec.name}`", spec.name, settings, False, spec.stability))
        state = state_model(spec)
        rows = tuple(
            each
            for each in state_declarations(spec)
            if isinstance(each, StateRows | ProjectStateRows)
        )
        if state is None and rows:  # only a project's rows: no section in the home
            state = create_model(spec.name.title().replace("-", "") + "State")
        if state is not None:
            found.append(
                Section(f"`{spec.name}` state", spec.name, state, True, spec.stability, rows)
            )
    return found


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
    result: CompositionResult, *, root: bool = True, tree: App | None = None
) -> list[Line]:
    """Every ``cfg`` and ``state`` line of the composition (``root=False``: plugins only).

    ``tree`` (the root app) names a plugin mark's object replacement, as on
    the plugin's ``cli`` lines.
    """
    lines: list[Line] = []
    for section in sections(result, extensions=False, root=root):
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
    model: type[BaseModel] = rows.model if isinstance(rows.model, type) else rows.model()
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
