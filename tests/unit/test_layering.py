"""Architectural-rule tests: enforce DDD import direction across every domain.

Two complementary rules (per ``AGENTS.md`` 4-layer DDD section):

- ``application/`` modules must not import their package's
  ``infrastructure`` namespace at runtime.
- ``infrastructure/`` modules must not import their package's
  ``application`` namespace at runtime — concrete adapters speak port
  ``Protocol`` shapes structurally, never importing from ``application/``.

``TYPE_CHECKING`` imports are allowed because they don't create a runtime
edge.

These tests discover every domain package by globbing
``packages/*/src/<import_root>/{application,infrastructure}/``, walk the
AST of every ``.py`` file in those directories, and assert the rules for
each. The discovery is intentional: a new domain that follows the recipe
in ``AGENTS.md`` is automatically covered with no test edits.

``untaped-core`` has no ``application/`` or ``infrastructure/`` directory
by design (it's a flat shared kit), so it is excluded automatically.

These tests partially overlap with the ``[tool.importlinter]`` contracts
in ``pyproject.toml`` (defense-in-depth on layers) but uniquely cover
what the contracts can't: the ``Settings``/``get_settings`` reach-around
(with alias-bypass detection — see :func:`_settings_violations_in_file`)
and the AWX-specific ``infrastructure``-only-spec-field guard. The
import-linter contracts cover layers, sibling-independence, and the
``core → siblings`` ban at the graph level. The contracts themselves are
self-tested in ``test_import_linter_contracts.py``. Keep all three in
sync if the rules change.
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


def _typecheck_block_lines(tree: ast.Module) -> set[int]:
    """Return line numbers belonging to ``if TYPE_CHECKING:`` blocks.

    Only the *if* branch (``node.body``) is type-check-only — the
    ``else`` branch executes at runtime when ``TYPE_CHECKING`` is False,
    so its statements must not be excluded from runtime-import
    scanning. Walking the whole ``If`` node would conflate the two and
    let a contributor smuggle a forbidden import through ``else:``.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for stmt in node.body:
                for child in ast.walk(stmt):
                    if hasattr(child, "lineno"):
                        lines.add(child.lineno)
    return lines


