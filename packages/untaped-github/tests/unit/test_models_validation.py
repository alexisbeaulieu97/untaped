"""Unit tests for Pydantic models validation."""
import pytest
from pydantic import ValidationError

from untaped_github.models.file_operation import FileOperation
from untaped_github.models.repository import Repository
from untaped_github.models.file_path import FilePath
from untaped_github.models.variable_file import VariableFile
from untaped_github.models.validation import ValidationResult, ValidationError


class TestFileOperationModel:
    """Test FileOperation model validation."""

    def test_valid_file_operation(self):
        """Test valid file operation configuration."""
        config = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md",
            "ref": "main"
        }
        operation = FileOperation(**config)
        assert operation.repository == "octocat/Hello-World"
        assert operation.file_path == "README.md"
        assert operation.ref == "main"

    def test_file_operation_without_ref(self):
        """Test file operation defaults to main branch."""
        config = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md"
        }
        operation = FileOperation(**config)
        assert operation.ref == "main"

    def test_invalid_repository_format(self):
        """Test validation fails for invalid repository format."""
        with pytest.raises(ValidationError):
            FileOperation(repository="invalid-repo", file_path="README.md")

    def test_invalid_file_path_with_directory_traversal(self):
        """Test validation fails for file path with directory traversal."""
        with pytest.raises(ValidationError):
            FileOperation(repository="octocat/Hello-World", file_path="../test.txt")

    def test_invalid_file_path_starting_with_slash(self):
        """Test validation fails for file path starting with slash."""
        with pytest.raises(ValidationError):
            FileOperation(repository="octocat/Hello-World", file_path="/test.txt")

    def test_empty_repository(self):
        """Test validation fails for empty repository."""
        with pytest.raises(ValidationError):
            FileOperation(repository="", file_path="README.md")

    def test_empty_file_path(self):
        """Test validation fails for empty file path."""
        with pytest.raises(ValidationError):
            FileOperation(repository="octocat/Hello-World", file_path="")


class TestRepositoryModel:
    """Test Repository model validation."""

    def test_valid_repository(self):
        """Test valid repository creation."""
        repo = Repository(owner="octocat", name="Hello-World")
        assert repo.owner == "octocat"
        assert repo.name == "Hello-World"
        assert repo.to_repository_string() == "octocat/Hello-World"

    def test_repository_owner_normalization(self):
        """Test repository owner is normalized to lowercase."""
        repo = Repository(owner="OctoCat", name="Hello-World")
        assert repo.owner == "octocat"

    def test_invalid_owner_characters(self):
        """Test validation fails for invalid owner characters."""
        with pytest.raises(ValidationError):
            Repository(owner="invalid@owner", name="Hello-World")

    def test_repository_name_length_limit(self):
        """Test validation fails for repository name too long."""
        long_name = "a" * 101  # 101 characters
        with pytest.raises(ValidationError):
            Repository(owner="octocat", name=long_name)

    def test_invalid_name_characters(self):
        """Test validation fails for invalid repository name characters."""
        with pytest.raises(ValidationError):
            Repository(owner="octocat", name="invalid@name")


class TestFilePathModel:
    """Test FilePath model validation."""

    def test_valid_file_path(self):
        """Test valid file path creation."""
        path = FilePath(path="README.md")
        assert path.path == "README.md"
        assert not path.is_directory

    def test_directory_path(self):
        """Test directory path creation."""
        path = FilePath(path="docs", is_directory=True)
        assert path.path == "docs"
        assert path.is_directory

    def test_path_normalization(self):
        """Test path normalization removes extra spaces."""
        path = FilePath(path="  README.md  ")
        assert path.path == "README.md"

    def test_invalid_path_with_double_slash(self):
        """Test validation fails for path with double slash."""
        with pytest.raises(ValidationError):
            FilePath(path="docs//README.md")

    def test_invalid_path_type(self):
        """Test validation fails when is_directory is not boolean."""
        with pytest.raises(ValidationError):
            FilePath(path="README.md", is_directory="true")

    def test_root_directory_detection(self):
        """Test root directory detection."""
        path = FilePath(path=".")
        assert path.is_root_directory()

        path = FilePath(path="test")
        assert not path.is_root_directory()


class TestValidationResultModel:
    """Test ValidationResult model."""

    def test_success_validation(self):
        """Test successful validation result."""
        result = ValidationResult.success()
        assert result.is_valid
        assert not result.errors
        assert not result.warnings

    def test_failure_validation(self):
        """Test failed validation result."""
        error = ValidationError(field="test", message="Test error", error_type="test")
        result = ValidationResult.failure([error])
        assert not result.is_valid
        assert len(result.errors) == 1
        assert result.errors[0].field == "test"

    def test_add_error(self):
        """Test adding errors to validation result."""
        result = ValidationResult.success()
        result.add_error("field1", "Error 1")
        assert not result.is_valid
        assert len(result.errors) == 1

    def test_add_warning(self):
        """Test adding warnings to validation result."""
        result = ValidationResult.success()
        result.add_warning("Warning message")
        assert result.is_valid
        assert len(result.warnings) == 1

    def test_error_summary(self):
        """Test error summary generation."""
        error = ValidationError(field="test", message="Test error", error_type="test")
        result = ValidationResult.failure([error])
        summary = result.error_summary()
        assert "Validation errors:" in summary
        assert "test: Test error" in summary
