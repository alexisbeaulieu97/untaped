"""Hook naming, module layout, and the hook-project compatibility contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from untaped.capabilities.recipe.hook_api import HOOK_API_VERSION

if TYPE_CHECKING:
    from untaped.capabilities.recipe.domain.pack import PackManifest

_DOTTED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
# ``untaped-recipe`` is the distribution name the recipe engine shipped under
# before it became a built-in capability; a hook project that still lists it
# (or ``untaped``) as a runtime dependency would pull the CLI into the pack env.
_FORBIDDEN_RUNTIME_DEPENDENCIES = frozenset({"untaped", "untaped-recipe"})
HookKind = Literal["transform", "validate"]


def is_valid_dotted_name(name: str) -> bool:
    """Return true when ``name`` is a safe dotted hook/module identifier."""
    return bool(_DOTTED_NAME_RE.fullmatch(name.strip()))


def normalize_hook_name(name: str) -> str:
    """Validate and return a public hook name."""
    normalized = name.strip()
    if not is_valid_dotted_name(normalized):
        raise ValueError(f"invalid hook name: {name}")
    return normalized


def hook_module_file(project_root: Path, module: str) -> Path:
    """Return the required src-layout file path for a declared hook module."""
    return project_root / "src" / Path(*module.split(".")).with_suffix(".py")


def ensure_hook_supports(exports: frozenset[str], hook: str, *, verb: str) -> None:
    """Reject a hook that does not export the verb the caller needs."""
    if verb not in exports:
        raise ValueError(f"{verb} step hook {hook!r} does not export a {verb}() function")


def untaped_dev_requirement(untaped_version: str) -> str:
    """Return the dev-only ``untaped`` requirement for an installed ``untaped`` version.

    Packs depend on the CLI only for editor/type discovery, so the range
    starts at the running release and stays within its major version.
    """
    version = Version(untaped_version)
    return f"untaped>={version.public},<{version.major + 1}"


def validate_hook_project_contract(
    project_root: Path,
    manifest: PackManifest,
    *,
    dev_requirement: str,
) -> None:
    """Require a hook project to be compatible with the running helper API."""
    for dependency in manifest.runtime_dependencies:
        dependency_distribution = dependency_name(dependency)
        if dependency_distribution in _FORBIDDEN_RUNTIME_DEPENDENCIES:
            raise ValueError(
                f"hook project must not depend on {dependency_distribution} at runtime; "
                "add the unified requirement to dependency-groups.dev instead: "
                f"{dev_requirement}; "
                f"{project_root}"
            )
    if manifest.requires_hook_api is None:
        return
    specifier = hook_api_specifier(manifest.requires_hook_api)
    if Version(HOOK_API_VERSION) not in specifier:
        raise ValueError(
            f"hook project requires hook API {manifest.requires_hook_api}, "
            f"but the untaped recipe capability provides {HOOK_API_VERSION}: {project_root}"
        )


def dependency_name(dependency: str) -> str:
    """Return the normalized PEP 508 project name for a dependency string."""
    try:
        return canonicalize_name(Requirement(dependency).name)
    except InvalidRequirement as exc:
        raise ValueError(
            f"[project].dependencies entry must be a valid requirement: {dependency}"
        ) from exc


def hook_api_specifier(value: str) -> SpecifierSet:
    """Parse a ``requires_hook_api`` specifier set."""
    try:
        return SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ValueError(f"invalid hook API requirement: {value}") from exc
