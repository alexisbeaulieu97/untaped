"""What a type checker sees of ``untaped.sdk``: typos are errors, screen names keep their types.

The SDK resolves its screen names lazily (a module-level ``__getattr__``),
which must stay hidden from the type checker: visible, it types every other
attribute as ``object`` and a plugin's misspelled import passes mypy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SNIPPET = """\
import untaped.sdk
from untaped.sdk import Cmd, Screen, UiContext
from untaped.sdk import Scren  # line 3: a typo in an import
from untaped.sdk import emmit  # line 4: a typo in a helper

untaped.sdk.Scren  # line 6: a typo as an attribute


def title(screen: Screen[int, str]) -> str:
    return screen.title


def run(ui: UiContext, screen: Screen[int, str]) -> str:
    return ui.run(screen)


reveal_type(Cmd.send)  # line 17
"""


def _mypy(tmp_path: Path) -> tuple[int, str]:
    source = tmp_path / "plugin.py"
    source.write_text(SNIPPET, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(REPO / "pyproject.toml"), str(source)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
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
