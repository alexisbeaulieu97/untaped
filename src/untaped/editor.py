"""Launch a configured external editor with literal argv and caller-owned streams."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from untaped.errors import ConfigError


def run_editor(
    path: Path,
    *,
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Wait for an editor; VISUAL precedes EDITOR unless explicit argv is supplied.

    No shell is used. Callers own terminal requirements, file permissions,
    validation and cleanup. GUI editors must be configured with their wait flag.
    Omitted streams inherit the process streams, as ``config edit`` expects.
    """
    try:
        command = (
            list(argv)
            if argv is not None
            else shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "")
        )
    except ValueError as exc:
        raise ConfigError("invalid quoting in $VISUAL or $EDITOR") from exc
    if not command:
        raise ConfigError("set $VISUAL or $EDITOR to use an external editor")
    try:
        subprocess.run([*command, str(path)], check=True, stdin=stdin, stdout=stdout, stderr=stderr)
    except FileNotFoundError as exc:
        raise ConfigError(f"editor not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"editor exited with status {exc.returncode}") from exc
    except OSError as exc:
        raise ConfigError("could not launch editor") from exc
    except KeyboardInterrupt as exc:
        raise ConfigError("editor cancelled") from exc
