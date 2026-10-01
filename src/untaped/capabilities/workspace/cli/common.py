"""Shared workspace CLI plumbing: settings, adapters, repo arguments and options."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.workspace.application.locate import locate_workspace, workspace_root
from untaped.capabilities.workspace.application.provision import ProvisionRepos
from untaped.capabilities.workspace.application.status import WorkspaceStatus
from untaped.capabilities.workspace.domain.models import RepoArg, RepoSpec, WorkspaceRecord
from untaped.capabilities.workspace.infrastructure import (
    GithubRepoCatalog,
    LocalGitWorktrees,
    StateWorkspaceStore,
)
from untaped.capabilities.workspace.settings import WorkspaceSettings
from untaped.capability_api import (
    ConfigError,
    ParallelOption,
    UsageError,
    clamp_parallel,
    get_config_section,
    read_stdin_input,
    resolve_text_input,
)

STDIN_KINDS = frozenset({"github.repo", "github.repo_hit", "github.sweep_repo"})
"""Pipe kinds ``create``/``add --stdin`` read repos from (see :func:`stdin_repos`)."""
NO_REPOS_HINT = "pass --repo OWNER/NAME (repeatable) or --stdin; the repo picker needs a terminal"
NO_NAME_HINT = "pass NAME and --repo OWNER/NAME (or --stdin); the repo picker needs a terminal"

NameArg = Annotated[
    str | None,
    Parameter(
        name="NAME",
        help="Workspace name. Default: the workspace containing the current directory.",
    ),
]
RepoOption = Annotated[
    list[str] | None,
    Parameter(
        name=["--repo", "-r"],
        negative="",
        consume_multiple=False,
        help=(
            "Repo to check out on the workspace branch: owner/name, a name unique in "
            "the GitHub inventory, or a clone URL (repeatable)."
        ),
    ),
]
ReadOnlyOption = Annotated[
    list[str] | None,
    Parameter(
        name="--read-only",
        negative="",
        consume_multiple=False,
        help="Repo to check out read-only, detached at its base branch (repeatable).",
    ),
]
BranchOption = Annotated[
    str | None,
    Parameter(
        name="--branch",
        help="Branch for every writable repo. Default: the workspace.branch_template setting.",
    ),
]
BaseOption = Annotated[
    str | None,
    Parameter(
        name="--base",
        help="Base branch for every repo, read-only ones too. Default: each repo's default branch.",
    ),
]
WorkspaceParallelOption = Annotated[
    ParallelOption,
    Parameter(
        help=(
            "Concurrent checkouts. Default: the workspace.parallel setting, else "
            "min(8, 2 x CPUs). Values above 2 x CPUs are clamped with a stderr warning."
        ),
    ),
]


def workspace_settings() -> WorkspaceSettings:
    """Typed workspace profile settings for the active profile."""
    return get_config_section("workspace", WorkspaceSettings)


def workspaces_dir(settings: WorkspaceSettings) -> Path:
    return settings.workspaces_dir.expanduser().resolve()


def workspace_dir(settings: WorkspaceSettings, name: str) -> Path:
    """Absolute directory of workspace ``name``."""
    return workspace_root(settings.workspaces_dir, name)


def git_worktrees(settings: WorkspaceSettings) -> LocalGitWorktrees:
    return LocalGitWorktrees(settings.cache_dir.expanduser())


def utc_now() -> datetime:
    return datetime.now(UTC)


def locate(settings: WorkspaceSettings, name: str | None) -> WorkspaceRecord:
    """The workspace ``name``, or the one containing the current directory."""
    return locate_workspace(
        StateWorkspaceStore(), name=name, workspaces_dir=workspaces_dir(settings), cwd=Path.cwd()
    )


def parallel_workers(settings: WorkspaceSettings, requested: int | None) -> int:
    """Worker count: ``--parallel``, else ``workspace.parallel``, else ``min(8, 2 x CPUs)``."""
    cap = (os.cpu_count() or 1) * 2
    if requested is None:
        requested = settings.parallel or min(8, cap)
    return clamp_parallel(requested, cap=cap, policy="2 * os.cpu_count()")


def status_reader(settings: WorkspaceSettings, git: LocalGitWorktrees) -> WorkspaceStatus:
    """The ``status`` use case (also ``archive``'s safety check), ``workspace.parallel`` wide."""
    return WorkspaceStatus(
        git, workspaces_dir=workspaces_dir(settings), parallel=parallel_workers(settings, None)
    )


def provisioner(settings: WorkspaceSettings, parallel: int | None) -> ProvisionRepos:
    """The ``create``/``add`` use case wired to the real adapters."""
    return ProvisionRepos(
        StateWorkspaceStore(workspaces_dir=workspaces_dir(settings)),
        git_worktrees(settings),
        GithubRepoCatalog(protocol=settings.protocol),
        workspaces_dir=workspaces_dir(settings),
        branch_template=settings.branch_template,
        parallel=parallel_workers(settings, parallel),
        now=utc_now,
    )


def repo_args(
    repo: list[str] | None,
    read_only: list[str] | None,
    *,
    branch: str | None,
    base: str | None,
    stdin: bool,
) -> list[RepoArg]:
    """``--repo`` then ``--stdin`` repos (writable), then ``--read-only`` repos."""
    args = [RepoArg(ident=ident, branch=branch, base=base) for ident in repo or []]
    if stdin:
        args += [arg.model_copy(update={"branch": branch, "base": base}) for arg in stdin_repos()]
    args += [RepoArg(ident=ident, read_only=True, base=base) for ident in read_only or []]
    if not args:
        raise UsageError("no repos given", hint=NO_REPOS_HINT)
    return args


def stdin_repos() -> list[RepoArg]:
    """Repos from stdin: bare lines (any repo identifier), or github pipe records.

    A record names its repo by ``full_name`` (else ``repo``, which github rows
    fill with the full name), resolved through the inventory so
    ``workspace.protocol`` and the default branch apply; its ``clone_url``
    (else ``url``) is the fallback, used alone when there is no name.
    """
    data = read_stdin_input(accept_kinds=STDIN_KINDS, what="repos")
    if data.records is None:
        return [RepoArg(ident=value) for value in data.values]
    args: list[RepoArg] = []
    for envelope in data.records:
        record = envelope.record
        name = _text(record.get("full_name")) or _text(record.get("repo"))
        url = _text(record.get("clone_url")) or _text(record.get("url"))
        if name:
            args.append(RepoArg(ident=name, fallback=url))
        elif url:
            args.append(RepoArg(ident=url))
        else:
            raise UsageError(
                f"stdin line {envelope.lineno}: record has no full_name, repo, clone_url or url"
            )
    return args


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


RUN_STDIN_KINDS = frozenset({"workspace.status", "workspace.repo_outcome", "workspace.run_outcome"})
"""Pipe kinds ``run --stdin`` reads repos from."""


def select_run_repos(
    specs: Sequence[RepoSpec],
    *,
    workspace: str,
    repo: Sequence[str] | None,
    stdin: bool,
    include_read_only: bool,
) -> list[RepoSpec]:
    """The repos ``run`` targets: ``--repo`` and ``--stdin`` names (display or dir), else all.

    Writable repos only unless ``include_read_only``: a read-only ``--repo`` is
    a usage error, while read-only repos arriving on stdin (``status`` rows
    list every repo) are dropped. Stdin records of another workspace are
    ignored, and an empty pipe selects nothing. An unknown name is a usage
    error listing the valid ones.
    """
    if not (repo or stdin):
        return [spec for spec in specs if include_read_only or not spec.read_only]
    chosen = _named_repos(specs, list(repo or []))
    read_only = sorted(spec.dir for spec in chosen if spec.read_only)
    if read_only and not include_read_only:
        raise UsageError(
            f"read-only repo selected: {', '.join(read_only)}",
            hint="add --include-read-only to run in read-only repos",
        )
    if stdin:
        piped = _named_repos(specs, _stdin_repo_names(workspace))
        chosen |= {spec for spec in piped if include_read_only or not spec.read_only}
    return [spec for spec in specs if spec in chosen]


def _named_repos(specs: Sequence[RepoSpec], names: Sequence[str]) -> set[RepoSpec]:
    by_name = {key: spec for spec in specs for key in (spec.name, spec.dir)}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        valid = ", ".join(f"{spec.name} ({spec.dir})" for spec in specs)
        raise UsageError(f"unknown repo: {', '.join(unknown)}", hint=f"valid repos: {valid}")
    return {by_name[name] for name in names}


def _stdin_repo_names(workspace: str) -> list[str]:
    """Repo names piped on stdin; records naming another ``workspace`` are skipped."""
    data = read_stdin_input(accept_kinds=RUN_STDIN_KINDS, what="repos", allow_empty=True)
    if data.records is None:
        return list(data.values)
    names: list[str] = []
    for envelope in data.records:
        record = envelope.record
        if _text(record.get("workspace")) not in (None, workspace):
            continue
        name = _text(record.get("repo")) or _text(record.get("dir"))
        if name is None:
            raise UsageError(f"stdin line {envelope.lineno}: record has no repo or dir")
        names.append(name)
    return names


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
