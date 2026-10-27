"""``untaped workspace run``: run one command in each repo of a workspace."""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from pydantic import Field

from untaped.sdk import (
    AllowHyphen,
    ConfigError,
    Failure,
    Output,
    Parallel,
    Positional,
    Short,
    Stdin,
    UsageError,
    echo,
    resolve_text_input,
)
from untaped_workspace.application.run import RunInRepos, RunTarget
from untaped_workspace.commands._workspace import WORKERS, WorkspaceCommand
from untaped_workspace.domain.models import RepoSpec
from untaped_workspace.infrastructure import SubprocessRunner
from untaped_workspace.records import RepoOutcome, RepoStatus, RunOutcome


def _blocks(rows: list[RunOutcome]) -> None:
    """Table format: ``run`` already printed each repo's block as it finished."""
    del rows


class Run(WorkspaceCommand):
    """Run a command in each repo of a workspace.

    A shell string runs as `sh -c -- CMD`, so pipes and `&&` work. A file,
    resolved against the current directory, runs directly when it is
    executable and starts with `#!`, else through `sh`. A CMD of `-` reads
    the script from stdin; nothing piped, or `-` with --stdin, exits 2. A
    command that starts with a hyphen goes after `--`; a lone word starting
    with `--` is taken as a mistyped option (exit 2).

    Each run starts in the repo directory with stdin from /dev/null and
    UNTAPED_WORKSPACE, UNTAPED_REPO (the display name), UNTAPED_BRANCH
    (empty for a read-only repo), UNTAPED_BASE and UNTAPED_READ_ONLY (`1` or
    `0`) in its environment. Background processes it starts are stopped when
    it exits, except ones in their own session or process group (`setsid`).
    There is no preview.

    Only writable repos run, unless --include-read-only: without it, naming
    a read-only repo with --repo exits 2, and --stdin drops read-only repos,
    so a whole workspace's `status` rows pipe straight in. --stdin reads repo
    names, or `workspace.repo_status`, `workspace.repo_outcome` or
    `workspace.run_outcome` records, ignoring other workspaces' records.
    With --repo as well, both sets run. When nothing is left to run, it
    warns and exits 0.

    Every selected repo runs, and the exit is 1 when any failed: a non-zero
    status, a timeout (the whole process tree is killed), a signal or a
    missing directory. In table format each finished repo prints a header,
    its stdout, then its stderr, and a summary follows on stderr; other
    formats print `workspace.run_outcome` records only.

    Examples
    --------
    ```console
    $ untaped workspace run PROJ-123 'git push -u origin HEAD'
    $ untaped workspace run PROJ-123 ./bump.sh
    $ untaped workspace run PROJ-123 - < rebase.sh
    $ untaped workspace status PROJ-123 -f pipe | untaped workspace run PROJ-123 'git stash' --stdin
    ```
    """

    name_or_cmd: Annotated[Positional[str], AllowHyphen] = Field(
        description=(
            "Workspace name, or the command when it is the only argument "
            "(the workspace is then found from the current directory)."
        )
    )
    cmd: Annotated[Positional[str | None], AllowHyphen] = Field(
        None,
        description=(
            "The command: a shell string, a script file, or - to read a script "
            "from stdin. Runs in each repo directory with stdin from /dev/null."
        ),
    )
    repo: Annotated[list[str], Short("-r")] = Field(
        default_factory=list,
        description="Only this repo, by display name or directory (repeatable).",
    )
    stdin: Stdin[RepoStatus | RepoOutcome | RunOutcome]
    include_read_only: bool = Field(False, description="Also run in read-only repos.")
    fail_fast: bool = Field(
        False, description="Stop starting repos after the first failure; the rest are skipped."
    )
    timeout: float = Field(
        600.0,
        gt=0,
        allow_inf_nan=False,
        description="Seconds allowed per repo before it is killed.",
    )
    parallel: Parallel = WORKERS
    output: Output[RunOutcome] = Output(render={"table": _blocks})

    def run(self) -> Iterator[RunOutcome]:
        name, command = (
            (None, self.name_or_cmd) if self.cmd is None else (self.name_or_cmd, self.cmd)
        )
        _check_positionals(name, command, stdin=self.stdin.requested)
        record = self.locate(name)
        specs = self._selected(record.repos, workspace=record.name)
        root = self.workspace_dir(record.name)
        targets = [RunTarget(record.name, spec, root / spec.dir) for spec in specs]
        blocks = self.output.format == "table" and self.output.requested is None
        if not targets:
            self.ctx.diag.warning("no repos to run in (read-only repos need --include-read-only)")
            return iter(())
        with run_argv(command) as argv:
            run = RunInRepos(
                SubprocessRunner(),
                parallel=self.parallel.value,
                timeout=self.timeout,
                fail_fast=self.fail_fast,
                on_done=_echo_block if blocks else None,
            )
            rows = run(targets, argv)
        if blocks:
            self._summary(rows)
        return iter(rows)

    def _selected(self, specs: Sequence[RepoSpec], *, workspace: str) -> list[RepoSpec]:
        """The repos to run in: ``--repo`` and ``--stdin`` names (display or dir), else all.

        Writable repos only unless ``--include-read-only``: a read-only
        ``--repo`` is a usage error, while read-only repos arriving on stdin
        (``status`` rows list every repo) are dropped. Stdin rows of another
        workspace are ignored, and an empty pipe selects nothing. An unknown
        name is a usage error listing the valid ones.
        """
        include = self.include_read_only
        if not (self.repo or self.stdin.requested):
            return [spec for spec in specs if include or not spec.read_only]
        chosen = _named(specs, self.repo)
        read_only = sorted(spec.dir for spec in chosen if spec.read_only)
        if read_only and not include:
            raise UsageError(
                f"read-only repo selected: {', '.join(read_only)}",
                hint="add --include-read-only to run in read-only repos",
            )
        if self.stdin.requested:
            names = [
                value if isinstance(value, str) else value.repo
                for value in self.stdin.values(allow_empty=True)
                if isinstance(value, str) or value.workspace == workspace
            ]
            chosen |= {spec for spec in _named(specs, names) if include or not spec.read_only}
        return [spec for spec in specs if spec in chosen]

    def _summary(self, rows: Sequence[RunOutcome]) -> None:
        failed = [row.dir for row in rows if row.failed]
        actions = Counter(row.action for row in rows)
        parts = [f"{actions['ran']} ok"]
        if failed:
            parts.append(f"{len(failed)} failed ({', '.join(failed)})")
        if actions["skipped"]:
            parts.append(f"{actions['skipped']} skipped")
        text = " · ".join(parts)
        if failed:
            self.ctx.diag.failure(Failure.plain(text))
        else:
            self.ctx.diag.success(text)


