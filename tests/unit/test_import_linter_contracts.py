"""Self-tests for the ``[tool.importlinter]`` contracts.

The contracts in ``pyproject.toml`` only matter if they actually catch
what they claim. Each test injects a deliberate violation into a real
source tree, runs ``uv run lint-imports``, and asserts that (1) the
linter exits non-zero, and (2) the right contract reports ``BROKEN``.
Without these tests, a typo in ``pyproject.toml`` (a missing module
from ``modules``, an inverted ``forbidden`` source/target, a renamed
contract that quietly stops matching) could silently degrade a contract
to a no-op and the green build wouldn't notice — exactly the failure
mode this whole quality gate exists to prevent.
"""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def _injected_violation(path: Path, source: str) -> Iterator[None]:
    """Write ``source`` to ``path``, yield, then unconditionally remove it.

    The ``finally`` cleanup runs even if the assertion blows up, so a
    failing test cannot orphan a violation file that would then break
    every subsequent ``uv run lint-imports`` invocation in the workspace.
    """
    assert not path.exists(), f"fixture path already exists: {path}"
    path.write_text(source, encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _run_lint_imports() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "--cache-dir", ".uv-cache", "run", "--no-sync", "lint-imports"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_independence_contract_catches_cross_plugin_import() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    plugin_modules = pyproject["tool"]["untaped"]["plugin_modules"]
    if len(plugin_modules) < 2:
        pytest.skip("sibling independence contract is vacuous with fewer than two in-repo plugins")

    fixture = REPO_ROOT / "packages/untaped-awx/src/untaped_awx/_contract_self_test_violation.py"
    with _injected_violation(
        fixture,
        "from untaped_workspace import app  # noqa: F401\n",
    ):
        result = _run_lint_imports()

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Sibling plugins are mutually independent" in result.stdout
    assert "BROKEN" in result.stdout


def test_layers_contract_catches_application_imports_infrastructure() -> None:
    fixture = (
        REPO_ROOT
        / "packages/untaped-awx/src/untaped_awx/application/_contract_self_test_violation.py"
    )
    with _injected_violation(
        fixture,
        "from untaped_awx.infrastructure import awx_client  # noqa: F401\n",
    ):
        result = _run_lint_imports()

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Per-plugin layers" in result.stdout
    assert "BROKEN" in result.stdout


def test_forbidden_contract_catches_core_importing_plugin() -> None:
    fixture = REPO_ROOT / "src/untaped/_contract_self_test_violation.py"
    with _injected_violation(
        fixture,
        "from untaped_awx import app  # noqa: F401\n",
    ):
        result = _run_lint_imports()

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "untaped core does not statically import plugins" in result.stdout
    assert "BROKEN" in result.stdout
