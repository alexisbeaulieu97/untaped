"""``untaped hello say``: greet each name, one record each."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Command, Output, Positional, Stdin
from untaped_hello.records import Salutation
from untaped_hello.settings import HelloSettings


class Say(Command[HelloSettings]):
    """Greet each name with ``hello.greeting``, one ``hello.salutation`` record each.

    ``--stdin`` reads names, one per line, or the records ``say --format
    pipe`` printed; a record of any other kind exits 2 instead of being
    misread.
    """

    names: Positional[list[str]] = Field(default_factory=list, description="Whom to greet.")
    stdin: Stdin[Salutation]
    output: Output[Salutation]

    def run(self) -> Iterator[Salutation]:
        for name in self.stdin.identifiers(self.names):
            yield Salutation(name=name, text=f"{self.settings.greeting}, {name}")
