"""Unit tests for GitHub file operations API."""
import pytest
from unittest.mock import Mock, patch

from untaped_github.api.file_operations import GitHubFileOperationsApi
from untaped_github.api.base import GitHubApiError


class TestGitHubFileOperationsApi:
    """Test GitHub file operations API."""

    def test_read_file_success(self):
        """Test successful file reading."""
        mock_gh = Mock()
        mock_gh.api_get_raw.return_value = "File content here"

        api = GitHubFileOperationsApi(mock_gh)
        result = api.read_file("octocat/Hello-World", "README.md", "main")

        assert result == "File content here"
        mock_gh.api_get_raw.assert_called_once_with("repos/octocat/Hello-World/contents/README.md?ref=main")

    def test_read_file_with_different_ref(self):
        """Test file reading with custom branch."""
        mock_gh = Mock()
        mock_gh.api_get_raw.return_value = "File content"

        api = GitHubFileOperationsApi(mock_gh)
        result = api.read_file("octocat/Hello-World", "README.md", "develop")

        assert result == "File content"
        mock_gh.api_get_raw.assert_called_once_with("repos/octocat/Hello-World/contents/README.md?ref=develop")

    def test_list_directory_success(self):
        """Test successful directory listing."""
        mock_gh = Mock()
        mock_gh.api_get.return_value = [
            {"name": "README.md", "type": "file", "size": 1024},
            {"name": "src", "type": "dir", "size": 0}
        ]

        api = GitHubFileOperationsApi(mock_gh)
        result = api.list_directory("octocat/Hello-World", "docs")

        assert len(result) == 2
        assert result[0]["name"] == "README.md"
        mock_gh.api_get.assert_called_once_with("repos/octocat/Hello-World/contents/docs")

    def test_list_directory_recursive(self):
        """Test recursive directory listing."""
        mock_gh = Mock()

        # Mock nested directory structure
        def mock_api_get(endpoint):
            if "contents/docs" in endpoint:
                return [
                    {"name": "README.md", "type": "file", "size": 1024},
                    {"name": "src", "type": "dir", "size": 0}
                ]
            elif "contents/docs/src" in endpoint:
                return [
                    {"name": "main.py", "type": "file", "size": 2048},
                    {"name": "utils", "type": "dir", "size": 0}
                ]
            elif "contents/docs/src/utils" in endpoint:
                return [{"name": "helpers.py", "type": "file", "size": 512}]
            else:
                return []

        mock_gh.api_get.side_effect = mock_api_get

        api = GitHubFileOperationsApi(mock_gh)
        result = api.list_directory("octocat/Hello-World", "docs", recursive=True)

        # Should contain files from all levels
        assert len(result) == 4  # README.md + main.py + helpers.py + src dir
        mock_gh.api_get.call_count = 3  # docs + src + utils

    def test_list_directory_with_subpath(self):
        """Test directory listing with subdirectory path."""
        mock_gh = Mock()
        mock_gh.api_get.return_value = [{"name": "main.py", "type": "file", "size": 2048}]

        api = GitHubFileOperationsApi(mock_gh)
        result = api.list_directory("octocat/Hello-World", "src/utils")

        assert len(result) == 1
        assert result[0]["name"] == "main.py"
        mock_gh.api_get.assert_called_once_with("repos/octocat/Hello-World/contents/src/utils")

    def test_get_file_info_success(self):
        """Test successful file info retrieval."""
        mock_gh = Mock()
        mock_gh.api_get.return_value = {"name": "README.md", "size": 1024, "type": "file"}

        api = GitHubFileOperationsApi(mock_gh)
        result = api.get_file_info("octocat/Hello-World", "README.md")

        assert result["name"] == "README.md"
        mock_gh.api_get.assert_called_once_with("repos/octocat/Hello-World/contents/README.md")

    def test_check_file_exists_true(self):
        """Test file existence check when file exists."""
        mock_gh = Mock()
        mock_gh.api_get.return_value = {"name": "README.md", "type": "file"}

        api = GitHubFileOperationsApi(mock_gh)
        result = api.check_file_exists("octocat/Hello-World", "README.md")

        assert result is True
        mock_gh.api_get.assert_called_once_with("repos/octocat/Hello-World/contents/README.md")

    def test_check_file_exists_false(self):
        """Test file existence check when file doesn't exist."""
        mock_gh = Mock()
        mock_gh.api_get.side_effect = GitHubApiError("File not found")

        api = GitHubFileOperationsApi(mock_gh)
        result = api.check_file_exists("octocat/Hello-World", "nonexistent.md")

        assert result is False

    def test_read_file_api_error(self):
        """Test file reading with API error."""
        mock_gh = Mock()
        mock_gh.api_get_raw.side_effect = Exception("API Error")

        api = GitHubFileOperationsApi(mock_gh)

        with pytest.raises(GitHubApiError, match="Failed to read file"):
            api.read_file("octocat/Hello-World", "README.md")

    def test_list_directory_api_error(self):
        """Test directory listing with API error."""
        mock_gh = Mock()
        mock_gh.api_get.side_effect = Exception("API Error")

        api = GitHubFileOperationsApi(mock_gh)

        with pytest.raises(GitHubApiError, match="Failed to list directory"):
            api.list_directory("octocat/Hello-World", "docs")

    def test_authentication_required(self):
        """Test that authentication is required for operations."""
        mock_gh = Mock()
        mock_gh.require_authentication.side_effect = Exception("Not authenticated")

        api = GitHubFileOperationsApi(mock_gh)

        with pytest.raises(Exception, match="Not authenticated"):
            api.read_file("octocat/Hello-World", "README.md")

    def test_repository_access_validation(self):
        """Test that repository access is validated."""
        mock_gh = Mock()
        mock_gh.validate_repository_access.side_effect = Exception("No access")

        api = GitHubFileOperationsApi(mock_gh)

        with pytest.raises(Exception, match="No access"):
            api.read_file("private/repo", "README.md")

    def test_validate_file_operation_file_exists(self):
        """Test validate_file_operation returns True when file exists."""
        mock_gh = Mock()
        mock_gh.api_get.return_value = {"name": "README.md", "type": "file"}

        api = GitHubFileOperationsApi(mock_gh)
        service = FileOperationService(mock_gh)

        file_op = Mock()
        file_op.repository = "octocat/Hello-World"
        file_op.file_path = "README.md"
        file_op.ref = "main"

        result = service.validate_file_operation(file_op)

        assert result is True

    def test_validate_file_operation_file_missing(self):
        """Test validate_file_operation returns False when file doesn't exist."""
        mock_gh = Mock()
        mock_gh.api_get.side_effect = Exception("File not found")

        api = GitHubFileOperationsApi(mock_gh)
        service = FileOperationService(mock_gh)

        file_op = Mock()
        file_op.repository = "octocat/Hello-World"
        file_op.file_path = "nonexistent.md"
        file_op.ref = "main"

        result = service.validate_file_operation(file_op)

        assert result is False

    def test_validate_file_operation_authentication_failure(self):
        """Test validate_file_operation returns False when authentication fails."""
        mock_gh = Mock()
        mock_gh.require_authentication.side_effect = Exception("Not authenticated")

        api = GitHubFileOperationsApi(mock_gh)
        service = FileOperationService(mock_gh)

        file_op = Mock()
        file_op.repository = "octocat/Hello-World"
        file_op.file_path = "README.md"
        file_op.ref = "main"

        result = service.validate_file_operation(file_op)

        assert result is False

    def test_validate_file_operation_repository_access_failure(self):
        """Test validate_file_operation returns False when repository access fails."""
        mock_gh = Mock()
        mock_gh.require_authentication.return_value = None
        mock_gh.validate_repository_access.side_effect = Exception("No access")

        api = GitHubFileOperationsApi(mock_gh)
        service = FileOperationService(mock_gh)

        file_op = Mock()
        file_op.repository = "private/repo"
        file_op.file_path = "README.md"
        file_op.ref = "main"

        result = service.validate_file_operation(file_op)

        assert result is False
