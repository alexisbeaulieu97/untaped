"""Display and user-environment commands for the workspace CLI."""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter
from untaped.api import (
    ColumnsOption,
    FormatOption,
    echo,
    read_identifiers,
    render_rows,
    report_errors,
    resolve_each,
)

from untaped_workspace.application import (
    EditWorkspace,
    ListWorkspaces,
    ShellInit,
    ShowWorkspace,
    WorkspacePath,
)
from untaped_workspace.cli.common import WorkspaceNameOption, WorkspacePathOption, resolve_workspace
from untaped_workspace.domain import Workspace, WorkspaceDetailRow
from untaped_workspace.infrastructure import (
    ManifestRepository,
    WorkspaceRegistryRepository,
    editor_runner,
    resolve_editor_argv,
)


def register_display_commands(app: App) -> None:
    app.command(list_command, name="list")
    app.command(show_command, name="show")


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
        rendered = render_rows(
            rows,
            fmt=fmt,
            columns=columns,
            kind="workspace.workspace",
            empty="No workspaces registered. Create one with `untaped workspace init <name>`.",
        )
        if rendered:
            echo(rendered)


def show_command(
    *,
    workspace: WorkspaceNameOption = None,
    path: WorkspacePathOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show manifest details for one workspace."""
    with report_errors():
        ws = resolve_workspace(workspace, path)
        rows = [_show_row(row) for row in ShowWorkspace(ManifestRepository())(ws)]
        echo(render_rows(rows, fmt=fmt, columns=columns, kind=_show_kind(rows)))


def path_command(
    names: Annotated[list[str] | None, Parameter(help="Workspace name(s).")] = None,
    *,
    stdin: Annotated[
        bool,
        Parameter(
            name="--stdin",
            negative="",
            help="Read workspace names from stdin (one per line, or a --format pipe stream).",
        ),
    ] = False,
) -> None:
    """Print the absolute path of one or more workspaces (one per line)."""
    get_path = WorkspacePath(WorkspaceRegistryRepository())
    any_failed = False
    with report_errors():
        idents = read_identifiers(list(names or []), stdin=stdin, id_field="name")

        def _echo_path(workspace_name: str) -> None:
            echo(str(get_path(workspace_name)))

        _, any_failed = resolve_each(idents, _echo_path)
    if any_failed:
        raise SystemExit(1)


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
            raise SystemExit(rc)


def _workspace_row(w: Workspace) -> dict[str, object]:
    # ``name`` first: under ``--format raw`` the first key is what pipelines
    # feed back into the next command. See root AGENTS.md '--format raw
    # default-column contract'; pinned by tests/unit/test_format_raw_first_key.py.
    return {"name": w.name, "path": str(w.path)}


def _show_row(row: WorkspaceDetailRow) -> dict[str, object]:
    data = row.model_dump()
    if data.get("target_path") is None:
        del data["target_path"]
    return data


def _show_kind(rows: list[dict[str, object]]) -> str:
    if (
        len(rows) == 1
        and rows[0].get("repo_count") == 0
        and rows[0].get("repo") == ""
        and "target_path" not in rows[0]
    ):
        return "workspace.summary"
    return "workspace.repo"
