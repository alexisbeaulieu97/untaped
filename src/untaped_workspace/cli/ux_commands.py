"""Display and user-environment commands for the workspace CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from untaped import (
    ColumnsOption,
    FormatOption,
    ProfileOverrideOption,
    profile_override,
    read_identifiers,
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
from untaped_workspace.cli.common import resolve_workspace
from untaped_workspace.cli.completions import complete_workspace_name
from untaped_workspace.cli.rendering import render_rows
from untaped_workspace.domain import Workspace
from untaped_workspace.infrastructure import (
    ManifestRepository,
    WorkspaceRegistryRepository,
    editor_runner,
    resolve_editor_argv,
)


def register_display_commands(app: typer.Typer) -> None:
    app.command("list")(list_command)
    app.command("show")(show_command)


def register_ux_commands(app: typer.Typer) -> None:
    app.command("path", no_args_is_help=True)(path_command)
    app.command("shell-init", no_args_is_help=True)(shell_init_command)
    app.command("edit")(edit_command)


def list_command(
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """List registered workspaces."""
    with report_errors(), profile_override(profile):
        use_case = ListWorkspaces(WorkspaceRegistryRepository())
        rows: list[dict[str, object]] = [_workspace_row(w) for w in use_case()]
        typer.echo(render_rows(rows, fmt=fmt, columns=columns))


def show_command(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    profile: ProfileOverrideOption = None,
) -> None:
    """Show manifest details for one workspace."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        rows = [row.model_dump() for row in ShowWorkspace(ManifestRepository())(ws)]
        typer.echo(render_rows(rows, fmt=fmt, columns=columns))


def path_command(
    names: list[str] | None = typer.Argument(
        None, help="Workspace name(s).", autocompletion=complete_workspace_name
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read workspace names from stdin (one per line)."
    ),
    profile: ProfileOverrideOption = None,
) -> None:
    """Print the absolute path of one or more workspaces (one per line)."""
    get_path = WorkspacePath(WorkspaceRegistryRepository())
    any_failed = False
    with report_errors(), profile_override(profile):
        idents = read_identifiers(list(names or []), stdin=stdin)

        def _echo_path(workspace_name: str) -> None:
            typer.echo(str(get_path(workspace_name)))

        _, any_failed = resolve_each(idents, _echo_path)
    if any_failed:
        raise typer.Exit(code=1)


def shell_init_command(
    shell: str = typer.Argument(..., help='One of "zsh", "bash", "fish".'),
) -> None:
    """Emit a shell snippet defining `uwcd <workspace>`."""
    with report_errors():
        snippet = ShellInit()(shell)
        typer.echo(snippet, nl=False)


def edit_command(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace name.",
        autocompletion=complete_workspace_name,
    ),
    path: Path | None = typer.Option(None, "--path", "-p", help="Workspace path."),
    editor: str | None = typer.Option(None, "--editor", "-e", help="Override $VISUAL/$EDITOR."),
    profile: ProfileOverrideOption = None,
) -> None:
    """Open the workspace directory in your editor."""
    with report_errors(), profile_override(profile):
        ws = resolve_workspace(workspace, path)
        argv = resolve_editor_argv(editor)
        rc = EditWorkspace(runner=editor_runner)(ws, argv=argv)
        if rc != 0:
            raise typer.Exit(code=rc)


def _workspace_row(w: Workspace) -> dict[str, object]:
    # ``name`` first: under ``--format raw`` the first key is what pipelines
    # feed back into the next command. See root AGENTS.md '--format raw
    # default-column contract'; pinned by tests/unit/test_format_raw_first_key.py.
    return {"name": w.name, "path": str(w.path)}
