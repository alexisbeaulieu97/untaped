"""Per-invocation tool execution context.

``app_context()`` hands a tool a context that resolves each settings section
lazily, at most once, the first time it is accessed. Only the sections a
command actually reads are validated, so an invalid value in one capability's
section never breaks another capability's commands. Profile (or any other
scope) selection happens before command dispatch via the root ``--profile``
option, so nothing about the resolution leaks into ambient process state from
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.settings import (
    HttpSettings,
    Settings,
    get_settings,
    get_settings_model,
    load_settings_section,
)
from untaped.theme import UiSettings, resolve_theme_or_default
from untaped.ui import UiContext, ui_context


@dataclass(frozen=True)
class AppContext:
    """Lazily resolved settings plus typed accessors handed to a tool command."""

    settings_model: type[Settings] = field(default_factory=get_settings_model)
    _resolved: dict[str, Any] = field(default_factory=dict, init=False, repr=False, compare=False)

    @property
    def settings(self) -> Settings:
        """The full aggregate settings, validating *every* section.

        Prefer :meth:`section`; this raises when any section is invalid.
        """
        if "" not in self._resolved:
            self._resolved[""] = get_settings()
        return cast("Settings", self._resolved[""])

    def section[T: BaseModel](self, name: str, model_cls: type[T]) -> T:
        """Return one typed, registered settings section.

        Only ``name`` (plus its own state section) is validated, once per
        context. Unlike :func:`untaped.settings.get_config_section`, this never
        builds a one-off model for unregistered sections — it serves only
        sections registered before the composition was resolved.
        """
        if name not in self.settings_model.model_fields:
            raise ConfigError(
                f"config section {name!r} is not registered in the current composition"
            )
        if name not in self._resolved:
            self._resolved[name] = load_settings_section(name, self.settings_model)
        value = self._resolved[name]
        if isinstance(value, model_cls):
            return value
        if isinstance(value, BaseModel):
            return model_cls.model_validate(value.model_dump())
        return model_cls.model_validate(value)

    @property
    def http(self) -> HttpSettings:
        """Cross-cutting HTTP settings for building clients."""
        return self.section("http", HttpSettings)

    def ui(self, *, strict: bool = True) -> UiContext:
        """The themed UI context for messages and prompts.

        The ``ui`` section is resolved once per context, so the theme is
        stable for the life of the context even if the settings cache is later
        invalidated. ``strict=False`` degrades a theme-resolution
        :class:`ConfigError` (e.g. an unknown theme name) to the default theme.
        """
        theme = resolve_theme_or_default(lambda: self.section("ui", UiSettings), strict=strict)
        return ui_context(theme=theme)


def app_context() -> AppContext:
    """Return an :class:`AppContext` resolving each settings section on first use.

    Profile selection happens before dispatch via the root ``--profile`` option,
    so no parameters are needed here.
    """
    return AppContext()
