"""Structure tests for the workspace CLI module split."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_DIR = _REPO_ROOT / "src/untaped_workspace/cli"

_EXPECTED_COMMAND_FUNCTIONS: dict[str, set[str]] = {
    "branch_commands.py": {
        "branch_set_command",
        "branch_unset_command",
        "branch_apply_command",
    },
    "lifecycle_commands.py": {
        "init_command",
        "adopt_command",
        "forget_command",
        "import_command",
    },
    "ops_commands.py": {
        "sync_command",
        "status_command",
        "foreach_command",
    },
    "repo_commands.py": {
        "add_command",
        "remove_command",
    },
    "ux_commands.py": {
        "list_command",
        "show_command",
        "path_command",
        "shell_init_command",
        "edit_command",
    },
}


def _function_names(source: Path) -> set[str]:
    tree = ast.parse(source.read_text())
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_workspace_cli_commands_are_split_by_concern() -> None:
    for module_name, expected_functions in _EXPECTED_COMMAND_FUNCTIONS.items():
        source = _CLI_DIR / module_name
        assert source.is_file(), f"expected split CLI module {source.relative_to(_REPO_ROOT)}"
        assert expected_functions <= _function_names(source)

    root_functions = _function_names(_CLI_DIR / "commands.py")
    assert root_functions == {"_callback"}
