"""Exceptions for Ansible Tower API client."""


class TowerError(Exception):
    """Base exception for Tower API errors."""
    pass


class TowerConnectionError(TowerError):
    """Raised when connection to Tower fails."""
    pass


class TowerAuthenticationError(TowerError):
    """Raised when Tower authentication fails."""
    pass


class TowerNotFoundError(TowerError):
    """Raised when a Tower resource is not found."""
    pass


class TowerPermissionError(TowerError):
    """Raised when Tower operation is not permitted."""
    pass


class TowerServerError(TowerError):
    """Raised when Tower server returns an error."""
    pass
