"""Shared constants and metadata helpers for the release boundary."""

import ast
import re
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - supported by the legacy fallback.
    tomllib = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9][A-Za-z0-9._+-]*)?")
TESTPYPI_INDEX = "https://test.pypi.org/simple/"
PYPI_INDEX = "https://pypi.org/simple/"
MANIFEST = ROOT / "release-manifest.toml"
SOURCE_EVIDENCE_PATH = ROOT / "release-source-evidence.toml"
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
BUILTIN_CAPABILITIES = (
    "workspace",
    "github",
    "jira",
    "awx",
    "ansible",
    "recipe",
    "orchestration",
)
MANAGEMENT_COMMANDS = ("config", "profile", "skills", "doctor", "capabilities")


class ReleaseCheckError(RuntimeError):
    """A release precondition failed."""


def load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML document for release metadata validation."""
    if tomllib is None:
        raise ReleaseCheckError("TOML validation requires Python 3.11 or newer")
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseCheckError(f"could not read TOML file {path}: {error}") from error
    return parsed


def requirement_specifier(requirement: str) -> str:
    """Return the specifier portion of a PEP 508 requirement."""
    match = re.match(r"^[A-Za-z0-9_.-]+(.*)$", requirement)
    if match is None or not match.group(1):
        return ""
    return match.group(1).replace(" ", "")


def dependency_name(requirement: str) -> str:
    """Return a normalized distribution name from a requirement string."""
    match = re.match(r"([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        return ""
    return normalize_package_name(match.group(1))


def normalize_package_name(name: str) -> str:
    """Normalize a distribution name according to PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def project_metadata(pyproject_path: Path, *, tomllib_module: Any = tomllib) -> dict[str, Any]:
    """Read project metadata, retaining the old pre-3.11 fallback parser."""
    text = pyproject_path.read_text(encoding="utf-8")
    if tomllib_module is not None:
        return tomllib_module.loads(text)["project"]
    return parse_project_metadata(text)


def parse_project_metadata(text: str) -> dict[str, Any]:
    """Parse the metadata subset needed by pre-sync release checks."""
    project_lines: list[str] = []
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("[") and stripped.endswith("]"):
            break
        if in_project:
            project_lines.append(line)

    project: dict[str, Any] = {}
    index = 0
    while index < len(project_lines):
        stripped = project_lines[index].strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            index += 1
            continue

        key, raw_value = [part.strip() for part in stripped.split("=", maxsplit=1)]
        if key in {"name", "version"}:
            project[key] = ast.literal_eval(raw_value)
        elif key == "dependencies":
            value_lines = [raw_value]
            while "]" not in value_lines[-1]:
                index += 1
                value_lines.append(project_lines[index].strip())
            project[key] = ast.literal_eval("\n".join(value_lines))
        index += 1

    return project


def verify_version(
    version: str,
    *,
    pyproject_path: Path = PYPROJECT,
    tomllib_module: Any = tomllib,
) -> None:
    """Verify a release version against project metadata."""
    if VERSION_RE.fullmatch(version) is None:
        raise ReleaseCheckError(f"unsafe or invalid release version input: {version!r}")

    project = project_metadata(pyproject_path, tomllib_module=tomllib_module)
    actual = str(project["version"])
    if actual != version:
        raise ReleaseCheckError(
            f"workflow input version {version!r} does not match pyproject.toml {actual!r}"
        )
    print(f"ok: package metadata version matches workflow input {version}")
