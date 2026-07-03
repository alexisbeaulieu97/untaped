"""Pin SDK ``--format raw`` first-key row contracts."""

from __future__ import annotations

import ast
from pathlib import Path

from untaped.config import SettingEntry, Source
from untaped.config.app import _entry_to_row

_CONTRACT_REF = "see AGENTS.md '--format raw default-column contract'"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_config_list_row_first_key_is_key() -> None:
    row = _entry_to_row(
        SettingEntry(
            key="log_level",
            value="INFO",
            default="INFO",
            source=Source(kind="default"),
        )
    )

    assert next(iter(row.keys())) == "key"


def test_config_list_command_calls_row_helper() -> None:
    source = _REPO_ROOT / "src/untaped/config/app.py"
    tree = ast.parse(source.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_list":
            callees = {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
            assert "_entry_to_row" in callees, (
                "config list no longer calls '_entry_to_row' — the helper-level "
                f"pin would point at dead code. Restore the call or update the catalogue "
                f"({_CONTRACT_REF})."
            )
            return
    raise AssertionError(f"function '_list' not found in {source}")
