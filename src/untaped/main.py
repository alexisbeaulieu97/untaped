"""Root Typer app for the untaped core and plugin hub."""

from __future__ import annotations

import os
from collections.abc import Iterable

import typer

from untaped.config import app as config_app
from untaped.plugins import (
    PluginRegistry,
    UntapedPlugin,
    discover_plugins,
    register_plugins,
    set_current_registry,
)
from untaped.plugins import (
    app as plugins_app,
)
from untaped.settings import get_settings


def build_app(plugins: Iterable[UntapedPlugin] | None = None) -> typer.Typer:
    """Build a root app and register core commands plus discovered plugins."""
    registry = PluginRegistry()
    selected = list(discover_plugins(registry) if plugins is None else plugins)
    register_plugins(registry, selected)
    set_current_registry(registry)

    app = typer.Typer(
        name="untaped",
        help="A personal DevOps CLI suite.",
        no_args_is_help=True,
    )

    @app.callback()
    def _root_callback(
        profile: str | None = typer.Option(
            None,
            "--profile",
            help="Override the active profile for this invocation only "
            "(equivalent to UNTAPED_PROFILE=<name>).",
        ),
    ) -> None:
        """A personal DevOps CLI suite."""
        if profile is not None:
            os.environ["UNTAPED_PROFILE"] = profile
            get_settings.cache_clear()

    app.add_typer(config_app, name="config")
    app.add_typer(plugins_app, name="plugins")
    for name, plugin_app in registry.clis.items():
        app.add_typer(plugin_app, name=name)
    return app


app = build_app()


if __name__ == "__main__":
    app()
