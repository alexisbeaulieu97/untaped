"""Per-invocation plugin execution context.

``plugin_context()`` resolves settings exactly once (optionally under a
read-time profile override) and hands plugins a frozen value object. Unlike
``profile_override`` + ``get_config_section``, nothing about the resolution
leaks into ambient process state: the override environment variable and the
settings cache are restored before the context is returned.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from untaped.cli import profile_override
from untaped.errors import ConfigError
from untaped.settings import HttpSettings, Settings, get_settings
from untaped.ui import UiContext, ui_context


@dataclass(frozen=True)
class PluginContext:
    """Resolved settings plus typed accessors handed to a plugin command."""

    settings: Settings

    def section[T: BaseModel](self, name: str, model_cls: type[T]) -> T:
        """Return one typed, registered settings section.

        Unlike :func:`untaped.settings.get_config_section`, this never builds
        a one-off model for unregistered sections — the context is a frozen
        snapshot, so it can only serve sections that were registered (via a
        plugin manifest in production, or
        :func:`untaped.testing.register_plugin_for_tests` in tests) before it
        was created.
        """
        value = getattr(self.settings, name, None)
        if value is None:
            raise ConfigError(
                f"config section {name!r} is not registered; sections are declared "
                f"in a plugin manifest (tests: untaped.testing.register_plugin_for_tests)"
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
        """The themed UI context for messages and prompts."""
        return ui_context(strict=strict)


def plugin_context(profile: str | None = None) -> PluginContext:
    """Resolve effective settings once and return a frozen :class:`PluginContext`."""
    with profile_override(profile):
        settings = get_settings()
    return PluginContext(settings=settings)
