"""CLI command for reading files from GitHub repositories."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from untaped_github.services.file_service import FileOperationService

from ..github_common import (
    create_config_processor,
    create_gh_wrapper,
    ensure_gh_cli_availability,
    format_success_output,
    handle_authentication,
    handle_operation_error,
    handle_validation_result_with_exit_codes,
    process_configuration_with_validation,
)

console = Console()
app = typer.Typer(
    name="read-file", help="Read a file from a GitHub repository", add_completion=False
)


@app.command("read-file")
def read_file(
    config_file: str = typer.Argument(..., help="YAML configuration file path"),
    vars_file: List[str] = typer.Option(
        None, "--vars-file", "-f", help="Variable file(s) for template rendering"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be done without executing"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> Optional[str]:
    """Read a file from a GitHub repository using YAML configuration.

    The configuration file should specify:
    - repository: GitHub repository in owner/repo format
    - file_path: Path to the file within the repository
    - ref: Optional branch/tag/commit (defaults to main)

    Example configuration:
        repository: "octocat/Hello-World"
        file_path: "README.md"
        ref: "main"

    Variables can be provided via --vars-file for template rendering:
        repository: "{{ owner }}/{{ repo }}"
        file_path: "{{ file_path }}"

    Variable file example:
        owner: "octocat"
        repo: "Hello-World"
        file_path: "README.md"
    """
    try:
        # Ensure GitHub CLI is available
        ensure_gh_cli_availability()

        # Create services
        gh_wrapper = create_gh_wrapper()
        config_processor = create_config_processor(gh_wrapper)
        file_service = FileOperationService(gh_wrapper)

        # Handle authentication
        handle_authentication(gh_wrapper)

        # Process and validate configuration
        config_data, validation_result = process_configuration_with_validation(
            config_file, vars_file, dry_run
        )

        handle_validation_result_with_exit_codes(validation_result, dry_run)

        if dry_run:
            # Perform dry run
            try:
                from untaped_github.models.file_operation import FileOperation

                file_operation = FileOperation(**config_data)
                dry_run_result = file_service.dry_run_file_read(file_operation)

                if dry_run_result["can_proceed"]:
                    console.print(
                        Panel.fit(
                            "[green]✅ Dry run successful - operation can proceed[/green]",
                            title="Dry Run Result",
                            border_style="green",
                        )
                    )

                    if verbose:
                        table = Table(title="Configuration Details")
                        table.add_column("Property", style="cyan")
                        table.add_column("Value", style="white")

                        table.add_row("Repository", dry_run_result["repository"])
                        table.add_row("File", dry_run_result["file_path"])
                        table.add_row("Branch", dry_run_result["ref"])
                        table.add_row(
                            "Authentication",
                            "✅ Valid"
                            if dry_run_result.get("authentication_valid")
                            else "❌ Invalid",
                        )
                        table.add_row(
                            "Repository Access",
                            "✅ Accessible"
                            if dry_run_result.get("repository_accessible")
                            else "❌ Not accessible",
                        )
                        table.add_row(
                            "File Exists",
                            "✅ Yes" if dry_run_result.get("file_exists") else "❌ No",
                        )

                        console.print(table)
                else:
                    console.print(
                        Panel.fit(
                            "[red]❌ Dry run failed - operation cannot proceed[/red]",
                            title="Dry Run Result",
                            border_style="red",
                        ),
                        err=True,
                    )

                    if "error" in dry_run_result:
                        console.print(
                            f"[red]Error: {dry_run_result['error']}[/red]", err=True
                        )
                    raise typer.Exit(code=1)

            except Exception as e:
                handle_operation_error(e, "Dry run")
            return None

        # Extract file operation from configuration
        try:
            from untaped_github.models.file_operation import FileOperation

            file_operation = FileOperation(**config_data)
        except Exception as e:
            handle_operation_error(e, "Configuration parsing")

        # Execute file read operation
        try:
            content = file_service.read_file(file_operation)

            # Format and display result
            lines = len(content.splitlines())
            size = len(content.encode("utf-8"))

            if verbose:
                # Rich formatted output for verbose mode
                table = Table(title="File Read Result")
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="white")

                table.add_row("Repository", file_operation.repository)
                table.add_row("File Path", file_operation.file_path)
                table.add_row("Branch", file_operation.ref or "main")
                table.add_row("Size", f"{size} bytes")
                table.add_row("Lines", str(lines))
                table.add_row("Status", "✅ Success")

                console.print(table)
                console.print()
                console.print(
                    Panel(
                        content,
                        title=f"📄 {file_operation.file_path}",
                        border_style="blue",
                        padding=(1, 2),
                    )
                )
            else:
                # Simple output for non-verbose mode
                typer.echo(f"✅ Successfully read file: {file_operation.file_path}")
                typer.echo(f"   Repository: {file_operation.repository}")
                typer.echo(f"   Size: {size} bytes, {lines} lines")
                typer.echo()
                typer.echo(content)

            return content

        except Exception as e:
            handle_operation_error(e, "File read operation")

    except Exception as e:
        handle_operation_error(e, "Read file command")


@app.callback()
def callback() -> None:
    """GitHub file reading commands."""
    pass
