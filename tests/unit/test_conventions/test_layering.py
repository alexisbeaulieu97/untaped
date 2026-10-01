"""Layering: which imports count as runtime imports, and where they point."""

from __future__ import annotations

import ast

from untaped.conventions.layering import import_targets, runtime_imports


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
