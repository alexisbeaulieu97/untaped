"""Shared workspace CLI plumbing: settings, adapters, repo arguments and options."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.workspace.application.locate import locate_workspace
from untaped.capabilities.workspace.application.provision import ProvisionRepos
from untaped.capabilities.workspace.domain.models import RepoArg, WorkspaceRecord
from untaped.capabilities.workspace.infrastructure import (
    GithubRepoCatalog,
    LocalGitWorktrees,
    StateWorkspaceStore,
)
from untaped.capabilities.workspace.settings import WorkspaceSettings
from untaped.capability_api import (
    ParallelOption,
    UsageError,
    clamp_parallel,
    get_config_section,
    read_stdin_input,
)

STDIN_KINDS = frozenset({"github.repo", "github.repo_hit", "github.sweep_repo"})
"""Pipe kinds ``create``/``add --stdin`` read repos from (``clone_url``, ``url`` or ``repo``)."""

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
        help="Base branch for every writable repo. Default: each repo's default branch.",
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
    return settings.workspaces_dir.expanduser().absolute()


def workspace_dir(settings: WorkspaceSettings, name: str) -> Path:
    """Absolute directory of workspace ``name``."""
    return workspaces_dir(settings) / name


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


def provisioner(settings: WorkspaceSettings, parallel: int | None) -> ProvisionRepos:
    """The ``create``/``add`` use case wired to the real adapters."""
    return ProvisionRepos(
        StateWorkspaceStore(),
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
    writable = [*(repo or []), *(stdin_repos() if stdin else [])]
    args = [RepoArg(ident=ident, branch=branch, base=base) for ident in writable]
    args += [RepoArg(ident=ident, read_only=True) for ident in read_only or []]
    if not args:
        raise UsageError("no repos given", hint="pass --repo OWNER/NAME (repeatable) or --stdin")
    return args


def stdin_repos() -> list[str]:
    """Repo identifiers from stdin: bare lines, or pipe records' ``clone_url``/``url``/``repo``."""
    data = read_stdin_input(accept_kinds=STDIN_KINDS, what="repos")
    if data.records is None:
        return list(data.values)
    idents: list[str] = []
    for envelope in data.records:
        record = envelope.record
        ident = record.get("clone_url") or record.get("url") or record.get("repo")
        if not isinstance(ident, str):
            raise UsageError(
                f"stdin line {envelope.lineno}: record has no clone_url, url or repo field"
            )
        idents.append(ident)
    return idents
