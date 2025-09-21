"""Configuration validator for GitHub file operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..gh_cli_wrapper import GitHubCliWrapper
from ..models.file_operation import FileOperation
from ..models.file_path import FilePath
from ..models.repository import Repository
from ..models.validation import ValidationError, ValidationResult


class ConfigurationValidator:
    """Validator for GitHub file operation configurations."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        self.gh_wrapper = gh_wrapper

    def validate_file_operation(self, config_data: Dict[str, Any]) -> ValidationResult:
        """Validate a file operation configuration."""
        result = ValidationResult.success()

        # Validate required fields
        required_fields = ["repository", "file_path"]
        for field in required_fields:
            if field not in config_data:
                result.add_error(field, f"Missing required field: {field}")
            elif not config_data[field]:
                result.add_error(field, f"Field cannot be empty: {field}")

        if result.has_errors():
            return result

        # Validate repository format
        try:
            repository = Repository(
                owner=config_data["repository"].split("/")[0],
                name=config_data["repository"].split("/")[1],
            )
        except (ValueError, IndexError) as e:
            result.add_error("repository", f"Invalid repository format: {e}")

        # Validate file path
        try:
            FilePath(path=config_data["file_path"])
        except ValueError as e:
            result.add_error("file_path", f"Invalid file path: {e}")

        # Validate optional ref field
        if "ref" in config_data and config_data["ref"]:
            if not isinstance(config_data["ref"], str) or not config_data["ref"].strip():
                result.add_error("ref", "ref must be a non-empty string if provided")

        return result

    def validate_gh_authentication(self) -> ValidationResult:
        """Validate GitHub CLI authentication."""
        result = ValidationResult.success()

        try:
            if not self.gh_wrapper.check_authentication():
                result.add_error("authentication", "Not authenticated with GitHub CLI")
                result.add_warning("Run 'gh auth login' to authenticate")
        except Exception as e:
            result.add_error("authentication", f"Failed to check authentication: {e}")

        return result

    def validate_repository_access(self, repository: str) -> ValidationResult:
        """Validate access to a specific repository."""
        result = ValidationResult.success()

        try:
            # Try to access the repository
            self.gh_wrapper.api_get(f"repos/{repository}")
        except Exception as e:
            result.add_error("repository", f"Cannot access repository: {e}")
            result.add_warning("Check that the repository exists and you have access to it")

        return result

    def validate_file_exists(
        self, repository: str, file_path: str, ref: str = "main"
    ) -> ValidationResult:
        """Validate that a file exists in the repository."""
        result = ValidationResult.success()

        try:
            # Try to get file content
            content = self.gh_wrapper.api_get_raw(f"repos/{repository}/contents/{file_path}")
            if not content:
                result.add_error("file", f"File is empty or cannot be read: {file_path}")
        except Exception as e:
            result.add_error("file", f"File does not exist or cannot be accessed: {e}")
            result.add_warning(f"Check that the file exists at {file_path} in {repository}")

        return result

    def comprehensive_validation(self, config_data: Dict[str, Any]) -> ValidationResult:
        """Perform comprehensive validation of the configuration."""
        result = ValidationResult.success()

        # Step 1: Basic configuration validation
        config_validation = self.validate_file_operation(config_data)
        result.errors.extend(config_validation.errors)
        result.warnings.extend(config_validation.warnings)
        result.is_valid = result.is_valid and config_validation.is_valid

        if result.has_errors():
            return result

        # Step 2: Authentication validation
        auth_validation = self.validate_gh_authentication()
        result.errors.extend(auth_validation.errors)
        result.warnings.extend(auth_validation.warnings)
        result.is_valid = result.is_valid and auth_validation.is_valid

        if result.has_errors():
            return result

        # Step 3: Repository access validation
        repository = config_data["repository"]
        repo_validation = self.validate_repository_access(repository)
        result.errors.extend(repo_validation.errors)
        result.warnings.extend(repo_validation.warnings)
        result.is_valid = result.is_valid and repo_validation.is_valid

        if result.has_errors():
            return result

        # Step 4: File existence validation
        file_path = config_data["file_path"]
        file_validation = self.validate_file_exists(
            repository, file_path, config_data.get("ref", "main")
        )
        result.errors.extend(file_validation.errors)
        result.warnings.extend(file_validation.warnings)
        result.is_valid = result.is_valid and file_validation.is_valid

        return result
