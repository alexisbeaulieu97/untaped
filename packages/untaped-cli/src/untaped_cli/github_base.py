"""Base classes for GitHub CLI commands to reduce duplication."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, TypeVar

import typer
from rich.console import Console
from untaped_github.services.file_service import FileOperationService

from ..github_common import (
    create_config_processor,
    create_gh_wrapper,
    ensure_gh_cli_availability,
    handle_authentication,
    handle_operation_error,
    handle_validation_result_with_exit_codes,
    process_configuration_with_validation,
)

T = TypeVar("T")


class GitHubBaseCommand(ABC):
    """Base class for GitHub CLI commands to reduce code duplication."""

    def __init__(self, name: str, help_text: str) -> None:
        self.console = Console()
        self.app = typer.Typer(name=name, help=help_text, add_completion=False)

    @abstractmethod
    def get_config_schema(self) -> Dict[str, str]:
        """Return the configuration schema for this command."""
        pass

    @abstractmethod
    def validate_config(self, config_data: Dict[str, Any]) -> None:
        """Validate command-specific configuration."""
        pass

    @abstractmethod
    def execute_operation(
        self, config_data: Dict[str, Any], dry_run: bool, verbose: bool
    ) -> Any:
        """Execute the specific operation."""
        pass

    def create_command(
        self,
        config_file: str,
        vars_file: List[str] = None,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> Any:
        """Common command execution logic."""
        try:
            # Ensure GitHub CLI is available
            ensure_gh_cli_availability()

            # Create services
            gh_wrapper = create_gh_wrapper()
            config_processor = create_config_processor(gh_wrapper)

            # Handle authentication
            handle_authentication(gh_wrapper)

            # Process and validate configuration
            config_data, validation_result = process_configuration_with_validation(
                config_file, vars_file, dry_run
            )

            handle_validation_result_with_exit_codes(validation_result, dry_run)

            if dry_run:
                return self.execute_dry_run(config_data, verbose)

            # Execute the specific operation
            return self.execute_operation(config_data, dry_run, verbose)

        except Exception as e:
            handle_operation_error(e, f"{self.app.info.name} command")

    def execute_dry_run(
        self, config_data: Dict[str, Any], verbose: bool
    ) -> Optional[Dict[str, Any]]:
        """Execute dry run for the command."""
        try:
            # Validate command-specific configuration
            self.validate_config(config_data)

            # Create dry run result
            dry_run_result = {
                "command": self.app.info.name,
                "can_proceed": True,
                "configuration": config_data,
            }

            if verbose:
                self.display_dry_run_details(dry_run_result)

            return dry_run_result

        except Exception as e:
            handle_operation_error(e, f"{self.app.info.name} dry run")

    def display_dry_run_details(self, dry_run_result: Dict[str, Any]) -> None:
        """Display detailed dry run information."""
        self.console.print(
            self.console.panel.Panel.fit(
                "[green]✅ Dry run successful - operation can proceed[/green]",
                title="Dry Run Result",
                border_style="green",
            )
        )

        if "configuration" in dry_run_result:
            config = dry_run_result["configuration"]

            # Display configuration details
            table = self.console.table.Table(title="Configuration Details")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="white")

            for key, value in config.items():
                table.add_row(key, str(value))

            self.console.print(table)

    def add_common_options(self) -> None:
        """Add common options to the command."""
        pass  # Subclasses can override this to add specific options

    def get_command_decorator(self) -> typer.models.CommandInfo:
        """Get command decorator for registration."""
        return self.app.command(self.app.info.name)
