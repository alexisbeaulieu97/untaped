"""Root ``untaped docs`` command: the documentation of this untaped, for people and agents.

Pages are Markdown, rendered with Rich at a terminal and printed raw when
piped or captured. The pages themselves live in :mod:`untaped.core.docs`,
imported only when the command runs.
"""

from __future__ import annotations

import sys

from pydantic import Field

from untaped.core.settings import CoreSettings
from untaped.sdk._cli import echo
from untaped.sdk.errors import UsageError
from untaped.sdk.fields import Positional
from untaped.sdk.plugin import Command


class Docs(Command[CoreSettings]):
    """Print the documentation of this untaped: every plugin's commands, files, rows and settings.

    Each page's title is its address; drop the last word to go up a level.
    Read `untaped docs` first: it explains the addresses and lists every
    installed plugin.

    Examples
    --------
    The overview, a plugin's page, one command's page, then a search:

    ```console
    $ untaped docs
    $ untaped docs untaped
    $ untaped docs untaped commands config set
    $ untaped docs untaped --search profile
    ```
    """

    address: Positional[list[str]] = Field(
        default_factory=list,
        description="The page: a plugin (`untaped` for core), a section and a name. "
        "Without one, the overview.",
    )
    search: str | None = Field(
        None,
        description="Print each line containing this text (any case) of every page under "
        "the address, after its page's address.",
    )

    def run(self) -> None:
        # Lazy: bootstrap is the CLI's root and imports this module, and only this
        # command pays for the pages.
        from untaped.core.docs import page, search  # noqa: PLC0415
        from untaped.runtime.bootstrap import composition, root_app  # noqa: PLC0415
        from untaped.runtime.render import (  # noqa: PLC0415
            render_markdown,
            should_colorize,
            stream_is_tty,
        )

        if self.search is not None:
            if not self.search.strip():
                raise UsageError("--search needs some text to look for")
            found = search(root_app(), composition(), self.address, self.search)
            if not found:
                under = " ".join(("untaped docs", *self.address))
                self.ctx.diag.info(f"no line under `{under}` contains {self.search!r}")
            echo(found, nl=False)
            return
        text = page(root_app(), composition(), self.address)
        if stream_is_tty(sys.stdout):
            echo(render_markdown(text, colorize=should_colorize(sys.stdout)))
        else:
            echo(text, nl=False)


__all__ = ["Docs"]
