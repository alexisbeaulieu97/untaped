"""Display and user-environment commands for the workspace CLI."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.workspace.application import (
    EditWorkspace,
    ListWorkspaces,
    ShellInit,
    ShowWorkspace,
    WorkspacePath,
)
from untaped.capabilities.workspace.cli.common import (
    WorkspaceNameOption,
    WorkspacePathOption,
    record_row,
    resolve_workspace,
)
from untaped.capabilities.workspace.domain import Workspace, WorkspaceSummaryRow
from untaped.capabilities.workspace.infrastructure import (
    WorkspaceRegistryRepository,
    YamlManifestRepository,
    editor_runner,
    resolve_editor_argv,
)
from untaped.capability_api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    StdinOption,
    deprecated_alias,
    echo,
    emit,
    finish,
    read_identifiers,
    report_errors,
    resolve_each,
)


def register_display_commands(app: App) -> None:
    app.command(list_command, name="list")
    app.command(get_command, name="get")
    deprecated_alias(app, "show", "get")


def register_ux_commands(app: App) -> None:
    app.command(path_command, name="path")
    app.command(shell_init_command, name="shell-init")
    app.command(edit_command, name="edit")


def list_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List registered workspaces."""
    with report_errors():
        use_case = ListWorkspaces(WorkspaceRegistryRepository())
        rows: list[dict[str, object]] = [_workspace_row(w) for w in use_case()]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="workspace.workspace",
            empty="No workspaces registered. Create one with `untaped workspace init <name>`.",
        )


def get_command(
    *,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show manifest details for one workspace."""
    with report_errors():
        ws = resolve_workspace(workspace, path)
        details = ShowWorkspace(YamlManifestRepository())(ws)
        kind = (
            "workspace.repo.summary"
            if any(isinstance(row, WorkspaceSummaryRow) for row in details)
            else "workspace.repo"
        )
        emit([record_row(row) for row in details], fmt=fmt, columns=columns, kind=kind)


def path_command(
    names: Annotated[list[str] | None, Parameter(negative="", help="Workspace names.")] = None,
    /,
    *,
    stdin: Annotated[
        StdinOption,
        Parameter(
            help=(
                "Read workspace names from stdin: one per line, or a --format pipe "
                "stream of workspace.workspace records."
            ),
        ),
    ] = False,
) -> None:
    """Print the absolute path of one or more workspaces (one per line)."""
    get_path = WorkspacePath(WorkspaceRegistryRepository())
    any_failed = False
    with report_errors():
        idents = read_identifiers(
            list(names or []),
            stdin=stdin,
            id_field="name",
            accept_kinds={"workspace.workspace"},
        )

        def _echo_path(workspace_name: str) -> None:
            echo(str(get_path(workspace_name)))

        _, any_failed = resolve_each(idents, _echo_path)
    finish(any_failed)


def shell_init_command(
    shell: Annotated[str, Parameter(help='One of "zsh", "bash", "fish".')],
    /,
) -> None:
    """Emit a shell snippet defining `uwcd <workspace>`."""
    with report_errors():
        snippet = ShellInit()(shell)
        echo(snippet, nl=False)


def edit_command(
    *,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    editor: Annotated[
        str | None,
        Parameter(name=["--editor", "-e"], help="Override $VISUAL/$EDITOR."),
    ] = None,
) -> None:
    """Open the workspace directory in your editor."""
    with report_errors():
        ws = resolve_workspace(workspace, path)
        argv = resolve_editor_argv(editor)
        rc = EditWorkspace(runner=editor_runner)(ws, argv=argv)
        if rc != 0:
            raise ConfigError(f"editor exited with status {rc}")


def _workspace_row(w: Workspace) -> dict[str, object]:
    # ``name`` first: under ``--format raw`` the first key is what pipelines
    # feed back into the next command. See root AGENTS.md '--format raw
    # default-column contract'; pinned by tests/unit/test_format_raw_first_key.py.
    return {"name": w.name, "path": str(w.path)}
