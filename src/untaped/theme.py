"""Theme model and built-in presets, free of rendering dependencies.

These primitives carry no ``rich``/``prompt_toolkit`` weight, so foundational
modules like :mod:`untaped.settings` can depend on them without dragging the
terminal rendering (or interactive prompt) stack into every import path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, Field

from untaped.errors import ConfigError

BorderStyle = Literal["rounded", "square", "ascii", "none"]
CollectionView = Literal["table", "list"]
DetailView = Literal["list", "table"]
Density = Literal["normal", "compact"]

DEFAULT_SYMBOLS: dict[str, str] = {
    "success": "",
    "warning": "",
    "error": "",
    "info": "",
}


class ThemeSpec(BaseModel):
    """Terminal presentation tokens and default semantic view choices."""

    border: BorderStyle = "rounded"
    density: Density = "normal"
    collection_view: CollectionView = "table"
    detail_view: DetailView = "list"
    symbols: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_SYMBOLS))
    color_roles: dict[str, str] = Field(default_factory=dict)


class UiSettings(BaseModel):
    """Per-profile UI presentation preferences (the ``ui`` section of a profile)."""

    theme: str = "default"
    border: BorderStyle | None = None
    density: Density | None = None
    collection_view: CollectionView | None = None
    detail_view: DetailView | None = None
    symbols: dict[str, str] = Field(default_factory=dict)
    color_roles: dict[str, str] = Field(default_factory=dict)

    def apply_to(self, theme: ThemeSpec) -> ThemeSpec:
        """Apply user overrides to a registered or built-in theme."""
        data = theme.model_dump()
        for field in ("border", "density", "collection_view", "detail_view"):
            value = getattr(self, field)
            if value is not None:
                data[field] = value
        data["symbols"] = {**theme.symbols, **self.symbols}
        data["color_roles"] = {**theme.color_roles, **self.color_roles}
        return ThemeSpec.model_validate(data)


BUILTIN_THEMES: dict[str, ThemeSpec] = {
    "default": ThemeSpec(),
    "plain": ThemeSpec(border="ascii"),
    "compact": ThemeSpec(density="compact"),
    "high-contrast": ThemeSpec(
        border="square",
        density="normal",
        collection_view="table",
        detail_view="list",
        color_roles={
            "header": "bold bright_cyan",
            "border": "bright_cyan",
            "key": "bold bright_cyan",
            "value": "bright_white",
            "success": "bold bright_green",
            "info": "bold bright_blue",
            "warning": "bold yellow",
            "error": "bold bright_red",
        },
    ),
    "quiet": ThemeSpec(
        border="none",
        density="compact",
        collection_view="list",
        detail_view="list",
        color_roles={
            "key": "dim cyan",
            "success": "green",
            "info": "blue",
            "warning": "yellow",
            "error": "red",
        },
    ),
    "classic": ThemeSpec(
        border="rounded",
        density="normal",
        collection_view="table",
        detail_view="list",
        color_roles={
            "header": "bold cyan",
            "border": "cyan",
            "key": "cyan",
            "value": "white",
            "success": "green",
            "info": "blue",
            "warning": "yellow",
            "error": "red",
        },
    ),
}


def resolve_theme(
    settings: UiSettings | None = None,
    *,
    themes: Mapping[str, ThemeSpec] | None = None,
) -> ThemeSpec:
    """Resolve the active theme plus user overrides."""
    ui_settings = settings or UiSettings()
    available = {**BUILTIN_THEMES, **dict(themes or {})}
    theme = available.get(ui_settings.theme)
    if theme is None:
        valid = ", ".join(sorted(available))
        raise ConfigError(f"unknown UI theme: {ui_settings.theme!r}. Valid themes: {valid}")
    return ui_settings.apply_to(theme)


def resolve_theme_or_default(
    produce_settings: Callable[[], UiSettings | None],
    *,
    strict: bool,
) -> ThemeSpec:
    """Resolve the theme from ``produce_settings()``, degrading to the default.

    A :class:`ConfigError` from either fetching the settings or resolving the
    theme degrades to the default preset unless ``strict``. The settings source
    is a thunk so callers supply their own (live cache vs. a frozen snapshot)
    while sharing this one degrade policy.
    """
    try:
        return resolve_theme(produce_settings())
    except ConfigError:
        if strict:
            raise
        return BUILTIN_THEMES["default"]
