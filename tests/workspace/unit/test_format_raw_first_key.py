"""Pin the workspace tool's ``--format raw`` first-key contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker


@pytest.mark.usefixtures("isolate_config")
def test_list_raw_first_key_is_name(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "alpha", "--path", str(tmp_path / "alpha")])

    result = runner.invoke(app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "alpha"
