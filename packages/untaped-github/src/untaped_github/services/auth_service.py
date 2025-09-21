"""Authentication validation service for GitHub operations."""

from __future__ import annotations

from typing import Optional

from ..api.auth import GitHubAuthentication, GitHubAuthError
from ..error_formatter import ErrorFormatter
from ..gh_cli_wrapper import GitHubCliWrapper


class AuthenticationService:
    """Service for validating GitHub authentication."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        self.gh_wrapper = gh_wrapper
        self.auth = GitHubAuthentication(gh_wrapper)

    def check_authentication_status(self) -> Dict[str, Any]:
        """Check the current authentication status."""
        result = {"authenticated": False, "username": None, "error": None}

        try:
            authenticated = self.auth.check_authentication()
            result["authenticated"] = authenticated

            if authenticated:
                username = self.auth.get_current_user()
                result["username"] = username

        except GitHubAuthError as e:
            result["error"] = str(e)
        except Exception as e:
            result["error"] = f"Authentication check failed: {e}"

        return result

    def require_authentication(self) -> None:
        """Require authentication, raising an error if not authenticated."""
        try:
            self.auth.require_authentication()
        except GitHubAuthError as e:
            formatted_error = ErrorFormatter.format_authentication_error()
            raise GitHubAuthError(formatted_error) from e

    def validate_repository_access(self, repository: str) -> Dict[str, Any]:
        """Validate access to a specific repository."""
        result = {"repository": repository, "accessible": False, "error": None}

        try:
            self.auth.ensure_repository_access(repository)
            result["accessible"] = True

        except GitHubAuthError as e:
            result["error"] = str(e)
        except Exception as e:
            result["error"] = f"Repository access check failed: {e}"

        return result

    def get_auth_help_text(self) -> str:
        """Get help text for authentication setup."""
        return (
            "To authenticate with GitHub:\n"
            "1. Install the GitHub CLI: https://cli.github.com/\n"
            "2. Run: gh auth login\n"
            "3. Follow the prompts to authenticate with your GitHub account\n"
            "4. Verify authentication: gh auth status"
        )

    def format_auth_error_message(self, operation: str = "GitHub operation") -> str:
        """Format an authentication error message for a specific operation."""
        return f"❌ {operation} failed due to authentication error.\n   {self.get_auth_help_text()}"

    def check_gh_cli_availability(self) -> Dict[str, Any]:
        """Check if GitHub CLI is available on the system."""
        result = {"available": False, "version": None, "error": None}

        try:
            import shutil

            if shutil.which("gh"):
                result["available"] = True

                # Try to get version
                version_result = self.gh_wrapper._run_gh_command(["--version"])
                if version_result.stdout:
                    lines = version_result.stdout.strip().split("\n")
                    if lines:
                        result["version"] = (
                            lines[0].split()[-1] if len(lines[0].split()) > 1 else lines[0]
                        )
            else:
                result["error"] = "GitHub CLI 'gh' command not found in PATH"

        except Exception as e:
            result["error"] = f"Failed to check GitHub CLI availability: {e}"

        return result
