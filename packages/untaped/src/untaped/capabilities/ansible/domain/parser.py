"""Parsers for Ansible role dependency declaration files."""

from __future__ import annotations

from typing import Any, Literal

import yaml

from untaped.capabilities.ansible.domain.models import (
    DependencyDeclaration,
    ParseReport,
    ParseWarning,
)


def parse_dependency_file(path: str, text: str) -> ParseReport:
    """Parse supported Ansible dependency files into role declarations."""
    parsed = _load_yaml(text)
    if parsed.status == "empty":
        return ParseReport()
    if parsed.status == "invalid":
        return _warning_report(path, "could not parse dependency YAML")
    data = parsed.data
    if _is_meta_main_path(path):
        if not isinstance(data, dict):
            return _warning_report(path, "expected mapping at top level")
        dependencies, warnings = _parse_list_section(data, "dependencies", path)
        return ParseReport(dependencies=dependencies, warnings=warnings)
    if _is_requirements_path(path):
        if isinstance(data, list):
            return ParseReport(dependencies=_parse_entries(data, path))
        if isinstance(data, dict):
            dependencies, dependency_warnings = _parse_list_section(data, "roles", path)
            collections, collection_warnings = _parse_collection_section(
                data,
                "collections",
                path,
            )
            return ParseReport(
                dependencies=dependencies,
                ignored_collections=collections,
                warnings=(*dependency_warnings, *collection_warnings),
            )
        return _warning_report(path, "expected mapping or list at top level")
    return _warning_report(path, "unsupported dependency file")


class _StringScalarLoader(yaml.SafeLoader):
    """Safe loader that keeps int/float-looking scalars as their original text.

    Role versions such as ``1.10`` must not become the float ``1.1``.
    """


_StringScalarLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in {"tag:yaml.org,2002:int", "tag:yaml.org,2002:float"}
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class _LoadedYaml:
    def __init__(
        self,
        status: Literal["empty", "invalid", "parsed"],
        data: Any = None,
    ) -> None:
        self.status = status
        self.data = data


def _load_yaml(text: str) -> _LoadedYaml:
    if not text.strip():
        return _LoadedYaml("empty")
    try:
        data = yaml.load(text, Loader=_StringScalarLoader)
    except yaml.YAMLError:
        return _LoadedYaml("invalid")
    if data is None:
        return _LoadedYaml("empty")
    return _LoadedYaml("parsed", data)


def _is_meta_main_path(path: str) -> bool:
    return path.endswith("meta/main.yml") or path.endswith("meta/main.yaml")


def _is_requirements_path(path: str) -> bool:
    return path.endswith("requirements.yml") or path.endswith("requirements.yaml")


def _warning_report(path: str, reason: str) -> ParseReport:
    return ParseReport(warnings=(ParseWarning(source_path=path, reason=reason),))


def _parse_list_section(
    data: dict[Any, Any],
    key: str,
    source_path: str,
) -> tuple[tuple[DependencyDeclaration, ...], tuple[ParseWarning, ...]]:
    raw, warnings = _section_list(data, key, source_path)
    if warnings:
        return (), warnings
    return _parse_entries(raw, source_path), ()


def _section_list(
    data: dict[Any, Any],
    key: str,
    source_path: str,
) -> tuple[list[Any], tuple[ParseWarning, ...]]:
    raw = data.get(key)
    if raw is None:
        return [], ()
    if not isinstance(raw, list):
        return [], (ParseWarning(source_path=source_path, reason=f"expected list at {key}"),)
    return raw, ()


def _parse_entries(raw: list[Any], source_path: str) -> tuple[DependencyDeclaration, ...]:
    declarations: list[DependencyDeclaration] = []
    for entry in raw:
        declaration = _parse_entry(entry, source_path)
        if declaration is not None:
            declarations.append(declaration)
    return tuple(declarations)


def _parse_entry(entry: Any, source_path: str) -> DependencyDeclaration | None:
    if isinstance(entry, str):
        entry_name = entry.strip()
        if not entry_name:
            return None
        return DependencyDeclaration(name=entry_name, src=entry_name, source_path=source_path)
    if not isinstance(entry, dict):
        return None
    src = _string(entry.get("src"))
    name = _string(entry.get("name")) or _string(entry.get("role")) or _name_from_src(src) or src
    if name is None:
        return None
    return DependencyDeclaration(
        name=name,
        src=src or name,
        version=_string(entry.get("version")),
        source_path=source_path,
    )


def _parse_collection_section(
    data: dict[Any, Any],
    key: str,
    source_path: str,
) -> tuple[tuple[str, ...], tuple[ParseWarning, ...]]:
    raw, warnings = _section_list(data, key, source_path)
    if warnings:
        return (), warnings
    names: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = _string(entry.get("name")) or ""
        else:
            name = ""
        if name:
            names.append(name)
    return tuple(names), ()


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _name_from_src(src: str | None) -> str | None:
    if src is None:
        return None
    normalized = src.removeprefix("git+").removesuffix(".git").rstrip("/")
    return normalized.rsplit("/", maxsplit=1)[-1] or None
