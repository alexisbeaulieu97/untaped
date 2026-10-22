"""Root ``untaped agents`` command: the pages agents read before running untaped.

Read-only and experimental: stub skills call it by name, and its pages and
spelling may still change. The pages themselves live in
:mod:`untaped.agents`, imported only when the command runs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from cyclopts import App, Parameter

from untaped.cli import create_app, echo, report_errors
from untaped.plugins.registry import CompositionResult
from untaped.stability import experimental


def build_root_agents_app(*, result: CompositionResult, root: Callable[[], App]) -> App:
    """Return the root ``agents`` command for one composition; ``root`` gives the root app."""
    app = create_app(
        name="agents",
        help="Print what this untaped can do, as Markdown pages for coding agents.",
        stability=experimental,
    )

    @app.default
    def agents_command(
        *path: Annotated[
            str,
            Parameter(
                help="A plugin (`untaped` for core), then a command under it. "
                "Without one, the map of every plugin.",
            ),
        ],
    ) -> None:
        """Print the map of this untaped, one plugin's page, or one command's page.

        The pages are generated from what the plugins declare: their commands,
        options, settings and extension points. Every page names the untaped
        that answered.

        Examples
        --------
        The map, then one plugin's page, then one command's page:

        ```console
        $ untaped agents
        $ untaped agents untaped
        $ untaped agents untaped config set
        ```
        """
        from untaped.agents import page  # noqa: PLC0415 - only this command pays for it

        with report_errors():
            echo(page(root(), result, path), nl=False)

    return app


__all__ = ["build_root_agents_app"]
