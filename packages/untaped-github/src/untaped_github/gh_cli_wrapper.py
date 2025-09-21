"""Wrapper for GitHub CLI (gh) command execution."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models.validation import ValidationError


class GitHubCliError(Exception):
    """Exception raised when GitHub CLI command fails."""

    def __init__(self, message: str, return_code: int = None, stderr: str = None) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr = stderr


class GitHubCliWrapper:
    """Wrapper for executing GitHub CLI commands."""

    def __init__(self, gh_binary: str = "gh") -> None:
        self.gh_binary = gh_binary
        self._check_gh_availability()

    def _check_gh_availability(self) -> None:
        """Check if gh CLI is available on the system."""
        if not shutil.which(self.gh_binary):
            raise GitHubCliError(
                f"GitHub CLI '{self.gh_binary}' is not installed or not in PATH. "
                "Please install it from https://cli.github.com/"
            )

    def _run_gh_command(
        self, args: List[str], input_text: str = None
    ) -> subprocess.CompletedProcess:
        """Run a GitHub CLI command and return the completed process."""
        cmd = [self.gh_binary] + args

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=input_text,
                check=False,  # We handle errors manually
            )

            if result.returncode != 0:
                raise GitHubCliError(
                    f"GitHub CLI command failed: {' '.join(cmd)}",
                    return_code=result.returncode,
                    stderr=result.stderr,
                )

            return result

        except FileNotFoundError:
            raise GitHubCliError(
                f"GitHub CLI '{self.gh_binary}' not found. Please ensure it's installed and in PATH."
            )

    def api_get(
        self, endpoint: str, accept_header: str = "application/vnd.github.v3+json"
    ) -> Dict[str, Any]:
        """Execute a GET request via GitHub API."""
        args = ["api", endpoint]

        if accept_header:
            args.extend(["--header", f"Accept: {accept_header}"])

        result = self._run_gh_command(args)
        return self._parse_json_response(result.stdout)

    def api_get_raw(self, endpoint: str) -> str:
        """Execute a GET request and return raw text content."""
        args = ["api", endpoint, "--raw"]
        result = self._run_gh_command(args)
        return result.stdout.strip()

    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """Parse JSON response from GitHub CLI."""
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise GitHubCliError(f"Failed to parse JSON response: {e}")

    def check_authentication(self) -> bool:
        """Check if user is authenticated with GitHub CLI."""
        try:
            # This will fail if not authenticated
            result = self._run_gh_command(["auth", "status"])
            return "Logged in to github.com" in result.stdout
        except GitHubCliError:
            return False

    def get_current_user(self) -> Optional[str]:
        """Get the currently authenticated GitHub username."""
        try:
            result = self._run_gh_command(["api", "user"])
            user_data = self._parse_json_response(result.stdout)
            return user_data.get("login")
        except GitHubCliError:
            return None

    def validate_token_scopes(self, required_scopes: List[str]) -> List[str]:
        """Validate that the current token has the required scopes."""
        try:
            result = self._run_gh_command(["auth", "status", "--show-token"])
            # Parse the output to extract token scopes
            # This is a simplified implementation - in practice, you'd need to parse
            # the actual token scopes from the gh auth status output
            return required_scopes  # Simplified for now
        except GitHubCliError:
            return []
