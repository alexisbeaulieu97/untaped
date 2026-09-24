"""Root management commands tag ``--format pipe`` records with ``untaped.*`` kinds."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from untaped.bootstrap import build_root_app
from untaped.testing import invoke_cli


@pytest.mark.parametrize(
    ("args", "kind"),
    [
        (["config", "list"], "untaped.setting"),
        (["config", "get", "log_level"], "untaped.setting"),
        (["profile", "list"], "untaped.profile"),
        (["capabilities"], "untaped.capability"),
        (["doctor"], "untaped.doctor_check"),
        (["skills", "list"], "untaped.skill"),
    ],
)
def test_root_pipe_records_carry_an_untaped_kind(args: list[str], kind: str) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("profiles:\n  default: {}\n", encoding="utf-8")
    root = build_root_app(externals=[])
    result = invoke_cli(root, [*args, "--format", "pipe"])

    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.output
    assert {line["kind"] for line in lines} == {kind}
