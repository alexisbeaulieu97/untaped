"""Shared helpers for workspace CLI command modules."""

from __future__ import annotations

import os
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.workspace.application import WorkspaceResolver
from untaped.capabilities.workspace.domain import Workspace
from untaped.capabilities.workspace.infrastructure import (
    WorkspaceRegistryRepository,
    YamlManifestRepository,
)
from untaped.capabilities.workspace.settings import WorkspaceSettings
from untaped.capability_api import (
    ParallelOption,
    clamp_parallel,
    get_config_section,
    raise_usage,
)

WORKSPACE_ARG_HELP = (
    "Workspace name, or a path inside one (`.` is the current directory). "
    "Default: the workspace containing the current directory."
)

RepoSelectorOption = Annotated[
    list[str] | None,
    Parameter(
        name=["--repo", "-r"],
        negative="",
        help="Limit to these repos (repeatable; name or URL).",
        consume_multiple=False,
    ),
]
WorkspaceArg = Annotated[str | None, Parameter(name="WS", help=WORKSPACE_ARG_HELP)]
"""Optional leading positional ``WS`` (see :class:`WorkspaceResolver`)."""

WorkspaceParallelOption = Annotated[
    ParallelOption,
    Parameter(
        help=(
            "Concurrent workers. Default: the workspace.parallel setting, else "
            "min(8, 2 x CPUs). Values above 2 x CPUs are clamped with a stderr warning."
        ),
    ),
]


def workspace_settings() -> WorkspaceSettings:
    """Typed workspace profile settings for the active profile.

    Stays on ``get_config_section`` rather than ``app_context().section``:
    the CLI app is exercised directly in tests (without section registration),
    where only ``get_config_section`` can build its one-off section model.
    Profile selection is owned by the root ``--profile`` option (valid in any
    token position); commands no longer take a command-local override.
    """
    return get_config_section("workspace", WorkspaceSettings)


def resolve_workspace(workspace: str | None) -> Workspace:
    """Resolve a ``WS`` argument (name, path, or ``None`` for the cwd)."""
    return WorkspaceResolver(
        registry=WorkspaceRegistryRepository(),
        manifests=YamlManifestRepository(),
    ).resolve(workspace)


def target_workspaces(workspace: str | None, *, all_workspaces: bool) -> list[Workspace]:
    if all_workspaces:
        if workspace is not None:
            raise_usage("--all cannot be combined with a workspace argument")
        return WorkspaceRegistryRepository().entries()
    return [resolve_workspace(workspace)]


def split_leading_workspace(first: str | None, second: str | None) -> tuple[str | None, str | None]:
    """Split ``[WS] VALUE`` positionals: one token is ``VALUE``, two are ``WS VALUE``."""
    if second is None:
        return None, first
    return first, second


def parallel_workers(requested: int | None) -> int:
    """Worker count: ``--parallel``, else ``workspace.parallel``, else ``min(8, cap)``.

    The cap, ``2 * os.cpu_count()``, is the I/O-bound rule of thumb shared by
    sync and foreach; it is computed per call so ``os.cpu_count``
    monkeypatching in tests stays live.
    """
    cap = (os.cpu_count() or 1) * 2
    if requested is None:
        requested = workspace_settings().parallel or min(8, cap)
    return clamp_parallel(requested, cap=cap, policy="2 * os.cpu_count()")
