"""Renouveau Schema Package.

This package provides YAML-based API schema definitions and a framework
for working with API endpoints in a structured way.
"""

from .framework import SchemaLoader, APIClient, APISchema, APIEndpoint
from .framework.exceptions import SchemaError, ValidationError

__version__ = "0.1.0"
__all__ = [
    "SchemaLoader",
    "APIClient", 
    "APISchema",
    "APIEndpoint",
    "SchemaError",
    "ValidationError",
]