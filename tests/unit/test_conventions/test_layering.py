"""Layering: which imports count as runtime imports, and where they point."""

from __future__ import annotations

import ast

import pytest

from untaped.conventions.layering import runtime_imports
from untaped.conventions.source import import_targets


def test_runtime_imports_skip_only_type_checking_bodies() -> None:
    source = """
from typing import TYPE_CHECKING
import untaped.capabilities.demo.cli.app
if TYPE_CHECKING:
    from untaped.capabilities.demo.application.ports import Port
else:
    from untaped.capabilities.demo.application import ports
from ..application import use_case
"""
    tree = ast.parse(source)
    package = "untaped.capabilities.demo.infrastructure"
    targets = [t for node in runtime_imports(tree) for t in import_targets(node, package)]
    assert targets == [
        "typing",
        "untaped.capabilities.demo.cli.app",
        "untaped.capabilities.demo.application",  # the else branch
        "untaped.capabilities.demo.application",  # the relative import
    ]


@pytest.mark.parametrize(
    ("statement", "targets"),
    [
        ("from . import x, y", ["demo.cli.x", "demo.cli.y"]),
        ("from .. import x", ["demo.x"]),
        ("from ... import x", []),  # above the top-level package
    ],
)
def test_import_targets_resolve_relative_imports(statement: str, targets: list[str]) -> None:
    (node,) = ast.parse(statement).body
    assert isinstance(node, ast.ImportFrom)
    assert import_targets(node, "demo.cli") == targets
