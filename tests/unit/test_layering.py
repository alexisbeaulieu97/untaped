"""Architectural-rule test: enforce DDD import direction across every domain.

The rule (per ``AGENTS.md`` 4-layer DDD section): ``application/`` modules
must not import their package's ``infrastructure`` namespace *at runtime*.
``TYPE_CHECKING`` imports are allowed because they don't create a runtime
edge.

This test discovers every domain package by globbing
``packages/*/src/<import_root>/application/``, walks the AST of every
``.py`` file in those directories, and asserts the rule for each. The
discovery is intentional: a new domain that follows the recipe in
``AGENTS.md`` is automatically covered with no test edits.

``untaped-core`` has no ``application/`` directory by design (it's a
flat shared kit), so it is excluded automatically.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"


def _discover_application_dirs() -> list[tuple[str, Path]]:
    """Return ``(import_root, application_dir)`` pairs for every domain.

    Globs ``packages/*/src/*/application/``; the parent directory's name
    is the package import root (``untaped_awx`` etc.). Returned sorted so
    test order is deterministic.
    """
    pairs: list[tuple[str, Path]] = []
    for app_dir in sorted(PACKAGES_DIR.glob("*/src/*/application")):
        if not app_dir.is_dir():
            continue
        import_root = app_dir.parent.name
        pairs.append((import_root, app_dir))
    return pairs


def _is_type_checking_guard(test: ast.expr) -> bool:
    # Handles both `if TYPE_CHECKING:` and `if typing.TYPE_CHECKING:`.
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return (
            test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id == "typing"
        )
    return False


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Return ``Import`` / ``ImportFrom`` nodes that execute at runtime.

    Skips imports inside ``if TYPE_CHECKING:`` blocks — those are evaluated
    only by type checkers, never at runtime, so they don't violate the
    layering contract. Includes both ``import x.y.z`` (``ast.Import``) and
    ``from x.y.z import ...`` (``ast.ImportFrom``) so neither form can
    bypass the rule.
    """
    typecheck_block_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    typecheck_block_lines.add(child.lineno)

    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and node.lineno not in typecheck_block_lines
    ]


def _violations_in_file(import_root: str, py_file: Path, application_dir: Path) -> list[str]:
    infra_root = f"{import_root}.infrastructure"
    rel = py_file.relative_to(application_dir.parent)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.Import):
            bad = [alias.name for alias in imp.names if alias.name.startswith(infra_root)]
            if bad:
                found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")
        elif imp.level > 0:
            # Relative import (`from ..infrastructure...`). Resolve against
            # the application/ package: any non-zero level pointing into a
            # sibling `infrastructure` package counts.
            module = imp.module or ""
            if module.startswith("infrastructure") or "infrastructure" in module:
                found.append(f"{rel}:{imp.lineno} imports {'.' * imp.level}{module}")
        elif imp.module and imp.module.startswith(infra_root):
            found.append(f"{rel}:{imp.lineno} imports {imp.module}")
    return found


