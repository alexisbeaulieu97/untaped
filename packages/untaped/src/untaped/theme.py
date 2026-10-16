"""Theme model and built-in presets, free of rendering dependencies.

These primitives carry no ``rich``/``prompt_toolkit`` weight, so foundational
modules like :mod:`untaped.settings` can depend on them without dragging the
terminal rendering (or interactive prompt) stack into every import path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from untaped.config_schema import Choices, field_choices
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
    "cycle.left": "\u2039",
    "cycle.right": "\u203a",
    "tab.active": "\u2501",
    "tab.inactive": "\u2500",
    "tree.open": "\u25be",
    "tree.closed": "\u25b8",
    "tag.add": "+",
    "tag.remove": "\u2715",
    "heading": "\u25c6",
    "dash": "\u2014",
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
    "cycle.left": "<",
    "cycle.right": ">",
    "tab.active": "=",
    "tab.inactive": "-",
    "tree.open": "v",
    "tree.closed": ">",
    "tag.add": "+",
    "tag.remove": "x",
    "heading": "*",
    "dash": "-",
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
    "screen.caret",
    "screen.emphasis",
    "screen.match",
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
    "screen.caret": "reverse",
    "screen.emphasis": "bold",
    "screen.match": "bold underline",
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
    "screen.caret": "reverse",
    "screen.emphasis": "bold",
    "screen.match": "bold underline",
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

#: Optional write-context entry naming the key being written (``ui.symbols``).
#: When present, the declared-name checks run only for that key, so a stray
#: name already on disk never blocks writing another ``ui`` key.
CONFIG_WRITTEN_KEY_CONTEXT = "untaped_config_written_key"


class UiSettings(BaseModel):
    """Per-profile UI presentation preferences (the ``ui`` section of a profile).

    ``theme`` must name a built-in theme. The check runs when the value is
    written (``config set``) and in ``doctor``; loading stays lenient so a
    stale theme only affects themed table output, not structured formats.
    ``format`` replaces the ``table`` default of the shared ``--format``
    option (``UNTAPED_FORMAT`` wins over it; an explicit flag wins over both).
    """

    theme: Annotated[str, Choices(lambda: BUILTIN_THEMES, "themes", noun="UI theme")] = Field(
        default="default", description="Built-in theme."
    )
    format: OutputFormat | None = Field(
        default=None,
        description=(
            "Default `--format` for commands whose default is `table`. "
            "`UNTAPED_FORMAT` wins over it; an explicit `--format` wins over both."
        ),
    )
    border: BorderStyle | None = Field(
        default=None, description="Table border style; overrides the theme."
    )
    density: Density | None = Field(default=None, description="Table density; overrides the theme.")
    collection_view: CollectionView | None = Field(
        default=None, description="How lists render in `table` format; overrides the theme."
    )
    detail_view: DetailView | None = Field(
        default=None,
        description="How a single record renders in `table` format; overrides the theme.",
    )
    hide_empty_columns: bool | None = Field(
        default=None,
        description=(
            "Leave out `table` columns that are empty on every row (on unless the "
            "theme turns it off); a column named in `--columns` always shows."
        ),
    )
    symbols: Annotated[dict[str, str], Choices(lambda: SYMBOL_NAMES, "symbols")] = Field(
        default_factory=dict, description="Symbol overrides merged over the theme's symbols."
    )
    color_roles: Annotated[dict[str, str], Choices(lambda: ROLE_NAMES, "color roles")] = Field(
        default_factory=dict, description="Color-role overrides merged over the theme's colors."
    )

    @field_validator("*")
    @classmethod
    def _declared_names_on_write(cls, value: object, info: ValidationInfo) -> object:
        """Refuse a name outside a field's :class:`Choices`, on write only.

        Loading stays lenient (doctor reports a stray). With a written key in
        the context, only that key is checked, so a stray already on disk never
        blocks writing another ``ui`` key.
        """
        context = info.context
        choices = field_choices(cls.model_fields[info.field_name or ""])
        if choices is None or not (isinstance(context, dict) and context.get(CONFIG_WRITE_CONTEXT)):
            return value
        written = context.get(CONFIG_WRITTEN_KEY_CONTEXT)
        if written is not None and written != f"ui.{info.field_name}":
            return value
        refusal = choices.refusal(value)
        if refusal is not None:
            raise ValueError(refusal)
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
            **NAMED_SCREEN_ROLES,
            "screen.accent": "bold bright_cyan",
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


def check_declared_tokens(ui: UiSettings) -> None:
    """Raise :class:`ConfigError` naming any stray key of a mapping with :class:`Choices`.

    That is ``ui.symbols`` and ``ui.color_roles``; the message lists the valid
    names. ``doctor`` calls it; ``config set`` reaches the same rule through
    :class:`UiSettings`' write-time validator.
    """
    problems = []
    for name, field in UiSettings.model_fields.items():
        choices = field_choices(field)
        value = getattr(ui, name)
        if choices is None or not isinstance(value, Mapping):
            continue
        refusal = choices.refusal(value, prefix=f"ui.{name}")
        if refusal is not None:
            problems.append(refusal)
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
