"""A kind string is written once, on its model, and ``plugin schema`` can reach every model.

A record's kind is its name on the wire, so it is a string, declared once
with ``class Widget(Record, kind="acme.widget")``. Everywhere else code
names the model (``kind_of(Widget)``, ``accepts=(Widget,)``), so renaming a
kind has one place to change and no line that compares or passes the old
string is left behind. Two checks hold that:

- ``kind-literal``: no string literal in ``packages/*/src`` or
  ``examples/*/src`` equals a declared kind outside its ``kind=``. Only
  declared kinds count, so a doctor id or a settings key never trips it
  unless it collides with a kind (worth renaming). Tests are not scanned:
  they assert the wire by its strings on purpose.
- Declarations are reachable: composing every first-party plugin and the
  examples, building every command and loading every contract offer
  registers exactly the kinds the source declares. A model in a module only
  a command body imports would be invisible to ``plugin schema`` until that
  command ran.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from repo.support import REPO_ROOT


@dataclass(frozen=True)
class Declared:
    """A ``kind=`` class keyword: the kind, its class and the literal holding it."""

    kind: str
    model: str
    node: ast.Constant


def _sources() -> list[Path]:
    return sorted(
        [*REPO_ROOT.glob("packages/*/src/**/*.py"), *REPO_ROOT.glob("examples/*/src/**/*.py")]
    )


def declared_kinds(trees: dict[Path, ast.Module]) -> dict[str, Declared]:
    """Every kind a class keyword declares in ``trees``."""
    found: dict[str, Declared] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if (
                    keyword.arg == "kind"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found[value.value] = Declared(value.value, node.name, value)
    return found


def kind_literals(trees: dict[Path, ast.Module], declared: dict[str, Declared]) -> list[str]:
    """One message per string literal equal to a declared kind, outside its declaration."""
    own = {id(each.node) for each in declared.values()}
    messages = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in declared
                and id(node) not in own
            ):
                model = declared[node.value].model
                messages.append(
                    f'{path}:{node.lineno}: "{node.value}" is the kind {model} declares; '
                    f"name the model (kind_of({model})) so a rename can't miss this line "
                    "[kind-literal]"
                )
    return messages


def _parsed(sources: dict[Path, str]) -> dict[Path, ast.Module]:
    return {path: ast.parse(text, filename=str(path)) for path, text in sources.items()}


def test_no_kind_string_outside_its_declaration() -> None:
    trees = _parsed({path.relative_to(REPO_ROOT): path.read_text() for path in _sources()})
    assert kind_literals(trees, declared_kinds(trees)) == []


_MODEL = """
class TestResult(Record, kind="awx.test_result"):
    name: str
"""

_STRAYS = """
_RESULT_KIND = "awx.test_result"
ACCEPTED = {"awx.test_result", "awx.job"}
VERBS = (("ran", "awx.test_result", "results"),)

def same(envelope):
    return envelope.kind != "awx.test_result"
"""


def test_kind_literal_names_the_model_for_each_stray_string() -> None:
    trees = _parsed({Path("records.py"): _MODEL, Path("cli/suite_commands.py"): _STRAYS})

    messages = kind_literals(trees, declared_kinds(trees))

    assert [message.partition(":")[2].partition(":")[0] for message in messages] == [
        "2",
        "3",
        "4",
        "7",
    ]
    assert messages[0] == (
        'cli/suite_commands.py:2: "awx.test_result" is the kind TestResult declares; '
        "name the model (kind_of(TestResult)) so a rename can't miss this line [kind-literal]"
    )


def test_kind_literal_ignores_strings_no_model_declares() -> None:
    others = """
DoctorCheck(id="awx.connection", title="t", run=run)
render(system="awx.suite")
KEY = "github.default_org"
STATE = "state.yml"
LOGGER = "untaped.http"
"""
    trees = _parsed({Path("records.py"): _MODEL, Path("other.py"): others})

    assert kind_literals(trees, declared_kinds(trees)) == []


_REACHABLE = """
import json, os, sys, tempfile
from pathlib import Path

os.environ["UNTAPED_CONFIG"] = str(Path(tempfile.mkdtemp()) / "config.yml")
sys.path[:0] = {example_src!r}

from untaped.bootstrap import build_root_app, composition
from untaped.contracts._registry import every_offer, owned_contracts
from untaped.plugins.registry import discover_candidates, run_deferred_factory
from untaped.records import record_kinds
from untaped.testing import plugin_candidate
import untaped_hello, untaped_library, untaped_shelf

examples = [
    plugin_candidate(untaped_hello.SPEC),
    plugin_candidate(untaped_shelf.SPEC),
    plugin_candidate(untaped_library.SPEC, requires_dist=["untaped-shelf>=11"]),
]
build_root_app(candidates=[*discover_candidates(), *examples])
assert not composition().quarantine, composition().quarantine
for plugin in composition().plugins:
    if plugin.spec.app_factory is not None:
        run_deferred_factory(plugin)
owned_contracts()
every_offer()
print(json.dumps(sorted(record_kinds())))
"""


def test_every_declared_kind_is_registered_once_its_plugin_is_composed() -> None:
    """``plugin schema`` composes, builds every command and loads every offer: all it can see."""
    example_src = [str(path) for path in sorted(REPO_ROOT.glob("examples/*/src"))]
    proc = subprocess.run(  # a clean interpreter: this one has imported every module already
        [sys.executable, "-c", _REACHABLE.format(example_src=example_src)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    registered = set(json.loads(proc.stdout))
    trees = _parsed({path: path.read_text() for path in _sources()})

    assert registered == set(declared_kinds(trees)), (
        "declare record models in a module the plugin's spec or app factory imports "
        "(a records module), never only inside a command body"
    )
