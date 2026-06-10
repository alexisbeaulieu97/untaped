"""Root Cyclopts app for the untaped core and plugin hub."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Annotated

from cyclopts import App, Parameter
from rich.console import Console

from untaped.cli import create_app, raise_usage, run_cyclopts_app
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
from untaped.skills import app as skills_app
from untaped.skills import register_builtin_skills

CORE_COMMAND_NAMES = frozenset({"config", "plugins", "skills"})
PROFILE_HELP = (
    "Override the active profile for this invocation only; must precede the command "
    "(equivalent to setting the UNTAPED_PROFILE environment variable)."
)


def build_app(plugins: Iterable[UntapedPlugin] | None = None) -> App:
    """Build a root app and register core commands plus discovered plugins."""
    registry = PluginRegistry(reserved_cli_names=CORE_COMMAND_NAMES)
    register_builtin_skills(registry)
    selected = list(discover_plugins(registry) if plugins is None else plugins)
    register_plugins(registry, selected)
    set_current_registry(registry)

    app = create_app(
        name="untaped",
        help="A personal DevOps CLI suite.",
    )

    @app.meta.default
    def _root_callback(
        *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        _profile: Annotated[
            str | None,
            Parameter(
                name="--profile",
                help=PROFILE_HELP,
                # Document the root option in help; parsing stays manual so
                # passthrough commands keep trailing --profile tokens.
                parse=False,
                show=True,
            ),
        ] = None,
    ) -> object:
        profile, command_tokens = _consume_leading_profile(list(tokens))
        if profile is not None:
            os.environ["UNTAPED_PROFILE"] = profile
            get_settings.cache_clear()
        return run_cyclopts_app(app, command_tokens, result_action="return_value")

    app.command(config_app, name="config")
    app.command(plugins_app, name="plugins")
    app.command(skills_app, name="skills")
    for name, plugin_app in registry.clis.items():
        app.command(plugin_app, name=name)
    app.register_install_completion_command()
    return app


app = build_app()


def main(
    tokens: Iterable[str] | None = None,
    *,
    console: Console | None = None,
    error_console: Console | None = None,
) -> object:
    """Console-script entrypoint that runs the root meta app."""
    return run_cyclopts_app(
        app.meta,
        tokens,
        console=console,
        error_console=error_console,
    )


def _consume_leading_profile(tokens: list[str]) -> tuple[str | None, list[str]]:
    if not tokens:
        return None, tokens
    first = tokens[0]
    if first == "--profile":
        if len(tokens) < 2 or tokens[1].startswith("-"):
            raise_usage("--profile expects a profile name")
        return tokens[1], tokens[2:]
    if first.startswith("--profile="):
        profile = first.partition("=")[2]
        if not profile:
            raise_usage("--profile expects a profile name")
        return profile, tokens[1:]
    return None, tokens


if __name__ == "__main__":
    main()
