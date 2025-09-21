"""File operation service for GitHub repositories."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..api.file_operations import GitHubFileOperationsApi
from ..error_formatter import ErrorFormatter
from ..gh_cli_wrapper import GitHubCliWrapper
from ..models.file_operation import FileOperation
from ..models.file_path import FilePath
from ..models.repository import Repository


class FileOperationService:
    """Service for executing file operations on GitHub repositories."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        self.gh_wrapper = gh_wrapper
        self.api = GitHubFileOperationsApi(gh_wrapper)

    def read_file(self, file_operation: FileOperation) -> str:
        """Read a file from a GitHub repository."""
        try:
            content = self.api.read_file(
                repository=file_operation.repository,
                file_path=file_operation.file_path,
                ref=file_operation.ref,
            )

            return content

        except Exception as e:
            formatted_error = ErrorFormatter.format_file_not_found(
                file_operation.repository, file_operation.file_path
            )
            raise Exception(f"{formatted_error}: {e}")

    def list_directory(
        self, repository: str, directory_path: str = "", recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """List files in a GitHub repository directory."""
        try:
            contents = self.api.list_directory(repository, directory_path, recursive)
            return contents

        except Exception as e:
            formatted_error = ErrorFormatter.format_file_not_found(repository, directory_path)
            raise Exception(f"{formatted_error}: {e}")

    def validate_file_operation(self, file_operation: FileOperation) -> bool:
        """Validate that a file operation can be executed successfully.

        This method checks authentication, repository access, and file existence.
        Returns False if any check fails, including if the file doesn't exist.

        Note: This method was fixed to properly check the return value of
        check_file_exists() instead of ignoring it. Previously, it would return
        True even when the target file didn't exist, causing dry runs to succeed
        while actual reads would fail.
        """
        try:
            # Check authentication
            self.api.require_authentication()

            # Check repository access
            self.api.validate_repository_access(file_operation.repository)

            # Check file exists - return False if file doesn't exist
            file_exists = self.api.check_file_exists(
                file_operation.repository, file_operation.file_path, file_operation.ref
            )

            return file_exists

        except Exception:
            return False

    def dry_run_file_read(self, file_operation: FileOperation) -> Dict[str, Any]:
        """Perform a dry run of a file read operation."""
        dry_run_result = {
            "operation": "file_read",
            "repository": file_operation.repository,
            "file_path": file_operation.file_path,
            "ref": file_operation.ref,
            "authentication_valid": False,
            "repository_accessible": False,
            "file_exists": False,
            "can_proceed": False,
        }

        try:
            # Check authentication
            self.api.require_authentication()
            dry_run_result["authentication_valid"] = True

            # Check repository access
            self.api.validate_repository_access(file_operation.repository)
            dry_run_result["repository_accessible"] = True

            # Check file exists
            file_exists = self.api.check_file_exists(
                file_operation.repository, file_operation.file_path, file_operation.ref
            )
            dry_run_result["file_exists"] = file_exists

            # Set final status
            dry_run_result["can_proceed"] = (
                dry_run_result["authentication_valid"]
                and dry_run_result["repository_accessible"]
                and dry_run_result["file_exists"]
            )

        except Exception as e:
            dry_run_result["error"] = str(e)

        return dry_run_result

    def dry_run_directory_list(self, repository: str, directory_path: str = "") -> Dict[str, Any]:
        """Perform a dry run of a directory listing operation."""
        dry_run_result = {
            "operation": "directory_list",
            "repository": repository,
            "directory_path": directory_path,
            "authentication_valid": False,
            "repository_accessible": False,
            "can_proceed": False,
        }

        try:
            # Check authentication
            self.api.require_authentication()
            dry_run_result["authentication_valid"] = True

            # Check repository access
            self.api.validate_repository_access(repository)
            dry_run_result["repository_accessible"] = True

            # Set final status
            dry_run_result["can_proceed"] = (
                dry_run_result["authentication_valid"] and dry_run_result["repository_accessible"]
            )

        except Exception as e:
            dry_run_result["error"] = str(e)

        return dry_run_result