def _named(specs: Sequence[RepoSpec], names: Sequence[str]) -> set[RepoSpec]:
    by_name = {key: spec for spec in specs for key in (spec.name, spec.dir)}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        valid = ", ".join(f"{spec.name} ({spec.dir})" for spec in specs)
        raise UsageError(f"unknown repo: {', '.join(unknown)}", hint=f"valid repos: {valid}")
    return {by_name[name] for name in names}


def _check_positionals(name: str | None, command: str, *, stdin: bool) -> None:
    """Usage errors ``run`` catches before touching anything.

    The positionals accept a leading hyphen (``-`` is a stdin script), so a
    mistyped long option would otherwise run as a command in every repo.
    """
    for value in (name, command):
        if value is not None and value.startswith("--") and " " not in value:
            raise UsageError(
                f"unknown option: {value}",
                hint="a command that starts with -- needs quoting with a space, e.g. ' --x'",
            )
    if command == "-" and stdin:
        raise UsageError("a script on stdin (-) cannot be combined with --stdin")


def _echo_block(row: RunOutcome) -> None:
    """One finished repo's output: a header (with the reason unless it ran), stdout, stderr.

    Skipped rows print nothing (the summary counts them); each line is
    flushed, so blocks stream through pipes.
    """
    if row.action == "skipped":
        return
    reason = f" · {row.detail}" if row.action != "ran" and row.detail else ""
    echo(f"── {row.repo} ({row.dir}){reason} ──")
    for text in (row.stdout, row.stderr):
        if text:
            echo(text.rstrip("\n"))


@contextmanager
def run_argv(command: str) -> Iterator[list[str]]:
    """The argv for ``run``'s command argument.

    ``-`` is a script read from stdin (kept in a temp file for the duration);
    an existing file runs directly when executable, else through ``sh``;
    anything else is a ``sh -c`` string.
    """
    if command == "-":
        try:
            script = resolve_text_input(value=None, file=None, what="script")
        except ConfigError:
            raise UsageError("`-` reads a script from stdin, but nothing was piped") from None
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
            handle.write(script + "\n")
        try:
            yield ["sh", handle.name]
        finally:
            Path(handle.name).unlink(missing_ok=True)
        return
    path = _existing_file(command)
    if path is not None:
        absolute = str(Path.cwd() / path)
        yield [absolute] if _runs_directly(absolute) else ["sh", absolute]
    else:
        yield ["sh", "-c", "--", command]


def _existing_file(command: str) -> Path | None:
    """``command`` as a path when it names an existing file; ``None`` for a command string.

    ``~nosuchuser/x`` raises ``RuntimeError`` from ``expanduser``: a command string too.
    """
    try:
        path = Path(command).expanduser()
        return path if path.is_file() else None
    except RuntimeError, OSError, ValueError:
        return None


def _runs_directly(path: str) -> bool:
    """Executable with a shebang; anything else needs ``sh`` (no ``Exec format error``)."""
    if not os.access(path, os.X_OK):
        return False
    with open(path, "rb") as handle:
        return handle.read(2) == b"#!"
