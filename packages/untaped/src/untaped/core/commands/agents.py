"""Root ``untaped agents`` command: the pages agents read before running untaped.

Read-only and experimental: stub skills call it by name, and its pages and
spelling may still change. The pages themselves live in
:mod:`untaped.core.agents`, imported only when the command runs.
"""

from __future__ import annotations

from pydantic import Field

from untaped.core.settings import CoreSettings
from untaped.sdk._cli import echo
from untaped.sdk.fields import Positional
from untaped.sdk.marks import experimental
from untaped.sdk.plugin import Command


class Agents(Command[CoreSettings]):
    """Print what this untaped can do, as Markdown pages for coding agents.

    Examples
    --------
    The map, then one plugin's page, then one command's page:

    ```console
    $ untaped agents
    $ untaped agents untaped
    $ untaped agents untaped config set
    ```
    """

    stability = experimental
    path: Positional[list[str]] = Field(
        default_factory=list,
        description="A plugin (`untaped` for core), then a command under it. "
        "Without one, the map of every plugin.",
    )

    def run(self) -> None:
        # Lazy: bootstrap is the CLI's root and imports this module, and only this
        # command pays for the pages.
        from untaped.core.agents import page  # noqa: PLC0415
        from untaped.runtime.bootstrap import composition, root_app  # noqa: PLC0415

        echo(page(root_app(), composition(), self.path), nl=False)


__all__ = ["Agents"]
