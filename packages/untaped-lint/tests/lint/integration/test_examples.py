"""The example plugins: lint finds what ``plugin check`` recorded before lint replaced it.

``tests/fixtures/plugin_check/<name>.json`` is ``untaped plugin check <name>
--format json`` from before lint, with ``library.volumes`` configured as CI
does. The examples run in their own process (``examples_run.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plugin_check"
SCRIPT = Path(__file__).with_name("examples_run.py")
CONFIG = (
    "profiles:\n  default:\n    library:\n      volumes:\n        - {shelf_mark: A1, name: Dune}\n"
)
_DRIFT = "owner-schema-drift: "


def _run(home: Path) -> dict[str, Any]:
    config = home / "config.yml"
    config.write_text(CONFIG, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        cwd=home,
        env={**os.environ, "UNTAPED_CONFIG": str(config)},
    )
    assert done.returncode == 0, done.stderr
    result: dict[str, Any] = json.loads(done.stdout)
    return result


@pytest.fixture(scope="module")
def examples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run(tmp_path_factory.mktemp("examples"))


def _recorded(name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return rows


def _as_finding(row: dict[str, str]) -> tuple[str, str]:
    """A recorded row as Conventions reports it: ``warn`` a warning, ``fail`` an error."""
    if row["check"] == "conventions":
        rule = row["title"]
    elif row["check"] == "schema" and row["detail"].startswith(_DRIFT):
        rule = "owner-schema-drift"
    else:
        rule = row["check"]
    return f"lint.{rule}", "warning" if row["status"] == "warn" else "error"


@pytest.mark.parametrize("name", ["hello", "shelf", "library"])
def test_lint_reports_what_plugin_check_recorded(examples: dict[str, Any], name: str) -> None:
    expected = {_as_finding(row) for row in _recorded(name) if row["status"] != "pass"}
    assert {tuple(each) for each in examples[name]["findings"]} == expected
    assert examples[name]["failures"] == []
