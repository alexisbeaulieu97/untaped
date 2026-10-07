"""Only the terminal adapter imports prompt_toolkit.

A screen's models, updates and views never touch the terminal library, so the
adapter can be replaced without changing a screen. An AST walk (every import
statement, function-level ones included) over each package's ``src``, the
example plugin and ``scripts`` fails on any other module that imports
``prompt_toolkit`` or one of its submodules.

Two modules predate the adapter and keep their own prompt_toolkit code until
the screens replace it. Each exception carries its reason and is checked for
staleness: it fails once its module stops importing prompt_toolkit, so the
change that removes the import must delete the entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT

ADAPTER = "packages/untaped/src/untaped/screen/terminal.py"
#: Module path (from the repo root) -> why it may still import prompt_toolkit.
EXCEPTIONS: dict[str, str] = {
    "packages/untaped/src/untaped/prompts.py": (
        "the one-shot prompts (text, secret, select, multiselect, confirm) still run on "
        "prompt_toolkit until they are rebuilt as screens"
    ),
    "packages/untaped/src/untaped/picker/app.py": (
        "the workspace picker still runs on its own application until it moves onto the runtime"
    ),
}


def imports_prompt_toolkit(tree: ast.AST) -> list[int]:
    """The line numbers of every import of ``prompt_toolkit`` or a submodule of it."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets = [node.module]
        else:
            continue
        if any(
            target == "prompt_toolkit" or target.startswith("prompt_toolkit.") for target in targets
        ):
            lines.append(node.lineno)
    return sorted(lines)


def source_modules() -> list[Path]:
    """Every shipped Python module: each package's ``src``, the example plugin and ``scripts``."""
    roots = [
        *PACKAGES.glob("*/src"),
        *(REPO_ROOT / "examples").glob("*/src"),
        REPO_ROOT / "scripts",
    ]
    return sorted(path for root in roots for path in root.rglob("*.py"))


def importers() -> dict[str, list[int]]:
    """Repo-relative module path -> the lines where it imports prompt_toolkit."""
    found: dict[str, list[int]] = {}
    for path in source_modules():
        lines = imports_prompt_toolkit(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            found[path.relative_to(REPO_ROOT).as_posix()] = lines
    return found


def test_only_the_adapter_and_the_listed_exceptions_import_prompt_toolkit() -> None:
    stray = sorted(set(importers()) - {ADAPTER, *EXCEPTIONS})
    assert not stray, (
        f"{stray} import prompt_toolkit; build screens with untaped.sdk "
        f"(only {ADAPTER} may import it)"
    )


def test_the_adapter_still_imports_prompt_toolkit() -> None:
    assert ADAPTER in importers()


def test_every_exception_is_still_needed() -> None:
    found = importers()
    stale = sorted(path for path in EXCEPTIONS if path not in found)
    assert not stale, f"{stale} no longer import prompt_toolkit; delete them from EXCEPTIONS"


def test_every_exception_names_its_reason() -> None:
    assert all(reason.strip() for reason in EXCEPTIONS.values())


def test_the_walk_finds_function_level_and_submodule_imports() -> None:
    tree = ast.parse(
        "import os\n"
        "def f():\n"
        "    from prompt_toolkit.shortcuts import choice\n"
        "import prompt_toolkit.keys\n"
        "from prompt_toolkit import Application\n"
        "from prompt_toolkit_extras import x\n"
        "from . import prompt_toolkit\n"
    )
    assert imports_prompt_toolkit(tree) == [3, 4, 5]
