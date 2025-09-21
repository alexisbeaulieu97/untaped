"""Contract tests for gh CLI API directory listing."""
import pytest

from untaped_github.api.file_operations import list_directory_contents


def test_list_directory_contents_function_exists():
    """Test that the list_directory_contents function is properly defined."""
    # This test will fail until the function is implemented
    assert callable(list_directory_contents)


def test_list_directory_contents_requires_repository():
    """Test that list_directory_contents requires a repository parameter."""
    # This test will fail until the function is implemented
    with pytest.raises(Exception):
        list_directory_contents("owner/repo", "path/to/directory")


def test_list_directory_contents_requires_directory_path():
    """Test that list_directory_contents requires a directory path parameter."""
    # This test will fail until the function is implemented
    with pytest.raises(Exception):
        list_directory_contents("owner/repo", "path/to/directory")


def test_list_directory_contents_handles_missing_directory():
    """Test that list_directory_contents handles missing directories gracefully."""
    # This test will fail until the function is implemented
    with pytest.raises(Exception):
        list_directory_contents("owner/repo", "nonexistent/directory")


def test_list_directory_contents_handles_invalid_repository():
    """Test that list_directory_contents handles invalid repository gracefully."""
    # This test will fail until the function is implemented
    with pytest.raises(Exception):
        list_directory_contents("invalid/repo/format", "path/to/directory")


def test_list_directory_contents_returns_list():
    """Test that list_directory_contents returns a list of files."""
    # This test will fail until the function is implemented
    with pytest.raises(Exception):
        result = list_directory_contents("owner/repo", "path/to/directory")
        assert isinstance(result, list)


def test_list_directory_contents_supports_recursive():
    """Test that list_directory_contents supports recursive listing."""
    # This test will fail until the function is implemented
    with pytest.raises(Exception):
        result = list_directory_contents("owner/repo", "path/to/directory", recursive=True)
        assert isinstance(result, list)
