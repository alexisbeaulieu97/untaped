"""Unit tests for configuration validator."""
import pytest
from unittest.mock import Mock, patch

from untaped_github.models.validation import ValidationResult, ValidationError
from untaped_github.validators.config_validator import ConfigurationValidator
from untaped_github.gh_cli_wrapper import GitHubCliWrapper


class TestConfigurationValidator:
    """Test ConfigurationValidator functionality."""

    def test_init(self):
        """Test validator initialization."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)
        assert validator.gh_wrapper == mock_gh

    def test_validate_file_operation_valid(self):
        """Test validation of valid file operation configuration."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md",
            "ref": "main"
        }

        result = validator.validate_file_operation(config)

        assert result.is_valid
        assert not result.errors
        assert not result.warnings

    def test_validate_file_operation_missing_repository(self):
        """Test validation fails when repository is missing."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "file_path": "README.md"
        }

        result = validator.validate_file_operation(config)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "repository"
        assert "Missing required field" in result.errors[0].message

    def test_validate_file_operation_missing_file_path(self):
        """Test validation fails when file_path is missing."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "octocat/Hello-World"
        }

        result = validator.validate_file_operation(config)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "file_path"
        assert "Missing required field" in result.errors[0].message

    def test_validate_file_operation_empty_fields(self):
        """Test validation fails when required fields are empty."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "",
            "file_path": ""
        }

        result = validator.validate_file_operation(config)

        assert not result.is_valid
        assert len(result.errors) == 2
        assert any(error.field == "repository" and "empty" in error.message for error in result.errors)
        assert any(error.field == "file_path" and "empty" in error.message for error in result.errors)

    def test_validate_file_operation_invalid_repository_format(self):
        """Test validation fails for invalid repository format."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "invalid-repo-format",
            "file_path": "README.md"
        }

        result = validator.validate_file_operation(config)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "repository"
        assert "Invalid repository format" in result.errors[0].message

    def test_validate_file_operation_invalid_file_path(self):
        """Test validation fails for invalid file path."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "octocat/Hello-World",
            "file_path": "../test.txt"  # Directory traversal
        }

        result = validator.validate_file_operation(config)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "file_path"
        assert "Invalid file path" in result.errors[0].message

    def test_validate_file_operation_invalid_ref(self):
        """Test validation fails for invalid ref."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md",
            "ref": ""  # Empty ref
        }

        result = validator.validate_file_operation(config)

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "ref"
        assert "non-empty string" in result.errors[0].message

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.check_authentication')
    def test_validate_gh_authentication_success(self, mock_check_auth):
        """Test GitHub authentication validation success."""
        mock_check_auth.return_value = True
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.check_authentication = mock_check_auth
        validator = ConfigurationValidator(mock_gh)

        result = validator.validate_gh_authentication()

        assert result.is_valid
        assert not result.errors
        mock_check_auth.assert_called_once()

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.check_authentication')
    def test_validate_gh_authentication_failure(self, mock_check_auth):
        """Test GitHub authentication validation failure."""
        mock_check_auth.return_value = False
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.check_authentication = mock_check_auth
        validator = ConfigurationValidator(mock_gh)

        result = validator.validate_gh_authentication()

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "authentication"
        assert "Not authenticated" in result.errors[0].message

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.api_get')
    def test_validate_repository_access_success(self, mock_api_get):
        """Test repository access validation success."""
        mock_api_get.return_value = {"id": 123}
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.api_get = mock_api_get
        validator = ConfigurationValidator(mock_gh)

        result = validator.validate_repository_access("octocat/Hello-World")

        assert result.is_valid
        assert not result.errors
        mock_api_get.assert_called_once_with("repos/octocat/Hello-World")

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.api_get')
    def test_validate_repository_access_failure(self, mock_api_get):
        """Test repository access validation failure."""
        mock_api_get.side_effect = Exception("Repository not found")
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.api_get = mock_api_get
        validator = ConfigurationValidator(mock_gh)

        result = validator.validate_repository_access("octocat/nonexistent")

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "repository"
        assert "Cannot access repository" in result.errors[0].message

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.api_get_raw')
    def test_validate_file_exists_success(self, mock_api_get_raw):
        """Test file existence validation success."""
        mock_api_get_raw.return_value = "File content"
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.api_get_raw = mock_api_get_raw
        validator = ConfigurationValidator(mock_gh)

        result = validator.validate_file_exists("octocat/Hello-World", "README.md", "main")

        assert result.is_valid
        assert not result.errors
        mock_api_get_raw.assert_called_once_with("repos/octocat/Hello-World/contents/README.md?ref=main")

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.api_get_raw')
    def test_validate_file_exists_failure(self, mock_api_get_raw):
        """Test file existence validation failure."""
        mock_api_get_raw.side_effect = Exception("File not found")
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.api_get_raw = mock_api_get_raw
        validator = ConfigurationValidator(mock_gh)

        result = validator.validate_file_exists("octocat/Hello-World", "nonexistent.md", "main")

        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "file"
        assert "does not exist" in result.errors[0].message

    def test_comprehensive_validation_all_success(self):
        """Test comprehensive validation with all checks passing."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.check_authentication.return_value = True
        mock_gh.api_get.return_value = {"id": 123}
        mock_gh.api_get_raw.return_value = "File content"

        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md",
            "ref": "main"
        }

        result = validator.comprehensive_validation(config)

        assert result.is_valid
        assert not result.errors
        assert not result.warnings

    def test_comprehensive_validation_multiple_failures(self):
        """Test comprehensive validation with multiple failures."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.check_authentication.return_value = False
        mock_gh.api_get.side_effect = Exception("Repository not found")
        mock_gh.api_get_raw.side_effect = Exception("File not found")

        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "invalid/repo",
            "file_path": "nonexistent.md"
        }

        result = validator.comprehensive_validation(config)

        assert not result.is_valid
        # Should have multiple errors (config validation, auth, repo access, file access)
        assert len(result.errors) >= 3

    def test_recursive_listing_validation(self):
        """Test that recursive listing parameter is properly validated."""
        mock_gh = Mock(spec=GitHubCliWrapper)
        validator = ConfigurationValidator(mock_gh)

        # Test valid recursive configuration
        config = {
            "repository": "octocat/Hello-World",
            "directory_path": "docs",
            "recursive": True
        }

        # Should not raise validation errors for recursive flag
        result = validator.validate_file_operation(config)
        assert result.is_valid
        assert not result.errors

        # Test invalid recursive type
        config_invalid = {
            "repository": "octocat/Hello-World",
            "directory_path": "docs",
            "recursive": "yes"  # Should be boolean
        }

        result_invalid = validator.validate_file_operation(config_invalid)
        assert not result_invalid.is_valid
        assert any("recursive" in str(error).lower() for error in result_invalid.errors)

    @patch('untaped_github.gh_cli_wrapper.GitHubCliWrapper.api_get')
    def test_recursive_directory_listing_api_calls(self, mock_api_get):
        """Test that recursive directory listing makes proper API calls."""
        # Mock nested directory structure
        mock_api_get.side_effect = [
            # First call to root directory
            [
                {"name": "README.md", "type": "file", "size": 1024},
                {"name": "src", "type": "dir", "size": 0},
                {"name": "docs", "type": "dir", "size": 0}
            ],
            # Second call to src directory (recursive=True)
            [
                {"name": "main.py", "type": "file", "size": 2048},
                {"name": "utils", "type": "dir", "size": 0},
                {"name": "tests", "type": "dir", "size": 0}
            ],
            # Third call to utils directory (recursive=True)
            [{"name": "helpers.py", "type": "file", "size": 512}],
            # Fourth call to tests directory (recursive=True)
            [{"name": "test_main.py", "type": "file", "size": 1024}],
            # Fifth call to docs directory (recursive=True)
            [{"name": "api.md", "type": "file", "size": 4096}]
        ]

        mock_gh = Mock(spec=GitHubCliWrapper)
        mock_gh.api_get = mock_api_get
        validator = ConfigurationValidator(mock_gh)

        config = {
            "repository": "octocat/test-repo",
            "directory_path": ".",
            "recursive": True
        }

        result = validator.validate_file_operation(config)

        # Should make multiple API calls for recursive listing
        assert mock_api_get.call_count == 5  # root + 4 subdirectories
        assert result.is_valid