def _runtime_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Return ``Import`` / ``ImportFrom`` nodes that execute at runtime.

    Skips imports inside ``if TYPE_CHECKING:`` blocks — those are evaluated
    only by type checkers, never at runtime, so they don't violate the
    layering contract. Includes both ``import x.y.z`` (``ast.Import``) and
    ``from x.y.z import ...`` (``ast.ImportFrom``) so neither form can
    bypass the rule.
    """
    typecheck_block_lines = _typecheck_block_lines(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and node.lineno not in typecheck_block_lines
    ]


def _violations_in_file(
    import_root: str,
    py_file: Path,
    source_dir: Path,
    forbidden_subpackage: str,
) -> list[str]:
    forbidden_root = f"{import_root}.{forbidden_subpackage}"
    rel = py_file.relative_to(source_dir.parent)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: list[str] = []
    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.Import):
            bad = [
                alias.name
                for alias in imp.names
                if alias.name == forbidden_root or alias.name.startswith(f"{forbidden_root}.")
            ]
            if bad:
                found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)}")
        elif imp.level > 0:
            # Relative import (`from ..<forbidden>...`). Resolve against
            # the source package: any non-zero level pointing into the
            # forbidden subpackage (or a submodule of it) counts. Match
            # exact-or-dotted-prefix to avoid false-positives on sibling
            # names like ``application_helper`` that share a substring.
            module = imp.module or ""
            if module == forbidden_subpackage or module.startswith(f"{forbidden_subpackage}."):
                found.append(f"{rel}:{imp.lineno} imports {'.' * imp.level}{module}")
        elif imp.module and (
            imp.module == forbidden_root or imp.module.startswith(f"{forbidden_root}.")
        ):
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
        violations.extend(
            _violations_in_file(
                import_root,
                py_file,
                application_dir,
                forbidden_subpackage="infrastructure",
            )
        )

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


# C901: layering contract walks the AST for the three forbidden Settings
# read forms — direct import, attribute access on ``untaped_core.settings``,
# alias rebinding. One branch per recognised form; refactoring would
# obscure the 1:1 mapping between contract clause and detector.
def _settings_violations_in_file(py_file: Path, src_dir: Path) -> list[str]:  # noqa: C901
    """Return ``"file:line ..."`` strings for forbidden Settings reads.

    Direct imports (flagged at the import site):
      - ``from untaped_core import Settings`` / ``get_settings``
      - ``from untaped_core.settings import Settings`` / ``get_settings``

    Module-alias bypasses (flagged at the *attribute access* site, since
    the import alone is harmless):
      - ``import untaped_core`` → ``untaped_core.get_settings(...)``
      - ``import untaped_core as c`` → ``c.Settings(...)``
      - ``import untaped_core.settings`` → ``untaped_core.settings.get_settings(...)``
      - ``import untaped_core.settings as s`` → ``s.get_settings(...)``
      - ``from untaped_core import settings`` → ``settings.get_settings(...)``

    Plain ``import untaped_core`` *without* a ``Settings`` /
    ``get_settings`` attribute access is fine — adapters legitimately use
    ``HttpSettings`` and other public re-exports.
    """
    forbidden_names = frozenset({"Settings", "get_settings"})
    rel = py_file.relative_to(src_dir.parent)
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    typecheck_lines = _typecheck_block_lines(tree)
    found: list[str] = []

    # Local names bound to ``untaped_core`` (the package) and
    # ``untaped_core.settings`` (the submodule). Tracked so attribute
    # access through aliases (``c.get_settings``, ``s.Settings``) is
    # caught even when the import line itself is harmless.
    top_aliases: set[str] = set()
    sub_aliases: set[str] = set()

    for imp in _runtime_imports(tree):
        if isinstance(imp, ast.ImportFrom):
            if imp.module in {"untaped_core", "untaped_core.settings"}:
                bad = sorted({alias.name for alias in imp.names if alias.name in forbidden_names})
                if bad:
                    found.append(f"{rel}:{imp.lineno} imports {', '.join(bad)} from {imp.module}")
            if imp.module == "untaped_core":
                for alias in imp.names:
                    if alias.name == "settings":
                        sub_aliases.add(alias.asname or "settings")
        elif isinstance(imp, ast.Import):
            for alias in imp.names:
                if alias.name == "untaped_core":
                    top_aliases.add(alias.asname or "untaped_core")
                elif alias.name == "untaped_core.settings":
                    if alias.asname:
                        sub_aliases.add(alias.asname)
                    else:
                        # ``import untaped_core.settings`` binds the
                        # top-level ``untaped_core`` name; the submodule
                        # is reached via attribute access.
                        top_aliases.add("untaped_core")

    if top_aliases or sub_aliases:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.lineno in typecheck_lines:
                continue
            if node.attr not in forbidden_names:
                continue
            # Direct: ``<alias>.Settings`` / ``<alias>.get_settings``.
            if isinstance(node.value, ast.Name):
                name = node.value.id
                if name in top_aliases or name in sub_aliases:
                    found.append(f"{rel}:{node.lineno} reads {name}.{node.attr}")
            # Chained: ``<top>.settings.Settings`` / ``<top>.settings.get_settings``.
            elif isinstance(node.value, ast.Attribute) and node.value.attr == "settings":
                inner = node.value.value
                if isinstance(inner, ast.Name) and inner.id in top_aliases:
                    found.append(f"{rel}:{node.lineno} reads {inner.id}.settings.{node.attr}")
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


@pytest.mark.parametrize(
    ("import_root", "infrastructure_dir"),
    _discover_infrastructure_dirs(),
    ids=lambda value: value if isinstance(value, str) else value.parent.name,
)
def test_infrastructure_does_not_import_application_at_runtime(
    import_root: str, infrastructure_dir: Path
) -> None:
    """``infrastructure/`` modules must not import their package's
    ``application`` namespace at runtime.

    AGENTS.md (root, "Architecture: 4-Layer DDD"): concrete adapters speak
    port shapes structurally — they don't import from ``application/``.
    Use cases declare port ``Protocol`` s in ``application/ports.py``;
    adapters in ``infrastructure/`` satisfy them by structural typing.
    ``TYPE_CHECKING`` imports are allowed because they don't create a
    runtime edge.
    """
    violations: list[str] = []
    for py_file in sorted(infrastructure_dir.rglob("*.py")):
        violations.extend(
            _violations_in_file(
                import_root,
                py_file,
                infrastructure_dir,
                forbidden_subpackage="application",
            )
        )

    assert not violations, (
        f"{import_root}/infrastructure must not import {import_root}.application "
        "at runtime (TYPE_CHECKING imports are fine):\n  " + "\n  ".join(violations)
    )


# Patterns the helper must catch. Each entry is (label, source). Sources
# simulate files written by a future contributor trying to bypass the
# direct-import check via module aliases or chained attribute access.
_BYPASS_SOURCES: list[tuple[str, str]] = [
    (
        "import-alias-direct",
        "import untaped_core as core\ndef f() -> None:\n    core.get_settings()\n",
    ),
    (
        "import-alias-class",
        "import untaped_core as core\ndef f() -> None:\n    core.Settings()\n",
    ),
    (
        "from-import-submodule",
        "from untaped_core import settings\ndef f() -> None:\n    settings.get_settings()\n",
    ),
    (
        "from-import-submodule-aliased",
        "from untaped_core import settings as cfg\ndef f() -> None:\n    cfg.get_settings()\n",
    ),
    (
        "import-submodule-chained",
        "import untaped_core.settings\n"
        "def f() -> None:\n"
        "    untaped_core.settings.get_settings()\n",
    ),
    (
        "import-submodule-aliased",
        "import untaped_core.settings as s\ndef f() -> None:\n    s.Settings()\n",
    ),
    (
        "direct-import",
        "from untaped_core import get_settings\ndef f() -> None:\n    get_settings()\n",
    ),
    (
        # Regression: only the `if TYPE_CHECKING:` branch is type-check-only.
        # An import in the `else:` branch executes at runtime and must be
        # flagged. Walking the whole ``If`` node (instead of just ``node.body``)
        # would let this slip through.
        "type-checking-else-branch",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from untaped_core import HttpSettings\n"
        "else:\n"
        "    from untaped_core import get_settings\n"
        "def f() -> None:\n"
        "    get_settings()\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    _BYPASS_SOURCES,
    ids=[lbl for lbl, _ in _BYPASS_SOURCES],
)
def test_settings_violation_helper_catches_alias_bypasses(
    tmp_path: Path, label: str, source: str
) -> None:
    """``_settings_violations_in_file`` must flag every alias-bypass form.

    The direct ``from untaped_core import get_settings`` form is an
    existing case kept here so the parametrised set is self-contained;
    the rest are the patterns added in response to the PR review.
    """
    src_dir = tmp_path / "untaped_fake"
    infra_dir = src_dir / "infrastructure"
    infra_dir.mkdir(parents=True)
    py_file = infra_dir / "client.py"
    py_file.write_text(source, encoding="utf-8")

    violations = _settings_violations_in_file(py_file, src_dir)
    assert violations, f"expected {label} pattern to be flagged"


def test_settings_violation_helper_ignores_legitimate_imports(tmp_path: Path) -> None:
    """``HttpSettings`` / ``ConfigError`` re-exports and ``HttpClient``
    construction must not be flagged — they're the canonical adapter shape.
    """
    src_dir = tmp_path / "untaped_fake"
    infra_dir = src_dir / "infrastructure"
    infra_dir.mkdir(parents=True)
    py_file = infra_dir / "client.py"
    py_file.write_text(
        "from untaped_core import ConfigError, HttpClient, HttpSettings\n"
        "import untaped_core\n"
        "def f() -> None:\n"
        "    untaped_core.HttpClient(base_url='x')\n",
        encoding="utf-8",
    )

    assert _settings_violations_in_file(py_file, src_dir) == []
