"""GitHub file operations API wrapper."""

from __future__ import annotations

from typing import Any, Dict, List

from ..gh_cli_wrapper import GitHubCliWrapper
from .base import GitHubApiError, GitHubBaseApi


class GitHubFileOperationsApi(GitHubBaseApi):
    """API wrapper for GitHub file operations."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        super().__init__(gh_wrapper)

    def read_file(self, repository: str, file_path: str, ref: str = "main") -> str:
        """Read a file from a GitHub repository."""
        self.require_authentication()
        self.validate_repository_access(repository)

        endpoint = f"repos/{repository}/contents/{file_path}"
        if ref != "main":
            endpoint += f"?ref={ref}"

        try:
            return self.gh_wrapper.api_get_raw(endpoint)
        except Exception as e:
            raise GitHubApiError(f"Failed to read file {file_path}: {e}")

    def list_directory(
        self, repository: str, directory_path: str = "", recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """List files in a GitHub repository directory."""
        self.require_authentication()
        self.validate_repository_access(repository)

        endpoint = f"repos/{repository}/contents"
        if directory_path:
            endpoint += f"/{directory_path}"

        try:
            contents = self.gh_wrapper.api_get(endpoint)

            # Handle both single file and directory listing
            if isinstance(contents, dict):
                # Single file response
                return [contents]
            elif isinstance(contents, list):
                # Directory listing response
                if recursive:
                    return self._get_recursive_contents(repository, contents, directory_path)
                return contents
            else:
                raise GitHubApiError(
                    f"Unexpected response format from GitHub API: {type(contents)}"
                )

        except Exception as e:
            raise GitHubApiError(f"Failed to list directory {directory_path}: {e}")

    def _get_recursive_contents(
        self, repository: str, items: List[Dict[str, Any]], base_path: str = ""
    ) -> List[Dict[str, Any]]:
        """Recursively get all contents including subdirectories."""
        all_contents = []

        for item in items:
            all_contents.append(item)

            # If it's a directory, recursively get its contents
            if item.get("type") == "dir":
                sub_path = f"{base_path}/{item['name']}" if base_path else item["name"]
                try:
                    sub_contents = self.list_directory(repository, sub_path, recursive=True)
                    all_contents.extend(sub_contents)
                except GitHubApiError:
                    # Skip directories we can't access
                    continue

        return all_contents

    def get_file_info(self, repository: str, file_path: str, ref: str = "main") -> Dict[str, Any]:
        """Get file metadata from a GitHub repository."""
        self.require_authentication()
        self.validate_repository_access(repository)

        endpoint = f"repos/{repository}/contents/{file_path}"
        if ref != "main":
            endpoint += f"?ref={ref}"

        try:
            return self.gh_wrapper.api_get(endpoint)
        except Exception as e:
            raise GitHubApiError(f"Failed to get file info for {file_path}: {e}")

    def check_file_exists(self, repository: str, file_path: str, ref: str = "main") -> bool:
        """Check if a file exists in a GitHub repository."""
        try:
            self.get_file_info(repository, file_path, ref)
            return True
        except GitHubApiError:
            return False
