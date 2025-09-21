"""Configuration processing service for GitHub operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from ..error_formatter import ErrorFormatter
from ..gh_cli_wrapper import GitHubCliWrapper
from ..models.file_operation import FileOperation
from ..models.file_path import FilePath
from ..models.repository import Repository
from ..models.validation import ValidationResult
from ..models.variable_file import VariableFile
from ..validators.config_validator import ConfigurationValidator


class ConfigurationProcessingError(Exception):
    """Exception raised during configuration processing."""

    def __init__(self, message: str, config_path: str = None) -> None:
        super().__init__(message)
        self.config_path = config_path


class ConfigurationProcessor:
    """Service for processing and validating GitHub operation configurations."""

    def __init__(self, gh_wrapper: GitHubCliWrapper) -> None:
        self.gh_wrapper = gh_wrapper
        self.validator = ConfigurationValidator(gh_wrapper)

    def load_yaml_config(self, config_path: str | Path) -> Dict[str, Any]:
        """Load YAML configuration from file."""
        path = Path(config_path)

        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            formatted_error = ErrorFormatter.format_yaml_error(e, str(path))
            raise ConfigurationProcessingError(formatted_error)
        except Exception as e:
            formatted_error = ErrorFormatter.format_yaml_error(e, str(path))
            raise ConfigurationProcessingError(formatted_error)

    def load_variable_files(self, var_files: List[str | Path]) -> Dict[str, Any]:
        """Load template variables from multiple variable files."""
        all_variables = {}

        for var_file in var_files:
            try:
                variable_file = VariableFile.from_yaml_file(var_file)
                # Merge variables, with later files overriding earlier ones
                all_variables.update(variable_file.variables)
            except Exception as e:
                raise ConfigurationProcessingError(f"Failed to load variable file {var_file}: {e}")

        return all_variables

    def render_template(
        self, config_data: Dict[str, Any], variables: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Render Jinja2 template variables in configuration."""
        from jinja2 import Template

        try:
            # Convert config to YAML string for template rendering
            config_yaml = yaml.dump(config_data, default_flow_style=False)

            # Create template and render
            template = Template(config_yaml)
            rendered_yaml = template.render(**variables)

            # Parse rendered YAML back to dict
            return yaml.safe_load(rendered_yaml)
        except yaml.YAMLError as e:
            raise ConfigurationProcessingError(f"Failed to parse rendered configuration: {e}")
        except Exception as e:
            raise ConfigurationProcessingError(f"Template rendering failed: {e}")

    def process_configuration(
        self,
        config_path: str | Path,
        var_files: List[str | Path] = None,
        environment_vars: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Process a configuration file with template rendering and validation."""
        try:
            # Load base configuration
            config_data = self.load_yaml_config(config_path)

            # Load variable files if provided
            variables = {}
            if var_files:
                variables.update(self.load_variable_files(var_files))

            # Add environment variables if provided
            if environment_vars:
                variables.update(environment_vars)

            # Render template if variables are present
            if variables:
                config_data = self.render_template(config_data, variables)

            return config_data

        except Exception as e:
            raise ConfigurationProcessingError(f"Configuration processing failed: {e}")

    def validate_configuration(self, config_data: Dict[str, Any]) -> ValidationResult:
        """Validate a processed configuration."""
        return self.validator.comprehensive_validation(config_data)

    def process_and_validate(
        self,
        config_path: str | Path,
        var_files: List[str | Path] = None,
        environment_vars: Dict[str, str] = None,
    ) -> tuple[Dict[str, Any], ValidationResult]:
        """Process and validate configuration in one step."""
        try:
            # Process configuration
            config_data = self.process_configuration(config_path, var_files, environment_vars)

            # Validate configuration
            validation_result = self.validate_configuration(config_data)

            return config_data, validation_result

        except ConfigurationProcessingError:
            # Return empty config and failed validation result
            return {}, ValidationResult.failure(
                [
                    ValidationError(
                        field="configuration",
                        message="Configuration processing failed",
                        error_type="processing",
                    )
                ]
            )

    def extract_file_operation(self, config_data: Dict[str, Any]) -> FileOperation:
        """Extract and create FileOperation model from configuration."""
        try:
            return FileOperation(**config_data)
        except Exception as e:
            raise ConfigurationProcessingError(f"Failed to create FileOperation: {e}")

    def extract_repository(self, config_data: Dict[str, Any]) -> Repository:
        """Extract and create Repository model from configuration."""
        try:
            repository_str = config_data["repository"]
            owner, name = repository_str.split("/")
            return Repository(owner=owner, name=name)
        except Exception as e:
            raise ConfigurationProcessingError(f"Failed to create Repository: {e}")

    def extract_file_path(self, config_data: Dict[str, Any]) -> FilePath:
        """Extract and create FilePath model from configuration."""
        try:
            return FilePath(path=config_data["file_path"])
        except Exception as e:
            raise ConfigurationProcessingError(f"Failed to create FilePath: {e}")
