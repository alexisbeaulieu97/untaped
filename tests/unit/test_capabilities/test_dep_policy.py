"""Distribution dependency-policy enforcement (spec §6, Wave 1.6).

Two independent CI checks run here as part of the default ``pytest`` run
(no separate manual step):

(a) ``docs/runtime-deps.toml`` records every direct runtime dependency from
    ``pyproject.toml`` with owning capability, rationale, and the exact
    normalized requirement string. The validator fails on unrecorded
    additions, requirement mismatches (broadened or narrowed bounds,
    changed pins, added/removed extras, renames — the differing value is
    named), and stale records.

(b) No capability imports another capability's implementation modules
    (``untaped.capabilities.<name>.*``) without a matching ``[[allow]]``
    entry in ``docs/dependency-policy.toml``; stale allow entries fail.
    A missing policy file means "no exceptions".
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Container, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
RUNTIME_DEPS_PATH = REPO_ROOT / "docs" / "runtime-deps.toml"
DEPENDENCY_POLICY_PATH = REPO_ROOT / "docs" / "dependency-policy.toml"
CAPABILITIES_SRC = REPO_ROOT / "src" / "untaped" / "capabilities"

_POLICY_VERSION = 1
_RUNTIME_TOP_KEYS = frozenset({"version", "dep"})
_RUNTIME_ENTRY_KEYS = frozenset({"name", "owner", "reason", "requirement"})
_ALLOW_TOP_KEYS = frozenset({"version", "allow"})
_ALLOW_ENTRY_KEYS = frozenset({"importer", "imported", "reason"})

_NAME_RE = re.compile(
    r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
    r"(\[[^\]]*\])?\s*(.*?)\s*(?:;\s*(.*))?$",
    re.DOTALL,
)


def normalize_distribution_name(value: str) -> str:
    """PEP 503 normalization: lowercase, runs of ``-_.`` become ``-``."""
    return re.sub(r"[-_.]+", "-", value).lower()


def normalize_requirement(value: str) -> str:
    """Normalized requirement: normalized name plus the verbatim remainder.

    Extras, version specifiers, and markers are preserved exactly as
    declared; only the distribution name is normalized and surrounding
    whitespace is stripped.
    """
    match = _NAME_RE.match(value.strip())
    if match is None:
        return value.strip()
    name, extras, specifier, marker = match.groups()
    normalized = normalize_distribution_name(name)
    if extras:
        normalized += extras
    if specifier:
        normalized += specifier
    if marker:
        normalized += f"; {marker}"
    return normalized


def requirement_name(value: str) -> str:
    """Normalized distribution name at the head of a requirement string."""
    match = _NAME_RE.match(value.strip())
    if match is None:
        return value.strip().lower()
    return normalize_distribution_name(match.group(1))


def read_pyproject_dependencies(path: Path = PYPROJECT_PATH) -> dict[str, str]:
    """Map normalized name to normalized requirement for each direct dep."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = data.get("project", {}).get("dependencies", [])
    return {requirement_name(entry): normalize_requirement(entry) for entry in raw}


def discover_capabilities(src: Path = CAPABILITIES_SRC) -> list[str]:
    """Names of in-repo capabilities: subpackages of ``capabilities/``."""
    if not src.is_dir():
        return []
    return sorted(
        child.name
        for child in src.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )


