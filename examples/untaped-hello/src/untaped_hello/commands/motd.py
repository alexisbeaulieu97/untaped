"""``untaped hello motd``: the message of the day, served through a cache."""

from __future__ import annotations

import hashlib
from functools import partial
from pathlib import Path

from untaped.sdk import (
    Command,
    Filled,
    ProgressHandle,
    Refresh,
    atomic_write,
    plural,
    shown_path,
)
from untaped_hello.errors import HelloError
from untaped_hello.settings import MOTD, HelloSettings


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
