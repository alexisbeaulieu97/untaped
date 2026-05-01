"""Shell-out helper used by the ``foreach`` use case."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

CommandRunner = Callable[[str, Path], "subprocess.CompletedProcess[str]"]


def shell_runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` (interpreted by the shell) inside ``cwd`` and capture output."""
    return subprocess.run(cmd, shell=True, cwd=cwd, text=True, capture_output=True, check=False)
