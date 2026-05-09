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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
        ["uv", "run", "lint-imports"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_independence_contract_catches_cross_domain_import() -> None:
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
    assert "Sibling domains are mutually independent" in result.stdout
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
    assert "Per-domain layers" in result.stdout
    assert "BROKEN" in result.stdout


def test_forbidden_contract_catches_core_importing_domain() -> None:
    fixture = REPO_ROOT / "packages/untaped-core/src/untaped_core/_contract_self_test_violation.py"
    with _injected_violation(
        fixture,
        "from untaped_awx import app  # noqa: F401\n",
    ):
        result = _run_lint_imports()

    assert result.returncode != 0, (
        f"expected non-zero exit; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "untaped_core does not depend on any domain" in result.stdout
    assert "BROKEN" in result.stdout
