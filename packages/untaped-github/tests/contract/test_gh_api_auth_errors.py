"""Contract tests for gh CLI API authentication errors."""
import pytest

from untaped_github.api.auth import GitHubAuthError, check_authentication


def test_check_authentication_function_exists():
    """Test that the check_authentication function is properly defined."""
    # This test will fail until the function is implemented
    assert callable(check_authentication)


def test_check_authentication_raises_error_when_not_logged_in():
    """Test that check_authentication raises GitHubAuthError when not logged in."""
    # This test will fail until the function is implemented
    with pytest.raises(GitHubAuthError):
        check_authentication()


def test_check_authentication_handles_missing_gh():
    """Test that check_authentication handles missing gh CLI gracefully."""
    # This test will fail until the function is implemented
    with pytest.raises(GitHubAuthError):
        check_authentication()


def test_check_authentication_handles_gh_not_logged_in():
    """Test that check_authentication handles gh CLI not being logged in."""
    # This test will fail until the function is implemented
    with pytest.raises(GitHubAuthError):
        check_authentication()


def test_github_auth_error_is_exception():
    """Test that GitHubAuthError is a proper exception class."""
    # This test will fail until the class is implemented
    assert issubclass(GitHubAuthError, Exception)


def test_github_auth_error_has_message():
    """Test that GitHubAuthError can be created with a message."""
    # This test will fail until the class is implemented
    error = GitHubAuthError("Test error message")
    assert str(error) == "Test error message"