@pytest.mark.parametrize(
    ("import_root", "application_dir"),
    _discover_application_dirs(),
    ids=lambda value: value if isinstance(value, str) else value.parent.name,
)
def test_application_does_not_import_infrastructure_at_runtime(
    import_root: str, application_dir: Path
) -> None:
    violations: list[str] = []
    for py_file in sorted(application_dir.rglob("*.py")):
        violations.extend(_violations_in_file(import_root, py_file, application_dir))

    assert not violations, (
        f"{import_root}/application must not import {import_root}.infrastructure "
        "at runtime (TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )


def test_awx_application_does_not_read_infrastructure_only_spec_fields() -> None:
    """``untaped_awx/application/`` must not read fields that exist only on
    ``AwxResourceSpec`` (infrastructure), not on the domain ``ResourceSpec``.

    The infra-only field set is derived from the two Pydantic models so a
    future infra-only field is automatically guarded — no test edit needed.

    The matcher is by attribute *name* only, regardless of receiver type.
    Today no ``application/`` file has an unrelated ``.api_path`` /
    ``.cli_name`` / ``.list_columns`` / ``.commands`` access, so this is
    precise enough. If a future use case legitimately needs one of those
    names on an unrelated object, rename the local field rather than
    silencing this test — or add an explicit ``(file, lineno)`` allowlist.
    Don't tighten the matcher to ``node.value.id == "spec"`` (parameter
    renames silently break the guard) and don't widen the scope back to
    every domain (the names are AWX-specific — a ``.commands`` access in
    another package would false-positive).
    """
    from untaped_awx.domain.spec import ResourceSpec
    from untaped_awx.infrastructure.spec import AwxResourceSpec

    infra_only = frozenset(AwxResourceSpec.model_fields.keys() - ResourceSpec.model_fields.keys())
    assert infra_only, "expected AwxResourceSpec to add fields beyond ResourceSpec"

    application_dir = PACKAGES_DIR / "untaped-awx" / "src" / "untaped_awx" / "application"
    violations: list[str] = []
    for py_file in sorted(application_dir.rglob("*.py")):
        rel = py_file.relative_to(application_dir.parent)
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in infra_only:
                violations.append(f"{rel}:{node.lineno} reads .{node.attr}")

    assert not violations, (
        f"untaped_awx/application must not read AwxResourceSpec-only fields "
        f"({sorted(infra_only)}). These live on AwxResourceSpec (infrastructure), "
        f"not the domain ResourceSpec — reading them couples application logic to "
        f"transport/CLI wiring.\n  " + "\n  ".join(violations)
    )


# Workspace packages that intentionally have no ``application/`` layer
# (flat shared kits without DDD layering). Every other package in
# ``packages/`` must have one — adding a new domain without one trips
# the guard below, prompting the contributor to either add the layer or
# document the exception by listing the package here.
_PACKAGES_WITHOUT_APPLICATION_LAYER = frozenset({"untaped_core"})


def _discover_package_roots() -> list[tuple[str, Path]]:
    """Return ``(import_root, src_dir)`` for every workspace package."""
    return [
        (src_dir.name, src_dir)
        for src_dir in sorted(PACKAGES_DIR.glob("*/src/*"))
        if src_dir.is_dir()
    ]


def test_every_domain_has_application_layer() -> None:
    """Every workspace package except known flat kits must have an
    ``application/`` directory.

    Derives the expected set from disk so a new domain that follows the
    recipe in ``AGENTS.md`` is automatically covered without test edits.
    A new domain *without* an ``application/`` layer trips this guard.
    """
    missing = [
        f"{import_root} ({src_dir})"
        for import_root, src_dir in _discover_package_roots()
        if import_root not in _PACKAGES_WITHOUT_APPLICATION_LAYER
        and not (src_dir / "application").is_dir()
    ]
    assert not missing, (
        "packages without application/ (add the layer or list in "
        f"_PACKAGES_WITHOUT_APPLICATION_LAYER): {missing}"
    )


# AGENTS.md: "Only ``cli/`` modules read ``untaped_core.Settings``."
# Infrastructure adapters must accept a package-local config struct
# (e.g. ``AwxConfig``, ``GithubConfig``) instead, so they can be
# constructed in tests without touching the global settings cache.
#
# Allowlist entries live below ``infrastructure/`` and are written as
# ``"<import_root>/<rel_path_under_src>"`` (POSIX separators):
_INFRA_MAY_READ_SETTINGS: frozenset[str] = frozenset(
    {
        # Meta-domain: the whole purpose of ``untaped-config`` is to read
        # and edit ``Settings``; the introspection adapter has to import it.
        "untaped_config/infrastructure/settings_repo.py",
        # Pre-existing tech debt: ``cache_path_for`` falls back to
        # ``get_settings().workspace.cache_dir`` when no ``cache_dir``
        # kwarg is passed. Callers (``GitRunner.ensure_bare``) already
        # accept ``cache_dir``; remove the fallback and drop this entry
        # in a follow-up PR.
        "untaped_workspace/infrastructure/bare_cache.py",
    }
)


def _discover_infrastructure_dirs() -> list[tuple[str, Path]]:
    """Return ``(import_root, infrastructure_dir)`` pairs for every domain."""
    pairs: list[tuple[str, Path]] = []
    for infra_dir in sorted(PACKAGES_DIR.glob("*/src/*/infrastructure")):
        if not infra_dir.is_dir():
            continue
        import_root = infra_dir.parent.name
        pairs.append((import_root, infra_dir))
    return pairs


def _settings_violations_in_file(py_file: Path, src_dir: Path) -> list[str]:
    """Return ``"file:line imports X"`` strings for forbidden Settings imports.

    Forbidden:
      - ``from untaped_core import Settings`` / ``get_settings``
      - ``from untaped_core.settings import Settings`` / ``get_settings``

    Plain ``import untaped_core`` is NOT flagged — it doesn't pull
    ``Settings`` into the namespace and adapters that need ``HttpSettings``
    (which is fine) would otherwise be tripped.
    """
    forbidden_names = frozenset({"Settings", "get_settings"})
    rel = py_file.relative_to(src_dir.parent)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if not isinstance(imp, ast.ImportFrom):
            continue
        if imp.module not in {"untaped_core", "untaped_core.settings"}:
            continue
        bad = sorted({alias.name for alias in imp.names if alias.name in forbidden_names})
        if bad:
            found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)} from {imp.module}")
    return found


@pytest.mark.parametrize(
    ("import_root", "infrastructure_dir"),
    _discover_infrastructure_dirs(),
    ids=lambda value: value if isinstance(value, str) else value.parent.name,
)
def test_infrastructure_does_not_read_settings(import_root: str, infrastructure_dir: Path) -> None:
    """Infrastructure adapters must not import ``Settings`` / ``get_settings``.

    AGENTS.md: only ``cli/`` modules read ``untaped_core.Settings``;
    everything downstream consumes a package-local config struct (e.g.
    :class:`untaped_awx.infrastructure.AwxConfig`,
    :class:`untaped_github.infrastructure.GithubConfig`). Adapters that
    read settings directly couple to the global cache and can't be
    constructed in unit tests without monkey-patching it.

    Documented exceptions live in ``_INFRA_MAY_READ_SETTINGS``. New
    violations should be fixed (composition root reads settings, builds
    a config, passes it to the adapter); only add to the allowlist with
    a rationale comment.
    """
    src_dir = infrastructure_dir.parent
    violations: list[str] = []
    for py_file in sorted(infrastructure_dir.rglob("*.py")):
        rel_under_src = py_file.relative_to(src_dir.parent).as_posix()
        if rel_under_src in _INFRA_MAY_READ_SETTINGS:
            continue
        violations.extend(_settings_violations_in_file(py_file, src_dir))

    assert not violations, (
        f"{import_root}/infrastructure must not import Settings / get_settings "
        "from untaped_core (only cli/ may read settings; pass a package-local "
        "config struct in instead). To document an intentional exception, add "
        "the path to _INFRA_MAY_READ_SETTINGS above with a rationale.\n  " + "\n  ".join(violations)
    )
