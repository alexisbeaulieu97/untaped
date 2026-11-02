"""AWX-specific layering rule: application code reads only domain ``ResourceSpec`` fields.

The generic one-way layer and settings rules live in
``untaped_lint.providers.conventions.layering``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from untaped_awx.domain.spec import ResourceSpec
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec

APPLICATION_DIR = Path(__file__).resolve().parents[3] / "src/untaped_awx/application"


def test_application_does_not_read_infrastructure_only_spec_fields() -> None:
    """Fields only ``AwxResourceSpec`` (infrastructure) carries stay out of ``application/``.

    The infra-only set is derived from both models, so a new infra-only field
    is guarded automatically. Matching is by attribute name only.
    """
    infra_only = frozenset(AwxResourceSpec.model_fields.keys() - ResourceSpec.model_fields.keys())
    assert infra_only
    violations = [
        f"{py_file.relative_to(APPLICATION_DIR)}:{node.lineno} reads .{node.attr}"
        for py_file in sorted(APPLICATION_DIR.rglob("*.py"))
        for node in ast.walk(ast.parse(py_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr in infra_only
    ]
    assert not violations, "\n".join(violations)


#: Imports of awx's commands from inside a function of ``application/suites/``, and why.
LAZY_COMMAND_IMPORTS = {
    # A pending approval's hint spells awx's cancel command, and the commands import the runner.
    ("runner.py", "untaped_awx.commands.job_actions"),
    # A temporary copy left behind is pruned with ``awx test prune``, which the hint spells.
    ("temporary_set.py", "untaped_awx.commands.test"),
}


def _imported(node: ast.AST) -> list[str]:
    """The modules ``node`` imports; ``from a import b`` names ``a``, then ``a.b``."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    return []


def _is_commands(module: str) -> bool:
    return module == "untaped_awx.commands" or module.startswith("untaped_awx.commands.")


def test_the_suite_runner_never_imports_the_commands_at_module_level() -> None:
    """``application/suites/`` runs under awx's commands; it names them only lazily, for a hint."""
    suites = APPLICATION_DIR / "suites"
    eager: list[str] = []
    lazy: set[tuple[str, str]] = set()
    for py_file in sorted(suites.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        inside = {
            id(node)
            for function in ast.walk(tree)
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
            for node in ast.walk(function)
        }
        for node in ast.walk(tree):
            module = next(filter(_is_commands, _imported(node)), None)
            if module is None:
                continue
            name = py_file.relative_to(suites).as_posix()
            if id(node) in inside:
                lazy.add((name, module))
            else:
                eager.append(f"{name}:{node.lineno} imports {module}")
    assert not eager, eager
    assert lazy == LAZY_COMMAND_IMPORTS
