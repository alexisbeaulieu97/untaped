"""The ``settings`` section: every setting a plugin reads, with its type, default and description.

The settings are read through the config reference's own reader
(:mod:`untaped.core.surface._settings`), so the page and the generated
reference list the same keys, cache max ages included. A setting's type is
spelled by :func:`untaped.runtime.describe.type_text`, as on every page.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_origin

from untaped.core.docs._docstring import first_line
from untaped.core.docs._site import ORGANIZED, Site
from untaped.core.surface._types import home_relative
from untaped.runtime.describe import type_text
from untaped.runtime.settings import resolve_config_path
from untaped.sdk.marks import Deprecated, Experimental
from untaped.sdk.words import shown_path


@dataclass(frozen=True)
class Setting:
    """One setting as its page lists it, its key relative to the plugin's section."""

    key: str
    type: str
    default: str | None
    description: str
    experimental: bool


def settings(site: Site, name: str) -> list[Setting]:
    """Plugin ``name``'s settings (core's under ``untaped``); deprecated keys left out."""
    # The config reference's reader of the same sections; imported only for this page.
    from untaped.core.surface._settings import sections, setting_leaves  # noqa: PLC0415

    strip = f"{name}."
    inherited = site.result_plugin(name).spec.stability
    rows: list[Setting] = []
    for section in sections(site.result, extensions=False, owner=name):
        if section.is_state:
            continue
        for leaf in setting_leaves(section):
            if isinstance(leaf.mark, Deprecated):
                continue
            description = " ".join(
                part for part in (first_line(leaf.description), leaf.bounds) if part
            )
            rows.append(
                Setting(
                    key=leaf.key.removeprefix(strip),
                    type=_type(leaf.annotation, leaf.choices.names() if leaf.choices else ()),
                    default=_default(leaf.default),
                    description=description,
                    experimental=leaf.mark is not inherited and isinstance(leaf.mark, Experimental),
                )
            )
    return rows


def page(site: Site, name: str) -> list[str]:
    """``untaped docs PLUGIN settings``: the section of config.yml the plugin reads."""
    path = shown_path(resolve_config_path())
    lines = [
        *site.header(f"{name} settings", name),
        ORGANIZED,
        "",
        f"The `{name}:` section of `{path}`. A named profile sets any of them under "
        f"`profiles.<name>.{name}`; `untaped config set {name}.KEY VALUE` changes one.",
        "",
    ]
    for setting in settings(site, name):
        shape = (
            setting.type
            if setting.default is None
            else f"{setting.type}, default `{setting.default}`"
        )
        mark = " [experimental]" if setting.experimental else ""
        text = f": {setting.description}" if setting.description else ""
        lines.append(f"- `{setting.key}`{mark} ({shape}){text}")
    return lines


def _type(annotation: Any, choices: Sequence[str]) -> str:
    if choices:
        listed = ", ".join(f"`{choice}`" for choice in choices)
        return f"map keyed by {listed}" if get_origin(annotation) is dict else f"one of {listed}"
    return type_text(annotation)


def _default(value: object) -> str | None:
    """A default as a page writes it; ``None`` when there is nothing worth saying."""
    if value is None or value == "" or (isinstance(value, list | tuple | dict | set) and not value):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | Path):
        return home_relative(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return None
