"""Layering: runtime imports, where they point, and the layer and settings rules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from untaped.conventions.layering import layering_violations, runtime_imports
from untaped.conventions.source import import_targets, source_files


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


@pytest.mark.parametrize(
    ("module", "source", "violations"),
    [
        (
            "domain/model.py",
            "from acme.cli import build",
            ["acme/domain/model.py::layer::domain -> acme.cli"],
        ),
        (
            "application/use.py",
            "from acme.infrastructure.http import Client",
            ["acme/application/use.py::layer::application -> acme.infrastructure.http"],
        ),
        (
            "infrastructure/http.py",
            "from ..application.ports import Port",
            ["acme/infrastructure/http.py::layer::infrastructure -> acme.application.ports"],
        ),
        (
            "application/use.py",
            "from untaped.sdk import app_context",
            ["acme/application/use.py::settings::application -> app_context"],
        ),
        ("domain/model.py", "from acme.cli import build  # untaped: allow layer", []),
        (
            "cli/app.py",
            "from untaped.sdk import app_context\nfrom acme.infrastructure import x",
            [],
        ),
    ],
    ids=["layer-domain", "layer-application", "layer-infrastructure", "settings", "allowed", "cli"],
)
def test_layer_and_settings_rules(
    tmp_path: Path, module: str, source: str, violations: list[str]
) -> None:
    source_dir = tmp_path / "acme"
    path = source_dir / module
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n", encoding="utf-8")
    files = list(source_files(source_dir))
    assert layering_violations("acme", source_dir, files) == violations
