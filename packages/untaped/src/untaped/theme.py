"""Theme model and built-in presets, free of rendering dependencies.

These primitives carry no ``rich``/``prompt_toolkit`` weight, so foundational
modules like :mod:`untaped.settings` can depend on them without dragging the
terminal rendering (or interactive prompt) stack into every import path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from untaped.errors import ConfigError

BorderStyle = Literal["rounded", "square", "ascii", "none"]
CollectionView = Literal["table", "list"]
DetailView = Literal["list", "table"]
Density = Literal["normal", "compact"]
OutputFormat = Literal["json", "yaml", "table", "raw", "pipe"]

#: Status symbols that prefix ``success``/``warning``/``error``/``info`` lines
#: (empty by default).
STATUS_SYMBOLS: dict[str, str] = {
    "success": "",
    "warning": "",
    "error": "",
    "info": "",
}

#: Glyphs screens draw with; every default is one cell wide.
SCREEN_SYMBOLS: dict[str, str] = {
    "chosen": "\u25b6",
    "checked": "\u2713",
    "unchecked": " ",
    "on": "\u2713",
    "off": "\u2717",
    "expand": "\u25bc",
    "collapse": "\u25b2",
    "mask": "\u2022",
    "ellipsis": "\u2026",
    "separator": "\u00b7",
}

#: ASCII fallbacks the ``plain`` theme layers over :data:`SCREEN_SYMBOLS`
#: (``ellipsis`` is the one token wider than a cell).
PLAIN_SCREEN_SYMBOLS: dict[str, str] = {
    "chosen": ">",
    "checked": "x",
    "unchecked": " ",
    "on": "+",
    "off": "x",
    "expand": "v",
    "collapse": "^",
    "mask": "*",
    "ellipsis": "...",
    "separator": "-",
}

DEFAULT_SYMBOLS: dict[str, str] = {**STATUS_SYMBOLS, **SCREEN_SYMBOLS}

#: The declared names ``ui.symbols`` and ``ui.color_roles`` accept on write.
SYMBOL_NAMES: tuple[str, ...] = (*STATUS_SYMBOLS, *SCREEN_SYMBOLS)
TABLE_ROLE_NAMES: tuple[str, ...] = (
    "header",
    "border",
    "key",
    "value",
    "success",
    "info",
    "warning",
    "error",
)
SCREEN_ROLE_NAMES: tuple[str, ...] = (
    "screen.accent",
    "screen.muted",
    "screen.highlight",
    "screen.focus",
    "screen.border",
    "screen.value",
    "screen.success",
    "screen.error",
)
ROLE_NAMES: tuple[str, ...] = (*TABLE_ROLE_NAMES, *SCREEN_ROLE_NAMES)

#: Screen roles of ``default`` and ``compact`` (the zinc palette).
ZINC_SCREEN_ROLES: dict[str, str] = {
    "screen.accent": "bold #e4e4e7",
    "screen.muted": "#a1a1aa",
    "screen.highlight": "bold #fafafa on #3f3f46",
    "screen.focus": "#e4e4e7",
    "screen.border": "#52525b",
    "screen.value": "#fafafa",
    "screen.success": "#4ade80",
    "screen.error": "#f87171",
}

#: Screen roles of the themes limited to named 16-colour styles.
NAMED_SCREEN_ROLES: dict[str, str] = {
    "screen.accent": "bold cyan",
    "screen.muted": "bright_black",
    "screen.highlight": "bold white on bright_black",
    "screen.focus": "bright_white",
    "screen.border": "bright_black",
    "screen.value": "white",
    "screen.success": "green",
    "screen.error": "red",
}


class ThemeSpec(BaseModel):
    """Terminal presentation tokens and default semantic view choices."""

    border: BorderStyle = "rounded"
    density: Density = "normal"
    collection_view: CollectionView = "table"
    detail_view: DetailView = "list"
    hide_empty_columns: bool = True
    symbols: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_SYMBOLS))
    color_roles: dict[str, str] = Field(default_factory=dict)


#: Pydantic validation context enabling write-time checks (``config set``):
#: ``validate_settings_section`` passes it so values that only fail at use
#: time (e.g. an unknown ``ui.theme``) are rejected before landing on disk.
CONFIG_WRITE_CONTEXT = "untaped_config_write"


class UiSettings(BaseModel):
    """Per-profile UI presentation preferences (the ``ui`` section of a profile).

    ``theme`` must name a built-in theme. The check runs when the value is
    written (``config set``) and in ``doctor``; loading stays lenient so a
    stale theme only affects themed table output, not structured formats.
    ``format`` replaces the ``table`` default of the shared ``--format``
    option (``UNTAPED_FORMAT`` wins over it; an explicit flag wins over both).
    """

    theme: str = "default"
    format: OutputFormat | None = None
    border: BorderStyle | None = None
    density: Density | None = None
    collection_view: CollectionView | None = None
    detail_view: DetailView | None = None
    hide_empty_columns: bool | None = None
    symbols: dict[str, str] = Field(default_factory=dict)
    color_roles: dict[str, str] = Field(default_factory=dict)

    @field_validator("symbols")
    @classmethod
    def _declared_symbols_on_write(
        cls, value: dict[str, str], info: ValidationInfo
    ) -> dict[str, str]:
        _reject_undeclared_on_write(info, "symbols", value, SYMBOL_NAMES)
        return value

    @field_validator("color_roles")
    @classmethod
    def _declared_roles_on_write(
        cls, value: dict[str, str], info: ValidationInfo
    ) -> dict[str, str]:
        _reject_undeclared_on_write(info, "color_roles", value, ROLE_NAMES)
        return value

    @field_validator("theme")
    @classmethod
    def _known_theme_on_write(cls, value: str, info: ValidationInfo) -> str:
        context = info.context
        if (
            isinstance(context, dict)
            and context.get(CONFIG_WRITE_CONTEXT)
            and value not in BUILTIN_THEMES
        ):
            raise ValueError(f"unknown UI theme {value!r}; valid themes: {_valid_themes()}")
        return value

    def apply_to(self, theme: ThemeSpec) -> ThemeSpec:
        """Apply user overrides to a registered or built-in theme."""
        data = theme.model_dump()
        for field in ("border", "density", "collection_view", "detail_view", "hide_empty_columns"):
            value = getattr(self, field)
            if value is not None:
                data[field] = value
        data["symbols"] = {**theme.symbols, **self.symbols}
        data["color_roles"] = {**theme.color_roles, **self.color_roles}
        return ThemeSpec.model_validate(data)


BUILTIN_THEMES: dict[str, ThemeSpec] = {
    "default": ThemeSpec(color_roles=dict(ZINC_SCREEN_ROLES)),
    "plain": ThemeSpec(
        border="ascii",
        symbols={**DEFAULT_SYMBOLS, **PLAIN_SCREEN_SYMBOLS},
        color_roles={**NAMED_SCREEN_ROLES, "screen.highlight": "reverse"},
    ),
    "compact": ThemeSpec(density="compact", color_roles=dict(ZINC_SCREEN_ROLES)),
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
            **{**NAMED_SCREEN_ROLES, "screen.accent": "bold bright_cyan"},
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
            **NAMED_SCREEN_ROLES,
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
            **NAMED_SCREEN_ROLES,
        },
    ),
}


def _undeclared(names: Iterable[str], declared: Sequence[str]) -> list[str]:
    return sorted(name for name in names if name not in declared)


def _undeclared_message(field: str, label: str, names: list[str], declared: Sequence[str]) -> str:
    stray = ", ".join(f"ui.{field}.{name}" for name in names)
    return f"unknown name {stray}. Valid {label}: {', '.join(sorted(declared))}"


def _reject_undeclared_on_write(
    info: ValidationInfo, field: str, value: Mapping[str, str], declared: Sequence[str]
) -> None:
    context = info.context
    if not (isinstance(context, dict) and context.get(CONFIG_WRITE_CONTEXT)):
        return
    names = _undeclared(value, declared)
    if names:
        label = "symbols" if field == "symbols" else "color roles"
        raise ValueError(_undeclared_message(field, label, names, declared))


def check_declared_tokens(ui: UiSettings) -> None:
    """Raise :class:`ConfigError` naming any ``ui.symbols``/``ui.color_roles`` stray.

    The message lists the valid names. ``doctor`` calls it; ``config set``
    reaches the same rule through :class:`UiSettings`'s write-time validators.
    """
    problems = []
    for field, label, declared in (
        ("symbols", "symbols", SYMBOL_NAMES),
        ("color_roles", "color roles", ROLE_NAMES),
    ):
        names = _undeclared(getattr(ui, field), declared)
        if names:
            problems.append(_undeclared_message(field, label, names, declared))
    if problems:
        raise ConfigError("; ".join(problems))


def resolve_theme(settings: UiSettings | None = None) -> ThemeSpec:
    """Resolve the active built-in theme plus user overrides."""
    ui_settings = settings or UiSettings()
    return ui_settings.apply_to(_theme_named(ui_settings.theme))


def _theme_named(name: str) -> ThemeSpec:
    theme = BUILTIN_THEMES.get(name)
    if theme is None:
        raise ConfigError(f"unknown UI theme: {name!r}. Valid themes: {_valid_themes()}")
    return theme


def _valid_themes() -> str:
    return ", ".join(sorted(BUILTIN_THEMES))


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
