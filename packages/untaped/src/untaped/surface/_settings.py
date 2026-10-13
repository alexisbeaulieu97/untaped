"""``cfg`` and ``state`` lines, and the settings sections they share with the config reference.

The sections are the root's own (``http``, ``ui``, ``skills``), the shell's,
and each composed plugin's settings and state models, as
``scripts/gen_config_reference.py`` documents them; both read them through
:func:`sections` and :func:`setting_leaves`. The surface asks for each owner's
``extensions`` too, written as one key per owned contract and method
(``<owner>.extensions.<contract>.rank.<method>``), since that is what a user
sets.

A ``cfg`` line carries the key's coarse type and declared default, with a
``choice=`` line per Literal value; a renamed key that is still read is
``cfg <old> -> <new>  [deprecated]``. A ``state`` line carries the key and its
type. A retired key gives no line: it is a major's removal, and its
``upgrading`` fragment is the signal the guard already reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import BaseModel

from untaped.surface._lines import Line, MarkName
from untaped.surface._types import choice_text, default_text, node_token, schema_of

if TYPE_CHECKING:
    from untaped.plugins.registry import CompositionResult
    from untaped.stability import Stability

#: The root model's own sections; the rest of it is the pydantic-settings base.
_ROOT_SECTIONS = frozenset({"http", "ui", "skills"})


class Section(NamedTuple):
    """One settings or state model: its heading, key prefix, mark and kind."""

    title: str
    prefix: str
    model: type[BaseModel]
    is_state: bool
    stability: Stability | None


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
    """The root's and shell's sections (unless ``root=False``), then each plugin's, in name order.

    ``extensions=False`` leaves the injected ``extensions`` key out of an
    owner's section (the config reference documents it elsewhere).
    """
    from untaped.bootstrap import SHELL_SPEC  # noqa: PLC0415
    from untaped.plugins.registry import section_settings  # noqa: PLC0415
    from untaped.settings import Settings  # noqa: PLC0415

    found: list[Section] = []
    if root:
        found.append(Section("Root", "", Settings, False, None))
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
        if spec.state is not None:
            found.append(
                Section(f"`{spec.name}` state", spec.name, spec.state, True, spec.stability)
            )
    return found


def setting_leaves(section: Section) -> list[Setting]:
    """Every leaf key of ``section``, collections included, with its effective mark."""
    from untaped.config_schema import walk_settings  # noqa: PLC0415
    from untaped.settings import model_sections  # noqa: PLC0415
    from untaped.stability import setting_mark  # noqa: PLC0415

    prefix, model = section.prefix, section.model
    leaves = walk_settings(model, (prefix,) if prefix else (), include_collections=True)
    if not prefix:
        leaves = [leaf for leaf in leaves if leaf.path[0] in _ROOT_SECTIONS]
    models = {prefix: model} if prefix else model_sections(model)
    return [
        Setting(
            key=leaf.key,
            annotation=leaf.annotation,
            optional=leaf.optional,
            default=leaf.default,
            description=leaf.description,
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


def settings_lines(result: CompositionResult, *, root: bool = True) -> list[Line]:
    """Every ``cfg`` and ``state`` line of the composition (``root=False``: plugins only)."""
    from untaped.stability import Deprecated, Experimental  # noqa: PLC0415

    lines: list[Line] = []
    for section in sections(result, extensions=False, root=root):
        area = "state" if section.is_state else "cfg"
        plugin_mark = _mark(section.stability, Deprecated, Experimental)
        schema = schema_of(section.model)
        for leaf in setting_leaves(section):
            mark = plugin_mark if section.is_state else _mark(leaf.mark, Deprecated, Experimental)
            lines.extend(_leaf_lines(area, leaf, mark, schema, section.prefix))
        if not section.is_state and section.prefix:
            lines.extend(_renamed(section))
    return lines


def extension_lines(
    owner: str, contracts: Mapping[str, list[str]], mark: MarkName | None
) -> list[Line]:
    """``cfg <owner>.extensions.<contract>.rank.<method>``: one per owned contract method."""
    return [
        Line(f"cfg {owner}.extensions.{contract}.rank.{method} string[] default=[]", mark)
        for contract, methods in sorted(contracts.items())
        for method in sorted(methods)
    ]


def _mark(stability: object, deprecated: type, experimental: type) -> MarkName | None:
    if isinstance(stability, deprecated):
        return "deprecated"
    if isinstance(stability, experimental):
        return "experimental"
    return None


def _leaf_lines(
    area: str, leaf: Setting, mark: MarkName | None, schema: Mapping[str, Any], prefix: str
) -> list[Line]:
    parts = leaf.key.split(".")[1:] if prefix else leaf.key.split(".")
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
    from untaped.deprecated_keys import key_mappings  # noqa: PLC0415

    mappings = key_mappings(section.model)
    return [
        Line(f"cfg {section.prefix}.{old} -> {section.prefix}.{new}", "deprecated")
        for old, new in sorted(mappings.readable.items())
    ]
