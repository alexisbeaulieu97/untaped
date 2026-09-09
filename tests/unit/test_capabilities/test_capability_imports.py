"""Kernel-only import boundary for capability code (spec §§2, 6(b), Wave 1.6).

Files under ``src/untaped/capabilities/<name>/`` are provider-side code:
every ``untaped``-rooted import in them must resolve to the kernel-only
surface — ``untaped.capability_api`` (the stable provider surface),
``untaped.api`` (the transitional v1 SDK path the in-repo capabilities
import from today), or the capability's own subtree. Anything else
(kernel internals by module path, the composition kernel
``untaped.capabilities.registry``, sibling capabilities, or the bare
``untaped`` root) fails this suite.

The approved helpers re-exported by ``untaped.capability_api`` are
pinned below as an explicit allowlist. Unapproved additions fail here
as well as in ``test_capability_api.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import untaped.capability_api as capability_api

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_SRC = REPO_ROOT / "src" / "untaped" / "capabilities"

COMPOSITION_NAMES = frozenset(
    {
        "ApplicationSpec",
        "CapabilitySpec",
        "CapabilityProvider",
        "CAPABILITY_API_VERSION",
        "SkillAsset",
        "DoctorCheck",
        "DoctorResult",
        "CapabilityContext",
    }
)

APPROVED_HELPERS = frozenset(
    {
        "ColumnsOption",
        "ConfigError",
        "FormatOption",
        "PipeEnvelope",
        "StateCollection",
        "UiContext",
        "UntapedError",
        "app_context",
        "create_app",
        "echo",
        "emit",
        "finish",
        "first_validation_error",
        "get_config_section",
        "parse_envelope_line",
        "raise_usage",
        "read_identifiers",
        "report_errors",
        "run_editor",
    }
)

_KERNEL_SURFACE_MODULES = frozenset({"untaped.capability_api", "untaped.api"})

#: Wave 2 amendment 1 (import plan): the single sanctioned cross-capability
#: import — ansible consumes GitHub behavior only through this closed API
#: module, also recorded as the [[allow]] entry in
#: ``docs/dependency-policy.toml``. Blanket sibling imports stay forbidden.
ANSIBLE_PREFIX = "untaped.capabilities.ansible"
ANSIBLE_GITHUB_API = "untaped.capabilities.github.ansible"


def discover_capabilities(src: Path = CAPABILITIES_SRC) -> list[str]:
    """Names of in-repo capabilities: subpackages of ``capabilities/``."""
    if not src.is_dir():
        return []
    return sorted(
        child.name
        for child in src.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )


def _package_of(py_file: Path, own_prefix: str, capability_dir: Path) -> str:
    """Containing package of a capability file (itself for ``__init__.py``)."""
    rel = py_file.relative_to(capability_dir).with_suffix("")
    parts = list(rel.parts)
    if parts:
        parts.pop()
    if not parts:
        return own_prefix
    return own_prefix + "." + ".".join(parts)


def _resolve_import_from(
    package: str, level: int, module: str | None, names: list[ast.alias]
) -> list[str]:
    """Resolve an ``ImportFrom`` to absolute dotted paths.

    Level 0 returns ``[module]``; level > 0 resolves from the containing
    package. ``from . import name`` binds the submodule ``base.name``.
    Unresolvable levels (beyond top-level) return ``[]``.
    """
    if level == 0:
        return [module] if module else []
    base = package
    for _ in range(level - 1):
        if "." in base:
            base = base.rpartition(".")[0]
        else:
            return []
    if module:
        return [f"{base}.{module}"]
    resolved: list[str] = []
    for alias in names:
        if alias.name == "*":
            resolved.append(base)
        else:
            resolved.append(f"{base}.{alias.name}")
    return resolved


def surface_violations(src: Path = CAPABILITIES_SRC) -> list[str]:
    """Flag capability files importing outside the kernel-only surface."""
    violations: list[str] = []
    for capability in discover_capabilities(src):
        capability_dir = src / capability
        own_prefix = f"untaped.capabilities.{capability}"
        for py_file in sorted(capability_dir.rglob("*.py")):
            package = _package_of(py_file, own_prefix, capability_dir)
            violations.extend(_file_violations(py_file, own_prefix, package))
    return violations


def _file_violations(py_file: Path, own_prefix: str, package: str) -> list[str]:
    found: list[str] = []
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for absolute in _resolve_import_from(package, node.level, node.module, node.names):
                problem = _from_violation(absolute, own_prefix)
                if problem is not None:
                    where = f"{_rel(py_file)}:{node.lineno}"
                    found.append(f"{where}: from {absolute} import …: {problem}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                problem = _import_violation(alias.name, own_prefix)
                if problem is not None:
                    found.append(f"{_rel(py_file)}:{node.lineno}: import {alias.name}: {problem}")
    return found


def _from_violation(module: str, own_prefix: str) -> str | None:
    if not module.startswith("untaped"):
        return None
    if module in _KERNEL_SURFACE_MODULES:
        return None
    if module == own_prefix or module.startswith(own_prefix + "."):
        return None
    if module == ANSIBLE_GITHUB_API and own_prefix == ANSIBLE_PREFIX:
        return None
    return (
        "capability code must import kernel helpers only via "
        "untaped.capability_api (or the transitional untaped.api) "
        f"and sibling code only from its own {own_prefix} subtree"
    )


def _import_violation(name: str, own_prefix: str) -> str | None:
    if not (name == "untaped" or name.startswith("untaped.")):
        return None
    if name in _KERNEL_SURFACE_MODULES:
        return None
    if name == own_prefix or name.startswith(own_prefix + "."):
        return None
    if name == ANSIBLE_GITHUB_API and own_prefix == ANSIBLE_PREFIX:
        return None
    return (
        "capability code must import kernel helpers only via "
        "untaped.capability_api (or the transitional untaped.api) "
        f"and sibling code only from its own {own_prefix} subtree"
    )


def _rel(py_file: Path) -> str:
    try:
        return str(py_file.relative_to(REPO_ROOT))
    except ValueError:
        return str(py_file)


# ── live-tree positive ───────────────────────────────────────────────────────


def test_capability_code_imports_kernel_only_surface() -> None:
    assert surface_violations() == []


def test_approved_helper_allowlist_matches_capability_api() -> None:
    helpers = set(capability_api.__all__) - COMPOSITION_NAMES
    assert helpers == set(APPROVED_HELPERS)
    assert len(capability_api.__all__) == len(COMPOSITION_NAMES) + len(APPROVED_HELPERS)


# ── negatives (hermetic probes) ──────────────────────────────────────────────


def _probe_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    src = tmp_path / "capabilities"
    for rel, text in files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return src


def test_kernel_internal_import_fails(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "atlas/mod.py": "from untaped.settings import get_settings\n",
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 1
    assert "untaped.settings" in violations[0]
    assert "capability_api" in violations[0]


def test_sibling_capability_import_fails(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "other/__init__.py": "",
            "atlas/mod.py": "from untaped.capabilities.other.engine import run\n",
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 1
    assert "untaped.capabilities.other.engine" in violations[0]


def test_composition_kernel_import_fails(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": ("from untaped.capabilities.registry import CapabilitySpec\n"),
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 1
    assert "untaped.capabilities.registry" in violations[0]


def test_bare_root_and_direct_module_imports_fail(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "atlas/a.py": "from untaped import api\n",
            "atlas/b.py": "import untaped.cli\n",
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 2


def test_stable_surface_and_own_subtree_pass(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "from untaped.capability_api import CapabilitySpec\n",
            "atlas/mod.py": (
                "from untaped.api import echo, report_errors\n"
                "from untaped.capabilities.atlas.inner import thing\n"
                "from . import sibling\n"
                "import untaped.capability_api\n"
            ),
        },
    )
    assert surface_violations(src) == []


# ── relative-import regressions (Wave 1.6 fix) ───────────────────────────────


def test_relative_own_subtree_passes(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "atlas/sub/__init__.py": "",
            "atlas/mod.py": "from . import sibling\nfrom .inner import thing\n",
            "atlas/sub/mod.py": "from ..inner import thing\n",
        },
    )
    assert surface_violations(src) == []


def test_relative_sibling_escape_fails(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "other/__init__.py": "",
            "atlas/mod.py": "from ..other.engine import run\n",
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 1
    assert "untaped.capabilities.other.engine" in violations[0]


def test_relative_bare_import_escape_fails(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "other/__init__.py": "",
            "atlas/mod.py": "from .. import other\n",
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 1
    assert "untaped.capabilities.other" in violations[0]


def test_relative_kernel_escape_fails(tmp_path: Path) -> None:
    src = _probe_tree(
        tmp_path,
        {
            "atlas/__init__.py": "",
            "atlas/mod.py": "from ...settings import get_settings\n",
        },
    )
    violations = surface_violations(src)
    assert len(violations) == 1
    assert "untaped.settings" in violations[0]
