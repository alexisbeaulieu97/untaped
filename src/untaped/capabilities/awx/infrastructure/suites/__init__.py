"""Concrete adapters for ``untaped awx test``.

Frontmatter parsing, the ``!ref`` YAML tag, the Jinja2 environment, the
interactive prompt, and the variable-resolution adapter all live here.
The application layer depends on these via :mod:`untaped.capabilities.awx.application.suites.ports`.
"""

from __future__ import annotations

from untaped.capabilities.awx.infrastructure.suites.filesystem import LocalFilesystem
from untaped.capabilities.awx.infrastructure.suites.parser import (
    DefaultParser,
    RefSentinel,
    build_jinja_env,
    load_yaml_with_refs,
    split_frontmatter,
)
from untaped.capabilities.awx.infrastructure.suites.prompt import UiPrompt
from untaped.capabilities.awx.infrastructure.suites.vars_resolver import resolve_variables

__all__ = [
    "DefaultParser",
    "LocalFilesystem",
    "RefSentinel",
    "UiPrompt",
    "build_jinja_env",
    "load_yaml_with_refs",
    "resolve_variables",
    "split_frontmatter",
]
