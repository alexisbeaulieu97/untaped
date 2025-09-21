"""Unit tests for GitHub CLI wrapper."""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock

from untaped_github.gh_cli_wrapper import GitHubCliWrapper, GitHubCliError


class TestGitHubCliWrapper:
    """Test GitHub CLI wrapper functionality."""

    def test_init_with_valid_binary(self):
        """Test wrapper initialization with valid gh binary."""
        with patch('shutil.which', return_value='/usr/bin/gh'):
            wrapper = GitHubCliWrapper()
            assert wrapper.gh_binary == 'gh'

    def test_init_with_custom_binary(self):
        """Test wrapper initialization with custom gh binary path."""
        with patch('shutil.which', return_value='/custom/path/gh'):
            wrapper = GitHubCliWrapper('/custom/path/gh')
            assert wrapper.gh_binary == '/custom/path/gh'

    def test_init_with_missing_binary(self):
        """Test wrapper initialization fails when gh binary not found."""
        with patch('shutil.which', return_value=None):
            with pytest.raises(GitHubCliError, match="GitHub CLI .* is not installed"):
                GitHubCliWrapper()

    @patch('subprocess.run')
    def test_run_gh_command_success(self, mock_run):
        """Test successful gh command execution."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"result": "success"}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper._run_gh_command(['api', 'user'])

        mock_run.assert_called_once_with(
            ['gh', 'api', 'user'],
            capture_output=True,
            text=True,
            input=None,
            check=False
        )
        assert result == mock_process

    @patch('subprocess.run')
    def test_run_gh_command_failure(self, mock_run):
        """Test gh command execution failure handling."""
        mock_process = Mock()
        mock_process.returncode = 1
        mock_process.stdout = ''
        mock_process.stderr = 'Error: Not authenticated'
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        with pytest.raises(GitHubCliError, match="GitHub CLI command failed"):
            wrapper._run_gh_command(['api', 'user'])

    @patch('subprocess.run')
    def test_api_get_success(self, mock_run):
        """Test successful API GET request."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"login": "testuser", "id": 123}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.api_get('user')

        assert result == {"login": "testuser", "id": 123}

    @patch('subprocess.run')
    def test_api_get_raw_success(self, mock_run):
        """Test successful API GET raw request."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = 'File content here'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.api_get_raw('repos/owner/repo/contents/file.txt')

        assert result == 'File content here'

    def test_parse_json_response_valid(self):
        """Test JSON response parsing with valid JSON."""
        wrapper = GitHubCliWrapper()
        result = wrapper._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_response_invalid(self):
        """Test JSON response parsing with invalid JSON."""
        wrapper = GitHubCliWrapper()

        with pytest.raises(GitHubCliError, match="Failed to parse JSON response"):
            wrapper._parse_json_response('invalid json')

    @patch('subprocess.run')
    def test_check_authentication_success(self, mock_run):
        """Test authentication check when logged in."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = 'Logged in to github.com as testuser'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.check_authentication()

        assert result is True

    @patch('subprocess.run')
    def test_check_authentication_failure(self, mock_run):
        """Test authentication check when not logged in."""
        mock_process = Mock()
        mock_process.returncode = 1
        mock_process.stdout = ''
        mock_process.stderr = 'Error: Not authenticated'
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.check_authentication()

        assert result is False

    @patch('subprocess.run')
    def test_get_current_user_success(self, mock_run):
        """Test getting current user when authenticated."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"login": "testuser", "id": 123}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.get_current_user()

        assert result == "testuser"

    @patch('subprocess.run')
    def test_get_current_user_failure(self, mock_run):
        """Test getting current user when not authenticated."""
        mock_process = Mock()
        mock_process.returncode = 1
        mock_process.stdout = ''
        mock_process.stderr = 'Error: Not authenticated'
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.get_current_user()

        assert result is None

    @patch('subprocess.run')
    def test_validate_token_scopes(self, mock_run):
        """Test token scope validation."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = 'Token scopes: read, write'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()
        result = wrapper.validate_token_scopes(['read', 'write'])

        # Simplified implementation returns the requested scopes
        assert result == ['read', 'write']
