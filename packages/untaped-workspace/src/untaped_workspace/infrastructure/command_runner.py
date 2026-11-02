"""Run a command in a directory with a timeout, capturing its output."""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

from untaped.sdk.process import run_process
from untaped_workspace.domain.models import CommandResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


class SubprocessRunner:
    """:class:`CommandRunner` on ``untaped.sdk.process``; stdin is ``/dev/null``.

    Each command runs in its own process group, stopped as a whole on a
    timeout or :meth:`cancel` (SIGTERM, then SIGKILL two seconds later) or
    when untaped is interrupted (the interrupt's own signal, then SIGKILL).
    What a command leaves behind keeps running after it exits, unless it
    still holds the command's output: that is stopped two seconds after the
    command exits. After :meth:`cancel` the runner starts nothing new.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()

    def cancel(self) -> None:
        """Stop every running command; each run ends its own group within two seconds."""
        self._stop.set()

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        if self._stop.is_set():
            return CommandResult(
                returncode=None,
                stdout="",
                stderr="",
                duration_s=0.0,
                timed_out=False,
                cancelled=True,
            )
        start = time.monotonic()
        try:
            result = run_process(
                argv, timeout=timeout, cwd=cwd, env={**os.environ, **env}, stop=self._stop
            )
        except (OSError, ValueError) as exc:
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"{exc}\n",
                duration_s=time.monotonic() - start,
                timed_out=False,
            )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
            duration_s=result.duration_s,
            timed_out=result.ended == "timed_out",
            stopped=result.ended == "stopped",
        )
