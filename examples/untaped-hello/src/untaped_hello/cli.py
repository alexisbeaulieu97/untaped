"""The ``untaped hello`` commands."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import Field

from untaped.sdk import (
    Command,
    Filled,
    Message,
    Output,
    Positional,
    ProgressHandle,
    Refresh,
    Stdin,
    atomic_write,
    plural,
    shown_path,
)
from untaped_hello.errors import HelloError
from untaped_hello.records import Salutation
from untaped_hello.settings import MOTD, HelloSettings


class DefaultGreeting(Message, kind="hello.default_greeting"):
    """A plugin's own stderr kind: the greeting is the default; name the setting that changes it."""

    level: Literal["hint"] = "hint"
    setting: str


class Greet(Command[HelloSettings]):
    """Print the configured greeting (``hello.greeting``)."""

    def run(self) -> str:
        if "greeting" not in self.settings.model_fields_set:
            self.ctx.diag.emit(
                DefaultGreeting(
                    message="this is the default greeting",
                    hint="set your own with `untaped config set hello.greeting TEXT`",
                    setting="hello.greeting",
                )
            )
        return self.settings.greeting


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


class Motd(Command[HelloSettings]):
    """Print the message of the day, kept from the file ``hello.motd_path`` names.

    The file stands in for an upstream: the copy is checked against it once
    it is older than its max age.
    """

    refresh: Refresh = Refresh(MOTD)

    def run(self) -> str:
        source = self.settings.motd_path
        if source is None:
            raise HelloError(
                "hello.motd_path is not set",
                category="config",
                hint="set it with `untaped config set hello.motd_path FILE`",
            )
        # the key is the file itself: each file, in any profile, keeps its own copy
        shown, source = shown_path(source.expanduser()), source.expanduser().resolve()
        copy = self._copy(source)
        served = MOTD.serve(
            {str(source): shown},
            refresh=self.refresh.directive,
            fill=partial(_fill, source, copy),
            read=lambda: copy.read_text(encoding="utf-8"),
        )
        return served.value.removesuffix("\n")  # core prints a line

    def _copy(self, source: Path) -> Path:
        digest = hashlib.sha256(str(source).encode()).hexdigest()[:16]
        return self.ctx.plugin.dir / "motd" / f"{digest}.txt"


def _fill(source: Path, copy: Path, _key: str, _progress: ProgressHandle) -> Filled:
    """Copy ``source`` into the plugin's directory: the cache's fill."""
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise HelloError(
            f"could not read {shown_path(source)}: {exc.strerror or exc}", category="unavailable"
        ) from exc
    atomic_write(copy, text)
    return Filled(summary=plural(len(text.splitlines()), "line"))
