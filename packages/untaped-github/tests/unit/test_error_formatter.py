"""Unit tests for error formatting utilities."""
import pytest

from untaped_github.error_formatter import ErrorFormatter
from untaped_github.models.validation import ValidationResult, ValidationError
from untaped_github.gh_cli_wrapper import GitHubCliError


class TestErrorFormatter:
    """Test ErrorFormatter functionality."""

    def test_format_validation_result_success(self):
        """Test formatting successful validation result."""
        result = ValidationResult.success()
        formatted = ErrorFormatter.format_validation_result(result)

        assert formatted == "✅ Configuration is valid"

    def test_format_validation_result_success_with_warnings(self):
        """Test formatting successful validation result with warnings."""
        result = ValidationResult.success()
        result.add_warning("Test warning")
        formatted = ErrorFormatter.format_validation_result(result)

        assert formatted == "✅ Configuration is valid\n\n⚠️  Warnings:\n  - Test warning"

    def test_format_validation_result_failure(self):
        """Test formatting failed validation result."""
        error = ValidationError(field="test_field", message="Test error", error_type="test")
        result = ValidationResult.failure([error])
        formatted = ErrorFormatter.format_validation_result(result)

        assert "❌ Configuration validation failed:" in formatted
        assert "test_field: Test error" in formatted

    def test_format_validation_result_failure_with_warnings(self):
        """Test formatting failed validation result with warnings."""
        error = ValidationError(field="test_field", message="Test error", error_type="test")
        result = ValidationResult.failure([error])
        result.add_warning("Test warning")
        formatted = ErrorFormatter.format_validation_result(result)

        assert "❌ Configuration validation failed:" in formatted
        assert "test_field: Test error" in formatted
        assert "⚠️  Warnings:" in formatted
        assert "Test warning" in formatted

    def test_format_gh_cli_error(self):
        """Test formatting GitHub CLI error."""
        error = GitHubCliError("Command failed", return_code=1, stderr="Details")
        formatted = ErrorFormatter.format_gh_cli_error(error)

        assert "❌ GitHub CLI Error: Command failed" in formatted
        assert "Exit code: 1" in formatted
        assert "Details" in formatted

    def test_format_yaml_error(self):
        """Test formatting YAML error."""
        yaml_error = ValueError("Invalid YAML syntax")
        formatted = ErrorFormatter.format_yaml_error(yaml_error, "config.yaml")

        assert "❌ YAML Error in config.yaml: Invalid YAML syntax" in formatted

    def test_format_yaml_error_without_file(self):
        """Test formatting YAML error without file path."""
        yaml_error = ValueError("Invalid YAML syntax")
        formatted = ErrorFormatter.format_yaml_error(yaml_error)

        assert "❌ YAML Error: Invalid YAML syntax" in formatted

    def test_format_file_not_found(self):
        """Test formatting file not found error."""
        formatted = ErrorFormatter.format_file_not_found("owner/repo", "README.md")

        assert "❌ File not found: README.md in repository owner/repo" in formatted

    def test_format_authentication_error(self):
        """Test formatting authentication error."""
        formatted = ErrorFormatter.format_authentication_error()

        assert "❌ Authentication Error: Not logged in to GitHub CLI" in formatted
        assert "gh auth login" in formatted

    def test_format_permission_error(self):
        """Test formatting permission error."""
        formatted = ErrorFormatter.format_permission_error("owner/repo")

        assert "❌ Permission Error: Cannot access repository owner/repo" in formatted
        assert "repository exists" in formatted
        assert "have access" in formatted

    def test_format_rate_limit_error(self):
        """Test formatting rate limit error."""
        formatted = ErrorFormatter.format_rate_limit_error()

        assert "❌ Rate Limit Error: GitHub API rate limit exceeded" in formatted
        assert "Please wait" in formatted

    def test_format_rate_limit_error_with_retry_after(self):
        """Test formatting rate limit error with retry time."""
        formatted = ErrorFormatter.format_rate_limit_error(retry_after=60)

        assert "❌ Rate Limit Error: GitHub API rate limit exceeded" in formatted
        assert "Please wait 60 seconds" in formatted

    def test_format_network_error(self):
        """Test formatting network error."""
        formatted = ErrorFormatter.format_network_error("api.github.com")

        assert "❌ Network Error: Cannot connect to api.github.com" in formatted
        assert "internet connection" in formatted
        assert "api.github.com" in formatted

    def test_format_success_with_content(self):
        """Test formatting successful file read."""
        content = "Hello World\nThis is a test file."
        formatted = ErrorFormatter.format_success_with_content(content, "owner/repo", "README.md")

        assert "✅ Successfully read file: README.md" in formatted
        assert "Repository: owner/repo" in formatted
        assert "Size:" in formatted
        assert "lines" in formatted
        assert "Hello World" in formatted

    def test_format_success_with_listing_empty(self):
        """Test formatting successful directory listing when empty."""
        formatted = ErrorFormatter.format_success_with_listing([], "owner/repo", "empty-dir")

        assert "📁 Directory is empty: empty-dir in owner/repo" in formatted

    def test_format_success_with_listing_with_files(self):
        """Test formatting successful directory listing with files."""
        files = [
            {"name": "README.md", "type": "file", "size": 1024},
            {"name": "docs", "type": "dir", "size": 0},
            {"name": "script.py", "type": "file", "size": 2048}
        ]
        formatted = ErrorFormatter.format_success_with_listing(files, "owner/repo", "src")

        assert "📁 Directory listing: src in owner/repo" in formatted
        assert "Found 3 items:" in formatted
        assert "📄 README.md" in formatted
        assert "📁 docs" in formatted
        assert "📄 script.py" in formatted
        assert "Total:" in formatted
