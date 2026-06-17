"""Per-invocation tool execution context.

``app_context()`` resolves settings exactly once and hands a tool a frozen
value object. Profile (or any other scope) selection happens before command
dispatch via the root ``--profile`` option, so nothing about the resolution
leaks into ambient process state from here.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.settings import HttpSettings, Settings, get_settings
from untaped.ui import BUILTIN_THEMES, UiContext, UiSettings, resolve_theme, ui_context


@dataclass(frozen=True)
class AppContext:
    """Resolved settings plus typed accessors handed to a tool command."""

    settings: Settings

    def section[T: BaseModel](self, name: str, model_cls: type[T]) -> T:
        """Return one typed, registered settings section.

        Unlike :func:`untaped.settings.get_config_section`, this never builds
        a one-off model for unregistered sections — the context is a frozen
        snapshot, so it can only serve sections that were registered (via
        :func:`untaped.tool.register_tool`) before it was created.
        """
        value = getattr(self.settings, name, None)
        if value is None:
            raise ConfigError(
                f"config section {name!r} is not registered; register the tool's "
                f"section with untaped.tool.register_tool before resolving a context"
            )
        if isinstance(value, model_cls):
            return value
        if isinstance(value, BaseModel):
            return model_cls.model_validate(value.model_dump())
        return model_cls.model_validate(value)

    @property
    def http(self) -> HttpSettings:
        """Cross-cutting HTTP settings for building clients."""
        return self.settings.http

    def ui(self, *, strict: bool = True) -> UiContext:
        """The themed UI context for messages and prompts.

        Built from this context's frozen settings snapshot, so the theme is
        stable for the life of the context even if the settings cache is later
        invalidated. ``strict=False`` degrades a theme-resolution
        :class:`ConfigError` (e.g. an unknown theme name) to the default theme.
        """
        try:
            theme = resolve_theme(self.section("ui", UiSettings))
        except ConfigError:
            if strict:
                raise
            theme = BUILTIN_THEMES["default"]
        return ui_context(theme=theme)


def app_context() -> AppContext:
    """Resolve effective settings once and return a frozen :class:`AppContext`.

    Profile selection happens before dispatch via the root ``--profile`` option,
    so no parameters are needed here.
    """
    return AppContext(settings=get_settings())
