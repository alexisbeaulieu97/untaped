"""Common utilities for GitHub CLI commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from pydantic import ValidationError
from untaped_core.models.configuration import ConfigurationFile
from untaped_github.api.auth import GitHubAuthError
from untaped_github.error_formatter import ErrorFormatter
from untaped_github.gh_cli_wrapper import GitHubCliWrapper
from untaped_github.models.validation import ValidationError, ValidationResult
from untaped_github.services.auth_service import AuthenticationService
from untaped_github.services.config_processor import ConfigurationProcessor


def create_gh_wrapper() -> GitHubCliWrapper:
    """Create a GitHub CLI wrapper instance."""
    return GitHubCliWrapper()


def create_config_processor(gh_wrapper: GitHubCliWrapper) -> ConfigurationProcessor:
    """Create a configuration processor instance."""
    return ConfigurationProcessor(gh_wrapper)


def create_auth_service(gh_wrapper: GitHubCliWrapper) -> AuthenticationService:
    """Create an authentication service instance."""
    return AuthenticationService(gh_wrapper)


def find_config_file(config_file: str = None) -> Path:
    """Find and validate the configuration file path using multiple discovery methods.

    Search order:
    1. Explicit path provided as argument
    2. Environment variable UNTAPED_GITHUB_CONFIG_FILE
    3. Default locations: ./untaped-github.yaml, ./untaped-github.yml
    4. Hidden files: ./.untaped-github.yaml, ./.untaped-github.yml

    Args:
        config_file: Explicit configuration file path from CLI argument

    Returns:
        Path to the configuration file

    Raises:
        typer.BadParameter: If no valid configuration file is found
    """
    # If explicit path provided, use it
    if config_file:
        path = Path(config_file)
        if not path.exists():
            raise typer.BadParameter(
                f"Configuration file does not exist: {config_file}"
            )
        if not path.is_file():
            raise typer.BadParameter(
                f"Configuration file path is not a file: {config_file}"
            )
        return path.resolve()

    # Check environment variable
    env_config = os.environ.get("UNTAPED_GITHUB_CONFIG_FILE")
    if env_config:
        path = Path(env_config)
        if path.exists() and path.is_file():
            return path.resolve()

    # Search in current directory
    current_dir = Path.cwd()

    # Default filenames to look for
    config_names = [
        "untaped-github.yaml",
        "untaped-github.yml",
        ".untaped-github.yaml",
        ".untaped-github.yml",
    ]

    for config_name in config_names:
        config_path = current_dir / config_name
        if config_path.exists() and config_path.is_file():
            return config_path.resolve()

    # If no config file found, provide helpful error message
    available_files = [
        f"./{name}" for name in config_names[:2]
    ]  # Show non-hidden options
    raise typer.BadParameter(
        f"No configuration file found. Please specify one using:\n"
        f"  --config-file <path>\n"
        f"  Or set environment variable: UNTAPED_GITHUB_CONFIG_FILE=<path>\n"
        f"  Or create one of: {', '.join(available_files)}\n"
        f"  Or use: {available_files[0]}"
    )


def get_config_file_path(config_file: str) -> Path:
    """Get and validate the configuration file path (legacy method)."""
    return find_config_file(config_file)


def get_var_file_paths(var_files: List[str]) -> List[Path]:
    """Get and validate variable file paths."""
    paths = []
    for var_file in var_files:
        path = Path(var_file)
        if not path.exists():
            raise typer.BadParameter(f"Variable file does not exist: {var_file}")
        if not path.is_file():
            raise typer.BadParameter(f"Variable file path is not a file: {var_file}")
        paths.append(path.resolve())
    return paths


def get_environment_variables() -> Dict[str, str]:
    """Get environment variables for template rendering."""
    # Define which environment variables to include for GitHub operations
    env_vars = [
        "GITHUB_TOKEN",
        "GITHUB_USER",
        "GITHUB_REPO",
        "GITHUB_ORG",
        "UNTAPED_GITHUB_*",  # Include all untaped-github prefixed env vars
    ]

    variables = {}
    for env_var in env_vars:
        if env_var.endswith("*"):
            # Handle wildcard patterns
            prefix = env_var[:-1]
            for key, value in os.environ.items():
                if key.startswith(prefix):
                    variables[key] = value
        elif env_var in os.environ:
            variables[env_var] = os.environ[env_var]

    return variables


def process_configuration_with_validation(
    config_file: str, var_files: List[str] = None, dry_run: bool = False
) -> tuple[Dict[str, Any], ValidationResult]:
    """Process and validate configuration, handling all errors gracefully."""
    gh_wrapper = create_gh_wrapper()
    processor = create_config_processor(gh_wrapper)

    try:
        # Get file paths
        config_path = get_config_file_path(config_file)
        var_paths = get_var_file_paths(var_files) if var_files else []

        # Get environment variables
        env_vars = get_environment_variables()

        # Process configuration
        config_data, validation_result = processor.process_and_validate(
            config_path, var_paths, env_vars
        )

        return config_data, validation_result

    except Exception as e:
        # Return failed validation result for configuration processing errors
        return {}, ValidationResult.failure(
            [
                ValidationError(
                    field="configuration", message=str(e), error_type="processing"
                )
            ]
        )


def handle_validation_result(
    validation_result: ValidationResult, dry_run: bool = False
) -> None:
    """Handle validation result and exit appropriately."""
    if not validation_result.is_valid:
        formatted_error = ErrorFormatter.format_validation_result(validation_result)
        typer.echo(formatted_error, err=True)
        raise typer.Exit(code=1)

    if validation_result.warnings and not dry_run:
        formatted_warnings = ErrorFormatter.format_validation_result(validation_result)
        typer.echo(formatted_warnings, err=True)


def handle_authentication(
    gh_wrapper: GitHubCliWrapper, require_auth: bool = True
) -> None:
    """Handle authentication requirements with proper error handling."""
    auth_service = AuthenticationService(gh_wrapper)

    if require_auth:
        try:
            auth_service.require_authentication()
        except GitHubAuthError as e:
            # Use authentication-specific error handling
            error_message = f"❌ Authentication Error: {e}"
            typer.echo(error_message, err=True)
            raise typer.Exit(code=ExitCodes.AUTHENTICATION_ERROR)
        except Exception as e:
            # Fallback for other authentication-related errors
            error_message = f"❌ Authentication check failed: {e}"
            typer.echo(error_message, err=True)
            raise typer.Exit(code=ExitCodes.AUTHENTICATION_ERROR)


def verify_gh_cli_setup() -> None:
    """Verify that GitHub CLI is properly set up and accessible."""
    try:
        ensure_gh_cli_availability()
    except Exception:
        raise typer.Exit(code=ExitCodes.CONFIGURATION_ERROR)

    # Check authentication status
    gh_wrapper = create_gh_wrapper()
    auth_service = AuthenticationService(gh_wrapper)

    try:
        status = auth_service.check_authentication_status()

        if status["authenticated"]:
            if status["username"]:
                typer.echo(f"✅ Authenticated as: {status['username']}")
            else:
                typer.echo("✅ Authenticated with GitHub CLI")
        else:
            typer.echo("⚠️  Not authenticated with GitHub CLI", err=True)
            typer.echo("   Run 'gh auth login' to authenticate", err=True)

    except Exception as e:
        typer.echo(f"❌ Failed to check authentication status: {e}", err=True)
        raise typer.Exit(code=ExitCodes.AUTHENTICATION_ERROR)


class ExitCodes:
    """Standard exit codes for GitHub CLI operations."""

    SUCCESS = 0
    VALIDATION_ERROR = 1
    AUTHENTICATION_ERROR = 2
    PERMISSION_ERROR = 3
    NETWORK_ERROR = 4
    FILE_NOT_FOUND = 5
    CONFIGURATION_ERROR = 6
    API_ERROR = 7
    UNKNOWN_ERROR = 100


def handle_operation_error(error: Exception, operation: str) -> None:
    """Handle operation errors and format them for display with appropriate exit codes."""
    error_message = f"❌ {operation} failed: {error}"
    typer.echo(error_message, err=True)

    # Determine exit code based on error type
    exit_code = ExitCodes.UNKNOWN_ERROR

    if isinstance(error, ValidationError):
        exit_code = ExitCodes.VALIDATION_ERROR
    elif isinstance(error, GitHubAuthError):
        exit_code = ExitCodes.AUTHENTICATION_ERROR
    elif "permission" in str(error).lower() or "forbidden" in str(error).lower():
        exit_code = ExitCodes.PERMISSION_ERROR
    elif "network" in str(error).lower() or "connection" in str(error).lower():
        exit_code = ExitCodes.NETWORK_ERROR
    elif "not found" in str(error).lower():
        exit_code = ExitCodes.FILE_NOT_FOUND
    elif "configuration" in str(error).lower():
        exit_code = ExitCodes.CONFIGURATION_ERROR
    elif "api" in str(error).lower():
        exit_code = ExitCodes.API_ERROR

    raise typer.Exit(code=exit_code)


def handle_validation_result_with_exit_codes(
    validation_result: ValidationResult, dry_run: bool = False
) -> None:
    """Handle validation result with appropriate exit codes."""
    if not validation_result.is_valid:
        formatted_error = ErrorFormatter.format_validation_result(validation_result)
        typer.echo(formatted_error, err=True)

        # Use validation error exit code
        raise typer.Exit(code=ExitCodes.VALIDATION_ERROR)

    if validation_result.warnings and not dry_run:
        formatted_warnings = ErrorFormatter.format_validation_result(validation_result)
        typer.echo(formatted_warnings, err=True)


def ensure_gh_cli_availability() -> None:
    """Ensure GitHub CLI is available on the system."""
    import shutil

    if not shutil.which("gh"):
        error_msg = (
            "❌ GitHub CLI 'gh' is not installed or not in PATH\n"
            "   Please install it from: https://cli.github.com/\n"
            "   On macOS: brew install gh\n"
            "   On Ubuntu: sudo apt install gh\n"
            "   On Windows: winget install --id GitHub.cli"
        )
        typer.echo(error_msg, err=True)
        raise typer.Exit(code=ExitCodes.CONFIGURATION_ERROR)


def format_success_output(content: str, repository: str, file_path: str) -> str:
    """Format successful file read output."""
    lines = len(content.splitlines())
    size = len(content.encode("utf-8"))
    return (
        f"✅ Successfully read file: {file_path}\n"
        f"   Repository: {repository}\n"
        f"   Size: {size} bytes, {lines} lines\n\n"
        f"{content}"
    )


def format_directory_listing(
    files: List[Dict[str, Any]], repository: str, directory: str
) -> str:
    """Format directory listing output."""
    if not files:
        return f"📁 Directory is empty: {directory} in {repository}"

    output = f"📁 Directory listing: {directory} in {repository}\n"
    output += f"   Found {len(files)} items:\n"

    for file_info in files:
        file_type = "📁" if file_info.get("type") == "dir" else "📄"
        size = file_info.get("size", "unknown")
        output += f"   {file_type} {file_info.get('name', 'unknown')} ({size} bytes)\n"

    return output
