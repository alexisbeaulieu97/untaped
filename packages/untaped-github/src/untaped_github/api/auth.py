"""GitHub authentication handling."""

from __future__ import annotations

from typing import Optional

from ..gh_cli_wrapper import GitHubCliError, GitHubCliWrapper


class GitHubAuthError(Exception):
    """Exception raised for GitHub authentication errors."""

    def __init__(self, message: str, help_text: str = None) -> None:
        super().__init__(message)
        self.help_text = help_text or "Run 'gh auth login' to authenticate"


class GitHubAuthentication:
    """Handles GitHub CLI authentication."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        self.gh_wrapper = gh_wrapper

    def check_authentication(self) -> bool:
        """Check if user is authenticated with GitHub CLI."""
        try:
            return self.gh_wrapper.check_authentication()
        except GitHubCliError as e:
            raise GitHubAuthError(f"Authentication check failed: {e}")

    def require_authentication(self) -> None:
        """Require authentication, raising an error if not authenticated."""
        if not self.check_authentication():
            raise GitHubAuthError(
                "Not authenticated with GitHub CLI",
                "Please run 'gh auth login' to authenticate with GitHub",
            )

    def get_current_user(self) -> Optional[str]:
        """Get the currently authenticated GitHub username."""
        try:
            return self.gh_wrapper.get_current_user()
        except GitHubCliError:
            return None

    def validate_repository_access(self, repository: str) -> bool:
        """Validate that the user has access to the specified repository."""
        try:
            self.gh_wrapper.api_get(f"repos/{repository}")
            return True
        except GitHubCliError:
            return False

    def ensure_repository_access(self, repository: str) -> None:
        """Ensure the user has access to the repository, raising an error if not."""
        if not self.validate_repository_access(repository):
            raise GitHubAuthError(
                f"Cannot access repository: {repository}",
                "Please check that the repository exists and you have access to it",
            )
