"""CLI command for listing directory contents from GitHub repositories."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from untaped_github.services.file_service import FileOperationService

from ..github_common import (
    create_config_processor,
    create_gh_wrapper,
    ensure_gh_cli_availability,
    format_directory_listing,
    handle_authentication,
    handle_operation_error,
    handle_validation_result_with_exit_codes,
    process_configuration_with_validation,
)

console = Console()
app = typer.Typer(
    name="list-directory",
    help="List files in a GitHub repository directory",
    add_completion=False,
)


@app.command("list-directory")
def list_directory(
    config_file: str = typer.Argument(..., help="YAML configuration file path"),
    vars_file: List[str] = typer.Option(
        None, "--vars-file", "-f", help="Variable file(s) for template rendering"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be done without executing"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Recursively list all files in subdirectories"
    ),
) -> Optional[List[dict]]:
    """List files in a GitHub repository directory using YAML configuration.

    The configuration file should specify:
    - repository: GitHub repository in owner/repo format
    - directory_path: Path to the directory within the repository (use "." for root)

    Example configuration:
        repository: "octocat/Hello-World"
        directory_path: "."
        recursive: true

    Variables can be provided via --vars-file for template rendering:
        repository: "{{ org }}/{{ repo }}"
        directory_path: "{{ path }}"

    Variable file example:
        org: "octocat"
        repo: "Hello-World"
        path: "docs"
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
                repository = config_data["repository"]
                directory_path = config_data.get("directory_path", ".")

                dry_run_result = file_service.dry_run_directory_list(
                    repository, directory_path
                )

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
                        table.add_row("Directory", dry_run_result["directory_path"])
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

        # Extract configuration data
        repository = config_data["repository"]
        directory_path = config_data.get("directory_path", ".")

        # Execute directory listing operation
        try:
            files = file_service.list_directory(repository, directory_path, recursive)

            # Format and display result
            if verbose:
                # Rich formatted output for verbose mode
                console.print(
                    f"[green]✅ Directory listing completed:[/green] {directory_path} in {repository}"
                )

                table = Table(title="Directory Contents")
                table.add_column("Type", style="cyan", justify="center")
                table.add_column("Name", style="white")
                table.add_column("Size", style="yellow", justify="right")

                total_size = 0
                for file_info in files:
                    file_type = "📁" if file_info.get("type") == "dir" else "📄"
                    size = file_info.get("size", 0)
                    total_size += size
                    table.add_row(
                        file_type, file_info.get("name", "unknown"), f"{size:,} bytes"
                    )

                console.print(table)
                console.print(
                    f"[blue]Total: {len(files)} items, {total_size:,} bytes[/blue]"
                )
            else:
                # Simple output for non-verbose mode
                formatted_output = format_directory_listing(
                    files, repository, directory_path
                )
                typer.echo(formatted_output)

            return files

        except Exception as e:
            handle_operation_error(e, "Directory listing operation")

    except Exception as e:
        handle_operation_error(e, "List directory command")


@app.callback()
def callback() -> None:
    """GitHub directory listing commands."""
    pass
