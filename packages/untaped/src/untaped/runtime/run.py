"""Building the run a 12.0 command sees, from the composition and today's machinery.

The mount calls :func:`build_run` once per invocation and sets the result with
:func:`untaped.sdk.run.run_scope`. Each member reads what the 11.x runtime
already provides: settings sections through ``get_config_section`` (each
section validated once per run), the stderr sink and the themed terminal
(built once, on first use).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from untaped.runtime.settings import get_config_section
from untaped.runtime.sink import Diagnostics
from untaped.sdk._app_context import app_context
from untaped.sdk.projects import untaped_home
from untaped.sdk.run import Composition, Run

if TYPE_CHECKING:
    from untaped.runtime.declaration import Declaration
    from untaped.sdk._ui import UiContext
    from untaped.sdk.plugin import Plugin


def composition_of(declaration: Declaration, plugins: Mapping[str, type[Plugin]]) -> Composition:
    """Who owns what among the composed plugins of ``declaration`` (``plugins`` by name)."""
    packages: dict[str, str] = {}
    for plugin in declaration.plugins:
        packages.setdefault(plugin.package, plugin.name)
    return Composition(
        owners={plugins[plugin.name].settings: plugin.name for plugin in declaration.plugins},
        packages=packages,
    )


def build_run(
    declaration: Declaration, plugins: Mapping[str, type[Plugin]], *, home: Path | None = None
) -> Run:
    """The run of one invocation over ``declaration``'s plugins."""
    picked: dict[str, BaseModel] = {}

    def picks(section: str, model: type[BaseModel]) -> BaseModel:
        if section not in picked:
            picked[section] = get_config_section(section, model)
        return picked[section]

    built: list[UiContext] = []

    def terminal() -> UiContext:
        if not built:
            built.append(app_context().ui())
        return built[0]

    return Run(
        home=untaped_home() if home is None else home,
        composition=composition_of(declaration, plugins),
        picks=picks,
        diag=Diagnostics(),
        terminal=terminal,
    )