def check_runtime_policy_shape(raw: Mapping[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Validate the ``runtime-deps.toml`` artifact shape (closed)."""
    errors: list[str] = []
    for key in raw:
        if key not in _RUNTIME_TOP_KEYS:
            errors.append(f"runtime-deps: unknown top-level key {key!r}")
    if raw.get("version") != _POLICY_VERSION:
        errors.append(
            f"runtime-deps: version must be {_POLICY_VERSION}, got {raw.get('version')!r}"
        )
    raw_deps = raw.get("dep", [])
    if not isinstance(raw_deps, list):
        return [], [*errors, "runtime-deps: 'dep' must be a list"]
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_deps):
        where = f"runtime-deps [[dep]] #{index}"
        if not isinstance(entry, Mapping):
            errors.append(f"{where}: entry must be a table")
            continue
        for key in entry:
            if key not in _RUNTIME_ENTRY_KEYS:
                errors.append(f"{where}: unknown key {key!r}")
        missing = _RUNTIME_ENTRY_KEYS - set(entry)
        if missing:
            errors.append(f"{where}: missing keys {sorted(missing)}")
            continue
        values = {key: entry[key] for key in _RUNTIME_ENTRY_KEYS}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            errors.append(f"{where}: name/owner/reason/requirement must be non-empty strings")
            continue
        name = normalize_distribution_name(values["name"])
        if name in seen:
            errors.append(f"{where}: duplicate entry for {name!r}")
            continue
        seen.add(name)
        entries.append(values)
    return entries, errors


def check_runtime_policy_content(
    dependencies: Mapping[str, str],
    entries: Sequence[Mapping[str, str]],
    *,
    capabilities: Container[str],
) -> list[str]:
    """Compare policy entries against pyproject requirements (exact match)."""
    errors: list[str] = []
    by_name: dict[str, Mapping[str, str]] = {}
    for entry in entries:
        name = normalize_distribution_name(entry["name"])
        by_name[name] = entry
        if entry["owner"] not in capabilities:
            errors.append(f"runtime-deps: {name!r} names unknown owner {entry['owner']!r}")
    for name, requirement in sorted(dependencies.items()):
        entry = by_name.get(name)
        if entry is None:
            errors.append(f"runtime-deps: unrecorded direct dependency {name!r}")
        elif entry["requirement"].strip() != requirement:
            errors.append(
                f"runtime-deps: {name!r} requirement mismatch: "
                f"pyproject {requirement!r} != record {entry['requirement'].strip()!r}"
            )
    for name in sorted(by_name):
        if name not in dependencies:
            errors.append(f"runtime-deps: stale record for {name!r} (not a direct dependency)")
    return errors


def validate_runtime_deps(
    pyproject: Path = PYPROJECT_PATH,
    policy: Path = RUNTIME_DEPS_PATH,
    capabilities: Sequence[str] | None = None,
) -> list[str]:
    """Full §6(a) check: shape errors plus exact-match content errors."""
    raw = tomllib.loads(policy.read_text(encoding="utf-8"))
    entries, errors = check_runtime_policy_shape(raw)
    if capabilities is None:
        capabilities = discover_capabilities()
    content = check_runtime_policy_content(
        read_pyproject_dependencies(pyproject), entries, capabilities=capabilities
    )
    return [*errors, *content]


def check_dependency_policy_shape(
    raw: Mapping[str, Any], *, capabilities: Container[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """Validate the ``dependency-policy.toml`` artifact shape (closed)."""
    errors: list[str] = []
    for key in raw:
        if key not in _ALLOW_TOP_KEYS:
            errors.append(f"dependency-policy: unknown top-level key {key!r}")
    if raw.get("version") != _POLICY_VERSION:
        errors.append(
            f"dependency-policy: version must be {_POLICY_VERSION}, got {raw.get('version')!r}"
        )
    raw_allow = raw.get("allow", [])
    if not isinstance(raw_allow, list):
        return [], [*errors, "dependency-policy: 'allow' must be a list"]
    allows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(raw_allow):
        where = f"dependency-policy [[allow]] #{index}"
        if not isinstance(entry, Mapping):
            errors.append(f"{where}: entry must be a table")
            continue
        pair, entry_errors = _check_allow_entry(entry, where, capabilities)
        errors.extend(entry_errors)
        if pair is None:
            continue
        if pair in seen:
            errors.append(f"{where}: duplicate (importer, imported) pair {pair!r}")
            continue
        seen.add(pair)
        allows.append(pair)
    return allows, errors


def _check_allow_entry(
    entry: Mapping[str, Any], where: str, capabilities: Container[str]
) -> tuple[tuple[str, str] | None, list[str]]:
    """Validate one ``[[allow]]`` entry; return its pair when well-formed."""
    errors: list[str] = []
    for key in entry:
        if key not in _ALLOW_ENTRY_KEYS:
            errors.append(f"{where}: unknown key {key!r}")
    if missing := _ALLOW_ENTRY_KEYS - set(entry):
        return None, [*errors, f"{where}: missing keys {sorted(missing)}"]
    importer, imported, reason = entry["importer"], entry["imported"], entry["reason"]
    if any(not isinstance(value, str) or not value.strip() for value in (importer, imported)):
        return None, [*errors, f"{where}: importer/imported must be non-empty strings"]
    if not isinstance(reason, str) or not reason.strip():
        return None, [*errors, f"{where}: reason must be a non-empty string"]
    for side, prefix in (("importer", importer), ("imported", imported)):
        if not _matches_capability_module(prefix, capabilities):
            errors.append(f"{where}: {side} {prefix!r} matches no capability module")
    if errors:
        return None, errors
    return (importer, imported), []


def _matches_capability_module(prefix: str, capabilities: Container[str]) -> bool:
    base = "untaped.capabilities."
    if not prefix.startswith(base) or prefix.strip(".") != prefix.strip():
        return False
    rest = prefix[len(base) :]
    if not rest or rest.startswith(".") or ".." in rest or " " in rest:
        return False
    head = rest.split(".", 1)[0]
    return head in capabilities


def read_dependency_policy(
    path: Path = DEPENDENCY_POLICY_PATH, *, capabilities: Sequence[str] | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Load the allow-list; a missing file means "no exceptions"."""
    owners: Container[str] = capabilities if capabilities is not None else discover_capabilities()
    if not path.is_file():
        return [], []
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return check_dependency_policy_shape(raw, capabilities=owners)


def _module_of(py_file: Path, capability_dir: Path, capability: str) -> str:
    rel = py_file.relative_to(capability_dir).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    base = f"untaped.capabilities.{capability}"
    return base if not parts else base + "." + ".".join(parts)


def scan_cross_capability_imports(
    src: Path = CAPABILITIES_SRC,
) -> tuple[list[str], list[tuple[str, str, str, int]]]:
    """Find imports of sibling capability modules.

    Returns ``(capabilities, observed)`` where each observation is
    ``(file, importer_module, imported_module, lineno)``.
    """
    capabilities = discover_capabilities(src)
    observed: list[tuple[str, str, str, int]] = []
    for capability in capabilities:
        capability_dir = src / capability
        for py_file in sorted(capability_dir.rglob("*.py")):
            importer = _module_of(py_file, capability_dir, capability)
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = _sibling_target(alias.name, capability, capabilities)
                        if target is not None:
                            observed.append((_rel(py_file), importer, target, node.lineno))
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module
                    and node.module.startswith("untaped.capabilities.")
                ):
                    target = _sibling_target(node.module, capability, capabilities)
                    if target is not None:
                        observed.append((_rel(py_file), importer, target, node.lineno))
    return capabilities, observed


def _sibling_target(module: str, own: str, capabilities: Sequence[str]) -> str | None:
    """The sibling capability module imported, or None when not cross-capability."""
    rest = module[len("untaped.capabilities.") :]
    target = rest.split(".", 1)[0]
    if target == "registry" or target == own or target not in capabilities:
        return None
    return module


def _rel(py_file: Path) -> str:
    try:
        return str(py_file.relative_to(REPO_ROOT))
    except ValueError:
        return str(py_file)


def check_cross_imports(
    observed: Sequence[tuple[str, str, str, int]],
    allows: Sequence[tuple[str, str]],
) -> list[str]:
    """Match observations against allow entries; report unallowed and stale."""
    errors: list[str] = []
    used = [False] * len(allows)
    for filename, importer, imported, lineno in observed:
        matched = False
        for index, (allow_importer, allow_imported) in enumerate(allows):
            if _prefix_match(importer, allow_importer) and _prefix_match(imported, allow_imported):
                matched = True
                used[index] = True
        if not matched:
            errors.append(
                f"{filename}:{lineno}: cross-capability import {imported!r} "
                f"by {importer!r} lacks a matching "
                f"(importer, imported) allow entry"
            )
    for index, pair in enumerate(allows):
        if not used[index]:
            errors.append(f"dependency-policy: stale allow entry {pair!r} matches no import")
    return errors


def _prefix_match(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def validate_cross_imports(
    src: Path = CAPABILITIES_SRC,
    policy: Path = DEPENDENCY_POLICY_PATH,
) -> list[str]:
    """Full §6(b) check: artifact shape plus observed-import matching."""
    capabilities, observed = scan_cross_capability_imports(src)
    allows, errors = read_dependency_policy(policy, capabilities=capabilities)
    errors.extend(check_cross_imports(observed, allows))
    return errors


# ── live-tree positives ──────────────────────────────────────────────────────


def test_runtime_deps_cover_pyproject_exactly() -> None:
    assert validate_runtime_deps() == []


def test_no_unallowed_cross_capability_imports() -> None:
    assert validate_cross_imports() == []


def test_missing_policy_file_means_no_exceptions(tmp_path: Path) -> None:
    allows, errors = read_dependency_policy(tmp_path / "absent.toml", capabilities=["workspace"])
    assert (allows, errors) == ([], [])


# ── §6(a) negatives (hermetic) ───────────────────────────────────────────────


def _shape(toml_text: str) -> tuple[list[dict[str, str]], list[str]]:
    return check_runtime_policy_shape(tomllib.loads(toml_text))


def test_runtime_policy_rejects_bad_version() -> None:
    _, errors = _shape("version = 2\n")
    assert any("version" in error for error in errors)


def test_runtime_policy_rejects_unknown_top_key() -> None:
    text = (
        'version = 1\nextra = true\n[[dep]]\nname = "x"\nowner = "c"\n'
        'reason = "r"\nrequirement = "x"\n'
    )
    _, errors = _shape(text)
    assert any("unknown top-level key" in error for error in errors)


def test_runtime_policy_rejects_entry_missing_key() -> None:
    _, errors = _shape('version = 1\n[[dep]]\nname = "x"\nowner = "c"\nreason = "r"\n')
    assert any("missing keys" in error for error in errors)


def test_runtime_policy_rejects_empty_reason() -> None:
    _, errors = _shape(
        'version = 1\n[[dep]]\nname = "x"\nowner = "c"\nreason = "  "\nrequirement = "x"\n'
    )
    assert any("non-empty strings" in error for error in errors)


def test_runtime_policy_rejects_duplicate_names() -> None:
    _, errors = _shape(
        'version = 1\n[[dep]]\nname = "X"\nowner = "c"\nreason = "r"\nrequirement = "x"\n'
        '[[dep]]\nname = "x"\nowner = "c"\nreason = "r"\nrequirement = "x"\n'
    )
    assert any("duplicate entry" in error for error in errors)


def test_unrecorded_dependency_fails() -> None:
    errors = check_runtime_policy_content({"httpx": "httpx>=0.28.1"}, [], capabilities=["c"])
    assert errors == ["runtime-deps: unrecorded direct dependency 'httpx'"]


def test_broadened_requirement_fails_naming_values() -> None:
    entries = [{"name": "httpx", "owner": "c", "reason": "r", "requirement": "httpx>=0.27"}]
    errors = check_runtime_policy_content({"httpx": "httpx>=0.28.1"}, entries, capabilities=["c"])
    assert len(errors) == 1
    assert "httpx>=0.28.1" in errors[0]
    assert "httpx>=0.27" in errors[0]


def test_narrowed_requirement_fails() -> None:
    entries = [{"name": "httpx", "owner": "c", "reason": "r", "requirement": "httpx>=0.28.1,<0.29"}]
    errors = check_runtime_policy_content({"httpx": "httpx>=0.28.1"}, entries, capabilities=["c"])
    assert any("mismatch" in error for error in errors)


def test_stale_record_fails() -> None:
    entries = [{"name": "gone", "owner": "c", "reason": "r", "requirement": "gone>=1"}]
    errors = check_runtime_policy_content({}, entries, capabilities=["c"])
    assert errors == ["runtime-deps: stale record for 'gone' (not a direct dependency)"]


def test_unknown_owner_fails() -> None:
    entries = [{"name": "httpx", "owner": "ghost", "reason": "r", "requirement": "httpx>=0.28.1"}]
    errors = check_runtime_policy_content({"httpx": "httpx>=0.28.1"}, entries, capabilities=["c"])
    assert any("unknown owner 'ghost'" in error for error in errors)


def test_name_normalization_matches_declared_spelling() -> None:
    entries = [
        {
            "name": "Prompt_Toolkit",
            "owner": "c",
            "reason": "r",
            "requirement": "prompt-toolkit>=3.0.52",
        }
    ]
    errors = check_runtime_policy_content(
        {"prompt-toolkit": "prompt-toolkit>=3.0.52"}, entries, capabilities=["c"]
    )
    assert errors == []


# ── §6(b) negatives (hermetic) ───────────────────────────────────────────────


def _cap_tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    src = tmp_path / "capabilities"
    for rel, text in files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return src


def test_sibling_import_without_allow_entry_fails(tmp_path: Path) -> None:
    src = _cap_tree(
        tmp_path,
        {
            "alpha/__init__.py": "",
            "beta/__init__.py": "",
            "alpha/mod.py": "from untaped.capabilities.beta.engine import run\n",
        },
    )
    capabilities, observed = scan_cross_capability_imports(src)
    assert capabilities == ["alpha", "beta"]
    assert len(observed) == 1
    errors = check_cross_imports(observed, [])
    assert len(errors) == 1
    assert "untaped.capabilities.beta.engine" in errors[0]
    assert "alpha" in errors[0]


def test_matching_allow_entry_permits_sibling_import(tmp_path: Path) -> None:
    src = _cap_tree(
        tmp_path,
        {
            "alpha/__init__.py": "",
            "beta/__init__.py": "",
            "alpha/mod.py": "from untaped.capabilities.beta.engine import run\n",
        },
    )
    _, observed = scan_cross_capability_imports(src)
    allows = [("untaped.capabilities.alpha", "untaped.capabilities.beta.engine")]
    assert check_cross_imports(observed, allows) == []


def test_stale_allow_entry_fails(tmp_path: Path) -> None:
    src = _cap_tree(tmp_path, {"alpha/__init__.py": "", "beta/__init__.py": ""})
    _, observed = scan_cross_capability_imports(src)
    allows = [("untaped.capabilities.alpha", "untaped.capabilities.beta.engine")]
    errors = check_cross_imports(observed, allows)
    assert errors == [
        "dependency-policy: stale allow entry "
        "('untaped.capabilities.alpha', 'untaped.capabilities.beta.engine') matches no import"
    ]


def test_own_subtree_and_registry_imports_are_not_cross_capability(tmp_path: Path) -> None:
    src = _cap_tree(
        tmp_path,
        {
            "alpha/__init__.py": "from untaped.capabilities.registry import CapabilitySpec\n",
            "alpha/mod.py": "from untaped.capabilities.alpha.inner import thing\n",
        },
    )
    _, observed = scan_cross_capability_imports(src)
    assert observed == []


def test_policy_shape_rejects_unknown_capability_prefix() -> None:
    raw = {
        "version": 1,
        "allow": [
            {
                "importer": "untaped.capabilities.ghost.mod",
                "imported": "untaped.capabilities.alpha.api",
                "reason": "shared helper; no settings access",
            }
        ],
    }
    _, errors = check_dependency_policy_shape(raw, capabilities=["alpha"])
    assert any("matches no capability module" in error for error in errors)


def test_policy_shape_rejects_empty_reason_and_duplicates() -> None:
    entry = {
        "importer": "untaped.capabilities.alpha",
        "imported": "untaped.capabilities.beta",
        "reason": "  ",
    }
    _, errors = check_dependency_policy_shape(
        {"version": 1, "allow": [entry]}, capabilities=["alpha", "beta"]
    )
    assert any("non-empty string" in error for error in errors)
    good = dict(entry, reason="shared paginator; no settings access")
    allows, errors = check_dependency_policy_shape(
        {"version": 1, "allow": [good, good]}, capabilities=["alpha", "beta"]
    )
    assert allows == [("untaped.capabilities.alpha", "untaped.capabilities.beta")]
    assert any("duplicate" in error for error in errors)
