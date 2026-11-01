"""Layering: where imports point, what the light modules load, and who reads settings."""

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


_COMMAND = "commands/run.py"


@pytest.mark.parametrize(
    ("files", "violations"),
    [
        (
            {"api.py": "from acme.commands.run import Run"},
            ["acme/api.py:1::layer::acme.api -> acme.commands.run"],
        ),
        (
            {"records.py": "from acme import commands"},
            ["acme/records.py:1::layer::acme.records -> acme.commands"],
        ),
        (
            {"api.py": "from .commands.run import Run"},
            ["acme/api.py:1::layer::acme.api -> acme.commands.run"],
        ),
        (
            {"settings.py": "from acme.helpers import x", "helpers.py": "import acme.commands.run"},
            ["acme/settings.py:1::layer::acme.settings -> acme.commands.run via acme.helpers"],
        ),
        (
            {"__init__.py": "from acme.commands.run import Run", "api.py": ""},
            ["acme/__init__.py:1::layer::acme.api -> acme.commands.run via acme"],
        ),
        (
            {
                "api.py": "from . import helpers, util",
                "helpers/__init__.py": "",
                "util.py": "",
                "helpers/util.py": "import acme.commands.run",
            },
            [],
        ),
        (
            {"api.py": "import acme.commands\nfrom acme.commands.run import Run"},
            [
                "acme/api.py:1::layer::acme.api -> acme.commands",
                "acme/api.py:2::layer::acme.api -> acme.commands.run",
            ],
        ),
        (
            {"api.py": "if TYPE_CHECKING:\n    pass\nelse:\n    from acme.commands.run import Run"},
            ["acme/api.py:4::layer::acme.api -> acme.commands.run"],
        ),
        ({"api.py": "def hint():\n    from acme.commands.run import Run"}, []),
        ({"api.py": "if TYPE_CHECKING:\n    from acme.commands.run import Run"}, []),
        ({"api.py": "from acme.commands.run import Run  # untaped: allow layer"}, []),
        ({"application/use.py": "from acme.commands.run import Run"}, []),
        ({"helpers.py": "from acme.commands.run import Run"}, []),
        (
            {"domain/model.py": "from untaped.sdk import settings_of"},
            ["acme/domain/model.py:1::settings::domain -> settings_of"],
        ),
        (
            {"domain/model.py": "def read():\n    from untaped.sdk import settings_of"},
            ["acme/domain/model.py:2::settings::domain -> settings_of"],
        ),
        ({"domain/model.py": "if TYPE_CHECKING:\n    from untaped.sdk import settings_of"}, []),
        ({"commands/app.py": "from untaped.sdk import settings_of"}, []),
    ],
    ids=[
        "api-imports-a-command",
        "records-imports-the-commands-package",
        "relative-import",
        "through-another-module",
        "a-package-init-the-import-runs",
        "a-name-from-another-import",
        "each-line-its-own-finding",
        "typing-else-runs",
        "lazy-import",
        "typing-import",
        "allowed",
        "logic-may-import-a-command",
        "a-module-no-light-one-imports",
        "settings-in-a-lower-folder",
        "settings-in-a-function",
        "settings-for-typing",
        "settings-in-commands",
    ],
)
def test_layer_and_settings_rules(
    tmp_path: Path, files: dict[str, str], violations: list[str]
) -> None:
    source_dir = tmp_path / "acme"
    for module, source in {"commands/__init__.py": "", _COMMAND: "class Run: ...", **files}.items():
        path = source_dir / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source + "\n", encoding="utf-8")
    parsed = list(source_files(source_dir))
    assert layering_violations("acme", source_dir, parsed) == violations
