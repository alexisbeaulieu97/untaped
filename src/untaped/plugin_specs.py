"""Plugin package spec parsing and canonicalization helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from untaped.errors import ConfigError

_PACKAGE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_NAMED_DIRECT_REFERENCE_RE = re.compile(
    rf"^\s*(?P<name>{_PACKAGE_NAME})(?:\[[^\]]+\])?\s*@\s*(?P<target>.+?)\s*$"
)
_REQUIREMENT_NAME_RE = re.compile(
    rf"^\s*(?P<name>{_PACKAGE_NAME})(?:\[[^\]]+\])?(?=\s*(?:$|[<>=!~;]))"
)
_VCS_PREFIX_RE = re.compile(r"^(?:git|hg|svn|bzr)\+")
_DIRECT_REFERENCE_PREFIXES = ("git+", "hg+", "svn+", "bzr+", "file:")
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".whl")


def canonical_plugin_spec(spec: str, *, reject_uninferable_direct: bool) -> str:
    """Return the canonical recorded representation for a plugin package spec."""
    stripped = stripped_plugin_spec(spec)
    named_direct = _NAMED_DIRECT_REFERENCE_RE.match(stripped)
    if named_direct is not None:
        name = normalize_package_name(named_direct.group("name"))
        return f"{name} @ {named_direct.group('target').strip()}"
    if looks_like_direct_reference(stripped):
        inferred = infer_direct_reference_name(stripped)
        if inferred is not None:
            return f"{inferred} @ {stripped}"
        if reject_uninferable_direct:
            raise uninferable_direct_reference_error(stripped)
    return stripped


def plugin_spec_key(spec: str, *, reject_bare_direct: bool) -> str:
    """Return the stable identity key for matching recorded plugin specs."""
    stripped = stripped_plugin_spec(spec)
    named_direct = _NAMED_DIRECT_REFERENCE_RE.match(stripped)
    if named_direct is not None:
        return normalize_package_name(named_direct.group("name"))
    requirement = _REQUIREMENT_NAME_RE.match(stripped)
    if requirement is not None:
        return normalize_package_name(requirement.group("name"))
    if looks_like_direct_reference(stripped):
        inferred = infer_direct_reference_name(stripped)
        if inferred is not None:
            return inferred
        if reject_bare_direct:
            raise uninferable_direct_reference_error(stripped)
        return stripped
    return stripped


def unique_plugin_specs(package_specs: Iterable[str]) -> list[str]:
    """Deduplicate package specs by normalized plugin identity while preserving order."""
    seen: set[str] = set()
    unique: list[str] = []
    for package_spec in package_specs:
        key = plugin_spec_key(package_spec, reject_bare_direct=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(package_spec)
    return unique


def stripped_plugin_spec(spec: str) -> str:
    stripped = spec.strip()
    if not stripped:
        raise ConfigError("plugin package spec cannot be empty")
    return stripped


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def looks_like_direct_reference(spec: str) -> bool:
    return "://" in spec or spec.startswith(_DIRECT_REFERENCE_PREFIXES)


def infer_direct_reference_name(spec: str) -> str | None:
    if spec.startswith("file:"):
        return None
    target = _VCS_PREFIX_RE.sub("", spec, count=1)
    parsed = urlparse(target)
    path = parsed.path if parsed.scheme else target
    basename = PurePosixPath(unquote(path).rstrip("/")).name
    lowered = basename.lower()
    if lowered.endswith(_ARCHIVE_SUFFIXES):
        return None
    git_ref_index = lowered.find(".git@")
    if git_ref_index != -1:
        basename = basename[: git_ref_index + len(".git")]
        lowered = basename.lower()
    if lowered.endswith(".git"):
        basename = basename[:-4]
    if not basename or re.fullmatch(_PACKAGE_NAME, basename) is None:
        return None
    return normalize_package_name(basename)


def uninferable_direct_reference_error(spec: str) -> ConfigError:
    return ConfigError(
        "could not infer plugin name from direct URL; use 'name @ url' "
        f"(for example: untaped-awx @ {spec})"
    )
