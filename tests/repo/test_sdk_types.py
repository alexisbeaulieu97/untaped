"""What a type checker sees of ``untaped.sdk``: typos are errors, screen names keep their types.

The SDK resolves its screen names lazily (a module-level ``__getattr__``),
which must stay hidden from the type checker: visible, it types every other
attribute as ``object`` and a plugin's misspelled import passes mypy.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from repo.support import REPO_ROOT

SDK = REPO_ROOT / "packages/untaped/src/untaped/sdk.py"

SNIPPET = """\
import untaped.sdk
from untaped.sdk import Cmd, Key, Screen, TextInput, UiContext, field_for
from untaped.sdk import Scren  # line 3: a typo in an import
from untaped.sdk import emmit  # line 4: a typo in a helper

untaped.sdk.Scren  # line 6: a typo as an attribute


def title(screen: Screen[int, str]) -> str:
    return screen.title


def run(ui: UiContext, screen: Screen[int, str]) -> str:
    return ui.run(screen)


reveal_type(Cmd.send)  # line 17


def edit(field: TextInput) -> str:
    updated, _ = field.update(Key("a"))
    return updated.value


reveal_type(TextInput("name").value)  # line 25
reveal_type(field_for)  # line 26
"""


def _mypy(tmp_path: Path) -> tuple[int, str]:
    source = tmp_path / "plugin.py"
    source.write_text(SNIPPET, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    return proc.returncode, proc.stdout


def test_a_typo_in_an_sdk_name_is_a_type_error_and_screen_keeps_its_type(tmp_path: Path) -> None:
    code, output = _mypy(tmp_path)
    errors = [line for line in output.splitlines() if ": error:" in line]
    assert code == 1, output
    assert sorted(line.split(":")[1] for line in errors) == ["3", "4", "6"], output
    assert all("attr-defined" in line for line in errors), output
    # Screen, Cmd and ui.run are the real types, not ``object``.
    assert "Revealed type is" in output
    assert "def (" in output.split("Revealed type is", 1)[1], output


def test_the_component_names_keep_their_real_types(tmp_path: Path) -> None:
    _, output = _mypy(tmp_path)
    revealed = {
        int(line.split(":")[1]): line.split("Revealed type is ", 1)[1]
        for line in output.splitlines()
        if "Revealed type is" in line
    }

    assert revealed[25] == '"str"', output  # TextInput(...).value, not object
    assert "config_schema.FieldDescriptor" in revealed[26], output  # field_for takes a descriptor
    assert revealed[26].rstrip('"').endswith("-> untaped.screen.components.fields.Field"), output


def _sdk_tree() -> ast.Module:
    return ast.parse(SDK.read_text(encoding="utf-8"))


def _type_checking_imports(tree: ast.Module) -> set[tuple[str, str]]:
    """``(module, name)`` for each name imported under ``if _typing.TYPE_CHECKING:``."""
    found: set[tuple[str, str]] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) in (
            "_typing.TYPE_CHECKING",
            "typing.TYPE_CHECKING",
            "TYPE_CHECKING",
        ):
            for statement in node.body:
                if isinstance(statement, ast.ImportFrom) and statement.module:
                    found.update((statement.module, alias.name) for alias in statement.names)
    return found


def _lazy_exports(tree: ast.Module) -> set[tuple[str, str]]:
    """``(module, name)`` for each entry of the ``_SCREEN_MODULES`` map."""
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_SCREEN_MODULES"
            and node.value is not None
        ):
            table: dict[str, tuple[str, ...]] = ast.literal_eval(node.value)
            return {(module, name) for module, names in table.items() for name in names}
    raise AssertionError("sdk.py has no _SCREEN_MODULES map")


def test_the_type_checker_imports_are_exactly_the_lazy_screen_exports() -> None:
    tree = _sdk_tree()
    typed, lazy = _type_checking_imports(tree), _lazy_exports(tree)

    assert typed, "no TYPE_CHECKING imports found"
    assert lazy - typed == set(), "exported lazily but invisible to the type checker"
    assert typed - lazy == set(), "visible to the type checker but not importable at run time"


def test_the_parity_check_sees_a_missing_import() -> None:
    tree = ast.parse(
        "import typing as _typing\n"
        "if _typing.TYPE_CHECKING:\n    from a.b import X\n"
        "_SCREEN_MODULES: dict[str, tuple[str, ...]] = {'a.b': ('X', 'Y')}\n"
    )

    assert _lazy_exports(tree) - _type_checking_imports(tree) == {("a.b", "Y")}
