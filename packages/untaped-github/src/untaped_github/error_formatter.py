"""Error formatting utilities for GitHub operations."""

from __future__ import annotations

from typing import Any, Dict, List

from .gh_cli_wrapper import GitHubCliError
from .models.validation import ValidationError, ValidationResult


class ErrorFormatter:
    """Utility class for formatting various types of errors."""

    @staticmethod
    def format_validation_result(result: ValidationResult) -> str:
        """Format a validation result for display."""
        if result.is_valid:
            if result.warnings:
                return "✅ Configuration is valid\n\n⚠️  Warnings:\n" + "\n".join(
                    f"  - {w}" for w in result.warnings
                )
            return "✅ Configuration is valid"

        error_lines = []
        for error in result.errors:
            error_lines.append(f"  - {error}")

        output = "❌ Configuration validation failed:\n" + "\n".join(error_lines)

        if result.warnings:
            output += "\n\n⚠️  Warnings:\n" + "\n".join(f"  - {w}" for w in result.warnings)

        return output

    @staticmethod
    def format_gh_cli_error(error: GitHubCliError) -> str:
        """Format a GitHub CLI error for display."""
        output = f"❌ GitHub CLI Error: {error}"

        if error.return_code is not None:
            output += f"\n   Exit code: {error.return_code}"

        if error.stderr:
            output += f"\n   Details: {error.stderr.strip()}"

        return output

    @staticmethod
    def format_yaml_error(error: Exception, file_path: str = None) -> str:
        """Format a YAML parsing error for display."""
        output = "❌ YAML Error"

        if file_path:
            output += f" in {file_path}"

        output += f": {error}"
        return output

    @staticmethod
    def format_file_not_found(repository: str, file_path: str) -> str:
        """Format a file not found error."""
        return f"❌ File not found: {file_path} in repository {repository}"

    @staticmethod
    def format_authentication_error() -> str:
        """Format an authentication error."""
        return "❌ Authentication Error: Not logged in to GitHub CLI\n   Please run: gh auth login"

    @staticmethod
    def format_permission_error(repository: str) -> str:
        """Format a permission error."""
        return (
            f"❌ Permission Error: Cannot access repository {repository}\n"
            "   Please check that:\n"
            "   - The repository exists\n"
            "   - You have access to the repository\n"
            "   - Your GitHub token has the required permissions"
        )

    @staticmethod
    def format_rate_limit_error(retry_after: int = None) -> str:
        """Format a rate limit error."""
        output = "❌ Rate Limit Error: GitHub API rate limit exceeded"

        if retry_after:
            output += f"\n   Please wait {retry_after} seconds before retrying"
        else:
            output += "\n   Please wait before making more requests"

        return output

    @staticmethod
    def format_network_error(host: str = "api.github.com") -> str:
        """Format a network error."""
        return (
            f"❌ Network Error: Cannot connect to {host}\n"
            "   Please check:\n"
            "   - Your internet connection\n"
            "   - That you can reach api.github.com\n"
            "   - Any firewall or proxy settings"
        )

    @staticmethod
    def format_success_with_content(content: str, repository: str, file_path: str) -> str:
        """Format a successful file read operation."""
        lines = len(content.splitlines())
        size = len(content.encode("utf-8"))
        return (
            f"✅ Successfully read file: {file_path}\n"
            f"   Repository: {repository}\n"
            f"   Size: {size} bytes, {lines} lines"
        )

    @staticmethod
    def format_success_with_listing(
        files: List[Dict[str, Any]], repository: str, directory: str
    ) -> str:
        """Format a successful directory listing operation."""
        if not files:
            return f"📁 Directory is empty: {directory} in {repository}"

        output = f"📁 Directory listing: {directory} in {repository}\n"
        output += f"   Found {len(files)} items:\n"

        for file_info in files:
            file_type = "📁" if file_info.get("type") == "dir" else "📄"
            size = file_info.get("size", "unknown")
            output += f"   {file_type} {file_info.get('name', 'unknown')} ({size} bytes)\n"

        return output
