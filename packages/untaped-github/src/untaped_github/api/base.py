"""Base API wrapper for GitHub CLI operations."""

from __future__ import annotations

from typing import Any, Dict, List

from ..error_formatter import ErrorFormatter
from ..gh_cli_wrapper import GitHubCliError, GitHubCliWrapper
from .auth import GitHubAuthentication, GitHubAuthError


class GitHubApiError(Exception):
    """Exception raised for GitHub API errors."""

    def __init__(
        self, message: str, status_code: int = None, response_data: Dict[str, Any] = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class GitHubBaseApi:
    """Base API wrapper for GitHub CLI operations with error handling."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        self.gh_wrapper = gh_wrapper
        self.auth = GitHubAuthentication(gh_wrapper)

    def _handle_gh_error(self, error: GitHubCliError) -> None:
        """Handle GitHub CLI errors and convert to appropriate exceptions."""
        error_msg = error.stderr or str(error)

        # Authentication errors
        if "not logged in" in error_msg.lower() or "authentication" in error_msg.lower():
            raise GitHubAuthError(f"Authentication required: {error_msg}")

        # Rate limiting
        if "rate limit" in error_msg.lower() or "too many requests" in error_msg.lower():
            raise GitHubApiError(f"Rate limit exceeded: {error_msg}")

        # Permission errors
        if "forbidden" in error_msg.lower() or "permission" in error_msg.lower():
            raise GitHubApiError(f"Permission denied: {error_msg}")

        # File not found
        if "not found" in error_msg.lower():
            raise GitHubApiError(f"Resource not found: {error_msg}")

        # Network errors
        if "connection" in error_msg.lower() or "timeout" in error_msg.lower():
            raise GitHubApiError(f"Network error: {error_msg}")

        # Generic API error
        raise GitHubApiError(f"GitHub API error: {error_msg}")

    def safe_api_call(self, api_method, *args, **kwargs) -> Any:
        """Safely execute an API call with error handling."""
        try:
            return api_method(*args, **kwargs)
        except GitHubCliError as e:
            self._handle_gh_error(e)

    def require_authentication(self) -> None:
        """Require authentication for API calls."""
        try:
            self.auth.require_authentication()
        except GitHubAuthError as e:
            formatted_error = ErrorFormatter.format_authentication_error()
            raise GitHubAuthError(formatted_error) from e

    def validate_repository_access(self, repository: str) -> None:
        """Validate repository access for API calls."""
        try:
            self.auth.ensure_repository_access(repository)
        except GitHubAuthError as e:
            formatted_error = ErrorFormatter.format_permission_error(repository)
            raise GitHubAuthError(formatted_error) from e
