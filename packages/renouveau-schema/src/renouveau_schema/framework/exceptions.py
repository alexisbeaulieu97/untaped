"""Exceptions for the API schema framework."""


class SchemaError(Exception):
    """Base exception for schema-related errors."""
    pass


class ValidationError(SchemaError):
    """Raised when parameter validation fails."""
    pass


class ClientError(Exception):
    """Base exception for API client errors."""
    pass


class AuthenticationError(ClientError):
    """Raised when authentication fails."""
    pass


class NotFoundError(ClientError):
    """Raised when a resource is not found."""
    pass


class ServerError(ClientError):
    """Raised when the server returns an error."""
    pass
