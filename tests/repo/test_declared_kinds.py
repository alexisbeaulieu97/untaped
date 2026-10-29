"""A kind string is never written: composition names it, and code names the model.

A record's kind is its name on the wire, ``<plugin>.<snake_case class
name>``, which composition derives from the plugin that lists or prints the
model. Code names the model (``kind_of(Widget)``, ``Stdin[Widget]``), so
renaming a kind has one place to change and no line that compares or passes
the old string is left behind. ``kind-literal`` holds that: no string
literal in ``packages/*/src`` or ``examples/*/src`` equals a kind the
first-party plugins and the examples compose. Only composed kinds count, so
a doctor id or a settings key never trips it unless it collides with a kind
(worth renaming). Tests are not scanned: they assert the wire by its strings
on purpose.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from repo.support import REPO_ROOT, everything_composed


def _sources() -> list[Path]:
    return sorted(
        [*REPO_ROOT.glob("packages/*/src/**/*.py"), *REPO_ROOT.glob("examples/*/src/**/*.py")]
    )


def composed_kinds() -> dict[str, str]:
    """Each kind the installed plugins and the examples compose, and its model's name."""
    with everything_composed() as composition:
        kinds = composition.kinds
    return {kind: target.rpartition(":")[2].rpartition(".")[2] for target, kind in kinds.items()}


def kind_literals(trees: dict[Path, ast.Module], kinds: Mapping[str, str]) -> list[str]:
    """One message per string literal equal to a composed kind (``kinds``: kind to model)."""
    messages = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                model = kinds.get(node.value)
                if model is not None:
                    messages.append(
                        f'{path}:{node.lineno}: "{node.value}" is the kind of {model}; '
                        f"name the model (kind_of({model})) so a rename can't miss this line "
                        "[kind-literal]"
                    )
    return messages


def _parsed(sources: dict[Path, str]) -> dict[Path, ast.Module]:
    return {path: ast.parse(text, filename=str(path)) for path, text in sources.items()}


def test_no_kind_string_in_the_source() -> None:
    trees = _parsed({path.relative_to(REPO_ROOT): path.read_text() for path in _sources()})
    assert kind_literals(trees, composed_kinds()) == []


_KINDS = {"awx.test_result": "TestResult"}

_STRAYS = """
_RESULT_KIND = "awx.test_result"
ACCEPTED = {"awx.test_result", "awx.job"}
VERBS = (("ran", "awx.test_result", "results"),)

def same(envelope):
    return envelope.kind != "awx.test_result"
"""


def test_kind_literal_names_the_model_for_each_stray_string() -> None:
    messages = kind_literals(_parsed({Path("cli/suite_commands.py"): _STRAYS}), _KINDS)

    assert [message.partition(":")[2].partition(":")[0] for message in messages] == [
        "2",
        "3",
        "4",
        "7",
    ]
    assert messages[0] == (
        'cli/suite_commands.py:2: "awx.test_result" is the kind of TestResult; '
        "name the model (kind_of(TestResult)) so a rename can't miss this line [kind-literal]"
    )


def test_kind_literal_ignores_strings_no_model_has() -> None:
    others = """
DoctorCheck(id="awx.connection", title="t", run=run)
render(system="awx.suite")
KEY = "github.default_org"
STATE = "state.yml"
LOGGER = "untaped.http"
"""
    assert kind_literals(_parsed({Path("other.py"): others}), _KINDS) == []


def test_plugin_schema_prints_a_core_kind_in_a_fresh_process(tmp_path: Path) -> None:
    """S3c: a kind core declares for a management command is there before the command runs."""
    proc = subprocess.run(
        [sys.executable, "-m", "untaped", "plugin", "schema", "untaped.plugin_check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "UNTAPED_HOME": str(tmp_path)},
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["$id"] == "untaped.plugin_check"
