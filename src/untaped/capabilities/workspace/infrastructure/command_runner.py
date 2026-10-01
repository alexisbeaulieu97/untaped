"""Run a command in a directory with a timeout, capturing its output."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING

from untaped.capabilities.workspace.domain.models import CommandResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode(errors="replace")


class SubprocessRunner:
    """:class:`CommandRunner` backed by ``Popen``; stdin is ``/dev/null``.

    The command runs in its own session so a timeout can kill its whole process tree.
    """

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        start = time.monotonic()
        try:
            if not argv:
                raise ValueError("empty command")
            proc = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env={**os.environ, **env},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"{exc}\n",
                duration_s=time.monotonic() - start,
                timed_out=False,
            )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        return CommandResult(
            returncode=None if timed_out else proc.returncode,
            stdout=_text(out),
            stderr=_text(err),
            duration_s=time.monotonic() - start,
            timed_out=timed_out,
        )
