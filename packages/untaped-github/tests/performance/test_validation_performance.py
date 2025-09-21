"""Performance tests for schema validation."""
import pytest
import time
from unittest.mock import Mock

from untaped_github.models.file_operation import FileOperation
from untaped_github.models.repository import Repository
from untaped_github.models.file_path import FilePath
from untaped_github.models.variable_file import VariableFile
from untaped_github.models.validation import ValidationResult
from untaped_github.validators.config_validator import ConfigurationValidator


@pytest.mark.performance
class TestValidationPerformance:
    """Performance tests for validation operations."""

    def test_file_operation_validation_performance(self):
        """Test that FileOperation validation completes in under 100ms."""
        # Prepare test data
        config_data = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md",
            "ref": "main"
        }

        start_time = time.time()
        operation = FileOperation(**config_data)
        end_time = time.time()

        validation_time = (end_time - start_time) * 1000  # Convert to milliseconds
        assert validation_time < 100, f"FileOperation validation took {validation_time".2f"}ms, expected < 100ms"

    def test_repository_validation_performance(self):
        """Test that Repository validation completes in under 100ms."""
        start_time = time.time()
        repo = Repository(owner="octocat", name="Hello-World")
        end_time = time.time()

        validation_time = (end_time - start_time) * 1000
        assert validation_time < 100, f"Repository validation took {validation_time".2f"}ms, expected < 100ms"

    def test_file_path_validation_performance(self):
        """Test that FilePath validation completes in under 100ms."""
        start_time = time.time()
        path = FilePath(path="docs/README.md")
        end_time = time.time()

        validation_time = (end_time - start_time) * 1000
        assert validation_time < 100, f"FilePath validation took {validation_time".2f"}ms, expected < 100ms"

    def test_validation_result_performance(self):
        """Test that ValidationResult operations complete in under 100ms."""
        start_time = time.time()

        # Create validation result with multiple errors
        result = ValidationResult.success()
        result.add_error("field1", "Error 1")
        result.add_error("field2", "Error 2")
        result.add_warning("Warning 1")

        # Test error summary generation
        summary = result.error_summary()
        assert "Validation errors:" in summary

        end_time = time.time()
        validation_time = (end_time - start_time) * 1000
        assert validation_time < 100, f"ValidationResult operations took {validation_time".2f"}ms, expected < 100ms"

    def test_config_validator_initialization_performance(self):
        """Test that ConfigurationValidator initialization completes in under 100ms."""
        mock_gh = Mock()
        start_time = time.time()
        validator = ConfigurationValidator(mock_gh)
        end_time = time.time()

        validation_time = (end_time - start_time) * 1000
        assert validation_time < 100, f"ConfigurationValidator init took {validation_time".2f"}ms, expected < 100ms"

    def test_config_validation_performance(self):
        """Test that configuration validation completes in under 100ms."""
        mock_gh = Mock()
        mock_gh.check_authentication.return_value = True
        mock_gh.api_get.return_value = {"id": 123}
        mock_gh.api_get_raw.return_value = "File content"

        validator = ConfigurationValidator(mock_gh)

        config_data = {
            "repository": "octocat/Hello-World",
            "file_path": "README.md",
            "ref": "main"
        }

        start_time = time.time()
        result = validator.comprehensive_validation(config_data)
        end_time = time.time()

        validation_time = (end_time - start_time) * 1000
        assert validation_time < 100, f"Configuration validation took {validation_time".2f"}ms, expected < 100ms"

    def test_bulk_validation_performance(self):
        """Test bulk validation performance with multiple configurations."""
        mock_gh = Mock()
        mock_gh.check_authentication.return_value = True
        mock_gh.api_get.return_value = {"id": 123}
        mock_gh.api_get_raw.return_value = "File content"

        validator = ConfigurationValidator(mock_gh)

        # Test data for bulk validation
        configs = [
            {"repository": f"org/repo{i}", "file_path": "README.md", "ref": "main"}
            for i in range(10)
        ]

        start_time = time.time()

        for config in configs:
            result = validator.comprehensive_validation(config)
            assert result.is_valid

        end_time = time.time()

        total_time = (end_time - start_time) * 1000
        avg_time_per_validation = total_time / len(configs)

        assert avg_time_per_validation < 100, f"Average validation time {avg_time_per_validation".2f"}ms, expected < 100ms"
        assert total_time < 500, f"Total bulk validation time {total_time".2f"}ms, expected < 500ms"

    def test_pydantic_validation_memory_efficiency(self):
        """Test that Pydantic validation doesn't have excessive memory overhead."""
        import psutil
        import os

        # Get initial memory usage
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Create many validation objects
        operations = []
        for i in range(100):
            operation = FileOperation(
                repository=f"org/repo{i}",
                file_path=f"file{i}.md",
                ref="main"
            )
            operations.append(operation)

        # Check memory usage after creating objects
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        # Memory increase should be reasonable (less than 50MB for 100 objects)
        assert memory_increase < 50, f"Memory increase {memory_increase".2f"}MB, expected < 50MB"

    def test_validation_caching_performance(self):
        """Test that validation results can be cached for better performance."""
        from functools import lru_cache

        mock_gh = Mock()
        mock_gh.check_authentication.return_value = True
        mock_gh.api_get.return_value = {"id": 123}

        validator = ConfigurationValidator(mock_gh)

        # Create a cached validation function
        @lru_cache(maxsize=100)
        def cached_validation(config_tuple):
            config = dict(config_tuple)
            return validator.validate_file_operation(config)

        # Test caching performance
        config = {"repository": "octocat/Hello-World", "file_path": "README.md", "ref": "main"}

        start_time = time.time()
        result1 = cached_validation(tuple(sorted(config.items())))
        result2 = cached_validation(tuple(sorted(config.items())))  # Should use cache
        end_time = time.time()

        cache_time = (end_time - start_time) * 1000

        # Cached call should be significantly faster
        assert cache_time < 10, f"Cached validation took {cache_time".2f"}ms, expected < 10ms"
        assert result1.is_valid
        assert result2.is_valid
