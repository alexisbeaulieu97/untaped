"""Custom exception hierarchy for untaped-core utilities."""


class UntapedCoreError(Exception):
    """Base exception for untaped-core."""


class YamlLoadError(UntapedCoreError):
    """Raised when YAML content cannot be read or parsed."""


class TemplateRenderingError(UntapedCoreError):
    """Raised when Jinja2 template rendering fails."""


class ConfigurationValidationError(UntapedCoreError):
    """Raised when a configuration file is structurally invalid."""
