"""What lint's tests share: ``untaped lint`` run under an install, and the ``site`` writer type."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from lint_fake_records import Linttest
from untaped.sdk import Plugin
from untaped.sdk.testing import Ran, install
from untaped_lint.plugin import LintPlugin

#: Writes ``{relative path: source}`` under an importable directory and returns it.
Site = Callable[[dict[str, str]], Path]


@dataclass(frozen=True)
class Result:
    """What one ``untaped lint`` run did, its stderr as one text."""

    exit_code: int
    stdout: str
    stderr: str
    ran: Ran


def run(
    args: Sequence[str],
    *,
    input: str | None = None,
    providers: Mapping[str, Sequence[type]] | None = None,
    plugins: Sequence[type[Plugin]] = (),
    settings: BaseModel | Sequence[BaseModel] = (),
) -> Result:
    """``untaped lint ARGS``, with ``input`` piped on stdin.

    lint is installed with the fake records' plugin, ``plugins`` and
    ``providers``, one plugin per key.
    """
    ran = install(LintPlugin, Linttest, *plugins, settings=settings, providers=providers).cli(
        ["lint", *args], input=input
    )
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)
