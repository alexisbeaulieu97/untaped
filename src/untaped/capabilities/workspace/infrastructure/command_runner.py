"""Run a command in a directory with a timeout, capturing its output."""

from __future__ import annotations

import os
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
    """:class:`CommandRunner` backed by ``subprocess.run``; stdin is ``/dev/null``."""

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                list(argv),
                cwd=cwd,
                env={**os.environ, **env},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=None,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
                duration_s=time.monotonic() - start,
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(
                returncode=127,
                stdout="",
                stderr=f"{exc}\n",
                duration_s=time.monotonic() - start,
                timed_out=False,
            )
        return CommandResult(
            returncode=proc.returncode,
            stdout=_text(proc.stdout),
            stderr=_text(proc.stderr),
            duration_s=time.monotonic() - start,
            timed_out=False,
        )
