"""Layering: where imports point, and the layer and settings rules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from untaped_lint.infrastructure.files import import_targets, source_files
from untaped_lint.providers.conventions.layering import layering_violations


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
            "domain/model.py",
            "from untaped.sdk import settings_of",
            ["acme/domain/model.py::settings::domain -> settings_of"],
        ),
        ("domain/model.py", "from acme.cli import build  # untaped: allow layer", []),
        (
            "cli/app.py",
            "from untaped.sdk import settings_of\nfrom acme.infrastructure import x",
            [],
        ),
        (
            "infrastructure/http.py",
            "if TYPE_CHECKING:\n    from acme.application.ports import Port",
            [],
        ),
        (
            "application/ports.py",
            "if TYPE_CHECKING:\n    from acme.infrastructure.http import Ref",
            ["acme/application/ports.py::layer::application -> acme.infrastructure.http"],
        ),
        (
            "infrastructure/http.py",
            "if TYPE_CHECKING:\n    pass\nelse:\n    from acme.application.ports import Port",
            ["acme/infrastructure/http.py::layer::infrastructure -> acme.application.ports"],
        ),
    ],
    ids=[
        "layer-domain",
        "layer-application",
        "layer-infrastructure",
        "settings-of",
        "allowed",
        "cli",
        "port-for-typing",
        "typing-import-against-the-layers",
        "else-branch-runs",
    ],
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
